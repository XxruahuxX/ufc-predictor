# Fase 2 · Esquema de base de datos

Código: [`backend/app/db/models.py`](../backend/app/db/models.py). 12 tablas en PostgreSQL, definidas con SQLAlchemy 2.0.

## Convención clave

Cada pelea tiene un **peleador A** (esquina roja) y un **peleador B** (esquina azul). Todas las probabilidades se guardan como **P(gana A)**. Si una fuente predice a B con confianza 0,7, internamente es P(A) = 0,3.

## Diagrama entidad-relación

```mermaid
erDiagram
    events ||--o{ fights : "tiene"
    fighters ||--o{ fights : "A o B"
    fighters ||--o{ fighter_aliases : "otros nombres"
    fights ||--o| fight_results : "resultado"
    fights ||--o{ fight_stats : "2 filas, una por peleador"
    fights ||--o{ odds : "cuotas"
    sources ||--o{ external_predictions : "hace"
    fights ||--o{ external_predictions : "sobre"
    sources ||--o{ source_scores : "historial de precisión"
    fights ||--o{ app_predictions : "predicción de la app"
    model_versions ||--o{ app_predictions : "usada en"

    events {
        int id PK
        string ufcstats_id UK
        string name
        date date
        string status
    }
    fighters {
        int id PK
        string ufcstats_id UK
        string name
        date birth_date
        float height_cm
        float reach_cm
        string stance
        int wins
        int losses
    }
    fights {
        int id PK
        int event_id FK
        int fighter_a_id FK
        int fighter_b_id FK
        int scheduled_rounds
        int bout_order
        string status
    }
    fight_results {
        int fight_id PK
        int winner_id FK
        string outcome
        string method
        int end_round
    }
    fight_stats {
        int fight_id FK
        int fighter_id FK
        int sig_strikes_landed
        int takedowns_landed
        int fight_seconds
    }
    sources {
        int id PK
        string name UK
        string kind
        float default_confidence
    }
    external_predictions {
        int source_id FK
        int fight_id FK
        int predicted_winner_id FK
        float confidence
        bool is_correct
        float brier
    }
    source_scores {
        int source_id FK
        datetime computed_at
        int n_evaluated
        float accuracy
        float brier
        float weight
    }
    odds {
        int fight_id FK
        string bookmaker
        float price_a
        float price_b
        datetime captured_at
    }
    model_versions {
        int id PK
        string version UK
        string algorithm
        json metrics
        bool is_active
    }
    app_predictions {
        int fight_id FK
        float prob_a_model
        float prob_a_sources
        float prob_a_market
        float prob_a_final
        float prob_a_low
        float prob_a_high
        bool is_final
    }
```

## Tablas

| Tabla | Para qué |
|---|---|
| `events` | Eventos. `status`: upcoming o completed. |
| `fighters` | Ficha del peleador. Fecha de nacimiento, no edad. |
| `fighter_aliases` | Cómo aparece el peleador en cada fuente ("Poatan" → Alex Pereira). |
| `fights` | Peleas de un evento, con orden en la cartelera. |
| `fight_results` | Resultado real: ganador, método, asalto y tiempo. |
| `fight_stats` | Totales de cada peleador en cada pelea (golpes, derribos, control). |
| `sources` | Fuentes de predicciones externas. |
| `external_predictions` | Una predicción por fuente y pelea. Se evalúa al llegar el resultado. |
| `source_scores` | Foto de acierto, Brier y peso de cada fuente tras cada evento. |
| `odds` | Cuotas decimales por casa y momento. |
| `model_versions` | Cada modelo entrenado, con sus métricas. Solo uno activo. |
| `app_predictions` | Predicción final con desglose (modelo, fuentes, mercado), pesos e intervalo. |

## Decisiones

- **Fecha de nacimiento en vez de edad.** La edad cambia. Para el backtesting necesitamos la edad en la fecha de cada pelea.
- **Totales por pelea en vez de medias.** UFCStats publica medias de toda la carrera, que incluyen peleas futuras respecto a una pelea pasada. Si el modelo aprendiera con ellas haría trampa (fuga de datos). Guardando totales por pelea, calculamos las medias con solo las peleas anteriores.
- **Racha y últimas peleas no se guardan.** Se calculan desde `fights` y `fight_results`. Un dato guardado dos veces acaba desincronizado.
- **`fight_results` separada de `fights`.** Una pelea próxima no tiene resultado. Así no hay columnas vacías y las restricciones son más claras.
- **Restricciones en la base de datos.** Por ejemplo: un peleador no puede pelear contra sí mismo, confianza entre 0,5 y 1, una victoria siempre tiene ganador. Los errores de los scrapers se detectan al guardar, no semanas después.
- **`is_final` en `app_predictions`.** La app recalcula muchas veces antes del evento. Solo la última, congelada antes de la pelea, cuenta para el histórico. Así nadie puede decir que la app "predijo" con información posterior.
- **Sin Alembic todavía.** De momento `create_all`. Cuando el esquema deje de cambiar (tras la fase 5) añadiremos migraciones.

## Cómo probarlo

```bash
cp .env.example .env
docker compose up -d db                    # arranca PostgreSQL
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.db.init_db                   # crea las tablas
pytest                                     # 7 tests en SQLite
TEST_DATABASE_URL=postgresql+psycopg://ufc:ufc@localhost:5432/ufc pytest   # en Postgres
```
