"""Agregar contactos pegando direcciones de perfiles de LinkedIn.

ORKESTA nunca abre LinkedIn: solo usa la dirección. Si la persona ya está en el índice propio
(bases autorizadas, sitios oficiales y búsquedas anteriores) trae sus datos al instante; si no,
toma el nombre de la dirección («/in/carlos-ramirez» → Carlos Ramírez) y la empresa que escriba el
usuario. Después, el motor de enriquecimiento busca el sitio de la empresa y arma y verifica el
correo.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

MAX_URLS = 200
_URL = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]+)", re.IGNORECASE)
# Sufijos que LinkedIn agrega para no repetir direcciones: «carlos-ramirez-4b2a91».
_NOISE = re.compile(r"^(?=.*\d)[a-z0-9]{4,}$")
_LOWER = {"de", "del", "la", "las", "los", "y", "da", "do", "dos", "van", "von"}


@dataclass
class Profile:
    url: str
    slug: str
    first: str
    last: str


def parse(text: str) -> tuple[list[Profile], list[str]]:
    """Perfiles válidos (sin repetir) y líneas que no son perfiles de LinkedIn."""
    from urllib.parse import unquote

    found: dict[str, Profile] = {}
    invalid: list[str] = []
    for line in re.split(r"[\s,;]+", text or ""):
        line = line.strip(" <>\"'")
        if not line:
            continue
        m = _URL.search(line)
        if not m:
            invalid.append(line[:120])
            continue
        slug = unquote(m.group(1)).strip("/").lower()
        words = [w for w in re.split(r"[-_.]+", slug) if w]
        while len(words) > 1 and _NOISE.match(words[-1]):
            words.pop()
        names = [w if w in _LOWER else w[:1].upper() + w[1:] for w in words if not w.isdigit()]
        if not names:
            invalid.append(line[:120])
            continue
        first, last = names[0], " ".join(names[1:])
        found.setdefault(slug, Profile(f"https://www.linkedin.com/in/{slug}", slug, first, last))
    return list(found.values())[:MAX_URLS], invalid


def _from_index(session: Session, profiles: list[Profile], company: str,
                country: str) -> dict[str, Any]:  # fmt: skip
    """Perfiles que ORKESTA ya conoce: por su dirección o por nombre + empresa."""
    from orkesta.research.directory import name_key
    from orkesta.research.models import PersonIndex
    from orkesta.research.people import person_key

    by_url = {f"li:/in/{p.slug}": p.slug for p in profiles}
    out: dict[str, Any] = {}
    for row in session.scalars(select(PersonIndex).where(PersonIndex.key.in_(list(by_url)))):
        out[by_url[row.key]] = row
    for row in session.scalars(
        select(PersonIndex).where(PersonIndex.linkedin.is_not(None),
                                  PersonIndex.linkedin.in_([p.url for p in profiles]))
    ):  # fmt: skip
        slug = (row.linkedin or "").rstrip("/").rsplit("/in/", 1)[-1].lower()
        out.setdefault(slug, row)
    if company and country:
        ckey = name_key(company)
        keys = {person_key(country, ckey, f"{p.first} {p.last}"): p.slug for p in profiles
                if p.slug not in out}  # fmt: skip
        for row in session.scalars(select(PersonIndex).where(PersonIndex.key.in_(list(keys)))):
            out.setdefault(keys[row.key], row)
    return out


def add(session: Session, *, tenant_id: uuid.UUID, user: Any, text: str, company: str = "",
        country: str = "", list_id: uuid.UUID | None = None) -> dict[str, Any]:  # fmt: skip
    """Crea los contactos y devuelve el resumen (los nuevos sin correo quedan para enriquecer)."""
    from orkesta.data.models import Contact
    from orkesta.data.service import ContactInput, DataError, add_to_list, create_contact
    from orkesta.engine.normalize.location import COUNTRIES

    profiles, invalid = parse(text)
    if not profiles:
        raise DataError("Pega al menos una dirección de perfil de LinkedIn (linkedin.com/in/…).")
    company = " ".join(company.split())[:200]
    code = country.strip().upper()
    code = code if code in COUNTRIES else ""
    known = _from_index(session, profiles, company, code)
    created: list[uuid.UUID] = []
    existing = 0
    errors: list[str] = []
    from_index = with_email = 0
    for p in profiles:
        row = known.get(p.slug)
        first, last = (
            (row.first_name or p.first, row.last_name or p.last) if row else (p.first, p.last)
        )
        email = ""
        if row is not None and row.email and row.email_status in ("publicado", "confirmado"):
            email = row.email
        data = ContactInput(
            first_name=first, last_name=last,
            job_title=(row.title or "") if row else "",
            company=(row.company if row else "") or company,
            email=email,
            country=COUNTRIES.get((row.country if row else "") or code, ("",))[0],
        )  # fmt: skip
        try:
            contact = create_contact(session, tenant_id=tenant_id, user=user, data=data)
        except DataError as exc:
            if exc.status == 409:
                existing += 1
            else:
                errors.append(f"{p.first} {p.last}: {exc}"[:160])
            continue
        contact.linkedin_url = p.url[:300]
        if row is not None:
            from_index += 1
            contact.city = contact.city or (row.location or "").split(",")[0][:120] or None
        if email:
            with_email += 1
            contact.email_status = "sin_validar"
            contact.email_check_reason = (
                "Correo confirmado por una respuesta" if row and row.email_status == "confirmado"
                else "Correo publicado (índice de personas de ORKESTA)"
            )  # fmt: skip
        created.append(contact.id)
    session.flush()
    if list_id and created:
        add_to_list(session, tenant_id=tenant_id, list_id=list_id, contact_ids=created)
    pending = [
        cid for cid in created
        if (c := session.get(Contact, cid)) is not None and not c.email and c.company_name
    ]  # fmt: skip
    no_company = sum(
        1 for cid in created if (c := session.get(Contact, cid)) is not None and not c.company_name
    )
    return {
        "received": len(profiles), "created": len(created), "existing": existing,
        "from_index": from_index, "with_email": with_email, "to_enrich": pending,
        "no_company": no_company, "invalid": invalid[:20], "errors": errors[:20],
        "contact_ids": [str(c) for c in created],
    }  # fmt: skip
