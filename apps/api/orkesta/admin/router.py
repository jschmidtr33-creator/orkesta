"""API del panel de administración de ORKESTA: /api/admin/…"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from orkesta.admin import service as svc
from orkesta.admin.models import AdminAudit
from orkesta.core.config import get_settings
from orkesta.core.db import set_context, unscoped
from orkesta.core.security import (
    ADMIN_UNLOCK_MINUTES,
    issue_admin_token,
    read_admin_token,
    verify_password,
)
from orkesta.core.web import CurrentPrincipal, Db, Principal, client_ip
from orkesta.platform.models import (
    AuthSession,
    DemoRequest,
    Invitation,
    LoginAttempt,
    Membership,
    Tenant,
    User,
)
from orkesta.platform.service import _too_many_failures, normalize_email

router = APIRouter(prefix="/api/admin")


LOCKED = "Confirma tus credenciales de administración para entrar al panel."


def require(permission: str) -> Callable[..., Principal]:
    """Acceso al panel: rol de plataforma + pase vigente (contraseña confirmada hace poco)."""

    def dependency(
        principal: CurrentPrincipal,
        db: Db,
        x_admin_token: Annotated[str | None, Header()] = None,
    ) -> Principal:
        if not principal.user.platform_role:
            raise HTTPException(403, "No tienes acceso al panel de administración.")
        if read_admin_token(x_admin_token or "") != principal.user.id:
            raise HTTPException(428, LOCKED)
        if permission and permission not in svc.permissions_for(db, principal.user):
            raise HTTPException(403, "Tu rol en el panel no permite esta acción.")
        from orkesta.core.db import set_private

        set_private(db, None)  # el panel de la plataforma trabaja con cifras de todas las cuentas
        return principal

    return dependency


Staff = Annotated[Principal, Depends(require(""))]


def _user(u: User) -> dict[str, Any]:
    return {"id": str(u.id), "name": u.full_name or u.email, "email": u.email}


class UnlockIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    code: str = Field(default="", max_length=20)


@router.post("/unlock")
def unlock(body: UnlockIn, request: Request, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    """Pide de nuevo correo y contraseña antes de abrir el panel, aunque haya sesión."""
    user = principal.user
    if not user.platform_role:
        raise HTTPException(403, "No tienes acceso al panel de administración.")
    email = normalize_email(body.email)
    if _too_many_failures(db, user.email):
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos.")
    ok = email == user.email and verify_password(body.password, user.password_hash)
    db.add(LoginAttempt(email=user.email, ip=client_ip(request), succeeded=ok))
    svc.log(db, user, "panel.ingreso" if ok else "panel.ingreso_fallido", user.email)
    if not ok:
        db.commit()
        if not user.password_hash:
            raise HTTPException(
                401, "Tu cuenta no tiene contraseña de ORKESTA, así que no puede abrir el panel."
            )
        raise HTTPException(401, "Correo o contraseña incorrectos.")
    if user.mfa_enabled:
        from orkesta.platform.service import check_mfa

        if not body.code:
            db.rollback()
            raise HTTPException(401, "Escribe el código de tu app de autenticación.")
        if not check_mfa(user, body.code):
            db.add(LoginAttempt(email=user.email, ip=client_ip(request), succeeded=False))
            db.commit()
            raise HTTPException(401, "El código de verificación no es correcto.")
    return {"token": issue_admin_token(user.id), "expires_in": ADMIN_UNLOCK_MINUTES * 60}


@router.get("/access")
def access(principal: CurrentPrincipal) -> dict[str, Any]:
    """¿Puede esta cuenta pedir entrada al panel? (sin datos del panel)."""
    if not principal.user.platform_role:
        raise HTTPException(403, "No tienes acceso al panel de administración.")
    return {"email": principal.user.email, "name": principal.user.full_name or ""}


@router.get("/me")
def me(principal: Staff, db: Db) -> dict[str, Any]:
    return {
        "user": _user(principal.user),
        "role": principal.user.platform_role,
        "role_label": svc.ROLES.get(principal.user.platform_role or "", ""),
        "permissions": sorted(svc.permissions_for(db, principal.user)),
    }


@router.get("/overview")
def overview(
    principal: Annotated[Principal, Depends(require("ver_resumen"))], db: Db
) -> dict[str, Any]:
    return svc.overview(db)


# --- Empresas -------------------------------------------------------------------------------


class TenantPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    plan: str | None = Field(default=None, max_length=30)
    extend_days: int | None = Field(default=None, ge=1, le=60)  # alarga la demo
    end_trial: bool = False  # termina la demo hoy


@router.get("/tenants")
def tenants(
    principal: Annotated[Principal, Depends(require("ver_empresas"))], db: Db, q: str = ""
) -> dict[str, Any]:
    return {"items": svc.tenant_rows(db, q[:100]), "plans": svc.PLANS}


@router.patch("/tenants/{tenant_id}")
def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantPatch,
    principal: Annotated[Principal, Depends(require("gestionar_empresas"))],
    db: Db,
) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Esa empresa no existe.")
    if body.status:
        tenant.status = body.status
        svc.log(db, principal.user, f"tenant.{body.status}", tenant.name)
    if body.plan:
        if body.plan not in svc.PLANS:
            raise HTTPException(400, "Plan no válido.")
        svc.log(db, principal.user, "tenant.plan", tenant.name, before=tenant.plan, after=body.plan)
        if body.plan == "demo" and tenant.plan != "demo":
            tenant.trial_ends_at = svc.now() + timedelta(days=svc.DEMO_DAYS)
        tenant.plan = body.plan
        if body.plan != "demo":
            tenant.trial_ends_at = None  # pasó a un plan pagado: ya no vence
    if body.extend_days and tenant.plan == "demo":
        start = max(tenant.trial_ends_at or svc.now(), svc.now())
        tenant.trial_ends_at = start + timedelta(days=body.extend_days)
        svc.log(db, principal.user, "tenant.demo_extended", tenant.name, days=body.extend_days)
    if body.end_trial and tenant.plan == "demo":
        tenant.trial_ends_at = svc.now()
        svc.log(db, principal.user, "tenant.demo_ended", tenant.name)
    ends = tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None
    return {"ok": True, "status": tenant.status, "plan": tenant.plan, "trial_ends_at": ends}


# --- Demos de 2 semanas para empresas ---------------------------------------------------------


class DemoStartIn(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    email: str = Field(max_length=320)
    days: int = Field(default=14, ge=1, le=60)
    request_id: uuid.UUID | None = None


class TenantDeleteIn(BaseModel):
    confirm: str = Field(max_length=200)


@router.post("/tenants/{tenant_id}/delete")
def delete_tenant(
    tenant_id: uuid.UUID,
    body: TenantDeleteIn,
    principal: Annotated[Principal, Depends(require("gestionar_empresas"))],
    db: Db,
) -> dict[str, Any]:
    """Borra la empresa con todos sus datos. Las cuentas que solo pertenecían a ella se borran
    también; quien está en otras empresas conserva su cuenta. No se puede deshacer."""
    from orkesta.crm.models import Deal

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Esa empresa no existe.")
    if tenant.id == principal.tenant_id:
        raise HTTPException(409, "No puedes borrar la empresa desde la que administras ORKESTA.")
    if " ".join(body.confirm.split()).lower() != " ".join(tenant.name.split()).lower():
        raise HTTPException(400, "Escribe el nombre exacto de la empresa para confirmar.")
    name = tenant.name
    set_context(db, tenant_id=tenant.id, user_id=principal.user.id)
    members = db.scalars(select(Membership.user_id).where(Membership.tenant_id == tenant.id)).all()
    db.execute(delete(Deal).where(Deal.tenant_id == tenant.id))  # antes que sus etapas
    db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    db.delete(tenant)
    db.flush()
    removed = 0
    for uid in members:
        user = db.get(User, uid)
        if user is None or user.platform_role:
            continue
        others = 0
        for tid in db.scalars(select(Tenant.id)).all():
            set_context(db, tenant_id=tid)
            others += db.scalar(
                select(func.count()).select_from(Membership)
                .where(Membership.tenant_id == tid, Membership.user_id == uid)
            ) or 0  # fmt: skip
        set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
        if not others:
            db.delete(user)
            removed += 1
    db.flush()
    svc.log(db, principal.user, "tenant.deleted", name, users_removed=removed)
    return {"ok": True, "users_removed": removed}


class CompanyIn(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    email: str = Field(max_length=320)
    plan: str = "pro"


@router.post("/tenants", status_code=201)
def add_company(
    body: CompanyIn,
    request: Request,
    principal: Annotated[Principal, Depends(require("gestionar_empresas"))],
    db: Db,
) -> dict[str, Any]:
    """Crea una empresa cliente con su plan e invita a su administración (enlace + mensaje)."""
    from orkesta.platform import service as platform

    if body.plan == "demo":
        return start_demo(DemoStartIn(company=body.company, email=body.email), request,
                          principal, db)  # fmt: skip
    if body.plan not in svc.PLANS:
        raise HTTPException(400, "Ese plan no existe.")
    email = normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(400, "Escribe un correo válido.")
    tenant = Tenant(name=body.company.strip()[:160], slug=platform._unique_slug(db, body.company),
                    plan=body.plan)  # fmt: skip
    db.add(tenant)
    db.flush()
    set_context(db, tenant_id=tenant.id, user_id=principal.user.id)
    try:
        _, token = platform.invite(db, tenant_id=tenant.id, inviter_id=principal.user.id,
                                   email=email, role="admin", ip=client_ip(request))  # fmt: skip
    except platform.PlatformError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    link = f"{base}/app/invitacion/{token}"
    svc.log(db, principal.user, "tenant.created", tenant.name, email=email, plan=body.plan)
    message = (
        f"Hola:\n\nTe doy la bienvenida a ORKESTA. Ya está listo el espacio de {tenant.name}.\n\n"
        f"1. Entra a este enlace y crea tu contraseña: {link}\n"
        "2. Conecta tu correo en Configuración → Integraciones.\n"
        "3. Invita a tu equipo desde Configuración → Equipo.\n\n"
        "El enlace vence en unos días; si se vence, te envío uno nuevo.\n\nSaludos,"
    )
    return {"tenant_id": str(tenant.id), "name": tenant.name, "link": link, "email": email,
            "trial_ends_at": None, "message": message}  # fmt: skip


@router.post("/demos/start", status_code=201)
def start_demo(
    body: DemoStartIn,
    request: Request,
    principal: Annotated[Principal, Depends(require("gestionar_empresas"))],
    db: Db,
) -> dict[str, Any]:
    """Crea el espacio de la empresa con todas las funciones por N días e invita a su
    administración. Devuelve el enlace y un mensaje listo para enviárselo."""
    from orkesta.platform import service as platform

    email = normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(400, "Escribe un correo válido.")
    tenant = Tenant(name=body.company.strip()[:160], slug=platform._unique_slug(db, body.company),
                    plan="demo", trial_ends_at=svc.now() + timedelta(days=body.days))  # fmt: skip
    db.add(tenant)
    db.flush()
    set_context(db, tenant_id=tenant.id, user_id=principal.user.id)
    try:
        _, token = platform.invite(db, tenant_id=tenant.id, inviter_id=principal.user.id,
                                   email=email, role="admin", ip=client_ip(request))  # fmt: skip
    except platform.PlatformError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    db.flush()  # la invitación y su registro se guardan dentro del espacio de la demo
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    link = f"{base}/app/invitacion/{token}"
    ends = tenant.trial_ends_at.strftime("%d-%m-%Y") if tenant.trial_ends_at else ""
    svc.log(db, principal.user, "tenant.demo_started", tenant.name, email=email, days=body.days)
    message = (
        f"Hola:\n\nTe dejo el acceso a tu demo de ORKESTA para {tenant.name}. Tienes {body.days} "
        f"días con todas las funciones activas (hasta el {ends}): prospección, campañas desde tu "
        "correo, CRM, propuestas con firma, automatizaciones, calendario y Concertino, el asesor "
        f"de ventas con IA.\n\n1. Entra a este enlace y crea tu contraseña: {link}\n"
        "2. Conecta tu correo en Configuración → Integraciones.\n"
        "3. Invita a tu equipo desde Configuración → Equipo.\n\n"
        "El enlace vence en unos días; si se vence, te envío uno nuevo.\n\nSaludos,"
    )
    ends_iso = tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None
    return {"tenant_id": str(tenant.id), "name": tenant.name, "link": link, "email": email,
            "trial_ends_at": ends_iso, "message": message}  # fmt: skip


# --- Ficha de cada empresa ---------------------------------------------------------------------

Companies = Annotated[Principal, Depends(require("gestionar_empresas"))]


def _pairs(rows: Any) -> dict[Any, Any]:
    return {r[0]: r[1] for r in rows}


def _access_message(tenant: Tenant, link: str, role: str) -> str:
    who = "administrar" if role == "admin" else "trabajar en"
    return (
        f"Hola:\n\nTe dejo tu acceso a ORKESTA para {who} el espacio de {tenant.name}.\n\n"
        f"1. Entra a este enlace y crea tu contraseña: {link}\n"
        "2. Conecta tu correo en Configuración → Integraciones.\n\n"
        "El enlace vence en unos días; si se vence, te envío uno nuevo.\n\nSaludos,"
    )


@router.get("/tenants/{tenant_id}")
def tenant_detail(
    tenant_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require("ver_empresas"))],
    db: Db,
) -> dict[str, Any]:
    """Todo de una empresa: equipo, invitaciones pendientes, actividad diaria y uso."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from orkesta.campaigns.models import Message as CampaignMessage
    from orkesta.crm.models import Activity

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Esa empresa no existe.")
    usage = svc._tenant_usage(db, tenant.id)
    set_context(db, tenant_id=tenant.id)
    members = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tenant.id)
        .order_by(Membership.created_at)
    ).all()
    now = datetime.now(UTC)
    invitations = db.scalars(
        select(Invitation)
        .where(Invitation.tenant_id == tenant.id, Invitation.accepted_at.is_(None),
               Invitation.revoked_at.is_(None))
        .order_by(Invitation.created_at.desc())
    ).all()  # fmt: skip
    since = now - timedelta(days=14)
    day_of = func.date_trunc("day", func.timezone("America/Bogota", CampaignMessage.sent_at))
    with unscoped(db):
        sent: dict[Any, Any] = _pairs(db.execute(
            select(day_of, func.count()).where(CampaignMessage.tenant_id == tenant.id,
                                               CampaignMessage.sent_at >= since).group_by(day_of)
        ).all())  # fmt: skip
        act_day = func.date_trunc("day", func.timezone("America/Bogota", Activity.created_at))
        acts: dict[Any, Any] = _pairs(db.execute(
            select(act_day, func.count()).where(Activity.tenant_id == tenant.id,
                                                Activity.created_at >= since).group_by(act_day)
        ).all())  # fmt: skip
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    user_ids = [u.id for _, u in members]
    login_day = func.date_trunc("day", func.timezone("America/Bogota", AuthSession.created_at))
    logins: dict[Any, Any] = _pairs(db.execute(
        select(login_day, func.count(func.distinct(AuthSession.user_id)))
        .where(AuthSession.user_id.in_(user_ids), AuthSession.created_at >= since)
        .group_by(login_day)
    ).all()) if user_ids else {}  # fmt: skip

    def key(d: Any) -> str:
        return str(d.date().isoformat())

    days = []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).astimezone(ZoneInfo("America/Bogota")).date().isoformat()
        days.append({
            "day": d,
            "emails": next((int(v) for k, v in sent.items() if key(k) == d), 0),
            "activities": next((int(v) for k, v in acts.items() if key(k) == d), 0),
            "people": next((int(v) for k, v in logins.items() if key(k) == d), 0),
        })  # fmt: skip
    last_login = max((u.last_login_at for _, u in members if u.last_login_at), default=None)
    return {
        "id": str(tenant.id), "name": tenant.name, "plan": tenant.plan, "status": tenant.status,
        "created_at": tenant.created_at.isoformat(),
        "trial_ends_at": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
        "trial_expired": tenant.trial_expired, "usage": usage,
        "last_login_at": last_login.isoformat() if last_login else None,
        "members": [{
            "user_id": str(u.id), "name": u.full_name or "", "email": u.email, "role": m.role,
            "status": m.status if u.is_active else "suspendido",
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "joined_at": m.created_at.isoformat(),
        } for m, u in members],
        "invitations": [{
            "id": str(i.id), "email": i.email, "role": i.role,
            "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat(),
            "expired": i.expires_at <= now,
        } for i in invitations],
        "activity": days,
        "plans": svc.PLANS,
    }  # fmt: skip


