"""Muestra qué hay dentro de una carpeta de archivos CSV.

Uso desde backend/:  python -m app.ingestion.inspect_csv data/kaggle

Para cada CSV imprime número de filas, columnas y las 3 primeras filas.
También lee los CSV que estén dentro de un .zip (así descarga Kaggle).
Sirve para escribir el importador sin tener que enviar los archivos.
"""
import csv
import io
import sys
import zipfile
from pathlib import Path


def short(value: str, limit: int = 40) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def describe(name: str, size_bytes: int, open_binary) -> None:
    """Imprime el resumen de un CSV. open_binary() devuelve el archivo en modo binario."""
    for encoding in ("utf-8-sig", "latin-1"):  # probamos las codificaciones más habituales
        try:
            with open_binary() as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding=encoding, newline=""))
                header = next(reader, [])
                samples, rows = [], 0
                for row in reader:
                    rows += 1
                    if len(samples) < 3:
                        samples.append(row)
            break
        except UnicodeDecodeError:
            continue
    print(f"\n=== {name}  ({rows} filas, {size_bytes / 1_000_000:.1f} MB)")
    print(f"Columnas ({len(header)}): {', '.join(header)}")
    for row in samples:
        print("  | " + " | ".join(short(v) for v in row))


def inspect(folder: Path) -> None:
    files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []
    if not files:
        print(f"La carpeta {folder.resolve()} está vacía o no existe.")
        print("Mueve ahí el archivo que descargaste de Kaggle.")
        return

    found = 0
    for path in files:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            describe(str(path.relative_to(folder)), path.stat().st_size, lambda p=path: p.open("rb"))
            found += 1
        elif suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.filename.lower().endswith(".csv"):
                        describe(f"{path.name} -> {info.filename}", info.file_size,
                                 lambda i=info: zf.open(i))
                        found += 1

    if not found:
        print(f"No hay archivos .csv (ni .zip con CSV dentro) en {folder.resolve()}")
        print("Archivos que sí hay:")
        for path in files:
            print(f"  {path.relative_to(folder)}  ({path.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    inspect(Path(sys.argv[1] if len(sys.argv) > 1 else "data/kaggle"))
