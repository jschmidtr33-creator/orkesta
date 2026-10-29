"""Configuración de la aplicación, leída de variables de entorno.

Railway entrega la URL de PostgreSQL como ``postgresql://`` o ``postgres://``; aquí se normaliza
al dialecto ``postgresql+psycopg://`` que usa SQLAlchemy con psycopg 3.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_MIN_SECRET_KEY_LENGTH = 32


def normalize_database_url(url: str) -> str:
    """Convierte las variantes de URL de PostgreSQL al driver psycopg 3."""
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgresql+psycopg://"):
        return url
    raise ValueError("DATABASE_URL debe ser una URL de PostgreSQL (postgresql://...)")


class Settings(BaseSettings):
    """Ajustes de ORKESTA. Cada campo se lee de la variable de entorno con el mismo nombre."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    database_url: str = Field(min_length=1)
    secret_key: str = ""

    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)

    worker_poll_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    job_lock_timeout_seconds: int = Field(default=600, ge=30)
    job_retry_base_seconds: int = Field(default=30, ge=1)
    job_retry_max_seconds: int = Field(default=3600, ge=1)
    job_default_max_attempts: int = Field(default=3, ge=1, le=20)

    max_upload_mb: int = Field(default=10, ge=1, le=100)
    max_import_rows: int = Field(default=50_000, ge=1, le=500_000)

    # URL pública de ORKESTA (enlaces de baja y retorno de OAuth). Ej.: https://orkesta.midominio.com
    public_base_url: str = ""
    # Credencial OAuth de Google Cloud para enviar por Gmail (opcional).
    google_client_id: str = ""
    google_client_secret: str = ""
    mail_default_daily_limit: int = Field(default=50, ge=1, le=2000)

    session_max_age_hours: int = Field(default=12, ge=1, le=24 * 30)
    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_lockout_minutes: int = Field(default=15, ge=1, le=24 * 60)

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _check_production_requirements(self) -> Settings:
        if self.environment in ("staging", "production") and (
            len(self.secret_key) < _MIN_SECRET_KEY_LENGTH
        ):
            raise ValueError(
                f"SECRET_KEY debe tener al menos {_MIN_SECRET_KEY_LENGTH} caracteres "
                "en staging y producción"
            )
        if self.job_retry_max_seconds < self.job_retry_base_seconds:
            raise ValueError("JOB_RETRY_MAX_SECONDS no puede ser menor que JOB_RETRY_BASE_SECONDS")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve los ajustes cargados una sola vez por proceso."""
    return Settings()
