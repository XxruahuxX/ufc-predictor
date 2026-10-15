"""Parsers de UFCStats: convierten HTML en datos simples (dataclasses).

No tocan la red ni la base de datos. Así se pueden probar con páginas
guardadas en tests/fixtures.

Páginas que se leen:
    /statistics/events/upcoming?page=all    lista de eventos próximos
    /statistics/events/completed?page=all   lista de eventos pasados
    /event-details/<id>                     cartelera (y resultados si ya pasó)
    /fight-details/<id>                     estadísticas de una pelea
    /fighter-details/<id>                   ficha de un peleador
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

BASE_URL = "http://ufcstats.com"

# Buscamos los datos por la forma de sus enlaces (/event-details/, /fighter-details/...)
# y no solo por clases CSS: las URLs cambian mucho menos que el diseño de la web.
EVENT_LINK = re.compile(r"/event-details/")
FIGHT_LINK = re.compile(r"/fight-details/")
FIGHTER_LINK = re.compile(r"/fighter-details/")
DATE_IN_TEXT = re.compile(r"[A-Z][a-z]+\.? \d{1,2}, \d{4}")  # "November 08, 2026"


# ---------------------------------------------------------------------------
# Estructuras de datos que devuelven los parsers
# ---------------------------------------------------------------------------

@dataclass
class EventSummary:
    ufcstats_id: str
    name: str
    date: date | None
    location: str | None


@dataclass
class FightRow:
    """Una fila de la cartelera de un evento."""
    ufcstats_id: str
    fighter_ids: tuple[str, str]  # en el orden en que aparecen en la web
    fighter_names: tuple[str, str]
    weight_class: str | None
    is_title_fight: bool
    bout_order: int
    # Solo si la pelea ya se celebró:
    outcome: str | None = None  # "win", "draw", "no_contest"
    winner_ufcstats_id: str | None = None
    method: str | None = None  # "KO/TKO", "SUB", "DEC", "DQ", "OTHER"
    method_detail: str | None = None
    end_round: int | None = None
    end_time_seconds: int | None = None


@dataclass
class EventDetail:
    ufcstats_id: str
    name: str
    date: date | None
    location: str | None
    fights: list[FightRow] = field(default_factory=list)


@dataclass
class FighterTotals:
    """Totales de un peleador en una pelea."""
    ufcstats_id: str
    knockdowns: int
    sig_strikes_landed: int
    sig_strikes_attempted: int
    total_strikes_landed: int
    total_strikes_attempted: int
    takedowns_landed: int
    takedowns_attempted: int
    submission_attempts: int
    control_seconds: int


@dataclass
class FightDetail:
    ufcstats_id: str
    scheduled_rounds: int | None
    totals: list[FighterTotals]  # dos elementos, vacío si no hay estadísticas


@dataclass
class FighterProfile:
    ufcstats_id: str
    name: str
    nickname: str | None
    wins: int
    losses: int
    draws: int
    no_contests: int
    height_cm: float | None
    reach_cm: float | None
    stance: str | None
    birth_date: date | None


# ---------------------------------------------------------------------------
# Funciones de ayuda para limpiar texto
# ---------------------------------------------------------------------------

def clean(text: str | None) -> str:
    """Quita espacios y saltos de línea sobrantes."""
    return " ".join((text or "").split())


def id_from_url(url: str) -> str:
    """'http://ufcstats.com/fighter-details/abc123' -> 'abc123'"""
    return urlparse(url).path.rstrip("/").split("/")[-1]


def parse_date(text: str) -> date | None:
    """'July 13, 2024' o 'Jul 13, 1990' -> date. '--' -> None."""
    text = clean(text)
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_height_cm(text: str) -> float | None:
    """'5\\' 11"' -> 180.3. '--' -> None."""
    m = re.search(r"(\d+)'\s*(\d+)", text)
    if not m:
        return None
    inches = int(m.group(1)) * 12 + int(m.group(2))
    return round(inches * 2.54, 1)


def parse_reach_cm(text: str) -> float | None:
    """'74"' -> 188.0. '--' -> None."""
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return round(float(m.group(1)) * 2.54, 1) if m else None


