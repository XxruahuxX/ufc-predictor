"""Guarda en la base de datos lo que devuelven los parsers.

Todas las funciones son "upsert": si el registro ya existe (mismo
ufcstats_id) se actualiza; si no, se crea. Así la ingesta se puede
repetir cuantas veces se quiera sin duplicar nada.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Fight, Fighter, FightResult, FightStats
from app.ingestion.ufcstats.parsers import EventDetail, FightDetail, FighterProfile

ROUND_SECONDS = 5 * 60


def _get_or_new(session: Session, model, ufcstats_id: str, **defaults):
    """Busca por ufcstats_id. Si no existe, lo crea (sin guardar todavía)."""
    obj = session.scalar(select(model).where(model.ufcstats_id == ufcstats_id))
    if obj is None:
        obj = model(ufcstats_id=ufcstats_id, **defaults)
        session.add(obj)
    return obj


def upsert_fighter_stub(session: Session, ufcstats_id: str, name: str) -> Fighter:
    """Crea el peleador solo con nombre si aún no existe.

    Se usa al leer una cartelera; la ficha completa llega después.
    """
    return _get_or_new(session, Fighter, ufcstats_id, name=name)


def upsert_fighter(session: Session, p: FighterProfile) -> Fighter:
    fighter = _get_or_new(session, Fighter, p.ufcstats_id, name=p.name)
    fighter.name = p.name
    fighter.nickname = p.nickname
    fighter.wins, fighter.losses = p.wins, p.losses
    fighter.draws, fighter.no_contests = p.draws, p.no_contests
    fighter.height_cm = p.height_cm
    fighter.reach_cm = p.reach_cm
    fighter.stance = p.stance
    fighter.birth_date = p.birth_date
    return fighter


def upsert_event(session: Session, e: EventDetail, today: date | None = None) -> Event:
    """Guarda el evento, su cartelera y los resultados (si los hay)."""
    today = today or date.today()
    event = _get_or_new(session, Event, e.ufcstats_id, name=e.name, date=e.date)
    event.name = e.name
    event.date = e.date
    event.location = e.location
    has_results = any(f.outcome for f in e.fights)
    event.status = "completed" if has_results or (e.date and e.date < today) else "upcoming"

    seen_ids = set()
    for row in e.fights:
        seen_ids.add(row.ufcstats_id)
        fighters = {
            fid: upsert_fighter_stub(session, fid, name)
            for fid, name in zip(row.fighter_ids, row.fighter_names)
        }
        session.flush()  # para que los peleadores nuevos tengan id

        # Esquina A y B: UFCStats pone primero al GANADOR. Si guardáramos ese
        # orden, "gana A" sería verdad casi siempre y el modelo aprendería
        # una trampa. Por eso ordenamos por id: el orden queda fijo y no
        # dice nada del resultado.
        id_a, id_b = sorted(row.fighter_ids)
        fight = _get_or_new(
            session, Fight, row.ufcstats_id,
            event=event, fighter_a=fighters[id_a], fighter_b=fighters[id_b],
        )
        fight.event = event
        fight.fighter_a, fight.fighter_b = fighters[id_a], fighters[id_b]
        fight.weight_class = row.weight_class
        fight.is_title_fight = row.is_title_fight
        fight.bout_order = row.bout_order
        if row.is_title_fight:
            fight.scheduled_rounds = 5

        if row.outcome:
            fight.status = "completed"
            session.flush()
            result = session.get(FightResult, fight.id) or FightResult(fight_id=fight.id)
            result.outcome = row.outcome
            result.winner_id = fighters[row.winner_ufcstats_id].id if row.winner_ufcstats_id else None
            result.method = row.method
            result.method_detail = row.method_detail
            result.end_round = row.end_round
            result.end_time_seconds = row.end_time_seconds
            session.add(result)
        else:
            fight.status = "scheduled"

    # Peleas que estaban en la cartelera y ya no aparecen: canceladas
    session.flush()
    for fight in event.fights:
        if fight.ufcstats_id not in seen_ids and fight.status == "scheduled":
            fight.status = "cancelled"
    return event


def save_fight_stats(session: Session, fight: Fight, d: FightDetail) -> None:
    """Guarda las estadísticas de los dos peleadores de una pelea."""
    if d.scheduled_rounds in (3, 5):
        fight.scheduled_rounds = d.scheduled_rounds
    if not d.totals or fight.result is None:
        return
    # Duración real de la pelea, para calcular medias por minuto
    seconds = (fight.result.end_round - 1) * ROUND_SECONDS + fight.result.end_time_seconds

    for t in d.totals:
        fighter = session.scalar(select(Fighter).where(Fighter.ufcstats_id == t.ufcstats_id))
        if fighter is None:
            continue
        stats = session.scalar(
            select(FightStats).where(
                FightStats.fight_id == fight.id, FightStats.fighter_id == fighter.id
            )
        ) or FightStats(fight_id=fight.id, fighter_id=fighter.id)
        stats.knockdowns = t.knockdowns
        stats.sig_strikes_landed = t.sig_strikes_landed
        stats.sig_strikes_attempted = t.sig_strikes_attempted
        stats.total_strikes_landed = t.total_strikes_landed
        stats.total_strikes_attempted = t.total_strikes_attempted
        stats.takedowns_landed = t.takedowns_landed
        stats.takedowns_attempted = t.takedowns_attempted
        stats.submission_attempts = t.submission_attempts
        stats.control_seconds = t.control_seconds
        stats.fight_seconds = seconds
        session.add(stats)
