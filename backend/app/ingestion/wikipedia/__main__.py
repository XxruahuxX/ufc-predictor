"""Próximos eventos de la UFC y sus carteleras, desde Wikipedia.

Uso desde backend/:
    python -m app.ingestion.wikipedia upcoming   # guarda eventos y peleas en la base de datos
    python -m app.ingestion.wikipedia sample     # guarda páginas reales para los tests

Antes de descargar se mira robots.txt:
    - si permite la API oficial (/w/api.php), se usa la API;
    - si solo permite las páginas de los artículos (/wiki/...), se usan esas;
    - si no permite ninguna de las dos, no se descarga nada.
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx

from app.ingestion.http import PoliteClient
from app.ingestion.ufcstats.parsers import clean
from app.ingestion.wikipedia import parsers
from app.ingestion.wikipedia.loader import SyncReport
from app.ingestion.wikipedia.parsers import Bout, ScheduledEvent, WikipediaError

SITE = "https://en.wikipedia.org"
LIST_PAGE = "List_of_UFC_events"
HOUR = 3600
SAMPLE_DIR = Path("tests/fixtures/wikipedia/real")


def api_url(title: str) -> str:
    return (f"{SITE}/w/api.php?action=parse&page={quote(title, safe='')}"
            "&prop=text&format=json&formatversion=2&redirects=1")


def article_url(title: str) -> str:
    return f"{SITE}/wiki/{quote(title)}"


class WikipediaReader:
    """Descarga el HTML de una página por la vía que permite robots.txt."""

    def __init__(self, client: PoliteClient):
        self.client = client
        if client.allowed(api_url(LIST_PAGE)):
            self.route = "api"
        elif client.allowed(article_url(LIST_PAGE)):
            self.route = "article"
        else:
            raise WikipediaError("robots.txt no permite descargar ni la API ni los artículos")

    def page(self, title: str, max_age: float | None = 6 * HOUR) -> tuple[str, str] | None:
        """(título real, HTML) de una página. None si no existe."""
        url = api_url(title) if self.route == "api" else article_url(title)
        if not self.client.allowed(url):
            raise WikipediaError(f"robots.txt no permite descargar {url}")
        try:
            text = self.client.get(url, max_age=max_age)
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 404:
                return None
            raise
        return parsers.api_page(text) if self.route == "api" else parsers.article_page(text)

    def event_page(self, event: ScheduledEvent,
                   max_age: float | None = 6 * HOUR) -> tuple[str, str] | None:
        """(título, HTML) de la página del evento, o None si aún no tiene.

        Se prueban sus títulos posibles en orden. Una página que existe pero
        es de otra cosa (un título que redirige a "2026 in UFC") no vale.
        """
        for title in event.pages:
            found = self.page(title, max_age)
            if found and parsers.same_event(found[0], event):
                return found
        return None


def read_schedule(reader: WikipediaReader, today: date,
                  max_age: float | None = 6 * HOUR) -> list[tuple[ScheduledEvent, list[Bout] | None]]:
    """Próximos eventos y la cartelera de cada uno (None si no tiene página)."""
    found = reader.page(LIST_PAGE, max_age)
    if found is None:
        raise WikipediaError(f"no existe la página {LIST_PAGE}")
    schedule = []
    for event in parsers.parse_scheduled_events(found[1]):
        if event.date < today:
            continue
        bouts = None
        try:
            page = reader.event_page(event, max_age)
        except httpx.HTTPStatusError as err:
            print(f"  No se pudo leer la página de {event.name}: error {err.response.status_code}")
        except WikipediaError as err:
            print(f"  No se pudo leer la página de {event.name}: {err}")
        else:
            if page is not None:
                bouts = parsers.parse_event_card(page[1])
        schedule.append((event, bouts))
    return sorted(schedule, key=lambda item: item[0].date)


def route_message(reader: WikipediaReader) -> str:
    if reader.route == "api":
        return "robots.txt permite la API oficial: se usa la API."
    return "robots.txt no permite la API a programas, pero sí los artículos: se leen los artículos."


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def print_report(r: SyncReport) -> None:
    print(f"Eventos: {r.events_new} nuevos, {r.events_updated} actualizados,"
          f" {len(r.events_deleted)} borrados")
    for name in r.events_deleted:
        print(f"  borrado (ya no está en Wikipedia): {name}")
    print(f"Peleas: {r.fights_new} nuevas, {r.fights_updated} actualizadas,"
          f" {r.fights_cancelled} canceladas")
    print(f"Peleadores: {len(r.fighters_new)} nuevos, {len(r.merged)} unidos con el CSV")
    for line in r.merged:
        print(f"  unido: {line}")
    if r.unknown:
        print(f"No están en el CSV: {len(r.unknown)}. Son debutantes o su nombre se escribe"
              " distinto. Si es lo segundo, apunta el alias en app/ingestion/aliases.csv:")
        for line in r.unknown:
            print(f"  {line}")
    if r.alias_problems:
        print("Alias de aliases.csv que no se han podido guardar:")
        for line in r.alias_problems:
            print(f"  {line}")
    if r.skipped:
        print("Peleas no guardadas:")
        for reason in r.skipped:
            print(f"  {reason}")


def upcoming(delay: float) -> None:
    from app.db.init_db import create_tables
    from app.db.session import SessionLocal
    from app.ingestion.aliases import read_aliases
    from app.ingestion.wikipedia.loader import sync_upcoming

    if not create_tables():  # por si aún no existen; avisa si PostgreSQL no responde
        sys.exit(1)
    reader = WikipediaReader(PoliteClient(cache_dir="cache/wikipedia", min_interval=delay))
    print(route_message(reader))
    today = date.today()
    schedule = read_schedule(reader, today)
    print(f"Próximos eventos en Wikipedia: {len(schedule)}")
    if not schedule:
        print("No se ha leído ningún evento: no se cambia nada. Ejecuta"
              " 'python -m app.ingestion.wikipedia sample' y pega lo que salga.")
        return
    for event, bouts in schedule:
        card = "sin página todavía" if bouts is None else f"{len(bouts)} peleas"
        print(f"  {event.date}  {event.name}: {card}")

    with SessionLocal() as session:
        report = sync_upcoming(session, schedule, today, read_aliases())
        session.commit()
    print_report(report)


def diagnose(html: str, problem: str) -> None:
    """Lo necesario para ajustar el parser sin tener que enviar el archivo."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    print("\n================ DIAGNÓSTICO ================")
    print(f"Problema: {problem}")
    print(f"Tamaño: {len(html)} caracteres. Título: {clean(title.group(1)) if title else '(sin título)'}")
    for pattern in ["Scheduled_events", "mw-parser-output", "<table", "Weight class", "bout:", "<li"]:
        print(f"  '{pattern}': {html.count(pattern)} veces")
    pos = max(html.find("Scheduled_events"), html.find("Weight class"), html.find("bout:"))
    print("---- Fragmento del HTML ----")
    print(html[max(0, pos - 1000):max(0, pos - 1000) + 2500])
    print("=============================================")
    print("Copia todo lo anterior desde DIAGNÓSTICO y pégalo en el chat.")


