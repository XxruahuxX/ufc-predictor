Páginas de ejemplo con la misma estructura HTML que UFCStats.com, reducidas
a lo que leen los parsers. Están escritas a mano: los nombres e ids son
inventados.

Para comprobar los parsers con páginas reales, ejecuta en tu máquina:
    python -m app.ingestion.ufcstats.sample
Guarda páginas reales en tests/fixtures/ufcstats/real/ y el test
test_real_pages las usa automáticamente.