@router.post("/tenants/{tenant_id}/invitations/{invitation_id}/renew")
def renew_invitation(
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    principal: Companies,
    db: Db,
) -> dict[str, Any]:
    """Nuevo enlace para una invitación pendiente (el anterior deja de servir)."""
    from orkesta.platform import service as platform

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Esa empresa no existe.")
    set_context(db, tenant_id=tenant.id, user_id=principal.user.id)
    old = db.get(Invitation, invitation_id)
    if old is None or old.tenant_id != tenant.id or old.accepted_at or old.revoked_at:
        raise HTTPException(404, "Esa invitación ya no está pendiente.")
    email, role = old.email, old.role
    try:
        _, token = platform.invite(db, tenant_id=tenant.id, inviter_id=principal.user.id,
                                   email=email, role=role, ip=client_ip(request))  # fmt: skip
    except platform.PlatformError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    link = f"{base}/app/invitacion/{token}"
    svc.log(db, principal.user, "invitation.renewed", email, tenant=tenant.name)
    return {"name": f"{email} ({tenant.name})", "email": email, "link": link,
            "trial_ends_at": None, "message": _access_message(tenant, link, role)}  # fmt: skip


@router.delete("/tenants/{tenant_id}/invitations/{invitation_id}")
def revoke_invitation(
    tenant_id: uuid.UUID, invitation_id: uuid.UUID, principal: Companies, db: Db
) -> dict[str, bool]:
    set_context(db, tenant_id=tenant_id, user_id=principal.user.id)
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.tenant_id != tenant_id or inv.accepted_at:
        raise HTTPException(404, "Esa invitación ya no está pendiente.")
    inv.revoked_at = svc.now()
    db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    svc.log(db, principal.user, "invitation.revoked", inv.email)
    return {"ok": True}


