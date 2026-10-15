# Fase 3 · Ingesta de datos

Código: [`backend/app/ingestion/`](../backend/app/ingestion/).

## Aviso: UFCStats bloquea programas (15/10/2026)

Desde un programa, `ufcstats.com` devuelve una página "Checking your browser…" que exige JavaScript y una prueba de trabajo. Es una barrera anti-bots puesta a propósito. **No se intenta saltar.** El código de UFCStats se conserva porque sus parsers sirven para páginas guardadas a mano desde el navegador.

Alternativas:
- Histórico: un CSV de Kaggle con los datos de UFCStats, descargado a mano (abajo).
- Próximos eventos y carteleras: Wikipedia (abajo).
- Cuotas: The Odds API (abajo).

## Histórico desde Kaggle

Código: [`backend/app/ingestion/kaggle_import.py`](../backend/app/ingestion/kaggle_import.py).

Archivo: `UFC_full_data_silver_v2.csv` (8937 peleas, 361 columnas). Se guarda en `backend/data/kaggle/`, que no se sube al repositorio. Pendiente: anotar el enlace del dataset y su licencia.

```bash
python -m app.ingestion.inspect_csv data/kaggle   # ver columnas y filas de ejemplo
python -m app.ingestion.kaggle_import data/kaggle/UFC_full_data_silver_v2.csv
```

| Columnas del CSV | Tabla |
|---|---|
| `event_*` | `events` (lugar = ciudad, estado, país) |
| `f_1_*`, `f_2_*` (nombre, URL, `fighter_*`) | `fighters` |
| `fight_url`, `num_rounds`, `title_fight`, `weight_class` | `fights` |
| `winner`, `result`, `result_details`, `finish_round`, `finish_time` | `fight_results` |
| `f_1_knockdowns` … `f_1_ctrl_time_sec` (y `f_2_`) | `fight_stats` |
| `f_1_odds`, `f_2_odds` | `odds` (casa `kaggle`) |

No se importan:
- **Medias de carrera** (`SlpM`, `Str_Acc`, `TD_Avg`...). Son una foto de hoy. Para predecir una pelea de 2015 el modelo vería datos posteriores. Las medias se calcularán con `fight_stats`, solo con peleas anteriores a cada fecha.
- Estadísticas por asalto, rankings y cuotas de KO o sumisión: no están en el esquema.

Decisiones:
- **Esquinas A y B por id.** El CSV pone primero al ganador, igual que UFCStats.
- **Ganador.** Se compara el nombre de `winner` con los dos peleadores, sin tildes ni mayúsculas. Si no coincide con ninguno, la pelea se guarda sin resultado y sale en los avisos.
- **Sin ganador:** decisión = empate (`draw`); `Overturned`, `Could Not Continue`, `Other` = sin resultado (`no_contest`).
- **Asaltos.** El esquema admite 3 o 5. Los formatos de los años 90 (1 asalto, sin límite) se guardan como 3.
- **Orden en la cartelera:** el del CSV, que pone la pelea estelar primero.
- **Cuotas.** Formato decimal o americano, según el número: una americana nunca está entre -100 y +100. Como el CSV no dice cuándo se tomaron, se guardan con fecha del evento a las 00:00 UTC.
- **Ficha del peleador:** récord, altura, alcance y fecha de nacimiento actuales; categoría de su pelea más reciente que no sea de peso pactado.

Se puede repetir: actualiza por `ufcstats_id` y no duplica. Al terminar imprime un resumen: resultados, cómo se tradujo cada valor de `result`, filas no importadas y avisos con ejemplos.

También completa lo que creó Wikipedia en vez de duplicarlo (ver "Cuando el CSV trae el evento celebrado").

## Próximas carteleras desde Wikipedia

Código: [`backend/app/ingestion/wikipedia/`](../backend/app/ingestion/wikipedia/).

```bash
python -m app.ingestion.wikipedia upcoming   # próximos eventos y peleas -> base de datos
python -m app.ingestion.wikipedia sample     # guarda páginas reales para los tests
```

| Página | Qué se saca |
|---|---|
| `List_of_UFC_events`, tabla "Scheduled events" | nombre, fecha, recinto y ciudad de cada evento |
| Página de cada evento | peleas: tabla con columna "Weight class" y listas "Lightweight bout: A vs. B" |

La lista no enlaza la página de los próximos eventos: se busca por su título.
- Numerados: primero solo el número ("UFC 333: Volkanovski vs. Evloev" → `UFC_333`) y después el nombre completo.
- El resto: el nombre completo (`UFC_Fight_Night:_Buckley_vs._Malott`).
- Si la lista sí enlaza una página, esa se prueba primero.

Un título que no existe (404) pasa al siguiente. La página que llega tiene que llamarse como el evento o como uno de esos títulos. Si no, se descarta: un título puede redirigir a otra página, como "2026 in UFC", y esa no es una cartelera.

Un evento sin página todavía se guarda sin peleas.

### Permisos