def parse_clock(text: str) -> int:
    """'4:32' -> 272 segundos. '--' -> 0."""
    m = re.search(r"(\d+):(\d+)", text)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def parse_of(text: str) -> tuple[int, int]:
    """'45 of 98' -> (45, 98)."""
    m = re.search(r"(\d+)\s+of\s+(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def to_int(text: str) -> int:
    m = re.search(r"\d+", text)
    return int(m.group()) if m else 0


def normalize_method(raw: str) -> tuple[str, str | None]:
    """Traduce el método de UFCStats a nuestras categorías.

    'KO/TKO' -> KO/TKO, 'SUB' -> SUB, 'U-DEC'/'S-DEC'/'M-DEC' -> DEC,
    'DQ' -> DQ, el resto ('Overturned', 'CNC'...) -> OTHER.
    Devuelve (categoría, detalle).
    """
    raw = clean(raw)
    upper = raw.upper()
    if "KO" in upper:
        return "KO/TKO", None
    if upper.startswith("SUB"):
        return "SUB", None
    if "DEC" in upper:
        detail = {"U": "Unanimous", "S": "Split", "M": "Majority"}.get(upper[0])
        return "DEC", detail
    if upper == "DQ":
        return "DQ", None
    return "OTHER", raw or None


def _info_items(soup: BeautifulSoup) -> dict[str, str]:
    """Lee las cajas tipo 'Height:  5' 11"' y devuelve {'height': '5' 11"'}."""
    items = {}
    for li in soup.select("li.b-list__box-list-item"):
        title = li.find("i")
        if not title:
            continue
        key = clean(title.get_text()).rstrip(":").lower()
        title.extract()  # quitamos la etiqueta para quedarnos con el valor
        items[key] = clean(li.get_text())
    return items


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_event_list(html: str) -> list[EventSummary]:
    """Lista de eventos (próximos o pasados)."""
    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen = set()
    for link in soup.find_all("a", href=EVENT_LINK):
        event_id = id_from_url(link["href"])
        if event_id in seen:
            continue
        seen.add(event_id)

        row = link.find_parent("tr")
        cells = row.find_all("td") if row else []
        # La fecha va en <span class="b-statistics__date">. Si no está,
        # buscamos algo con forma de fecha en el texto de la fila.
        date_span = row.select_one("span.b-statistics__date") if row else None
        date_text = date_span.get_text() if date_span else ""
        if not date_text and row:
            match = DATE_IN_TEXT.search(clean(row.get_text(" ")))
            date_text = match.group() if match else ""

        events.append(EventSummary(
            ufcstats_id=event_id,
            name=clean(link.get_text()),
            date=parse_date(date_text),
            location=clean(cells[1].get_text()) if len(cells) > 1 else None,
        ))
    return events


def parse_event_detail(html: str, ufcstats_id: str) -> EventDetail:
    """Página de un evento: datos y cartelera completa."""
    soup = BeautifulSoup(html, "html.parser")
    info = _info_items(soup)
    event = EventDetail(
        ufcstats_id=ufcstats_id,
        name=clean(soup.select_one("span.b-content__title-highlight").get_text()),
        date=parse_date(info.get("date", "")),
        location=info.get("location"),
    )

    rows = soup.find_all("tr", attrs={"data-link": FIGHT_LINK})
    for order, row in enumerate(rows, start=1):
        cols = row.find_all("td")
        fighter_links = row.find_all("a", href=FIGHTER_LINK)
        if len(fighter_links) < 2 or len(cols) < 10:
            continue
        ids = (id_from_url(fighter_links[0]["href"]), id_from_url(fighter_links[1]["href"]))
        names = (clean(fighter_links[0].get_text()), clean(fighter_links[1].get_text()))

        weight_cell = cols[6]
        fight = FightRow(
            ufcstats_id=id_from_url(row["data-link"]),
            fighter_ids=ids,
            fighter_names=names,
            weight_class=clean(weight_cell.get_text()) or None,
            # En las peleas por el título aparece el icono de un cinturón
            is_title_fight=weight_cell.find("img") is not None,
            bout_order=order,
        )

        # Resultado: la primera columna tiene "win", "draw" o "nc".
        # En pelea próxima está vacía.
        flag = clean(cols[0].get_text()).lower()
        if flag:
            if flag.startswith("win"):
                # UFCStats pone siempre al ganador en primer lugar
                fight.outcome, fight.winner_ufcstats_id = "win", ids[0]
            elif flag.startswith("draw"):
                fight.outcome = "draw"
            else:
                fight.outcome = "no_contest"
            method_ps = cols[7].find_all("p")
            fight.method, fight.method_detail = normalize_method(method_ps[0].get_text())
            if len(method_ps) > 1 and clean(method_ps[1].get_text()):
                fight.method_detail = clean(method_ps[1].get_text())
            fight.end_round = to_int(cols[8].get_text())
            fight.end_time_seconds = parse_clock(cols[9].get_text())
        event.fights.append(fight)
    return event


def parse_fight_detail(html: str, ufcstats_id: str) -> FightDetail:
    """Página de una pelea: rondas programadas y totales de cada peleador."""
    soup = BeautifulSoup(html, "html.parser")

    scheduled_rounds = None
    for p in soup.select("p.b-fight-details__text i.b-fight-details__text-item"):
        text = clean(p.get_text())
        if text.startswith("Time format:"):
            scheduled_rounds = to_int(text.split(":", 1)[1]) or None

    totals: list[FighterTotals] = []
    # La primera tabla de la página es la de totales
    table = soup.select_one("table")
    row = table.select_one("tbody tr") if table else None
    if row:
        cols = row.find_all("td")

        def pair(i):
            """Cada celda tiene dos <p>: uno por peleador."""
            return [clean(p.get_text()) for p in cols[i].find_all("p")]

        links = cols[0].find_all("a", href=FIGHTER_LINK)
        kd, sig, tot, td = pair(1), pair(2), pair(4), pair(5)
        sub, ctrl = pair(7), pair(9)
        for k in range(2):
            sig_l, sig_a = parse_of(sig[k])
            tot_l, tot_a = parse_of(tot[k])
            td_l, td_a = parse_of(td[k])
            totals.append(FighterTotals(
                ufcstats_id=id_from_url(links[k]["href"]),
                knockdowns=to_int(kd[k]),
                sig_strikes_landed=sig_l, sig_strikes_attempted=sig_a,
                total_strikes_landed=tot_l, total_strikes_attempted=tot_a,
                takedowns_landed=td_l, takedowns_attempted=td_a,
                submission_attempts=to_int(sub[k]),
                control_seconds=parse_clock(ctrl[k]),
            ))
    return FightDetail(ufcstats_id=ufcstats_id, scheduled_rounds=scheduled_rounds, totals=totals)


def parse_fighter(html: str, ufcstats_id: str) -> FighterProfile:
    """Ficha de un peleador."""
    soup = BeautifulSoup(html, "html.parser")
    record_text = clean(soup.select_one("span.b-content__title-record").get_text())
    # "Record: 23-8-0 (1 NC)"
    m = re.search(r"(\d+)-(\d+)-(\d+)(?:\s*\((\d+)\s*NC\))?", record_text)
    wins, losses, draws, ncs = (int(g) if g else 0 for g in m.groups()) if m else (0, 0, 0, 0)

    nickname_el = soup.select_one("p.b-content__Nickname")
    info = _info_items(soup)
    stance = info.get("stance")
    return FighterProfile(
        ufcstats_id=ufcstats_id,
        name=clean(soup.select_one("span.b-content__title-highlight").get_text()),
        nickname=(clean(nickname_el.get_text()) or None) if nickname_el else None,
        wins=wins, losses=losses, draws=draws, no_contests=ncs,
        height_cm=parse_height_cm(info.get("height", "")),
        reach_cm=parse_reach_cm(info.get("reach", "")),
        stance=stance if stance and stance != "--" else None,
        birth_date=parse_date(info.get("dob", "")),
    )
