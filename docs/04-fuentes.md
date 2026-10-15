# Fase 4 · Puntuación de fuentes

Código: [`backend/app/scoring/`](../backend/app/scoring) y [`backend/app/ingestion/predictions.py`](../backend/app/ingestion/predictions.py).

Cada fuente externa (un medio, un tipster, un foro) da predicciones. Cuando llega el resultado, se mira si acertó y se le da un peso según su historial. En la predicción final (fase 5), las fuentes con más peso cuentan más.

## Uso

Desde `backend/`:

```bash
python -m app.ingestion.predictions predicciones.csv   # importa predicciones
python -m app.scoring                                  # evalúa y muestra el ranking
python -m app.scoring --hasta 2025-12-31               # el ranking tal como estaba ese día
```

Salida de ejemplo (datos inventados):

```
Predicciones evaluadas: 50. Sin valor (cancelada, empate o cambio de rival): 0. Pendientes de resultado: 0.

Ranking de fuentes hasta el 2026-10-15
Peso 0 = menos de 20 predicciones evaluadas o no mejora a decir siempre 50 %.
  Fuente                       Evaluadas  Acierto  Brier  Peso
  MMA Junkie                          40   57.5%  0.250  0.00
  Tipster X                           10   60.0%  0.280  0.00
  Mercado (cuotas)                    40   50.0%  0.260     —  referencia
Fuentes sin predicciones evaluadas: Reddit r/MMA
```

## Importar predicciones

De momento se apuntan a mano en un CSV. Automatizar una fuente concreta exige revisar antes su robots.txt y sus términos, como en la fase 3.

```
fuente,tipo,fecha_evento,ganador,rival,confianza,metodo,publicado,url
MMA Junkie,media,2026-10-24,Movsar Evloev,Alexander Volkanovski,65 %,DEC,2026-10-20,https://...
Tipster X,tipster,2026-10-24,Jiri Prochazka,Khalil Rountree,,,,
```

| Columna | Obligatoria | Valores |
|---|---|---|
| `fuente` | sí | Nombre. Si no existe, se crea. |
| `tipo` | sí | `website`, `reddit`, `tipster`, `forum` o `media` |
| `fecha_evento` | sí | `AAAA-MM-DD`. Vale un día de diferencia, por la zona horaria. |
| `ganador`, `rival` | sí | Nombres. Se comparan igual que en la fase 3: sin tildes, con alias, sin "Jr.". |
| `confianza` | no | `0.65`, `65` o `65 %`, entre 50 y 100 %. Vacío: la de la fuente (60 %). |
| `metodo` | no | `KO/TKO`, `SUB` o `DEC` |
| `publicado` | no | `AAAA-MM-DD`. Vacío: el día del evento. |
| `url` | no | De dónde sale, para poder comprobarla. |

- Si la fuente ya tenía una predicción para esa pelea, se sustituye: cuenta la última antes del evento.
- Una fila con un error no se guarda y no deja nada a medias. El resumen dice la línea y el motivo.

## Evaluar cada predicción

[`evaluate.py`](../backend/app/scoring/evaluate.py). Cuando la pelea tiene resultado:

- `is_correct`: si ganó el elegido.
- `brier` = (confianza − acierto)², con acierto 1 o 0. De 0 (perfecto) a 1.

| Predicción | Resultado | Brier |
|---|---|---|
| A con 80 % | gana A | (0,8 − 1)² = 0,04 |
| A con 80 % | gana B | (0,8 − 0)² = 0,64 |
| A con 50 % | cualquiera | 0,25 |

No cuentan (ni a favor ni en contra): peleas canceladas, empates, sin resultado (no contest) y predicciones que eligieron a un peleador que ya no está en la pelea (cambio de rival).

Se recalcula todo en cada ejecución. Si se corrige un resultado, la evaluación se corrige sola.

## Peso de cada fuente

[`sources.py`](../backend/app/scoring/sources.py).

1. Solo cuentan los eventos hasta la fecha `--hasta`. En el backtesting (fase 5) será el día antes de cada pelea, para no usar resultados que entonces no se conocían.
2. Menos de 20 predicciones evaluadas: peso 0. Con pocas no se distingue una buena fuente de una con suerte.
3. **Brier reciente**: media del Brier donde cada predicción cuenta 0,5^(días / 365). Una de hace un año cuenta la mitad que una de hoy.
4. **Peso** = 1 − Brier reciente / 0,25, mínimo 0. 0,25 es el Brier de decir siempre 50 %: quien no mejora a una moneda no aporta nada.

Ejemplo: una fuente que acierta siempre con 80 % tiene Brier 0,04 y peso 1 − 0,04 / 0,25 = 0,84.

Por qué el Brier y no solo el % de acierto: premia que la confianza sea realista. Acertar con 90 % vale más que con 55 %, y fallar con 90 % se castiga más.

Los dos números se cambian en `.env`:

```
SOURCE_MIN_PREDICTIONS=20
SOURCE_HALF_LIFE_DAYS=365
```

El ranking muestra el acierto y el Brier de todo el historial, que son más fáciles de leer. El peso usa el Brier reciente.

## Consenso de una pelea

`consensus()` combina las fuentes que opinaron de una pelea: media ponderada de P(gana A) con el peso de cada una. Si eligen a B con 60 %, P(A) = 0,4. Las fuentes con peso 0 salen en el detalle, pero no cuentan. Si ninguna tiene peso, no hay consenso y en la fase 5 manda el resto (modelo y mercado).

## El mercado como referencia

Las cuotas no son una fuente: son otro componente de la predicción final (fase 1). El ranking muestra su acierto y Brier solo para comparar. Si una fuente no mejora al mercado, aporta poco.

Por pelea se usa la última cuota de cada casa tomada hasta el día del evento, sin el margen de la casa, y la media entre casas. Las cuotas del CSV de Kaggle cuentan como una casa.

## Historial

Cada ejecución con la fecha de hoy guarda una fila por fuente en `source_scores`, solo si tiene predicciones evaluadas nuevas (tras un evento). Así se ve cómo cambia el peso de cada fuente con el tiempo, sin filas repetidas.

## Unión de peleadores

Si un peleador creado desde Wikipedia resulta ser uno del CSV (fase 3), sus predicciones pasan al del CSV (`move_picks`). Antes de este cambio, unir dos peleadores con predicciones guardadas daba un error de clave foránea.

## Tests

- `test_predictions.py`: importación, formatos de confianza, alias y tildes, cambio de opinión y filas con errores.
- `test_scoring.py`: Brier, predicciones sin valor, corrección de resultados, peso, recencia, fecha límite, mercado, historial y consenso.
- `test_wikipedia_loader.py`: las predicciones siguen al peleador al unir duplicados y al adoptar peleas desde el CSV.

## Pendiente

- No hay predicciones reales todavía. Falta elegir la primera fuente y revisar sus términos antes de automatizarla.
