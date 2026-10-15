"""Acierto, Brier y peso de cada fuente, y el consenso de las fuentes en una pelea.

Peso de una fuente
------------------
1. Solo cuentan las predicciones de eventos hasta la fecha `as_of`. En el
   backtesting (fase 5) se pasa el día antes de cada pelea: así el peso no
   usa resultados que entonces no se conocían.
2. Si la fuente tiene menos de `min_n` predicciones evaluadas, peso 0.
   Con 5 aciertos seguidos no se sabe si es buena o ha tenido suerte.
3. Brier reciente: media del Brier en la que cada predicción cuenta
   0.5 ^ (días de antigüedad / half_life). Con half_life = 365, una de hace
   un año cuenta la mitad que una de hoy, y una de hace dos años, un cuarto.
4. Peso = 1 - Brier reciente / 0.25, y 0 si sale negativo. 0.25 es el
   Brier de decir siempre 50 %: una fuente que no mejora a una moneda
   no aporta nada. Una perfecta (Brier 0) tendría peso 1.

Se usa el Brier y no solo el % de acierto porque premia que la confianza
sea realista: acertar con 90 % vale más que acertar con 55 %, y fallar
con 90 % se castiga más.

El mercado (las cuotas) no es una fuente: es otro componente de la
predicción final. Aquí se calcula su acierto y Brier solo como referencia,
para ver si una fuente mejora o no a las casas de apuestas.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (Event, ExternalPrediction, Fight, FightResult, Odds, Source,
                           SourceScore)
from app.ingestion.odds_api import no_vig_probability

COIN_FLIP_BRIER = 0.25  # Brier de decir siempre 50 %


@dataclass
class Score:
    """Resumen de una fuente (o del mercado) hasta una fecha."""
    name: str
    n: int  # predicciones evaluadas
    accuracy: float  # de 0 a 1
    brier: float  # media de todas; menor es mejor
    recent_brier: float  # media dando más peso a las recientes
    weight: float  # 0 si no llega al mínimo
    source_id: int | None = None  # None para el mercado


@dataclass
class Consensus:
    """Lo que dicen las fuentes de una pelea, combinado."""
    prob_a: float | None  # media ponderada de P(gana A); None si ninguna fuente con peso
    # Detalle de cada fuente que opinó: (nombre, P(gana A), peso)
    parts: list[tuple[str, float, float]] = field(default_factory=list)


def recency(day: date, as_of: date, half_life: float) -> float:
    """Cuánto cuenta una predicción de ese día: 1 hoy, 0.5 hace half_life días."""
    return 0.5 ** (max((as_of - day).days, 0) / half_life)


def summarize(name: str, rows: list[tuple[date, bool, float]], as_of: date,
              min_n: int, half_life: float) -> Score:
    """rows: (día del evento, acertó, Brier) de cada predicción evaluada."""
    n = len(rows)
    weights = [recency(day, as_of, half_life) for day, _, _ in rows]
    recent = sum(w * b for w, (_, _, b) in zip(weights, rows)) / sum(weights)
    weight = max(0.0, 1 - recent / COIN_FLIP_BRIER) if n >= min_n else 0.0
    return Score(
        name=name, n=n,
        accuracy=sum(correct for _, correct, _ in rows) / n,
        brier=sum(b for _, _, b in rows) / n,
        recent_brier=recent, weight=weight,
    )


def score_sources(session: Session, as_of: date, min_n: int | None = None,
                  half_life: float | None = None) -> list[Score]:
    """Puntuación de cada fuente con predicciones evaluadas, de más a menos peso."""
    min_n = settings.source_min_predictions if min_n is None else min_n
    half_life = half_life or settings.source_half_life_days
    query = (
        select(Source.id, Source.name, Event.date, ExternalPrediction.is_correct,
               ExternalPrediction.brier)
        .join(ExternalPrediction.source).join(ExternalPrediction.fight).join(Fight.event)
        .where(ExternalPrediction.brier.is_not(None), Event.date <= as_of)
    )
    by_source = defaultdict(list)
    names = {}
    for source_id, name, day, correct, b in session.execute(query):
        by_source[source_id].append((day, correct, b))
        names[source_id] = name
    scores = []
    for source_id, rows in by_source.items():
        score = summarize(names[source_id], rows, as_of, min_n, half_life)
        score.source_id = source_id
        scores.append(score)
    return sorted(scores, key=lambda s: (-s.weight, s.brier, s.name))


def market_score(session: Session, as_of: date, half_life: float | None = None) -> Score | None:
    """Acierto y Brier del favorito de las casas de apuestas (referencia).

    Por pelea: la última cuota de cada casa tomada hasta el día del evento,
    sin el margen de la casa, y la media entre casas.
    """
    half_life = half_life or settings.source_half_life_days
    query = (
        select(Fight.id, Fight.fighter_a_id, Event.date, FightResult.winner_id,
               Odds.bookmaker, Odds.captured_at, Odds.price_a, Odds.price_b)
        .join(Fight.event).join(Fight.result).join(Odds, Odds.fight_id == Fight.id)
        .where(FightResult.outcome == "win", Event.date <= as_of)
    )
    latest = {}  # (pelea, casa) -> fila con la cuota más reciente válida
    for row in session.execute(query):
        end_of_day = datetime.combine(row.date + timedelta(days=1), time.min, timezone.utc)
        captured = row.captured_at
        if captured.tzinfo is None:  # SQLite no guarda la zona horaria
            captured = captured.replace(tzinfo=timezone.utc)
        if captured >= end_of_day:
            continue  # tomada después del evento: no vale
        key = (row.id, row.bookmaker)
        if key not in latest or captured > latest[key][0]:
            latest[key] = (captured, row)
    probs = defaultdict(list)  # pelea -> P(gana A) de cada casa
    info = {}
    for (fight_id, _), (_, row) in latest.items():
        probs[fight_id].append(no_vig_probability(row.price_a, row.price_b))
        info[fight_id] = (row.date, row.winner_id == row.fighter_a_id)
    rows = []
    for fight_id, ps in probs.items():
        p_a = sum(ps) / len(ps)
        day, a_won = info[fight_id]
        rows.append((day, (p_a >= 0.5) == a_won, (p_a - a_won) ** 2))
    if not rows:
        return None
    score = summarize("Mercado (cuotas)", rows, as_of, 0, half_life)
    score.weight = 0.0  # no es una fuente: su peso se decide en la fase 5
    return score


def save_scores(session: Session, scores: list[Score]) -> int:
    """Guarda una foto de la puntuación de cada fuente (tabla source_scores).

    Solo si la fuente tiene predicciones evaluadas nuevas desde la última
    foto, es decir, tras un evento. Ejecutarlo dos veces seguidas no duplica.
    Devuelve cuántas fotos ha guardado.
    """
    saved = 0
    for s in scores:
        if s.source_id is None:
            continue
        last = session.scalar(select(SourceScore).where(SourceScore.source_id == s.source_id)
                              .order_by(SourceScore.id.desc()).limit(1))
        if last is not None and last.n_evaluated == s.n:
            continue
        session.add(SourceScore(source_id=s.source_id, n_evaluated=s.n,
                                accuracy=s.accuracy, brier=s.brier, weight=s.weight))
        saved += 1
    session.flush()
    return saved


def consensus(session: Session, fight: Fight, weights: dict[int, float]) -> Consensus:
    """Media ponderada de lo que dicen las fuentes de esta pelea, como P(gana A).

    weights: id de fuente -> peso (de score_sources). Las fuentes con peso 0
    aparecen en el detalle pero no cuentan en la media. Como solo cuentan las
    fuentes que opinaron de esta pelea, los pesos se reparten entre ellas.
    """
    result = Consensus(prob_a=None)
    predictions = session.scalars(
        select(ExternalPrediction).where(ExternalPrediction.fight_id == fight.id)
    ).all()
    total = weighted = 0.0
    for p in predictions:
        if p.predicted_winner_id == fight.fighter_a_id:
            prob_a = p.confidence
        elif p.predicted_winner_id == fight.fighter_b_id:
            prob_a = 1 - p.confidence
        else:
            continue  # eligió a alguien que ya no está en la pelea
        weight = weights.get(p.source_id, 0.0)
        result.parts.append((p.source.name, prob_a, weight))
        total += weight
        weighted += weight * prob_a
    if total > 0:
        result.prob_a = weighted / total
    result.parts.sort(key=lambda part: -part[2])
    return result
