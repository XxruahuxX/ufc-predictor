"""Guarda en la base de datos los próximos eventos y carteleras de Wikipedia.

Wikipedia no tiene los ids de UFCStats, así que se empareja así:
    evento    mismo nombre, aunque haya cambiado de fecha; si no, misma
              fecha (o un día de diferencia, por la zona horaria)
    peleador  nombre, sin tildes ni mayúsculas (ver matching.py)
    pelea     mismo evento y los mismos dos peleadores, en cualquier orden

Lo que no existe se crea sin ufcstats_id. Cuando el CSV de Kaggle traiga el
resultado, kaggle_import.py lo une con estos registros en vez de duplicarlos.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Event, Fight, Fighter
from app.ingestion.matching import FighterIndex, is_catchweight
from app.ingestion.names import normalize_name
from app.ingestion.wikipedia.parsers import Bout, ScheduledEvent


@dataclass
class SyncReport:
    """Resumen de una sincronización. Se imprime al final."""
    events_new: int = 0
    events_updated: int = 0
    events_deleted: list[str] = field(default_factory=list)
    fights_new: int = 0
    fights_updated: int = 0
    fights_cancelled: int = 0
    fighters_new: list[str] = field(default_factory=list)  # debutantes (o nombre escrito distinto)
    skipped: list[str] = field(default_factory=list)  # peleas no guardadas y por qué


def find_event(candidates: list[Event], s: ScheduledEvent, used: set) -> Event | None:
    """El evento de la base de datos que corresponde a uno de Wikipedia.

    1. Mismo nombre, aunque haya cambiado de fecha (evento aplazado).
    2. Misma fecha: el nombre cambia cuando se anuncia la pelea estelar
       ("UFC Fight Night 289" -> "UFC Fight Night: Allen vs. Duncan").
    3. Un día antes o después: según la zona horaria, la fecha puede variar.
    """
    free = [e for e in candidates if e not in used]
    for e in free:
        if normalize_name(e.name) == normalize_name(s.name):
            return e
    for days in (0, 1, -1):
        for e in free:
            if e.date == s.date + timedelta(days=days):
                return e
    return None


def find_fighter(session: Session, index: FighterIndex, name: str, bout: Bout,
                 report: SyncReport, event: Event) -> Fighter | None:
    """El peleador con ese nombre. Si no existe, lo crea (debutante)."""
    found = index.find(name, bout.weight_class)
    if len(found) == 1:
        return found[0]
    if found:
        report.skipped.append(f"{event.name}: {bout.fighter_1} vs. {bout.fighter_2}"
                              f" ({len(found)} peleadores se llaman {name})")
        return None
    fighter = Fighter(name=name[:120])
    if bout.weight_class and not is_catchweight(bout.weight_class):
        fighter.weight_class = bout.weight_class
    session.add(fighter)
    index.add(fighter)
    report.fighters_new.append(f"{name} ({event.name})")
    return fighter


def sync_card(session: Session, event: Event, bouts: list[Bout], index: FighterIndex,
              report: SyncReport) -> None:
    """Crea o actualiza las peleas del evento. Las que ya no están, canceladas."""
    existing = {frozenset((f.fighter_a, f.fighter_b)): f for f in event.fights}
    seen = set()
    for bout in bouts:
        a = find_fighter(session, index, bout.fighter_1, bout, report, event)
        b = find_fighter(session, index, bout.fighter_2, bout, report, event)
        if a is None or b is None or a is b or frozenset((a, b)) in seen:
            continue
        pair = frozenset((a, b))
        seen.add(pair)
        fight = existing.get(pair)
        if fight is None:
            # Esquinas A y B por orden alfabético. No se copia el orden de
            # Wikipedia porque, tras la pelea, pone primero al ganador.
            first, second = sorted((a, b), key=lambda f: normalize_name(f.name))
            fight = Fight(event=event, fighter_a=first, fighter_b=second)
            session.add(fight)
            report.fights_new += 1
        elif fight.status == "completed":
            continue  # ya tiene resultado: no se toca
        else:
            report.fights_updated += 1
        fight.weight_class = bout.weight_class
        fight.is_title_fight = bout.is_title_fight
        # En la UFC, las peleas por el título y las estelares son a 5 asaltos
        fight.scheduled_rounds = 5 if bout.is_title_fight or bout.main_event else 3
        fight.bout_order = len(seen)
        fight.status = "scheduled"

    for pair, fight in existing.items():
        if pair not in seen and fight.status == "scheduled":
            fight.status = "cancelled"
            report.fights_cancelled += 1


def sync_upcoming(session: Session, schedule: list[tuple[ScheduledEvent, list[Bout] | None]],
                  today: date) -> SyncReport:
    """Guarda los próximos eventos (sin commit: eso lo decide quien llama).

    schedule: cada evento con su cartelera. None = no hay página o no se
    pudo leer; en ese caso no se tocan sus peleas.
    """
    report = SyncReport()
    index = FighterIndex(session.scalars(select(Fighter).options(selectinload(Fighter.aliases))))
    # Solo eventos próximos: uno ya celebrado nunca se toca desde aquí
    candidates = list(session.scalars(select(Event).where(
        Event.status == "upcoming", Event.date >= today - timedelta(days=1)
    )))
    used = set()

    for s, bouts in schedule:
        if s.date < today:
            continue
        event = find_event(candidates, s, used)
        if event is None:
            event = Event(name=s.name, date=s.date, location=s.location, status="upcoming")
            session.add(event)
            report.events_new += 1
        else:
            report.events_updated += 1
        if event.ufcstats_id is None:  # creado por Wikipedia: manda Wikipedia
            event.name, event.date = s.name, s.date
            event.location = s.location or event.location
        else:  # viene de UFCStats: solo se rellena lo que falte
            event.location = event.location or s.location
        used.add(event)
        if bouts:
            sync_card(session, event, bouts, index, report)

    # Eventos creados por Wikipedia que ya no están en su lista: cancelados
    # o con otra fecha y otro nombre. Se borran con sus peleas. Si la lista
    # no trae ningún evento próximo (algo falló al leerla) no se borra nada.
    ghosts = [e for e in candidates
              if e not in used and e.ufcstats_id is None and e.date > today]
    if used and ghosts:
        session.flush()
        session.execute(delete(Event).where(Event.id.in_([e.id for e in ghosts])))
        report.events_deleted = [f"{e.date} {e.name}" for e in ghosts]
    session.flush()
    return report
