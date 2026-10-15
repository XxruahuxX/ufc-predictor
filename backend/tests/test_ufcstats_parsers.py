"""Tests de los parsers con páginas de ejemplo (tests/fixtures/ufcstats)."""
from datetime import date
from pathlib import Path

from app.ingestion.ufcstats import parsers

FIXTURES = Path(__file__).parent / "fixtures" / "ufcstats"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_lista_de_eventos():
    events = parsers.parse_event_list(load("event_list.html"))
    assert len(events) == 2  # la fila vacía se ignora
    assert events[0].ufcstats_id == "ev0000000000001"
    assert events[0].name == "UFC Fight Night: Alpha vs. Bravo"
    assert events[0].date == date(2026, 11, 8)
    assert events[1].location == "Madrid, Spain"


def test_evento_celebrado_con_resultados():
    e = parsers.parse_event_detail(load("event_completed.html"), "ev")
    assert e.name == "UFC 329: Echo vs. Foxtrot"
    assert e.date == date(2026, 10, 4)
    assert len(e.fights) == 4

    ko = e.fights[0]
    assert ko.bout_order == 1
    assert ko.is_title_fight
    assert ko.weight_class == "Lightweight"
    assert ko.outcome == "win"
    assert ko.winner_ufcstats_id == "fzz000000000001"
    assert (ko.method, ko.method_detail) == ("KO/TKO", "Punches")
    assert (ko.end_round, ko.end_time_seconds) == (2, 272)

    dec = e.fights[1]
    assert not dec.is_title_fight
    assert (dec.method, dec.method_detail) == ("DEC", "Unanimous")

    assert e.fights[2].outcome == "draw"
    assert e.fights[2].winner_ufcstats_id is None
    assert e.fights[3].outcome == "no_contest"
    assert e.fights[3].method == "OTHER"


def test_evento_proximo_sin_resultados():
    e = parsers.parse_event_detail(load("event_upcoming.html"), "ev")
    assert len(e.fights) == 2
    assert all(f.outcome is None for f in e.fights)
    assert e.fights[0].fighter_names == ("Alpha Nueve", "Bravo Diez")


def test_estadisticas_de_pelea():
    d = parsers.parse_fight_detail(load("fight.html"), "fi")
    assert d.scheduled_rounds == 5
    a, b = d.totals
    assert a.ufcstats_id == "fzz000000000001"
    assert (a.knockdowns, a.sig_strikes_landed, a.sig_strikes_attempted) == (1, 45, 98)
    assert (a.takedowns_landed, a.takedowns_attempted) == (2, 4)
    assert a.control_seconds == 192
    assert (b.total_strikes_landed, b.total_strikes_attempted) == (41, 95)


def test_ficha_de_peleador():
    p = parsers.parse_fighter(load("fighter.html"), "fzz")
    assert p.name == "Echo Uno"
    assert p.nickname == "The Example"
    assert (p.wins, p.losses, p.draws, p.no_contests) == (23, 8, 0, 1)
    assert p.height_cm == 180.3  # 5' 11"
    assert p.reach_cm == 188.0  # 74"
    assert p.stance == "Southpaw"
    assert p.birth_date == date(1990, 7, 13)


def test_conversiones():
    assert parsers.parse_height_cm("--") is None
    assert parsers.parse_reach_cm("--") is None
    assert parsers.parse_date("--") is None
    assert parsers.parse_clock("--") == 0
    assert parsers.normalize_method("M-DEC") == ("DEC", "Majority")
    assert parsers.normalize_method("SUB") == ("SUB", None)
    assert parsers.normalize_method("DQ") == ("DQ", None)
