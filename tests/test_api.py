from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from orkesta import __version__
from orkesta.api.main import create_app


def _client(engine: Engine) -> TestClient:
    return TestClient(create_app(engine_provider=lambda: engine))


def test_health_does_not_need_database() -> None:
    unreachable = create_engine("postgresql+psycopg://nadie@127.0.0.1:1/ninguna")
    response = TestClient(create_app(engine_provider=lambda: unreachable)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_reports_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nadie@127.0.0.1:1/ninguna")
    from orkesta.config import get_settings

    get_settings.cache_clear()
    unreachable = create_engine(
        "postgresql+psycopg://nadie@127.0.0.1:1/ninguna", connect_args={"connect_timeout": 2}
    )
    with _client(unreachable) as client:
        response = client.get("/ready")
    get_settings.cache_clear()
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unreachable"


@pytest.mark.db
def test_ready_ok_when_migrated(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "migrations": "ok"},
    }


@pytest.mark.db
def test_ready_not_ready_when_migrations_pending(
    database_url: str, empty_database_url: str
) -> None:
    empty = create_engine(empty_database_url)
    try:
        with _client(empty) as client:
            response = client.get("/ready")
    finally:
        empty.dispose()
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migrations"] == "pending"
    assert body["checks"]["migrations_current"] == []
