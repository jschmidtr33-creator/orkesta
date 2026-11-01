"""API de campañas: conexión de Gmail, campañas, destinatarios y seguimiento público (/t/…)."""

from __future__ import annotations

import base64
import secrets
import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from orkesta.campaigns import gmail, templating
from orkesta.campaigns import service as svc
from orkesta.campaigns.models import Campaign, EmailAsset, Mailbox, Recipient, Suppression
from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.security import read_state, sign_state
from orkesta.core.web import Db, Principal, RateLimiter, client_ip, require_roles
from orkesta.data.models import Contact
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.platform.service import audit

router = APIRouter()
tracking = APIRouter(include_in_schema=False)
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
Manager = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]
STATE_COOKIE = "ork_mail_state"
INTEGRATIONS = "/app/configuracion/integraciones"
public_limiter = RateLimiter(limit=300, window_seconds=600)
PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _fail(exc: svc.CampaignError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


# --- Gmail ----------------------------------------------------------------------------------


@router.get("/api/mail/google/start")
def mail_start(principal: Member) -> Response:
    if not gmail.enabled():
        return RedirectResponse(f"{INTEGRATIONS}?gmail=pendiente", status_code=303)
    nonce = secrets.token_urlsafe(16)
    state = sign_state({"uid": str(principal.user.id), "tid": str(principal.tenant_id), "n": nonce})
    response = RedirectResponse(
        gmail.authorization_url(state, principal.user.email), status_code=303
    )
    response.set_cookie(
        STATE_COOKIE,
        nonce,
        httponly=True,
        secure=get_settings().is_deployed,
        samesite="lax",
        max_age=600,
        path="/api/mail/google",
    )
    return response


@router.get("/api/mail/google/callback")
def mail_callback(
    request: Request, db: Db, code: str = "", state: str = "", error: str = ""
) -> Response:
    def back(result: str) -> Response:
        response = RedirectResponse(f"{INTEGRATIONS}?gmail={result}", status_code=303)
        response.delete_cookie(STATE_COOKIE, path="/api/mail/google")
        return response

    if error or not code:
        return back("cancelado")
    data = read_state(state)
    if not data or not data.get("n") or data.get("n") != request.cookies.get(STATE_COOKIE):
        return back("error")
    try:
        connected = gmail.exchange_code(code)
    except gmail.GmailError:
        return back("error")
    tenant_id, user_id = uuid.UUID(data["tid"]), uuid.UUID(data["uid"])
    set_context(db, tenant_id=tenant_id)
    mailbox = svc.save_mailbox(db, tenant_id=tenant_id, user_id=user_id, connected=connected)
    audit(
        db,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="mailbox.connected",
        entity_type="mailbox",
        entity_id=mailbox.id,
        ip=client_ip(request),
        email=mailbox.email,
    )
    return back("conectado")


class MailboxPatch(BaseModel):
    daily_limit: int | None = Field(default=None, ge=1, le=2000)  # tope de Gmail Workspace
    signature: str | None = Field(default=None, max_length=50000)
    display_name: str | None = Field(default=None, max_length=160)


def _mailbox(db: Db, principal: Principal, mailbox_id: uuid.UUID) -> Mailbox:
    mailbox = db.get(Mailbox, mailbox_id)
    if mailbox is None or mailbox.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Ese buzón no existe.")
    if principal.role == "vendedor" and mailbox.user_id != principal.user.id:
        raise HTTPException(403, "Solo puedes cambiar tu propio buzón.")
    return mailbox


@router.get("/api/mailboxes")
def mailboxes(principal: Member, db: Db) -> dict[str, Any]:
    rows = db.scalars(
        select(Mailbox).where(Mailbox.tenant_id == principal.tenant_id).order_by(Mailbox.created_at)
    ).all()
    tz = svc._tz("America/Bogota")
    return {
        "enabled": gmail.enabled(),
        "items": [svc.mailbox_dict(m, svc.sent_today(db, m.id, tz)) for m in rows],
    }


@router.patch("/api/mailboxes/{mailbox_id}")
def update_mailbox(
    mailbox_id: uuid.UUID, body: MailboxPatch, principal: Member, db: Db
) -> dict[str, Any]:
    mailbox = _mailbox(db, principal, mailbox_id)
    if body.daily_limit is not None:
        mailbox.daily_limit = body.daily_limit
    if body.signature is not None:
        signature = body.signature.strip()
        mailbox.signature = (
            templating.clean_html(signature) if templating.is_html(signature) else signature
        )
    if body.display_name is not None:
        mailbox.display_name = " ".join(body.display_name.split())
    return svc.mailbox_dict(mailbox)


@router.delete("/api/mailboxes/{mailbox_id}")
def delete_mailbox(
    mailbox_id: uuid.UUID, request: Request, principal: Member, db: Db
) -> dict[str, bool]:
    mailbox = _mailbox(db, principal, mailbox_id)
    for campaign in db.scalars(select(Campaign).where(Campaign.mailbox_id == mailbox.id)):
        if campaign.status == "active":
            campaign.status = "paused"
    audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user.id,
        action="mailbox.removed",
        entity_type="mailbox",
        entity_id=mailbox.id,
        ip=client_ip(request),
        email=mailbox.email,
    )
    db.delete(mailbox)
    return {"ok": True}


