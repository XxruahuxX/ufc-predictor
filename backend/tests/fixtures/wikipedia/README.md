# Páginas de ejemplo de Wikipedia

Escritas a mano con la estructura del HTML que devuelve Wikipedia
(API `action=parse` o página del artículo): títulos de sección con id,
tabla "Scheduled events", tabla de cartelera con columna "Weight class"
y lista de peleas anunciadas ("Lightweight bout: A vs. B").

Los peleadores, fechas y recintos son de prueba.

- `list_of_ufc_events.html`: títulos con el formato nuevo (`<h2 id=...>`).
- `event_card.html`: cartelera en tabla y lista de peleas anunciadas.
- `event_announced.html`: solo lista de peleas, títulos con el formato antiguo (`<span class="mw-headline">`).
- `robots.txt`: ejemplo con las reglas habituales (API prohibida, artículos permitidos). No es una copia del real.

Las páginas reales se descargan con `python -m app.ingestion.wikipedia sample`
en `real/`, que no se sube al repositorio.
