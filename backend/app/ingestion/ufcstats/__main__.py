"""Línea de comandos de la ingesta de UFCStats. Ver pipeline.py."""
import argparse

from app.db.models import Base
from app.db.session import SessionLocal, engine
from app.ingestion.http import PoliteClient
from app.ingestion.ufcstats.pipeline import UFCStatsIngestor


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta de datos de UFCStats.com")
    parser.add_argument("task", choices=["upcoming", "completed"])
    parser.add_argument("--limit", type=int, default=3, help="eventos pasados a descargar")
    parser.add_argument("--delay", type=float, default=2.0, help="segundos entre peticiones")
    args = parser.parse_args()

    Base.metadata.create_all(engine)  # por si aún no existen las tablas
    client = PoliteClient(cache_dir="cache/ufcstats", min_interval=args.delay)
    with SessionLocal() as session:
        ingestor = UFCStatsIngestor(client, session)
        if args.task == "upcoming":
            print("Descargando próximos eventos...")
            n = ingestor.sync_upcoming()
        else:
            print(f"Descargando los últimos {args.limit} eventos celebrados...")
            n = ingestor.sync_completed(limit=args.limit)
    print(f"Hecho: {n} eventos.")


if __name__ == "__main__":
    main()
