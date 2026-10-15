"""Tests del conector de Wikipedia: guardado, descarga y unión con el CSV de Kaggle.

Sin red: las descargas se simulan con las páginas de tests/fixtures/wikipedia.
"""
import json
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Event, Fight, Fighter, FightResult
from app.ingestion.http import PoliteClient
from app.ingestion.kaggle_import import import_csv
from app.ingestion.wikipedia.__main__ import WikipediaReader, read_schedule
from app.ingestion.wikipedia.loader import sync_upcoming
from app.ingestion.wikipedia.parsers import Bout, ScheduledEvent, WikipediaError
from tests.test_kaggle_import import CAMILO, HERBERT, escribir_csv, filas_de_prueba, pelea, peleador

FIXTURES = Path(__file__).parent / "fixtures" / "wikipedia"
HOY = date(2026, 10, 15)


def contar(session, model):
    return session.scalar(select(func.count()).select_from(model))


def nuevo_peleador(session, nombre, categoria="Lightweight", ufcstats_id=None):
    fighter = Fighter(name=nombre, weight_class=categoria, ufcstats_id=ufcstats_id)
    session.add(fighter)
    session.flush()
    return fighter


def evento(nombre, dia, pagina="pagina"):
    return ScheduledEvent(name=nombre, date=dia, location="Meta Apex, Las Vegas", page=pagina)


def combate(f1, f2, categoria="Lightweight", titulo=False, estelar=False):
    return Bout(f1, f2, categoria, titulo, estelar)


def peleas_de(session, nombre_evento):
    event = session.scalar(select(Event).where(Event.name == nombre_evento))
    return {(f.fighter_a.name, f.fighter_b.name): f for f in event.fights}


@pytest.fixture
def conocidos(session):
    """Peleadores que ya están en la base de datos (vienen del CSV)."""
    for nombre, categoria, fid in [
        ("Islam Makhachev", "Lightweight", "f001"), ("Ian Machado Garry", "Welterweight", "f002"),
        ("Brendan Allen", "Middleweight", "f003"), ("Jiri Prochazka", "Light Heavyweight", "f004"),
        ("Khalil Rountree Jr.", "Light Heavyweight", "f005"),
    ]:
        nuevo_peleador(session, nombre, categoria, fid)


# ---------------------------------------------------------------------------
# Guardado (loader)
# ---------------------------------------------------------------------------

def test_crea_eventos_peleas_y_debutantes(session, conocidos):
    schedule = [
        (evento("UFC 330: Makhachev vs. Machado Garry", date(2026, 11, 15)), [
            combate("Islam Makhachev", "Ian Machado Garry", titulo=True, estelar=True),
            combate("Jiří Procházka", "Khalil Rountree", "Light Heavyweight"),
            combate("Brendan Allen", "Nuevo Debutante", "Catchweight (185 lb)"),
        ]),
        (evento("UFC Fight Night 289", date(2026, 11, 22), pagina=None), None),
        (evento("UFC Fight Night: Ya celebrado", date(2026, 10, 10)), [combate("A Uno", "B Dos")]),
    ]
    report = sync_upcoming(session, schedule, HOY)
    session.commit()

    # El evento ya celebrado no se toca: sus resultados llegan con el CSV
    assert (report.events_new, report.fights_new) == (2, 3)
    assert contar(session, Event) == 2
    ufc_330 = session.scalar(select(Event).where(Event.date == date(2026, 11, 15)))
    assert (ufc_330.status, ufc_330.location, ufc_330.ufcstats_id) == ("upcoming", "Meta Apex, Las Vegas", None)

    peleas = peleas_de(session, "UFC 330: Makhachev vs. Machado Garry")
    # Esquinas por orden alfabético: Ian < Islam, Brendan < Nuevo
    estelar = peleas[("Ian Machado Garry", "Islam Makhachev")]
    assert (estelar.bout_order, estelar.scheduled_rounds, estelar.is_title_fight) == (1, 5, True)
    assert estelar.status == "scheduled"
    # "Jiří Procházka" y "Khalil Rountree" son los del CSV, escritos de otra forma
    prochazka = peleas[("Jiri Prochazka", "Khalil Rountree Jr.")]
    assert (prochazka.bout_order, prochazka.scheduled_rounds) == (2, 3)

    debutante = session.scalar(select(Fighter).where(Fighter.name == "Nuevo Debutante"))
    assert debutante.ufcstats_id is None
    assert debutante.weight_class is None  # peso pactado: no es su categoría
    assert report.fighters_new == ["Nuevo Debutante (UFC 330: Makhachev vs. Machado Garry)"]
    assert contar(session, Fighter) == 6


