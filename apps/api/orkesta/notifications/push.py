"""Notificaciones push propias (estándar Web Push: VAPID, RFC 8292, y cifrado aes128gcm,
RFC 8291). Llegan al navegador o al celular aunque ORKESTA esté cerrado. Sin servicios de
terceros: se usan los servidores push que trae cada navegador."""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.platform.models import PushSubscription

logger = logging.getLogger(__name__)
TRANSPORT: httpx.BaseTransport | None = None  # pruebas
ENABLED = True
_keys: tuple[ec.EllipticCurvePrivateKey, str] | None = None
_LOCK = threading.Lock()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def vapid_keys(session: Session) -> tuple[ec.EllipticCurvePrivateKey, str]:
    """Llaves VAPID de la plataforma: se crean solas la primera vez y se guardan."""
    global _keys
    with _LOCK:
        if _keys is not None:
            return _keys
        from orkesta.admin.service import _get, _put
        from orkesta.campaigns.gmail import decrypt, encrypt

        stored = _get(session, "vapid", {})
        if stored.get("private"):
            private = serialization.load_pem_private_key(decrypt(stored["private"]).encode(), None)
            assert isinstance(private, ec.EllipticCurvePrivateKey)  # noqa: S101
        else:
            private = ec.generate_private_key(ec.SECP256R1())
            pem = private.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()  # fmt: skip
            _put(session, "vapid", {"private": encrypt(pem)})
        _keys = (private, b64url(_public_bytes(private.public_key())))
        return _keys


def encrypt_payload(payload: bytes, p256dh: str, auth: str) -> bytes:
    """Cuerpo cifrado aes128gcm para una suscripción (RFC 8291)."""
    ua_public = unb64url(p256dh)
    auth_secret = unb64url(auth)
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_public = _public_bytes(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), ua_public))  # fmt: skip
    ikm = HKDF(hashes.SHA256(), 32, auth_secret,
               b"WebPush: info\x00" + ua_public + server_public).derive(shared)  # fmt: skip
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return (
        salt + (4096).to_bytes(4, "big") + bytes([len(server_public)]) + server_public + ciphertext
    )


def _vapid_header(private: ec.EllipticCurvePrivateKey, public: str, endpoint: str) -> str:
    from orkesta.core.config import get_settings

    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600,
              "sub": get_settings().public_base_url or "mailto:soporte@orkesta.app"}  # fmt: skip
    token = jwt.encode(claims, private, algorithm="ES256")
    return f"vapid t={token}, k={public}"


def send_to(session: Session, sub: PushSubscription, message: dict[str, Any]) -> int:
    private, public = vapid_keys(session)
    body = encrypt_payload(json.dumps(message, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
    with httpx.Client(timeout=10.0, transport=TRANSPORT) as client:
        response = client.post(sub.endpoint, content=body, headers={
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
            "TTL": "86400", "Urgency": "normal",
            "Authorization": _vapid_header(private, public, sub.endpoint),
        })  # fmt: skip
    if response.status_code in (404, 410):
        session.delete(sub)  # el navegador anuló la suscripción
    return response.status_code


def deliver(session: Session, user_id: uuid.UUID, message: dict[str, Any]) -> int:
    subs = session.scalars(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    ).all()
    ok = 0
    for sub in subs:
        try:
            ok += send_to(session, sub, message) < 300
        except (httpx.HTTPError, ValueError):
            logger.info("push_failed", extra={"endpoint": sub.endpoint[:60]})
    return ok


# --- Cola en segundo plano: el aviso no demora la acción que lo generó ----------------------

_QUEUE: queue.Queue[tuple[Any, uuid.UUID, dict[str, Any]]] = queue.Queue(maxsize=2000)
_worker: threading.Thread | None = None


def _run() -> None:
    while True:
        bind, user_id, message = _QUEUE.get()
        try:
            with Session(bind=bind) as session, session.begin():
                deliver(session, user_id, message)
        except Exception:
            logger.exception("push_worker")


def enqueue(session: Session, user_id: uuid.UUID, message: dict[str, Any]) -> None:
    global _worker
    if not ENABLED:
        return
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run, daemon=True, name="orkesta-push")
        _worker.start()
    try:
        _QUEUE.put_nowait((session.get_bind(), user_id, message))
    except queue.Full:
        logger.warning("push_queue_full")
