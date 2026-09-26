# 🎯 Candidatos de memecoins (Solana) — v2

App web para el iPhone que se actualiza sola cada 20 minutos. No hay que ejecutar nada.

## Qué hace
1. **Descarta** tokens con señales de estafa documentadas (autoridad de congelación o acuñación
   activa, honeypot, riesgos graves según RugCheck, liquidez retirable en pools libres).
2. **Puntúa** el resto de 0 a 100 con reglas transparentes. Cada regla indica si su base es un
   estudio publicado, una herramienta experta o una práctica común de traders.
3. **Se valida a sí misma**: guarda el precio de cada token al detectarlo y mide qué pasó 1 h,
   6 h y 24 h después. La pestaña "Validación" dice si los candidatos rinden de verdad mejor
   que el resto. Necesita unos días de datos para decir algo fiable.

**No predice subidas.** Los estudios predicen la graduación en pump.fun, no el beneficio de
quien compra.

## Cómo está montado
- Rama `main`: el código (estos archivos). El robot no la modifica.
- Rama `gh-pages`: la web publicada (index.html, data.json, historial.json). El robot la
  reescribe entera en cada ejecución, así el repositorio no crece aunque se actualice cada 20 min.
- **GitHub Pages debe publicar desde la rama `gh-pages`** (Settings → Pages). Esa rama solo
  existe después de la primera ejecución del robot.

## Fuentes de datos (gratuitas, sin clave)
- GeckoTerminal: pools en tendencia y nuevos, ficha de cada token, precios de seguimiento.
- RugCheck: resumen de riesgos de cada token.

## Si algo falla
- Pestaña **Actions**: una ejecución en rojo muestra el error exacto.
- En la app, pestaña **Método** (abajo del todo): avisos de la última ejecución.
- **No se ha podido probar contra las APIs reales antes de entregarlo.** Es posible que la
  primera ejecución revele algún campo con otro nombre; los fallos quedan anotados sin detener
  el resto.
- Si GitHub desactiva el robot por inactividad, entra en Actions y pulsa "Enable workflow".

## Ajustes
Al principio de `generar_web.py`: umbrales (`UMBRAL_...`), límites de peticiones por ejecución
y liquidez mínima.