def test_repetir_no_duplica(session, conocidos):
    schedule = [(evento("UFC 330", date(2026, 11, 15)), [
        combate("Islam Makhachev", "Ian Machado Garry", estelar=True),
        combate("Brendan Allen", "Nuevo Debutante"),
    ])]
    sync_upcoming(session, schedule, HOY)
    session.commit()
    report = sync_upcoming(session, schedule, HOY)
    session.commit()

    assert (report.events_new, report.events_updated) == (0, 1)
    assert (report.fights_new, report.fights_updated, report.fights_cancelled) == (0, 2, 0)
    assert report.fighters_new == []
    assert (contar(session, Event), contar(session, Fight), contar(session, Fighter)) == (1, 2, 6)


def test_cambios_en_las_carteleras(session, conocidos):
    sync_upcoming(session, [
        (evento("UFC Fight Night 288", date(2026, 11, 8)), [
            combate("Islam Makhachev", "Ian Machado Garry", estelar=True),
            combate("Brendan Allen", "Nuevo Debutante"),
        ]),
        (evento("UFC 331", date(2026, 11, 22)), None),
        (evento("UFC 332", date(2026, 12, 5)), None),
    ], HOY)
    session.commit()
    ids = {e.name: e.id for e in session.scalars(select(Event))}

    report = sync_upcoming(session, [
        # Se anuncia la estelar: cambia el nombre, no la fecha. Allen cambia de rival.
        (evento("UFC Fight Night: Allen vs. Prochazka", date(2026, 11, 8)), [
            combate("Brendan Allen", "Jiri Prochazka", estelar=True),
            combate("Islam Makhachev", "Ian Machado Garry"),
        ]),
        (evento("UFC 331", date(2026, 11, 29)), None),  # aplazado una semana
        # UFC 332 ya no está en la lista
    ], HOY)
    session.commit()

    assert report.events_deleted == ["2026-12-05 UFC 332"]
    eventos = {e.name: e for e in session.scalars(select(Event))}
    assert eventos["UFC Fight Night: Allen vs. Prochazka"].id == ids["UFC Fight Night 288"]
    assert eventos["UFC 331"].id == ids["UFC 331"]
    assert eventos["UFC 331"].date == date(2026, 11, 29)
    assert "UFC 332" not in eventos

    peleas = peleas_de(session, "UFC Fight Night: Allen vs. Prochazka")
    assert peleas[("Brendan Allen", "Nuevo Debutante")].status == "cancelled"
    assert peleas[("Brendan Allen", "Jiri Prochazka")].bout_order == 1
    assert peleas[("Brendan Allen", "Jiri Prochazka")].scheduled_rounds == 5
    makhachev = peleas[("Ian Machado Garry", "Islam Makhachev")]
    assert (makhachev.bout_order, makhachev.scheduled_rounds, makhachev.status) == (2, 3, "scheduled")
    assert (report.fights_new, report.fights_updated, report.fights_cancelled) == (1, 1, 1)


def test_una_pelea_cancelada_puede_volver(session, conocidos):
    def sync(bouts):
        sync_upcoming(session, [(evento("UFC 330", date(2026, 11, 15)), bouts)], HOY)
        session.commit()

    estelar = combate("Islam Makhachev", "Ian Machado Garry", estelar=True)
    otra = combate("Brendan Allen", "Jiri Prochazka")
    sync([estelar, otra])
    sync([estelar])
    sync([estelar, otra])
    assert peleas_de(session, "UFC 330")[("Brendan Allen", "Jiri Prochazka")].status == "scheduled"
    assert contar(session, Fight) == 2


