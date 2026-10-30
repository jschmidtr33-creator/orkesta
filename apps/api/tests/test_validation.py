"""Etapa 3: validación de correos con DNS y SMTP simulados."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.db import set_context
from orkesta.core.jobs import run_once
from orkesta.data.models import Contact, DataPoint
from orkesta.validation import agent
from orkesta.validation.checks import Checker, SmtpHealth, SmtpProber, SmtpUnavailable
from tests.conftest import register

# Buzones que «existen» en cada dominio simulado.
MAILBOXES = {
    "bancoandino.co": {"laura.gomez@bancoandino.co", "agarcia@bancoandino.co"},
    "lacteos.co": {"ventas@lacteos.co"},
}
CATCH_ALL = {"todo.co"}
BLOCKED = {"bloqueado.co"}
MX = {
    "bancoandino.co": ["mx1.bancoandino.co"],
    "lacteos.co": ["mx.lacteos.co"],
    "todo.co": ["mx.todo.co"],
    "bloqueado.co": ["mx.bloqueado.co"],
    "mailinator.com": ["mx.mailinator.com"],
}


class FakeProber(SmtpProber):
    def __init__(self) -> None:
        super().__init__("orkesta.test", "verificacion@orkesta.test")
        self.sessions = 0

    def probe(self, mx: str, addresses: list[str]) -> dict[str, tuple[int, str]]:
        domain = mx.split(".", 1)[1]
        if domain in BLOCKED:
            raise SmtpUnavailable("timeout")
        self.sessions += 1
        out = {}
        for a in addresses:
            if domain in CATCH_ALL or a in MAILBOXES.get(domain, set()):
                out[a] = (250, "2.1.5 OK")
            else:
                out[a] = (550, "5.1.1 User unknown")
        return out


@pytest.fixture(autouse=True)
def fake_network() -> Iterator[FakeProber]:
    prober = FakeProber()
    agent.RESOLVER = lambda d: MX.get(d, [])
    agent.PROBER_FACTORY = lambda: prober
    from orkesta.validation import router

    router.limiter.hits.clear()
    yield prober
    agent.RESOLVER, agent.PROBER_FACTORY = None, None


def _contact(client: TestClient, **data: str) -> str:
    response = client.post("/api/contacts", json=data)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _validate(client: TestClient, factory: sessionmaker[Session], **body: object) -> dict:
    created = client.post("/api/validation/runs", json=body)
    assert created.status_code == 201, created.text
    while run_once(factory, "pruebas"):
        pass
    return client.get(f"/api/validation/runs/{created.json()['id']}").json()


def test_validates_and_finds_missing_emails(
    client: TestClient, session_factory: sessionmaker[Session], fake_network: FakeProber
) -> None:
    me = register(client, "ana@lacteos.co")
    ok = _contact(client, first_name="Laura", last_name="Gómez", company="Banco Andino",
                  email="laura.gomez@bancoandino.co")  # fmt: skip
    bad = _contact(client, first_name="Pedro", last_name="Paz", company="Banco Andino",
                   email="pedro.paz@bancoandino.co")  # fmt: skip
    syntax = _contact(client, first_name="Sin", last_name="Dominio", email="x@nodomain.zz")
    catch = _contact(client, first_name="Toda", last_name="Via", email="toda@todo.co")
    temp = _contact(client, first_name="Temp", last_name="Oral", email="t@mailinator.com")
    # Sin correo, pero su empresa tiene dominio conocido: el agente prueba formatos.
    missing = _contact(client, first_name="Andrés", last_name="García", company="Banco Andino")
    with session_factory.begin() as session:
        set_context(session, tenant_id=me["workspace"]["id"])
        contact = session.get(Contact, missing)
        assert contact is not None
        company = session.get(Contact, ok)
        assert company is not None
        contact.company_id = company.company_id
        from orkesta.data.models import Company

        firm = session.get(Company, company.company_id)
        assert firm is not None
        firm.domain = "bancoandino.co"

    run = _validate(client, session_factory, find_missing=True)
    assert run["status"] == "completed", run
    assert run["totals"]["found"] == 1

    def get(cid: str) -> dict:
        return client.get(f"/api/contacts/{cid}").json()

    assert get(ok)["email_status"] == "valido" and get(ok)["status"] == "validado"
    assert get(bad)["email_status"] == "invalido"
    assert get(syntax)["email_status"] == "invalido"
    assert "sin MX" in get(syntax)["email_check_reason"]
    assert get(catch)["email_status"] == "catch_all"
    assert get(temp)["email_status"] == "riesgoso"
    found = get(missing)
    assert found["email"] == "agarcia@bancoandino.co" and found["email_status"] == "valido"
    with session_factory() as session:
        set_context(session, tenant_id=me["workspace"]["id"])
        point = session.scalar(
            select(DataPoint).where(
                DataPoint.contact_id == missing, DataPoint.source_type == "validacion"
            )
        )
        assert point is not None and "SMTP" in point.source_label

    summary = client.get("/api/validation/summary").json()
    assert summary["counts"]["valido"] == 2
    listed = client.get("/api/contacts", params={"correo": "invalido"}).json()
    assert listed["total"] == 2
    assert client.get("/api/dashboard/summary").json()["kpis"]["emails_validated"] == 2


def test_editing_email_resets_validation(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    cid = _contact(client, first_name="Laura", last_name="Gómez",
                   email="laura.gomez@bancoandino.co")  # fmt: skip
    _validate(client, session_factory, ids=[cid])
    assert client.get(f"/api/contacts/{cid}").json()["email_status"] == "valido"
    client.patch(f"/api/contacts/{cid}", json={"first_name": "Laura", "last_name": "Gómez",
                                                "email": "otra@bancoandino.co"})  # fmt: skip
    assert client.get(f"/api/contacts/{cid}").json()["email_status"] == "sin_validar"


def test_blocked_smtp_is_never_reported_as_valid() -> None:
    health = SmtpHealth()
    prober = FakeProber()
    checker = Checker(prober=prober, resolver=lambda d: MX.get(d, []), health=health)
    for domain in ("bloqueado.co",):
        MX[domain] = ["mx.bloqueado.co", "mx2.bloqueado.co"]
    verdicts = checker.verify(["a@bloqueado.co"])
    assert verdicts[0].status == "desconocido"
    assert "no se pudo confirmar" in verdicts[0].reason


def test_validation_runs_are_isolated(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _contact(client, first_name="Laura", last_name="Gómez", email="laura.gomez@bancoandino.co")
    run = _validate(client, session_factory)
    client.post("/api/auth/logout")
    register(client, "otro@otra.co", company="Otra")
    assert client.get(f"/api/validation/runs/{run['id']}").status_code == 404
    assert client.get("/api/validation/summary").json()["counts"] == {}


def test_estimated_email_that_bounces_is_replaced_by_the_confirmed_one(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    me = register(client, "ana@lacteos.co")
    cid = _contact(client, first_name="Andrés", last_name="García",
                   email="andres.garcia@bancoandino.co")  # fmt: skip
    with session_factory.begin() as session:
        set_context(session, tenant_id=me["workspace"]["id"])
        contact = session.get(Contact, cid)
        assert contact is not None
        contact.email_status = "estimado"
    run = _validate(client, session_factory, find_missing=True)
    assert run["totals"]["found"] == 1 and run["totals"]["valido"] == 1
    assert run["totals"].get("invalido", 0) == 0
    fixed = client.get(f"/api/contacts/{cid}").json()
    assert fixed["email"] == "agarcia@bancoandino.co" and fixed["email_status"] == "valido"
