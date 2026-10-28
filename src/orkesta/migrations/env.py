"""Entorno de Alembic. La URL sale de ``DATABASE_URL`` salvo que se pase explícitamente."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from orkesta.config import normalize_database_url
from orkesta.db import models  # noqa: F401  (registra las tablas en el metadata)
from orkesta.db.base import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    from orkesta.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=normalize_database_url(_database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = normalize_database_url(_database_url())
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
