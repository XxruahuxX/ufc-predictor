"""Descarga unas pocas páginas reales de UFCStats para probar los parsers.

Uso desde backend/:  python -m app.ingestion.ufcstats.sample

Guarda 5 páginas en tests/fixtures/ufcstats/real/. Después, `pytest`
comprueba que los parsers las entienden (test_ufcstats_real.py).

Si una página no se entiende, muestra un diagnóstico para poder arreglarlo.
"""
import re
from datetime import date
from pathlib import Path

from app.ingestion.http import PoliteClient
from app.ingestion.ufcstats import parsers
from app.ingestion.ufcstats.parsers import BASE_URL, clean

OUT = Path("tests/fixtures/ufcstats/real")


def diagnose(client: PoliteClient, html: str, problem: str) -> None:
    """Imprime lo necesario para entender qué ha devuelto la web."""
    r = client.last_response
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    print("\n================ DIAGNÓSTICO ================")
    print(f"Problema: {problem}")
    if r is not None:
        print(f"URL final:   {r.url}")
        print(f"Estado HTTP: {r.status_code}")
        print(f"Tipo:        {r.headers.get('content-type')}")
        print(f"Servidor:    {r.headers.get('server')}")
    print(f"Tamaño:      {len(html)} caracteres")
    print(f"Título:      {clean(title.group(1)) if title else '(sin título)'}")
    for pattern in ["event-details/", "fight-details/", "fighter-details/",
                    "b-statistics__table-row", "b-fight-details__table-row", "<tr", "<script"]:
        print(f"  '{pattern}': {html.count(pattern)} veces")
    # Un trozo del HTML: alrededor del primer enlace a un evento o, si no hay, el principio
    pos = html.find("event-details/")
    start = max(0, pos - 1200) if pos >= 0 else 0
    print("---- Fragmento del HTML ----")
    print(html[start:start + 2000])
    print("=============================================")
    print("Copia todo lo anterior desde DIAGNÓSTICO y pégalo en el chat.")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = PoliteClient(cache_dir="cache/ufcstats", min_interval=2.0)

    def save(name: str, url: str) -> str:
        html = client.get(url, max_age=0)
        (OUT / name).write_text(html, encoding="utf-8")
        print(f"  {name:<26} <- {url}")
        return html

    html = save("event_list.html", f"{BASE_URL}/statistics/events/completed?page=all")
    events = parsers.parse_event_list(html)
    past = [e for e in events if e.date and e.date < date.today()]
    print(f"    {len(events)} eventos leídos, {len(past)} ya celebrados")
    if not past:
        return diagnose(client, html, "no se ha encontrado ningún evento celebrado en la lista")

    event_id = past[0].ufcstats_id
    html = save("event_completed.html", f"{BASE_URL}/event-details/{event_id}")
    fights = parsers.parse_event_detail(html, event_id).fights
    print(f"    {len(fights)} peleas leídas")
    if not fights:
        return diagnose(client, html, "no se ha encontrado ninguna pelea en el evento")

    save("fight.html", f"{BASE_URL}/fight-details/{fights[0].ufcstats_id}")
    save("fighter.html", f"{BASE_URL}/fighter-details/{fights[0].fighter_ids[0]}")
    save("event_list_upcoming.html", f"{BASE_URL}/statistics/events/upcoming?page=all")
    print("Listo. Ahora ejecuta: python -m pytest tests/test_ufcstats_real.py -v")


if __name__ == "__main__":
    main()
