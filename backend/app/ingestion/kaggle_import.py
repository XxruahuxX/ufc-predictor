"""Importa el histórico de peleas desde el CSV de Kaggle.

UFCStats bloquea los programas (ver docs/03-ingesta.md). Este CSV trae
sus mismos datos: eventos, peleas, resultados y estadísticas por pelea.

Uso desde backend/:
    python -m app.ingestion.kaggle_import data/kaggle/UFC_full_data_silver_v2.csv

Se importa:
    eventos, peleadores, peleas, resultados, estadísticas de cada pelea
    y cuotas (si el CSV las trae).
NO se importa:
    - Las medias de carrera (SlpM, Str_Acc, TD_Avg...). Son una foto de
      hoy: usarlas para predecir una pelea de 2015 sería dejar que el
      modelo vea el futuro. Las medias se calculan con fight_stats.
    - Estadísticas por asalto, rankings y cuotas de KO o sumisión: no
      están en el esquema.

Se puede ejecutar varias veces: actualiza lo que ya existe (mismo
ufcstats_id) y no duplica nada.
"""
import argparse
import csv
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Event, Fight, Fighter, FightResult, FightStats, Odds
from app.ingestion.names import normalize_name
from app.ingestion.ufcstats.parsers import clean, id_from_url, parse_clock, parse_date, to_int

ROUND_SECONDS = 5 * 60
BOOKMAKER = "kaggle"  # nombre con el que se guardan las cuotas del CSV

# Sin estas columnas no se puede importar: el CSV sería de otro dataset
REQUIRED_COLUMNS = [
    "event_name", "event_date", "event_url", "fight_url",
    "f_1_name", "f_2_name", "f_1_url", "f_2_url",
    "winner", "result", "finish_round", "finish_time",
]

# Estadísticas de cada pelea: columna del CSV (sin "f_1_"/"f_2_") -> campo de FightStats
STAT_COLUMNS = {
    "knockdowns": "knockdowns",
    "sig_strikes_succ": "sig_strikes_landed",
    "sig_strikes_att": "sig_strikes_attempted",
    "total_strikes_succ": "total_strikes_landed",
    "total_strikes_att": "total_strikes_attempted",
    "takedown_succ": "takedowns_landed",
    "takedown_att": "takedowns_attempted",
    "submission_att": "submission_attempts",
    "ctrl_time_sec": "control_seconds",
}

# Textos de la columna "winner" que significan "no hay ganador" (ya normalizados)
NO_WINNER = {"", "draw", "nc", "no contest", "n a", "none", "nan", "null"}


# ---------------------------------------------------------------------------
# Datos de una fila, ya limpios
# ---------------------------------------------------------------------------

@dataclass
class CsvFighter:
    ufcstats_id: str
    name: str  # como aparece en la pelea: "Brendan Allen"
    full_name: str  # nombre + apellidos de su ficha, para reconocer al ganador
    nickname: str | None
    height_cm: float | None
    reach_cm: float | None
    stance: str | None
    birth_date: date | None
    wins: int
    losses: int
    draws: int
    no_contests: int


@dataclass
class CsvFight:
    """Una fila del CSV. Los pares van en el orden del CSV: (f_1, f_2)."""
    ufcstats_id: str
    event_id: str
    event_name: str
    event_date: date
    location: str | None
    weight_class: str | None
    is_title_fight: bool
    scheduled_rounds: int
    fighters: tuple[CsvFighter, CsvFighter]
    bout_order: int = 0  # 1 = pelea estelar
    # Resultado. Sin method = la pelea aún no se ha celebrado.
    method: str | None = None
    method_detail: str | None = None
    outcome: str | None = None  # "win", "draw", "no_contest"; None = no se sabe
    winner: int | None = None  # 0 = ganó f_1, 1 = ganó f_2
    end_round: int | None = None
    end_time_seconds: int | None = None
    totals: tuple[dict | None, dict | None] = (None, None)  # {campo de FightStats: valor}
    prices: tuple[float, float] | None = None  # cuotas decimales (f_1, f_2)

    @property
    def fight_seconds(self) -> int | None:
        """Duración real de la pelea: 2:46 del 5.º asalto -> 4 * 300 + 166 = 1366."""
        if self.end_round is None or self.end_time_seconds is None:
            return None
        return (self.end_round - 1) * ROUND_SECONDS + self.end_time_seconds


