"""Tests del esquema: se crean las tablas y las restricciones funcionan."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AppPrediction,
    Event,
    ExternalPrediction,
    Fight,
    Fighter,
    FightResult,
    Source,
)


def crear_pelea(session):
    """Crea un evento con una pelea entre dos peleadores."""
    evento = Event(name="UFC Test", date=date(2026, 11, 1))
    a = Fighter(name="Peleador A", reach_cm=190)
    b = Fighter(name="Peleador B", reach_cm=180)
    pelea = Fight(event=evento, fighter_a=a, fighter_b=b, bout_order=1)
    session.add(pelea)
    session.commit()
    return pelea


def test_crear_evento_con_pelea(session):
    pelea = crear_pelea(session)
    evento = session.get(Event, pelea.event_id)
    assert evento.fights[0].fighter_a.name == "Peleador A"


def test_resultado_victoria(session):
    pelea = crear_pelea(session)
    session.add(FightResult(
        fight=pelea, winner_id=pelea.fighter_a_id, outcome="win",
        method="KO/TKO", end_round=2, end_time_seconds=95,
    ))
    session.commit()
    assert pelea.result.method == "KO/TKO"


def test_victoria_sin_ganador_falla(session):
    pelea = crear_pelea(session)
    session.add(FightResult(
        fight=pelea, winner_id=None, outcome="win",
        method="DEC", end_round=3, end_time_seconds=300,
    ))
    with pytest.raises(IntegrityError):
        session.commit()


def test_peleador_contra_si_mismo_falla(session):
    a = Fighter(name="Solo")
    session.add(Fight(event=Event(name="X", date=date(2026, 1, 1)), fighter_a=a, fighter_b=a))
    with pytest.raises(IntegrityError):
        session.commit()


def test_una_prediccion_por_fuente_y_pelea(session):
    pelea = crear_pelea(session)
    fuente = Source(name="r/MMA", kind="reddit")
    ahora = datetime.now(timezone.utc)
    for _ in range(2):
        session.add(ExternalPrediction(
            source=fuente, fight=pelea, predicted_winner_id=pelea.fighter_a_id,
            confidence=0.7, published_at=ahora,
        ))
    with pytest.raises(IntegrityError):
        session.commit()


def test_confianza_fuera_de_rango_falla(session):
    pelea = crear_pelea(session)
    session.add(ExternalPrediction(
        source=Source(name="Tipster", kind="tipster"), fight=pelea,
        predicted_winner_id=pelea.fighter_b_id, confidence=0.3,
        published_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        session.commit()


def test_prediccion_app_intervalo_coherente(session):
    pelea = crear_pelea(session)
    # El intervalo [0.70, 0.80] no contiene 0.60: debe fallar
    session.add(AppPrediction(
        fight=pelea, prob_a_final=0.60, prob_a_low=0.70, prob_a_high=0.80,
    ))
    with pytest.raises(IntegrityError):
        session.commit()
