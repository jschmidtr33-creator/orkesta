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

import nh3

from orkesta.copywriter.engine import contact_industry, contact_reto
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
    "reto": "Reto de su área",
    "industria": "Su industria",
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
        # IA propia: el reto típico del área del contacto y su industria, para personalizar.
        "reto": contact_reto(getattr(contact, "area_code", None)),
        "industria": contact_industry(
            getattr(contact, "industry_code", None), getattr(contact, "industry", None)
        ),
    }


def render(text: str, values: dict[str, str], *, escape: bool = False) -> tuple[str, list[str]]:
    """Texto con variables reemplazadas y la lista de variables que quedaron vacías."""
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        value = values.get(name, "")
        if value:
            return html.escape(value) if escape else value
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


# --- HTML: cuerpo y firma con formato, enlaces e imágenes ----------------------------------

TAGS = {
    "p", "br", "div", "span", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "blockquote", "img", "table", "thead", "tbody", "tr", "td", "th",
    "hr", "font", "center", "small", "sup", "sub",
}  # fmt: skip
ATTRIBUTES = {
    "*": {"style", "align"},
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height", "border"},
    "table": {"width", "cellpadding", "cellspacing", "border", "bgcolor", "role"},
    "td": {"width", "valign", "colspan", "rowspan", "bgcolor", "height"},
    "th": {"width", "valign", "colspan", "rowspan", "bgcolor"},
    "font": {"color", "face", "size"},
}
STYLES = {
    "color", "background-color", "font-family", "font-size", "font-weight", "font-style",
    "text-decoration", "text-align", "line-height", "margin", "margin-top", "margin-bottom",
    "margin-left", "margin-right", "padding", "padding-top", "padding-bottom", "padding-left",
    "padding-right", "width", "max-width", "height", "border", "border-radius", "display",
    "vertical-align", "letter-spacing",
}  # fmt: skip
_HTML = re.compile(r"<(p|div|br|img|a|table|strong|b|em|ul|ol|span|h[1-4])\b", re.I)
_HREF = re.compile(r'href="(https?://[^"]+)"')
_IMG = re.compile(r"<img\b(?![^>]*style=)", re.I)


def is_html(text: str) -> bool:
    return bool(_HTML.search(text or ""))


def clean_html(value: str) -> str:
    return nh3.clean(
        value, tags=TAGS, attributes=ATTRIBUTES, url_schemes={"http", "https", "mailto", "tel"},
        filter_style_properties=STYLES, link_rel=None, strip_comments=True,
    )  # fmt: skip


def as_html(text: str) -> str:
    """HTML seguro a partir de lo que escribió el usuario (texto plano o HTML)."""
    if not (text or "").strip():
        return ""
    if is_html(text):
        return clean_html(text)
    escaped = _URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', html.escape(text))
    paragraphs = [p.replace("\n", "<br>") for p in escaped.strip().split("\n\n")]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", value)

    def link(m: re.Match[str]) -> str:
        href, label = m.group(1), m.group(2)
        return label if href in label else f"{label} ({href})"

    text = re.sub(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', link, text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "• ", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-4]|tr|blockquote|table)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build(
    *,
    sender_email: str,
    sender_name: str,
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str,
    signature_html: str,
    token: str,
    track_opens: bool,
    track_clicks: bool,
    in_reply_to: str | None = None,
    references: str | None = None,
    plain: bool = False,
) -> Built:
    """Arma el correo. ``body_html`` y ``signature_html`` ya vienen limpios y personalizados."""
    base = get_settings().public_base_url.rstrip("/")
    tracking = bool(base)
    unsubscribe_url = f"{base}/t/u/{token}" if tracking else ""
    content = body_html + (f'<div style="margin-top:16px">{signature_html}</div>'
                           if signature_html.strip() else "")  # fmt: skip
    text_body = html_to_text(content)
    if tracking and track_clicks:
        content = _HREF.sub(
            lambda m: f'href="{html.escape(click_url(token, html.unescape(m.group(1))))}"', content
        )
    content = _IMG.sub('<img style="max-width:100%;height:auto"', content)
    footer_text = (
        "\n\n—\nSi prefieres no recibir más correos míos, responde «baja»"
        + (f" o entra a {unsubscribe_url}" if unsubscribe_url else "")
        + "."
    )
    html_body = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;"
        "color:#1f2937\">" + content
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
    if not plain:  # modo corporativo: solo texto, sin pixel, enlaces rastreados ni imágenes
        message.add_alternative(html_body, subtype="html")
    return Built(message, rfc_id, subject)
