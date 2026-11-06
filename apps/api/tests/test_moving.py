"""Mudanza de servidor: aviso durante la copia final y redirección después."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orkesta.core.config import get_settings
from orkesta.main import create_app


def test_maintenance_then_redirect(monkeypatch, session_factory) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MOVED_TO", "mantenimiento")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app(session_factory))
        assert client.get("/api/health").status_code == 200
        page = client.get("/app/ingresar")
        assert page.status_code == 503 and "mudando" in page.text
        assert client.post("/api/auth/login", json={}).status_code == 503

        monkeypatch.setenv("MOVED_TO", "https://3-130-137-39.sslip.io/")
        get_settings.cache_clear()
        client = TestClient(create_app(session_factory))
        moved = client.get("/app/p/abc123?vista_previa=1", follow_redirects=False)
        assert moved.status_code == 308
        assert (
            moved.headers["location"] == "https://3-130-137-39.sslip.io/app/p/abc123?vista_previa=1"
        )
    finally:
        monkeypatch.delenv("MOVED_TO")
        get_settings.cache_clear()