class MemberPatch(BaseModel):
    role: str = Field(pattern="^(admin|supervisor|vendedor)$")


@router.patch("/tenants/{tenant_id}/members/{user_id}")
def change_member_role(
    tenant_id: uuid.UUID, user_id: uuid.UUID, body: MemberPatch, principal: Companies, db: Db
) -> dict[str, bool]:
    set_context(db, tenant_id=tenant_id, user_id=principal.user.id)
    member = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id,
                                                Membership.user_id == user_id))  # fmt: skip
    if member is None:
        raise HTTPException(404, "Esa persona no está en la empresa.")
    if member.role == "admin" and body.role != "admin":
        admins = db.scalar(select(func.count()).select_from(Membership).where(
            Membership.tenant_id == tenant_id, Membership.role == "admin",
            Membership.status == "active"))  # fmt: skip
        if (admins or 0) <= 1:
            raise HTTPException(409, "La empresa debe conservar al menos un administrador.")
    member.role = body.role
    db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    svc.log(db, principal.user, "member.role", str(user_id), role=body.role)
    return {"ok": True}


@router.delete("/tenants/{tenant_id}/members/{user_id}")
def remove_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, principal: Companies, db: Db
) -> dict[str, bool]:
    """Saca a la persona de esta empresa (su cuenta sigue si está en otras)."""
    set_context(db, tenant_id=tenant_id, user_id=principal.user.id)
    member = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id,
                                                Membership.user_id == user_id))  # fmt: skip
    if member is None:
        raise HTTPException(404, "Esa persona no está en la empresa.")
    if member.role == "admin":
        admins = db.scalar(select(func.count()).select_from(Membership).where(
            Membership.tenant_id == tenant_id, Membership.role == "admin",
            Membership.status == "active"))  # fmt: skip
        if (admins or 0) <= 1:
            raise HTTPException(409, "Es el único administrador: asigna otro antes de sacarlo.")
    db.delete(member)
    db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    svc.log(db, principal.user, "member.removed", str(user_id))
    return {"ok": True}


