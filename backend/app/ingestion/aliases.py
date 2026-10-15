"""Alias de peleadores: nombres que otra fuente escribe distinto que UFCStats.

Hay cambios que la comparación de nombres no puede adivinar, como un
diminutivo: Wikipedia dice "Abusupiyan Magomedov" y UFCStats, "Abus
Magomedov". Esos casos se apuntan a mano en aliases.csv.

La lista está en un archivo del repositorio, no solo en la base de datos,
para que no se pierda si se vuelve a crear. Cada vez que se sincroniza
Wikipedia, los alias que falten se guardan en la tabla fighter_aliases.

Solo se apunta un alias cuando se ha comprobado que es la misma persona.
"""
import csv
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Fighter, FighterAlias
from app.ingestion.names import normalize_name

ALIASES_FILE = Path(__file__).with_name("aliases.csv")


def read_aliases(path: Path = ALIASES_FILE) -> list[tuple[str, str]]:
    """(otro nombre, nombre en UFCStats) de cada fila. Las líneas con # son comentarios."""
    # utf-8-sig: el Bloc de notas de Windows puede añadir una marca al principio
    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = [line for line in f if line.strip() and not line.startswith("#")]
    rows = list(csv.reader(lines))[1:]  # la primera es la cabecera
    return [(row[0].strip(), row[1].strip()) for row in rows if len(row) >= 2]


def add_aliases(session: Session, aliases: list[tuple[str, str]]) -> list[str]:
    """Guarda en fighter_aliases los alias que falten.

    Devuelve los que no se han podido guardar y por qué.
    """
    known = defaultdict(list)  # nombre normalizado -> peleadores del CSV con ese nombre
    for fighter in session.scalars(select(Fighter).where(Fighter.ufcstats_id.is_not(None))
                                   .options(selectinload(Fighter.aliases))):
        known[normalize_name(fighter.name)].append(fighter)

    problems = []
    for alias, name in aliases:
        found = known.get(normalize_name(name), [])
        if len(found) != 1:
            why = "no está en la base de datos" if not found else f"hay {len(found)} con ese nombre"
            problems.append(f"{alias} -> {name}: {why}")
            continue
        fighter = found[0]
        if normalize_name(alias) not in {normalize_name(a.alias) for a in fighter.aliases}:
            fighter.aliases.append(FighterAlias(alias=alias[:120]))
    return problems
