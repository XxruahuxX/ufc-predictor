"""Conector de The Odds API: próximas peleas de MMA y sus cuotas.

API oficial con clave: https://the-odds-api.com
Plan gratuito: 500 créditos al mes. Cada consulta de cuotas cuesta
(mercados x regiones) créditos. Con 1 mercado ("h2h", quién gana) y
1 región ("eu") cuesta 1 crédito.

Uso desde backend/:  python -m app.ingestion.odds_api
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Fight, FighterAlias, Odds
from app.ingestion.http import PoliteClient
from app.ingestion.names import normalize_name

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "mma_mixed_martial_arts"  # incluye UFC y otras organizaciones


@dataclass
class BookmakerPrice:
    bookmaker: str
    price_1: float  # cuota decimal del peleador 1
    price_2: float  # cuota decimal del peleador 2
    last_update: datetime


@dataclass
class OddsEvent:
    """Una pelea según The Odds API."""
    odds_api_id: str
    commence_time: datetime
    fighter_1: str
    fighter_2: str
    prices: list[BookmakerPrice]


def parse_time(text: str) -> datetime:
    """'2026-10-18T02:00:00Z' -> datetime con zona horaria UTC."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def parse_odds(json_text: str) -> list[OddsEvent]:
    """Convierte la respuesta JSON de /sports/{sport}/odds en OddsEvent."""
    events = []
    for item in json.loads(json_text):
        f1, f2 = item["home_team"], item["away_team"]  # en MMA son los dos peleadores
        prices = []
        for bookmaker in item.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] != "h2h":
                    continue
                by_name = {o["name"]: o["price"] for o in market["outcomes"]}
                if f1 in by_name and f2 in by_name:
                    prices.append(BookmakerPrice(
                        bookmaker=bookmaker["key"],
                        price_1=by_name[f1],
                        price_2=by_name[f2],
                        last_update=parse_time(market.get("last_update") or bookmaker["last_update"]),
                    ))
        events.append(OddsEvent(
            odds_api_id=item["id"],
            commence_time=parse_time(item["commence_time"]),
            fighter_1=f1, fighter_2=f2, prices=prices,
        ))
    return events


def no_vig_probability(price_a: float, price_b: float) -> float:
    """Probabilidad implícita de que gane A, sin el margen de la casa.

    Ejemplo: cuotas 1.50 y 2.70 -> 1/1.50 = 0.667 y 1/2.70 = 0.370.
    Suman 1.037: ese 3.7 % de más es el margen de la casa. Repartiéndolo,
    P(A) = 0.667 / 1.037 = 0.643.
    """
    implied_a, implied_b = 1 / price_a, 1 / price_b
    return implied_a / (implied_a + implied_b)


def _names_of(fighter) -> set[str]:
    """Nombre del peleador y sus alias, normalizados."""
    return {normalize_name(fighter.name)} | {normalize_name(a.alias) for a in fighter.aliases}


def find_fight(session: Session, e: OddsEvent) -> tuple[Fight, bool] | None:
    """Busca la pelea programada que corresponde a este evento de The Odds API.

    Devuelve (pelea, invertida). invertida = True si el peleador 1 de la API
    es nuestro peleador B. Solo mira eventos a ±2 días de la fecha de la API.
    """
    day = e.commence_time.date()
    fights = session.scalars(
        select(Fight).join(Event).where(
            Fight.status == "scheduled",
            Event.date.between(day - timedelta(days=2), day + timedelta(days=2)),
        )
    ).all()
    n1, n2 = normalize_name(e.fighter_1), normalize_name(e.fighter_2)
    for fight in fights:
        names_a, names_b = _names_of(fight.fighter_a), _names_of(fight.fighter_b)
        if n1 in names_a and n2 in names_b:
            return fight, False
        if n1 in names_b and n2 in names_a:
            return fight, True
    return None


def save_odds(session: Session, events: list[OddsEvent]) -> tuple[int, list[OddsEvent]]:
    """Guarda las cuotas de las peleas que encuentra en la base de datos.

    Devuelve (cuotas nuevas guardadas, eventos sin pelea correspondiente).
    """
    saved, unmatched = 0, []
    for e in events:
        match = find_fight(session, e)
        if match is None:
            unmatched.append(e)
            continue
        fight, inverted = match
        for p in e.prices:
            price_a, price_b = (p.price_2, p.price_1) if inverted else (p.price_1, p.price_2)
            exists = session.scalar(select(Odds).where(
                Odds.fight_id == fight.id,
                Odds.bookmaker == p.bookmaker,
                Odds.captured_at == p.last_update,
            ))
            if exists:
                continue  # misma foto de cuotas ya guardada
            session.add(Odds(
                fight_id=fight.id, bookmaker=p.bookmaker,
                price_a=price_a, price_b=price_b, captured_at=p.last_update,
            ))
            saved += 1
    session.commit()
    return saved, unmatched


def fetch_odds(client: PoliteClient, api_key: str, regions: str = "eu") -> str:
    """Descarga las cuotas. Caché de 6 horas para no gastar créditos de más."""
    url = (
        f"{BASE_URL}/sports/{SPORT}/odds"
        f"?apiKey={api_key}&regions={regions}&markets=h2h&oddsFormat=decimal"
    )
    return client.get(url, max_age=6 * 3600)


def main() -> None:
    from app.config import settings
    from app.db.models import Base
    from app.db.session import SessionLocal, engine

    if not settings.odds_api_key:
        print("Falta ODDS_API_KEY en el archivo .env. Consíguela gratis en https://the-odds-api.com")
        return
    Base.metadata.create_all(engine)
    client = PoliteClient(cache_dir="cache/odds_api", min_interval=1.0)
    try:
        events = parse_odds(fetch_odds(client, settings.odds_api_key, settings.odds_api_regions))
    except httpx.HTTPStatusError as err:
        # No mostramos la URL: lleva tu clave dentro
        print(f"The Odds API respondió {err.response.status_code}: {err.response.text[:300]}")
        return

    r = client.last_response
    if r is not None:  # None = respuesta sacada de la caché
        print(f"Créditos usados: {r.headers.get('x-requests-used')}, "
              f"restantes: {r.headers.get('x-requests-remaining')}")
    print(f"{len(events)} peleas de MMA con cuotas")

    with SessionLocal() as session:
        saved, unmatched = save_odds(session, events)
    print(f"Cuotas nuevas guardadas: {saved}")
    if unmatched:
        print(f"Sin pelea en la base de datos ({len(unmatched)}):")
        for e in unmatched[:20]:
            print(f"  {e.commence_time:%Y-%m-%d}  {e.fighter_1} vs {e.fighter_2}")


if __name__ == "__main__":
    main()
