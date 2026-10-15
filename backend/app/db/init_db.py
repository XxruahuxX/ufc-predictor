"""Crea todas las tablas en la base de datos.

Ejecutar desde backend/:  python -m app.db.init_db

Cuando el esquema se estabilice pasaremos a migraciones con Alembic,
para poder cambiar tablas sin borrar datos.
"""
import sys

from sqlalchemy.exc import OperationalError

from app.db.models import Base
from app.db.session import engine

HOW_TO_FIX = """No puedo conectar con PostgreSQL:
  {error}
Comprueba que está arrancado:
  1. Abre Docker Desktop y espera a que ponga «Engine running».
  2. En la carpeta del proyecto: docker compose up -d db
  3. Espera a que «docker compose ps» muestre (healthy) y vuelve a probar.
Si el error habla de la contraseña, otro PostgreSQL ocupa el puerto 5432 (ver README)."""


def create_tables(db_engine=engine) -> bool:
    """Crea las tablas que falten. Si la base de datos no responde, explica qué hacer.

    Devuelve True si todo fue bien. Los comandos la llaman al empezar para
    dar un mensaje claro en vez de una traza de error de 100 líneas.
    """
    try:
        Base.metadata.create_all(db_engine)
    except OperationalError as err:
        print(HOW_TO_FIX.format(error=str(err.orig or err).splitlines()[0]))
        return False
    return True


def main() -> None:
    if not create_tables():
        sys.exit(1)
    print("Tablas creadas:", ", ".join(sorted(Base.metadata.tables)))


if __name__ == "__main__":
    main()
