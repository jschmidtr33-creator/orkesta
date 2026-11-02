"""Doble verificación (TOTP + códigos de respaldo), resumen diario y notificaciones push."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from typing import Any

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.platform import mfa
from tests.conftest import register

PASSWORD = "ClaveSegura2026"


def _code(secret: str, offset: int = 0) -> str:
    return mfa.code_at(secret, int(time.time() // 30) + offset)


def test_two_factor_login_recovery_and_admin(client: TestClient, database: tuple[str, str]) -> None:
    register(client, "ana@lacteos.co")
    setup = client.post("/api/profile/mfa/setup").json()
    assert setup["qr"].startswith("data:image/svg+xml") and "otpauth://totp/" in setup["uri"]
    secret = setup["secret"]
    assert client.post("/api/profile/mfa/enable", json={"code": "000000"}).status_code == 400
    enabled = client.post("/api/profile/mfa/enable", json={"code": _code(secret)}).json()
    codes = enabled["recovery_codes"]
    assert len(codes) == 8 and client.get("/api/profile").json()["mfa_enabled"]

    client.post("/api/auth/logout")
    first = client.post("/api/auth/login", json={"email": "ana@lacteos.co", "password": PASSWORD})
    body = first.json()
    assert body["mfa_required"] and "user" not in body
    assert client.get("/api/auth/me").status_code == 401  # sin sesión todavía
    bad = client.post("/api/auth/mfa", json={"token": body["mfa_token"], "code": "123456"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/mfa", json={"token": body["mfa_token"], "code": _code(secret, 1)})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "ana@lacteos.co"

    # Código de respaldo: sirve una sola vez.
    client.post("/api/auth/logout")
    token = client.post("/api/auth/login", json={"email": "ana@lacteos.co",
                                                 "password": PASSWORD}).json()["mfa_token"]  # fmt: skip
    assert client.post("/api/auth/mfa", json={"token": token, "code": codes[0]}).status_code == 200
    client.post("/api/auth/logout")
    token = client.post("/api/auth/login", json={"email": "ana@lacteos.co",
                                                 "password": PASSWORD}).json()["mfa_token"]  # fmt: skip
    assert client.post("/api/auth/mfa", json={"token": token, "code": codes[0]}).status_code == 401
    assert client.post("/api/auth/mfa", json={"token": token, "code": codes[1]}).status_code == 200
    assert client.get("/api/profile").json()["recovery_left"] == 6

    # El panel de administración también pide el código.
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.users SET platform_role = 'owner'")
    no_code = client.post(
        "/api/admin/unlock", json={"email": "ana@lacteos.co", "password": PASSWORD}
    )
    assert no_code.status_code == 401 and "código" in no_code.json()["detail"]
    unlocked = client.post("/api/admin/unlock", json={"email": "ana@lacteos.co",
                                                      "password": PASSWORD, "code": codes[2]})  # fmt: skip
    assert unlocked.status_code == 200
    off = client.post("/api/profile/mfa/disable", json={"code": codes[3]})
    assert off.status_code == 200 and off.json()["mfa_enabled"] is False


def test_daily_digest_from_own_mailbox(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import imaplib
    import smtplib

    from tests.test_mail_providers import FakeIMAP, FakeSMTP

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    FakeSMTP.sent, FakeSMTP.fail = [], False
    register(client, "ana@lacteos.co")
    assert client.post("/api/profile/digest/test").status_code == 400  # sin buzón
    client.post("/api/mailboxes/smtp", json={"email": "ana@lacteos.co", "password": "clave-app",
                                             "smtp_host": "smtp.lacteos.co"})  # fmt: skip
    contact = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
                                                 "company": "Banco Andino"}).json()  # fmt: skip
    client.post("/api/crm/activities", json={
        "type": "task", "contact_id": contact["id"], "body": "Llamar a Laura por la propuesta",
        "due_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat()})  # fmt: skip
    sent = client.post("/api/profile/digest/test")
    assert sent.status_code == 200 and sent.json()["to"] == "ana@lacteos.co"
    msg = message_from_bytes(FakeSMTP.sent[-1], policy=policy.default)
    assert msg["Subject"].startswith("Tu día en ORKESTA") and msg["Auto-Submitted"]
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_content()
    assert "Llamar a Laura por la propuesta" in html and "Tareas vencidas" in html
    assert (
        client.put("/api/profile/digest", json={"enabled": False}).json()["digest_enabled"] is False
    )


def _decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    salt, keylen = body[:16], body[20]
    server_public = body[21 : 21 + keylen]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)  # fmt: skip
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), server_public))  # fmt: skip
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_public + server_public).derive(
        shared
    )
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, body[21 + keylen :], None)
    return plain.rstrip(b"\x00")[:-1]


def test_web_push(client: TestClient, session_factory: sessionmaker[Session]) -> None:
    from orkesta.notifications import push

    register(client, "ana@lacteos.co")
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)  # fmt: skip
    auth = b"0123456789abcdef"
    key = client.get("/api/push/key").json()["key"]
    endpoint = "https://push.example.com/send/abc123"
    assert client.post("/api/push/subscribe", json={"endpoint": endpoint, "p256dh": push.b64url(ua_pub),
                       "auth": push.b64url(auth)}).status_code == 201  # fmt: skip
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    push.TRANSPORT = httpx.MockTransport(route)
    try:
        assert client.post("/api/push/test").json()["sent"] == 1
    finally:
        push.TRANSPORT = None
    request = seen[0]
    assert request.headers["content-encoding"] == "aes128gcm"
    message = json.loads(_decrypt(request.content, ua, auth))
    assert message["title"] == "ORKESTA" and message["link"] == "/app/"
    token = request.headers["authorization"].split("t=")[1].split(",")[0]
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64url(key))
    claims: dict[str, Any] = jwt.decode(token, public, algorithms=["ES256"],
                                        audience="https://push.example.com")  # fmt: skip
    assert claims["exp"] > time.time()

    # Si el navegador anuló la suscripción, se borra sola.
    push.TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(410))
    try:
        client.post("/api/push/test")
        assert client.post("/api/push/test").json()["sent"] == 0
    finally:
        push.TRANSPORT = None
