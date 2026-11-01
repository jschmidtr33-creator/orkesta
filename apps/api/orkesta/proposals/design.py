"""Diseño de propuestas: marca de la empresa, plantillas, idiomas y datos del ejecutivo."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.platform.models import Tenant, User
from orkesta.proposals.models import Proposal, ProposalBrand

TEMPLATES = {
    "ejecutiva": "Ejecutiva",
    "moderna": "Moderna",
    "corporativa": "Corporativa",
    "minimal": "Minimal",
    "audaz": "Audaz",
}
LANGUAGES = {"es": "Español", "en": "English", "pt": "Português"}
FONTS = {
    "sans": "Sans moderna",
    "serif": "Clásica (serif)",
    "geometrica": "Geométrica",
    "humanista": "Humanista",
}
DEFAULT_BRAND: dict[str, Any] = {
    "logo_url": "",
    "primary": "#1E3A8A",
    "accent": "#06B6D4",
    "font": "sans",
    "template": "ejecutiva",
    "language": "es",
    "legal_name": "",
    "website": "",
    "phone": "",
    "address": "",
    "tax_id": "",
    "footer": "",
}
EXECUTIVE_FIELDS = ("name", "title", "email", "phone", "photo_url", "linkedin", "calendar_url")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class DesignError(ValueError):
    pass


def _color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(c * 2 for c in text[1:])
    return text.upper() if _HEX.match(text) else fallback


def _url(value: Any) -> str:
    text = str(value or "").strip()[:500]
    if text and not text.startswith(("https://", "http://", "/t/i/")):
        raise DesignError("Las imágenes y enlaces deben empezar por https://")
    return text


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def get_brand(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    row = session.get(ProposalBrand, tenant_id)
    tenant = session.get(Tenant, tenant_id)
    data = {**DEFAULT_BRAND, **(row.data if row else {})}
    data["legal_name"] = data["legal_name"] or (tenant.name if tenant else "")
    return data


def save_brand(session: Session, tenant_id: uuid.UUID, raw: dict[str, Any]) -> dict[str, Any]:
    current = get_brand(session, tenant_id)
    data = dict(current)
    if "logo_url" in raw:
        data["logo_url"] = _url(raw["logo_url"])
    for key in ("primary", "accent"):
        if key in raw:
            data[key] = _color(raw[key], current[key])
    if raw.get("font") in FONTS:
        data["font"] = raw["font"]
    if raw.get("template") in TEMPLATES:
        data["template"] = raw["template"]
    if raw.get("language") in LANGUAGES:
        data["language"] = raw["language"]
    for key in ("legal_name", "phone", "address", "tax_id"):
        if key in raw:
            data[key] = _text(raw[key])
    if "website" in raw:
        data["website"] = _text(raw["website"], 200)
    if "footer" in raw:
        data["footer"] = str(raw["footer"] or "").strip()[:600]
    row = session.get(ProposalBrand, tenant_id)
    if row is None:
        session.add(ProposalBrand(tenant_id=tenant_id, data=data))
    else:
        row.data = data
    session.flush()
    return data


def default_design(session: Session, tenant_id: uuid.UUID, user: User | None) -> dict[str, Any]:
    """Diseño inicial: el de la marca y los datos del ejecutivo que usó en su última propuesta."""
    brand = get_brand(session, tenant_id)
    executive: dict[str, str] = {}
    if user is not None:
        last = session.scalar(
            select(Proposal.design)
            .where(Proposal.tenant_id == tenant_id, Proposal.created_by == user.id)
            .order_by(Proposal.created_at.desc())
            .limit(1)
        )
        executive = dict((last or {}).get("executive") or {})
        executive.setdefault("name", user.full_name or "")
        executive.setdefault("email", user.email)
    return {
        "template": brand["template"],
        "language": brand["language"],
        "primary": "",
        "accent": "",
        "font": "",
        "cover_url": "",
        "executive": executive,
    }


def clean_design(raw: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    data = dict(current)
    if raw.get("template") in TEMPLATES:
        data["template"] = raw["template"]
    if raw.get("language") in LANGUAGES:
        data["language"] = raw["language"]
    for key in ("primary", "accent"):
        if key in raw:
            data[key] = _color(raw[key], "") if raw[key] else ""
    if "font" in raw:
        data["font"] = raw["font"] if raw["font"] in FONTS else ""
    if "cover_url" in raw:
        data["cover_url"] = _url(raw["cover_url"])
    if "executive" in raw:
        ex = dict(raw["executive"] or {})
        data["executive"] = {
            "name": _text(ex.get("name"), 160),
            "title": _text(ex.get("title"), 160),
            "email": _text(ex.get("email"), 320),
            "phone": _text(ex.get("phone"), 60),
            "photo_url": _url(ex.get("photo_url")),
            "linkedin": _url(ex.get("linkedin")),
            "calendar_url": _url(ex.get("calendar_url")),
        }
    return data


def resolved(brand: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    """Lo que ve el cliente: la propuesta hereda de la marca lo que no personalizó."""
    return {
        "template": design.get("template") or brand["template"],
        "language": design.get("language") or brand["language"],
        "primary": design.get("primary") or brand["primary"],
        "accent": design.get("accent") or brand["accent"],
        "font": design.get("font") or brand["font"],
        "cover_url": design.get("cover_url") or "",
        "logo_url": brand["logo_url"],
        "executive": design.get("executive") or {},
        "company": {
            k: brand[k] for k in ("legal_name", "website", "phone", "address", "tax_id", "footer")
        },
    }
