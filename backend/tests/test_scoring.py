"""Tests de la fase 4: evaluación de predicciones y puntuación de fuentes."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.models import (Event, ExternalPrediction, Fight, Fighter, FightResult, Odds, Source,
                           SourceScore)
from app.scoring.evaluate import brier, evaluate_predictions
from app.scoring.sources import (consensus, market_score, recency, save_scores, score_sources,
                                 summarize)

HOY = date(2026, 10, 15)


def utc(day: date, hour: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


class Datos:
    """Crea eventos, peleas y predicciones de prueba con pocas líneas."""

    def __init__(self, session):
        self.session = session
        self.n = 0

    def pelea(self, dia: date, ganador: str | None = "A", estado: str = "completed") -> Fight:
        """ganador: "A", "B", "empate" o None (sin resultado)."""
        self.n += 1
        event = Event(name=f"Evento {self.n}", date=dia,
                      status="completed" if estado == "completed" else "upcoming")
        a, b = Fighter(name=f"A{self.n}"), Fighter(name=f"B{self.n}")
        fight = Fight(event=event, fighter_a=a, fighter_b=b, status=estado)
        self.session.add(fight)
        self.session.flush()
        if ganador in ("A", "B"):
            winner = a if ganador == "A" else b
            fight.result = FightResult(winner_id=winner.id, outcome="win", method="DEC",
                                       end_round=3, end_time_seconds=300)
        elif ganador == "empate":
            fight.result = FightResult(outcome="draw", method="DEC", end_round=3, end_time_seconds=300)
        self.session.flush()
        return fight

    def fuente(self, nombre: str) -> Source:
        source = self.session.scalar(select(Source).where(Source.name == nombre))
        if source is None:
            source = Source(name=nombre, kind="media")
            self.session.add(source)
            self.session.flush()
        return source

    def prediccion(self, fuente: str, fight: Fight, elige: str, confianza: float) -> ExternalPrediction:
        p = ExternalPrediction(
            source=self.fuente(fuente), fight=fight, confidence=confianza,
            predicted_winner_id=fight.fighter_a_id if elige == "A" else fight.fighter_b_id,
            published_at=utc(fight.event.date))
        self.session.add(p)
        self.session.flush()
        return p


@pytest.fixture
def datos(session):
    return Datos(session)


# ---------------------------------------------------------------------------
# Evaluación de cada predicción
# ---------------------------------------------------------------------------

def test_brier():
    assert brier(0.8, True) == pytest.approx(0.04)
    assert brier(0.8, False) == pytest.approx(0.64)
    assert brier(0.5, True) == brier(0.5, False) == 0.25


def test_evalua_las_predicciones_con_resultado(session, datos):
    gano_a = datos.pelea(date(2026, 1, 10), "A")
    gano_b = datos.pelea(date(2026, 1, 10), "B")
    acierto = datos.prediccion("F", gano_a, "A", 0.8)
    fallo = datos.prediccion("F", gano_b, "A", 0.7)
    acierto_b = datos.prediccion("G", gano_b, "B", 0.6)

    report = evaluate_predictions(session)
    assert (report.evaluated, report.void, report.pending) == (3, 0, 0)
    assert (acierto.is_correct, acierto.brier) == (True, pytest.approx(0.04))
    assert (fallo.is_correct, fallo.brier) == (False, pytest.approx(0.49))
    assert (acierto_b.is_correct, acierto_b.brier) == (True, pytest.approx(0.16))
    assert acierto.evaluated_at is not None


def test_predicciones_sin_valor_o_pendientes(session, datos):
    empate = datos.prediccion("F", datos.pelea(date(2026, 1, 10), "empate"), "A", 0.7)
    cancelada = datos.prediccion("F", datos.pelea(date(2026, 1, 10), None, "cancelled"), "A", 0.7)
    proxima = datos.prediccion("F", datos.pelea(date(2026, 11, 1), None, "scheduled"), "A", 0.7)
    # Cambió un peleador: la predicción elegía a alguien que ya no está
    cambiada = datos.prediccion("F", datos.pelea(date(2026, 1, 10), "A"), "A", 0.7)
    cambiada.fight.fighter_a = Fighter(name="Sustituto")
    session.flush()

    report = evaluate_predictions(session)
    assert (report.evaluated, report.void, report.pending) == (0, 3, 1)
    for p in (empate, cancelada, proxima, cambiada):
        assert (p.is_correct, p.brier) == (None, None)
    assert proxima.evaluated_at is None and empate.evaluated_at is not None


def test_si_se_corrige_un_resultado_se_corrige_la_evaluacion(session, datos):
    fight = datos.pelea(date(2026, 1, 10), "A")
    p = datos.prediccion("F", fight, "A", 0.8)
    evaluate_predictions(session)
    assert p.is_correct is True
    fight.result.winner_id = fight.fighter_b_id
    evaluate_predictions(session)
    assert (p.is_correct, p.brier) == (False, pytest.approx(0.64))


# ---------------------------------------------------------------------------
# Puntuación de cada fuente
# ---------------------------------------------------------------------------

def test_recencia():
    assert recency(HOY, HOY, 365) == 1
    assert recency(HOY - timedelta(days=365), HOY, 365) == pytest.approx(0.5)
    assert recency(HOY - timedelta(days=730), HOY, 365) == pytest.approx(0.25)


def test_peso_segun_el_brier():
    # Siempre acierta con 0.8: Brier 0.04 -> peso 1 - 0.04 / 0.25 = 0.84
    buena = summarize("Buena", [(HOY, True, 0.04)] * 20, HOY, 20, 365)
    assert (buena.n, buena.accuracy, buena.brier) == (20, 1.0, pytest.approx(0.04))
    assert buena.weight == pytest.approx(0.84)
    # Peor que una moneda: peso 0, nunca negativo
    mala = summarize("Mala", [(HOY, False, 0.49)] * 20, HOY, 20, 365)
    assert mala.weight == 0
    # Muy buena pero con pocas predicciones: aún no tiene peso
    nueva = summarize("Nueva", [(HOY, True, 0.04)] * 19, HOY, 20, 365)
    assert (nueva.n, nueva.weight) == (19, 0)


def test_las_recientes_cuentan_mas():
    # 10 fallos hace dos años y 10 aciertos hoy: el Brier reciente se acerca a los aciertos
    rows = [(HOY - timedelta(days=730), False, 0.64)] * 10 + [(HOY, True, 0.04)] * 10
    score = summarize("F", rows, HOY, 20, 365)
    assert score.brier == pytest.approx(0.34)  # media simple
    assert score.recent_brier == pytest.approx((0.25 * 0.64 + 0.04) / 1.25)  # = 0.16
    assert score.weight == pytest.approx(1 - 0.16 / 0.25)


def test_ranking_de_fuentes(session, datos):
    for i in range(4):
        fight = datos.pelea(date(2026, 1, 10) + timedelta(days=i), "A")
        datos.prediccion("Acierta", fight, "A", 0.7)
        datos.prediccion("Falla", fight, "B", 0.7)
    futura = datos.pelea(date(2026, 9, 1), "A")
    datos.prediccion("Acierta", futura, "B", 0.9)  # posterior a as_of: no cuenta
    datos.fuente("Sin predicciones")
    evaluate_predictions(session)

    scores = score_sources(session, as_of=date(2026, 6, 1), min_n=3)
    assert [(s.name, s.n, s.accuracy) for s in scores] == [("Acierta", 4, 1.0), ("Falla", 4, 0.0)]
    assert scores[0].weight == pytest.approx(1 - 0.09 / 0.25)
    assert scores[1].weight == 0
    # Con la fecha de hoy ya cuenta el fallo de septiembre
    assert score_sources(session, as_of=HOY, min_n=3)[0].n == 5


def test_guardar_la_puntuacion_no_duplica(session, datos):
    fight = datos.pelea(date(2026, 1, 10), "A")
    datos.prediccion("F", fight, "A", 0.7)
    evaluate_predictions(session)
    assert save_scores(session, score_sources(session, HOY)) == 1
    assert save_scores(session, score_sources(session, HOY)) == 0  # nada nuevo
    datos.prediccion("F", datos.pelea(date(2026, 2, 10), "B"), "A", 0.6)
    evaluate_predictions(session)
    assert save_scores(session, score_sources(session, HOY)) == 1  # tras otro evento
    fotos = session.scalars(select(SourceScore).order_by(SourceScore.id)).all()
    assert [(f.n_evaluated, f.accuracy) for f in fotos] == [(1, 1.0), (2, 0.5)]
    assert session.scalar(select(func.count()).select_from(SourceScore)) == 2


# ---------------------------------------------------------------------------
# Mercado (referencia)
# ---------------------------------------------------------------------------

def test_mercado(session, datos):
    favorito_gana = datos.pelea(date(2026, 1, 10), "A")
    sorpresa = datos.pelea(date(2026, 2, 10), "B")
    dia1, dia2 = favorito_gana.event.date, sorpresa.event.date
    session.add_all([
        # Dos casas: se usa la media sin margen
        Odds(fight_id=favorito_gana.id, bookmaker="x", price_a=1.5, price_b=2.7, captured_at=utc(dia1)),
        Odds(fight_id=favorito_gana.id, bookmaker="y", price_a=1.4, price_b=3.0, captured_at=utc(dia1)),
        # De la misma casa cuenta la última antes de acabar el día del evento
        Odds(fight_id=sorpresa.id, bookmaker="x", price_a=3.0, price_b=1.4, captured_at=utc(dia2 - timedelta(days=3))),
        Odds(fight_id=sorpresa.id, bookmaker="x", price_a=1.5, price_b=2.7, captured_at=utc(dia2, 20)),
        Odds(fight_id=sorpresa.id, bookmaker="x", price_a=9.0, price_b=1.05, captured_at=utc(dia2 + timedelta(days=1), 1)),
    ])
    session.flush()
    market = market_score(session, HOY)
    p1 = ((1 / 1.5) / (1 / 1.5 + 1 / 2.7) + (1 / 1.4) / (1 / 1.4 + 1 / 3.0)) / 2
    p2 = (1 / 1.5) / (1 / 1.5 + 1 / 2.7)  # favorito A y ganó B
    assert (market.n, market.accuracy) == (2, 0.5)
    assert market.brier == pytest.approx(((1 - p1) ** 2 + p2 ** 2) / 2)
    assert market.source_id is None
    assert market_score(session, date(2025, 1, 1)) is None


# ---------------------------------------------------------------------------
# Consenso de las fuentes en una pelea
# ---------------------------------------------------------------------------

def test_consenso(session, datos):
    fight = datos.pelea(date(2026, 11, 1), None, "scheduled")
    datos.prediccion("Buena", fight, "A", 0.8)    # P(A) = 0.8
    datos.prediccion("Regular", fight, "B", 0.6)  # P(A) = 0.4
    datos.prediccion("Nueva", fight, "B", 0.9)    # sin peso todavía
    ids = {s.name: s.id for s in session.scalars(select(Source))}
    result = consensus(session, fight, {ids["Buena"]: 0.6, ids["Regular"]: 0.2})
    assert result.prob_a == pytest.approx((0.6 * 0.8 + 0.2 * 0.4) / 0.8)
    assert result.parts == [("Buena", 0.8, 0.6), ("Regular", pytest.approx(0.4), 0.2),
                            ("Nueva", pytest.approx(0.1), 0.0)]
    # Si ninguna fuente que opina tiene peso, no hay consenso
    assert consensus(session, fight, {}).prob_a is None
