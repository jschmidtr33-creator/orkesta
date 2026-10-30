"""Configuración desde variables de entorno (nunca secretos en el código)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
MIN_SECRET_LENGTH = 32


def normalize_database_url(url: str) -> str:
    """Render y Railway entregan ``postgres://`` o ``postgresql://``; SQLAlchemy usa psycopg 3."""
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    if url.startswith("postgresql+psycopg://"):
        return url
    raise ValueError("DATABASE_URL debe ser una URL de PostgreSQL")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Environment = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/orkesta"
    # Esquema propio: la versión anterior de ORKESTA vive en ``public`` y no se toca.
    database_schema: str = "orkesta"
    secret_key: str = ""
    public_base_url: str = ""

    access_token_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_days: int = Field(default=30, ge=1, le=90)
    invitation_days: int = Field(default=7, ge=1, le=30)
    login_max_failures: int = Field(default=8, ge=3, le=50)
    login_window_minutes: int = Field(default=15, ge=1, le=120)

    # Worker de trabajos en segundo plano dentro del proceso web (un solo servicio).
    run_worker: bool = False
    # Verificación de buzones por SMTP (puerto 25). Se desactiva si la red lo bloquea.
    smtp_probe: bool = True
    worker_threads: int = Field(default=2, ge=1, le=8)

    # Inicio de sesión con Google (opcional: sin estas variables se muestra «pendiente»).
    google_client_id: str = ""
    google_client_secret: str = ""

    # Primer administrador de la plataforma, creado en el despliegue.
    orkesta_bootstrap_admin_email: str = ""
    orkesta_bootstrap_admin_password: str = ""
    orkesta_bootstrap_org_name: str = "Mi empresa"

    @field_validator("database_url")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _check_secrets(self) -> Settings:
        if self.is_deployed and len(self.secret_key) < MIN_SECRET_LENGTH:
            raise ValueError(f"SECRET_KEY debe tener al menos {MIN_SECRET_LENGTH} caracteres")
        return self

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")

    @property
    def signing_key(self) -> str:
        # En desarrollo y pruebas se usa una clave fija conocida; en producción es obligatoria.
        return self.secret_key or "orkesta-clave-solo-para-desarrollo-local-000"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.public_base_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
