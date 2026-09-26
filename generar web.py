"""
GENERADOR DE DATOS — CANDIDATOS DE MEMECOINS EN SOLANA (v2)
============================================================

Lo ejecuta el robot de GitHub Actions cada 20 minutos. No hace falta ejecutarlo a mano.

QUÉ HACE Y QUÉ NO
-----------------
No existe un predictor demostrado de "este token va a subir". Lo que sí está documentado:
  * señales de fracaso/estafa (autoridad de congelación o acuñación activa, liquidez
    retirable, concentración oculta, historial del creador);
  * señales asociadas a la GRADUACIÓN en pump.fun (no a la rentabilidad del comprador):
    presencia en redes sociales (Kamat 2026: Telegram ×8,9; las tres redes ×17,4),
    avance en la curva y acumulación rápida con pocas operaciones grandes (Marino et al. 2026).

Por eso la app hace tres cosas:
  1. DESCARTA tokens con señales de estafa documentadas.
  2. PUNTÚA el resto con reglas transparentes (cada regla dice si su base es un
     estudio, una herramienta experta o una práctica común de traders).
  3. VALIDA su propia puntuación: guarda el precio al detectar cada token y mide qué
     pasó 1 h, 6 h y 24 h después. La pestaña "Validación" compara las bandas con el
     conjunto. Si la puntuación no funciona, los datos lo dirán.

FUENTES (gratuitas, sin clave)
  * GeckoTerminal: pools en tendencia/nuevos, ficha del token (redes, holders,
    autoridades, % del desarrollador, avance de la curva, GT Score), precios de seguimiento.
  * RugCheck: resumen de riesgos (motor experto: LP bloqueado, insiders, concentración...).

LIMITACIÓN HONESTA: no se ha podido probar contra las APIs reales antes de entregarlo.
Los fallos se registran en "avisos" sin detener la ejecución.
"""

import json
import random
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ================================================================ configuración
GT = "https://api.geckoterminal.com/api/v2"
RC = "https://api.rugcheck.xyz/v1"
AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "data.json"
HISTORIAL = AQUI / "historial.json"

PAGINAS_TENDENCIA = 2
PAGINAS_NUEVOS = 3
LIQUIDEZ_MINIMA_USD = 1000        # por debajo, el pool está prácticamente vacío
LIQUIDEZ_MUERTO_USD = 500         # en el seguimiento: por debajo, cuenta como pérdida total
MAX_INFO_POR_RUN = 24             # fichas de GeckoTerminal (1 llamada por token)
MAX_RUGCHECK_POR_RUN = 24
CACHE_HORAS = 3                   # las fichas y los informes se reutilizan 3 h
GT_INTERVALO_S = 2.2              # ~27 llamadas/min: por debajo del límite público (~30/min)
RC_INTERVALO_S = 1.2
MAX_SEGUIMIENTO_NUEVOS = 120      # tokens nuevos que entran en seguimiento por ejecución
DIAS_CERRADOS = 21                # días de resultados que se conservan para la validación

# Umbrales (editables). Cada regla indica su base en la pestaña "Método".
UMBRAL_LP_BLOQUEADO_MIN = 50.0    # % mínimo de LP bloqueado/quemado en pools libres
UMBRAL_VENTA_H1 = 0.65            # vendedores únicos / (compradores + vendedores) en 1 h
UMBRAL_VOL_LIQ = 15.0             # volumen 24 h / liquidez (posible volumen lavado)
UMBRAL_DEV_PCT = 10.0             # % en manos del desarrollador
EDAD_MINIMA_MIN = 10              # antes de 10 min los datos casi no significan nada

BANDAS = ["descartado", "sin verificar", "débil", "vigilar", "candidato"]
FASES = ["curva pump.fun", "graduado (PumpSwap)", "pool libre"]

SESION = requests.Session()
SESION.headers.update({"Accept": "application/json", "User-Agent": "memecoin-candidatos/2.0"})
_ultima = {"gt": 0.0, "rc": 0.0}


