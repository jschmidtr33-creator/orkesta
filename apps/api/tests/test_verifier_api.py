"""Verificador de correos externo (MillionVerifier): clave cifrada desde el panel, mismo contrato
que la verificación por SMTP y sin gastar consultas en la dirección de prueba. Datos sintéticos."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from orkesta.validation import api_verifier
from tests.conftest import register
from tests.test_admin import _role, _unlock


def test_api_prober_maps_results_and_deduces_catch_all(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    answers = {"ana@lacteos.co": "ok", "zzz@lacteos.co": "invalid", "x@todo.co": "catch_all"}

    def fake(self: Any, client: Any, email: str) -> tuple[int, str, str]:
        asked.append(email)
        kind = answers[email]
        code, why = api_verifier.CODES[kind]
        return code, why, kind

    monkeypatch.setattr(api_verifier.ApiProber, "_one", fake)
    prober = api_verifier.ApiProber("k")
    canary = "zz-0123456789ab@lacteos.co"
    out = prober.probe("mx", [canary, "ana@lacteos.co", "zzz@lacteos.co"])
    assert out["ana@lacteos.co"][0] == 250 and out["zzz@lacteos.co"][0] == 550
    assert out[canary][0] == 550 and canary not in asked  # la prueba no gasta créditos
    out = prober.probe("mx", ["zz-aaaaaaaaaaaa@todo.co", "x@todo.co"])
    assert out["zz-aaaaaaaaaaaa@todo.co"][0] == 250  # el dominio acepta todo


def test_admin_connects_the_verifier_key(
    client: TestClient, database: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_verifier, "balance", lambda key: 1500 if key == "buena" else None)
    register(client, "ana@lacteos.co")
    assert client.get("/api/admin/verifier").status_code == 403
    _role(database, "ana@lacteos.co", "owner")
    _unlock(client, "ana@lacteos.co")
    assert client.get("/api/admin/verifier").json() == {"configured": False, "credits": None}
    assert client.put("/api/admin/verifier", json={"key": "mala"}).status_code == 400
    saved = client.put("/api/admin/verifier", json={"key": "buena"}).json()
    assert saved == {"configured": True, "credits": 1500}
    status = client.get("/api/admin/verifier").json()
    assert status["configured"] and "buena" not in str(status)
    assert client.put("/api/admin/verifier", json={"key": ""}).json()["configured"] is False
