"""API del panel de administración de ORKESTA: /api/admin/…"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from orkesta.admin import service as svc
from orkesta.admin.models import AdminAudit
from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.web import CurrentPrincipal, Db, Principal
from orkesta.platform.models import AuthSession, DemoRequest, Membership, Tenant, User

router = APIRouter(prefix="/api/admin")


def require(permission: str) -> Callable[[Principal, Any], Principal]:
    def dependency(principal: CurrentPrincipal, db: Db) -> Principal:
        if not principal.user.platform_role:
            raise HTTPException(403, "No tienes acceso al panel de administración.")
        if permission and permission not in svc.permissions_for(db, principal.user):
            raise HTTPException(403, "Tu rol en el panel no permite esta acción.")
        return principal

    return dependency


Staff = Annotated[Principal, Depends(require(""))]


def _user(u: User) -> dict[str, Any]:
    return {"id": str(u.id), "name": u.full_name or u.email, "email": u.email}


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
        tenant.plan = body.plan
    return {"ok": True, "status": tenant.status, "plan": tenant.plan}


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
