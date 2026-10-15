"""Parsers de Wikipedia: convierten el HTML de sus páginas en datos simples.

No tocan la red ni la base de datos. Así se pueden probar con páginas
guardadas en tests/fixtures/wikipedia.

Páginas que se leen:
    List_of_UFC_events     tabla "Scheduled events": próximos eventos
    página de cada evento  cartelera: tabla con columna "Weight class" y
                           lista de peleas anunciadas ("Lightweight bout: A vs. B")

Sirve el HTML de la API (action=parse) y el de la página normal: los dos
llevan el artículo dentro de un div "mw-parser-output".

Los datos se buscan por el id del título de sección ("Scheduled_events"),
por las cabeceras de las tablas ("Event", "Weight class") y por la forma
del texto ("... bout: A vs. B"), que cambian mucho menos que el diseño.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import unescape
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from app.ingestion.names import normalize_name
from app.ingestion.ufcstats.parsers import clean

# Lo que no es contenido del artículo: notas [1], lista de referencias, cajas
# de navegación, claves de ordenación ocultas y enlaces "editar"
HIDDEN = (
    "sup.reference, .reflist, ol.references, .navbox, .sortkey, .mw-editsection, "
    "[style*='display:none'], [style*='display: none']"
)

MDY = re.compile(r"([A-Z][a-z]+)\.? (\d{1,2}), (\d{4})")  # Sep 5, 2026
DMY = re.compile(r"(\d{1,2}) ([A-Z][a-z]+)\.? (\d{4})")  # 5 September 2026
ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")  # 2026-09-05

# "Women's Flyweight bout: Veronica Hardy vs. Dione Barbosa"
ANNOUNCED = re.compile(r"^(?P<weight>.+?)\s+bout:\s*(?P<a>.+?)\s+vs\.?\s+(?P<b>.+?)$", re.I)
CHAMPION = re.compile(r"\((?:c|ic)\)", re.I)  # "(c)" campeón, "(ic)" campeón interino
# Rival que aún no se sabe: "TBD", "TBA", "To be determined"...
PLACEHOLDER = re.compile(r"^(?:tb[acd]|to be (?:announced|confirmed|determined))$")


class WikipediaError(Exception):
    """Wikipedia devolvió un error o robots.txt no permite la descarga."""


@dataclass
class ScheduledEvent:
    name: str
    date: date
    location: str | None
    # Títulos de página que pueden ser la del evento, en el orden en que se prueban
    pages: list[str] = field(default_factory=list)


@dataclass
class Bout:
    fighter_1: str  # en el orden de Wikipedia (tras la pelea, el ganador va primero)
    fighter_2: str
    weight_class: str | None
    is_title_fight: bool
    main_event: bool = False  # primera pelea de la tabla de la cartelera


# ---------------------------------------------------------------------------
# Funciones de ayuda
# ---------------------------------------------------------------------------

def api_page(json_text: str) -> tuple[str, str] | None:
    """(título, HTML) de una respuesta de action=parse. None si la página no existe."""
    data = json.loads(json_text)
    if "error" in data:
        if data["error"].get("code") == "missingtitle":
            return None
        raise WikipediaError(data["error"].get("info", "error desconocido"))
    content = data["parse"]["text"]
    html = content["*"] if isinstance(content, dict) else content  # formatversion 1 o 2
    return data["parse"].get("title", ""), html


def article_page(html: str) -> tuple[str, str]:
    """(título, HTML) de la página de un artículo.

    El título sale de <title>UFC 333 - Wikipedia</title>. Si la dirección
    pedida era una redirección, es el título de la página de destino.
    """
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = clean(unescape(m.group(1))) if m else ""
    return re.sub(r"\s+[-–—]\s+Wikipedia$", "", title), html


def article(html: str) -> Tag:
    """El contenido del artículo, sin menús ni pie de página, y sin texto oculto."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select(HIDDEN):
        element.decompose()
    # En la página completa, el artículo está dentro de #mw-content-text. Hay
    # otros "mw-parser-output" antes, como los iconos de página protegida.
    return (soup.select_one("#mw-content-text .mw-parser-output")
            or soup.select_one(".mw-parser-output") or soup)


def tidy(value: str) -> str:
    """Espacios sobrantes fuera: 'New York , U.S.' -> 'New York, U.S.'."""
    value = re.sub(r"\s+([,.;:)])", r"\1", clean(value))
    return re.sub(r"\(\s+", "(", value)