@dataclass
class Report:
    """Resumen de la importación. Se imprime al final para revisar los datos."""
    rows: int = 0
    events_new: int = 0
    fighters_new: int = 0
    fights_new: int = 0
    fights_updated: int = 0
    with_stats: int = 0
    with_odds: int = 0
    outcomes: Counter = field(default_factory=Counter)  # "win" -> nº de peleas
    methods: Counter = field(default_factory=Counter)  # (texto del CSV, categoría) -> nº
    skipped: Counter = field(default_factory=Counter)  # motivo -> filas que no se importan
    problems: Counter = field(default_factory=Counter)  # se importa la pelea, pero sin ese dato
    examples: dict[str, list[str]] = field(default_factory=dict)  # hasta 3 casos de cada aviso

    def problem(self, kind: str, example: str) -> None:
        self.problems[kind] += 1
        examples = self.examples.setdefault(kind, [])
        if len(examples) < 3:
            examples.append(example)


class SkipRow(Exception):
    """La fila no se puede importar. El mensaje dice por qué."""


class WrongCsvError(Exception):
    """Al CSV le faltan columnas: es de otro dataset."""


# ---------------------------------------------------------------------------
# Funciones para limpiar valores
# ---------------------------------------------------------------------------

def text(value: str | None, limit: int) -> str | None:
    """Texto limpio y cortado a `limit` letras (lo que cabe en la columna).

    None si está vacío o es '--'.
    """
    value = clean(value)
    return value[:limit] if value and value != "--" else None


def number(value: str | None) -> float | None:
    """'519' o '519.0' -> 519.0. Vacío, '--' o 'nan' -> None."""
    try:
        result = float(clean(value))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def integer(value: str | None, default: int | None = None) -> int | None:
    result = number(value)
    return int(result) if result is not None else default


def iso_date(value: str | None) -> date | None:
    """'1995-12-28' -> date. También acepta 'Dec 28, 1995'. Vacío -> None."""
    value = clean(value)
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return parse_date(value)


def decimal_odds(value: str | None) -> float | None:
    """Pasa una cuota a formato decimal, el que usa la tabla odds.

    Americana: -150 -> 1.667 y +130 -> 2.3. Decimal: 1.8 -> 1.8.
    Una cuota americana nunca está entre -100 y +100, y una decimal
    siempre es mayor que 1: el propio número dice en qué formato viene.
    """
    odds = number(value)
    if odds is None:
        return None
    if odds >= 100:
        return round(1 + odds / 100, 3)
    if odds <= -100:
        return round(1 + 100 / -odds, 3)
    if 1 < odds < 100:
        return round(odds, 3)
    return None  # 0,5, 1, -50...: no es una cuota válida


def normalize_result(result: str | None, details: str | None) -> tuple[str, str | None]:
    """Traduce la columna "result" del CSV a nuestras categorías.

    'KO/TKO'                  -> ('KO/TKO', 'Punches')           detalle de result_details
    "TKO - Doctor's Stoppage" -> ('KO/TKO', "Doctor's Stoppage")
    'Submission'              -> ('SUB', 'Rear Naked Choke')
    'Decision - Split'        -> ('DEC', 'Split')                sin las tarjetas de los jueces
    'DQ'                      -> ('DQ', 'Illegal Knee')
    'Overturned', 'Could Not Continue', 'Other' -> ('OTHER', 'Overturned')
    """
    result = clean(result)
    details = clean(details)[:120] or None
    after_dash = result.split("-", 1)[1].strip() if "-" in result else None
    upper = result.upper()
    if upper.startswith("DECISION"):
        return "DEC", after_dash
    if "KO" in upper:
        return "KO/TKO", after_dash or details
    if upper.startswith("SUB"):
        return "SUB", details
    if upper in ("DQ", "DISQUALIFICATION"):
        return "DQ", details
    return "OTHER", result[:120] or None


