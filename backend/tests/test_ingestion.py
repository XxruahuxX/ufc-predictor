"""Tests del cliente HTTP y del pipeline completo (sin red)."""
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.db.models import Event, Fight, Fighter, FightStats
from app.ingestion.http import PoliteClient
from app.ingestion.ufcstats.pipeline import UFCStatsIngestor

FIXTURES = Path(__file__).parent / "fixtures" / "ufcstats"


def fake_ufcstats(request: httpx.Request) -> httpx.Response:
    """Simula UFCStats sirviendo las páginas de ejemplo."""
    path = request.url.path
    if path.endswith("/events/upcoming"):
        name = "event_list.html"
    elif path.endswith("/events/completed"):
        # Un único evento celebrado, que apunta a event_completed.html
        html = (
            '<table><tr class="b-statistics__table-row"><td>'
            '<a class="b-link" href="http://ufcstats.com/event-details/ev0000000000002">UFC 329</a>'
            '<span class="b-statistics__date">October 04, 2026</span></td><td>Abu Dhabi</td></tr></table>'
        )
        return httpx.Response(200, text=html)
    elif "/event-details/ev0000000000001" in path:
        name = "event_upcoming.html"
    elif "/event-details/" in path:
        name = "event_completed.html"
    elif "/fight-details/" in path:
        name = "fight.html"
    else:
        name = "fighter.html"
    return httpx.Response(200, text=(FIXTURES / name).read_text())


def make_client(tmp_path, handler=fake_ufcstats, sleeps=None):
    return PoliteClient(
        cache_dir=tmp_path, min_interval=2.0,
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def test_cache_evita_segunda_descarga(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, text="hola")

    client = make_client(tmp_path, handler)
    assert client.get("http://x.com/a") == "hola"
    assert client.get("http://x.com/a") == "hola"
    assert len(calls) == 1
    client.get("http://x.com/a", max_age=0)  # sin caché
    assert len(calls) == 2


def test_rate_limit_espera_entre_peticiones(tmp_path):
    sleeps = []
    client = make_client(tmp_path, lambda r: httpx.Response(200, text="ok"), sleeps)
    client.get("http://x.com/1")
    client.get("http://x.com/2")
    assert len(sleeps) == 1 and 1.9 < sleeps[0] <= 2.0


def test_reintenta_si_el_servidor_falla(tmp_path):
    responses = iter([httpx.Response(503), httpx.Response(200, text="ok")])
    client = make_client(tmp_path, lambda r: next(responses))
    assert client.get("http://x.com/a") == "ok"


def test_pipeline_eventos_celebrados(session, tmp_path):
    ingestor = UFCStatsIngestor(make_client(tmp_path), session, today=date(2026, 10, 15))
    ingestor.sync_completed(limit=1)

    event = session.scalar(select(Event))
    assert event.status == "completed"
    assert len(event.fights) == 4

    ko = event.fights[0]
    # El ganador (fzz...) aparece primero en la web, pero en la base de datos
    # las esquinas se ordenan por id: A = faa..., B = fzz...
    assert ko.fighter_a.ufcstats_id == "faa000000000001"
    assert ko.result.winner_id == ko.fighter_b_id
    assert ko.is_title_fight and ko.scheduled_rounds == 5

    stats = session.scalars(select(FightStats).where(FightStats.fight_id == ko.id)).all()
    assert len(stats) == 2
    assert stats[0].fight_seconds == 300 + 272  # asalto 2, minuto 4:32


def test_pipeline_es_idempotente(session, tmp_path):
    """Ejecutarlo dos veces no duplica nada."""
    ingestor = UFCStatsIngestor(make_client(tmp_path), session, today=date(2026, 10, 15))
    ingestor.sync_completed(limit=1)
    ingestor.sync_completed(limit=1)
    assert session.scalar(select(func.count(Fight.id))) == 4
    assert session.scalar(select(func.count(FightStats.id))) == 8


def test_pipeline_proximos_eventos(session, tmp_path):
    ingestor = UFCStatsIngestor(make_client(tmp_path), session, today=date(2026, 10, 15))
    ingestor.sync_upcoming()
    events = session.scalars(select(Event)).all()
    assert len(events) == 2
    upcoming = session.scalar(select(Event).where(Event.ufcstats_id == "ev0000000000001"))
    assert upcoming.status == "upcoming"
    assert all(f.status == "scheduled" for f in upcoming.fights)
    assert session.scalar(select(func.count(Fighter.id))) > 0
