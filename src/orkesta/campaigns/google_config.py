"""Configuración de Gmail y de la URL pública sin tocar variables del servidor.

- La credencial OAuth de Google (ID y secreto de cliente) se puede guardar desde la pantalla de
  Correo; el secreto queda cifrado. Las variables ``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET``
  siguen teniendo prioridad si están definidas.
- La URL pública (enlaces de baja y retorno de Google) se toma de ``PUBLIC_BASE_URL`` o, si
  falta, se recuerda la dirección con la que un usuario abrió la aplicación.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.config import get_settings
from orkesta.db.models import AppSetting
from orkesta.engine.mail import gmail
from orkesta.engine.mail.smtp import MailTransportError
from orkesta.secrets_box import SecretError, decrypt, encrypt

GOOGLE_KEY = "google_oauth"
PUBLIC_URL_KEY = "public_base_url"
RETURN_PATH = "/cuentas/google/retorno"


@dataclass(frozen=True)
class GoogleCredentials:
    client_id: str
    client_secret: str
    from_environment: bool


def _setting(session: Session, key: str, organization_id: uuid.UUID | None = None) -> object:
    query = select(AppSetting.value).where(AppSetting.key == key)
    if organization_id is not None:
        query = query.where(AppSetting.organization_id == organization_id)
    return session.scalar(query.order_by(AppSetting.updated_at.desc()).limit(1))


def _save_setting(session: Session, organization_id: uuid.UUID, key: str, value: object) -> None:
    statement = pg_insert(AppSetting).values(
        id=uuid.uuid4(), organization_id=organization_id, key=key, value=value
    )
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_app_settings_organization_id_key",
            set_={"value": statement.excluded.value},
        )
    )


# --- URL pública ---------------------------------------------------------------------------


def public_base_url(session: Session) -> str:
    configured = get_settings().public_base_url.strip().rstrip("/")
    if configured:
        return configured
    remembered = _setting(session, PUBLIC_URL_KEY)
    return str(remembered).rstrip("/") if isinstance(remembered, str) else ""


def remember_public_base_url(session: Session, organization_id: uuid.UUID, url: str) -> None:
    """Guarda la dirección pública si no está configurada (solo direcciones https)."""
    if get_settings().public_base_url or not url.startswith("https://"):
        return
    url = url.rstrip("/")
    if _setting(session, PUBLIC_URL_KEY, organization_id) != url:
        _save_setting(session, organization_id, PUBLIC_URL_KEY, url)


# --- Credencial de Google ------------------------------------------------------------------


def google_credentials(session: Session, organization_id: uuid.UUID) -> GoogleCredentials | None:
    settings = get_settings()
    if settings.google_client_id and settings.google_client_secret:
        return GoogleCredentials(settings.google_client_id, settings.google_client_secret, True)
    stored = _setting(session, GOOGLE_KEY, organization_id)
    if not isinstance(stored, dict) or not stored.get("client_id") or not stored.get("secret"):
        return None
    try:
        secret = decrypt(base64.b64decode(str(stored["secret"])))
    except (ValueError, SecretError):
        return None  # la clave del servidor cambió: hay que volver a guardar la credencial
    return GoogleCredentials(str(stored["client_id"]), secret, False)


def save_google_credentials(
    session: Session, organization_id: uuid.UUID, client_id: str, client_secret: str
) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError(
            "El ID de cliente de Google termina en «.apps.googleusercontent.com». Cópialo completo."
        )
    if len(client_secret) < 10:
        raise ValueError("El secreto de cliente parece incompleto. Cópialo completo.")
    secret = base64.b64encode(encrypt(client_secret)).decode("ascii")
    _save_setting(session, organization_id, GOOGLE_KEY, {"client_id": client_id, "secret": secret})


def redirect_uri(session: Session) -> str:
    base = public_base_url(session)
    return f"{base}{RETURN_PATH}" if base else ""


def google_client(session: Session, organization_id: uuid.UUID) -> gmail.GoogleClient:
    credentials = google_credentials(session, organization_id)
    if credentials is None:
        raise MailTransportError(
            "Gmail todavía no está configurado: un administrador debe guardar la credencial de "
            "Google en Correo → Configurar Google."
        )
    uri = redirect_uri(session)
    if not uri:
        raise MailTransportError("Falta la dirección pública de ORKESTA para volver desde Google.")
    return gmail.GoogleClient(credentials.client_id, credentials.client_secret, uri)
