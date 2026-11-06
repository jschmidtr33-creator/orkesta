"""Unir la cuenta de una persona a la empresa de su equipo, sin perder nada."""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi.testclient import TestClient

from tests.conftest import register
from tests.test_admin import _login, _role

SOFIA = {"first_name": "Sofía", "last_name": "Rojas", "email": "sofia@retaildemo.cl",
         "company": "Retail Demo", "country": "Chile"}  # fmt: skip


def _workspace(client: TestClient) -> dict:
    return client.get("/api/auth/session").json()["workspace"]


def test_merge_moves_everything_and_keeps_owners(
    client: TestClient, database: tuple[str, str]
) -> None:
    # Empresa de destino: Ana.
    register(client, "ana@americadigital.co", company="America Digital")
    target = _workspace(client)
    client.post("/api/contacts", json=SOFIA)
    ana_deal = client.post("/api/crm/deals", json={"title": "Venta Ana"}).json()
    client.post("/api/lists", json={"name": "Decisores"})
    client.post("/api/proposals", json={"deal_id": ana_deal["id"]})
    client.post("/api/auth/logout")

    # Cuenta propia de Luis, con un contacto que Ana también tiene.
    register(client, "luis@americadigital.co", company="Cuenta de Luis")
    source = _workspace(client)
    client.post("/api/contacts", json=SOFIA)
    client.post(
        "/api/contacts", json={**SOFIA, "first_name": "Pedro", "email": "pedro@retaildemo.cl"}
    )
    luis_deal = client.post("/api/crm/deals", json={"title": "Venta Luis"}).json()
    luis_list = client.post("/api/lists", json={"name": "Decisores"}).json()
    proposal = client.post("/api/proposals", json={"deal_id": luis_deal["id"]}).json()
    assert proposal["number"] == 1
    client.post("/api/auth/logout")

    # La creadora del panel une la cuenta de Luis a America Digital.
    register(client, "jenn@orkesta.co", company="Equipo ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _login(client, "jenn@orkesta.co")
    url = f"/api/admin/tenants/{source['id']}/merge"
    wrong = client.post(url, json={"target_id": target["id"], "confirm": "otra"})
    assert wrong.status_code == 400
    merged = client.post(url, json={"target_id": target["id"], "role": "vendedor",
                                    "confirm": "Cuenta de Luis"})  # fmt: skip
    assert merged.status_code == 200, merged.text
    report = merged.json()
    assert report["contactos_que_ya_existian"] == 1 and report["empresas_cliente_unidas"] == 1
    assert report["moved"]["contacts"] == 2
    with psycopg.connect(database[0]) as conn:
        assert conn.execute("SELECT count(*) FROM orkesta.tenants WHERE id = %s",
                            (source["id"],)).fetchone() == (0,)  # fmt: skip

    # Luis entra: ahora es vendedor de America Digital y tiene todo lo suyo.
    client.headers.pop("X-Admin-Token", None)
    _login(client, "luis@americadigital.co", panel=False)
    me = _workspace(client)
    assert me["id"] == target["id"] and me["role"] == "vendedor"
    assert client.get("/api/contacts").json()["total"] == 2
    assert {d["title"] for d in client.get("/api/crm/deals").json()["deals"]} == {"Venta Luis"}
    assert [x["id"] for x in client.get("/api/lists").json()] == [luis_list["id"]]
    proposals = client.get("/api/proposals").json()
    rows = proposals["items"] if isinstance(proposals, dict) else proposals
    assert [p["number"] for p in rows] == [2]  # sigue la numeración de la empresa

    # Ana (administradora) ve el trabajo de todo el equipo, cada cosa con su dueño.
    _login(client, "ana@americadigital.co", panel=False)
    assert {d["title"] for d in client.get("/api/crm/deals").json()["deals"]} == {
        "Venta Ana", "Venta Luis"}  # fmt: skip
    assert client.get("/api/contacts").json()["total"] == 3
    team = {m["email"]: m["role"] for m in client.get("/api/team").json()["members"]}
    assert team == {"ana@americadigital.co": "admin", "luis@americadigital.co": "vendedor"}


def test_signed_proposal_keeps_its_fingerprint_after_a_merge() -> None:
    """La huella usa el número y el emisor con que se firmó, aunque la cuenta cambie de empresa."""
    import uuid
    from types import SimpleNamespace
    from typing import Any, cast

    from orkesta.proposals.service import document_hash

    company = {"name": "Cuenta de Luis"}
    session = cast(Any, SimpleNamespace(get=lambda _model, _id: SimpleNamespace(**company)))
    proposal = cast(Any, SimpleNamespace(
        tenant_id=uuid.uuid4(), number=1, title="Licencias", currency="USD", items=[],
        sections=[], tax_rate=19, valid_until=None, signature={}, pricing_mode="total",
    ))  # fmt: skip
    signed = document_hash(session, proposal)
    # La unión renumera y cambia el emisor, pero guarda lo que tenía al firmarse.
    proposal.number, company["name"] = 7, "America Digital"
    assert document_hash(session, proposal) != signed
    proposal.signature = {"doc_hash": signed, "frozen": {"number": 1, "issuer": "Cuenta de Luis"}}
    assert document_hash(session, proposal) == signed


def test_accepting_an_invitation_brings_your_own_workspace(client: TestClient) -> None:
    """Caso real: cada persona creó su cuenta; al aceptar la invitación de su empresa se lleva todo."""
    register(client, "jennifer@americadigital.co", company="Jenn Prospección")
    own = _workspace(client)
    client.post("/api/contacts", json=SOFIA)
    client.post("/api/crm/deals", json={"title": "Venta Jenn"})
    client.post("/api/lists", json={"name": "Mis decisores"})
    client.post("/api/auth/logout")

    register(client, "gerente@americadigital.co", company="America Digital")
    target = _workspace(client)
    link = client.post("/api/team/invitations", json={
        "email": "jennifer@americadigital.co", "role": "vendedor"}).json()["link"]  # fmt: skip
    client.post("/api/auth/logout")

    _login(client, "jennifer@americadigital.co", panel=False)
    token = link.rsplit("/", 1)[1]
    accepted = client.post(f"/api/invitations/{token}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    me = _workspace(client)
    assert me["id"] == target["id"] and me["role"] == "vendedor"
    assert client.get("/api/contacts").json()["total"] == 1
    assert {d["title"] for d in client.get("/api/crm/deals").json()["deals"]} == {"Venta Jenn"}
    assert [x["name"] for x in client.get("/api/lists").json()] == ["Mis decisores"]
    assert own["id"] not in {w["id"] for w in client.get("/api/auth/session").json()["workspaces"]}


def test_a_shared_workspace_is_not_moved_on_accept(
    client: TestClient, session_factory: Any
) -> None:
    register(client, "jennifer@americadigital.co", company="Jenn Prospección")
    client.post(
        "/api/team/invitations", json={"email": "socio@americadigital.co", "role": "vendedor"}
    )
    invite = client.post(
        "/api/team/invitations", json={"email": "socio@americadigital.co", "role": "vendedor"}
    ).json()["link"]
    partner = TestClient(client.app)
    joined = partner.post(f"/api/invitations/{invite.rsplit('/', 1)[1]}/accept",
                 json={"full_name": "Socio", "password": "ClaveSegura2026"})  # fmt: skip
    assert joined.status_code == 200, joined.text
    client.post("/api/auth/logout")
    register(client, "gerente@americadigital.co", company="America Digital")
    link = client.post("/api/team/invitations", json={
        "email": "jennifer@americadigital.co", "role": "vendedor"}).json()["link"]  # fmt: skip
    client.post("/api/auth/logout")
    _login(client, "jennifer@americadigital.co", panel=False)
    refused = client.post(f"/api/invitations/{link.rsplit('/', 1)[1]}/accept", json={})
    assert refused.status_code == 409 and "más personas" in refused.json()["detail"]