- **robots.txt.** Antes de descargar se lee (`PoliteClient.allowed`). Si permite la API oficial (`/w/api.php`), se usa la API. Si solo permite las páginas de los artículos (`/wiki/...`), se leen esas. Si no permite ninguna, no se descarga nada. `sample` imprime qué permite. Comprobado el 15/10/2026: no permite la API a programas como este y sí los artículos, así que se leen los artículos.
- **Normas de la API de Wikimedia.** Piden un User-Agent con contacto y no fijan un límite de peticiones, pero sí que se hagan de una en una. Se envía `ufc-predictor/0.1 (https://github.com/XxruahuxX/ufc-predictor; proyecto de estudiante) httpx/…`, se espera 2 s entre peticiones y hay caché de 6 horas.
- **Licencia.** El texto de Wikipedia es CC BY-SA. Se guardan datos (fechas, nombres, quién pelea con quién), no texto. La web citará Wikipedia como fuente.

### Cómo se une con lo que ya hay

Wikipedia no tiene los ids de UFCStats: se compara por nombre y fecha.

| Qué | Cómo se reconoce |
|---|---|
| Evento | Mismo nombre, aunque cambie de fecha. Si no, misma fecha (±1 día por la zona horaria). |
| Peleador | Nombre sin tildes ni mayúsculas, o un alias (ver "Alias"). Si no, variantes: sin "Jr.", sin espacios ("Da Woon" = "Dawoon"), palabras en otro orden ("Zhang Weili" = "Weili Zhang") y nombres coreanos ("Lee Jeong-yeong" = "JeongYeong Lee"). |
| Pelea | Mismo evento y mismos dos peleadores, en cualquier orden. |

Decisiones:
- **Lo que no existe se crea sin `ufcstats_id`**: eventos, peleas y debutantes.
- **Esquinas A y B por orden alfabético.** Tras la pelea, Wikipedia pone primero al ganador: ese orden no puede llegar a los datos.
- **Asaltos.** 5 si es por el título o es la estelar (primera fila de la tabla); 3 en el resto.
- **Dos peleadores con el mismo nombre.** Decide la categoría de la pelea. Si sigue la duda, la pelea no se guarda y sale en el resumen.
- **Pelea que desaparece de la cartelera:** `cancelled`. Si vuelve, `scheduled` otra vez.
- **Evento creado por Wikipedia que desaparece de la lista** (cancelado, o con otra fecha y otro nombre): se borra con sus peleas. Si la lista llega vacía, no se borra nada.
- **Rival por decidir** ("TBD", "TBA"): la pelea no se guarda hasta que se sepa.
- **Nunca se tocan** eventos pasados, peleas con resultado ni el nombre de un evento que viene de UFCStats.

### Alias

Hay cambios de nombre que no se pueden adivinar, como un diminutivo: Wikipedia dice "Abusupiyan Magomedov" y UFCStats, "Abus Magomedov". Se apuntan a mano en [`aliases.csv`](../backend/app/ingestion/aliases.csv): el nombre en la otra fuente y el de UFCStats. Solo si se ha comprobado que es la misma persona.

En cada `upcoming`:
1. Los alias del archivo que falten se guardan en `fighter_aliases`. El archivo está en el repositorio para no perderlos si se vuelve a crear la base de datos.
2. Los peleadores que creó Wikipedia y que ahora se reconocen se unen con los del CSV. Sus peleas pasan al del CSV (la misma fila, con las mismas esquinas) y el duplicado se borra.

El resumen lista los peleadores de las carteleras que no están en el CSV: debutantes o nombres escritos de otra forma. Si hay uno parecido en el CSV, lo sugiere ("Timothy Cuamba: ¿es Timmy Cuamba?"). Es solo una pista: no une a nadie.

### Cuando el CSV trae el evento celebrado

`kaggle_import` busca primero lo que creó Wikipedia:
- **Evento:** misma fecha (±1 día).
- **Debutante:** mismo nombre, solo si en el CSV no hay dos peleadores con ese nombre.
- **Pelea:** mismo evento y mismos dos peleadores. Conserva sus esquinas A y B: puede tener predicciones guardadas, que son P(gana A).
- **Peleas anunciadas que el CSV no trae**, en un evento ya celebrado: `cancelled`.

Así las predicciones hechas antes del evento quedan unidas a su resultado.

Limitaciones:
- Un debutante que se llama igual que un peleador antiguo se toma por él.
- Un nombre escrito muy distinto ("Saint Preux" / "St. Preux") crea un peleador nuevo. Se arreglará con alias.

## The Odds API

Código: [`backend/app/ingestion/odds_api.py`](../backend/app/ingestion/odds_api.py). API oficial con clave gratuita: 500 créditos al mes. Una consulta de cuotas de MMA con 1 mercado (`h2h`) y 1 región (`eu`) cuesta 1 crédito. Caché de 6 horas.

```bash
# en .env (raíz del proyecto):  ODDS_API_KEY=tu_clave
python -m app.ingestion.odds_api
```

