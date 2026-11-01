"""Gmail por OAuth: conexión del buzón, envío y lectura de hilos (respuestas y rebotes).

Los tokens se guardan cifrados (Fernet con una clave derivada de SECRET_KEY).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from orkesta.core.config import get_settings
from orkesta.research import fetch

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALLBACK_PATH = "/api/mail/google/callback"
SCOPES = (
    "openid email profile https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/gmail.readonly"
)


class GmailError(Exception):
    def __init__(self, message: str, *, auth: bool = False, recipient: bool = False):
        super().__init__(message)
        self.auth = auth  # el buzón perdió el permiso: hay que reconectarlo
        self.recipient = recipient  # el problema es del destinatario, no del buzón


def _fernet() -> Fernet:
    key = hashlib.sha256(b"orkesta-mailbox:" + get_settings().signing_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise GmailError("No se pudo leer el permiso del buzón; reconéctalo.", auth=True) from exc


def enabled() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret and s.public_base_url)


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + CALLBACK_PATH


def authorization_url(state: str, login_hint: str = "") -> str:
    params = {
        "client_id": get_settings().google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, transport=fetch.TRANSPORT)


@dataclass
class Connected:
    email: str
    name: str
    refresh_token: str
    access_token: str
    expires_at: datetime
    scopes: str


def exchange_code(code: str) -> Connected:
    s = get_settings()
    with _client() as client:
        response = client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200:
        raise GmailError("Google no aceptó la autorización. Intenta conectar de nuevo.")
    data = response.json()
    if not data.get("refresh_token"):
        raise GmailError("Google no entregó un permiso permanente. Vuelve a conectar el buzón.")
    scopes = str(data.get("scope", ""))
    if "gmail.send" not in scopes:
        raise GmailError("Debes marcar el permiso para enviar correos en la pantalla de Google.")
    # El id_token llega directo de Google por TLS: se puede leer sin verificar la firma.
    claims = jwt.decode(str(data.get("id_token", "")), options={"verify_signature": False})
    email = str(claims.get("email", "")).lower()
    if not email or not claims.get("email_verified", True):
        raise GmailError("No pudimos confirmar el correo de la cuenta de Google.")
    return Connected(
        email=email,
        name=str(claims.get("name", "")),
        refresh_token=str(data["refresh_token"]),
        access_token=str(data["access_token"]),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)) - 60),
        scopes=scopes,
    )


def refresh_access_token(refresh_token: str) -> tuple[str, datetime]:
    s = get_settings()
    with _client() as client:
        response = client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code in (400, 401):
        raise GmailError("Google revocó el permiso del buzón; reconéctalo.", auth=True)
    if response.status_code != 200:
        raise GmailError(f"Google no respondió ({response.status_code}).")
    data = response.json()
    expires = datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
    return str(data["access_token"]), expires


def send(access_token: str, raw: bytes, thread_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(raw).decode()}
    if thread_id:
        body["threadId"] = thread_id
    with _client() as client:
        response = client.post(
            f"{API}/messages/send", json=body, headers={"Authorization": f"Bearer {access_token}"}
        )
    if response.status_code == 401:
        raise GmailError("El permiso del buzón venció; reconéctalo.", auth=True)
    if response.status_code == 429:
        raise GmailError("Gmail limitó los envíos por ahora (límite de la cuenta).")
    if response.status_code == 400:
        raise GmailError("Gmail rechazó este correo (dirección no válida).", recipient=True)
    if response.status_code != 200:
        raise GmailError(f"Gmail no respondió ({response.status_code}).")
    return dict(response.json())


def message_rfc_id(access_token: str, gmail_id: str) -> str:
    """El Message-ID real (para que los seguimientos queden en el mismo hilo del cliente)."""
    with _client() as client:
        response = client.get(
            f"{API}/messages/{gmail_id}",
            params={"format": "metadata", "metadataHeaders": "Message-ID"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        return ""
    for header in response.json().get("payload", {}).get("headers", []):
        if str(header.get("name", "")).lower() == "message-id":
            return str(header.get("value", ""))
    return ""


BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery")
BOUNCE_SUBJECTS = (
    "delivery status notification", "undeliverable", "undelivered", "returned mail",
    "no se puede entregar", "no se pudo entregar", "failure notice", "mail delivery failed",
    "entrega fallida",
)  # fmt: skip


def thread_outcome(access_token: str, thread_id: str, mailbox_email: str) -> str:
    """«replied», «bounced» o «» según los mensajes del hilo en Gmail."""
    with _client() as client:
        response = client.get(
            f"{API}/threads/{thread_id}",
            params=[("format", "metadata"), ("metadataHeaders", "From"),
                    ("metadataHeaders", "Subject"), ("metadataHeaders", "Auto-Submitted")],
            headers={"Authorization": f"Bearer {access_token}"},
        )  # fmt: skip
    if response.status_code == 401:
        raise GmailError("El permiso del buzón venció; reconéctalo.", auth=True)
    if response.status_code != 200:
        return ""
    outcome = ""
    for message in response.json().get("messages", []):
        headers = {str(h.get("name", "")).lower(): str(h.get("value", ""))
                   for h in message.get("payload", {}).get("headers", [])}  # fmt: skip
        sender = parseaddr(headers.get("from", ""))[1].lower()
        name = headers.get("from", "").lower()
        if sender == mailbox_email.lower() or "SENT" in message.get("labelIds", []):
            continue
        subject = headers.get("subject", "").lower()
        if any(b in name for b in BOUNCE_SENDERS) or any(b in subject for b in BOUNCE_SUBJECTS):
            outcome = "bounced"
            continue
        if headers.get("auto-submitted", "no").lower() not in ("", "no"):
            continue  # respuestas automáticas (fuera de oficina) no detienen la secuencia
        return "replied"
    return outcome
