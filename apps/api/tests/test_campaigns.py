"""Campañas con Gmail: conexión OAuth, lanzamiento, ritmo de envío, hilo, respuestas, rebotes,
aperturas, clics y bajas. Google y Gmail se simulan con un transporte HTTP falso."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import timedelta
from email import message_from_bytes
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns import service as svc
from orkesta.research import fetch
from tests.conftest import register


class FakeGoogle:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.threads: dict[str, list[dict[str, Any]]] = {}

    def route(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["refresh_token"]:
                return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
            id_token = jwt.encode(
                {"email": "ana@lacteos.co", "email_verified": True, "name": "Ana López"},
                "x" * 32, algorithm="HS256",
            )  # fmt: skip
            return httpx.Response(200, json={
                "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                "scope": "openid email https://www.googleapis.com/auth/gmail.send "
                         "https://www.googleapis.com/auth/gmail.readonly",
                "id_token": id_token,
            })  # fmt: skip
        if url.endswith("/messages/send"):
            body = json.loads(request.content)
            raw = message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
            n = len(self.sent) + 1
            thread = body.get("threadId") or f"th-{n}"
            self.sent.append({"msg": raw, "thread": body.get("threadId"), "id": f"m-{n}"})
            self.threads.setdefault(thread, []).append(
                {
                    "labelIds": ["SENT"],
                    "payload": {"headers": [{"name": "From", "value": "ana@lacteos.co"}]},
                }
            )
            return httpx.Response(200, json={"id": f"m-{n}", "threadId": thread})
        if "/messages/m-" in url:
            gid = url.split("/messages/")[1].split("?")[0]
            return httpx.Response(200, json={"payload": {"headers": [
                {"name": "Message-ID", "value": f"<{gid}@mail.gmail.com>"}]}})  # fmt: skip
        if "/threads/" in url:
            tid = url.split("/threads/")[1].split("?")[0]
            return httpx.Response(200, json={"messages": self.threads.get(tid, [])})
        return httpx.Response(404)

    def reply(self, thread: str, sender: str, subject: str = "Re: hola") -> None:
        self.threads[thread].append({"payload": {"headers": [
            {"name": "From", "value": sender}, {"name": "Subject", "value": subject}]}})  # fmt: skip


@pytest.fixture()
def google(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGoogle]:
    from orkesta.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cliente")
    monkeypatch.setattr(settings, "google_client_secret", "secreto")
    monkeypatch.setattr(settings, "public_base_url", "https://orkesta.test")
    monkeypatch.setattr(svc, "_in_window", lambda _settings: True)
    fake = FakeGoogle()
    fetch.TRANSPORT = httpx.MockTransport(fake.route)
    yield fake
    fetch.TRANSPORT = None


def _connect(client: TestClient) -> dict[str, Any]:
    start = client.get("/api/mail/google/start", follow_redirects=False)
    assert start.status_code == 303 and "accounts.google.com" in start.headers["location"]
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    # Sin la cookie del navegador que inició el flujo, el estado no sirve.
    bad = TestClient(client.app).get(
        "/api/mail/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert bad.headers["location"].endswith("gmail=error")
    done = client.get(
        "/api/mail/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert done.headers["location"].endswith("gmail=conectado"), done.headers["location"]
    boxes = client.get("/api/mailboxes").json()
    assert boxes["enabled"] and len(boxes["items"]) == 1
    return dict(boxes["items"][0])


def _contacts(client: TestClient) -> list[str]:
    ids = []
    for first, last, email in [
        ("Laura", "Gómez", "laura@bancoandino.co"),
        ("Pedro", "Ruiz", "pedro@bancoandino.co"),
        ("Sofía", "Mena", "sofia@bancoandino.co"),
    ]:
        r = client.post("/api/contacts", json={"first_name": first, "last_name": last,
                        "company": "Banco Andino", "email": email})  # fmt: skip
        ids.append(r.json()["id"])
    client.post("/api/contacts", json={"first_name": "Sin", "last_name": "Correo",
                "company": "Banco Andino"})  # fmt: skip
    return ids


def _flush(factory: sessionmaker[Session], times: int = 1) -> int:
    return sum(svc.tick(factory) for _ in range(times))


def _rewind(database: tuple[str, str]) -> None:
    """Simula que pasó el tiempo: la pausa del buzón y la última revisión del hilo."""
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.mailboxes SET next_send_after = NULL")
        conn.execute("UPDATE orkesta.campaign_recipients SET checked_at = NULL")


def test_full_campaign_flow(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch, database: tuple[str, str],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    mailbox = _connect(client)
    client.patch(f"/api/mailboxes/{mailbox['id']}", json={"signature": "Ana · Lácteos"})
    _contacts(client)
    client.post("/api/suppressions", json={"email": "sofia@bancoandino.co"})

    campaign = client.post("/api/campaigns", json={"name": "Bancos Q4"}).json()
    assert campaign["mailbox"]["email"] == "ana@lacteos.co" and campaign["steps_count"] == 3
    preview_audience = client.post(
        "/api/campaigns/audience", json={"audience": {"filters": {"q": "banco andino"}}}
    ).json()
    assert preview_audience["eligible"] == 2
    assert preview_audience["no_email"] == 1 and preview_audience["suppressed"] == 1

    bad = client.patch(f"/api/campaigns/{campaign['id']}", json={"steps": [
        {"subject": "Hola {{nombr}}", "body": "Un mensaje suficientemente largo", "delay_days": 0}
    ]})  # fmt: skip
    assert bad.status_code == 200
    assert "nombr" in client.post(f"/api/campaigns/{campaign['id']}/launch").json()["detail"]

    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [
            {"subject": "{{nombre}}, idea para {{empresa}}", "delay_days": 0,
             "body": "Hola {{nombre}}, mira https://lacteos.co/demo y me cuentas."},
            {"subject": "", "delay_days": 0, "body": "¿Pudiste verlo, {{nombre}}?"},
        ],
        "audience": {"filters": {"q": "banco andino"}},
    })  # fmt: skip
    preview = client.post(f"/api/campaigns/{campaign['id']}/preview", json={"step": 1}).json()
    assert preview["subject"].startswith("Re: ") and preview["threaded"]

    launched = client.post(f"/api/campaigns/{campaign['id']}/launch")
    assert launched.status_code == 200, launched.text
    assert launched.json()["stats"]["recipients"] == 2

    # Ritmo humano: un correo por buzón por vuelta, con pausa entre envíos.
    assert _flush(session_factory) == 1
    assert _flush(session_factory) == 0
    monkeypatch.setattr(svc, "_gap", lambda *a: timedelta(0))
    _rewind(database)
    assert _flush(session_factory, 3) == 3  # segundo destinatario + los dos seguimientos
    assert len(google.sent) == 4

    first = google.sent[0]["msg"]
    assert first["Subject"].endswith("idea para Banco Andino")
    assert first["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    html = (
        next(p for p in first.walk() if p.get_content_type() == "text/html")
        .get_payload(decode=True)
        .decode()
    )
    assert "https://orkesta.test/t/o/" in html and "https://orkesta.test/t/c/" in html
    text = (
        next(p for p in first.walk() if p.get_content_type() == "text/plain")
        .get_payload(decode=True)
        .decode()
    )
    assert "Ana · Lácteos" in text

    follow = next(s for s in google.sent if s["thread"])
    assert follow["msg"]["Subject"].startswith("Re: ")
    assert follow["msg"]["In-Reply-To"].endswith("@mail.gmail.com>")

    # Apertura, clic y estadística.
    monkeypatch.setattr(svc, "PREFETCH", timedelta(0))
    token = html.split("/t/o/")[1].split(".gif")[0]
    assert client.get(f"/t/o/{token}.gif").headers["content-type"] == "image/gif"
    link = html.split('href="')[1].split('"')[0].replace("&amp;", "&")
    path = urlsplit(link)
    r = client.get(f"{path.path}?{path.query}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://lacteos.co/demo"
    tampered = client.get(
        f"{path.path}?{path.query}".replace("demo", "malo"), follow_redirects=False
    )
    assert tampered.status_code == 404

    # Respuesta del cliente → se detecta en el hilo.
    thread = google.sent[0]["id"].replace("m-", "th-")
    google.reply(thread, "Laura <laura@bancoandino.co>")
    _rewind(database)
    _flush(session_factory)
    stats = client.get(f"/api/campaigns/{campaign['id']}").json()["stats"]
    assert stats["opened"] == 1 and stats["clicked"] == 1 and stats["replied"] == 1
    assert stats["emails_sent"] == 4
    recipients = client.get(f"/api/campaigns/{campaign['id']}/recipients").json()["items"]
    replied = next(r for r in recipients if r["status"] == "replied")
    timeline = client.get("/api/crm/timeline", params={"contacto": replied["contact_id"]}).json()
    assert any("Respondió a la campaña" in a["body"] for a in timeline)
    assert any(a["type"] == "task" for a in timeline)

    # Baja con un clic desde el correo.
    other = next(s for s in google.sent if s["id"] == "m-2")["msg"]
    unsub = other["List-Unsubscribe"].split("<")[1].split(">")[0]
    assert "baja" in client.post(urlsplit(unsub).path).text
    supp = {s["email"]: s["reason"] for s in client.get("/api/suppressions").json()}
    to = other["To"].split("<")[-1].rstrip(">")
    assert supp.get(to) == "unsubscribed"

    # Otra empresa no ve nada de esta.
    register(client, "otro@otra.co", company="Otra Empresa")
    assert client.get(f"/api/campaigns/{campaign['id']}").status_code == 404
    assert client.get("/api/mailboxes").json()["items"] == []


def test_bounce_suppresses_and_marks_invalid(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],
    database: tuple[str, str],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    _connect(client)
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Una"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola", "body": "Mensaje de prueba largo", "delay_days": 0},
                  {"subject": "", "body": "Seguimiento de prueba", "delay_days": 2}],
        "audience": {"contact_ids": [ids[0]]},
    })  # fmt: skip
    assert client.post(f"/api/campaigns/{campaign['id']}/launch").status_code == 200
    assert _flush(session_factory) == 1
    google.reply("th-1", "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                 "Delivery Status Notification (Failure)")  # fmt: skip
    _rewind(database)
    _flush(session_factory)
    contact = client.get(f"/api/contacts/{ids[0]}").json()
    assert contact["email_status"] == "invalido"
    stats = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert stats["stats"]["bounced"] == 1 and stats["status"] == "completed"
    # Una campaña nueva ya no le escribe.
    again = client.post("/api/campaigns/audience", json={"audience": {"contact_ids": [ids[0]]}})
    assert again.json()["eligible"] == 0


def test_pause_and_disabled_gmail(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    start = client.get("/api/mail/google/start", follow_redirects=False)
    assert start.headers["location"].endswith("gmail=pendiente")
    campaign = client.post("/api/campaigns", json={"name": "Sin buzón"}).json()
    r = client.post(f"/api/campaigns/{campaign['id']}/launch")
    assert r.status_code == 400 and "buzón" in r.json()["detail"]
    assert client.post(f"/api/campaigns/{campaign['id']}/pause").status_code == 409


def test_next_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    # Domingo 1 nov 2026, 9:54 en Bogotá → el lunes a las 8:00.
    monkeypatch.setattr(svc, "now", lambda: datetime(2026, 11, 1, 14, 54, tzinfo=UTC))
    nxt = svc.next_window(dict(svc.DEFAULT_SETTINGS))
    assert nxt is not None and nxt.isoweekday() == 1 and nxt.hour == 8
    assert svc.next_window({**svc.DEFAULT_SETTINGS, "days": [7]}) is None


def test_gmail_403_explained() -> None:
    from orkesta.campaigns import gmail

    def route(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "Gmail API has not "
                              "been used in project 1 before or it is disabled.",
                              "errors": [{"reason": "accessNotConfigured"}]}})  # fmt: skip

    fetch.TRANSPORT = httpx.MockTransport(route)
    try:
        with pytest.raises(gmail.GmailError, match="no está habilitada"):
            gmail.send("t", b"raw")
    finally:
        fetch.TRANSPORT = None


def test_rich_html_body_signature_and_images(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    mailbox = _connect(client)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    up = client.post("/api/email-assets", files={"file": ("banner.png", png, "image/png")})
    assert up.status_code == 201, up.text
    url = up.json()["url"]
    assert client.get(urlsplit(url).path).content == png
    bad = client.post("/api/email-assets", files={"file": ("x.png", b"<svg/>", "image/png")})
    assert bad.status_code == 400

    client.patch(f"/api/mailboxes/{mailbox['id']}", json={
        "signature": '<p><b>Jennifer Schmidt</b><br>Strategic Business Development</p>'
                     f'<img src="{url}" width="120"><script>alert(1)</script>'
    })  # fmt: skip
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Rica"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola {{nombre}}", "delay_days": 0,
                   "body": "<p>Hola <strong>{{nombre}}</strong>,</p><ul><li>Miami</li></ul>"
                           f'<p><img src="{url}"></p>'
                           '<p><a href="https://cal.com/jenn">Agendar llamada</a></p>'
                           '<p onclick="x()">Fin</p>'}],
        "audience": {"contact_ids": [ids[0]]},
    })  # fmt: skip
    stored = client.get(f"/api/campaigns/{campaign['id']}").json()["steps"][0]["body"]
    assert "onclick" not in stored and "<strong>" in stored
    preview = client.post(f"/api/campaigns/{campaign['id']}/preview", json={"step": 0}).json()
    assert "<strong>Laura</strong>" in preview["html"] and "script" not in preview["html"]
    assert "Agendar llamada (https://cal.com/jenn)" in preview["body"]

    assert client.post(f"/api/campaigns/{campaign['id']}/launch").status_code == 200
    assert _flush(session_factory) == 1
    msg = google.sent[0]["msg"]
    html = (
        next(p for p in msg.walk() if p.get_content_type() == "text/html")
        .get_payload(decode=True)
        .decode()
    )
    text = (
        next(p for p in msg.walk() if p.get_content_type() == "text/plain")
        .get_payload(decode=True)
        .decode()
    )
    assert "<li>Miami</li>" in html and url in html and "Strategic Business Development" in html
    assert "https://orkesta.test/t/c/" in html and "cal.com" not in html.split("t/c/")[0][-20:]
    assert "max-width:100%" in html and "alert(1)" not in html
    assert "• Miami" in text and "Jennifer Schmidt" in text


def test_gap_fits_daily_limit_in_window() -> None:
    settings = {"start_hour": 8, "end_hour": 17}
    fast = [svc._gap(settings, 1500).total_seconds() for _ in range(200)]
    assert min(fast) >= 12 and max(fast) <= 31  # ~1.500 correos en 9 horas
    slow = [svc._gap(settings, 80).total_seconds() for _ in range(200)]
    assert min(slow) >= 70  # buzones pequeños: ritmo humano


def test_estimated_email_that_bounces_teaches_and_proposes_next_format(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],
    database: tuple[str, str],
) -> None:  # fmt: skip
    from orkesta.research import memory

    register(client, "ana@lacteos.co")
    _connect(client)
    r = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
                                           "company": "Banco Andino",
                                           "email": "laura.gomez@bancoandino.co"})  # fmt: skip
    cid = r.json()["id"]
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.contacts SET email_status = 'estimado' WHERE id = %s", (cid,))
    campaign = client.post("/api/campaigns", json={"name": "Una"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola", "body": "Mensaje de prueba largo", "delay_days": 0}],
        "audience": {"contact_ids": [cid], "include_estimated": True},
    })  # fmt: skip
    assert client.post(f"/api/campaigns/{campaign['id']}/launch").status_code == 200
    assert _flush(session_factory) == 1
    google.reply("th-1", "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                 "Delivery Status Notification (Failure)")  # fmt: skip
    _rewind(database)
    _flush(session_factory)

    contact = client.get(f"/api/contacts/{cid}").json()
    # El formato nombre.apellido quedó descartado en ese dominio: se propone el siguiente.
    assert contact["email"] == "lgomez@bancoandino.co" and contact["email_status"] == "estimado"
    chain, bounced = memory.candidates(session_factory, "bancoandino.co", "first.last")
    assert "first.last" not in chain and chain[0] == "flast"
    assert memory.address_hash("laura.gomez@bancoandino.co") in bounced
    supp = {s["email"] for s in client.get("/api/suppressions").json()}
    assert "laura.gomez@bancoandino.co" in supp
    detail = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert detail["learning"]["reproposed"] == 1
    retry = client.post(f"/api/campaigns/{campaign['id']}/retry-bounced").json()
    assert retry["status"] == "draft" and retry["name"].endswith("reintento")
    assert client.get(f"/api/campaigns/{retry['id']}").json()["audience"]["contact_ids"] == [cid]
