"""Calendario: Google Calendar de cada persona en ORKESTA, en ambos sentidos y privado."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.calendar import service as cal
from orkesta.research import fetch
from tests.conftest import register
from tests.test_campaigns import FakeGoogle, _connect, _contacts, google  # noqa: F401

SCOPES = ("openid email https://www.googleapis.com/auth/gmail.send "
          "https://www.googleapis.com/auth/gmail.readonly "
          "https://www.googleapis.com/auth/calendar.events")  # fmt: skip


class FakeCalendar:
    """Google Calendar de mentira: guarda los eventos y entrega los cambios por token."""

    def __init__(self, gmail: FakeGoogle) -> None:
        self.gmail = gmail
        self.events: dict[str, dict[str, Any]] = {}
        self.changed: list[str] = []
        self.inserted: list[dict[str, Any]] = []
        self.n = 0

    def route(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token") and b"authorization_code" in (
            request.content
        ):
            token = jwt.encode({"email": "ana@lacteos.co", "email_verified": True,
                                "name": "Ana López"}, "x" * 32, algorithm="HS256")  # fmt: skip
            return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1",
                                             "expires_in": 3600, "scope": SCOPES,
                                             "id_token": token})  # fmt: skip
        if "/calendar/v3/calendars/primary/events" not in url:
            return self.gmail.route(request)
        gid = url.split("/events/")[1].split("?")[0] if "/events/" in url else ""
        if request.method == "GET":
            items = [self.events[i] for i in self.changed]
            self.changed = []
            return httpx.Response(200, json={"items": items, "nextSyncToken": f"s{self.n}"})
        if request.method == "POST":
            body = json.loads(request.content)
            self.n += 1
            event = {**body, "id": f"ev-{self.n}", "status": "confirmed",
                     "htmlLink": f"https://calendar.google.com/e/{self.n}",
                     "hangoutLink": "https://meet.google.com/abc" if body.get("conferenceData")
                     else None}  # fmt: skip
            self.events[event["id"]] = event
            self.inserted.append(body)
            return httpx.Response(200, json=event)
        if request.method == "PATCH":
            self.events[gid].update(json.loads(request.content))
            return httpx.Response(200, json=self.events[gid])
        if request.method == "DELETE":
            self.events.pop(gid, None)
            return httpx.Response(204)
        return httpx.Response(404)

    def from_google(self, gid: str, **event: Any) -> None:
        self.events[gid] = {"id": gid, "status": "confirmed", **event}
        self.changed.append(gid)


@pytest.fixture()
def calendar(google: FakeGoogle) -> FakeCalendar:  # noqa: F811
    fake = FakeCalendar(google)
    fetch.TRANSPORT = httpx.MockTransport(fake.route)
    return fake


def _at(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat()


def test_meeting_booked_by_a_lead_stops_the_campaign_and_creates_the_deal(
    client: TestClient, calendar: FakeCalendar, session_factory: sessionmaker[Session],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    _connect(client)
    status = client.get("/api/calendar/status").json()
    assert status["connected"] and status["email"] == "ana@lacteos.co"
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Bancos"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola", "body": "Mensaje de prueba largo", "delay_days": 0},
                  {"subject": "", "body": "Seguimiento de prueba", "delay_days": 3}],
        "audience": {"contact_ids": [ids[0]]},
    })  # fmt: skip
    client.post(f"/api/campaigns/{campaign['id']}/launch")

    # La lead agenda una reunión (por ejemplo, con la agenda de citas de Google).
    calendar.from_google("g-1", summary="Demo ORKESTA", start={"dateTime": _at(2)},
                         end={"dateTime": _at(2.05)}, htmlLink="https://calendar.google.com/x",
                         attendees=[{"email": "ana@lacteos.co", "self": True},
                                    {"email": "laura@bancoandino.co", "displayName": "Laura"}])  # fmt: skip
    assert cal.sync_all(session_factory) == 1
    events = client.get("/api/calendar/events").json()
    assert len(events) == 1 and events[0]["contact"]["full_name"] == "Laura Gómez"
    assert events[0]["campaign_id"] == campaign["id"] and events[0]["deal_id"]
    recipient = client.get(f"/api/campaigns/{campaign['id']}/recipients").json()["items"][0]
    assert recipient["status"] == "replied" and recipient["reply_category"] == "reunion"
    assert recipient["deal_id"] == events[0]["deal_id"]

    # La cancela en Google: desaparece de ORKESTA.
    calendar.from_google("g-1", status="cancelled")
    client.post("/api/calendar/sync")
    assert client.get("/api/calendar/events").json() == []


def test_meetings_created_in_orkesta_go_to_google_and_stay_private(
    client: TestClient, calendar: FakeCalendar, session_factory: sessionmaker[Session],
) -> None:  # fmt: skip
    from tests.test_supervision import _join, _pro

    register(client, "ana@lacteos.co")
    _pro(session_factory)
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    _connect(client)
    contact = client.post("/api/contacts", json={"first_name": "Mario", "last_name": "Paz",
                                                 "email": "mario@retail.co"}).json()  # fmt: skip
    made = client.post("/api/calendar/events", json={
        "title": "Reunión con Mario", "start_at": _at(1), "end_at": _at(1.04),
        "contact_id": contact["id"], "meet": True})  # fmt: skip
    assert made.status_code == 201, made.text
    event = made.json()
    assert calendar.inserted[0]["attendees"] == [{"email": "mario@retail.co",
                                                  "displayName": "Mario Paz"}]  # fmt: skip
    assert event["meet_url"] == "https://meet.google.com/abc" and event["source"] == "orkesta"
    timeline = client.get("/api/crm/timeline", params={"contacto": contact["id"]}).json()
    assert any(a["type"] == "meeting" for a in timeline)

    moved = client.patch(f"/api/calendar/events/{event['id']}", json={"title": "Demo con Mario"})
    assert moved.status_code == 200 and calendar.events["ev-1"]["summary"] == "Demo con Mario"

    # Privado: nadie más lo ve ni lo cambia, ni siquiera desde otra cuenta del equipo.
    assert luis.get("/api/calendar/events").json() == []
    assert luis.patch(f"/api/calendar/events/{event['id']}", json={"title": "x"}).status_code == 404
    assert luis.post("/api/calendar/events", json={"title": "x", "start_at": _at(1),
                                                   "end_at": _at(1.1)}).status_code == 409  # fmt: skip

    assert client.delete(f"/api/calendar/events/{event['id']}").status_code == 200
    assert "ev-1" not in calendar.events and client.get("/api/calendar/events").json() == []
