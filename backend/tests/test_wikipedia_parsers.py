"""Tests de los parsers de Wikipedia con páginas de ejemplo (sin red)."""
import json
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.ingestion.wikipedia.parsers import (
    ScheduledEvent,
    WikipediaError,
    api_page,
    article_page,
    fighter_name,
    guess_titles,
    page_title,
    parse_any_date,
    parse_event_card,
    parse_scheduled_events,
    same_event,
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
    fn_289, ufc_330, medic, fn_287 = events
    assert fn_289.location is None  # "TBA"
    assert ufc_330.location == "Madison Square Garden, New York City, New York, U.S."
    # Títulos de página que se probarán. Como en la página real, solo uno lleva enlace.
    assert fn_289.pages == ["UFC_Fight_Night_289"]
    assert ufc_330.pages == ["UFC_330", "UFC_330:_Makhachev_vs._Machado_Garry"]
    assert medic.pages == ["UFC_Fight_Night:_Medić_vs._Rodriguez"]
    assert fn_287.pages == ["UFC_Fight_Night_287"]  # el enlace y el título adivinado coinciden
    # La celda del recinto ocupa dos filas (rowspan): vale para los dos eventos
    assert fn_287.location == medic.location == "Meta Apex, Las Vegas, Nevada, U.S."


def test_lista_con_titulos_antiguos():
    html = """<div class="mw-parser-output">
    <h2><span class="mw-headline" id="Scheduled_events">Scheduled events</span></h2>
    <table class="wikitable"><tr><th>Event</th><th>Date</th><th>Venue</th><th>Location</th></tr>
    <tr><td><a href="/wiki/UFC_330">UFC 330</a></td><td>15 November 2026</td><td>MSG</td><td>New York</td></tr>
    </table></div>"""
    [event] = parse_scheduled_events(html)
    assert (event.name, event.date, event.pages) == ("UFC 330", date(2026, 11, 15), ["UFC_330"])


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


def test_pagina_completa_del_articulo():
    # Los iconos de arriba (página protegida...) también van en un div
    # "mw-parser-output", antes del artículo: no se pueden confundir
    html = ('<html><body><div class="mw-indicators"><div class="mw-parser-output">'
            '<span>candado</span></div></div><div id="mw-content-text">'
            + load("event_announced.html") + "</div></body></html>")
    assert len(parse_event_card(html)) == 4


def test_pagina_sin_cartelera():
    assert parse_event_card("<div class='mw-parser-output'><p>Sin peleas.</p></div>") == []


def test_titulos_posibles_de_la_pagina_del_evento():
    # Numerados: primero solo el número, que es como se llaman sus páginas
    assert guess_titles("UFC 333: Volkanovski vs. Evloev") == ["UFC_333", "UFC_333:_Volkanovski_vs._Evloev"]
    assert guess_titles("UFC Fight Night: Buckley vs. Malott") == ["UFC_Fight_Night:_Buckley_vs._Malott"]
    assert guess_titles("UFC Fight Night 295") == ["UFC_Fight_Night_295"]

    # Si la lista enlaza una página, se prueba antes que las adivinadas
    html = """<h2 id="Scheduled_events">Scheduled events</h2>
    <table><tr><th>Event</th><th>Date</th></tr><tr>
    <td><a href="/wiki/UFC_Fight_Night:_Allen_vs._Duncan">UFC Fight Night 289</a></td><td>Nov 22, 2026</td>
    </tr></table>"""
    [event] = parse_scheduled_events(html)
    assert event.pages == ["UFC_Fight_Night:_Allen_vs._Duncan", "UFC_Fight_Night_289"]


def test_la_pagina_tiene_que_ser_la_del_evento():
    event = ScheduledEvent("UFC 333: Volkanovski vs. Evloev", date(2026, 10, 24), None,
                           guess_titles("UFC 333: Volkanovski vs. Evloev"))
    assert same_event("UFC 333", event)
    assert same_event("UFC 333: Volkanovski vs. Evloev", event)
    # Páginas a las que redirige el título de un evento que aún no tiene la suya
    assert not same_event("2026 in UFC", event)
    assert not same_event("List of UFC events", event)


def test_respuesta_de_la_api():
    html = "<div class='mw-parser-output'><p>Hola</p></div>"
    assert api_page(json.dumps({"parse": {"title": "UFC 330", "text": html}})) == ("UFC 330", html)
    assert api_page(json.dumps({"parse": {"title": "X", "text": {"*": html}}})) == ("X", html)  # formato antiguo
    # La página no existe
    assert api_page(json.dumps({"error": {"code": "missingtitle", "info": "doesn't exist"}})) is None
    with pytest.raises(WikipediaError, match="too many requests"):
        api_page(json.dumps({"error": {"code": "ratelimited", "info": "too many requests"}}))


def test_pagina_del_articulo():
    html = "<html><head><title>UFC Fight Night: Medić &amp; Co. - Wikipedia</title></head><body></body></html>"
    assert article_page(html) == ("UFC Fight Night: Medić & Co.", html)
    assert article_page("<p>sin título</p>")[0] == ""


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