def test_lista_vacia_no_borra_nada(session, conocidos):
    sync_upcoming(session, [(evento("UFC 330", date(2026, 11, 15)), None)], HOY)
    session.commit()
    report = sync_upcoming(session, [], HOY)  # p. ej. la tabla cambió y no se entendió
    assert report.events_deleted == []
    # Solo eventos ya pasados: tampoco es una lista de próximos eventos válida
    report = sync_upcoming(session, [(evento("UFC 329", date(2026, 10, 1)), None)], HOY)
    session.commit()
    assert report.events_deleted == []
    assert contar(session, Event) == 1


def test_pagina_sin_peleas_no_cancela_nada(session, conocidos):
    bouts = [combate("Islam Makhachev", "Ian Machado Garry", estelar=True)]
    sync_upcoming(session, [(evento("UFC 330", date(2026, 11, 15)), bouts)], HOY)
    sync_upcoming(session, [(evento("UFC 330", date(2026, 11, 15)), [])], HOY)
    session.commit()
    assert session.scalar(select(Fight)).status == "scheduled"


def test_nombre_repetido_no_se_adivina(session):
    nuevo_peleador(session, "Bruno Silva", "Middleweight", "b001")
    mosca = nuevo_peleador(session, "Bruno Silva", "Flyweight", "b002")
    nuevo_peleador(session, "Rival Uno", "Flyweight", "r001")
    nuevo_peleador(session, "Rival Dos", "Bantamweight", "r002")

    report = sync_upcoming(session, [(evento("UFC 330", date(2026, 11, 15)), [
        combate("Bruno Silva", "Rival Uno", "Flyweight"),  # la categoría lo aclara
        combate("Bruno Silva", "Rival Dos", "Bantamweight"),  # no lo aclara
    ])], HOY)
    session.commit()

    fight = session.scalar(select(Fight))
    assert contar(session, Fight) == 1
    assert mosca in (fight.fighter_a, fight.fighter_b)
    assert report.skipped == ["UFC 330: Bruno Silva vs. Rival Dos (2 peleadores se llaman Bruno Silva)"]
    assert report.fighters_new == []


def test_no_toca_eventos_de_ufcstats_ni_peleas_con_resultado(session, conocidos):
    makhachev, garry = (session.scalar(select(Fighter).where(Fighter.name == n))
                        for n in ("Islam Makhachev", "Ian Machado Garry"))
    event = Event(ufcstats_id="e001", name="UFC 330: Makhachev vs. Garry", date=HOY,
                  location=None, status="upcoming")
    fight = Fight(event=event, fighter_a=garry, fighter_b=makhachev, status="completed")
    fight.result = FightResult(winner_id=None, outcome="draw", method="DEC", end_round=5,
                               end_time_seconds=300)
    session.add_all([event, fight])
    session.commit()

    sync_upcoming(session, [(evento("UFC 330", HOY), [
        combate("Islam Makhachev", "Ian Machado Garry", estelar=True),
    ])], HOY)
    session.commit()

    assert event.name == "UFC 330: Makhachev vs. Garry"  # el nombre de UFCStats se queda
    assert event.location == "Meta Apex, Las Vegas"  # solo se rellena lo que faltaba
    assert (fight.status, fight.scheduled_rounds) == ("completed", 3)
    assert contar(session, Fight) == 1


# ---------------------------------------------------------------------------
# Descarga: robots.txt decide si se usa la API o la página del artículo
# ---------------------------------------------------------------------------

PAGES = {
    "List_of_UFC_events": "list_of_ufc_events.html",
    "UFC_330": "event_card.html",
    "UFC_Fight_Night:_Medić_vs._Rodriguez": "event_announced.html",
}  # UFC_Fight_Night_287 no está: la web responde 404


