"""Tests del conector de The Odds API (sin red: usa una respuesta guardada)."""
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import Event, Fight, Fighter, FighterAlias, Odds
from app.ingestion import odds_api
from app.ingestion.names import normalize_name

SAMPLE = (Path(__file__).parent / "fixtures" / "odds_api" / "mma_odds.json").read_text()


def test_normalizar_nombres():
    assert normalize_name("José  Aldo Jr.") == "jose aldo jr"
    assert normalize_name("JOSE ALDO") == normalize_name("José Aldo")
    assert normalize_name("Jan Błachowicz") == "jan blachowicz"


def test_leer_respuesta():
    events = odds_api.parse_odds(SAMPLE)
    assert len(events) == 2
    e = events[0]
    assert (e.fighter_1, e.fighter_2) == ("Oscar Doce", "Alpha Nueve")
    assert e.commence_time.tzinfo is not None
    unibet = e.prices[0]
    assert unibet.bookmaker == "unibet_eu"
    # price_1 es la cuota del peleador 1 (Oscar), aunque en el JSON venga en otro orden
    assert (unibet.price_1, unibet.price_2) == (2.7, 1.5)
    assert events[1].prices == []


def test_probabilidad_sin_margen():
    p = odds_api.no_vig_probability(1.5, 2.7)
    assert p == pytest.approx(0.643, abs=0.001)
    assert p + odds_api.no_vig_probability(2.7, 1.5) == pytest.approx(1.0)


@pytest.fixture
def pelea_programada(session):
    """Pelea el 8/11/2026: A = Alpha Nueve, B = Óscar Doce (con tilde)."""
    fight = Fight(
        event=Event(name="UFC Test", date=date(2026, 11, 8)),
        fighter_a=Fighter(name="Alpha Nueve"),
        fighter_b=Fighter(name="Óscar Doce"),
    )
    session.add(fight)
    session.commit()
    return fight


def test_guardar_cuotas_orientadas_a_nuestra_pelea(session, pelea_programada):
    saved, unmatched = odds_api.save_odds(session, odds_api.parse_odds(SAMPLE))
    assert saved == 2
    # La segunda pelea de la API no está en la base de datos
    assert [e.odds_api_id for e in unmatched] == ["bbb222"]

    unibet = session.scalar(select(Odds).where(Odds.bookmaker == "unibet_eu"))
    # En la API Oscar es el peleador 1, pero en nuestra pelea Alpha es A:
    # la cuota de A tiene que ser la de Alpha (1.5)
    assert (unibet.price_a, unibet.price_b) == (1.5, 2.7)


def test_guardar_cuotas_dos_veces_no_duplica(session, pelea_programada):
    events = odds_api.parse_odds(SAMPLE)
    odds_api.save_odds(session, events)
    saved, _ = odds_api.save_odds(session, events)
    assert saved == 0
    assert session.scalar(select(func.count(Odds.id))) == 2


def test_empareja_por_alias(session, pelea_programada):
    fighter = pelea_programada.fighter_b
    fighter.name = "Oscar Doce Junior"  # nombre distinto al de la API...
    session.add(FighterAlias(fighter=fighter, alias="Oscar Doce"))  # ...pero con alias
    session.commit()
    saved, _ = odds_api.save_odds(session, odds_api.parse_odds(SAMPLE))
    assert saved == 2


def test_no_empareja_peleas_lejanas_en_el_tiempo(session, pelea_programada):
    pelea_programada.event.date = date(2026, 12, 20)
    session.commit()
    saved, unmatched = odds_api.save_odds(session, odds_api.parse_odds(SAMPLE))
    assert saved == 0 and len(unmatched) == 2
