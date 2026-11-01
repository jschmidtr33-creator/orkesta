"""Personalización y armado del correo (texto + HTML) con seguimiento y baja en un clic."""

from __future__ import annotations

import hashlib
import hmac
import html
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any
from urllib.parse import quote, urlencode

from orkesta.core.config import get_settings

VARIABLES = {
    "nombre": "Nombre",
    "apellido": "Apellido",
    "nombre_completo": "Nombre completo",
    "empresa": "Empresa",
    "cargo": "Cargo",
    "ciudad": "Ciudad",
    "pais": "País",
    "remitente": "Tu nombre",
}
_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*(?:\|\s*([^}]*))?\}\}")
_URL = re.compile(r"(https?://[^\s<>\"')]+)")


def context(contact: Any, sender_name: str, country_name: str = "") -> dict[str, str]:
    return {
        "nombre": (contact.first_name or "").split(" ")[0] if contact.first_name else "",
        "apellido": contact.last_name or "",
        "nombre_completo": contact.full_name or "",
        "empresa": contact.company_name or "",
        "cargo": contact.job_title or "",
        "ciudad": contact.city or "",
        "pais": country_name,
        "remitente": sender_name,
    }


def render(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Texto con variables reemplazadas y la lista de variables que quedaron vacías."""
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        value = values.get(name, "")
        if value:
            return value
        if fallback is not None:
            return fallback.strip()
        missing.append(name)
        return ""

    return _VAR.sub(repl, text), missing


def _sign(value: str) -> str:
    key = get_settings().signing_key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:24]


def click_url(token: str, url: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/t/c/{token}?{urlencode({'u': url, 's': _sign(token + url)})}"


def valid_click(token: str, url: str, signature: str) -> bool:
    return hmac.compare_digest(_sign(token + url), signature or "")


@dataclass
class Built:
    message: EmailMessage
    rfc_id: str
    subject: str


def build(
    *,
    sender_email: str,
    sender_name: str,
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    signature: str,
    token: str,
    track_opens: bool,
    track_clicks: bool,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> Built:
    base = get_settings().public_base_url.rstrip("/")
    tracking = bool(base)
    unsubscribe_url = f"{base}/t/u/{token}" if tracking else ""
    text_body = body.rstrip() + (f"\n\n{signature.strip()}" if signature.strip() else "")
    footer_text = (
        "\n\n—\nSi prefieres no recibir más correos míos, responde «baja»"
        + (f" o entra a {unsubscribe_url}" if unsubscribe_url else "")
        + "."
    )

    def to_html(text: str) -> str:
        escaped = html.escape(text)

        def link(m: re.Match[str]) -> str:
            url = html.unescape(m.group(1))
            href = click_url(token, url) if tracking and track_clicks else url
            return f'<a href="{html.escape(href)}">{m.group(1)}</a>'

        escaped = _URL.sub(link, escaped)
        paragraphs = [p.replace("\n", "<br>") for p in escaped.split("\n\n")]
        return "".join(f'<p style="margin:0 0 12px">{p}</p>' for p in paragraphs)

    html_body = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;"
        "color:#1f2937\">" + to_html(text_body)
        + "<p style=\"margin:24px 0 0;font-size:11px;color:#94a3b8\">Si prefieres no recibir más "
        "correos míos, "
        + (f"<a href=\"{html.escape(unsubscribe_url)}\" style=\"color:#94a3b8\">"
           "date de baja aquí</a>"
           if unsubscribe_url else "responde «baja»")
        + ".</p>"
        + (f"<img src=\"{base}/t/o/{token}.gif\" width=\"1\" height=\"1\" alt=\"\" "
           "style=\"display:block\">" if tracking and track_opens else "")
        + "</div>"
    )  # fmt: skip
    message = EmailMessage()
    domain = sender_email.split("@", 1)[1] if "@" in sender_email else "orkesta.local"
    rfc_id = make_msgid(domain=domain)
    message["Message-ID"] = rfc_id
    message["From"] = formataddr((sender_name, sender_email))
    message["To"] = formataddr((to_name, to_email))
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    if unsubscribe_url:
        message["List-Unsubscribe"] = (
            f"<{unsubscribe_url}>, <mailto:{sender_email}?subject={quote('baja')}>"
        )
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(text_body + footer_text)
    message.add_alternative(html_body, subtype="html")
    return Built(message, rfc_id, subject)
