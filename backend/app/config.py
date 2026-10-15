"""Configuración leída de variables de entorno (o del archivo .env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ufc:ufc@localhost:5432/ufc"


settings = Settings()