def fake_wikipedia(robots: str | None, requests: list):
    """Simula Wikipedia: robots.txt, la API y las páginas de los artículos."""
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        if path == "/w/api.php":
            name = PAGES.get(request.url.params["page"])
            if name is None:
                return httpx.Response(200, json={"error": {"code": "missingtitle", "info": "no existe"}})
            html = (FIXTURES / name).read_text(encoding="utf-8")
            return httpx.Response(200, text=json.dumps({"parse": {"text": html}}))
        name = PAGES.get(unquote(path.removeprefix("/wiki/")))
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, text=(FIXTURES / name).read_text(encoding="utf-8"))
    return handler


def reader_for(tmp_path, robots, requests) -> WikipediaReader:
    client = PoliteClient(cache_dir=tmp_path, transport=httpx.MockTransport(fake_wikipedia(robots, requests)),
                          sleep=lambda s: None)
    return WikipediaReader(client)


def resumen(schedule):
    return [(e.date.isoformat(), None if bouts is None else len(bouts)) for e, bouts in schedule]


def test_usa_los_articulos_si_robots_prohibe_la_api(tmp_path, session, capsys):
    requests = []
    reader = reader_for(tmp_path, (FIXTURES / "robots.txt").read_text(), requests)
    assert reader.route == "article"

    schedule = read_schedule(reader, HOY)
    # Sin página, 4 peleas, 7 peleas, página que no existe (404)
    assert resumen(schedule) == [("2026-10-25", None), ("2026-11-08", 4),
                                 ("2026-11-15", 7), ("2026-11-22", None)]
    assert "No se pudo leer la página UFC_Fight_Night_287" in capsys.readouterr().out
    assert not any(url.path.startswith("/w/") for url in requests)
    assert sum(url.path == "/robots.txt" for url in requests) == 1  # se lee una vez

    report = sync_upcoming(session, schedule, HOY)
    session.commit()
    assert (report.events_new, report.fights_new) == (4, 11)


def test_usa_la_api_si_robots_la_permite(tmp_path):
    requests = []
    reader = reader_for(tmp_path, None, requests)  # sin robots.txt: no hay restricciones
    assert reader.route == "api"

    schedule = read_schedule(reader, HOY)
    assert resumen(schedule) == [("2026-10-25", None), ("2026-11-08", 4),
                                 ("2026-11-15", 7), ("2026-11-22", None)]
    assert not any(url.path.startswith("/wiki/") for url in requests)
    assert requests[1].params["action"] == "parse"


def test_si_robots_lo_prohibe_todo_no_descarga_nada(tmp_path):
    requests = []
    with pytest.raises(WikipediaError, match="robots.txt"):
        reader_for(tmp_path, "User-agent: *\nDisallow: /\n", requests)
    assert [url.path for url in requests] == ["/robots.txt"]


# ---------------------------------------------------------------------------
# Unión con el CSV de Kaggle cuando el evento ya se ha celebrado
# ---------------------------------------------------------------------------

DEBUTANTE_1, DEBUTANTE_2 = "d1d1d1d1d1d1d1d9", "d2d2d2d2d2d2d2d9"
NOCHE = ("7a7a7a7a7a7a7a71", "UFC Fight Night: Herbert vs. Camilo", "2026-10-25")


