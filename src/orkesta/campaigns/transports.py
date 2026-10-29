"""Transporte de una cuenta de envío: Gmail (API) o correo corporativo (SMTP + IMAP)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from orkesta.config import get_settings
from orkesta.db.models import MailAccount
from orkesta.engine.mail import gmail
from orkesta.engine.mail.smtp import (
    MailTransportError,
    SmtpSettings,
    fetch_inbox,
    parse_inbox,
    send_smtp,
)
from orkesta.secrets_box import decrypt


@dataclass(frozen=True)
class SentRef:
    provider_id: str | None
    thread_id: str | None


@dataclass(frozen=True)
class Watched:
    """Lo necesario para saber si un destinatario respondió o rebotó."""

    recipient_key: str
    email: str
    thread_id: str | None
    message_ids: frozenset[str]


@dataclass(frozen=True)
class Events:
    replied: set[str]  # recipient_key
    bounced: set[str]


class Transport(Protocol):
    def send(self, message: EmailMessage, thread_id: str | None) -> SentRef: ...

    def events(self, watched: list[Watched]) -> Events: ...


def google_client() -> gmail.GoogleClient:
    settings = get_settings()
    if not (
        settings.google_client_id and settings.google_client_secret and settings.public_base_url
    ):
        raise MailTransportError(
            "Gmail no está configurado: faltan GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET o "
            "PUBLIC_BASE_URL."
        )
    return gmail.GoogleClient(
        settings.google_client_id,
        settings.google_client_secret,
        settings.public_base_url.rstrip("/") + "/cuentas/google/retorno",
    )


class GmailTransport:
    def __init__(self, account: MailAccount) -> None:
        if account.secret is None:
            raise MailTransportError("La cuenta de Gmail no tiene autorización: reconéctala.")
        self.account = account
        self._token: str | None = None

    def _access(self) -> str:
        if self._token is None:
            self._token = gmail.access_token(google_client(), decrypt(self.account.secret or b""))
        return self._token

    def send(self, message: EmailMessage, thread_id: str | None) -> SentRef:
        provider_id, thread = gmail.send_gmail(self._access(), message, thread_id)
        return SentRef(provider_id, thread)

    def events(self, watched: list[Watched]) -> Events:
        replied: set[str] = set()
        bounced: set[str] = set()
        for item in watched:
            if not item.thread_id:
                continue
            state = gmail.thread_state(self._access(), item.thread_id, self.account.email)
            if state.bounced:
                bounced.add(item.recipient_key)
            elif state.replied:
                replied.add(item.recipient_key)
        return Events(replied, bounced)


class SmtpTransport:
    def __init__(self, account: MailAccount) -> None:
        if not (account.smtp_host and account.smtp_port and account.secret):
            raise MailTransportError(
                "La cuenta SMTP está incompleta: revisa servidor y contraseña."
            )
        self.account = account
        self.password = decrypt(account.secret)

    def send(self, message: EmailMessage, thread_id: str | None) -> SentRef:
        account = self.account
        send_smtp(
            SmtpSettings(
                account.smtp_host or "",
                account.smtp_port or 587,
                account.smtp_security or "starttls",
                account.username or account.email,
                self.password,
            ),
            message,
        )
        return SentRef(None, None)

    def events(self, watched: list[Watched]) -> Events:
        account = self.account
        if not account.imap_host or not watched:
            return Events(set(), set())  # sin IMAP no se detectan respuestas
        inbox = fetch_inbox(
            account.imap_host, account.imap_port or 993, account.username or account.email,
            self.password,
        )  # fmt: skip
        ids = {mid for item in watched for mid in item.message_ids}
        found = parse_inbox(inbox, ids, {item.email for item in watched})
        replied = {item.recipient_key for item in watched if item.message_ids & found.replied}
        bounced = {item.recipient_key for item in watched if item.email in found.bounced}
        return Events(replied - bounced, bounced)


def default_transport(account: MailAccount) -> Transport:
    if account.provider == "gmail":
        return GmailTransport(account)
    return SmtpTransport(account)


TransportFactory = Callable[[MailAccount], Transport]
