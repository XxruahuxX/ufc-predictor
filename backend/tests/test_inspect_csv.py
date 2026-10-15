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