def winner_index(winner: str | None, fighters: tuple[CsvFighter, CsvFighter]) -> int | None:
    """0 si ganó f_1, 1 si ganó f_2, None si no hay ganador.

    Lanza ValueError si el nombre no es claramente de uno de los dos:
    es mejor no guardar un resultado que adivinarlo.
    """
    name = normalize_name(winner or "")
    if name in NO_WINNER:
        return None
    matches = [
        i for i, f in enumerate(fighters)
        if name in (normalize_name(f.name), normalize_name(f.full_name))
    ]
    if len(matches) != 1:
        raise ValueError(winner)
    return matches[0]


# ---------------------------------------------------------------------------
# Lectura del CSV
# ---------------------------------------------------------------------------

def parse_fighter(row: dict[str, str], n: int) -> CsvFighter:
    """Lee los datos del peleador n (1 o 2) de una fila."""
    p = f"f_{n}_"
    url = clean(row.get(p + "url")) or clean(row.get(p + "fighter_url"))
    height = number(row.get(p + "fighter_height_cm"))
    reach = number(row.get(p + "fighter_reach_cm"))
    first = clean(row.get(p + "fighter_f_name"))
    last = clean(row.get(p + "fighter_l_name"))
    return CsvFighter(
        ufcstats_id=id_from_url(url),
        name=clean(row.get(p + "name"))[:120],
        full_name=f"{first} {last}".strip(),
        nickname=text(row.get(p + "fighter_nickname"), 120),
        height_cm=round(height, 1) if height else None,
        reach_cm=round(reach, 1) if reach else None,
        stance=text(row.get(p + "fighter_stance"), 20),
        birth_date=iso_date(row.get(p + "fighter_dob")),
        wins=integer(row.get(p + "fighter_w"), 0),
        losses=integer(row.get(p + "fighter_l"), 0),
        draws=integer(row.get(p + "fighter_d"), 0),
        no_contests=to_int(clean(row.get(p + "fighter_nc_dq"))),  # puede venir como "1 NC"
    )


def parse_totals(row: dict[str, str], n: int) -> dict | None:
    """Estadísticas del peleador n en la pelea. None si no hay (peleas antiguas)."""
    values = {name: integer(row.get(f"f_{n}_{column}")) for column, name in STAT_COLUMNS.items()}
    if all(v is None for v in values.values()):
        return None
    return {name: v or 0 for name, v in values.items()}


