"""Tests con páginas REALES de UFCStats.

Se saltan si no existen. Para generarlas, desde backend/:
    python -m app.ingestion.ufcstats.sample
"""
from pathlib import Path

import pytest

from app.ingestion.ufcstats import parsers

REAL = Path(__file__).parent / "fixtures" / "ufcstats" / "real"
pytestmark = pytest.mark.skipif(not REAL.exists(), reason="no hay páginas reales descargadas")


def load(name: str) -> str:
    path = REAL / name
    if not path.exists():
        pytest.skip(f"falta {name}: vuelve a ejecutar el comando sample")
    return path.read_text(encoding="utf-8")


def test_lista_real():
    events = parsers.parse_event_list(load("event_list.html"))
    assert len(events) > 100
    assert all(e.ufcstats_id and e.name for e in events)
    assert sum(e.date is not None for e in events) > 100


def test_evento_real():
    e = parsers.parse_event_detail(load("event_completed.html"), "x")
    assert e.date is not None
    assert len(e.fights) >= 5
    assert all(f.outcome in ("win", "draw", "no_contest") for f in e.fights)
    assert all(f.method in ("KO/TKO", "SUB", "DEC", "DQ", "OTHER") for f in e.fights)
    assert all(f.end_round and f.end_round >= 1 for f in e.fights)


def test_pelea_real():
    d = parsers.parse_fight_detail(load("fight.html"), "x")
    assert d.scheduled_rounds in (3, 5)
    assert len(d.totals) == 2
    assert all(t.sig_strikes_attempted >= t.sig_strikes_landed for t in d.totals)


def test_peleador_real():
    p = parsers.parse_fighter(load("fighter.html"), "x")
    assert p.name
    assert p.wins + p.losses > 0