# --- Campañas -------------------------------------------------------------------------------


class CampaignIn(BaseModel):
    name: str = Field(max_length=160)


class StepIn(BaseModel):
    subject: str = Field(default="", max_length=250)
    body: str = Field(default="", max_length=100000)
    delay_days: int = Field(default=3, ge=0, le=60)


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    steps: list[StepIn] | None = Field(default=None, max_length=svc.MAX_STEPS)
    settings: dict[str, Any] | None = None
    audience: dict[str, Any] | None = None
    mailbox_id: uuid.UUID | None = None


class AudienceIn(BaseModel):
    audience: dict[str, Any]


class PreviewIn(BaseModel):
    step: int = Field(default=0, ge=0, lt=svc.MAX_STEPS)
    contact_id: uuid.UUID | None = None


def _campaign(db: Db, principal: Principal, campaign_id: uuid.UUID) -> Campaign:
    try:
        return svc.get_campaign(db, principal.tenant_id, campaign_id)
    except svc.CampaignError as exc:
        raise _fail(exc) from exc


@router.get("/api/campaigns")
def campaigns(principal: Member, db: Db) -> dict[str, Any]:
    rows = db.scalars(
        select(Campaign)
        .where(Campaign.tenant_id == principal.tenant_id)
        .order_by(Campaign.created_at.desc())
        .limit(200)
    ).all()
    return {
        "items": [svc.campaign_dict(db, c, full=False) for c in rows],
        "variables": templating.VARIABLES,
        "gmail_enabled": gmail.enabled(),
    }


@router.post("/api/campaigns", status_code=201)
def create(body: CampaignIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    try:
        campaign = svc.create_campaign(
            db, tenant_id=principal.tenant_id, user=principal.user, name=body.name
        )
    except svc.CampaignError as exc:
        raise _fail(exc) from exc
    audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user.id,
        action="campaign.created",
        entity_type="campaign",
        entity_id=campaign.id,
        ip=client_ip(request),
    )
    return svc.campaign_dict(db, campaign)


