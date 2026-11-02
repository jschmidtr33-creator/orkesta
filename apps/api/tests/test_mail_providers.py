"""Campañas desde Microsoft 365 / Outlook (Graph) y desde correo corporativo (SMTP + IMAP).
Servidores simulados: nada sale a internet."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import timedelta
from email import message_from_bytes
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns import service as svc
from orkesta.research import fetch
from tests.conftest import register
from tests.test_campaigns import _contacts, _flush, _rewind

STEPS = [{"subject": "Hola {{nombre}}", "body": "Mensaje de prueba para {{empresa}}", "delay_days": 0},
         {"subject": "", "body": "Seguimiento de prueba", "delay_days": 2}]  # fmt: skip


class FakeGraph:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.conversations: dict[str, list[dict[str, Any]]] = {}

    def route(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/token"):
            token = jwt.encode({"preferred_username": "ana@lacteos.co", "name": "Ana López"},
                               "x" * 32, algorithm="HS256")  # fmt: skip
            return httpx.Response(200, json={
                "access_token": "ms-1", "refresh_token": "ms-r", "expires_in": 3600,
                "scope": "openid email Mail.Send Mail.ReadWrite", "id_token": token})  # fmt: skip
        if url.endswith("/me/messages") and request.method == "POST":
            raw = base64.b64decode(request.content)
            n = len(self.sent) + 1
            msg = message_from_bytes(raw)
            conv = f"conv-{n}" if not msg.get("In-Reply-To") else "conv-1"
            self.sent.append({"msg": msg, "id": f"ms-{n}"})
            self.conversations.setdefault(conv, [])
            return httpx.Response(201, json={"id": f"ms-{n}", "conversationId": conv,
                                             "internetMessageId": f"<ms-{n}@outlook.com>"})  # fmt: skip
        if "/me/messages/" in url and url.endswith("/send"):
            return httpx.Response(202)
        if "/me/messages" in url and request.method == "GET":
            flt = parse_qs(urlsplit(url).query)["$filter"][0]
            conv = flt.split("'")[1]
            return httpx.Response(200, json={"value": self.conversations.get(conv, [])})
        return httpx.Response(404)


@pytest.fixture()
def graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGraph]:
    from orkesta.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "microsoft_client_id", "cliente")
    monkeypatch.setattr(s, "microsoft_client_secret", "secreto")
    monkeypatch.setattr(s, "public_base_url", "https://orkesta.test")
    monkeypatch.setattr(svc, "_in_window", lambda _s: True)
    monkeypatch.setattr(svc, "_gap", lambda *a: timedelta(0))
    fake = FakeGraph()
    fetch.TRANSPORT = httpx.MockTransport(fake.route)
    yield fake
    fetch.TRANSPORT = None


def test_microsoft_365_campaign(client: TestClient, graph: FakeGraph,
                                session_factory: sessionmaker[Session],
                                database: tuple[str, str]) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    start = client.get("/api/mail/microsoft/start", follow_redirects=False)
    assert "login.microsoftonline.com" in start.headers["location"]
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    done = client.get("/api/mail/microsoft/callback", params={"code": "c", "state": state},
                      follow_redirects=False)  # fmt: skip
    assert done.headers["location"].endswith("microsoft=conectado")
    boxes = client.get("/api/mailboxes").json()
    assert boxes["providers"]["microsoft"] and boxes["items"][0]["provider"] == "microsoft"
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Outlook"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={"steps": STEPS,
                 "audience": {"contact_ids": [ids[0]]}})  # fmt: skip
    assert client.post(f"/api/campaigns/{campaign['id']}/launch").status_code == 200
    assert _flush(session_factory) == 1
    assert graph.sent[0]["msg"]["Subject"] == "Hola Laura"
    graph.conversations["conv-1"].append({"from": {"emailAddress": {"address": "laura@bancoandino.co",
                                          "name": "Laura"}}, "subject": "RE: Hola Laura",
                                          "bodyPreview": "Me interesa, agendemos una llamada."})  # fmt: skip
    _rewind(database)
    _flush(session_factory)
    recipients = client.get(f"/api/campaigns/{campaign['id']}/recipients").json()["items"]
    assert recipients[0]["status"] == "replied" and recipients[0]["reply_category"] == "reunion"


class FakeSMTP:
    sent: ClassVar[list[bytes]] = []
    fail = False

    def __init__(self, host: str, port: int, **kw: Any) -> None:
        if FakeSMTP.fail:
            raise OSError("[Errno 101] Network is unreachable")

    def starttls(self, **kw: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        assert password == "clave-app"

    def sendmail(self, sender: str, to: list[str], raw: bytes) -> dict[str, Any]:
        FakeSMTP.sent.append(raw)
        return {}

    def quit(self) -> None:
        pass


class FakeIMAP:
    inbox: ClassVar[list[bytes]] = []

    def __init__(self, host: str, port: int, **kw: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def select(self, box: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def search(self, charset: Any, criteria: str) -> tuple[str, list[bytes]]:
        ref = criteria.split('"')[1]
        hits = [str(i + 1).encode() for i, m in enumerate(FakeIMAP.inbox) if ref.encode() in m]
        return "OK", [b" ".join(hits)]

    def fetch(self, num: str, parts: str) -> tuple[str, list[Any]]:
        return "OK", [(b"1 (BODY[] {1}", FakeIMAP.inbox[int(num) - 1])]

    def logout(self) -> None:
        pass


def test_smtp_campaign(client: TestClient, session_factory: sessionmaker[Session],
                       monkeypatch: pytest.MonkeyPatch, database: tuple[str, str]) -> None:  # fmt: skip
    import imaplib
    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(svc, "_in_window", lambda _s: True)
    monkeypatch.setattr(svc, "_gap", lambda *a: timedelta(0))
    FakeSMTP.sent, FakeIMAP.inbox, FakeSMTP.fail = [], [], True
    register(client, "ana@lacteos.co")
    body = {"email": "ana@lacteos.co", "password": "clave-app", "smtp_host": "smtp.lacteos.co",
            "smtp_port": 587, "imap_host": "imap.lacteos.co"}  # fmt: skip
    blocked = client.post("/api/mailboxes/smtp", json=body)
    assert blocked.status_code == 400 and "puertos SMTP" in blocked.json()["detail"]
    FakeSMTP.fail = False
    box = client.post("/api/mailboxes/smtp", json=body)
    assert box.status_code == 201 and box.json()["provider"] == "smtp"
    with __import__("psycopg").connect(database[0], autocommit=True) as conn:
        stored = conn.execute("SELECT refresh_token FROM orkesta.mailboxes").fetchone()[0]
    assert "clave-app" not in stored  # cifrada
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "SMTP"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={"steps": STEPS,
                 "audience": {"contact_ids": [ids[0]]}})  # fmt: skip
    assert client.post(f"/api/campaigns/{campaign['id']}/launch").status_code == 200
    assert _flush(session_factory) == 1
    first = message_from_bytes(FakeSMTP.sent[0])
    rfc = first["Message-ID"]
    FakeIMAP.inbox.append(
        f"From: Laura <laura@bancoandino.co>\r\nSubject: Re: Hola\r\nIn-Reply-To: {rfc}\r\n"
        f"References: {rfc}\r\n\r\nNo me interesa, gracias.".encode()
    )
    _rewind(database)
    _flush(session_factory)
    recipients = client.get(f"/api/campaigns/{campaign['id']}/recipients").json()["items"]
    assert (
        recipients[0]["status"] == "replied" and recipients[0]["reply_category"] == "no_interesado"
    )
