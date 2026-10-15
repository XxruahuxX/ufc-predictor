"""Tests con páginas REALES de Wikipedia.

Se saltan si no existen. Para generarlas, desde backend/:
    python -m app.ingestion.wikipedia sample
"""
from pathlib import Path

import pytest

from app.ingestion.wikipedia.parsers import parse_event_card, parse_scheduled_events

REAL = Path(__file__).parent / "fixtures" / "wikipedia" / "real"
pytestmark = pytest.mark.skipif(not REAL.exists(), reason="no hay páginas reales descargadas")


def test_lista_real():
    path = REAL / "list.html"
    if not path.exists():
        pytest.skip("falta list.html: vuelve a ejecutar el comando sample")
    events = parse_scheduled_events(path.read_text(encoding="utf-8"))
    assert events, "no se ha leído ningún evento programado"
    assert all(e.name and e.date for e in events)
    assert len({e.date for e in events}) == len(events)  # una fecha por evento


def test_carteleras_reales():
    pages = sorted(REAL.glob("event_*.html"))
    if not pages:
        pytest.skip("no hay carteleras guardadas: vuelve a ejecutar el comando sample")
    for path in pages:
        bouts = parse_event_card(path.read_text(encoding="utf-8"))
        assert bouts, path.name
        assert all(b.fighter_1 and b.fighter_2 and b.fighter_1 != b.fighter_2 for b in bouts)
        assert all(len(b.fighter_1) < 60 and len(b.fighter_2) < 60 for b in bouts), path.name
