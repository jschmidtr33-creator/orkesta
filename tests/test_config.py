from __future__ import annotations

import pytest
from pydantic import ValidationError

from orkesta.config import Settings, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("  postgresql://h/db  ", "postgresql+psycopg://h/db"),
    ],
)
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_rejects_non_postgres_url() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        normalize_database_url("mysql://h/db")


def test_production_requires_long_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(database_url="postgresql://h/db", environment="production", secret_key="corta")


def test_production_accepts_long_secret_key() -> None:
    settings = Settings(
        database_url="postgresql://h/db", environment="production", secret_key="x" * 40
    )
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_development_allows_empty_secret_key() -> None:
    assert Settings(database_url="postgresql://h/db").secret_key == ""


def test_retry_max_must_not_be_below_base() -> None:
    with pytest.raises(ValidationError, match="JOB_RETRY_MAX_SECONDS"):
        Settings(
            database_url="postgresql://h/db", job_retry_base_seconds=100, job_retry_max_seconds=10
        )