@router.get("/trials")
def trials(
    principal: Annotated[Principal, Depends(require("ver_empresas"))], db: Db
) -> list[dict[str, Any]]:
    """Demos activas y vencidas, con lo que la empresa alcanzó a usar."""
    rows = db.scalars(select(Tenant).where(Tenant.plan == "demo")
                      .order_by(Tenant.trial_ends_at.asc().nulls_last())).all()  # fmt: skip
    out = []
    now = svc.now()
    for t in rows:
        left = (t.trial_ends_at - now).total_seconds() / 86400 if t.trial_ends_at else None
        out.append({"id": str(t.id), "name": t.name, "created_at": t.created_at.isoformat(),
                    "trial_ends_at": t.trial_ends_at.isoformat() if t.trial_ends_at else None,
                    "days_left": max(0, int(left + 0.999)) if left is not None else None,
                    "expired": t.trial_expired, "status": t.status,
                    "members": svc._members(db, t.id), "owner": svc._owner(db, t.id),
                    **svc._tenant_usage(db, t.id)})  # fmt: skip
    return out


class MergeIn(BaseModel):
    target_id: uuid.UUID
    role: str | None = Field(default="vendedor", pattern="^(admin|supervisor|vendedor)$")
    confirm: str = Field(default="", max_length=200)


@router.post("/tenants/{tenant_id}/merge")
def merge_tenant(
    tenant_id: uuid.UUID,
    body: MergeIn,
    principal: Annotated[Principal, Depends(require("gestionar_empresas"))],
    db: Db,
) -> dict[str, Any]:
    """Une una empresa a otra: todo su trabajo pasa al destino y el origen se cierra."""
    from orkesta.platform.merge import MergeError, merge_tenants

    if principal.user.platform_role != "owner":
        raise HTTPException(403, "Solo la creadora del panel puede unir empresas.")
    source = db.get(Tenant, tenant_id)
    if source is None:
        raise HTTPException(404, "Esa empresa no existe.")
    if " ".join(body.confirm.split()).lower() != " ".join(source.name.split()).lower():
        raise HTTPException(400, f"Para confirmar, escribe el nombre exacto: {source.name}")
    try:
        report = merge_tenants(db, source_id=tenant_id, target_id=body.target_id, role=body.role)
    except MergeError as exc:
        raise HTTPException(400, str(exc)) from exc
    svc.log(db, principal.user, "tenant.merged", f"{report['source']} → {report['target']}",
            moved=sum(report["moved"].values()))  # fmt: skip
    return report


# --- Usuarios -------------------------------------------------------------------------------


class UserPatch(BaseModel):
    is_active: bool


@router.get("/users")
def users(
    principal: Annotated[Principal, Depends(require("ver_usuarios"))], db: Db, q: str = ""
) -> dict[str, Any]:
    query = select(User).order_by(User.created_at.desc()).limit(300)
    if q:
        term = f"%{q.lower().strip()[:100]}%"
        query = query.where(
            func.lower(User.email).like(term) | func.lower(User.full_name).like(term)
        )
    rows = db.scalars(query).all()
    spaces: dict[uuid.UUID, list[str]] = {}
    for t in db.scalars(select(Tenant)).all():
        set_context(db, tenant_id=t.id)
        for uid, role in db.execute(
            select(Membership.user_id, Membership.role).where(
                Membership.tenant_id == t.id, Membership.status == "active"
            )
        ).all():
            spaces.setdefault(uid, []).append(f"{t.name} ({role})")
    set_context(db)
    return {
        "items": [
            {
                **_user(u),
                "is_active": u.is_active,
                "platform_role": u.platform_role,
                "created_at": u.created_at.isoformat(),
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "workspaces": spaces.get(u.id, []),
                "google": bool(u.google_sub),
            }
            for u in rows
        ]
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    principal: Annotated[Principal, Depends(require("gestionar_usuarios"))],
    db: Db,
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Ese usuario no existe.")
    if user.platform_role == "owner" or user.id == principal.user.id:
        raise HTTPException(409, "No puedes suspender esta cuenta.")
    user.is_active = body.is_active
    if not body.is_active:
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=svc.now())
        )
    svc.log(db, principal.user, "user.active" if body.is_active else "user.suspended", user.email)
    return {"ok": True}


class UserAddIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    tenant_id: uuid.UUID
    role: str = "vendedor"


@router.post("/users", status_code=201)
def add_user(
    body: UserAddIn,
    request: Request,
    principal: Annotated[Principal, Depends(require("gestionar_usuarios"))],
    db: Db,
) -> dict[str, Any]:
    """Suma una persona a una empresa: crea su invitación y devuelve el enlace para enviárselo."""
    from orkesta.platform import service as platform

    email = normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(400, "Escribe un correo válido.")
    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(404, "Esa empresa no existe.")
    set_context(db, tenant_id=tenant.id, user_id=principal.user.id)
    try:
        _, token = platform.invite(db, tenant_id=tenant.id, inviter_id=principal.user.id,
                                   email=email, role=body.role, ip=client_ip(request))  # fmt: skip
    except platform.PlatformError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    db.flush()  # la invitación se guarda dentro del espacio de esa empresa
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    link = f"{base}/app/invitacion/{token}"
    svc.log(db, principal.user, "user.added", email, tenant=tenant.name, role=body.role)
    message = (
        f"Hola:\n\nTe dejo tu acceso a ORKESTA en {tenant.name}.\n\n"
        f"1. Entra a este enlace y crea tu contraseña: {link}\n"
        "2. Conecta tu correo en Configuración → Integraciones.\n\n"
        "El enlace vence en unos días; si se vence, te envío uno nuevo.\n\nSaludos,"
    )
    return {"email": email, "tenant": tenant.name, "link": link, "message": message}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require("gestionar_usuarios"))],
    db: Db,
) -> dict[str, Any]:
    """Elimina la cuenta por completo. Su trabajo (contactos, oportunidades, propuestas) queda
    en la empresa sin dueño, para que la administración lo reasigne; sus buzones conectados se
    borran y sus campañas activas se pausan."""
    from orkesta.campaigns.models import Campaign, Mailbox
    from orkesta.core.db import unscoped

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Ese usuario no existe.")
    if user.platform_role or user.id == principal.user.id:
        raise HTTPException(409, "No se puede eliminar una cuenta del equipo de ORKESTA.")
    email = user.email
    paused = 0
    for tid in db.scalars(select(Tenant.id)).all():
        set_context(db, tenant_id=tid, user_id=principal.user.id)
        member = db.scalar(
            select(Membership.id).where(Membership.tenant_id == tid, Membership.user_id == user.id)
        )
        if member is None:
            continue
        with unscoped(db):
            result = db.execute(
                update(Campaign)
                .where(Campaign.tenant_id == tid, Campaign.created_by == user.id,
                       Campaign.status == "active")
                .values(status="paused")
            )  # fmt: skip
            paused += int(getattr(result, "rowcount", 0) or 0)
            db.execute(delete(Mailbox).where(Mailbox.tenant_id == tid, Mailbox.user_id == user.id))
        db.flush()
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    db.delete(user)
    db.flush()
    svc.log(db, principal.user, "user.deleted", email, paused_campaigns=paused)
    return {"ok": True, "paused_campaigns": paused}


class AccessMailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="Tu acceso a ORKESTA", max_length=200)
    message: str = Field(min_length=10, max_length=5000)


@router.post("/send-access")
def send_access(
    body: AccessMailIn,
    principal: Annotated[Principal, Depends(require(""))],
    db: Db,
) -> dict[str, Any]:
    """Envía el mensaje de acceso desde el buzón que la persona del panel conectó a ORKESTA."""
    from email.message import EmailMessage
    from email.utils import formataddr, make_msgid

    from orkesta.campaigns.models import Mailbox
    from orkesta.campaigns.service import deliver

    email = normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(400, "Escribe un correo válido.")
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    mailbox = db.scalar(
        select(Mailbox)
        .where(Mailbox.tenant_id == principal.tenant_id, Mailbox.user_id == principal.user.id,
               Mailbox.status == "active")
        .order_by(Mailbox.created_at).limit(1)
    )  # fmt: skip
    if mailbox is None:
        from orkesta.core import system_mail

        if system_mail.send(db, to=email, subject=body.subject, text=body.message):
            set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
            svc.log(db, principal.user, "access.sent", email)
            sender = (system_mail.config(db) or {}).get("email", "")
            return {"ok": True, "from": sender, "to": email}
        raise HTTPException(
            409,
            "Conecta tu correo en Configuración → Integraciones para enviar el acceso desde "
            "ORKESTA, o copia el mensaje y envíalo tú.",
        )
    msg = EmailMessage()
    msg["From"] = formataddr((principal.user.full_name or "ORKESTA", mailbox.email))
    msg["To"] = email
    msg["Subject"] = body.subject
    rfc_id = make_msgid(domain=mailbox.email.rsplit("@", 1)[-1])
    msg["Message-ID"] = rfc_id
    msg.set_content(body.message)
    try:
        deliver(db, mailbox, msg.as_bytes(), None, email, rfc_id)
    except Exception as exc:  # Gmail, Microsoft 365 o SMTP: el mensaje del proveedor
        raise HTTPException(502, f"No se pudo enviar desde {mailbox.email}: {exc}") from exc
    svc.log(db, principal.user, "access.sent", email)
    return {"ok": True, "from": mailbox.email, "to": email}