def text(tag: Tag | None) -> str:
    # get_text(" ") separa con un espacio cada trozo: "<a>Islam</a> (c)" -> "Islam (c)"
    return tidy(tag.get_text(" ")) if tag is not None else ""


def parse_any_date(value: str) -> date | None:
    """'Sep 5, 2026', '5 September 2026' o '2026-09-05' -> date."""
    if m := ISO.search(value):
        return date(int(m[1]), int(m[2]), int(m[3]))
    for regex, order in ((MDY, (1, 2, 3)), (DMY, (2, 1, 3))):
        if m := regex.search(value):
            month, day, year = (m[i] for i in order)
            for fmt in ("%b %d %Y", "%B %d %Y"):
                try:
                    return datetime.strptime(f"{month[:3] if fmt.startswith('%b') else month} {day} {year}", fmt).date()
                except ValueError:
                    pass
    return None


def table_rows(table: Tag) -> list[list[Tag | None]]:
    """Filas de una tabla como listas de celdas.

    Una celda con colspan=3 ocupa 3 posiciones y una con rowspan=2 se repite
    en la fila siguiente. Así la columna 2 es siempre la columna 2.
    """
    rows: list[list[Tag | None]] = []
    carry: dict[int, list] = {}  # columna -> [celda, filas que aún ocupa]
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue  # fila de una tabla metida dentro de otra
        cells = tr.find_all(["td", "th"], recursive=False)
        row: list[Tag | None] = []
        col = 0
        while cells or any(c >= col for c in carry):
            if col in carry:
                cell, left = carry[col]
                if left <= 1:
                    del carry[col]
                else:
                    carry[col][1] = left - 1
                row.append(cell)
                col += 1
            elif cells:
                cell = cells.pop(0)
                colspan, rowspan = _span(cell, "colspan"), _span(cell, "rowspan")
                for _ in range(colspan):
                    if rowspan > 1:
                        carry[col] = [cell, rowspan - 1]
                    row.append(cell)
                    col += 1
            else:
                row.append(None)  # hueco: ninguna celda llega a esta columna
                col += 1
        rows.append(row)
    return rows


def _span(cell: Tag, attr: str) -> int:
    try:
        return max(1, int(cell.get(attr, 1)))
    except ValueError:
        return 1


def page_title(link: Tag | None) -> str | None:
    """'/wiki/UFC_330' -> 'UFC_330'. None si la página no existe (enlace en rojo)."""
    if link is None or "new" in (link.get("class") or []):
        return None
    href = link.get("href", "")
    if not href.startswith("/wiki/") or "#" in href:
        return None  # enlace a otra web o a una sección de otra página
    return unquote(href[len("/wiki/"):])


def own_text(li: Tag) -> str:
    """Texto de un elemento de lista sin el de sus sublistas."""
    parts = []
    for string in li.find_all(string=True):
        parent = string.parent
        while parent is not li and parent.name not in ("ul", "ol"):
            parent = parent.parent
        if parent is li:
            parts.append(string)
    return tidy(" ".join(parts))


def guess_titles(name: str) -> list[str]:
    """Títulos que suele tener la página de un evento.

    La lista de eventos casi nunca enlaza los próximos. Sus páginas se llaman
    como el evento ('UFC Fight Night: Buckley vs. Malott') o, en los
    numerados, solo con el número ('UFC 333: Volkanovski vs. Evloev' -> 'UFC 333').
    """
    titles = []
    prefix = name.split(":")[0].strip()
    if ":" in name and re.search(r"\d", prefix):
        titles.append(prefix)
    titles.append(name)
    return [t.replace(" ", "_") for t in titles]


def same_event(title: str, event: ScheduledEvent) -> bool:
    """¿Es esa página la del evento? Evita leer como cartelera otra página
    (por ejemplo "2026 in UFC") a la que redirige un título."""
    names = [event.name, *(t.replace("_", " ") for t in event.pages)]
    return normalize_name(title) in {normalize_name(n) for n in names}


def fighter_name(raw: str) -> str:
    """'Islam Makhachev (c)' -> 'Islam Makhachev'. Quita también '*' y notas entre paréntesis.

    Si el rival aún no se sabe ('TBD') devuelve "": esa pelea no se guarda.
    """
    name = clean(re.sub(r"\([^)]*\)", " ", raw).replace("*", " "))
    return "" if PLACEHOLDER.match(normalize_name(name)) else name


