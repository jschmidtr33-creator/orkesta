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


def test_plan_quota_stops_paid_checks() -> None:
    """El cupo de verificaciones del plan se respeta: al agotarse no se gasta ni un crédito más."""

    class Answer:
        def json(self) -> dict:
            return {"result": "ok"}

    class Client:
        calls = 0

        def get(self, *a: Any, **k: Any) -> Answer:
            Client.calls += 1
            return Answer()

    prober = api_verifier.ApiProber("k", limit=1)
    prober._one(Client(), "ana@lacteos.co")  # type: ignore[arg-type]
    with pytest.raises(api_verifier.SmtpUnavailable):
        prober._one(Client(), "luis@lacteos.co")  # type: ignore[arg-type]
    assert Client.calls == 1 and prober.used == 1


def test_pricing_is_public_and_packages_add_quota(
    client: TestClient, database: tuple[str, str]
) -> None:
    """Precios públicos desde el panel; un paquete extra suma verificaciones al mes."""
    register(client, "cliente@banco.co", company="Banco Andino")
    client.cookies.clear()
    pricing = client.get("/api/public/pricing").json()
    assert [p["code"] for p in pricing["plans"]] == ["personal", "inicial", "pro", "empresa"]
    pro = pricing["plans"][2]
    assert pro["price_month"] == 59 and pro["min_users"] == 10 and pricing["package"]["price"] == 9
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")
    settings = client.get("/api/admin/settings").json()["settings"]
    limits = settings["limits"]
    limits["pro"]["price_month"] = 75
    limits["inicial"]["verifications_per_user"] = 200
    saved = client.put("/api/admin/settings", json={"limits": limits,
                                                    "package": {"verifications": 500, "price": 7}})  # fmt: skip
    assert saved.status_code == 200
    assert client.get("/api/public/pricing").json()["plans"][2]["price_month"] == 75
    bank = next(t for t in client.get("/api/admin/tenants").json()["items"]
                if t["name"] == "Banco Andino")  # fmt: skip
    client.patch(f"/api/admin/tenants/{bank['id']}", json={"plan": "inicial"})
    assert client.get(f"/api/admin/tenants/{bank['id']}").json()["verifications_left"] == 200
    added = client.post(f"/api/admin/tenants/{bank['id']}/verifications", json={"packages": 2})
    assert added.json() == {"extra_this_month": 1000, "left": 1200}


def test_failed_service_calls_are_not_charged_to_the_plan() -> None:
    """Sin créditos en el servicio (o sin respuesta), la consulta no se descuenta del plan."""

    class Answer:
        def json(self) -> dict:
            return {"error": "Insufficient credits"}

    class Client:
        def get(self, *a: Any, **k: Any) -> Answer:
            return Answer()

    prober = api_verifier.ApiProber("k", limit=10)
    with pytest.raises(api_verifier.SmtpUnavailable):
        prober._one(Client(), "ana@lacteos.co")  # type: ignore[arg-type]
    assert prober.used == 0