def aviso(msg):
    print(msg, flush=True)


def ahora_min():
    return int(time.time() // 60)


# ================================================================ HTTP con ritmo
def _esperar(clave, intervalo):
    t = time.time() - _ultima[clave]
    if t < intervalo:
        time.sleep(intervalo - t)
    _ultima[clave] = time.time()


def http_get(url, clave, intervalo, params=None, intentos=3):
    ultimo = None
    for i in range(intentos):
        _esperar(clave, intervalo)
        try:
            r = SESION.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(6 * (i + 1))
                ultimo = RuntimeError("429 demasiadas peticiones")
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:                                  # noqa: BLE001
            ultimo = e
            time.sleep(1.5 * (i + 1))
    raise ultimo or RuntimeError("sin respuesta")


def gt_get(ruta, params=None):
    return http_get(GT + ruta, "gt", GT_INTERVALO_S, params)


def rc_get(ruta):
    return http_get(RC + ruta, "rc", RC_INTERVALO_S)


# ================================================================ utilidades
def _num(x):
    try:
        v = float(x)
        return v if v == v else None                            # NaN -> None
    except (TypeError, ValueError):
        return None


def _activa(valor):
    """Autoridad de acuñación/congelación: GeckoTerminal usa null, 'no' o una dirección."""
    if valor is None:
        return False
    s = str(valor).strip().lower()
    return s not in ("", "no", "false", "none", "null", "revoked", "0")


def _incluidos(doc):
    return {(it.get("type"), it.get("id")): it for it in (doc or {}).get("included", []) or []}


def _rel_id(item, nombre):
    d = ((item.get("relationships") or {}).get(nombre) or {}).get("data")
    return (d.get("type"), d.get("id")) if d else None


def fase_de(dex_id, dex_nombre, info=None):
    """
    Corrige el error de la v1: desde el 20/03/2025 los graduados de pump.fun migran a
    PumpSwap (LP quemado por el protocolo). Hay que comprobar 'pumpswap' ANTES que 'pump'.
    """
    if info and info.get("grad_completado") is True:
        base = f"{dex_id or ''} {dex_nombre or ''}".lower()
        return 1 if ("pumpswap" in base or "pump-swap" in base or "pump swap" in base) else 2
    t = f"{dex_id or ''} {dex_nombre or ''}".lower()
    if "pumpswap" in t or "pump-swap" in t or "pump swap" in t or "pump amm" in t:
        return 1
    if "pump" in t:
        return 0
    return 2


# ================================================================ 1. pools
def leer_pool(item, inc, fuente):
    a = item.get("attributes") or {}
    base = (inc.get(_rel_id(item, "base_token")) or {}).get("attributes") or {}
    dex_ref = _rel_id(item, "dex")
    dex = (inc.get(dex_ref) or {}).get("attributes") or {}
    tx = a.get("transactions") or {}
    vol = a.get("volume_usd") or {}
    cambio = a.get("price_change_percentage") or {}

    def txw(k):
        w = tx.get(k) or {}
        return {"compras": _num(w.get("buys")) or 0, "ventas": _num(w.get("sells")) or 0,
                "compradores": _num(w.get("buyers")), "vendedores": _num(w.get("sellers"))}

    creado = a.get("pool_created_at")
    edad = None
    if creado:
        try:
            edad = (datetime.now(timezone.utc) - datetime.fromisoformat(creado.replace("Z", "+00:00"))).total_seconds() / 60
        except ValueError:
            edad = None
    dex_id = dex_ref[1] if dex_ref else None
    return {
        "mint": base.get("address"), "pool": a.get("address") or (item.get("id") or "").split("_", 1)[-1],
        "nombre": base.get("name") or a.get("name"), "simbolo": base.get("symbol"),
        "imagen": base.get("image_url") if str(base.get("image_url") or "").startswith("http") else None,
        "dex_id": dex_id, "dex": dex.get("name") or dex_id,
        "creado": creado, "edad_min": round(edad, 1) if edad is not None else None,
        "precio": _num(a.get("base_token_price_usd")),
        "fdv": _num(a.get("fdv_usd")) or _num(a.get("market_cap_usd")),
        "liquidez": _num(a.get("reserve_in_usd")),
        "vol": {k: _num(vol.get(k)) for k in ("m5", "h1", "h6", "h24")},
        "cambio": {k: _num(cambio.get(k)) for k in ("m5", "h1", "h6", "h24")},
        "tx": {k: txw(k) for k in ("m5", "h1", "h24")},
        "fuente": fuente,
    }


def descargar_pools(avisos):
    vistos, filas = set(), []
    for ruta, paginas, fuente in (("/networks/solana/trending_pools", PAGINAS_TENDENCIA, "tendencia"),
                                  ("/networks/solana/new_pools", PAGINAS_NUEVOS, "nuevo")):
        for p in range(1, paginas + 1):
            try:
                doc = gt_get(ruta, {"page": p, "include": "base_token,dex"})
            except Exception as e:                              # noqa: BLE001
                avisos.append(f"GeckoTerminal {fuente} pág. {p}: {e}")
                continue
            inc = _incluidos(doc)
            for item in (doc or {}).get("data", []) or []:
                try:
                    f = leer_pool(item, inc, fuente)
                except Exception as e:                          # noqa: BLE001
                    avisos.append(f"Pool ilegible ({fuente}): {e}")
                    continue
                if not f["mint"] or f["mint"] in vistos:
                    continue
                vistos.add(f["mint"])
                filas.append(f)
    return [f for f in filas if (f["liquidez"] or 0) >= LIQUIDEZ_MINIMA_USD]


# ================================================================ 2. ficha y RugCheck (con caché)
def leer_info(doc):
    a = ((doc or {}).get("data") or {}).get("attributes") or {}
    hold = a.get("holders") or {}
    dist = hold.get("distribution_percentage") or {}
    lp = a.get("launchpad_details") or {}
    webs = [w for w in (a.get("websites") or []) if w]
    return {
        "telegram": bool(a.get("telegram_handle")), "twitter": bool(a.get("twitter_handle")),
        "web": bool(webs), "discord": bool(a.get("discord_url")),
        "gt_score": _num(a.get("gt_score")),
        "holders": _num(hold.get("count")), "top10": _num(dist.get("top_10")),
        "mint_activa": _activa(a.get("mint_authority")), "freeze_activa": _activa(a.get("freeze_authority")),
        "honeypot": a.get("is_honeypot") is True or str(a.get("is_honeypot")).lower() == "true",
        "dev_pct": _num(a.get("developer_holding_percentage")),
        "grad_pct": _num(lp.get("graduation_percentage")),
        "grad_completado": lp.get("completed") if isinstance(lp.get("completed"), bool) else None,
    }


def leer_rugcheck(doc):
    doc = doc or {}
    riesgos = []
    for r in doc.get("risks") or []:
        riesgos.append({"nombre": r.get("name"), "nivel": (r.get("level") or "").lower(),
                        "descripcion": r.get("description"), "valor": r.get("value")})
    return {"puntuacion": _num(doc.get("score_normalised")), "lp_bloqueado": _num(doc.get("lpLockedPct")),
            "rugged": bool(doc.get("rugged")), "riesgos": riesgos}


def enriquecer(filas, cache, avisos):
    t = time.time()
    fresco = lambda c: c and (t - c.get("_t", 0)) < CACHE_HORAS * 3600       # noqa: E731
    prioridad = sorted(filas, key=lambda f: (f["vol"].get("h1") or 0), reverse=True)
    n_info = n_rc = fallos_info = fallos_rc = 0
    for f in prioridad:
        m = f["mint"]
        ci = cache["info"].get(m)
        if not fresco(ci) and n_info < MAX_INFO_POR_RUN:
            n_info += 1
            try:
                ci = leer_info(gt_get(f"/networks/solana/tokens/{m}/info"))
                ci["_t"] = t
                cache["info"][m] = ci
            except Exception as e:                              # noqa: BLE001
                fallos_info += 1
                if fallos_info <= 3:
                    avisos.append(f"Ficha GeckoTerminal de {f.get('simbolo') or m[:6]}: {e}")
        cr = cache["rug"].get(m)
        if not fresco(cr) and n_rc < MAX_RUGCHECK_POR_RUN:
            n_rc += 1
            try:
                doc = rc_get(f"/tokens/{m}/report/summary")
                if doc is not None:
                    cr = leer_rugcheck(doc)
                    cr["_t"] = t
                    cache["rug"][m] = cr
            except Exception as e:                              # noqa: BLE001
                fallos_rc += 1
                if fallos_rc <= 3:
                    avisos.append(f"RugCheck de {f.get('simbolo') or m[:6]}: {e}")
        f["info"] = {k: v for k, v in (cache["info"].get(m) or {}).items() if k != "_t"} or None
        f["rug"] = {k: v for k, v in (cache["rug"].get(m) or {}).items() if k != "_t"} or None
    if fallos_rc > 3:
        avisos.append(f"... y {fallos_rc - 3} fallos más de RugCheck.")
    return {"fichas_pedidas": n_info, "fichas_fallidas": fallos_info,
            "rugcheck_pedidos": n_rc, "rugcheck_fallidos": fallos_rc}


# ================================================================ 3. evaluación
def _comp(nombre, pts, maximo, base, detalle):
    return {"nombre": nombre, "pts": round(max(0.0, min(pts, maximo)), 1), "max": maximo, "base": base, "detalle": detalle}


def evaluar(f):
    info, rug = f.get("info"), f.get("rug")
    f["fase_cod"] = fase_de(f.get("dex_id"), f.get("dex"), info)
    f["fase"] = FASES[f["fase_cod"]]
    excl, alertas, comps = [], [], []

    # ---------- exclusiones (señales de estafa documentadas)
    if info:
        if info["freeze_activa"]:
            excl.append(("Autoridad de congelación activa: el creador puede bloquear tus tokens.", "estudio"))
        if info["mint_activa"]:
            excl.append(("Autoridad de acuñación activa: el creador puede crear tokens nuevos.", "estudio"))
        if info["honeypot"]:
            excl.append(("Marcado como honeypot (no se puede vender).", "herramienta"))
    if rug:
        if rug["rugged"]:
            excl.append(("RugCheck lo marca como 'rugged' (liquidez ya retirada).", "herramienta"))
        for r in rug["riesgos"]:
            if r["nivel"] == "danger":
                excl.append((f"RugCheck (peligro): {r['nombre']}.", "herramienta"))
        if f["fase_cod"] == 2 and rug["lp_bloqueado"] is not None and rug["lp_bloqueado"] < UMBRAL_LP_BLOQUEADO_MIN:
            excl.append((f"Solo {rug['lp_bloqueado']:.0f}% de la liquidez bloqueada o quemada: el creador puede retirarla.",
                         "estudio"))

    # ---------- datos derivados
    h1 = f["tx"]["h1"]
    compradores, vendedores = h1["compradores"], h1["vendedores"]
    if compradores is None or vendedores is None:              # sin únicos: usar operaciones
        compradores, vendedores = h1["compras"], h1["ventas"]
    ops_h1 = (h1["compras"] or 0) + (h1["ventas"] or 0)
    f["ticket_medio_h1"] = round(f["vol"]["h1"] / ops_h1, 1) if f["vol"].get("h1") and ops_h1 else None
    f["ratio_venta_h1"] = round(vendedores / (compradores + vendedores), 3) if (compradores + vendedores) >= 5 else None
    f["liq_fdv"] = round(f["liquidez"] / f["fdv"], 4) if f["liquidez"] and f["fdv"] else None
    f["vol_liq"] = round(f["vol"]["h24"] / f["liquidez"], 1) if f["vol"].get("h24") and f["liquidez"] else None

    # ---------- alertas (restan, no excluyen)
    if f["ratio_venta_h1"] is not None and f["ratio_venta_h1"] > UMBRAL_VENTA_H1:
        alertas.append((f"Predominan los vendedores en la última hora ({100 * f['ratio_venta_h1']:.0f}%).", "práctica", 15))
    if f["vol_liq"] is not None and f["vol_liq"] > UMBRAL_VOL_LIQ:
        alertas.append((f"Volumen 24 h de {f['vol_liq']:.0f}× la liquidez: posible volumen lavado entre pocas carteras.",
                        "práctica", 10))
    if info and info["dev_pct"] is not None and info["dev_pct"] > UMBRAL_DEV_PCT:
        alertas.append((f"El desarrollador conserva el {info['dev_pct']:.1f}% del suministro.", "práctica", 10))
    if rug:
        for r in rug["riesgos"]:
            if r["nivel"] in ("warn", "warning"):
                alertas.append((f"RugCheck (aviso): {r['nombre']}.", "herramienta", 4))
    if f["edad_min"] is not None and f["edad_min"] < EDAD_MINIMA_MIN:
        alertas.append((f"Solo {f['edad_min']:.0f} min de vida: los datos aún no significan casi nada.", "práctica", 0))

    # ---------- puntuación positiva (0-100)
    if info:
        redes = sum([info["telegram"], info["twitter"], info["web"]])
        pts_redes = {0: 0, 1: 8, 2: 16, 3: 25}[redes] + (5 if info["telegram"] else 0)
        comps.append(_comp("Presencia en redes", pts_redes, 25, "estudio",
                           f"Telegram {'sí' if info['telegram'] else 'no'} · X {'sí' if info['twitter'] else 'no'} · "
                           f"web {'sí' if info['web'] else 'no'} (con las tres redes la graduación fue ×17 en 833.000 lanzamientos)"))
    if f["fase_cod"] == 0:
        gp = info.get("grad_pct") if info else None
        comps.append(_comp("Avance en la curva", (gp or 0) / 100 * 25, 25, "estudio",
                           f"{gp:.0f}% del camino a la graduación" if gp is not None else "sin dato"))
        tm = f["ticket_medio_h1"]
        comps.append(_comp("Tamaño medio de operación (1 h)", 0 if not tm else min(20, 20 * tm / 150), 20, "estudio",
                           f"{tm:.0f} $ por operación (aproximación a 'acumular liquidez con pocas operaciones', el mejor predictor de graduación)"
                           if tm else "sin dato"))
    else:
        liq = f["liquidez"] or 0
        comps.append(_comp("Liquidez", min(25, 25 * liq / 60000), 25, "práctica",
                           f"{liq:,.0f} $ (por debajo de ~50.000 $ salir mueve el precio un 10-20%)".replace(",", ".")))
        lf = f["liq_fdv"]
        comps.append(_comp("Liquidez / capitalización", 0 if lf is None else min(20, 20 * lf / 0.10), 20, "práctica",
                           f"{100 * lf:.1f}% de la FDV" if lf is not None else "sin dato"))
    rv = f["ratio_venta_h1"]
    comps.append(_comp("Dominio comprador (1 h)", 0 if rv is None else 15 * max(0.0, (0.6 - rv) / 0.3), 15, "práctica",
                       f"{100 * (1 - rv):.0f}% compradores" if rv is not None else "menos de 5 participantes"))
    ub = compradores or 0
    comps.append(_comp("Compradores distintos (1 h)", min(10, 10 * ub / 60), 10, "práctica", f"{ub:.0f} carteras"))
    if f["fase_cod"] != 0 and info and info.get("gt_score") is not None:
        comps.append(_comp("GT Score", info["gt_score"] / 100 * 5, 5, "herramienta", f"{info['gt_score']:.0f}/100"))

    maximo = sum(c["max"] for c in comps) or 1
    bruto = sum(c["pts"] for c in comps) / maximo * 100
    penal = sum(a[2] for a in alertas)
    total = max(0.0, bruto - penal)

    # ---------- banda
    verificado = info is not None and rug is not None
    if excl:
        banda = 0
    elif not verificado:
        banda = 1
    elif total >= 60:
        banda = 4
    elif total >= 40:
        banda = 3
    else:
        banda = 2
    f.update({"exclusiones": [{"texto": t, "base": b} for t, b in excl],
              "alertas": [{"texto": t, "base": b, "penal": p} for t, b, p in alertas],
              "componentes": comps, "puntuacion": round(total, 1) if banda >= 2 else None,
              "banda_cod": banda, "banda": BANDAS[banda]})
    return f


# ================================================================ 4. validación hacia delante
PUNTOS_CONTROL = [(60, 5), (360, 6), (1440, 7)]              # (minutos, índice en la entrada)


def cargar_historial(ruta=HISTORIAL):
    try:
        h = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        h = {}
    h.setdefault("seguimiento", {})
    h.setdefault("cerrados", [])
    h.setdefault("cache", {"info": {}, "rug": {}})
    h["cache"].setdefault("info", {})
    h["cache"].setdefault("rug", {})
    h.setdefault("desde", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    return h


def registrar_nuevos(h, filas):
    """Entrada: [t0_min, precio0, puntuación, banda, fase, r1h, r6h, r24h]."""
    t = ahora_min()
    cerrados_recientes = {c[8] for c in h["cerrados"] if len(c) > 8}
    nuevos = [f for f in filas if f["mint"] not in h["seguimiento"] and f["mint"] not in cerrados_recientes
              and f.get("precio")]
    random.shuffle(nuevos)
    for f in nuevos[:MAX_SEGUIMIENTO_NUEVOS]:
        h["seguimiento"][f["mint"]] = [t, f["precio"], f.get("puntuacion"), f["banda_cod"], f["fase_cod"],
                                       None, None, None]
    return min(len(nuevos), MAX_SEGUIMIENTO_NUEVOS)


def precios_actuales(mints, avisos):
    precios = {}
    for i in range(0, len(mints), 30):
        lote = mints[i:i + 30]
        try:
            doc = gt_get(f"/networks/solana/tokens/multi/{','.join(lote)}")
        except Exception as e:                                  # noqa: BLE001
            avisos.append(f"Precios de seguimiento (lote {i // 30 + 1}): {e}")
            for m in lote:
                precios[m] = "error"
            continue
        for it in (doc or {}).get("data", []) or []:
            a = it.get("attributes") or {}
            if a.get("address"):
                precios[a["address"]] = (_num(a.get("price_usd")), _num(a.get("total_reserve_in_usd")))
    return precios


def actualizar_seguimiento(h, avisos):
    t = ahora_min()
    pendientes = [m for m, e in h["seguimiento"].items()
                  if any(e[idx] is None and t - e[0] >= mins for mins, idx in PUNTOS_CONTROL)]
    if not pendientes:
        return 0
    precios = precios_actuales(pendientes, avisos)
    n = 0
    for m in pendientes:
        p = precios.get(m)
        if p == "error":
            continue                                             # se reintenta en la siguiente ejecución
        e = h["seguimiento"][m]
        precio, reserva = p if isinstance(p, tuple) else (None, None)
        # Pérdida total (-100%) si GeckoTerminal ya no devuelve el token, no tiene precio, o su
        # liquidez ha caído por debajo de LIQUIDEZ_MUERTO_USD: aunque se muestre un último precio,
        # en la práctica ya no se puede vender. Criterio conservador.
        if not precio or not e[1] or (reserva is not None and reserva < LIQUIDEZ_MUERTO_USD):
            r = -1.0
        else:
            r = precio / e[1] - 1
        for mins, idx in PUNTOS_CONTROL:
            if e[idx] is None and t - e[0] >= mins:
                e[idx] = round(r, 4)
                n += 1
        if e[7] is not None:
            h["cerrados"].append(e[:8] + [m])
            del h["seguimiento"][m]
    return n


def podar(h):
    t = ahora_min()
    h["cerrados"] = [c for c in h["cerrados"] if t - c[0] <= DIAS_CERRADOS * 1440][-25000:]
    for m in [m for m, e in h["seguimiento"].items() if t - e[0] > 1440 * 2]:
        del h["seguimiento"][m]                                  # sin cierre en 48 h: se abandona
    corte = time.time() - CACHE_HORAS * 3600 * 2
    for tipo in ("info", "rug"):
        h["cache"][tipo] = {m: c for m, c in h["cache"][tipo].items() if c.get("_t", 0) > corte}


def estadisticas(h):
    def resumen(valores):
        v = [x for x in valores if x is not None]
        if not v:
            return {"n": 0}
        return {"n": len(v), "mediana": round(statistics.median(v), 4),
                "pct_x2": round(sum(1 for x in v if x >= 1.0) / len(v), 4),
                "pct_mas50": round(sum(1 for x in v if x >= 0.5) / len(v), 4),
                "pct_menos80": round(sum(1 for x in v if x <= -0.8) / len(v), 4)}

    todos = h["cerrados"] + list(h["seguimiento"].values())
    salida = {}
    for mins, idx in PUNTOS_CONTROL:
        clave = f"{mins // 60}h"
        salida[clave] = {"todos": resumen(e[idx] for e in todos)}
        for b, nombre in enumerate(BANDAS):
            salida[clave][nombre] = resumen(e[idx] for e in todos if e[3] == b)
        for fcod, nombre in enumerate(FASES):
            salida[clave]["fase:" + nombre] = resumen(e[idx] for e in todos if e[4] == fcod)
    return salida


# ================================================================ programa principal
def main(salida=SALIDA, ruta_historial=HISTORIAL):
    t0 = time.time()
    avisos = []
    h = cargar_historial(ruta_historial)

    aviso("1/4 Descargando pools de Solana (GeckoTerminal)...")
    filas = descargar_pools(avisos)
    aviso(f"    {len(filas)} tokens con liquidez >= {LIQUIDEZ_MINIMA_USD} $")

    aviso("2/4 Fichas de GeckoTerminal e informes de RugCheck (con caché de 3 h)...")
    estado = enriquecer(filas, h["cache"], avisos)
    aviso(f"    {estado}")

    aviso("3/4 Evaluando...")
    for f in filas:
        evaluar(f)
    filas.sort(key=lambda f: (f["banda_cod"], f.get("puntuacion") or 0), reverse=True)

    aviso("4/4 Validación: registrando y midiendo tokens en seguimiento...")
    n_act = actualizar_seguimiento(h, avisos)
    n_nuevos = registrar_nuevos(h, filas)
    podar(h)
    aviso(f"    {n_nuevos} nuevos en seguimiento · {n_act} mediciones · "
          f"{len(h['seguimiento'])} abiertos · {len(h['cerrados'])} cerrados")

    conteo = {b: sum(1 for f in filas if f["banda_cod"] == i) for i, b in enumerate(BANDAS)}
    datos = {
        "version": 2, "generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "conteo": conteo, "estado_fuentes": estado, "tokens": filas,
        "validacion": {"desde": h["desde"], "abiertos": len(h["seguimiento"]), "cerrados": len(h["cerrados"]),
                       "estadisticas": estadisticas(h)},
        "umbrales": {"lp_bloqueado_min": UMBRAL_LP_BLOQUEADO_MIN, "venta_h1": UMBRAL_VENTA_H1,
                     "vol_liq": UMBRAL_VOL_LIQ, "dev_pct": UMBRAL_DEV_PCT, "edad_min": EDAD_MINIMA_MIN},
        "avisos": avisos,
    }
    Path(salida).write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    Path(ruta_historial).write_text(json.dumps(h, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    aviso(f"Escrito {Path(salida).name} ({Path(salida).stat().st_size / 1024:.0f} KB) y "
          f"{Path(ruta_historial).name} ({Path(ruta_historial).stat().st_size / 1024:.0f} KB) en {time.time() - t0:.0f} s")
    return datos


if __name__ == "__main__":
    try:
        main()
    except Exception:                                           # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