@router.get("/search-independence")
def search_independence(
    principal: Annotated[Principal, Depends(require("ia"))], db: Db, dias: int = 7
) -> dict[str, Any]:
    """Cuánto de la prospección sale de fuentes propias y cuánto del buscador pago (Serper)."""
    from datetime import UTC, datetime

    from orkesta.research import crawler
    from orkesta.research.models import ResearchRun

    since = datetime.now(UTC) - timedelta(days=max(1, min(dias, 90)))
    totals = {"runs": 0, "people": 0, "from_index": 0, "serp_calls": 0, "serp_failed_runs": 0,
              "index_only_runs": 0}  # fmt: skip
    for tid in db.scalars(select(Tenant.id)).all():
        set_context(db, tenant_id=tid)
        with unscoped(db):
            rows = db.scalars(
                select(ResearchRun.totals).where(ResearchRun.created_at >= since,
                                                 ResearchRun.status == "completed")
            ).all()  # fmt: skip
        for t in rows:
            t = t or {}
            totals["runs"] += 1
            totals["people"] += int(t.get("people", 0) or 0)
            totals["from_index"] += int(t.get("source_indice", 0) or 0)
            totals["serp_calls"] += int(t.get("serp_calls", 0) or 0)
            totals["serp_failed_runs"] += int(any(k.startswith("serp_fatal_") for k in t))
            totals["index_only_runs"] += int(not t.get("serp_calls"))
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    own = round(100 * min(totals["from_index"], totals["people"]) / totals["people"]) if (
        totals["people"]) else 0  # fmt: skip
    return {**totals, "own_share": own, "goal": 80, "days": dias,
            "crawler": crawler.overview(db)}  # fmt: skip


@router.get("/system-mail")
def get_system_mail(
    principal: Annotated[Principal, Depends(require("configuracion"))], db: Db
) -> dict[str, Any]:
    from orkesta.core import system_mail

    data = system_mail.config(db)
    return {"configured": data is not None, "email": data.get("email") if data else None}


@router.post("/system-mail")
def use_my_mailbox(
    principal: Annotated[Principal, Depends(require("configuracion"))], db: Db
) -> dict[str, Any]:
    """El correo del sistema sale del buzón que esta persona conectó en su espacio actual."""
    from orkesta.campaigns.models import Mailbox

    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    mailbox = db.scalar(
        select(Mailbox)
        .where(Mailbox.tenant_id == principal.tenant_id, Mailbox.user_id == principal.user.id,
               Mailbox.status == "active")
        .order_by(Mailbox.created_at).limit(1)
    )  # fmt: skip
    if mailbox is None:
        raise HTTPException(409, "Conecta primero tu correo en Configuración → Integraciones.")
    value = {
        "tenant_id": str(principal.tenant_id),
        "user_id": str(principal.user.id),
        "mailbox_id": str(mailbox.id),
        "email": mailbox.email,
    }
    svc.set_system_mail(db, value)
    svc.log(db, principal.user, "system_mail.set", mailbox.email)
    return {"configured": True, "email": mailbox.email}


@router.post("/users/{user_id}/reset-link")
def user_reset_link(
    user_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require("gestionar_usuarios"))],
    db: Db,
) -> dict[str, Any]:
    """Enlace para que la persona cree una contraseña nueva; se envía si hay correo del sistema."""
    from orkesta.platform.router import RESET_MINUTES, reset_link, send_reset

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Ese usuario no existe.")
    sent = send_reset(db, request, user)
    set_context(db, tenant_id=principal.tenant_id, user_id=principal.user.id)
    svc.log(db, principal.user, "user.reset_link", user.email, sent=sent)
    return {"link": reset_link(request, user), "sent": sent, "minutes": RESET_MINUTES}


@router.post("/users/{user_id}/logout")
def logout_user(
    user_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require("gestionar_usuarios"))],
    db: Db,
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Ese usuario no existe.")
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=svc.now())
    )
    svc.log(db, principal.user, "user.logout", user.email)
    return {"ok": True, "closed": result.rowcount}  # type: ignore[attr-defined]


# --- Solicitudes de demo, integraciones, configuración ---------------------------------------


@router.get("/demos")
def demos(
    principal: Annotated[Principal, Depends(require("ver_demos"))], db: Db
) -> list[dict[str, Any]]:
    rows = db.scalars(select(DemoRequest).order_by(DemoRequest.created_at.desc()).limit(300))
    return [
        {
            "id": str(d.id),
            "at": d.created_at.isoformat(),
            "name": d.name,
            "email": d.email,
            "company": d.company,
            "job_title": d.job_title,
            "country": d.country,
            "message": d.message,
        }
        for d in rows
    ]


@router.get("/integrations")
def integrations(
    principal: Annotated[Principal, Depends(require("integraciones"))],
) -> list[dict[str, Any]]:
    s = get_settings()
    return [
        {
            "key": "google",
            "name": "Google (inicio de sesión y Gmail)",
            "ok": bool(s.google_client_id and s.google_client_secret),
            "detail": "GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET",
        },
        {
            "key": "microsoft",
            "name": "Microsoft 365 / Outlook (campañas)",
            "ok": bool(s.microsoft_client_id and s.microsoft_client_secret),
            "detail": "MICROSOFT_CLIENT_ID y MICROSOFT_CLIENT_SECRET",
        },
        {
            "key": "public_url",
            "name": "URL pública",
            "ok": bool(s.public_base_url),
            "detail": s.public_base_url or "PUBLIC_BASE_URL sin configurar",
        },
        {
            "key": "serper",
            "name": "Buscador de Google (Serper)",
            "ok": bool(s.serper_api_key),
            "detail": "SERPER_API_KEY",
        },
        {
            "key": "worker",
            "name": "Motor de tareas en segundo plano",
            "ok": bool(s.run_worker),
            "detail": "RUN_WORKER",
        },
    ]


@router.get("/settings")
def get_app_settings(
    principal: Annotated[Principal, Depends(require("configuracion"))], db: Db
) -> dict[str, Any]:
    return {
        "settings": svc.app_settings(db),
        "plans": svc.PLANS,
        "can_edit_plans": "planes" in svc.permissions_for(db, principal.user),
    }


