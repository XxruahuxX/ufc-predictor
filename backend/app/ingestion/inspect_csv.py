"""Muestra qué hay dentro de una carpeta de archivos CSV.

Uso desde backend/:  python -m app.ingestion.inspect_csv data/kaggle

Para cada CSV imprime número de filas, columnas y las 3 primeras filas.
Sirve para escribir el importador sin tener que enviar los archivos.
"""
import csv
import sys
from pathlib import Path


def open_text(path: Path):
    """Abre el CSV probando las codificaciones más habituales."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with path.open(encoding=encoding) as f:
                f.read(100_000)
            return path.open(encoding=encoding, newline="")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"No se pudo leer {path}")


def short(value: str, limit: int = 40) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def inspect(folder: Path) -> None:
    files = sorted(folder.rglob("*.csv"))
    if not files:
        print(f"No hay archivos .csv en {folder.resolve()}")
        return
    for path in files:
        with open_text(path) as f:
            reader = csv.reader(f)
            header = next(reader, [])
            samples, rows = [], 0
            for row in reader:
                rows += 1
                if len(samples) < 3:
                    samples.append(row)
        size_mb = path.stat().st_size / 1_000_000
        print(f"\n=== {path.relative_to(folder)}  ({rows} filas, {size_mb:.1f} MB)")
        print(f"Columnas ({len(header)}): {', '.join(header)}")
        for row in samples:
            print("  | " + " | ".join(short(v) for v in row))


if __name__ == "__main__":
    inspect(Path(sys.argv[1] if len(sys.argv) > 1 else "data/kaggle"))
