from sqlalchemy import create_engine, inspect

from app.db.init_db import create_tables


def test_crea_las_tablas():
    engine = create_engine("sqlite://")
    assert create_tables(engine) is True
    assert "fights" in inspect(engine).get_table_names()


def test_sin_base_de_datos_explica_que_hacer(tmp_path, capsys):
    # Una base de datos que no se puede abrir: la carpeta no existe
    engine = create_engine(f"sqlite:///{tmp_path / 'no_existe' / 'ufc.db'}")
    assert create_tables(engine) is False
    out = capsys.readouterr().out
    assert "No puedo conectar con PostgreSQL" in out
    assert "docker compose up -d db" in out
    assert "Traceback" not in out
