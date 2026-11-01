"""API de la plataforma: autenticación, espacio de trabajo, equipo, auditoría y sitio público."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.security import PasswordPolicyError
from orkesta.core.web import (
    REFRESH_COOKIE,
    CurrentPrincipal,
    Db,
    Principal,
    RateLimiter,
    clear_auth_cookies,
    client_ip,
    require_platform_admin,
    require_roles,
    set_auth_cookies,
)
from orkesta.crm import service as crm_service
from orkesta.data import service as data_service
from orkesta.platform import google
from orkesta.platform import service as svc
from orkesta.platform.models import (
    ROLE_LABELS,
    AuditLog,
    DemoRequest,
    Invitation,
    Membership,
    Tenant,
    User,
)

router = APIRouter(prefix="/api")
demo_limiter = RateLimiter(limit=5, window_seconds=3600)
register_limiter = RateLimiter(limit=10, window_seconds=3600)
GOOGLE_STATE_COOKIE = "ork_gs"

Admin = Annotated[Principal, Depends(require_roles("admin"))]
Manager = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
PlatformAdmin = Annotated[Principal, Depends(require_platform_admin)]


def _fail(exc: Exception) -> HTTPException:
    status = exc.status if isinstance(exc, svc.PlatformError) else 400
    return HTTPException(status, str(exc))


# --- Esquemas ------------------------------------------------------------------------------


class RegisterIn(BaseModel):
    company: str = Field(max_length=160)
    full_name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class WorkspaceIn(BaseModel):
    name: str = Field(max_length=160)


class SwitchIn(BaseModel):
    tenant_id: uuid.UUID


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    role: str


class MemberPatch(BaseModel):
    role: str | None = None
    active: bool | None = None


class AcceptIn(BaseModel):
    full_name: str = Field(default="", max_length=200)
    password: str | None = Field(default=None, max_length=200)


class DemoIn(BaseModel):
    name: str = Field(max_length=160)
    email: str = Field(max_length=320)
    company: str = Field(max_length=160)
    job_title: str = Field(default="", max_length=160)
    country: str = Field(default="", max_length=80)
    message: str = Field(default="", max_length=2000)
    consent: bool = False


def _session_payload(db: Db, user: User, tenant_id: uuid.UUID | None) -> dict[str, Any]:
    workspaces = [
        {"id": str(t.id), "name": t.name, "role": m.role, "role_label": ROLE_LABELS[m.role]}
        for m, t in svc.active_memberships(db, user.id)
    ]
    current = next((w for w in workspaces if w["id"] == str(tenant_id)), None)
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "platform_admin": user.is_platform_admin,
            "has_password": user.password_hash is not None,
        },
        "workspace": current,
        "workspaces": workspaces,
    }


def _issue(db: Db, tokens: svc.Tokens, response: Response) -> dict[str, Any]:
    set_auth_cookies(response, tokens.access, tokens.refresh)
    user = db.get(User, tokens.claims.user_id)
    assert user is not None  # noqa: S101
    return _session_payload(db, user, tokens.claims.tenant_id)


# --- Autenticación -------------------------------------------------------------------------


@router.get("/health")
def health() -> dict[str, str]:
    # Render expone el commit desplegado; la app lo usa para avisar que hay versión nueva.
    return {"status": "ok", "version": os.environ.get("RENDER_GIT_COMMIT", "dev")[:12]}


@router.get("/auth/config")
def auth_config() -> dict[str, bool]:
    return {"google": get_settings().google_enabled}


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, response: Response, db: Db) -> dict[str, Any]:
    ip = client_ip(request)
    register_limiter.check(ip or "?")
    try:
        user, tenant = svc.register(
            db, company=body.company, full_name=body.full_name, email=body.email,
            password=body.password, ip=ip,
        )  # fmt: skip
        tokens = svc.start_session(
            db, user, tenant_id=tenant.id, user_agent=request.headers.get("user-agent"), ip=ip
        )
    except (svc.PlatformError, PasswordPolicyError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    return _issue(db, tokens, response)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Db) -> Any:
    try:
        tokens = svc.login(
            db, email=body.email, password=body.password,
            user_agent=request.headers.get("user-agent"), ip=client_ip(request),
        )  # fmt: skip
    except svc.PlatformError as exc:
        # El intento fallido se guarda igual (límite de intentos).
        db.commit()
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)
    return _issue(db, tokens, response)


@router.post("/auth/refresh")
def refresh(request: Request, response: Response, db: Db) -> Any:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(401, "Inicia sesión.")
    try:
        tokens = svc.refresh_session(db, token, ip=client_ip(request))
    except svc.PlatformError as exc:
        db.commit()  # una reutilización sospechosa ya revocó las sesiones
        failed = JSONResponse({"detail": str(exc)}, status_code=exc.status)
        clear_auth_cookies(failed)
        return failed
    return _issue(db, tokens, response)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Db) -> dict[str, bool]:
    svc.end_session(db, request.cookies.get(REFRESH_COOKIE))
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/auth/me")
def me(principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    return _session_payload(db, principal.user, principal.tenant.id if principal.tenant else None)


@router.get("/auth/session")
def current_session(request: Request, response: Response, db: Db) -> dict[str, Any] | None:
    """Sesión actual sin errores cuando no la hay (la app lo consulta al abrir). Si el acceso
    venció pero el token de refresco sigue vigente, la renueva aquí mismo."""
    from orkesta.core.web import current_principal

    try:
        principal = current_principal(request, db)
    except HTTPException:
        db.rollback()
        token = request.cookies.get(REFRESH_COOKIE)
        if not token:
            return None
        try:
            tokens = svc.refresh_session(db, token, ip=client_ip(request))
        except svc.PlatformError:
            db.commit()
            clear_auth_cookies(response)
            return None
        return _issue(db, tokens, response)
    return _session_payload(db, principal.user, principal.tenant.id if principal.tenant else None)


@router.post("/auth/switch")
def switch_workspace(
    body: SwitchIn, request: Request, response: Response, principal: CurrentPrincipal, db: Db
) -> dict[str, Any]:
    svc.end_session(db, request.cookies.get(REFRESH_COOKIE))
    try:
        tokens = svc.start_session(
            db, principal.user, tenant_id=body.tenant_id,
            user_agent=request.headers.get("user-agent"), ip=client_ip(request),
        )  # fmt: skip
    except svc.PlatformError as exc:
        raise _fail(exc) from exc
    return _issue(db, tokens, response)


@router.get("/auth/google/start")
def google_start(next: str = "/app") -> Response:  # noqa: A002 - nombre del parámetro público
    if not get_settings().google_enabled:
        return RedirectResponse("/app/ingresar?error=google_pendiente", status_code=303)
    url, state = google.authorization_url(next)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        GOOGLE_STATE_COOKIE, state, httponly=True, secure=get_settings().is_deployed,
        samesite="lax", max_age=600, path="/api/auth/google",
    )  # fmt: skip
    return response


@router.get("/auth/google/callback")
def google_callback(
    request: Request, db: Db, code: str = "", state: str = "", error: str = ""
) -> Response:
    if error or not code:
        return RedirectResponse("/app/ingresar?error=google_cancelado", status_code=303)
    try:
        identity, next_path = google.exchange_code(
            code, state, request.cookies.get(GOOGLE_STATE_COOKIE)
        )
    except google.GoogleAuthError:
        return RedirectResponse("/app/ingresar?error=google", status_code=303)
    user = db.scalar(select(User).where(User.google_sub == identity.sub))
    if user is None:
        user = db.scalar(select(User).where(User.email == identity.email))
        if user is not None:
            user.google_sub = identity.sub  # mismo correo verificado por Google: se vincula
        else:
            user = svc.create_user(
                db, email=identity.email, full_name=identity.name, google_sub=identity.sub
            )
    if not user.is_active:
        return RedirectResponse("/app/ingresar?error=cuenta_inactiva", status_code=303)
    tokens = svc.start_session(
        db, user, user_agent=request.headers.get("user-agent"), ip=client_ip(request)
    )
    target = next_path if tokens.claims.tenant_id else "/app/bienvenida"
    response = RedirectResponse(target, status_code=303)
    set_auth_cookies(response, tokens.access, tokens.refresh)
    response.delete_cookie(GOOGLE_STATE_COOKIE, path="/api/auth/google")
    return response


# --- Espacio de trabajo --------------------------------------------------------------------


@router.post("/workspaces")
def create_workspace(
    body: WorkspaceIn, request: Request, response: Response, principal: CurrentPrincipal, db: Db
) -> dict[str, Any]:
    try:
        tenant = svc.create_tenant(db, owner=principal.user, name=body.name, ip=client_ip(request))
        tokens = svc.start_session(
            db, principal.user, tenant_id=tenant.id,
            user_agent=request.headers.get("user-agent"), ip=client_ip(request),
        )  # fmt: skip
    except svc.PlatformError as exc:
        raise _fail(exc) from exc
    return _issue(db, tokens, response)


@router.patch("/workspace")
def rename_workspace(
    body: WorkspaceIn, request: Request, principal: Admin, db: Db
) -> dict[str, str]:
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "Escribe el nombre de la empresa.")
    tenant = principal.tenant
    assert tenant is not None  # noqa: S101
    previous, tenant.name = tenant.name, name
    svc.audit(
        db, tenant_id=tenant.id, actor_id=principal.user.id, action="workspace.renamed",
        ip=client_ip(request), before=previous, after=name,
    )  # fmt: skip
    return {"name": tenant.name}


# --- Equipo --------------------------------------------------------------------------------


@router.get("/team")
def team(principal: Member, db: Db) -> dict[str, Any]:
    tid = principal.tenant_id
    members = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tid)
        .order_by(User.full_name, User.email)
    ).tuples()
    invitations = db.scalars(
        select(Invitation)
        .where(
            Invitation.tenant_id == tid,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > func.now(),
        )
        .order_by(Invitation.created_at.desc())
    )
    return {
        "roles": [{"value": k, "label": v} for k, v in ROLE_LABELS.items()],
        "members": [
            {
                "id": str(m.id),
                "user_id": str(u.id),
                "full_name": u.full_name,
                "email": u.email,
                "role": m.role,
                "active": m.status == "active",
                "is_me": u.id == principal.user.id,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for m, u in members
        ],
        "invitations": [
            {
                "id": str(i.id),
                "email": i.email,
                "role": i.role,
                "expires_at": i.expires_at.isoformat(),
            }
            for i in invitations
        ],
    }


@router.post("/team/invitations")
def invite(body: InviteIn, request: Request, principal: Admin, db: Db) -> dict[str, str]:
    try:
        invitation, token = svc.invite(
            db, tenant_id=principal.tenant_id, inviter_id=principal.user.id,
            email=body.email, role=body.role, ip=client_ip(request),
        )  # fmt: skip
    except svc.PlatformError as exc:
        raise _fail(exc) from exc
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    # Sin servicio de correo en esta etapa: el administrador copia y comparte el enlace.
    return {"id": str(invitation.id), "link": f"{base}/app/invitacion/{token}"}


@router.delete("/team/invitations/{invitation_id}")
def revoke(invitation_id: uuid.UUID, principal: Admin, db: Db) -> dict[str, bool]:
    try:
        svc.revoke_invitation(
            db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
            invitation_id=invitation_id,
        )  # fmt: skip
    except svc.PlatformError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


@router.patch("/team/members/{membership_id}")
def update_member(
    membership_id: uuid.UUID, body: MemberPatch, principal: Admin, db: Db
) -> dict[str, bool]:
    try:
        svc.update_member(
            db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
            membership_id=membership_id, role=body.role, active=body.active,
        )  # fmt: skip
    except svc.PlatformError as exc:
        db.rollback()
        raise _fail(exc) from exc
    return {"ok": True}


@router.get("/invitations/{token}")
def invitation_info(token: str, db: Db) -> dict[str, str]:
    try:
        invitation, tenant = svc.find_invitation(db, token)
    except svc.PlatformError as exc:
        raise _fail(exc) from exc
    exists = db.scalar(select(User.id).where(User.email == invitation.email)) is not None
    return {
        "workspace": tenant.name,
        "email": invitation.email,
        "role": ROLE_LABELS[invitation.role],
        "account_exists": "yes" if exists else "no",
    }


@router.post("/invitations/{token}/accept")
def accept(
    token: str, body: AcceptIn, request: Request, response: Response, db: Db
) -> dict[str, Any]:
    from orkesta.core.security import read_access_token
    from orkesta.core.web import ACCESS_COOKIE

    claims = read_access_token(request.cookies.get(ACCESS_COOKIE, ""))
    current = db.get(User, claims.user_id) if claims else None
    try:
        user = svc.accept_invitation(
            db, token=token, user=current, full_name=body.full_name, password=body.password,
            ip=client_ip(request),
        )  # fmt: skip
        tokens = svc.start_session(
            db, user, tenant_id=user.last_tenant_id,
            user_agent=request.headers.get("user-agent"), ip=client_ip(request),
        )  # fmt: skip
    except (svc.PlatformError, PasswordPolicyError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    return _issue(db, tokens, response)


# --- Auditoría e inicio --------------------------------------------------------------------


ACTION_LABELS = {
    "enrichment.started": "inició un enriquecimiento de contactos",
    "deal.created": "creó una oportunidad",
    "deal.deleted": "eliminó una oportunidad",
    "validation.started": "inició una validación de correos",
    "research.started": "inició una investigación de prospectos",
    "research.saved": "guardó decisores investigados en Contactos",
    "tenant.created": "creó el espacio de trabajo",
    "workspace.renamed": "cambió el nombre de la empresa",
    "member.invited": "invitó a una persona",
    "member.updated": "cambió el rol o el acceso de un miembro",
    "invitation.accepted": "aceptó una invitación",
    "invitation.revoked": "anuló una invitación",
    "contacts.imported": "importó una base de contactos",
    "contacts.exported": "exportó contactos",
    "contacts.deleted": "eliminó contactos",
    "contact.created": "creó un contacto",
    "contact.updated": "editó un contacto",
}


@router.get("/audit")
def audit_log(principal: Manager, db: Db, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AuditLog, User)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.tenant_id == principal.tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(max(1, min(limit, 200)))
    ).tuples()
    return [
        {
            "id": str(a.id),
            "at": a.created_at.isoformat(),
            "actor": (u.full_name or u.email) if u else "Sistema",
            "action": a.action,
            "label": ACTION_LABELS.get(a.action, a.action),
            "detail": a.detail,
        }
        for a, u in rows
    ]


@router.get("/dashboard/summary")
def dashboard(principal: Member, db: Db) -> dict[str, Any]:
    tid = principal.tenant_id
    members = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.tenant_id == tid, Membership.status == "active")
    )
    pending = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.tenant_id == tid,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > func.now(),
        )
    )
    # Indicadores comerciales: ``None`` = el módulo todavía no existe (no se inventan cifras).
    crm_metrics = crm_service.metrics(db, tid)
    return {
        "team": {"members": members or 0, "pending_invitations": pending or 0},
        "contacts": data_service.contacts_total(db, tid),
        "pipeline": crm_metrics["pipeline"],
        "kpis": {
            "contacts_found": None,
            "emails_validated": data_service.emails_validated(db, tid),
            "open_opportunities": crm_metrics["open"],
            "conversion_rate": crm_metrics["conversion_rate"],
        },
        "integrations": {
            "google_sign_in": get_settings().google_enabled,
            "web_search": bool(get_settings().serper_api_key),
        },
    }


# --- Sitio público y plataforma ------------------------------------------------------------


@router.post("/public/demo-requests", status_code=201)
def demo_request(body: DemoIn, request: Request, db: Db) -> dict[str, bool]:
    ip = client_ip(request)
    demo_limiter.check(ip or "?")
    try:
        svc.create_demo_request(
            db, name=body.name, email=body.email, company=body.company,
            job_title=body.job_title, country=body.country, message=body.message,
            consent=body.consent, ip=ip,
        )  # fmt: skip
    except svc.PlatformError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


@router.get("/platform/overview")
def platform_overview(principal: PlatformAdmin, db: Db) -> dict[str, Any]:
    set_context(db, user_id=principal.user.id)
    demos = db.scalars(select(DemoRequest).order_by(DemoRequest.created_at.desc()).limit(100))
    return {
        "tenants": db.scalar(select(func.count()).select_from(Tenant)) or 0,
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "demo_requests": [
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
            for d in demos
        ],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
