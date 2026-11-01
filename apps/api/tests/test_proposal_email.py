"""Envío de propuestas por correo desde el Gmail conectado."""

from __future__ import annotations

from email.header import decode_header, make_header

from fastapi.testclient import TestClient

from tests.conftest import register
from tests.test_campaigns import FakeGoogle, _connect, google  # noqa: F401


def test_send_proposal_by_email(client: TestClient, google: FakeGoogle) -> None:  # noqa: F811
    register(client, "ana@lacteos.co")
    mailbox = _connect(client)
    contact = client.post("/api/contacts", json={
        "first_name": "Laura", "last_name": "Gómez", "company": "Banco Andino",
        "job_title": "CIO", "email": "laura@bancoandino.co"}).json()  # fmt: skip
    deal = client.post("/api/crm/deals", json={"contact_id": contact["id"], "value": 5000}).json()
    p = client.post("/api/proposals", json={"deal_id": deal["id"]}).json()

    bad = client.post(f"/api/proposals/{p['id']}/email", json={
        "mailbox_id": mailbox["id"], "to": "no-es-correo", "subject": "x", "body": "y"})  # fmt: skip
    assert bad.status_code == 400

    r = client.post(f"/api/proposals/{p['id']}/email", json={
        "mailbox_id": mailbox["id"], "to": "laura@bancoandino.co", "cc": "jefe@lacteos.co",
        "subject": "{{nombre}}, tu propuesta de {{mi_empresa}}",
        "body": "<p>Hola {{nombre}} ({{cargo}}), te comparto la propuesta para {{empresa}}.</p>",
    })  # fmt: skip
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "sent" and data["email"]["to"] == ["laura@bancoandino.co"]
    msg = google.sent[-1]["msg"]
    subject = str(make_header(decode_header(msg["Subject"])))
    assert subject == "Laura, tu propuesta de Lácteos Inventados"
    assert msg["To"] == "laura@bancoandino.co" and msg["Cc"] == "jefe@lacteos.co"
    html = (
        next(x for x in msg.walk() if x.get_content_type() == "text/html")
        .get_payload(decode=True)
        .decode()
    )
    assert "Hola Laura (CIO)" in html and f"/app/p/{p['public_id']}" in html
    assert "Ver la propuesta" in html
    timeline = client.get("/api/crm/timeline", params={"contacto": contact["id"]}).json()
    assert any("Envió por correo la propuesta" in a["body"] for a in timeline)
    assert client.get(f"/api/crm/deals/{deal['id']}").json()["stage"] == "Propuesta enviada"