@router.put("/settings")
def put_app_settings(
    body: dict[str, Any], principal: Annotated[Principal, Depends(require("configuracion"))], db: Db
) -> dict[str, Any]:
    if "limits" in body and "planes" not in svc.permissions_for(db, principal.user):
        body = {k: v for k, v in body.items() if k != "limits"}
    data = svc.save_app_settings(db, body)
    svc.log(db, principal.user, "settings.updated", "", keys=sorted(body))
    return {"settings": data}


# --- Directorio de empresas (registros públicos) ---------------------------------------------

Directory = Annotated[Principal, Depends(require("directorio"))]


Brain = Annotated[Principal, Depends(require("ia"))]


def _brain() -> None:
    import orkesta.brain.capabilities  # noqa: F401  (registra las capacidades)


@router.get("/brain")
def brain_overview(principal: Brain, db: Db) -> dict[str, Any]:
    from orkesta.brain import registry

    _brain()
    return registry.overview(db)


@router.post("/brain/{task}/train", status_code=202)
def brain_train(task: str, request: Request, principal: Brain, db: Db) -> dict[str, Any]:
    from orkesta.brain import registry

    _brain()
    if task not in registry.TASKS:
        raise HTTPException(404, "Esa capacidad no existe.")
    if registry.TASKS[task].build is None:
        raise HTTPException(409, f"El modelo de esta capacidad llega en el paso "
                                 f"{registry.TASKS[task].step} del plan.")  # fmt: skip
    try:
        registry.train_async(request.app.state.session_factory, task)
    except registry.BrainError as exc:
        raise HTTPException(409, str(exc)) from exc
    svc.log(db, principal.user, "brain.train", task)
    return {"ok": True}


@router.post("/brain/{task}/versions/{version}/activate")
def brain_activate(task: str, version: int, principal: Brain, db: Db) -> dict[str, Any]:
    from orkesta.brain import registry

    _brain()
    try:
        registry.activate(db, task, version)
    except registry.BrainError as exc:
        raise HTTPException(404, str(exc)) from exc
    svc.log(db, principal.user, "brain.activate", f"{task} v{version}")
    return registry.overview(db)


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class TitleLabelIn(TitleIn):
    area: str | None = Field(default=None, max_length=40)
    level: str | None = Field(default=None, max_length=40)


@router.post("/brain/cargos/try")
def brain_try_title(body: TitleIn, principal: Brain) -> dict[str, Any]:
    from orkesta.brain import titles

    return titles.explain(body.title.strip())


@router.get("/brain/cargos/doubts")
def brain_title_doubts(principal: Brain, db: Db) -> dict[str, Any]:
    from orkesta.brain import titles
    from orkesta.engine.taxonomy.titles import AREA_LABELS, LEVEL_LABELS

    return {"items": titles.doubts(db), "areas": AREA_LABELS, "levels": LEVEL_LABELS}


@router.post("/brain/cargos/teach", status_code=201)
def brain_teach_title(body: TitleLabelIn, principal: Brain, db: Db) -> dict[str, Any]:
    """Enseña un cargo: queda como ejemplo humano para el próximo entrenamiento."""
    from orkesta.brain import titles
    from orkesta.brain.registry import record_feedback
    from orkesta.engine.taxonomy.titles import AREAS, LEVELS, classify_title

    title = body.title.strip()
    if body.area not in (None, *AREAS) or body.level not in (None, *LEVELS):
        raise HTTPException(422, "Área o nivel desconocido.")
    level, area = classify_title(title)
    record_feedback(db, titles.AREA, body.area or titles.NONE, text=title, predicted=area,
                    user_id=principal.user.id)  # fmt: skip
    if body.level:
        record_feedback(db, titles.LEVEL, body.level, text=title, predicted=level,
                        user_id=principal.user.id)  # fmt: skip
    svc.log(db, principal.user, "brain.teach", title, area=body.area, level=body.level)
    return {"ok": True}


@router.get("/directory")
def directory_overview(principal: Directory, db: Db) -> dict[str, Any]:
    from orkesta.research import directory

    return directory.overview(db)


@router.get("/learning")
def learning_summary(principal: Directory, db: Db) -> dict[str, Any]:
    """Lo que ORKESTA sabe, de dónde lo aprendió y qué tan bien acierta, en palabras simples."""
    from orkesta.admin.learning import summary

    return summary(db)


@router.post("/learning/run")
def learning_run(request: Request, principal: Directory, db: Db) -> dict[str, Any]:
    """Repasa ya toda la data cargada (lo mismo que se hace cada noche)."""
    from orkesta.brain.consolidate import start

    started = start(request.app.state.session_factory)
    svc.log(db, principal.user, "learning.run", "")
    return {"started": started}


@router.post("/directory/presets/{preset_id}")
def directory_load_preset(
    preset_id: str, request: Request, principal: Directory, db: Db
) -> dict[str, Any]:
    from orkesta.research import directory

    if preset_id not in directory.PRESETS:
        raise HTTPException(404, "Esa fuente no existe.")
    factory = request.app.state.session_factory
    started = directory.start(lambda: directory.run_preset(factory, preset_id), preset_id)
    svc.log(db, principal.user, "directory.preset", preset_id)
    return {"started": started}


@router.post("/directory/crawl")
def directory_crawl(request: Request, principal: Directory, db: Db) -> dict[str, Any]:
    from orkesta.research import crawler

    crawler.start_batch(request.app.state.session_factory)
    svc.log(db, principal.user, "directory.crawl", "")
    return {"started": True}


class RemovePersonIn(BaseModel):
    value: str = Field(min_length=5, max_length=320)


@router.post("/people/remove")
def remove_person(body: RemovePersonIn, principal: Directory, db: Db) -> dict[str, Any]:
    """Saca a una persona del índice para siempre (derecho a no figurar)."""
    from orkesta.research import people

    removed = people.remove(db, body.value)
    svc.log(db, principal.user, "people.removed", "", removed=removed)
    return {"removed": removed}


MAX_UPLOAD = 60 * 1024 * 1024


