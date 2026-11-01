"""Notificaciones: recordatorio de tareas vencidas (campana + correo) y lectura."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.notifications.service import due_task_reminders
from tests.conftest import register
from tests.test_campaigns import FakeGoogle, _connect, google  # noqa: F401


def test_due_task_reminder_notifies_and_emails_once(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],  # noqa: F811
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    _connect(client)
    contact = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
                                                 "company": "Banco Andino"}).json()  # fmt: skip
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    for body, due in (("Llamar a Laura", past), ("Enviar contrato", future)):
        r = client.post("/api/crm/activities", json={"type": "task", "body": body,
                                                     "contact_id": contact["id"], "due_at": due})  # fmt: skip
        assert r.status_code in (200, 201), r.text

    assert due_task_reminders(session_factory) == 1
    assert due_task_reminders(session_factory) == 0  # no se repite
    data = client.get("/api/notifications").json()
    assert data["unread"] == 1
    n = data["items"][0]
    assert n["kind"] == "task_due" and "Llamar a Laura" in n["title"] and "Laura Gómez" in n["body"]
    mail = google.sent[-1]["msg"]
    assert mail["To"] == "ana@lacteos.co"
    assert "Llamar a Laura" in str(make_header(decode_header(mail["Subject"])))

    client.post("/api/notifications/read", json={"all": True})
    assert client.get("/api/notifications").json()["unread"] == 0
    register(client, "otro@otra.co", company="Otra")
    assert client.get("/api/notifications").json()["items"] == []
