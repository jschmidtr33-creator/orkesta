"""Correo corporativo genérico: envío por SMTP y respuestas/rebotes por IMAP.

Sirve para Microsoft 365, Zoho, Google Workspace con contraseña de aplicación, cPanel, etc.
"""

from __future__ import annotations

import email
import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage, Message
from email.utils import parseaddr

TIMEOUT = 30
BOUNCE_SENDERS = ("mailer-daemon", "postmaster")


class MailTransportError(Exception):
    """Fallo de envío o de conexión (credenciales, servidor, red)."""


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str  # "ssl" (465) o "starttls" (587)
    username: str
    password: str


def send_smtp(settings: SmtpSettings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            with smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=TIMEOUT, context=context
            ) as client:
                client.login(settings.username, settings.password)
                client.send_message(message)
        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT) as client:
                client.starttls(context=context)
                client.login(settings.username, settings.password)
                client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailTransportError("El servidor rechazó el usuario o la contraseña.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailTransportError(f"No se pudo enviar: {type(exc).__name__}: {exc}") from exc


def check_smtp(settings: SmtpSettings) -> None:
    """Prueba de conexión y autenticación sin enviar nada."""
    context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            with smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=TIMEOUT, context=context
            ) as client:
                client.login(settings.username, settings.password)
        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT) as client:
                client.starttls(context=context)
                client.login(settings.username, settings.password)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailTransportError("El servidor rechazó el usuario o la contraseña.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailTransportError(f"No se pudo conectar: {type(exc).__name__}: {exc}") from exc


@dataclass(frozen=True)
class InboxEvents:
    replied: set[str]  # Message-ID nuestros que recibieron respuesta
    bounced: set[str]  # direcciones de destino que rebotaron


def _references(message: Message) -> set[str]:
    values = " ".join(str(message.get(header, "")) for header in ("In-Reply-To", "References"))
    return {token for token in values.split() if token.startswith("<")}


def parse_inbox(messages: list[Message], sent_ids: set[str], sent_to: set[str]) -> InboxEvents:
    """Respuestas: correos cuyo In-Reply-To/References cita un Message-ID nuestro.
    Rebotes: avisos de mailer-daemon/postmaster que mencionan una dirección a la que enviamos."""
    replied: set[str] = set()
    bounced: set[str] = set()
    for message in messages:
        sender = parseaddr(str(message.get("From", "")))[1].lower()
        if sender.split("@", 1)[0] in BOUNCE_SENDERS or "delivery status" in str(
            message.get("Content-Type", "")
        ):
            payload = message.as_string().lower()
            bounced.update(address for address in sent_to if address in payload)
            continue
        replied.update(_references(message) & sent_ids)
    return InboxEvents(replied, bounced)


def fetch_inbox(
    host: str, port: int, username: str, password: str, since_days: int = 14
) -> list[Message]:
    """Correos recientes de la bandeja de entrada (solo cabeceras y cuerpo para cotejar)."""
    import datetime as dt

    since = (dt.date.today() - dt.timedelta(days=since_days)).strftime("%d-%b-%Y")
    try:
        with imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT) as client:
            client.login(username, password)
            client.select("INBOX", readonly=True)
            status, data = client.search(None, f'(SINCE "{since}")')
            if status != "OK":
                return []
            messages: list[Message] = []
            for number in data[0].split()[-500:]:
                status, parts = client.fetch(number, "(BODY.PEEK[])")
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    messages.append(email.message_from_bytes(parts[0][1]))
            return messages
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailTransportError(f"No se pudo leer el buzón (IMAP): {exc}") from exc
