"""Propuestas comerciales: generador de borradores, cálculo de totales, envío y enlace público.

El borrador lo arma el motor propio de ORKESTA (sin IA externa) con los datos reales del CRM:
contacto, empresa, industria, valor de la oportunidad, notas de llamadas y reuniones, y los
productos del catálogo. Cada sección queda editable.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from orkesta.core.db import set_context
from orkesta.crm.models import CURRENCIES, Activity, Deal, Stage
from orkesta.crm.service import log as crm_log
from orkesta.crm.service import move_deal
from orkesta.data.models import Company, Contact
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS
from orkesta.platform.models import Tenant, User
from orkesta.proposals.models import STATUS_LABELS, Product, Proposal, ProposalLink

CENT = Decimal("0.01")


class ProposalError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def _money(value: Any, field: str = "valor") -> Decimal:
    try:
        amount = Decimal(str(value if value not in (None, "") else 0).replace(",", ""))
    except InvalidOperation as exc:
        raise ProposalError(f"El {field} debe ser un número.") from exc
    if amount < 0 or amount >= Decimal("1e12"):
        raise ProposalError(f"El {field} no es válido.")
    return amount.quantize(CENT, ROUND_HALF_UP)


# --- Catálogo -------------------------------------------------------------------------------


def product_dict(p: Product) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "unit_price": float(p.unit_price),
        "currency": p.currency,
        "unit": p.unit,
        "active": p.active,
    }


def save_product(session: Session, tenant_id: uuid.UUID, data: dict[str, Any],
                 product: Product | None = None) -> Product:  # fmt: skip
    name = " ".join(str(data.get("name", "")).split())[:160]
    if len(name) < 2:
        raise ProposalError("Escribe el nombre del producto o servicio.")
    currency = str(data.get("currency") or "USD").upper()
    if currency not in CURRENCIES:
        raise ProposalError("Moneda no soportada.")
    duplicate = session.scalar(
        select(Product.id).where(
            Product.tenant_id == tenant_id,
            func.lower(Product.name) == name.lower(),
            Product.id != (product.id if product else uuid.uuid4()),
        )
    )
    if duplicate:
        raise ProposalError("Ya existe un producto con ese nombre.", 409)
    if product is None:
        product = Product(tenant_id=tenant_id, name=name)
        session.add(product)
    product.name = name
    product.description = str(data.get("description", ""))[:2000]
    product.unit_price = _money(data.get("unit_price"), "precio")
    product.currency = currency
    product.unit = " ".join(str(data.get("unit") or "unidad").split())[:40]
    product.active = bool(data.get("active", True))
    session.flush()
    return product


# --- Cálculos -------------------------------------------------------------------------------


def clean_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in items[:100]:
        name = " ".join(str(raw.get("name", "")).split())[:200]
        if not name:
            continue
        quantity = _money(raw.get("quantity", 1), "cantidad")
        discount = _money(raw.get("discount", 0), "descuento")
        if discount > 100:
            raise ProposalError("El descuento no puede superar el 100 %.")
        out.append(
            {
                "name": name,
                "description": str(raw.get("description", ""))[:2000],
                "quantity": float(quantity),
                "unit": " ".join(str(raw.get("unit") or "").split())[:40],
                "unit_price": float(_money(raw.get("unit_price", 0), "precio")),
                "discount": float(discount),
            }
        )
    return out


def totals(items: list[dict[str, Any]], tax_rate: Decimal | float) -> dict[str, float]:
    subtotal = Decimal(0)
    discounts = Decimal(0)
    for item in items:
        gross = Decimal(str(item["quantity"])) * Decimal(str(item["unit_price"]))
        off = gross * Decimal(str(item.get("discount", 0))) / 100
        subtotal += gross - off
        discounts += off
    tax = subtotal * Decimal(str(tax_rate)) / 100

    def q(value: Decimal) -> float:
        return float(value.quantize(CENT, ROUND_HALF_UP))

    return {
        "subtotal": q(subtotal),
        "discounts": q(discounts),
        "tax": q(tax),
        "total": q(subtotal + tax),
    }


# --- Generador de borradores (motor propio) -------------------------------------------------

INDUSTRY_CONTEXT = {
    "banca_finanzas": "En el sector financiero la seguridad, el cumplimiento regulatorio y la "
    "experiencia digital del cliente marcan la diferencia frente a la competencia.",
    "seguros": "En seguros, la eficiencia en la suscripción, la atención de siniestros y la "
    "relación con canales y asegurados son claves para crecer con rentabilidad.",
    "telecomunicaciones": "En telecomunicaciones, la retención de clientes, la calidad del "
    "servicio y la velocidad para lanzar ofertas definen el crecimiento.",
    "tecnologia_software": "En tecnología, escalar con eficiencia, acelerar la salida al mercado "
    "y sostener la calidad son las prioridades del equipo directivo.",
    "farmaceutica_salud": "En salud y farmacéutica, el cumplimiento, la trazabilidad y la "
    "relación con profesionales y pacientes son determinantes.",
    "retail_consumo": "En retail y consumo, la experiencia omnicanal, la rotación de inventario "
    "y el conocimiento del cliente impulsan las ventas.",
    "alimentos_bebidas": "En alimentos y bebidas, la eficiencia de la cadena de suministro, la "
    "calidad y la presencia en el punto de venta son decisivas.",
    "energia": "En energía, la eficiencia operativa, la seguridad y la transición energética "
    "orientan las inversiones.",
    "mineria_petroleo": "En minería e hidrocarburos, la seguridad, la productividad y la "
    "sostenibilidad son los ejes de cada decisión.",
    "transporte_logistica": "En logística, la visibilidad de la operación, los tiempos de entrega "
    "y los costos por envío determinan la competitividad.",
    "construccion_inmobiliario": "En construcción e inmobiliario, el control de costos, los "
    "tiempos de obra y la experiencia del comprador son prioritarios.",
    "educacion": "En educación, la experiencia del estudiante, la retención y la eficiencia "
    "administrativa son las metas principales.",
    "gobierno": "En el sector público, la transparencia, la eficiencia y el servicio al ciudadano "
    "guían cada proyecto.",
}
GENERIC_CONTEXT = (
    "Entendemos que el objetivo es crecer con eficiencia, con resultados medibles desde las "
    "primeras semanas."
)


@dataclass
class Context:
    tenant: str
    contact_first: str
    contact_name: str
    company: str
    industry: str
    deal_title: str
    owner: str
    owner_email: str
    notes: list[str]


def _context(session: Session, tenant_id: uuid.UUID, deal: Deal | None,
             contact: Contact | None, user: User | None) -> Context:  # fmt: skip
    tenant = session.get(Tenant, tenant_id)
    company = session.get(Company, deal.company_id) if deal and deal.company_id else None
    notes: list[str] = []
    if deal is not None or contact is not None:
        query = select(Activity.body).where(
            Activity.tenant_id == tenant_id,
            Activity.type.in_(("note", "call", "meeting")),
        )
        if deal is not None:
            query = query.where(Activity.deal_id == deal.id)
        elif contact is not None:
            query = query.where(Activity.contact_id == contact.id)
        notes = [b for b in session.scalars(query.order_by(Activity.created_at.desc()).limit(4))]
    owner = session.get(User, deal.owner_user_id) if deal and deal.owner_user_id else user
    return Context(
        tenant=tenant.name if tenant else "",
        contact_first=(contact.first_name if contact else "") or "",
        contact_name=(contact.full_name if contact else "") or "",
        company=(company.name if company else (contact.company_name if contact else "")) or "",
        industry=(company.industry_code if company else (contact.industry_code if contact else ""))
        or "",
        deal_title=deal.title if deal else "",
        owner=(owner.full_name or owner.email) if owner else "",
        owner_email=owner.email if owner else "",
        notes=notes,
    )


def draft_sections(ctx: Context, items: list[dict[str, Any]], valid_until: date | None,
                   currency: str) -> list[dict[str, str]]:  # fmt: skip
    hello = f"Hola, {ctx.contact_first}:" if ctx.contact_first else "Hola:"
    company = ctx.company or "su organización"
    sector = INDUSTRY_LABELS.get(ctx.industry, "")
    intro = (
        f"{hello}\n\nGracias por el tiempo y la confianza. En esta propuesta, "
        f"{ctx.tenant or 'nuestro equipo'} presenta cómo podemos acompañar a {company}"
        + (f" en «{ctx.deal_title}»" if ctx.deal_title else "")
        + ", con un alcance claro, entregables concretos y una inversión transparente."
    )
    need = INDUSTRY_CONTEXT.get(ctx.industry, GENERIC_CONTEXT)
    if sector and ctx.industry not in INDUSTRY_CONTEXT:
        need = f"Para el sector de {sector.lower()}: {need}"
    if ctx.notes:
        bullets = "\n".join(f"• {n.strip()[:300]}" for n in ctx.notes if n.strip())
        need += f"\n\nEn nuestras conversaciones identificamos:\n{bullets}"
    if items:
        solution = "Proponemos la siguiente solución:\n" + "\n".join(
            f"• {i['name']}" + (f": {i['description'].strip()}" if i.get("description") else "")
            for i in items
        )
        scope = "\n".join(
            f"• {i['name']} — {i['quantity']:g} {i.get('unit') or ''}".rstrip() for i in items
        )
    else:
        solution = "Describe aquí la solución propuesta y el valor que genera para el cliente."
        scope = "• Entregable 1\n• Entregable 2\n• Entregable 3"
    deadline = valid_until.strftime("%d/%m/%Y") if valid_until else "la fecha indicada"
    steps = (
        "1. Revisión de esta propuesta y resolución de dudas.\n"
        "2. Aceptación desde este mismo documento.\n"
        "3. Reunión de arranque y plan de trabajo detallado en la primera semana."
    )
    terms = (
        f"• Valores expresados en {currency}, más impuestos aplicables."
        + f"\n• Propuesta válida hasta {deadline}."
        "\n• Forma de pago: 50 % al inicio y 50 % contra entrega, salvo acuerdo distinto."
        "\n• La información compartida se trata de forma confidencial."
    )
    contact_line = f"\n\n{ctx.owner}" + (f" · {ctx.owner_email}" if ctx.owner_email else "")
    return [
        {"key": "resumen", "title": "Resumen", "body": intro},
        {"key": "necesidad", "title": "Entendimiento de la necesidad", "body": need},
        {"key": "solucion", "title": "Solución propuesta", "body": solution},
        {"key": "alcance", "title": "Alcance y entregables", "body": scope},
        {"key": "pasos", "title": "Próximos pasos", "body": steps + contact_line},
        {"key": "terminos", "title": "Términos y condiciones", "body": terms},
    ]


# --- Propuestas -----------------------------------------------------------------------------


def _next_number(session: Session, tenant_id: uuid.UUID) -> int:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"proposal-number:{tenant_id}"}
    )
    current = session.scalar(
        select(func.coalesce(func.max(Proposal.number), 0)).where(Proposal.tenant_id == tenant_id)
    )
    return int(current or 0) + 1


def create_proposal(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user: User,
    deal_id: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    product_ids: list[uuid.UUID],
) -> Proposal:
    deal = session.get(Deal, deal_id) if deal_id else None
    if deal_id and (deal is None or deal.tenant_id != tenant_id):
        raise ProposalError("Esa oportunidad no existe.", 404)
    contact_id = contact_id or (deal.contact_id if deal else None)
    contact = session.get(Contact, contact_id) if contact_id else None
    if contact_id and (contact is None or contact.tenant_id != tenant_id):
        raise ProposalError("Ese contacto no existe.", 404)
    currency = deal.currency if deal else "USD"
    items: list[dict[str, Any]] = []
    if product_ids:
        products = session.scalars(
            select(Product).where(Product.tenant_id == tenant_id, Product.id.in_(product_ids))
        ).all()
        items = [
            {"name": p.name, "description": p.description, "quantity": 1.0, "unit": p.unit,
             "unit_price": float(p.unit_price), "discount": 0.0}
            for p in products
        ]  # fmt: skip
        if products:
            currency = products[0].currency
    elif deal is not None and deal.value > 0:
        items = [{"name": deal.title, "description": "", "quantity": 1.0, "unit": "",
                  "unit_price": float(deal.value), "discount": 0.0}]  # fmt: skip
    valid_until = (now() + timedelta(days=30)).date()
    ctx = _context(session, tenant_id, deal, contact, user)
    title = f"Propuesta para {ctx.company}" if ctx.company else "Propuesta comercial"
    proposal = Proposal(
        tenant_id=tenant_id,
        number=_next_number(session, tenant_id),
        deal_id=deal.id if deal else None,
        contact_id=contact.id if contact else None,
        created_by=user.id,
        title=title[:200],
        currency=currency,
        items=items,
        sections=draft_sections(ctx, items, valid_until, currency),
        tax_rate=Decimal(0),
        valid_until=valid_until,
        public_id=secrets.token_urlsafe(24),
    )
    session.add(proposal)
    session.flush()
    session.add(ProposalLink(public_id=proposal.public_id, tenant_id=tenant_id,
                             proposal_id=proposal.id))  # fmt: skip
    if deal is not None or contact is not None:
        crm_log(
            session, tenant_id=tenant_id, kind="system", user_id=user.id,
            contact_id=contact.id if contact else None, deal_id=deal.id if deal else None,
            body=f"Creó la propuesta N.º {proposal.number}: «{proposal.title}».",
        )  # fmt: skip
    return proposal


def get_proposal(session: Session, tenant_id: uuid.UUID, proposal_id: uuid.UUID) -> Proposal:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None or proposal.tenant_id != tenant_id:
        raise ProposalError("Esa propuesta no existe.", 404)
    return proposal


def update_proposal(session: Session, proposal: Proposal, data: dict[str, Any]) -> Proposal:
    if proposal.status in ("accepted", "rejected"):
        raise ProposalError(
            "La propuesta ya fue respondida por el cliente; duplícala para cambiarla.", 409
        )
    if "title" in data:
        title = " ".join(str(data["title"]).split())[:200]
        if not title:
            raise ProposalError("La propuesta necesita un título.")
        proposal.title = title
    if "currency" in data:
        currency = str(data["currency"]).upper()
        if currency not in CURRENCIES:
            raise ProposalError("Moneda no soportada.")
        proposal.currency = currency
    if "items" in data:
        proposal.items = clean_items(list(data["items"] or []))
    if "sections" in data:
        proposal.sections = [
            {
                "key": str(s.get("key") or f"s{i}")[:30],
                "title": " ".join(str(s.get("title", "")).split())[:120],
                "body": str(s.get("body", ""))[:10000],
            }
            for i, s in enumerate(list(data["sections"] or [])[:20])
            if str(s.get("title", "")).strip()
        ]
    if "tax_rate" in data:
        rate = _money(data["tax_rate"], "impuesto")
        if rate > 100:
            raise ProposalError("El impuesto no puede superar el 100 %.")
        proposal.tax_rate = rate
    if "valid_until" in data:
        proposal.valid_until = data["valid_until"]
    return proposal


def regenerate(session: Session, proposal: Proposal, user: User) -> Proposal:
    deal = session.get(Deal, proposal.deal_id) if proposal.deal_id else None
    contact = session.get(Contact, proposal.contact_id) if proposal.contact_id else None
    ctx = _context(session, proposal.tenant_id, deal, contact, user)
    proposal.sections = draft_sections(ctx, proposal.items, proposal.valid_until,
                                       proposal.currency)  # fmt: skip
    return proposal


def mark_sent(session: Session, proposal: Proposal, user: User) -> Proposal:
    if proposal.status == "draft":
        proposal.status = "sent"
    proposal.sent_at = proposal.sent_at or now()
    deal = session.get(Deal, proposal.deal_id) if proposal.deal_id else None
    if deal is not None and deal.status == "open":
        stages = session.scalars(
            select(Stage).where(Stage.tenant_id == deal.tenant_id).order_by(Stage.position)
        ).all()
        target = next(
            (s for s in stages if "propuesta" in s.name.lower() and s.kind == "open"), None
        )
        current = next((s for s in stages if s.id == deal.stage_id), None)
        if target and current and current.position < target.position:
            move_deal(session, deal, stage_id=target.id, user_id=user.id)
    crm_log(
        session, tenant_id=proposal.tenant_id, kind="system", user_id=user.id,
        contact_id=proposal.contact_id, deal_id=proposal.deal_id,
        body=f"Envió la propuesta N.º {proposal.number} al cliente.",
    )  # fmt: skip
    return proposal


def duplicate(session: Session, proposal: Proposal, user: User) -> Proposal:
    copy = Proposal(
        tenant_id=proposal.tenant_id,
        number=_next_number(session, proposal.tenant_id),
        deal_id=proposal.deal_id,
        contact_id=proposal.contact_id,
        created_by=user.id,
        title=proposal.title,
        currency=proposal.currency,
        items=list(proposal.items),
        sections=list(proposal.sections),
        tax_rate=proposal.tax_rate,
        valid_until=(now() + timedelta(days=30)).date(),
        public_id=secrets.token_urlsafe(24),
    )
    session.add(copy)
    session.flush()
    session.add(ProposalLink(public_id=copy.public_id, tenant_id=copy.tenant_id,
                             proposal_id=copy.id))  # fmt: skip
    return copy


def proposal_dict(session: Session, p: Proposal, *, full: bool = True) -> dict[str, Any]:
    contact = session.get(Contact, p.contact_id) if p.contact_id else None
    deal = session.get(Deal, p.deal_id) if p.deal_id else None
    company = session.get(Company, deal.company_id) if deal and deal.company_id else None
    data: dict[str, Any] = {
        "id": str(p.id),
        "number": p.number,
        "title": p.title,
        "status": p.status,
        "status_label": STATUS_LABELS[p.status],
        "currency": p.currency,
        "totals": totals(p.items, p.tax_rate),
        "valid_until": p.valid_until.isoformat() if p.valid_until else None,
        "public_id": p.public_id,
        "contact": {"id": str(contact.id), "full_name": contact.full_name, "email": contact.email}
        if contact
        else None,
        "company": (company.name if company else (contact.company_name if contact else None)),
        "deal": {"id": str(deal.id), "title": deal.title} if deal else None,
        "view_count": p.view_count,
        "first_viewed_at": p.first_viewed_at.isoformat() if p.first_viewed_at else None,
        "last_viewed_at": p.last_viewed_at.isoformat() if p.last_viewed_at else None,
        "sent_at": p.sent_at.isoformat() if p.sent_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "decided_by": p.decided_by,
        "decision_note": p.decision_note,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if full:
        data.update(items=p.items, sections=p.sections, tax_rate=float(p.tax_rate))
    return data


def list_proposals(session: Session, tenant_id: uuid.UUID, status: str = "",
                   deal_id: uuid.UUID | None = None) -> list[dict[str, Any]]:  # fmt: skip
    query = select(Proposal).where(Proposal.tenant_id == tenant_id)
    if status in STATUS_LABELS:
        query = query.where(Proposal.status == status)
    if deal_id:
        query = query.where(Proposal.deal_id == deal_id)
    rows = session.scalars(query.order_by(Proposal.created_at.desc()).limit(300)).all()
    return [proposal_dict(session, p, full=False) for p in rows]


# --- Enlace público -------------------------------------------------------------------------


def public_load(session: Session, public_id: str) -> Proposal:
    link = session.get(ProposalLink, public_id[:48])
    if link is None:
        raise ProposalError("Esta propuesta no existe o ya no está disponible.", 404)
    set_context(session, tenant_id=link.tenant_id)
    proposal = session.get(Proposal, link.proposal_id)
    if proposal is None:
        raise ProposalError("Esta propuesta no existe o ya no está disponible.", 404)
    return proposal


def public_view(session: Session, proposal: Proposal, *, record: bool) -> dict[str, Any]:
    if record:
        proposal.view_count += 1
        proposal.last_viewed_at = now()
        if proposal.first_viewed_at is None:
            proposal.first_viewed_at = proposal.last_viewed_at
            crm_log(
                session, tenant_id=proposal.tenant_id, kind="system", user_id=None,
                contact_id=proposal.contact_id, deal_id=proposal.deal_id,
                body=f"El cliente abrió la propuesta N.º {proposal.number} por primera vez.",
            )  # fmt: skip
        if proposal.status == "sent":
            proposal.status = "viewed"
    tenant = session.get(Tenant, proposal.tenant_id)
    owner = session.get(User, proposal.created_by) if proposal.created_by else None
    data = proposal_dict(session, proposal)
    expired = bool(proposal.valid_until and proposal.valid_until < now().date())
    return {
        "number": data["number"],
        "title": data["title"],
        "status": data["status"],
        "currency": data["currency"],
        "items": data["items"],
        "sections": data["sections"],
        "tax_rate": data["tax_rate"],
        "totals": data["totals"],
        "valid_until": data["valid_until"],
        "expired": expired,
        "company": data["company"],
        "contact_name": data["contact"]["full_name"] if data["contact"] else None,
        "issuer": tenant.name if tenant else "",
        "owner": {"name": owner.full_name or owner.email, "email": owner.email} if owner else None,
        "decided_at": data["decided_at"],
        "decided_by": data["decided_by"],
        "created_at": data["created_at"],
    }


def decide(session: Session, proposal: Proposal, *, accept: bool, name: str, note: str) -> None:
    if proposal.status in ("accepted", "rejected"):
        raise ProposalError("Esta propuesta ya fue respondida.", 409)
    if proposal.status == "draft":
        raise ProposalError("Esta propuesta todavía no ha sido enviada.", 409)
    if proposal.valid_until and proposal.valid_until < now().date():
        raise ProposalError("Esta propuesta venció. Pide una actualización a tu asesor.", 409)
    who = " ".join(name.split())[:160]
    if accept and len(who) < 3:
        raise ProposalError("Escribe tu nombre completo para aceptar.")
    proposal.status = "accepted" if accept else "rejected"
    proposal.decided_at = now()
    proposal.decided_by = who or None
    proposal.decision_note = note.strip()[:500] or None
    verb = "aceptó" if accept else "rechazó"
    body = f"{who or 'El cliente'} {verb} la propuesta N.º {proposal.number}."
    if proposal.decision_note:
        body += f" Comentario: {proposal.decision_note}"
    crm_log(session, tenant_id=proposal.tenant_id, kind="system", user_id=None,
            contact_id=proposal.contact_id, deal_id=proposal.deal_id, body=body)  # fmt: skip
    deal = session.get(Deal, proposal.deal_id) if proposal.deal_id else None
    if deal is not None and deal.status == "open" and accept:
        won = session.scalar(
            select(Stage).where(Stage.tenant_id == deal.tenant_id, Stage.kind == "won")
            .order_by(Stage.position).limit(1)
        )  # fmt: skip
        if won is not None:
            move_deal(session, deal, stage_id=won.id, user_id=None)
