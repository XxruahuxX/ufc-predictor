"""Descarga unas pocas páginas reales de UFCStats para probar los parsers.

Uso desde backend/:  python -m app.ingestion.ufcstats.sample

Guarda 5 páginas en tests/fixtures/ufcstats/real/. Después, `pytest`
comprueba que los parsers las entienden (test_real_pages).
"""
import re
from datetime import date
from pathlib import Path

from app.ingestion.http import PoliteClient
from app.ingestion.ufcstats import parsers
from app.ingestion.ufcstats.parsers import BASE_URL, clean

OUT = Path("tests/fixtures/ufcstats/real")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = PoliteClient(cache_dir="cache/ufcstats", min_interval=2.0)

    def save(name: str, url: str) -> str:
        html = client.get(url, max_age=0)
        (OUT / name).write_text(html, encoding="utf-8")
        print(f"  {name:<22} <- {url}")
        return html

    html = save("event_list.html", f"{BASE_URL}/statistics/events/completed?page=all")
    past = [e for e in parsers.parse_event_list(html) if e.date and e.date < date.today()]
    if not past:
        # El parser no entiende la página: mostramos qué ha llegado para poder arreglarlo
        title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        print("\nNo se ha encontrado ningún evento en la lista.")
        print(f"  Tamaño: {len(html)} caracteres")
        print(f"  Título: {clean(title.group(1)) if title else '(sin título)'}")
        print(f"  Enlaces a eventos: {html.count('event-details/')}")
        print("Envía tests/fixtures/ufcstats/real/event_list.html para revisarlo.")
        return
    event_id = past[0].ufcstats_id
    html = save("event_completed.html", f"{BASE_URL}/event-details/{event_id}")
    fight = parsers.parse_event_detail(html, event_id).fights[0]
    save("fight.html", f"{BASE_URL}/fight-details/{fight.ufcstats_id}")
    save("fighter.html", f"{BASE_URL}/fighter-details/{fight.fighter_ids[0]}")

    html = save("event_list_upcoming.html", f"{BASE_URL}/statistics/events/upcoming?page=all")
    print("Listo. Ahora ejecuta: pytest -v tests/test_ufcstats_real.py")


if __name__ == "__main__":
    main()