- Empareja cada pelea de la API con una pelea programada de la base de datos: mismos dos peleadores (nombre o alias, sin tildes ni mayúsculas) y fecha a ±2 días.
- Orienta las cuotas a nuestras esquinas A y B.
- Lista las peleas que no encuentra. `mma_mixed_martial_arts` incluye otras organizaciones además de UFC.
- `no_vig_probability()` quita el margen de la casa: cuotas 1,50 y 2,70 → P(A) = 0,643.
- Si la API da error, no se muestra la URL porque contiene la clave.

## Permisos

`ufcstats.com/robots.txt` no existe (404, comprobado el 15/10/2026), así que el sitio no declara restricciones. Aun así:
- 1 petición cada 2 segundos como mínimo (`--delay` para cambiarlo).
- Caché en disco (`backend/cache/`). Una página que no cambia no se pide dos veces.
- User-Agent que identifica el proyecto, con la URL del repositorio como contacto.
- Las páginas descargadas no se suben al repositorio.

Wikipedia: ver su sección. Ahí robots.txt se comprueba en cada ejecución.

## Piezas

| Archivo | Qué hace |
|---|---|
| `http.py` | Cliente HTTP común: rate limit por dominio, caché, reintentos (2 s, 4 s, 8 s) y comprobación de robots.txt. Todos los conectores lo usan. |
| `names.py`, `matching.py` | Comparar nombres de peleadores entre fuentes. |
| `aliases.py`, `aliases.csv` | Nombres que otra fuente escribe distinto, apuntados a mano. |
| `ufcstats/parsers.py` | HTML → datos. No toca red ni base de datos. |
| `ufcstats/loader.py` | Datos → base de datos. Upsert por `ufcstats_id`: repetir no duplica. |
| `ufcstats/pipeline.py` | Une las dos: qué páginas pedir, en qué orden y con qué caché. |
| `ufcstats/sample.py` | Descarga 5 páginas reales para probar los parsers. |
| `wikipedia/parsers.py` | HTML de Wikipedia → eventos y peleas. |
| `wikipedia/loader.py` | Eventos y peleas → base de datos, emparejando por nombre y fecha. |
| `wikipedia/__main__.py` | Comandos `upcoming` y `sample`; elige API o artículo según robots.txt. |

## Flujo

```
lista de eventos ──> evento (cartelera + resultados) ──> ficha de cada peleador
                                                    └──> estadísticas de cada pelea (solo celebradas)
```

| Página | Caché |
|---|---|
| Lista de eventos | 6 horas |
| Evento próximo | 6 horas |
| Evento de hace más de 3 días | Para siempre |
| Ficha de peleador | 1 día |
| Estadísticas de pelea | Para siempre |

## Decisiones

- **Esquinas A y B ordenadas por id, no por la web.** UFCStats pone siempre al ganador primero. Si copiáramos ese orden, "gana A" sería cierto casi siempre y el modelo aprendería una trampa.
- **Se guarda pelea a pelea.** Si la descarga se corta, lo ya descargado queda guardado y en caché.
- **Peleas canceladas.** Si una pelea desaparece de la cartelera, se marca `cancelled` en vez de borrarla.

## Uso

```bash
cd backend
python -m app.ingestion.ufcstats upcoming
python -m app.ingestion.ufcstats completed --limit 3
```

Un evento tiene unas 13 peleas y 26 peleadores: unas 40 peticiones, menos de 2 minutos. Todo el histórico (unos 700 eventos) lleva varias horas; se hará en la fase 5.

## Tests

- `test_ufcstats_parsers.py`: páginas de ejemplo escritas a mano con la estructura de UFCStats.
- `test_ingestion.py`: caché, rate limit, reintentos y pipeline completo contra un UFCStats simulado.
- `test_ufcstats_real.py`: páginas reales. Se saltan hasta que ejecutes `python -m app.ingestion.ufcstats.sample`.
- `test_kaggle_import.py`: CSV con las columnas del real; dos filas copian datos reales (Allen vs. Duncan). Prueba resultados, esquinas, estadísticas, cuotas y que importar dos veces no duplica.
- `test_matching.py`: nombres escritos de formas distintas, nombres repetidos y sugerencias de nombres parecidos.
- `test_wikipedia_parsers.py`: páginas de ejemplo escritas a mano con la estructura de Wikipedia (títulos nuevos y antiguos, tabla y listas).
- `test_wikipedia_loader.py`: carteleras que cambian, eventos aplazados o cancelados, descarga simulada por API o por artículo según robots.txt (con páginas que no existen y redirecciones), alias y duplicados, y unión con el CSV cuando el evento ya se celebró.
- `test_wikipedia_real.py`: páginas reales. Se saltan hasta que ejecutes `python -m app.ingestion.wikipedia sample`.

Si `sample` no entiende una página, imprime un diagnóstico (estado HTTP, título, cuántos enlaces hay y un trozo del HTML) para poder ajustar el parser sin tener que enviar el archivo.

Los parsers buscan los datos por la forma de los enlaces (`/event-details/`, `/fight-details/`, `/fighter-details/`) antes que por clases CSS, porque las URLs cambian mucho menos que el diseño.