def parse_row(row: dict[str, str], report: Report) -> CsvFight:
    """Convierte una fila del CSV en un CsvFight. Lanza SkipRow si no sirve."""
    fight_id = id_from_url(clean(row["fight_url"]))
    event_id = id_from_url(clean(row["event_url"]))
    fighters = (parse_fighter(row, 1), parse_fighter(row, 2))
    ids = [fight_id, event_id, fighters[0].ufcstats_id, fighters[1].ufcstats_id]
    if not all(ids) or any(len(i) > 32 for i in ids):
        raise SkipRow("falta la URL de la pelea, del evento o de un peleador")
    if fighters[0].ufcstats_id == fighters[1].ufcstats_id:
        raise SkipRow("el mismo peleador en las dos esquinas")
    event_date = iso_date(row["event_date"])
    if event_date is None:
        raise SkipRow("fecha de evento no válida")

    location = ", ".join(
        part for part in (clean(row.get(c)) for c in ("event_city", "event_state", "event_country"))
        if part
    )
    is_title = clean(row.get("title_fight")).lower() in ("true", "1", "yes")
    finish_round = integer(row["finish_round"])
    rounds = integer(row.get("num_rounds")) or (5 if is_title else 3)
    fight = CsvFight(
        ufcstats_id=fight_id,
        event_id=event_id,
        event_name=clean(row["event_name"])[:200],
        event_date=event_date,
        location=location[:200] or None,
        weight_class=text(row.get("weight_class"), 40),
        is_title_fight=is_title,
        # El esquema solo admite 3 o 5 asaltos. Los formatos de los años 90
        # (1 asalto, sin límite de tiempo...) se guardan como 3.
        scheduled_rounds=5 if max(rounds, finish_round or 0) > 3 else 3,
        fighters=fighters,
        totals=(parse_totals(row, 1), parse_totals(row, 2)),
    )
    label = f"{fight.event_date} {fighters[0].name} vs {fighters[1].name}"

    raw = clean(row["result"])
    if raw:  # la pelea se celebró
        fight.method, fight.method_detail = normalize_result(raw, row.get("result_details"))
        report.methods[(raw, fight.method)] += 1
        fight.end_round = finish_round
        fight.end_time_seconds = parse_clock(row["finish_time"])
        try:
            fight.winner = winner_index(row["winner"], fighters)
        except ValueError:
            report.problem("ganador no reconocido", f"{label} (winner = {row['winner']!r})")
        else:
            if fight.winner is not None:
                fight.outcome = "win"
            elif fight.method == "DEC" or "DRAW" in raw.upper():
                fight.outcome = "draw"  # decisión sin ganador
            elif fight.method == "OTHER":
                fight.outcome = "no_contest"  # anulada, no pudo continuar...
            else:
                report.problem(f"{fight.method} sin ganador", label)
        if fight.outcome and finish_round is None:
            report.problem("sin asalto final", label)
            fight.outcome = None
        if fight.outcome:
            report.outcomes[fight.outcome] += 1

    price_1, price_2 = decimal_odds(row.get("f_1_odds")), decimal_odds(row.get("f_2_odds"))
    if price_1 and price_2:
        fight.prices = (price_1, price_2)
    elif clean(row.get("f_1_odds")) or clean(row.get("f_2_odds")):
        report.problem("cuota incompleta o no válida",
                       f"{label} ({row.get('f_1_odds')!r}, {row.get('f_2_odds')!r})")
    return fight


def parse_rows(reader: csv.DictReader) -> tuple[list[CsvFight], Report]:
    """Lee todas las filas. Devuelve las peleas válidas y el resumen."""
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise WrongCsvError(f"Al CSV le faltan columnas: {', '.join(missing)}. ¿Es el dataset correcto?")

    report = Report()
    fights: list[CsvFight] = []
    seen: set[str] = set()
    bout_order: Counter = Counter()  # posición de cada pelea dentro de su evento
    for row in reader:
        report.rows += 1
        if id_from_url(clean(row["fight_url"])) in seen:
            report.skipped["pelea repetida"] += 1
            continue
        try:
            fight = parse_row(row, report)
        except SkipRow as err:
            report.skipped[str(err)] += 1
            continue
        seen.add(fight.ufcstats_id)
        # El CSV lista cada cartelera como UFCStats: la pelea estelar primero
        bout_order[fight.event_id] += 1
        fight.bout_order = bout_order[fight.event_id]
        fights.append(fight)
    return fights, report


