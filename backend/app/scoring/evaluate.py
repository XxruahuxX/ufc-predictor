"""Evaluar cada predicción externa cuando ya se conoce el resultado.

Brier de una predicción = (probabilidad dada al ganador elegido - acierto)²,
con acierto = 1 si ganó y 0 si no. Va de 0 (perfecto) a 1 (peor imposible).
Ejemplos:
    eligió a A con 0.8 y ganó A    (0.8 - 1)² = 0.04
    eligió a A con 0.8 y ganó B    (0.8 - 0)² = 0.64
    eligió a A con 0.5 (moneda)    0.25 gane quien gane

Es lo mismo que calcularlo con P(gana A), la convención del resto de la app.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ExternalPrediction, Fight


@dataclass
class EvaluationReport:
    evaluated: int = 0  # con acierto y Brier
    void: int = 0  # cancelada, empate, sin resultado o el elegido ya no está en la pelea
    pending: int = 0  # la pelea aún no tiene resultado


def brier(confidence: float, correct: bool) -> float:
    return (confidence - (1.0 if correct else 0.0)) ** 2


def evaluate_predictions(session: Session, now: datetime | None = None) -> EvaluationReport:
    """Rellena is_correct y brier de todas las predicciones con resultado.

    Se recalcula todo cada vez: son pocas filas y así, si se corrige un
    resultado, la evaluación se corrige también. No hace commit.
    """
    now = now or datetime.now(timezone.utc)
    report = EvaluationReport()
    predictions = session.scalars(
        select(ExternalPrediction)
        .options(selectinload(ExternalPrediction.fight).selectinload(Fight.result))
    ).all()
    for p in predictions:
        fight, result = p.fight, p.fight.result
        if fight.status == "cancelled":
            report.void += 1
            p.is_correct = p.brier = None
            p.evaluated_at = now
            continue
        if fight.status != "completed" or result is None:
            report.pending += 1
            p.is_correct = p.brier = p.evaluated_at = None
            continue
        p.evaluated_at = now
        if (result.outcome != "win"
                or p.predicted_winner_id not in (fight.fighter_a_id, fight.fighter_b_id)):
            # Un empate no da la razón a nadie. Y si cambió un peleador,
            # la predicción era sobre otra pelea.
            report.void += 1
            p.is_correct = p.brier = None
            continue
        p.is_correct = p.predicted_winner_id == result.winner_id
        p.brier = brier(p.confidence, p.is_correct)
        report.evaluated += 1
    session.flush()
    return report
