"""Reasignar cuentas: pasar contactos y oportunidades a otra persona del equipo."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register
from tests.test_supervision import _contact, _deals, _join, _members, _pro


def test_reassign_contact_with_its_deal_and_tasks(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    jefa = _join(client, "jefa@lacteos.co", "supervisor", "Jefa Comercial")
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    sara = _join(client, "sara@lacteos.co", "vendedor", "Sara")
    people = _members(client)
    client.put(f"/api/team/members/{people['jefa@lacteos.co']['id']}/assignments",
               json={"member_ids": [people["luis@lacteos.co"]["user_id"]]})  # fmt: skip
    sara_id = people["sara@lacteos.co"]["user_id"]

    contact = _contact(luis, "sofia@retaildemo.cl")
    deal = luis.post("/api/crm/deals", json={"title": "Venta Sofía", "contact_id": contact["id"]})
    assert deal.status_code == 201, deal.text
    luis.post("/api/crm/activities", json={"type": "task", "body": "Llamar", "contact_id": contact["id"],
                                          "due_at": "2026-12-01T15:00:00Z"})  # fmt: skip
    names = {p["name"] for p in luis.get("/api/crm/reassign/people").json()}
    assert {"Luis", "Sara", "Jefa Comercial"} <= names

    # Sara no puede quitarle cuentas a Luis.
    refused = sara.post("/api/crm/reassign", json={"kind": "contact", "ids": [contact["id"]],
                                                   "to_user_id": sara_id})  # fmt: skip
    assert refused.status_code == 404  # ni siquiera la ve

    # Luis pasa su contacto a Sara: se van también la oportunidad y la tarea.
    moved = luis.post("/api/crm/reassign", json={
        "kind": "contact", "ids": [contact["id"]], "to_user_id": sara_id,
        "reason": "Cubre la cuenta de retail"})  # fmt: skip
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == {"contacts": 1, "deals": 1, "tasks": 1}
    assert sara.get("/api/contacts").json()["total"] == 1
    assert luis.get("/api/contacts").json()["total"] == 0
    assert _deals(sara) == {"Venta Sofía"} and _deals(luis) == set()
    assert any("te asignó" in n["title"] for n in sara.get("/api/notifications").json()["items"])

    # La jefatura reasigna cuentas de su equipo (Luis), no las de Sara (fuera de su equipo).
    mine = luis.post("/api/crm/deals", json={"title": "Venta Luis"}).json()
    back = jefa.post("/api/crm/reassign", json={"kind": "deal", "ids": [mine["id"]],
                                                "to_user_id": people["jefa@lacteos.co"]["user_id"]})  # fmt: skip
    assert back.status_code == 200, back.text
    sara_deal = next(
        d for d in client.get("/api/crm/deals").json()["deals"] if d["title"] == "Venta Sofía"
    )
    assert jefa.post("/api/crm/reassign", json={"kind": "deal", "ids": [sara_deal["id"]],
                                                "to_user_id": people["luis@lacteos.co"]["user_id"]}
                     ).status_code == 404  # fmt: skip
    # La administración reasigna cualquiera.
    assert client.post("/api/crm/reassign", json={"kind": "deal", "ids": [sara_deal["id"]],
                                                  "to_user_id": people["luis@lacteos.co"]["user_id"]}
                       ).status_code == 200  # fmt: skip
    assert "Venta Sofía" in _deals(luis)