def read_csv(path: Path) -> tuple[list[CsvFight], Report]:
    """Lee el CSV en UTF-8 y, si no lo es, en latin-1 (acepta cualquier byte)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return parse_rows(csv.DictReader(f))
    except UnicodeDecodeError:
        with path.open(encoding="latin-1", newline="") as f:
            return parse_rows(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Guardado en la base de datos
# ---------------------------------------------------------------------------

def is_catchweight(weight_class: str) -> bool:
    """Peso pactado o abierto: no es la categoría habitual del peleador."""
    lower = weight_class.lower()
    return "catch" in lower or "open" in lower


def save_fighter(fighter: Fighter, p: CsvFighter) -> None:
    fighter.name = p.name
    fighter.wins, fighter.losses = p.wins, p.losses
    fighter.draws, fighter.no_contests = p.draws, p.no_contests
    # Solo pisamos un dato si el CSV lo trae
    for attr in ("nickname", "height_cm", "reach_cm", "stance", "birth_date"):
        value = getattr(p, attr)
        if value is not None:
            setattr(fighter, attr, value)


def save_result(fight: Fight, cf: CsvFight, fighters: dict[str, Fighter]) -> None:
    if cf.outcome is None:
        return  # aún no se ha celebrado, o el resultado no está claro
    result = fight.result or FightResult()
    fight.result = result
    result.outcome = cf.outcome
    winner = cf.fighters[cf.winner] if cf.winner is not None else None
    result.winner_id = fighters[winner.ufcstats_id].id if winner else None
    result.method = cf.method
    result.method_detail = cf.method_detail
    result.end_round = cf.end_round
    result.end_time_seconds = cf.end_time_seconds


def save_stats(fight: Fight, cf: CsvFight, fighters: dict[str, Fighter]) -> bool:
    """Guarda los totales de los dos peleadores. Devuelve True si guardó algo."""
    seconds = cf.fight_seconds
    if not seconds:
        return False
    saved = False
    for p, totals in zip(cf.fighters, cf.totals):
        if totals is None:
            continue
        fighter_id = fighters[p.ufcstats_id].id
        stats = next((s for s in fight.stats if s.fighter_id == fighter_id), None)
        if stats is None:
            stats = FightStats(fighter_id=fighter_id)
            fight.stats.append(stats)
        for name, value in totals.items():
            setattr(stats, name, value)
        stats.fight_seconds = seconds
        saved = True
    return saved


def save_fights(session: Session, fights: list[CsvFight], report: Report, today: date) -> None:
    """Guarda las peleas leídas. No hace commit: eso lo decide quien llama."""
    # 1. Lo que ya hay en la base de datos, en diccionarios por ufcstats_id.
    #    Así no hace falta una consulta por fila (son casi 9000).
    events = {e.ufcstats_id: e for e in session.scalars(select(Event))}
    fighters = {f.ufcstats_id: f for f in session.scalars(select(Fighter))}
    query = select(Fight).options(selectinload(Fight.result), selectinload(Fight.stats))
    existing = {f.ufcstats_id: f for f in session.scalars(query)}

    # De la más antigua a la más reciente: así cada peleador acaba con los
    # datos de su última pelea (nombre, categoría de peso...).
    fights = sorted(fights, key=lambda cf: cf.event_date)

    # 2. Eventos y peleadores
    for cf in fights:
        event = events.get(cf.event_id)
        if event is None:
            event = Event(ufcstats_id=cf.event_id, name=cf.event_name, date=cf.event_date,
                          status="upcoming")
            session.add(event)
            events[cf.event_id] = event
            report.events_new += 1
        event.name, event.date, event.location = cf.event_name, cf.event_date, cf.location
        if cf.method or cf.event_date < today:
            event.status = "completed"

        for p in cf.fighters:
            fighter = fighters.get(p.ufcstats_id)
            if fighter is None:
                fighter = Fighter(ufcstats_id=p.ufcstats_id, name=p.name)
                session.add(fighter)
                fighters[p.ufcstats_id] = fighter
                report.fighters_new += 1
            save_fighter(fighter, p)
            if cf.weight_class and not is_catchweight(cf.weight_class):
                fighter.weight_class = cf.weight_class
    session.flush()  # ahora todos los peleadores tienen id

    # 3. Peleas, resultados y estadísticas
    for cf in fights:
        # Esquinas A y B ordenadas por id. El CSV pone primero al GANADOR:
        # si copiáramos su orden, "gana A" sería casi siempre verdad y el
        # modelo aprendería una trampa.
        id_a, id_b = sorted(p.ufcstats_id for p in cf.fighters)
        fight = existing.get(cf.ufcstats_id)
        if fight is None:
            fight = Fight(
                ufcstats_id=cf.ufcstats_id, event=events[cf.event_id],
                fighter_a=fighters[id_a], fighter_b=fighters[id_b],
            )
            session.add(fight)
            existing[cf.ufcstats_id] = fight
            report.fights_new += 1
        else:
            report.fights_updated += 1
        fight.event = events[cf.event_id]
        fight.fighter_a, fight.fighter_b = fighters[id_a], fighters[id_b]
        fight.weight_class = cf.weight_class
        fight.is_title_fight = cf.is_title_fight
        fight.scheduled_rounds = cf.scheduled_rounds
        fight.bout_order = cf.bout_order
        if cf.method or cf.event_date < today:
            fight.status = "completed"
            if cf.method is None:
                report.problem("pelea pasada sin resultado", f"{cf.event_date} {cf.event_name}")
        else:
            fight.status = "scheduled"
        save_result(fight, cf, fighters)
        if save_stats(fight, cf, fighters):
            report.with_stats += 1
    session.flush()  # ahora todas las peleas tienen id

    # 4. Cuotas (una fila por pelea, con la casa "kaggle")
    old_odds = {o.fight_id: o for o in session.scalars(select(Odds).where(Odds.bookmaker == BOOKMAKER))}
    for cf in fights:
        if cf.prices is None:
            continue
        fight = existing[cf.ufcstats_id]
        price_1, price_2 = cf.prices
        if fight.fighter_a.ufcstats_id != cf.fighters[0].ufcstats_id:
            price_1, price_2 = price_2, price_1  # f_1 quedó en la esquina B
        odds = old_odds.get(fight.id)
        if odds is None:
            odds = Odds(fight_id=fight.id, bookmaker=BOOKMAKER)
            session.add(odds)
        odds.price_a, odds.price_b = price_1, price_2
        # El CSV no dice cuándo se tomaron: usamos el día del evento a las 00:00 UTC
        d = cf.event_date
        odds.captured_at = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        report.with_odds += 1


def import_csv(session: Session, path: Path, today: date | None = None) -> Report:
    """Lee el CSV y lo guarda en la base de datos (sin commit)."""
    fights, report = read_csv(path)
    save_fights(session, fights, report, today or date.today())
    return report


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------

def print_report(r: Report) -> None:
    print(f"Filas leídas: {r.rows}")
    print(f"Nuevos: {r.events_new} eventos, {r.fighters_new} peleadores, {r.fights_new} peleas"
          f" (ya estaban: {r.fights_updated})")
    print("Resultados: " + (", ".join(f"{k} {v}" for k, v in r.outcomes.most_common()) or "ninguno"))
    print(f"Peleas con estadísticas: {r.with_stats}. Con cuotas: {r.with_odds}.")
    print("Columna 'result' del CSV -> categoría:")
    for (raw, method), n in r.methods.most_common():
        print(f"  {n:6}  {raw} -> {method}")
    if r.skipped:
        print("Filas no importadas:")
        for reason, n in r.skipped.most_common():
            print(f"  {n:6}  {reason}")
    if r.problems:
        print("Avisos (la pelea se importa, pero sin ese dato):")
        for kind, n in r.problems.most_common():
            print(f"  {n:6}  {kind}")
            for example in r.examples[kind]:
                print(f"          p. ej. {example}")


def print_totals(session: Session) -> None:
    tables = [("eventos", Event), ("peleadores", Fighter), ("peleas", Fight),
              ("resultados", FightResult), ("estadísticas", FightStats), ("cuotas", Odds)]
    counts = [f"{session.scalar(select(func.count()).select_from(model))} {name}"
              for name, model in tables]
    print("En la base de datos: " + ", ".join(counts))


def main() -> None:
    from app.db.models import Base
    from app.db.session import SessionLocal, engine

    parser = argparse.ArgumentParser(description="Importa el histórico de peleas desde el CSV de Kaggle")
    parser.add_argument("csv", type=Path, help="p. ej. data/kaggle/UFC_full_data_silver_v2.csv")
    args = parser.parse_args()
    if not args.csv.is_file():
        print(f"No encuentro el archivo {args.csv.resolve()}")
        return

    Base.metadata.create_all(engine)  # por si aún no existen las tablas
    start = time.perf_counter()
    with SessionLocal() as session:
        try:
            report = import_csv(session, args.csv)
        except WrongCsvError as err:
            print(err)
            return
        session.commit()
        print_report(report)
        print_totals(session)
    print(f"Tiempo: {time.perf_counter() - start:.0f} s")


if __name__ == "__main__":
    main()
