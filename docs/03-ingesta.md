# Fase 3 · Ingesta de UFCStats

Código: [`backend/app/ingestion/`](../backend/app/ingestion/).

## Permisos

`ufcstats.com/robots.txt` no existe (404, comprobado el 15/10/2026), así que el sitio no declara restricciones. Aun así:
- 1 petición cada 2 segundos como mínimo (`--delay` para cambiarlo).
- Caché en disco (`backend/cache/`). Una página que no cambia no se pide dos veces.
- User-Agent que identifica el proyecto.
- Las páginas descargadas no se suben al repositorio.

## Piezas

| Archivo | Qué hace |
|---|---|
| `http.py` | Cliente HTTP común: rate limit por dominio, caché y reintentos (2 s, 4 s, 8 s). Todos los conectores futuros (Reddit, cuotas...) lo usarán. |
| `ufcstats/parsers.py` | HTML → datos. No toca red ni base de datos. |
| `ufcstats/loader.py` | Datos → base de datos. Upsert por `ufcstats_id`: repetir no duplica. |
| `ufcstats/pipeline.py` | Une las dos: qué páginas pedir, en qué orden y con qué caché. |
| `ufcstats/sample.py` | Descarga 5 páginas reales para probar los parsers. |

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

Si `sample` no entiende una página, imprime un diagnóstico (estado HTTP, título, cuántos enlaces hay y un trozo del HTML) para poder ajustar el parser sin tener que enviar el archivo.

Los parsers buscan los datos por la forma de los enlaces (`/event-details/`, `/fight-details/`, `/fighter-details/`) antes que por clases CSS, porque las URLs cambian mucho menos que el diseño.
