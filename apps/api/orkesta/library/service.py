"""Guardar y usar plantillas de equipo."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from orkesta.library.models import TeamTemplate
from orkesta.platform.models import User


class TemplateError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _clean_name(name: str) -> str:
    clean = " ".join(name.split())[:120]
    if len(clean) < 2:
        raise TemplateError("Escribe un nombre para la plantilla.")
    return clean


def campaign_payload(campaign: Any) -> dict[str, Any]:
    """Lo reutilizable de una campaña: textos, tiempos entre pasos y horario (sin audiencia,
    buzón ni resultados de pruebas A/B)."""
    steps = []
    for step in campaign.steps or []:
        clean = {k: step.get(k) for k in ("subject", "body", "delay_days") if k in step}
        if step.get("variant_b"):
            clean["variant_b"] = {k: step["variant_b"].get(k, "") for k in ("subject", "body")}
        steps.append(clean)
    settings = {k: v for k, v in (campaign.settings or {}).items()}
    return {"steps": steps, "settings": settings}


def proposal_payload(proposal: Any) -> dict[str, Any]:
    """Lo reutilizable de una propuesta: contenido, productos y diseño (sin cliente ni datos del
    ejecutivo, que se toman de quien la use)."""
    design = {k: v for k, v in (proposal.design or {}).items()
              if k not in ("executive", "cover_image_data")}  # fmt: skip
    return {
        "title": proposal.title,
        "currency": proposal.currency,
        "items": copy.deepcopy(proposal.items or []),
        "sections": copy.deepcopy(proposal.sections or []),
        "tax_rate": str(proposal.tax_rate),
        "pricing_mode": proposal.pricing_mode,
        "design": design,
    }


def save(
    session: Session, *, tenant_id: uuid.UUID, user: User, kind: str, source_id: uuid.UUID,
    name: str, description: str = "", shared: bool = False,
) -> TeamTemplate:  # fmt: skip
    if kind == "campaign":
        from orkesta.campaigns.models import Campaign

        source: Any = session.get(Campaign, source_id)
        build: Any = campaign_payload
    elif kind == "proposal":
        from orkesta.proposals.models import Proposal

        source = session.get(Proposal, source_id)
        build = proposal_payload
    else:
        raise TemplateError("Tipo de plantilla no válido.")
    if source is None or source.tenant_id != tenant_id or source.created_by not in (user.id, None):
        raise TemplateError("No encontramos lo que quieres guardar.", 404)
    template = TeamTemplate(
        tenant_id=tenant_id, created_by=user.id, kind=kind, name=_clean_name(name),
        description=" ".join(description.split())[:300], payload=build(source), shared=shared,
    )  # fmt: skip
    session.add(template)
    session.flush()
    return template


def get(session: Session, tenant_id: uuid.UUID, template_id: uuid.UUID, kind: str,
        user_id: uuid.UUID | None = None) -> TeamTemplate:  # fmt: skip
    template = session.get(TeamTemplate, template_id)
    if template is None or template.tenant_id != tenant_id or template.kind != kind:
        raise TemplateError("Esa plantilla ya no existe.", 404)
    if not template.shared and template.created_by != user_id:
        raise TemplateError("Esa plantilla ya no existe.", 404)  # personal de otra persona
    return template


def can_manage(template: TeamTemplate, user: User, role: str | None) -> bool:
    """Una plantilla personal solo la maneja quien la guardó; una de equipo, también la
    administración."""
    if not template.shared:
        return template.created_by == user.id
    return role == "admin" or template.created_by == user.id


def listing(
    session: Session, tenant_id: uuid.UUID, kind: str, user: User, role: str | None
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(TeamTemplate, User)
        .outerjoin(User, User.id == TeamTemplate.created_by)
        .where(
            TeamTemplate.tenant_id == tenant_id,
            TeamTemplate.kind == kind,
            or_(TeamTemplate.shared.is_(True), TeamTemplate.created_by == user.id),
        )
        .order_by(TeamTemplate.use_count.desc(), TeamTemplate.created_at.desc())
    ).tuples()
    out = []
    for t, author in rows:
        p = t.payload or {}
        if kind == "campaign":
            steps = p.get("steps") or []
            summary = f"{len(steps)} correo{'s' if len(steps) != 1 else ''}"
            preview = (steps[0].get("subject") or "") if steps else ""
        else:
            items = p.get("items") or []
            summary = (
                f"{len(items)} producto{'s' if len(items) != 1 else ''} · {p.get('currency', '')}"
            )
            preview = p.get("title") or ""
        out.append({
            "id": str(t.id), "name": t.name, "description": t.description, "summary": summary,
            "preview": preview[:160], "use_count": t.use_count,
            "author": (author.full_name or author.email) if author else "—",
            "mine": t.created_by == user.id, "can_manage": can_manage(t, user, role),
            "shared": t.shared,
            "created_at": t.created_at.isoformat(),
        })  # fmt: skip
    return out


def apply_to_campaign(campaign: Any, template: TeamTemplate) -> None:
    payload = template.payload or {}
    if payload.get("steps"):
        campaign.steps = copy.deepcopy(payload["steps"])
    if payload.get("settings"):
        campaign.settings = {**(campaign.settings or {}), **payload["settings"]}
    template.use_count += 1


def apply_to_proposal(proposal: Any, template: TeamTemplate) -> None:
    from decimal import Decimal

    payload = template.payload or {}
    for key in ("currency", "items", "sections", "pricing_mode"):
        if payload.get(key) is not None:
            setattr(proposal, key, copy.deepcopy(payload[key]))
    if payload.get("tax_rate") is not None:
        proposal.tax_rate = Decimal(str(payload["tax_rate"]))
    if not proposal.contact_id and not proposal.deal_id and payload.get("title"):
        proposal.title = str(payload["title"])[:200]
    if payload.get("design"):
        # El diseño de la plantilla, con los datos del ejecutivo de quien la usa.
        proposal.design = {**(proposal.design or {}), **payload["design"]}
    template.use_count += 1
