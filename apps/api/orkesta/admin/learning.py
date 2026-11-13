"""Resumen en palabras simples de lo que ORKESTA sabe y de qué tan bien acierta (panel)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

SOURCE_LABELS = {
    "bases": "Bases que subiste para entrenar",
    "bases_clientes": "Contactos cargados en la plataforma",
    "deducido": "Deducido de correos reales",
    "buscador": "Búsquedas en internet",
    "maps": "Mapas",
    "enriquecimiento": "Enriquecimientos",
    "sitio": "Sitios oficiales",
}
SENT_DAYS = 90


def _n(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _sends(session: Session, tenants: list[Any]) -> dict[str, dict[str, int]]:
    """Resultado de los envíos de los últimos 90 días: estimados y de la base, por separado."""
    from orkesta.campaigns.models import Message, Recipient
    from orkesta.core.db import set_context
    from orkesta.data.models import Contact

    since = datetime.now(UTC) - timedelta(days=SENT_DAYS)
    out = {kind: {"sent": 0, "delivered": 0, "bounced": 0, "blocked": 0, "replied": 0}
           for kind in ("estimated", "base")}  # fmt: skip
    for tenant_id in tenants:
        set_context(session, tenant_id=tenant_id)
        recent = select(Message.recipient_id).where(Message.sent_at >= since)
        rows = session.execute(
            select(Contact.email_status, Recipient.status, Recipient.learned_at.is_not(None),
                   func.count())
            .join(Contact, Contact.id == Recipient.contact_id)
            .where(Recipient.tenant_id == tenant_id, Recipient.sent_count > 0,
                   Recipient.id.in_(recent))
            .group_by(Contact.email_status, Recipient.status, Recipient.learned_at.is_not(None))
        ).all()  # fmt: skip
        for email_status, status, learned, n in rows:
            # Un estimado que rebotó y se re-propuso sigue «estimado»; los demás pasan a inválido.
            kind = "estimated" if email_status == "estimado" else "base"
            bucket = out[kind]
            bucket["sent"] += n
            if status == "bounced":
                bucket["bounced"] += n
            elif status == "blocked":
                bucket["blocked"] += n
            elif status == "replied":
                bucket["replied"] += n
                bucket["delivered"] += n
            elif learned:
                bucket["delivered"] += n
    set_context(session)
    return out


def summary(session: Session) -> dict[str, Any]:
    from orkesta.brain.consolidate import last
    from orkesta.core.db import set_context
    from orkesta.data.models import Company, Contact
    from orkesta.platform.models import Tenant
    from orkesta.research.models import CompanyDirectory, CompanyDomain, PatternStat, PersonIndex

    directory, with_site = session.execute(
        select(func.count(), func.count(CompanyDirectory.domain))
    ).one()
    formats = session.scalar(select(func.count(func.distinct(PatternStat.domain)))) or 0
    seen, delivered, confirmed, bounced = session.execute(
        select(func.coalesce(func.sum(PatternStat.seen), 0),
               func.coalesce(func.sum(PatternStat.delivered), 0),
               func.coalesce(func.sum(PatternStat.confirmed), 0),
               func.coalesce(func.sum(PatternStat.bounced), 0))
    ).one()  # fmt: skip
    people, people_email, people_ok = session.execute(
        select(func.count(), func.count(PersonIndex.email),
               func.count().filter(PersonIndex.email_status.in_(("publicado", "confirmado"))))
    ).one()  # fmt: skip
    sources = [
        {"source": src or "otro", "label": SOURCE_LABELS.get(src or "", src or "Otras fuentes"),
         "companies": int(n)}
        for src, n in session.execute(
            select(CompanyDomain.source, func.count()).group_by(CompanyDomain.source)
            .order_by(func.count().desc())
        ).all()
    ]  # fmt: skip
    tenants = list(session.scalars(select(Tenant.id)))
    contacts = corporate = estimated = no_site = companies = 0
    for tenant_id in tenants:
        set_context(session, tenant_id=tenant_id)
        c, corp, est = session.execute(
            select(func.count(),
                   func.count().filter(Contact.email.is_not(None),
                                       Contact.email_status.notin_(("estimado", "invalido"))),
                   func.count().filter(Contact.email_status == "estimado"))
            .where(Contact.tenant_id == tenant_id)
        ).one()  # fmt: skip
        total, missing = session.execute(
            select(func.count(), func.count().filter(Company.domain.is_(None))).where(
                Company.tenant_id == tenant_id
            )
        ).one()
        contacts += c
        corporate += corp
        estimated += est
        companies += total
        no_site += missing
    sends = _sends(session, tenants)
    est = sends["estimated"]
    bounce_rate = round(100 * est["bounced"] / est["sent"], 1) if est["sent"] else None
    tips = []
    if no_site:
        tips.append(
            f"{_n(no_site)} empresas de los contactos aún no tienen sitio web: sin él no se "
            "puede armar el correo. «Usar toda la data ahora» completa las que ya se "
            "conocen; el resto se busca al enriquecer."
        )
    if bounce_rate is not None and bounce_rate > 5:
        tips.append(
            f"Rebota el {bounce_rate} % de los correos estimados. ORKESTA ya prueba "
            "primero uno por empresa y espera 48 h antes de escribir al resto."
        )
    if est["blocked"] or sends["base"]["blocked"]:
        blocked = est["blocked"] + sends["base"]["blocked"]
        tips.append(
            f"{_n(blocked)} correos los frenó el filtro de la empresa destino (no es que "
            "el correo esté mal): conviene calentar el buzón y enviar menos por día."
        )
    return {
        "knows": {
            "companies": int(directory), "with_site": int(with_site), "formats": int(formats),
            "people": int(people), "people_email": int(people_email),
            "people_verified": int(people_ok),
        },
        "evidence": {"seen": int(seen), "delivered": int(delivered),
                     "confirmed": int(confirmed), "bounced": int(bounced)},
        "sources": sources,
        "platform": {"contacts": contacts, "corporate": corporate, "estimated": estimated,
                     "companies": companies, "companies_no_site": no_site},
        "sends": sends,
        "bounce_rate": bounce_rate,
        "days": SENT_DAYS,
        "tips": tips,
        "last_run": last(session),
    }  # fmt: skip
