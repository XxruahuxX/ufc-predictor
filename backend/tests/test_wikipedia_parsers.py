"""Tests de los parsers de Wikipedia con páginas de ejemplo (sin red)."""
import json
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.ingestion.wikipedia.parsers import (
    WikipediaError,
    fighter_name,
    html_from_api,
    page_title,
    parse_any_date,
    parse_event_card,
    parse_scheduled_events,
    table_rows,
    weight_class,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wikipedia"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_lista_de_proximos_eventos():
    events = parse_scheduled_events(load("list_of_ufc_events.html"))

    # 4 eventos: "UFC 331" sin fecha (TBA) no cuenta y la tabla de pasados no se lee
    assert [(e.name, e.date) for e in events] == [
        ("UFC Fight Night 289", date(2026, 11, 22)),
        ("UFC 330: Makhachev vs. Machado Garry", date(2026, 11, 15)),
        ("UFC Fight Night: Medić vs. Rodriguez", date(2026, 11, 8)),
        ("UFC Fight Night 287", date(2026, 10, 25)),
    ]
    sin_pagina, ufc_330, medic, fn_287 = events
    assert sin_pagina.page is None  # enlace en rojo: la página no existe
    assert sin_pagina.location is None  # "TBA"
    assert ufc_330.page == "UFC_330"
    assert ufc_330.location == "Madison Square Garden, New York City, New York, U.S."
    assert medic.page == "UFC_Fight_Night:_Medić_vs._Rodriguez"
    # La celda del recinto ocupa dos filas (rowspan): vale para los dos eventos
    assert fn_287.location == medic.location == "Meta Apex, Las Vegas, Nevada, U.S."


def test_lista_con_titulos_antiguos():
    html = """<div class="mw-parser-output">
    <h2><span class="mw-headline" id="Scheduled_events">Scheduled events</span></h2>
    <table class="wikitable"><tr><th>Event</th><th>Date</th><th>Venue</th><th>Location</th></tr>
    <tr><td><a href="/wiki/UFC_330">UFC 330</a></td><td>15 November 2026</td><td>MSG</td><td>New York</td></tr>
    </table></div>"""
    [event] = parse_scheduled_events(html)
    assert (event.name, event.date, event.page) == ("UFC 330", date(2026, 11, 15), "UFC_330")


def test_lista_con_otro_titulo():
    html = """<h2 id="Upcoming_events">Upcoming events</h2>
    <table><tr><th>Event</th><th>Date</th></tr><tr><td>UFC 330</td><td>Nov 15, 2026</td></tr></table>"""
    assert [e.name for e in parse_scheduled_events(html)] == ["UFC 330"]


def test_lista_sin_tabla_de_proximos():
    assert parse_scheduled_events("<p>Nada</p>") == []
    # El título existe pero la sección no tiene tabla: no se lee la de eventos pasados
    html = """<h2 id="Scheduled_events">Scheduled events</h2><p>None.</p>
    <h2 id="Past_events">Past events</h2>
    <table><tr><th>Event</th><th>Date</th></tr><tr><td>UFC 1</td><td>Nov 12, 1993</td></tr></table>"""
    assert parse_scheduled_events(html) == []


def test_cartelera_en_tabla_y_lista():
    bouts = parse_event_card(load("event_card.html"))

    assert [(b.weight_class, b.fighter_1, b.fighter_2) for b in bouts] == [
        ("Lightweight", "Islam Makhachev", "Ian Machado Garry"),
        ("Welterweight", "Leon Edwards", "Sean Brady"),
        ("Women's Strawweight", "Zhang Weili", "Tatiana Suarez"),
        ("Light Heavyweight", "Jiří Procházka", "Khalil Rountree Jr."),
        ("Catchweight (150 lb)", "Brendan Allen", "Nuevo Debutante"),
        # De la lista de peleas anunciadas. Edwards vs. Brady está repetida y
        # no cuenta; tampoco el texto normal, las referencias ni el menú final.
        ("Women's Flyweight", "Veronica Hardy", "Dione Barbosa"),
        ("Lightweight", "Mateusz Gamrot", "Rafael Fiziev"),
    ]
    estelar = bouts[0]
    assert estelar.is_title_fight and estelar.main_event  # "(c)": campeón
    assert not any(b.main_event for b in bouts[1:])
    assert not any(b.is_title_fight for b in bouts[1:])


def test_cartelera_solo_con_peleas_anunciadas():
    bouts = parse_event_card(load("event_announced.html"))

    assert [(b.weight_class, b.fighter_1, b.fighter_2, b.is_title_fight) for b in bouts] == [
        ("Welterweight", "Uroš Medić", "Daniel Rodriguez", False),
        ("Featherweight", "Alexander Volkanovski", "Diego Lopes", True),
        ("Middleweight", "Brad Tavares", "Marc-André Barriault", False),
        ("Bantamweight", "Farid Basharat", "Ethyn Ewing", False),
    ]
    assert not any(b.main_event for b in bouts)  # sin tabla no se sabe cuál es la estelar


def test_pagina_sin_cartelera():
    assert parse_event_card("<div class='mw-parser-output'><p>Sin peleas.</p></div>") == []


def test_respuesta_de_la_api():
    html = "<div class='mw-parser-output'><p>Hola</p></div>"
    assert html_from_api(json.dumps({"parse": {"title": "X", "text": html}})) == html
    assert html_from_api(json.dumps({"parse": {"text": {"*": html}}})) == html  # formato antiguo
    error = json.dumps({"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}})
    with pytest.raises(WikipediaError, match="doesn't exist"):
        html_from_api(error)


def test_fechas():
    assert parse_any_date("Nov 8, 2026") == date(2026, 11, 8)
    assert parse_any_date("November 8, 2026") == date(2026, 11, 8)
    assert parse_any_date("Sept. 5, 2026") == date(2026, 9, 5)
    assert parse_any_date("8 November 2026") == date(2026, 11, 8)
    assert parse_any_date("2026-11-08") == date(2026, 11, 8)
    assert parse_any_date("TBA") is None


def test_categorias_y_nombres():
    assert weight_class("UFC Lightweight Championship") == ("Lightweight", True)
    assert weight_class("Interim UFC Featherweight Championship") == ("Featherweight", True)
    assert weight_class("Women's Flyweight") == ("Women's Flyweight", False)
    assert weight_class("Lightweight (155 lb)") == ("Lightweight", False)
    assert weight_class("Catchweight (150 lb)") == ("Catchweight (150 lb)", False)
    assert weight_class("") == (None, False)
    assert fighter_name("Islam Makhachev (c)") == "Islam Makhachev"
    assert fighter_name("Jon Jones (ic) *") == "Jon Jones"


def test_titulo_de_pagina():
    a = BeautifulSoup('<a href="/wiki/UFC_Fight_Night:_Medi%C4%87_vs._Rodriguez">x</a>', "html.parser").a
    assert page_title(a) == "UFC_Fight_Night:_Medić_vs._Rodriguez"
    rojo = BeautifulSoup('<a class="new" href="/w/index.php?title=X&redlink=1">x</a>', "html.parser").a
    assert page_title(rojo) is None
    seccion = BeautifulSoup('<a href="/wiki/2026_in_UFC#UFC_331">x</a>', "html.parser").a
    assert page_title(seccion) is None
    assert page_title(None) is None


def test_tabla_con_celdas_que_ocupan_varias_filas_y_columnas():
    table = BeautifulSoup("""<table>
        <tr><th colspan="2">AB</th><th>C</th></tr>
        <tr><td rowspan="2">a</td><td>b</td><td>c</td></tr>
        <tr><td>b2</td><td>c2</td></tr>
    </table>""", "html.parser").table
    rows = [[cell.get_text() for cell in row] for row in table_rows(table)]
    assert rows == [["AB", "AB", "C"], ["a", "b", "c"], ["a", "b2", "c2"]]
