# UFC Predictor

Aplicación web que predice peleas de la UFC combinando un modelo estadístico propio con predicciones externas ponderadas por su historial de aciertos.

> Las predicciones son informativas y no garantizan resultados.

## Estado

- [Fase 1: arquitectura](docs/01-arquitectura.md)
- [Fase 2: esquema de base de datos](docs/02-esquema.md)
- [Fase 3: ingesta de UFCStats](docs/03-ingesta.md)

## Puesta en marcha

Necesitas Docker Desktop, Git y Python 3.12 o superior.

```bash
git clone https://github.com/XxruahuxX/ufc-predictor
cd ufc-predictor
docker compose up -d db          # arranca PostgreSQL
cd backend
```

Crea el entorno de Python e instala las librerías.

**Windows (PowerShell):**
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Mac / Linux:**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

A partir de aquí, `python` significa `.\.venv\Scripts\python.exe` en Windows o `.venv/bin/python` en Mac/Linux.

```bash
python -m app.db.init_db                         # crea las tablas
python -m pytest                                 # tests
python -m app.ingestion.odds_api                 # cuotas (necesita ODDS_API_KEY en .env)
python -m app.ingestion.inspect_csv data/kaggle  # ver columnas de unos CSV
```

> UFCStats bloquea programas desde octubre de 2026. Ver [docs/03-ingesta.md](docs/03-ingesta.md).

## Problemas frecuentes en Windows

- **`python` abre otro Python** (por ejemplo el de MSYS2, sin pip). Usa `py` para crear el entorno y luego la ruta completa `.\.venv\Scripts\python.exe`.
- **"la autentificación password falló para el usuario ufc"**: tienes otro PostgreSQL instalado en Windows ocupando el puerto 5432. Compruébalo con `Get-Service postgresql*` y desinstálalo; este proyecto usa el de Docker.
