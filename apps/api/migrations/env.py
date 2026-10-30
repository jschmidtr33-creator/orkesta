"""Migraciones de ORKESTA: todo vive en el esquema ``orkesta``."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, text

from orkesta.core.config import get_settings, normalize_database_url
from orkesta.core.db import Base
from orkesta.models import load_all

load_all()
SCHEMA = "orkesta"
url = normalize_database_url(
    context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
)


def run() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_name=lambda name, type_, _parent: type_ != "schema" or name == SCHEMA,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run()
