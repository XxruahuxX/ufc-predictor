"""Configuración leída de variables de entorno o del archivo .env.

Se busca .env en la raíz del proyecto (../.env, el mismo que usa Docker)
y en backend/.env. Si una variable está en los dos, gana backend/.env.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://ufc:ufc@localhost:5432/ufc"

    # The Odds API (https://the-odds-api.com). Clave gratuita: 500 créditos al mes.
    odds_api_key: str = ""
    # Región de las casas de apuestas: "eu" (Europa), "uk", "us", "au"
    odds_api_regions: str = "eu"


settings = Settings()