def sample(delay: float) -> None:
    """Guarda robots.txt, la lista de eventos y hasta 3 carteleras reales."""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    client = PoliteClient(cache_dir="cache/wikipedia", min_interval=delay)
    try:
        robots = client.get(f"{SITE}/robots.txt", max_age=0)
    except httpx.HTTPStatusError as err:
        robots = f"# robots.txt respondió {err.response.status_code}\n"
    (SAMPLE_DIR / "robots.txt").write_text(robots, encoding="utf-8")
    for label, url in (("la API", api_url(LIST_PAGE)), ("los artículos", article_url(LIST_PAGE))):
        verdict = "permite" if client.allowed(url) else "NO permite"
        print(f"robots.txt {verdict} {label}: {url}")
    reader = WikipediaReader(client)
    print(route_message(reader))

    found = reader.page(LIST_PAGE, max_age=0)
    if found is None:
        raise WikipediaError(f"no existe la página {LIST_PAGE}")
    html = found[1]
    (SAMPLE_DIR / "list.html").write_text(html, encoding="utf-8")
    events = sorted(parsers.parse_scheduled_events(html), key=lambda e: e.date)
    print(f"Lista de eventos: {len(events)} programados")
    if not events:
        return diagnose(html, "no se ha encontrado la tabla de próximos eventos")

    saved, last = 0, ""
    for event in events:
        try:
            page = reader.event_page(event, max_age=0)
        except httpx.HTTPStatusError as err:
            print(f"  {event.date}  {event.name}: error {err.response.status_code}")
            continue
        except WikipediaError as err:
            print(f"  {event.date}  {event.name}: {err}")
            continue
        if page is None:
            print(f"  {event.date}  {event.name}: sin página (probado: {', '.join(event.pages)})")
            continue
        title, last = page
        bouts = parsers.parse_event_card(last)
        print(f"  {event.date}  {event.name}: {len(bouts)} peleas (página {title})")
        for b in bouts[:3]:
            print(f"      {b.weight_class}: {b.fighter_1} vs. {b.fighter_2}"
                  f"{' (título)' if b.is_title_fight else ''}")
        if bouts and saved < 3:
            saved += 1
            (SAMPLE_DIR / f"event_{saved}.html").write_text(last, encoding="utf-8")
    if saved == 0 and last:
        return diagnose(last, "ninguna página de evento tiene peleas que se entiendan")
    print(f"Guardado en {SAMPLE_DIR}. Ahora: python -m pytest tests/test_wikipedia_real.py -v")


def main() -> None:
    parser = argparse.ArgumentParser(description="Próximos eventos de la UFC desde Wikipedia")
    parser.add_argument("task", choices=["upcoming", "sample"])
    parser.add_argument("--delay", type=float, default=2.0, help="segundos entre peticiones")
    args = parser.parse_args()
    try:
        if args.task == "upcoming":
            upcoming(args.delay)
        else:
            sample(args.delay)
    except WikipediaError as err:
        print(f"Wikipedia: {err}")
        sys.exit(1)
    except httpx.HTTPStatusError as err:
        print(f"Wikipedia respondió {err.response.status_code} a {err.request.url}")
        sys.exit(1)
    except httpx.HTTPError as err:
        print(f"No se pudo conectar con Wikipedia: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
