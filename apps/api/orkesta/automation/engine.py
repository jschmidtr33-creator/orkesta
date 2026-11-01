"""Motor de automatización: catálogo de disparadores y acciones, recetas y ejecución."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.automation.models import Rule, Run
from orkesta.campaigns import gmail, templating
from orkesta.campaigns.models import Campaign, Mailbox, Recipient, Suppression
from orkesta.core.db import set_context
from orkesta.core.jobs import handler
from orkesta.crm import service as crm
from orkesta.crm.models import Deal, Stage
from orkesta.data import service as data_service
from orkesta.data.models import CONTACT_STATUSES, STATUS_LABELS, Contact, ContactList
from orkesta.platform.models import User

logger = logging.getLogger("orkesta.automation")

TRIGGERS: dict[str, dict[str, Any]] = {
    "campaign.replied": {
        "label": "Responde a una campaña",
        "group": "Campañas",
        "conditions": ["campaign_id"],
    },
    "campaign.opened": {
        "label": "Abre un correo de campaña",
        "group": "Campañas",
        "conditions": ["campaign_id", "min_opens"],
    },
    "campaign.clicked": {
        "label": "Hace clic en un enlace del correo",
        "group": "Campañas",
        "conditions": ["campaign_id"],
    },
    "campaign.finished": {
        "label": "Termina la secuencia sin responder",
        "group": "Campañas",
        "conditions": ["campaign_id", "wait_days"],
    },
    "campaign.bounced": {
        "label": "Su correo rebota",
        "group": "Campañas",
        "conditions": ["campaign_id"],
    },
    "campaign.unsubscribed": {
        "label": "Se da de baja",
        "group": "Campañas",
        "conditions": ["campaign_id"],
    },
    "contact.created": {
        "label": "Llega un contacto nuevo",
        "group": "Contactos",
        "conditions": ["source"],
    },
    "deal.stage_changed": {
        "label": "Una oportunidad entra a una etapa",
        "group": "CRM",
        "conditions": ["stage_id"],
    },
    "deal.won": {"label": "Se gana una oportunidad", "group": "CRM", "conditions": []},
    "deal.lost": {"label": "Se pierde una oportunidad", "group": "CRM", "conditions": []},
    "proposal.viewed": {
        "label": "El cliente abre la propuesta",
        "group": "Propuestas",
        "conditions": [],
    },
    "proposal.accepted": {
        "label": "El cliente acepta la propuesta",
        "group": "Propuestas",
        "conditions": [],
    },
    "proposal.rejected": {
        "label": "El cliente rechaza la propuesta",
        "group": "Propuestas",
        "conditions": [],
    },
}
ACTIONS: dict[str, dict[str, Any]] = {
    "create_task": {"label": "Crear una tarea", "fields": ["title", "due_hours"]},
    "create_deal": {
        "label": "Crear una oportunidad (si no tiene una abierta)",
        "fields": ["stage_id", "value"],
    },
    "move_deal": {"label": "Mover su oportunidad a una etapa", "fields": ["stage_id"]},
    "set_status": {"label": "Cambiar el estado del contacto", "fields": ["status"]},
    "add_to_list": {"label": "Agregar a una lista", "fields": ["list_id"]},
    "add_to_campaign": {"label": "Agregar a otra campaña", "fields": ["campaign_id"]},
    "stop_sequences": {"label": "Detener sus secuencias de correo", "fields": []},
    "add_note": {"label": "Agregar una nota al historial", "fields": ["text"]},
    "notify_email": {"label": "Avisarme por correo", "fields": ["text"]},
}
SOURCES = {
    "investigacion": "Prospección",
    "importacion": "Importación",
    "manual": "Manual",
    "enriquecimiento": "Enriquecimiento",
}

RECIPES: list[dict[str, Any]] = [
    {
        "key": "respondio",
        "name": "Respondió → oportunidad y tarea para contestar hoy",
        "trigger": "campaign.replied",
        "conditions": {},
        "actions": [
            {"type": "create_deal", "stage": "Contactado"},
            {
                "type": "create_task",
                "title": "Responder a {{nombre}} ({{empresa}})",
                "due_hours": 4,
            },
        ],
    },
    {
        "key": "interesado",
        "name": "Abrió 3 veces sin responder → llamar",
        "trigger": "campaign.opened",
        "conditions": {"min_opens": 3},
        "actions": [
            {
                "type": "create_task",
                "title": "Llamar a {{nombre}}: abrió el correo 3 veces",
                "due_hours": 24,
            }
        ],
    },
    {
        "key": "clic",
        "name": "Hizo clic en el enlace → avisarme y crear tarea",
        "trigger": "campaign.clicked",
        "conditions": {},
        "actions": [
            {"type": "notify_email", "text": "{{nombre}} de {{empresa}} hizo clic en tu correo."},
            {
                "type": "create_task",
                "title": "Seguimiento a {{nombre}}: hizo clic",
                "due_hours": 24,
            },
        ],
    },
    {
        "key": "nutrir",
        "name": "Terminó la secuencia sin responder → lista «Nutrir»",
        "trigger": "campaign.finished",
        "conditions": {"wait_days": 3},
        "actions": [{"type": "add_to_list", "list": "Nutrir"}],
    },
    {
        "key": "propuesta_vista",
        "name": "Abrió la propuesta → seguimiento en 24 h",
        "trigger": "proposal.viewed",
        "conditions": {},
        "actions": [
            {
                "type": "create_task",
                "title": "Seguimiento de la propuesta a {{nombre}}",
                "due_hours": 24,
            }
        ],
    },
    {
        "key": "propuesta_aceptada",
        "name": "Aceptó la propuesta → avisarme por correo",
        "trigger": "proposal.accepted",
        "conditions": {},
        "actions": [
            {"type": "notify_email", "text": "¡{{nombre}} de {{empresa}} aceptó la propuesta!"}
        ],
    },
    {
        "key": "ganado",
        "name": "Negocio ganado → detener secuencias y tarea de onboarding",
        "trigger": "deal.won",
        "conditions": {},
        "actions": [
            {"type": "stop_sequences"},
            {"type": "create_task", "title": "Kickoff con {{empresa}}", "due_hours": 48},
        ],
    },
]


class AutomationError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


# --- Validación de reglas -------------------------------------------------------------------


def clean_rule(session: Session, tenant_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
    trigger = str(data.get("trigger", ""))
    if trigger not in TRIGGERS:
        raise AutomationError("Elige cuándo se activa la regla.")
    name = " ".join(str(data.get("name", "")).split())[:160] or TRIGGERS[trigger]["label"]
    raw = dict(data.get("conditions") or {})
    conditions: dict[str, Any] = {}
    for key in TRIGGERS[trigger]["conditions"]:
        value = raw.get(key)
        if value in (None, ""):
            continue
        if key in ("min_opens", "wait_days"):
            conditions[key] = max(1 if key == "min_opens" else 0, min(30, int(str(value))))
        elif key == "source":
            if value not in SOURCES:
                raise AutomationError("Origen no válido.")
            conditions[key] = value
        else:
            conditions[key] = str(_owned(session, tenant_id, key, value))
    actions = []
    for raw_action in list(data.get("actions") or [])[:8]:
        kind = str(raw_action.get("type", ""))
        if kind not in ACTIONS:
            raise AutomationError("Hay una acción no válida.")
        action: dict[str, Any] = {"type": kind}
        if kind in ("create_task", "add_note", "notify_email"):
            text = " ".join(str(raw_action.get("title") or raw_action.get("text") or "").split())
            if len(text) < 3:
                raise AutomationError("Escribe el texto de la tarea, nota o aviso.")
            action["title" if kind == "create_task" else "text"] = text[:300]
            if kind == "create_task":
                action["due_hours"] = max(0, min(720, int(raw_action.get("due_hours") or 24)))
        elif kind in ("create_deal", "move_deal"):
            if not raw_action.get("stage_id") and kind == "move_deal":
                raise AutomationError("Elige la etapa.")
            if raw_action.get("stage_id"):
                action["stage_id"] = str(
                    _owned(session, tenant_id, "stage_id", raw_action["stage_id"])
                )
            if kind == "create_deal":
                action["value"] = max(0.0, float(raw_action.get("value") or 0))
        elif kind == "set_status":
            if raw_action.get("status") not in CONTACT_STATUSES:
                raise AutomationError("Elige el estado.")
            action["status"] = raw_action["status"]
        elif kind in ("add_to_list", "add_to_campaign"):
            key = "list_id" if kind == "add_to_list" else "campaign_id"
            if not raw_action.get(key):
                raise AutomationError("Elige la lista o la campaña.")
            action[key] = str(_owned(session, tenant_id, key, raw_action[key]))
        actions.append(action)
    if not actions:
        raise AutomationError("Agrega al menos una acción.")
    return {"name": name, "trigger": trigger, "conditions": conditions, "actions": actions}


def _owned(session: Session, tenant_id: uuid.UUID, key: str, value: Any) -> uuid.UUID:
    models: dict[str, Any] = {"campaign_id": Campaign, "stage_id": Stage, "list_id": ContactList}
    try:
        row: Any = session.get(models[key], uuid.UUID(str(value)))
    except ValueError:
        row = None
    if row is None or row.tenant_id != tenant_id:
        raise AutomationError("Uno de los elementos elegidos ya no existe.", 404)
    return uuid.UUID(str(row.id))


def install_recipe(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, key: str) -> Rule:
    recipe = next((r for r in RECIPES if r["key"] == key), None)
    if recipe is None:
        raise AutomationError("Esa receta no existe.", 404)
    stages = crm.ensure_stages(session, tenant_id)
    actions = []
    for a in recipe["actions"]:
        action = {k: v for k, v in a.items() if k not in ("stage", "list")}
        if "stage" in a:
            stage = next((s for s in stages if s.name.lower() == a["stage"].lower()), None)
            stage = stage or next(s for s in stages if s.kind == "open")
            action["stage_id"] = str(stage.id)
        if "list" in a:
            lst = session.scalar(
                select(ContactList).where(
                    ContactList.tenant_id == tenant_id, ContactList.name == a["list"]
                )
            )
            if lst is None:
                lst = data_service.create_list(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name=a["list"],
                    description="Creada por una automatización",
                )
            action["list_id"] = str(lst.id)
        actions.append(action)
    rule = Rule(
        tenant_id=tenant_id,
        created_by=user_id,
        name=recipe["name"],
        trigger=recipe["trigger"],
        conditions=dict(recipe["conditions"]),
        actions=actions,
    )
    session.add(rule)
    session.flush()
    return rule


# --- Ejecución ------------------------------------------------------------------------------


def _values(contact: Contact | None) -> dict[str, str]:
    if contact is None:
        return {}
    return templating.context(contact, "", "")


def _open_deal(session: Session, contact_id: uuid.UUID | None, deal_id: Any) -> Deal | None:
    if deal_id:
        deal = session.get(Deal, uuid.UUID(str(deal_id)))
        if deal is not None:
            return deal
    if contact_id is None:
        return None
    return session.scalar(
        select(Deal)
        .where(Deal.contact_id == contact_id, Deal.status == "open")
        .order_by(Deal.updated_at.desc())
        .limit(1)
    )


def _assignee(
    session: Session, rule: Rule, contact: Contact | None, deal: Deal | None, ctx: dict[str, Any]
) -> uuid.UUID | None:
    for candidate in (
        deal.owner_user_id if deal else None,
        ctx.get("user_id"),
        contact.owner_user_id if contact else None,
        rule.created_by,
    ):
        if candidate:
            return uuid.UUID(str(candidate))
    return None


def _notify(session: Session, rule: Rule, user_id: uuid.UUID | None, text: str) -> str:
    user = session.get(User, user_id) if user_id else None
    mailbox = session.scalar(
        select(Mailbox).where(
            Mailbox.tenant_id == rule.tenant_id,
            Mailbox.status == "active",
            Mailbox.user_id == user_id,
        )
    ) or session.scalar(
        select(Mailbox).where(Mailbox.tenant_id == rule.tenant_id, Mailbox.status == "active")
    )
    if user is None or mailbox is None:
        raise AutomationError("No hay un Gmail conectado para enviar el aviso.")
    from orkesta.campaigns.service import access_token  # import diferido: evita ciclos

    message = EmailMessage()
    message["From"] = formataddr(("ORKESTA", mailbox.email))
    message["To"] = user.email
    message["Subject"] = f"ORKESTA · {text[:120]}"
    message.set_content(f"{text}\n\nRegla: {rule.name}\nEnviado automáticamente por ORKESTA.")
    gmail.send(access_token(session, mailbox), message.as_bytes())
    return f"Aviso enviado a {user.email}"


def _act(
    session: Session,
    rule: Rule,
    action: dict[str, Any],
    contact: Contact | None,
    ctx: dict[str, Any],
    depth: int,
) -> str:
    kind = action["type"]
    values = _values(contact)
    deal = _open_deal(session, contact.id if contact else None, ctx.get("deal_id"))
    who = _assignee(session, rule, contact, deal, ctx)
    if kind == "create_task":
        title = templating.render(action["title"], values)[0]
        crm.log(
            session,
            tenant_id=rule.tenant_id,
            kind="task",
            user_id=who,
            contact_id=contact.id if contact else None,
            deal_id=deal.id if deal else None,
            body=title,
            due_at=now() + timedelta(hours=int(action.get("due_hours", 24))),
            meta={"automation": str(rule.id)},
        )
        return f"Tarea: {title}"
    if kind == "add_note":
        text = templating.render(action["text"], values)[0]
        crm.log(
            session,
            tenant_id=rule.tenant_id,
            kind="note",
            user_id=None,
            contact_id=contact.id if contact else None,
            deal_id=deal.id if deal else None,
            body=text,
            meta={"automation": str(rule.id)},
        )
        return "Nota agregada"
    if kind == "notify_email":
        return _notify(session, rule, who, templating.render(action["text"], values)[0])
    if contact is None:
        return "Sin contacto: acción omitida"
    if kind == "create_deal":
        if deal is not None and deal.status == "open":
            return "Ya tenía una oportunidad abierta"
        owner = who or contact.owner_user_id or rule.created_by
        if owner is None:
            return "Sin responsable para la oportunidad"
        stage_id = uuid.UUID(action["stage_id"]) if action.get("stage_id") else None
        created = crm.create_deal(
            session,
            tenant_id=rule.tenant_id,
            user_id=owner,
            data=crm.DealInput(
                contact_id=contact.id, stage_id=stage_id, value=action.get("value") or 0
            ),
        )
        return f"Oportunidad creada: {created.title}"
    if kind == "move_deal":
        if deal is None:
            return "No tiene oportunidad abierta"
        crm.move_deal(
            session, deal, stage_id=uuid.UUID(action["stage_id"]), user_id=None, depth=depth + 1
        )
        return "Oportunidad movida"
    if kind == "set_status":
        contact.status = action["status"]
        return f"Estado: {STATUS_LABELS.get(action['status'], action['status'])}"
    if kind == "add_to_list":
        data_service.add_to_list(
            session,
            tenant_id=rule.tenant_id,
            list_id=uuid.UUID(action["list_id"]),
            contact_ids=[contact.id],
        )
        return "Agregado a la lista"
    if kind == "add_to_campaign":
        campaign = session.get(Campaign, uuid.UUID(action["campaign_id"]))
        if campaign is None or campaign.status not in ("active", "paused"):
            return "La campaña no está activa"
        if not contact.email or contact.email_status == "invalido":
            return "El contacto no tiene correo válido"
        if session.scalar(
            select(Suppression.id).where(
                Suppression.tenant_id == rule.tenant_id, Suppression.email == contact.email
            )
        ):
            return "El contacto se dio de baja"
        exists = session.scalar(
            select(Recipient.id).where(
                Recipient.campaign_id == campaign.id, Recipient.contact_id == contact.id
            )
        )
        if exists:
            return "Ya estaba en la campaña"
        session.add(
            Recipient(
                tenant_id=rule.tenant_id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                email=contact.email,
                next_send_at=now(),
            )
        )
        return f"Agregado a «{campaign.name}»"
    if kind == "stop_sequences":
        stopped = 0
        for r in session.scalars(
            select(Recipient).where(
                Recipient.contact_id == contact.id, Recipient.status.in_(("pending", "active"))
            )
        ):
            r.status, r.next_send_at = "skipped", None
            stopped += 1
        return f"{stopped} secuencias detenidas"
    return "Acción desconocida"


def execute(
    session: Session,
    rule: Rule,
    trigger: str,
    ctx: dict[str, Any],
    depth: int,
    contact_id: uuid.UUID | None,
) -> Run:
    contact = session.get(Contact, contact_id) if contact_id else None
    if trigger == "campaign.finished" and ctx.get("recipient_id"):
        recipient = session.get(Recipient, uuid.UUID(str(ctx["recipient_id"])))
        if recipient is None or recipient.status != "completed":
            run = Run(
                tenant_id=rule.tenant_id,
                rule_id=rule.id,
                trigger=trigger,
                contact_id=contact_id,
                status="skipped",
                results=[{"ok": True, "detail": "Respondió o se detuvo antes: no aplica"}],
            )
            session.add(run)
            return run
    results, failed = [], False
    for action in rule.actions:
        try:
            with session.begin_nested():
                detail = _act(session, rule, action, contact, ctx, depth)
            results.append({"type": action["type"], "ok": True, "detail": detail})
        except Exception as exc:
            failed = True
            message = (
                str(exc) if isinstance(exc, ValueError | gmail.GmailError) else "Error inesperado"
            )
            logger.warning("automation_action_failed %s %s", rule.id, exc)
            results.append({"type": action["type"], "ok": False, "detail": message[:300]})
    rule.run_count += 1
    rule.last_run_at = now()
    run = Run(
        tenant_id=rule.tenant_id,
        rule_id=rule.id,
        trigger=trigger,
        contact_id=contact_id,
        status="error" if failed else "ok",
        results=results,
    )
    session.add(run)
    return run


@handler("automation.run")
def run_job(factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any]) -> None:
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        rule = session.get(Rule, uuid.UUID(payload["rule_id"]))
        if rule is None or not rule.enabled:
            return
        ctx = dict(payload.get("ctx") or {})
        ids = payload.get("contact_ids") or (
            [ctx["contact_id"]] if ctx.get("contact_id") else [None]
        )
        for cid in ids:
            execute(
                session,
                rule,
                payload["trigger"],
                ctx,
                int(payload.get("depth", 0)),
                uuid.UUID(str(cid)) if cid else None,
            )
