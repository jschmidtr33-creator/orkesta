# ruff: noqa: E501
"""«Prepárame la demo con Clínica Horizonte»: encontrar en el CRM la cuenta que la persona
nombra (oportunidad, empresa o contacto) para responder con su historia y no en general."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.crm.models import Deal
from orkesta.data.models import Company, Contact
from orkesta.engine.normalize.text import fold

_ABOUT_ACCOUNT = re.compile(
    r"\b(demo|reunion|llamada|cita|visita|preparar|preparo|preparame|cuenta|oportunidad|negocio|"
    r"avanzo|avanzar|cierro|cerrar|propuesta|seguimiento|como voy con|que hago con|que le (escribo|digo))\b"
)
_GENERIC = {"empresa", "cliente", "clientes", "equipo", "demo", "propuesta", "oportunidad"}


def _key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", fold(text)))


def _in(name: str, question: str, strict: bool = False) -> bool:
    key = _key(name)
    if len(key) < (6 if strict else 4) or key in _GENERIC:
        return False
    return re.search(rf"\b{re.escape(key)}\b", question) is not None


def mentioned(
    session: Session, tenant_id: uuid.UUID, question: str
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(contacto, oportunidad) que la pregunta nombra, si habla de una cuenta. Prefiere la
    oportunidad abierta más reciente de esa empresa o contacto."""
    folded = _key(question)
    # Con verbos de cuenta («demo», «avanzo») basta un nombre corto; sin ellos, el nombre debe
    # ser claro (6 letras o más) para no confundir palabras comunes con una empresa.
    strict = not _ABOUT_ACCOUNT.search(folded)
    deals = list(session.scalars(
        select(Deal).where(Deal.tenant_id == tenant_id).order_by(Deal.created_at.desc()).limit(500)
    ))  # fmt: skip
    open_first = sorted(deals, key=lambda d: d.status != "open")
    companies = {
        c.id: c.name
        for c in session.scalars(
            select(Company).where(Company.tenant_id == tenant_id,
                                  Company.id.in_([d.company_id for d in deals if d.company_id]))
        )
    }  # fmt: skip
    for d in open_first:
        name = companies.get(d.company_id) if d.company_id else None
        if (name and _in(name, folded, strict)) or _in(d.title.split("·")[0], folded, strict):
            return d.contact_id, d.id
    for c in session.scalars(
        select(Contact).where(Contact.tenant_id == tenant_id).order_by(Contact.created_at.desc()).limit(2000)
    ):  # fmt: skip
        if _in(c.full_name, folded, strict) or (
            c.company_name and _in(c.company_name, folded, strict)
        ):
            deal = next((d for d in open_first if d.contact_id == c.id), None)
            return c.id, deal.id if deal else None
    return None, None
