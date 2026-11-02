"""Panel de administración: roles del equipo interno, permisos, configuración y métricas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.admin.models import AdminAudit, PlatformSetting
from orkesta.core.db import set_context
from orkesta.platform.models import Membership, Tenant, User

ROLES = {
    "owner": "Creadora",
    "admin": "Administrador",
    "soporte": "Soporte",
    "lectura": "Solo lectura",
}
PERMISSIONS = {
    "ver_resumen": "Ver resumen y métricas de la plataforma",
    "ver_empresas": "Ver empresas y su uso",
    "gestionar_empresas": "Suspender o reactivar empresas y cambiar su plan",
    "ver_usuarios": "Ver usuarios y sus correos",
    "gestionar_usuarios": "Suspender o reactivar usuarios y cerrar sus sesiones",
    "ver_demos": "Ver solicitudes de demo del sitio",
    "configuracion": "Configuración de la app (registro, mantenimiento, anuncio)",
    "planes": "Planes y límites por plan",
    "integraciones": "Ver el estado de las integraciones",
    "bitacora": "Ver la bitácora del equipo",
    "directorio": "Directorio de empresas: cargar registros públicos",
}
DEFAULT_MATRIX = {
    "admin": list(PERMISSIONS),
    "soporte": [
        "ver_resumen",
        "ver_empresas",
        "ver_usuarios",
        "gestionar_usuarios",
        "ver_demos",
        "integraciones",
    ],
    "lectura": ["ver_resumen", "ver_empresas", "ver_usuarios", "ver_demos"],
}
PLANS = {"inicial": "Inicial", "pro": "Pro", "empresa": "Empresa", "cortesia": "Cortesía"}
DEFAULT_APP: dict[str, Any] = {
    "signup_open": True,
    "maintenance": False,
    "maintenance_message": "Estamos mejorando ORKESTA. Volvemos en unos minutos.",
    "announcement": {"active": False, "text": "", "tone": "blue"},
    "limits": {
        "inicial": {"contacts": 5000, "emails_per_day": 80, "users": 3},
        "pro": {"contacts": 50000, "emails_per_day": 300, "users": 15},
        "empresa": {"contacts": 500000, "emails_per_day": 2000, "users": 100},
        "cortesia": {"contacts": 50000, "emails_per_day": 300, "users": 15},
    },
}


class AdminError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def _get(session: Session, key: str, default: Any) -> Any:
    row = session.get(PlatformSetting, key)
    if row is None:
        return default
    if isinstance(default, dict):
        merged = {**default, **row.value}
        return merged
    return row.value


def _put(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(PlatformSetting, key)
    if row is None:
        session.add(PlatformSetting(key=key, value=value))
    else:
        row.value = value
    session.flush()


def matrix(session: Session) -> dict[str, list[str]]:
    data = _get(session, "permissions", {"roles": DEFAULT_MATRIX})
    roles = data.get("roles") or DEFAULT_MATRIX
    return {
        r: [p for p in roles.get(r, DEFAULT_MATRIX[r]) if p in PERMISSIONS] for r in DEFAULT_MATRIX
    }


def permissions_for(session: Session, user: User) -> set[str]:
    role = user.platform_role
    if role == "owner":
        return set(PERMISSIONS) | {"equipo"}
    if role in DEFAULT_MATRIX:
        return set(matrix(session)[role])
    return set()


def save_matrix(session: Session, roles: dict[str, list[str]]) -> dict[str, list[str]]:
    clean = {r: sorted({p for p in roles.get(r, []) if p in PERMISSIONS}) for r in DEFAULT_MATRIX}
    _put(session, "permissions", {"roles": clean})
    return clean


def app_settings(session: Session) -> dict[str, Any]:
    data: dict[str, Any] = dict(_get(session, "app", DEFAULT_APP))
    data["announcement"] = {**DEFAULT_APP["announcement"], **(data.get("announcement") or {})}
    limits: dict[str, Any] = dict(DEFAULT_APP["limits"])
    limits.update(data.get("limits") or {})
    data["limits"] = limits
    return data


def save_app_settings(session: Session, raw: dict[str, Any]) -> dict[str, Any]:
    data = app_settings(session)
    if "signup_open" in raw:
        data["signup_open"] = bool(raw["signup_open"])
    if "maintenance" in raw:
        data["maintenance"] = bool(raw["maintenance"])
    if "maintenance_message" in raw:
        data["maintenance_message"] = str(raw["maintenance_message"] or "").strip()[:300]
    if "announcement" in raw:
        a = dict(raw["announcement"] or {})
        tone = a.get("tone") if a.get("tone") in ("blue", "ok", "warn", "bad") else "blue"
        data["announcement"] = {
            "active": bool(a.get("active")),
            "text": str(a.get("text") or "").strip()[:400],
            "tone": tone,
        }
    if "limits" in raw:
        limits = {}
        for plan in PLANS:
            src = (raw["limits"] or {}).get(plan) or data["limits"].get(plan) or {}
            limits[plan] = {
                k: max(0, int(src.get(k, 0) or 0)) for k in ("contacts", "emails_per_day", "users")
            }
        data["limits"] = limits
    _put(session, "app", data)
    return data


def log(session: Session, actor: User, action: str, target: str = "", **detail: Any) -> None:
    session.add(AdminAudit(actor_id=actor.id, action=action, target=target[:200], detail=detail))


# --- Métricas -------------------------------------------------------------------------------


def _tenant_usage(session: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """Cifras de uso de una empresa (cada tabla está protegida por RLS: se fija su contexto)."""
    from orkesta.campaigns.models import Message
    from orkesta.crm.models import Deal
    from orkesta.data.models import Contact
    from orkesta.proposals.models import Proposal
    from orkesta.research.models import ResearchRun

    set_context(session, tenant_id=tenant_id)

    def count(model: Any) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
            )
            or 0
        )

    usage = {
        "contacts": count(Contact),
        "emails_sent": count(Message),
        "deals": count(Deal),
        "proposals": count(Proposal),
        "research_runs": count(ResearchRun),
    }
    set_context(session)
    return usage


def tenant_rows(session: Session, q: str = "", limit: int = 200) -> list[dict[str, Any]]:
    query = select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)
    if q:
        query = query.where(func.lower(Tenant.name).contains(q.lower().strip()))
    rows = []
    for t in session.scalars(query).all():
        members = _members(session, t.id)
        owner = _owner(session, t.id)
        rows.append(
            {
                "id": str(t.id),
                "name": t.name,
                "plan": t.plan,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
                "members": members,
                "owner": owner,
                **_tenant_usage(session, t.id),
            }
        )
    return rows


def _members(session: Session, tenant_id: uuid.UUID) -> int:
    set_context(session, tenant_id=tenant_id)
    n = session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.tenant_id == tenant_id, Membership.status == "active")
    )
    set_context(session)
    return int(n or 0)


def _owner(session: Session, tenant_id: uuid.UUID) -> dict[str, str] | None:
    set_context(session, tenant_id=tenant_id)
    row = session.execute(
        select(User.full_name, User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id, Membership.role == "admin")
        .order_by(Membership.created_at)
        .limit(1)
    ).first()
    set_context(session)
    return {"name": row[0] or row[1], "email": row[1]} if row else None


def overview(session: Session) -> dict[str, Any]:
    since7, since30 = now() - timedelta(days=7), now() - timedelta(days=30)
    users = int(session.scalar(select(func.count()).select_from(User)) or 0)
    tenants = session.scalars(select(Tenant)).all()
    usage = {t.id: _tenant_usage(session, t.id) for t in tenants}
    totals = {
        k: sum(u[k] for u in usage.values())
        for k in ("contacts", "emails_sent", "deals", "proposals", "research_runs")
    }
    day = func.date_trunc("day", func.timezone("America/Bogota", User.created_at))
    signups = session.execute(
        select(day.label("d"), func.count()).where(User.created_at >= since30).group_by("d")
    ).all()
    by_day = {r.d.date().isoformat(): int(r[1]) for r in signups}
    series = []
    for i in range(30):
        d = (now() - timedelta(days=29 - i)).astimezone().date().isoformat()
        series.append({"date": d, "signups": by_day.get(d, 0)})
    top = sorted(tenants, key=lambda t: -usage[t.id]["contacts"])[:5]
    return {
        "tenants": len(tenants),
        "tenants_active": sum(1 for t in tenants if t.status == "active"),
        "tenants_new_30d": sum(1 for t in tenants if t.created_at >= since30),
        "users": users,
        "users_active_7d": int(
            session.scalar(
                select(func.count()).select_from(User).where(User.last_login_at >= since7)
            )
            or 0
        ),
        "users_new_30d": int(
            session.scalar(select(func.count()).select_from(User).where(User.created_at >= since30))
            or 0
        ),
        "totals": totals,
        "signups": series,
        "top_tenants": [
            {"id": str(t.id), "name": t.name, "plan": t.plan, **usage[t.id]} for t in top
        ],
    }
