"""Base de datos de prueba.

Por defecto usa SQLite en memoria: no necesita Docker y es instantáneo.
Para probar contra PostgreSQL de verdad:
    TEST_DATABASE_URL=postgresql+psycopg://ufc:ufc@localhost:5432/ufc pytest
"""
import os

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.models import Base

TEST_URL = os.getenv("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def session():
    engine = create_engine(TEST_URL)
    if engine.dialect.name == "sqlite":
        # SQLite no comprueba claves foráneas si no se le pide
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)
