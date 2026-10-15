"""Pipeline de ingesta de UFCStats: descarga -> parsea -> guarda.

Uso desde backend/:
    python -m app.ingestion.ufcstats upcoming            # próximos eventos
    python -m app.ingestion.ufcstats completed --limit 3 # últimos 3 eventos celebrados
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.ingestion.http import PoliteClient
from app.ingestion.ufcstats import loader, parsers
from app.ingestion.ufcstats.parsers import BASE_URL

HOUR = 3600
DAY = 24 * HOUR


class UFCStatsIngestor:
    def __init__(self, client: PoliteClient, session: Session, today: date | None = None):
        self.client = client
        self.session = session
        self.today = today or date.today()

    # --- descargas (cada una con su tiempo de caché) ---------------------

    def _event_list(self, kind: str) -> list[parsers.EventSummary]:
        html = self.client.get(f"{BASE_URL}/statistics/events/{kind}?page=all", max_age=6 * HOUR)
        return parsers.parse_event_list(html)

    def _event(self, summary: parsers.EventSummary) -> parsers.EventDetail:
        # Un evento celebrado hace más de 3 días ya no cambia: caché para siempre
        old = summary.date and summary.date < self.today - timedelta(days=3)
        html = self.client.get(
            f"{BASE_URL}/event-details/{summary.ufcstats_id}", max_age=None if old else 6 * HOUR
        )
        return parsers.parse_event_detail(html, summary.ufcstats_id)

    def _fighter(self, ufcstats_id: str) -> parsers.FighterProfile:
        html = self.client.get(f"{BASE_URL}/fighter-details/{ufcstats_id}", max_age=DAY)
        return parsers.parse_fighter(html, ufcstats_id)

    def _fight(self, ufcstats_id: str) -> parsers.FightDetail:
        html = self.client.get(f"{BASE_URL}/fight-details/{ufcstats_id}", max_age=None)
        return parsers.parse_fight_detail(html, ufcstats_id)

    # --- tareas ------------------------------------------------------------

    def sync_upcoming(self) -> int:
        """Próximos eventos, sus carteleras y la ficha de cada peleador."""
        count = 0
        for summary in self._event_list("upcoming"):
            event = self._sync_event(summary, with_stats=False)
            print(f"  {event.date}  {event.name}: {len(event.fights)} peleas")
            count += 1
        return count

    def sync_completed(self, limit: int = 3) -> int:
        """Los `limit` eventos celebrados más recientes, con resultados y estadísticas."""
        # La lista de "completed" a veces incluye arriba el próximo evento: lo saltamos
        past = [e for e in self._event_list("completed") if e.date and e.date < self.today]
        for summary in past[:limit]:
            event = self._sync_event(summary, with_stats=True)
            print(f"  {event.date}  {event.name}: {len(event.fights)} peleas")
        return min(limit, len(past))

    def _sync_event(self, summary: parsers.EventSummary, with_stats: bool):
        detail = self._event(summary)
        event = loader.upsert_event(self.session, detail, today=self.today)
        self.session.commit()

        for fight in event.fights:
            if fight.status == "cancelled":
                continue
            for fighter in (fight.fighter_a, fight.fighter_b):
                loader.upsert_fighter(self.session, self._fighter(fighter.ufcstats_id))
            if with_stats and fight.result is not None:
                loader.save_fight_stats(self.session, fight, self._fight(fight.ufcstats_id))
            self.session.commit()  # guardamos pelea a pelea: si se corta, no se pierde todo
        return event
