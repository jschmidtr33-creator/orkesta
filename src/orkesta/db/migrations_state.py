"""Comprueba si la base de datos está en la última migración de Alembic."""

from __future__ import annotations

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

SCRIPT_LOCATION = "orkesta:migrations"


def alembic_config(database_url: str | None = None) -> Config:
    """Configuración de Alembic sin depender de ``alembic.ini`` (útil en tests y en ``/ready``)."""
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def expected_heads() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def current_heads(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(MigrationContext.configure(connection).get_current_heads())
