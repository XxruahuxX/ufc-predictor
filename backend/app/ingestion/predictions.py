"""Predicciones de fuentes externas (medios, tipsters, foros).

Por ahora se apuntan a mano en un CSV y se importan con:
    python -m app.ingestion.predictions archivo.csv

Una fila por predicción. Columnas (la primera fila es la cabecera):
    fuente        nombre de la fuente: "MMA Junkie", "Tipster X"...
    tipo          website, reddit, tipster, forum o media
    fecha_evento  2026-10-17 (vale un día de diferencia, por la zona horaria)
    ganador       a quién elige la fuente
    rival         el otro peleador
    confianza     opcional: 0.65, 65 o 65 %. Vacío = la de la fuente (0.6)
    metodo        opcional: KO/TKO, SUB o DEC
    publicado     opcional: 2026-10-15. Vacío = el día del evento
    url           opcional: de dónde sale, para poder comprobarlo

Si la misma fuente ya tenía una predicción para esa pelea, se sustituye:
cuenta la última que dio antes del evento.
"""
import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Event, ExternalPrediction, Fight, Fighter, Source
from app.ingestion.matching import FighterIndex
from app.ingestion.names import normalize_name

KINDS = ("website", "reddit", "tipster", "forum", "media")
METHODS = {"ko": "KO/TKO", "tko": "KO/TKO", "ko/tko": "KO/TKO", "sub": "SUB",
           "submission": "SUB", "dec": "DEC", "decision": "DEC"}
COLUMNS = ("fuente", "tipo", "fecha_evento", "ganador", "rival")  # obligatorias


class RowError(ValueError):
    """Una fila que no se puede guardar. El mensaje dice por qué."""


@dataclass
class ImportReport:
    new: int = 0
    updated: int = 0
    sources_new: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # "línea 5: ..."


def parse_confidence(text: str) -> float | None:
    """'0.65', '65' o '65 %' -> 0.65. Vacío -> None (se usará la de la fuente)."""
    text = text.replace("%", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise RowError(f"confianza no válida: {text!r}") from None
    if value > 1:
        value /= 100  # venía en porcentaje
    if not 0.5 <= value <= 1:
        raise RowError(f"la confianza tiene que estar entre 50 y 100 %: {text!r}")
    return value


def parse_day(text: str, column: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise RowError(f"{column} no es una fecha AAAA-MM-DD: {text!r}") from None


def parse_method(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    if text.lower() not in METHODS:
        raise RowError(f"método no válido: {text!r} (KO/TKO, SUB o DEC)")
    return METHODS[text.lower()]


def find_fight(session: Session, day: date, winner: str, loser: str) -> tuple[Fight, Fighter]:
    """La pelea de ese día entre los dos peleadores, y el peleador elegido."""
    fights = session.scalars(
        select(Fight).join(Event)
        .where(Event.date.between(day - timedelta(days=1), day + timedelta(days=1)),
               Fight.status != "cancelled")
        .options(selectinload(Fight.fighter_a).selectinload(Fighter.aliases),
                 selectinload(Fight.fighter_b).selectinload(Fighter.aliases))
    ).all()
    for fight in fights:
        index = FighterIndex([fight.fighter_a, fight.fighter_b])
        picked, other = index.find(winner), index.find(loser)
        if len(picked) == 1 and len(other) == 1 and picked != other:
            return fight, picked[0]
    if not fights:
        raise RowError(f"no hay ningún evento el {day}")
    raise RowError(f"no hay ninguna pelea {winner} vs. {loser} el {day}")


def save_row(session: Session, row: dict[str, str], report: ImportReport) -> None:
    """Guarda una fila. Lanza RowError si no se puede.

    Primero se comprueba todo y después se escribe: así una fila con un
    error no deja nada a medias (por ejemplo, una fuente nueva sin predicción).
    """
    missing = [c for c in COLUMNS if not row.get(c, "").strip()]
    if missing:
        raise RowError(f"faltan columnas: {', '.join(missing)}")
    day = parse_day(row["fecha_evento"], "fecha_evento")
    fight, picked = find_fight(session, day, row["ganador"].strip(), row["rival"].strip())
    confidence = parse_confidence(row.get("confianza", ""))
    method = parse_method(row.get("metodo", ""))
    published = row.get("publicado", "").strip()
    published_day = parse_day(published, "publicado") if published else fight.event.date
    name, kind = row["fuente"].strip(), row["tipo"].strip().lower()
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None and kind not in KINDS:
        raise RowError(f"tipo no válido: {kind!r} ({', '.join(KINDS)})")

    # A partir de aquí, todo es válido: se escribe
    if source is None:
        source = Source(name=name, kind=kind)
        session.add(source)
        session.flush()  # ahora tiene id y default_confidence
        report.sources_new.append(name)
    prediction = session.scalar(select(ExternalPrediction).where(
        ExternalPrediction.source_id == source.id, ExternalPrediction.fight_id == fight.id))
    if prediction is None:
        prediction = ExternalPrediction(source=source, fight=fight)
        session.add(prediction)
        report.new += 1
    else:
        report.updated += 1
    prediction.predicted_winner_id = picked.id
    prediction.confidence = confidence or source.default_confidence
    prediction.predicted_method = method
    prediction.published_at = datetime.combine(published_day, datetime.min.time(), timezone.utc)
    prediction.url = row.get("url", "").strip()[:500] or None
    # Si había una evaluación de la predicción anterior, ya no vale
    prediction.is_correct = prediction.brier = prediction.evaluated_at = None
    session.flush()


def import_predictions(session: Session, lines: list[str]) -> ImportReport:
    """Importa las filas del CSV. No hace commit: eso lo decide quien llama."""
    report = ImportReport()
    reader = csv.DictReader(lines)
    reader.fieldnames = [normalize_name(n).replace(" ", "_") for n in reader.fieldnames or []]
    for row in reader:
        try:
            save_row(session, {k: v or "" for k, v in row.items() if k}, report)
        except RowError as e:
            report.problems.append(f"línea {reader.line_num}: {e}")
    return report


def move_picks(session: Session, fight_id: int, moves: dict[int, int]) -> None:
    """Cambia de peleador las predicciones de una pelea. moves: id viejo -> id nuevo.

    Hace falta cuando un peleador creado desde Wikipedia resulta ser uno del
    CSV: la predicción que lo elegía tiene que elegir ahora al del CSV.
    """
    for prediction in session.scalars(select(ExternalPrediction)
                                      .where(ExternalPrediction.fight_id == fight_id)):
        prediction.predicted_winner_id = moves.get(prediction.predicted_winner_id,
                                                   prediction.predicted_winner_id)


def main() -> None:
    from app.db.session import SessionLocal  # aquí, para que los tests no se conecten

    parser = argparse.ArgumentParser(description="Importa predicciones de fuentes externas desde un CSV")
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    if not args.csv.exists():
        sys.exit(f"No existe {args.csv}")
    # utf-8-sig: Excel y el Bloc de notas pueden añadir una marca al principio
    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        lines = f.read().splitlines()
    with SessionLocal() as session:
        report = import_predictions(session, lines)
        session.commit()
    print(f"Predicciones: {report.new} nuevas, {report.updated} actualizadas")
    if report.sources_new:
        print(f"Fuentes nuevas: {', '.join(report.sources_new)}")
    if report.problems:
        print(f"No se han guardado {len(report.problems)} filas:")
        for problem in report.problems:
            print(f"  {problem}")


if __name__ == "__main__":
    main()
