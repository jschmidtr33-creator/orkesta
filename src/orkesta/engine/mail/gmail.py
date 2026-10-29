"""Gmail: conexión con OAuth de Google, envío por la API de Gmail y detección de respuestas.

Permisos pedidos: ``gmail.send`` (enviar) y ``gmail.readonly`` (ver si un hilo recibió
respuesta). Para una cuenta de la Workspace de la empresa, la app se configura como interna en
Google Cloud y no requiere verificación de Google.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlencode

import httpx

from orkesta.engine.mail.smtp import BOUNCE_SENDERS, MailTransportError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL pública, no un secreto
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)
TIMEOUT = 30


@dataclass(frozen=True)
class GoogleClient:
    client_id: str
    client_secret: str
    redirect_uri: str


def authorization_url(client: GoogleClient, state: str) -> str:
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",  # refresh token para enviar sin que el usuario esté
                "prompt": "consent",
                "state": state,
            }
        )
    )


def exchange_code(
    client: GoogleClient, code: str, http: httpx.Client | None = None
) -> tuple[str, str]:
    """Código de autorización → (refresh_token, correo de la cuenta)."""
    with http or httpx.Client(timeout=TIMEOUT) as session:
        response = session.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "redirect_uri": client.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.status_code != 200:
            raise MailTransportError("Google no aceptó la autorización. Intenta conectar de nuevo.")
        data = response.json()
        refresh = data.get("refresh_token")
        if not refresh:
            raise MailTransportError(
                "Google no entregó permiso permanente. Revoca el acceso y reconecta."
            )
        profile = session.get(
            f"{API}/profile", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        if profile.status_code != 200:
            raise MailTransportError("No se pudo leer el perfil de Gmail.")
        return refresh, str(profile.json()["emailAddress"]).lower()


def access_token(client: GoogleClient, refresh_token: str, http: httpx.Client | None = None) -> str:
    with http or httpx.Client(timeout=TIMEOUT) as session:
        response = session.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code != 200:
        raise MailTransportError(
            "El acceso a Gmail expiró o fue revocado: vuelve a conectar la cuenta."
        )
    return str(response.json()["access_token"])


def send_gmail(
    token: str, message: EmailMessage, thread_id: str | None, http: httpx.Client | None = None
) -> tuple[str, str]:
    """Envía y devuelve (id del mensaje, id del hilo)."""
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body: dict[str, str] = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    with http or httpx.Client(timeout=TIMEOUT) as session:
        response = session.post(
            f"{API}/messages/send", json=body, headers={"Authorization": f"Bearer {token}"}
        )
    if response.status_code == 429:
        raise MailTransportError("Gmail limitó los envíos por ahora (cuota). Se reintentará.")
    if response.status_code != 200:
        raise MailTransportError(f"Gmail rechazó el envío ({response.status_code}).")
    data = response.json()
    return str(data["id"]), str(data["threadId"])


@dataclass(frozen=True)
class ThreadState:
    replied: bool
    bounced: bool


def thread_state(
    token: str, thread_id: str, own_email: str, http: httpx.Client | None = None
) -> ThreadState:
    """¿Alguien distinto de nosotros escribió en el hilo? ¿Hubo un aviso de rebote?"""
    with http or httpx.Client(timeout=TIMEOUT) as session:
        response = session.get(
            f"{API}/threads/{thread_id}",
            params={"format": "metadata", "metadataHeaders": ["From"]},
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise MailTransportError(f"No se pudo leer el hilo ({response.status_code}).")
    replied = bounced = False
    for message in response.json().get("messages", []):
        headers = {
            h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])
        }
        sender = parseaddr(headers.get("from", ""))[1].lower()
        if not sender or sender == own_email.lower():
            continue
        if sender.split("@", 1)[0] in BOUNCE_SENDERS:
            bounced = True
        else:
            replied = True
    return ThreadState(replied, bounced)
