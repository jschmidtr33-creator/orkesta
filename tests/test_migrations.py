from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

from orkesta.db.base import Base
from orkesta.db.migrations_state import alembic_config

MVP_TABLES = {
    "organizations",
    "users",
    "enrichment_batches",
    "import_rows",
    "companies",
    "contacts",
    "domains",
    "evidence_records",
    "email_pattern_observations",
    "email_candidates",
    "enrichment_jobs",
    "job_events",
    "ml_models",
    "training_examples",
    "app_settings",
    "audit_logs",
}
PROSPECTING_TABLES = {"prospects", "domain_patterns", "prospect_uploads"}

pytestmark = pytest.mark.db


def test_head_creates_mvp_and_prospecting_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == MVP_TABLES | PROSPECTING_TABLES


def test_models_and_migrations_are_in_sync(engine: Engine) -> None:
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_roundtrip(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    eng = create_engine(empty_database_url)
    try:
        assert set(inspect(eng).get_table_names()) - {"alembic_version"} == set()
        command.upgrade(config, "head")
        assert (
            set(inspect(eng).get_table_names()) - {"alembic_version"}
            == MVP_TABLES | PROSPECTING_TABLES
        )
    finally:
        eng.dispose()
