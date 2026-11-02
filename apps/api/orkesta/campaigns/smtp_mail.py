"""Correo corporativo genérico: SMTP para enviar e IMAP para leer respuestas y rebotes.

La contraseña (idealmente una «contraseña de aplicación») se guarda cifrada. Algunos hostings
bloquean los puertos SMTP; en ese caso la prueba de conexión lo explica.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import json
import re
import smtplib
import socket
import ssl
from dataclasses import asdict, dataclass
from email.utils import parseaddr

from orkesta.campaigns.gmail import (
    BOUNCE_SENDERS,
    BOUNCE_SUBJECTS,
    GmailError,
    classify_bounce,
    decrypt,
    encrypt,
)

TIMEOUT = 20


@dataclass
class SmtpConfig:
    username: str
    password: str
    smtp_host: str
    smtp_port: int = 587
    imap_host: str = ""
    imap_port: int = 993

    def dumps(self) -> str:
        return encrypt(json.dumps(asdict(self)))

    @classmethod
    def loads(cls, value: str) -> SmtpConfig:
        return cls(**json.loads(decrypt(value)))


def _blocked(exc: Exception) -> GmailError:
    text = str(exc).lower()
    if (
        isinstance(exc, smtplib.SMTPAuthenticationError)
        or "authentication" in text
        or "auth" in text
    ):
        return GmailError(
            "El servidor rechazó el usuario o la contraseña. Si tu correo tiene "
            "verificación en dos pasos, usa una contraseña de aplicación.",
            auth=True,
        )
    if isinstance(exc, socket.timeout | TimeoutError | OSError) and (
        "unreachable" in text or "timed out" in text or "refused" in text or "errno 101" in text
    ):
        return GmailError(
            "No se pudo conectar al servidor de correo. Revisa el servidor y el "
            "puerto; algunos hostings bloquean los puertos SMTP (25, 465 y 587)."
        )
    return GmailError(f"El servidor de correo respondió con un error: {str(exc)[:160]}")


def _smtp(config: SmtpConfig) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if config.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port,
                                                timeout=TIMEOUT, context=context)  # fmt: skip
    else:
        server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=TIMEOUT)
        server.starttls(context=context)
    server.login(config.username, config.password)
    return server


def _imap(config: SmtpConfig) -> imaplib.IMAP4_SSL:
    host = config.imap_host or config.smtp_host.replace("smtp.", "imap.", 1)
    box = imaplib.IMAP4_SSL(host, config.imap_port, ssl_context=ssl.create_default_context(),
                            timeout=TIMEOUT)  # fmt: skip
    box.login(config.username, config.password)
    return box


def test(config: SmtpConfig) -> None:
    """Prueba de conexión: inicia sesión en SMTP y en IMAP."""
    try:
        server = _smtp(config)
        server.quit()
        box = _imap(config)
        box.logout()
    except (smtplib.SMTPException, imaplib.IMAP4.error, OSError) as exc:
        raise _blocked(exc) from exc


def send(config: SmtpConfig, raw: bytes, sender: str, to: str, rfc_id: str) -> dict[str, str]:
    try:
        server = _smtp(config)
        try:
            refused = server.sendmail(sender, [to], raw)
        finally:
            server.quit()
    except smtplib.SMTPRecipientsRefused as exc:
        raise GmailError(
            "El servidor rechazó la dirección del destinatario.", recipient=True
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise _blocked(exc) from exc
    if refused:
        raise GmailError("El servidor rechazó la dirección del destinatario.", recipient=True)
    # El hilo se sigue por el Message-ID del primer correo.
    return {"id": rfc_id, "threadId": rfc_id, "rfc_id": rfc_id}


def _text(msg: email.message.Message) -> str:
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace") if isinstance(payload, bytes) else ""
    return ""


def thread_check(config: SmtpConfig, rfc_id: str, mailbox_email: str) -> tuple[str, list[str]]:
    """Busca en la bandeja de entrada las respuestas y rebotes que citan el primer correo."""
    if not rfc_id:
        return "", []
    quoted = rfc_id.replace('"', "")
    try:
        box = _imap(config)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise _blocked(exc) from exc
    outcome, replies = "", []
    try:
        box.select("INBOX", readonly=True)
        ids: set[bytes] = set()
        for criteria in (f'HEADER References "{quoted}"', f'HEADER In-Reply-To "{quoted}"',
                         f'TEXT "{quoted}"'):  # fmt: skip
            status, data = box.search(None, criteria)
            if status == "OK" and data and data[0]:
                ids.update(data[0].split())
        for num in sorted(ids)[-30:]:
            status, parts = box.fetch(num.decode(), "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            msg = email.message_from_bytes(parts[0][1])
            sender = parseaddr(str(msg.get("From", "")))[1].lower()
            name = str(msg.get("From", "")).lower()
            if sender == mailbox_email.lower():
                continue
            subject = str(msg.get("Subject", "")).lower()
            body = re.sub(r"\s+", " ", _text(msg))[:600]
            if any(b in name for b in BOUNCE_SENDERS) or any(b in subject for b in BOUNCE_SUBJECTS):
                kind = classify_bounce(f"{subject} {body}")
                if kind == "bounced" or (kind == "blocked" and outcome != "bounced"):
                    outcome = kind
                continue
            if str(msg.get("Auto-Submitted", "no")).lower() not in ("", "no"):
                continue
            replies.append(body[:300])
    finally:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            box.logout()
    return ("replied" if replies else outcome), replies
