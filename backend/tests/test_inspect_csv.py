import zipfile

from app.ingestion.inspect_csv import inspect


def test_inspeccionar_csv(tmp_path, capsys):
    (tmp_path / "fights.csv").write_text(
        "fighter_1,fighter_2,winner\nAlpha,Bravo,Alpha\nCharlie,Delta,Delta\n", encoding="utf-8"
    )
    inspect(tmp_path)
    out = capsys.readouterr().out
    assert "fights.csv  (2 filas" in out
    assert "Columnas (3): fighter_1, fighter_2, winner" in out
    assert "Alpha | Bravo | Alpha" in out


def test_inspeccionar_csv_dentro_de_zip(tmp_path, capsys):
    with zipfile.ZipFile(tmp_path / "archive.zip", "w") as zf:
        zf.writestr("data/fighters.csv", "name,reach\nAlpha,190\n")
    inspect(tmp_path)
    out = capsys.readouterr().out
    assert "archive.zip -> data/fighters.csv  (1 filas" in out
    assert "Columnas (2): name, reach" in out


def test_csv_en_latin1(tmp_path, capsys):
    (tmp_path / "x.csv").write_bytes("nombre\nJosé\n".encode("latin-1"))
    inspect(tmp_path)
    assert "José" in capsys.readouterr().out


def test_carpeta_sin_csv_lista_lo_que_hay(tmp_path, capsys):
    (tmp_path / "ufc.xlsx").write_bytes(b"abc")
    inspect(tmp_path)
    out = capsys.readouterr().out
    assert "No hay archivos .csv" in out
    assert "ufc.xlsx" in out


def test_carpeta_vacia(tmp_path, capsys):
    inspect(tmp_path / "no_existe")
    assert "vacía o no existe" in capsys.readouterr().out