@router.get("/api/campaigns/{campaign_id}")
def detail(campaign_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    return svc.campaign_dict(db, _campaign(db, principal, campaign_id))


@router.patch("/api/campaigns/{campaign_id}")
def update(
    campaign_id: uuid.UUID, body: CampaignPatch, principal: Member, db: Db
) -> dict[str, Any]:
    campaign = _campaign(db, principal, campaign_id)
    data = body.model_dump(exclude_unset=True)
    if "steps" in data:
        data["steps"] = [s.model_dump() for s in body.steps or []]
    try:
        svc.update_campaign(db, campaign, data)
    except svc.CampaignError as exc:
        raise _fail(exc) from exc
    return svc.campaign_dict(db, campaign)


@router.delete("/api/campaigns/{campaign_id}")
def delete(campaign_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    campaign = _campaign(db, principal, campaign_id)
    if campaign.status == "active":
        raise HTTPException(409, "Pausa la campaña antes de eliminarla.")
    db.delete(campaign)
    return {"ok": True}


@router.post("/api/campaigns/audience")
def audience(body: AudienceIn, principal: Member, db: Db) -> dict[str, Any]:
    return svc.audience_preview(db, principal.tenant_id, body.audience)


@router.post("/api/campaigns/{campaign_id}/preview")
def preview(campaign_id: uuid.UUID, body: PreviewIn, principal: Member, db: Db) -> dict[str, Any]:
    campaign = _campaign(db, principal, campaign_id)
    if body.step >= len(campaign.steps):
        raise HTTPException(400, "Ese paso no existe.")
    contact = db.get(Contact, body.contact_id) if body.contact_id else None
    if contact is None or contact.tenant_id != principal.tenant_id:
        contact = db.scalars(
            svc._audience_query(principal.tenant_id, campaign.audience)
            .where(Contact.email.is_not(None))
            .limit(1)
        ).first()
    mailbox = db.get(Mailbox, campaign.mailbox_id) if campaign.mailbox_id else None
    sender = (mailbox.display_name if mailbox else "") or principal.user.full_name or "Tu nombre"
    if contact is not None:
        country = COUNTRIES.get(contact.country_code or "", ("",))[0]
        values = templating.context(contact, sender, country)
    else:
        values = {
            "nombre": "Laura",
            "apellido": "Gómez",
            "nombre_completo": "Laura Gómez",
            "empresa": "Empresa Ejemplo",
            "cargo": "Gerente comercial",
            "ciudad": "Bogotá",
            "pais": "Colombia",
            "remitente": sender,
        }
    step = campaign.steps[body.step]
    subject_src = step["subject"].strip() or (
        "Re: " + campaign.steps[0]["subject"] if body.step else "(sin asunto)"
    )
    subject, missing_s = templating.render(subject_src, values)
    body_html, missing_b = templating.render(templating.as_html(step["body"]), values, escape=True)
    signature, _ = templating.render(
        templating.as_html(mailbox.signature if mailbox else ""), values, escape=True
    )
    content = body_html + (f'<div style="margin-top:16px">{signature}</div>' if signature else "")
    return {
        "contact": {"id": str(contact.id), "full_name": contact.full_name, "email": contact.email}
        if contact
        else None,
        "from": mailbox.email if mailbox else None,
        "subject": subject,
        "html": content,
        "body": templating.html_to_text(content),
        "missing": sorted(set(missing_s + missing_b)),
        "threaded": body.step > 0 and not step["subject"].strip(),
    }


def _action(
    campaign_id: uuid.UUID, principal: Principal, db: Db, request: Request, action: str
) -> dict[str, Any]:
    campaign = _campaign(db, principal, campaign_id)
    detail_extra: dict[str, Any] = {}
    try:
        if action == "launch":
            detail_extra["recipients"] = svc.launch(db, campaign)
        elif action == "pause":
            if campaign.status != "active":
                raise svc.CampaignError("Solo se pausa una campaña activa.", 409)
            campaign.status = "paused"
        elif action == "resume":
            if campaign.status != "paused":
                raise svc.CampaignError("Solo se reanuda una campaña pausada.", 409)
            problems = svc.validate_for_launch(db, campaign)
            if problems:
                raise svc.CampaignError(" ".join(problems))
            campaign.status = "active"
    except svc.CampaignError as exc:
        raise _fail(exc) from exc
    audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user.id,
        action=f"campaign.{action}",
        entity_type="campaign",
        entity_id=campaign.id,
        ip=client_ip(request),
        **detail_extra,
    )
    return svc.campaign_dict(db, campaign)


@router.post("/api/campaigns/{campaign_id}/launch")
def launch(campaign_id: uuid.UUID, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    return _action(campaign_id, principal, db, request, "launch")


@router.post("/api/campaigns/{campaign_id}/retry-bounced", status_code=201)
def retry_bounced(campaign_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    campaign = _campaign(db, principal, campaign_id)
    try:
        retry = svc.retry_bounced(db, campaign, principal.user)
    except svc.CampaignError as exc:
        raise _fail(exc) from exc
    return svc.campaign_dict(db, retry)


@router.post("/api/campaigns/{campaign_id}/pause")
def pause(campaign_id: uuid.UUID, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    return _action(campaign_id, principal, db, request, "pause")


@router.post("/api/campaigns/{campaign_id}/resume")
def resume(campaign_id: uuid.UUID, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    return _action(campaign_id, principal, db, request, "resume")


@router.get("/api/campaigns/{campaign_id}/recipients")
def recipients(
    campaign_id: uuid.UUID, principal: Member, db: Db, status: str = "", page: int = 1
) -> dict[str, Any]:
    campaign = _campaign(db, principal, campaign_id)
    mailbox = db.get(Mailbox, campaign.mailbox_id) if campaign.mailbox_id else None
    mailbox_email = mailbox.email if mailbox else ""
    query = (
        select(Recipient, Contact)
        .join(Contact, Contact.id == Recipient.contact_id)
        .where(Recipient.campaign_id == campaign.id)
    )
    if status:
        query = query.where(Recipient.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    size = 50
    rows = db.execute(
        query.order_by(Recipient.replied_at.desc().nulls_last(), Contact.full_name)
        .offset((max(1, page) - 1) * size)
        .limit(size)
    ).tuples()
    return {
        "total": total,
        "items": [
            {
                "id": str(r.id),
                "contact_id": str(c.id),
                "full_name": c.full_name,
                "company_name": c.company_name,
                "job_title": c.job_title,
                "email": r.email,
                "email_status": c.email_status,
                "status": r.status,
                "step": r.step,
                "sent_count": r.sent_count,
                "next_send_at": r.next_send_at.isoformat() if r.next_send_at else None,
                "opened": r.opened_at is not None,
                "open_count": r.open_count,
                "clicked": r.clicked_at is not None,
                "replied_at": r.replied_at.isoformat() if r.replied_at else None,
                "gmail_url": (
                    f"https://mail.google.com/mail/?authuser={quote(mailbox_email)}#all/{r.thread_id}"
                    if r.thread_id and mailbox_email
                    else None
                ),
                "error": r.error,
            }
            for r, c in rows
        ],
    }


@router.post("/api/campaigns/{campaign_id}/recipients/{recipient_id}/stop")
def stop_recipient(
    campaign_id: uuid.UUID,
    recipient_id: uuid.UUID,
    principal: Member,
    db: Db,
    replied: bool = False,
) -> dict[str, bool]:
    recipient = db.get(Recipient, recipient_id)
    if recipient is None or recipient.campaign_id != _campaign(db, principal, campaign_id).id:
        raise HTTPException(404, "Ese destinatario no existe.")
    if recipient.status in ("pending", "active", "completed"):
        recipient.status = "replied" if replied else "skipped"
        recipient.next_send_at = None
        if replied:
            recipient.replied_at = svc.now()
    return {"ok": True}


class SuppressionIn(BaseModel):
    email: str = Field(max_length=320)


@router.get("/api/suppressions")
def suppressions(principal: Member, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Suppression)
        .where(Suppression.tenant_id == principal.tenant_id)
        .order_by(Suppression.created_at.desc())
        .limit(500)
    )
    return [
        {
            "id": str(s.id),
            "email": s.email,
            "reason": s.reason,
            "created_at": s.created_at.isoformat(),
        }
        for s in rows
    ]


@router.post("/api/suppressions", status_code=201)
def add_suppression(body: SuppressionIn, principal: Member, db: Db) -> dict[str, bool]:
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Escribe un correo válido.")
    svc.suppress(db, principal.tenant_id, email, "manual")
    return {"ok": True}


@router.delete("/api/suppressions/{suppression_id}")
def remove_suppression(suppression_id: uuid.UUID, principal: Manager, db: Db) -> dict[str, bool]:
    row = db.get(Suppression, suppression_id)
    if row is None or row.tenant_id != principal.tenant_id:
        raise HTTPException(404, "No existe.")
    db.delete(row)
    return {"ok": True}


# --- Imágenes para correos y firmas ---------------------------------------------------------

IMAGE_TYPES = {
    "image/png": (b"\x89PNG", "png"),
    "image/jpeg": (b"\xff\xd8\xff", "jpg"),
    "image/gif": (b"GIF8", "gif"),
    "image/webp": (b"RIFF", "webp"),
}
MAX_IMAGE = 3 * 1024 * 1024


def _asset_url(request: Request, asset: EmailAsset) -> str:
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/t/i/{asset.id}.{IMAGE_TYPES[asset.content_type][1]}"


@router.post("/api/email-assets", status_code=201)
async def upload_asset(
    file: UploadFile, request: Request, principal: Member, db: Db
) -> dict[str, Any]:
    data = await file.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise HTTPException(413, "La imagen pesa más de 3 MB. Redúcela y vuelve a subirla.")
    kind = next((t for t, (magic, _) in IMAGE_TYPES.items() if data.startswith(magic)), None)
    if kind is None or (kind == "image/webp" and data[8:12] != b"WEBP"):
        raise HTTPException(400, "Sube una imagen PNG, JPG, GIF o WEBP.")
    asset = EmailAsset(tenant_id=principal.tenant_id, content_type=kind, size=len(data),
                       name=(file.filename or "")[:200], data=data)  # fmt: skip
    db.add(asset)
    db.flush()
    return {"id": str(asset.id), "url": _asset_url(request, asset), "size": len(data)}


@tracking.get("/t/i/{name}")
def asset(name: str, db: Db) -> Response:
    try:
        asset_id = uuid.UUID(name.split(".", 1)[0])
    except ValueError:
        raise HTTPException(404, "No existe.") from None
    row = db.get(EmailAsset, asset_id)
    if row is None:
        raise HTTPException(404, "No existe.")
    return Response(row.data, media_type=row.content_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})  # fmt: skip


# --- Seguimiento público (sin sesión) -------------------------------------------------------


@tracking.get("/t/o/{name}")
def open_pixel(name: str, request: Request, db: Db) -> Response:
    token = name.removesuffix(".gif")
    if public_limiter.allow(f"t:{client_ip(request)}"):
        svc.record_open(db, token)
    return Response(PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store, max-age=0"})


@tracking.get("/t/c/{token}")
def click(token: str, request: Request, db: Db, u: str = "", s: str = "") -> Response:
    if not u.startswith(("http://", "https://")) or not templating.valid_click(token, u, s):
        raise HTTPException(404, "Enlace no válido.")
    if public_limiter.allow(f"t:{client_ip(request)}"):
        svc.record_click(db, token)
    return RedirectResponse(u, status_code=302)


def _page(title: str, body: str, form: str = "") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport "
        "content='width=device-width,initial-scale=1'><title>" + title + "</title>"
        "<body style='font-family:system-ui,Arial,sans-serif;background:#f6f7fb;margin:0'>"
        "<main style='max-width:440px;margin:12vh auto;background:#fff;padding:32px;"
        "border-radius:16px;box-shadow:0 1px 3px #0001'><h1 style='font-size:20px;margin:0 0 8px'>"
        + title
        + "</h1><p style='color:#475569;font-size:15px;line-height:1.5'>"
        + body
        + "</p>"
        + form
        + "</main></body></html>"
    )


@tracking.get("/t/u/{token}")
def unsubscribe_page(token: str) -> HTMLResponse:
    form = (
        "<form method=post><button style='background:#1f4ed8;color:#fff;border:0;"
        "padding:10px 18px;border-radius:10px;font-size:15px;cursor:pointer'>"
        "Confirmar baja</button></form>"
    )
    return _page(
        "¿Dejar de recibir estos correos?",
        "Confirma y no volverás a recibir correos de esta secuencia ni de futuras "
        "campañas de este remitente.",
        form,
    )


@tracking.post("/t/u/{token}")
def unsubscribe(token: str, request: Request, db: Db) -> HTMLResponse:
    if not public_limiter.allow(f"u:{client_ip(request)}"):
        raise HTTPException(429, "Demasiadas solicitudes.")
    email = svc.unsubscribe(db, token)
    if email is None:
        return _page(
            "Enlace no válido",
            "No encontramos ese correo. Si sigues recibiendo "
            "mensajes, responde al correo con la palabra «baja».",
        )
    return _page("Listo, te diste de baja", "No volverás a recibir correos de este remitente.")
