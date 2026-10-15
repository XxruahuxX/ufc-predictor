"""Crea todas las tablas en la base de datos.

Ejecutar desde backend/:  python -m app.db.init_db

Cuando el esquema se estabilice pasaremos a migraciones con Alembic,
para poder cambiar tablas sin borrar datos.
"""
from app.db.models import Base
from app.db.session import engine


def main() -> None:
    Base.metadata.create_all(engine)
    print("Tablas creadas:", ", ".join(sorted(Base.metadata.tables)))


if __name__ == "__main__":
    main()