@router.post("/directory/upload")
async def directory_upload(
    request: Request,
    principal: Directory,
    db: Db,
    file: Annotated[UploadFile, File()],
    country: Annotated[str, Form(pattern="^[A-Z]{2}$")],
    source: Annotated[str, Form(min_length=2, max_length=60)],
) -> dict[str, Any]:
    from orkesta.research import directory

    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "El archivo supera 60 MB. Divídelo en partes.")
    factory = request.app.state.session_factory
    key = f"archivo:{source}"
    started = directory.start(lambda: directory.run_upload(factory, raw, country, source), key)
    svc.log(db, principal.user, "directory.upload", source, country=country, bytes=len(raw))
    return {"started": started}


@router.get("/knowledge/history")
def knowledge_history(principal: Directory, db: Db) -> dict[str, Any]:
    """Entrenamientos anteriores: cuándo, cuántas filas y cuántas personas quedaron."""
    from orkesta.admin.models import AdminAudit

    rows = db.execute(
        select(AdminAudit.created_at, AdminAudit.detail)
        .where(AdminAudit.action == "directory.knowledge")
        .order_by(AdminAudit.created_at.desc())
        .limit(40)
    ).all()
    return {"items": [{"at": at.isoformat(), **{k: d.get(k) for k in (
        "files", "rows", "profiles", "companies", "domains", "with_domain", "country", "reports")}}
        for at, d in rows]}  # fmt: skip


@router.post("/knowledge")
async def train_knowledge(
    request: Request,
    principal: Directory,
    db: Db,
    files: Annotated[list[UploadFile], File()],
    country: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """Entrena la IA con bases reales (Wiza, registros, campañas) o con un archivo de
    conocimiento de ORKESTA. Solo se guarda lo que se sabe de cada empresa."""
    from starlette.concurrency import run_in_threadpool

    from orkesta.engine.files.reader import ImportFileError
    from orkesta.research import knowledge as kn

    if not files or len(files) > 40:
        raise HTTPException(422, "Sube entre 1 y 40 archivos.")
    from orkesta.engine.normalize.location import COUNTRIES

    default = country.strip().upper() if country.strip().upper() in COUNTRIES else ""
    learned = kn.Knowledge()
    errors: list[str] = []
    for upload in files:
        raw = await upload.read(MAX_UPLOAD + 1)
        name = upload.filename or "archivo"
        if len(raw) > MAX_UPLOAD:
            errors.append(f"{name}: supera 60 MB")
            continue
        try:
            if name.lower().endswith(".json"):
                await run_in_threadpool(kn.load_json, learned, raw)
            else:
                await run_in_threadpool(kn.learn_file, learned, raw, name, default)
        except (ImportFileError, ValueError) as exc:
            errors.append(f"{name}: {exc}"[:200])
    stats = await run_in_threadpool(kn.apply, request.app.state.session_factory, learned)
    reports = learned.reports
    svc.log(db, principal.user, "directory.knowledge", "", files=len(files), **stats,
            rows=sum(r["rows"] for r in reports), country=default,
            reports=[{k: r[k] for k in ("file", "rows", "people", "emails", "no_country",
                                        "no_name_or_company")} for r in reports])  # fmt: skip
    return {"people": learned.people, "files": learned.files, **stats,
            "evaluation": kn.evaluate(learned), "errors": errors, "reports": reports}  # fmt: skip


# --- Equipo, permisos y bitácora -------------------------------------------------------------


class MemberIn(BaseModel):
    email: str = Field(max_length=320)
    role: str = Field(pattern="^(admin|soporte|lectura)$")


class RoleIn(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|soporte|lectura)$")


Owner = Annotated[Principal, Depends(require("equipo"))]


@router.get("/team")
def team(principal: Staff, db: Db) -> dict[str, Any]:
    rows = sorted(
        db.scalars(select(User).where(User.platform_role.is_not(None))).all(),
        key=lambda u: (u.platform_role != "owner", u.created_at),
    )
    return {
        "members": [
            {
                **_user(u),
                "role": u.platform_role,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in rows
        ],
        "roles": svc.ROLES,
        "permissions": svc.PERMISSIONS,
        "matrix": svc.matrix(db),
        "can_edit": principal.user.platform_role == "owner",
    }


@router.post("/team", status_code=201)
def add_member(body: MemberIn, principal: Owner, db: Db) -> dict[str, Any]:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None:
        raise HTTPException(
            404, "Esa persona aún no tiene cuenta en ORKESTA. Pídele que se registre primero."
        )
    if user.platform_role == "owner":
        raise HTTPException(409, "Esa persona ya es la creadora.")
    user.platform_role, user.is_platform_admin = body.role, True
    svc.log(db, principal.user, "team.added", user.email, role=body.role)
    return {"ok": True}


@router.patch("/team/{user_id}")
def change_role(user_id: uuid.UUID, body: RoleIn, principal: Owner, db: Db) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None or user.platform_role is None:
        raise HTTPException(404, "No forma parte del equipo.")
    if user.platform_role == "owner":
        raise HTTPException(409, "El rol de la creadora no se puede cambiar.")
    before = user.platform_role
    user.platform_role = body.role
    user.is_platform_admin = body.role is not None
    svc.log(
        db,
        principal.user,
        "team.removed" if body.role is None else "team.role",
        user.email,
        before=before,
        after=body.role,
    )
    return {"ok": True}


@router.put("/permissions")
def put_permissions(body: dict[str, list[str]], principal: Owner, db: Db) -> dict[str, Any]:
    clean = svc.save_matrix(db, body)
    svc.log(db, principal.user, "permissions.updated", "")
    return {"matrix": clean}


@router.get("/audit")
def audit(
    principal: Annotated[Principal, Depends(require("bitacora"))], db: Db
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AdminAudit, User)
        .outerjoin(User, User.id == AdminAudit.actor_id)
        .order_by(AdminAudit.created_at.desc())
        .limit(200)
    ).tuples()
    return [
        {
            "id": str(a.id),
            "at": a.created_at.isoformat(),
            "actor": (u.full_name or u.email) if u else "—",
            "action": a.action,
            "target": a.target,
            "detail": a.detail,
        }
        for a, u in rows
    ]
