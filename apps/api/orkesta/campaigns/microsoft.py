"""Microsoft 365 / Outlook por OAuth y Microsoft Graph (HTTPS; funciona aunque el servidor no
pueda abrir puertos SMTP). Envía el mismo MIME que Gmail y lee respuestas por conversación."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from orkesta.campaigns.gmail import (
    BOUNCE_SENDERS,
    BOUNCE_SUBJECTS,
    Connected,
    GmailError,
    classify_bounce,
)
from orkesta.core.config import get_settings
from orkesta.research import fetch

AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
API = "https://graph.microsoft.com/v1.0/me"
CALLBACK_PATH = "/api/mail/microsoft/callback"
SCOPES = "openid email profile offline_access User.Read Mail.Send Mail.ReadWrite"
AUTO_REPLY = ("respuesta automática", "automatic reply", "fuera de la oficina", "out of office",
              "resposta automática")  # fmt: skip


def enabled() -> bool:
    s = get_settings()
    return bool(s.microsoft_client_id and s.microsoft_client_secret and s.public_base_url)


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + CALLBACK_PATH


def authorization_url(state: str, login_hint: str = "") -> str:
    params = {
        "client_id": get_settings().microsoft_client_id, "response_type": "code",
        "redirect_uri": redirect_uri(), "response_mode": "query", "scope": SCOPES,
        "state": state, "prompt": "select_account",
    }  # fmt: skip
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, transport=fetch.TRANSPORT)


def _token(data: dict[str, str]) -> dict[str, Any]:
    s = get_settings()
    with _client() as client:
        response = client.post(TOKEN_URL, data={
            **data, "client_id": s.microsoft_client_id, "client_secret": s.microsoft_client_secret,
            "scope": SCOPES,
        })  # fmt: skip
    if response.status_code in (400, 401):
        raise GmailError("Microsoft revocó o rechazó el permiso del buzón; reconéctalo.", auth=True)
    if response.status_code != 200:
        raise GmailError(f"Microsoft no respondió ({response.status_code}).")
    return dict(response.json())


def exchange_code(code: str) -> Connected:
    data = _token({"code": code, "redirect_uri": redirect_uri(),
                   "grant_type": "authorization_code"})  # fmt: skip
    if not data.get("refresh_token"):
        raise GmailError("Microsoft no entregó un permiso permanente. Vuelve a conectar.")
    if "mail.send" not in str(data.get("scope", "")).lower():
        raise GmailError(
            "Debes aceptar el permiso para enviar correos en la pantalla de Microsoft."
        )
    # El id_token llega directo de Microsoft por TLS: se puede leer sin verificar la firma.
    claims = jwt.decode(str(data.get("id_token", "")), options={"verify_signature": False})
    email = str(claims.get("email") or claims.get("preferred_username") or "").lower()
    if "@" not in email:
        raise GmailError("No pudimos confirmar el correo de la cuenta de Microsoft.")
    return Connected(
        email=email, name=str(claims.get("name", "")), refresh_token=str(data["refresh_token"]),
        access_token=str(data["access_token"]),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)) - 60),
        scopes=str(data.get("scope", "")),
    )  # fmt: skip


def refresh_access_token(refresh_token: str) -> tuple[str, datetime]:
    data = _token({"refresh_token": refresh_token, "grant_type": "refresh_token"})
    expires = datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
    return str(data["access_token"]), expires


def _check(response: httpx.Response, action: str) -> None:
    if response.status_code == 401:
        raise GmailError("El permiso del buzón de Microsoft venció; reconéctalo.", auth=True)
    if response.status_code == 429:
        raise GmailError("Microsoft limitó los envíos por ahora (límite de la cuenta).")
    if response.status_code == 400 and action == "send":
        raise GmailError("Microsoft rechazó este correo (dirección no válida).", recipient=True)
    if response.status_code >= 300:
        raise GmailError(f"Microsoft no respondió ({response.status_code}).")


def send(access_token: str, raw: bytes) -> dict[str, str]:
    """Crea el mensaje desde el MIME (así se conserva el hilo y el seguimiento) y lo envía."""
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "text/plain"}
    with _client() as client:
        draft = client.post(f"{API}/messages", content=base64.b64encode(raw), headers=headers)
        _check(draft, "send")
        message = draft.json()
        sent = client.post(f"{API}/messages/{message['id']}/send",
                           headers={"Authorization": f"Bearer {access_token}"})  # fmt: skip
        _check(sent, "send")
    return {
        "id": str(message.get("id", "")),
        "threadId": str(message.get("conversationId", "")),
        "rfc_id": str(message.get("internetMessageId", "")),
    }


def thread_check(
    access_token: str, conversation_id: str, mailbox_email: str
) -> tuple[str, list[str]]:
    """Respuestas, rebotes y bloqueos en la conversación del destinatario."""
    if not conversation_id:
        return "", []
    safe = conversation_id.replace("'", "''")
    with _client() as client:
        response = client.get(f"{API}/messages", params={
            "$filter": f"conversationId eq '{safe}'", "$top": "50",
            "$select": "from,subject,bodyPreview,isDraft,inferenceClassification",
        }, headers={"Authorization": f"Bearer {access_token}"})  # fmt: skip
    if response.status_code == 401:
        raise GmailError("El permiso del buzón de Microsoft venció; reconéctalo.", auth=True)
    if response.status_code != 200:
        return "", []
    outcome, replies = "", []
    for message in response.json().get("value", []):
        sender = message.get("from", {}).get("emailAddress", {})
        address = str(sender.get("address", "")).lower()
        name = f"{sender.get('name', '')} {address}".lower()
        if address == mailbox_email.lower() or message.get("isDraft"):
            continue
        subject = str(message.get("subject", "")).lower()
        preview = str(message.get("bodyPreview", ""))
        if any(b in name for b in BOUNCE_SENDERS) or any(b in subject for b in BOUNCE_SUBJECTS):
            kind = classify_bounce(f"{subject} {preview}")
            if kind == "bounced" or (kind == "blocked" and outcome != "bounced"):
                outcome = kind
            continue
        if any(a in subject for a in AUTO_REPLY):
            continue
        replies.append(preview)
    return ("replied" if replies else outcome), replies