def test_el_csv_completa_lo_que_creo_wikipedia(session, tmp_path):
    # 1. Histórico del CSV y, después, la cartelera de Wikipedia
    import_csv(session, escribir_csv(tmp_path / "antes.csv", filas_de_prueba()), today=HOY)
    session.commit()
    sync_upcoming(session, [(evento("UFC Fight Night 287", date(2026, 10, 25)), [
        combate("Jai Herbert", "Matheus Camilo", estelar=True),
        combate("Debutante Uno", "Debutante Dos"),
        combate("Brendan Allen", "Oscar Fourteen", "Middleweight"),  # se cancelará
    ])], HOY)
    session.commit()
    eventos, peleadores, peleas = contar(session, Event), contar(session, Fighter), contar(session, Fight)
    event = session.scalar(select(Event).where(Event.name == "UFC Fight Night 287"))
    antes = peleas_de(session, "UFC Fight Night 287")
    herbert_camilo = antes[("Jai Herbert", "Matheus Camilo")]
    debutantes = antes[("Debutante Dos", "Debutante Uno")]  # orden alfabético

    # 2. Tras el evento, un CSV nuevo trae los resultados. Camilo ganó: va primero.
    filas = [
        pelea("8a8a8a8a8a8a8a81", NOCHE, peleador(1, "Matheus Camilo", CAMILO),
              peleador(2, "Jai Herbert", HERBERT), num_rounds="5"),
        pelea("8b8b8b8b8b8b8b81", NOCHE, peleador(1, "Debutante Uno", DEBUTANTE_1),
              peleador(2, "Debutante Dos", DEBUTANTE_2), result="KO/TKO",
              result_details="Punches", finish_round="1", finish_time="1:00"),
    ]
    report = import_csv(session, escribir_csv(tmp_path / "despues.csv", filas), today=date(2026, 10, 26))
    session.commit()

    # Nada duplicado: se completaron el evento, las peleas y los debutantes
    assert report.adopted == {"eventos": 1, "peleadores": 2, "peleas": 2}
    assert (report.events_new, report.fighters_new, report.fights_new) == (0, 0, 0)
    assert (contar(session, Event), contar(session, Fighter), contar(session, Fight)) == (eventos, peleadores, peleas)
    assert (event.ufcstats_id, event.name, event.status) == (NOCHE[0], NOCHE[1], "completed")

    # La pelea es la misma fila y conserva sus esquinas: Herbert sigue siendo A
    assert herbert_camilo.ufcstats_id == "8a8a8a8a8a8a8a81"
    assert (herbert_camilo.fighter_a.name, herbert_camilo.fighter_b.name) == ("Jai Herbert", "Matheus Camilo")
    assert herbert_camilo.result.winner_id == herbert_camilo.fighter_b_id
    assert (herbert_camilo.status, herbert_camilo.scheduled_rounds) == ("completed", 5)
    assert {s.fighter_id for s in herbert_camilo.stats} == {herbert_camilo.fighter_a_id, herbert_camilo.fighter_b_id}

    assert debutantes.ufcstats_id == "8b8b8b8b8b8b8b81"
    assert debutantes.fighter_b.ufcstats_id == DEBUTANTE_1  # "Debutante Uno": el mismo registro
    assert debutantes.result.winner_id == debutantes.fighter_b_id
    assert debutantes.result.method == "KO/TKO"

    # Allen vs. Fourteen no está en el CSV: no se celebró
    assert antes[("Brendan Allen", "Oscar Fourteen")].status == "cancelled"
    assert report.orphans_cancelled == 1

    # Repetir la importación no cambia nada
    report = import_csv(session, tmp_path / "despues.csv", today=date(2026, 10, 26))
    session.commit()
    assert report.adopted == {}
    assert (report.fights_new, report.fights_updated) == (0, 2)
    assert (herbert_camilo.fighter_a.name, herbert_camilo.fighter_b.name) == ("Jai Herbert", "Matheus Camilo")


def test_el_csv_no_une_por_nombre_si_hay_dos_iguales(session, tmp_path):
    # Wikipedia creó a "Joe Doe" para un evento futuro; el CSV trae a dos
    # peleadores distintos con ese nombre: no se sabe cuál es, no se une.
    sync_upcoming(session, [(evento("UFC 330", date(2026, 11, 15)), [combate("Joe Doe", "Rival Futuro")])], HOY)
    session.commit()
    viejo = ("0d0d0d0d0d0d0d01", "UFC 100", "2009-07-11")
    filas = [
        pelea("9d9d9d9d9d9d9d91", viejo, peleador(1, "Joe Doe", "e0e0e0e0e0e0e0e9"), peleador(2, "Otro Uno", "e1e1e1e1e1e1e1e9")),
        pelea("9d9d9d9d9d9d9d92", viejo, peleador(1, "Joe Doe", "f0f0f0f0f0f0f0f9"), peleador(2, "Otro Dos", "f1f1f1f1f1f1f1f9")),
    ]
    report = import_csv(session, escribir_csv(tmp_path / "doe.csv", filas), today=HOY)
    session.commit()

    assert report.adopted == {}
    assert session.scalar(select(func.count()).where(Fighter.name == "Joe Doe")) == 3
