"""Armado de correos de campaña: plantillas con variables, firma, hilo y baja de un clic."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid

VARIABLES = ("nombre", "apellido", "empresa", "cargo")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Záéíóúñ_]+)\s*\}\}")


def render_text(template: str, variables: dict[str, str]) -> str:
    """Reemplaza {{nombre}}, {{empresa}}… Una variable desconocida o vacía queda vacía."""
    return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1).lower(), ""), template)


def unknown_variables(template: str) -> set[str]:
    return {m.group(1).lower() for m in _PLACEHOLDER.finditer(template)} - set(VARIABLES)


def text_to_html(text: str) -> str:
    """Cuerpo en texto → HTML sencillo (párrafos y saltos de línea), escapando todo."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in p.splitlines()) + "</p>"
        for p in paragraphs
    )


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    html: str
    message_id: str
    in_reply_to: str | None = None
    unsubscribe_url: str | None = None


def build_mail(
    *,
    to: str,
    subject_template: str,
    body_template: str,
    variables: dict[str, str],
    signature_text: str | None,
    signature_html: str | None,
    sender_email: str,
    in_reply_to: str | None = None,
    unsubscribe_url: str | None = None,
    tracking_pixel_url: str | None = None,
) -> OutgoingMail:
    subject = " ".join(render_text(subject_template, variables).split())
    body = render_text(body_template, variables).strip()
    # El texto (plantilla + variables) se escapa una sola vez al pasarlo a HTML: un nombre con
    # "<" nunca inyecta marcado.
    body_html = text_to_html(body)
    text = body
    if signature_text:
        text += "\n\n" + signature_text.strip()
    body_html += (
        f"<div>{signature_html}</div>"
        if signature_html
        else (
            f"<p>{html.escape(signature_text)}</p>".replace("\n", "<br>") if signature_text else ""
        )
    )
    if unsubscribe_url:
        text += f"\n\nSi prefieres no recibir más correos: {unsubscribe_url}"
        body_html += (
            '<p style="font-size:12px;color:#777">Si prefieres no recibir más correos, '
            f'<a href="{html.escape(unsubscribe_url)}">date de baja aquí</a>.</p>'
        )
    if tracking_pixel_url:
        body_html += f'<img src="{html.escape(tracking_pixel_url)}" width="1" height="1" alt="">'
    domain = sender_email.rsplit("@", 1)[-1]
    if in_reply_to and not subject.lower().startswith(("re:", "re ")):
        subject = f"Re: {subject}"
    return OutgoingMail(
        to=to,
        subject=subject,
        text=text,
        html=f"<div>{body_html}</div>",
        message_id=make_msgid(domain=domain),
        in_reply_to=in_reply_to,
        unsubscribe_url=unsubscribe_url,
    )


def to_mime(mail: OutgoingMail, sender_email: str, sender_name: str | None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((sender_name or "", sender_email))
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = mail.message_id
    if mail.in_reply_to:
        # Seguimiento en el mismo hilo que el primer correo.
        message["In-Reply-To"] = mail.in_reply_to
        message["References"] = mail.in_reply_to
    if mail.unsubscribe_url:
        # Baja de un clic (RFC 8058), exigida por Gmail y Yahoo a remitentes masivos.
        message["List-Unsubscribe"] = f"<{mail.unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(mail.text)
    message.add_alternative(mail.html, subtype="html")
    return message