def weight_class(raw: str) -> tuple[str | None, bool]:
    """'UFC Lightweight Championship' -> ('Lightweight', True).

    El peso entre paréntesis solo se deja en los pesos pactados:
    'Lightweight (155 lb)' -> 'Lightweight', 'Catchweight (150 lb)' se queda igual.
    """
    is_title = bool(re.search(r"championship|title", raw, re.I))
    name = re.sub(r"\b(?:UFC|Interim|Championship|Title|Bout)\b", " ", raw, flags=re.I)
    if "catch" not in name.lower():
        name = re.sub(r"\([^)]*\)", " ", name)
    name = clean(name)
    return (name[:40] or None), is_title


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_scheduled_events(html: str) -> list[ScheduledEvent]:
    """Tabla "Scheduled events" de List_of_UFC_events."""
    content = article(html)
    heading = content.find(id="Scheduled_events") or content.find(id="Upcoming_events")
    if heading is None:
        return []
    # La primera tabla con columnas "Event" y "Date" antes del siguiente
    # título. Así nunca se lee la tabla de eventos pasados.
    for element in heading.find_all_next(["table", "h2", "h3"]):
        if element.name != "table":
            return []
        rows = table_rows(element)
        header = [text(c).lower().rstrip(".") for c in rows[0]] if rows else []
        if "event" in header and "date" in header:
            break
    else:
        return []

    def cell(row: list, name: str) -> Tag | None:
        return row[header.index(name)] if name in header and header.index(name) < len(row) else None

    events = []
    for row in rows[1:]:
        if all(c is None or c.name == "th" for c in row) and len(set(map(id, row))) > 1:
            continue  # otra fila de cabecera
        name_cell, date_cell = cell(row, "event"), cell(row, "date")
        event_date = parse_any_date(text(date_cell))
        if name_cell is None or event_date is None:
            continue  # sin fecha todavía ("TBA")
        parts = (text(cell(row, "venue")), text(cell(row, "location")))
        location = ", ".join(t for t in parts if t and t.upper() not in ("TBA", "TBD"))
        name = text(name_cell)[:200]
        linked = page_title(name_cell.find("a", href=True))  # None si no hay enlace
        pages = list(dict.fromkeys(([linked] if linked else []) + guess_titles(name)))
        events.append(ScheduledEvent(name, event_date, location[:200] or None, pages))
    return events


def parse_event_card(html: str) -> list[Bout]:
    """Cartelera de la página de un evento, de la pelea estelar a la última.

    Primero las tablas de la cartelera ("Main card", "Preliminary card"...)
    y después las peleas anunciadas en listas ("Lightweight bout: A vs. B"),
    que es como aparecen mientras la cartelera no está cerrada.
    """
    content = article(html)
    bouts: list[Bout] = []

    for table in content.find_all("table"):
        rows = table_rows(table)
        if not any(text(c).lower() == "weight class" for row in rows for c in row if c is not None):
            continue  # no es una tabla de cartelera
        for row in rows:
            cells = [c for c in row if c is not None]
            if len(set(map(id, cells))) < 4 or cells[0].name == "th":
                continue  # título ("Main card") o cabecera ("Weight class")
            weight, raw_1, _, raw_2 = (text(c) for c in cells[:4])
            notes = text(cells[-1]) if len(cells) > 4 else ""
            name, is_title = weight_class(weight)
            is_title = is_title or bool(CHAMPION.search(raw_1 + raw_2)) or "championship" in notes.lower()
            bouts.append(Bout(fighter_name(raw_1), fighter_name(raw_2), name, is_title,
                              main_event=not bouts))

    for li in content.find_all("li"):
        if li.parent.name != "ul":
            continue  # las listas numeradas son referencias, no peleas
        m = ANNOUNCED.match(own_text(li))
        if m:
            name, is_title = weight_class(m["weight"])
            is_title = is_title or bool(CHAMPION.search(m["a"] + m["b"]))
            bouts.append(Bout(fighter_name(m["a"]), fighter_name(m["b"]), name, is_title))

    # Fuera las peleas sin nombre y las repetidas (en la tabla y en la lista)
    result, seen = [], set()
    for b in bouts:
        pair = frozenset((normalize_name(b.fighter_1), normalize_name(b.fighter_2)))
        if b.fighter_1 and b.fighter_2 and len(pair) == 2 and pair not in seen:
            seen.add(pair)
            result.append(b)
    return result
