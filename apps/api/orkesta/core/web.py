"""Dependencias HTTP: sesión de base de datos, usuario actual, roles, cookies y límites."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from orkesta.core.config import get_settings
from orkesta.core.db import set_context, set_scope
from orkesta.core.security import read_access_token
from orkesta.platform.models import Membership, TeamAssignment, Tenant, User

ACCESS_COOKIE = "ork_at"
REFRESH_COOKIE = "ork_rt"


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # Un registro único que ya existe (a veces de otra persona del equipo, que no se ve).
        raise HTTPException(409, "Eso ya existe en tu empresa.") from exc
    except (StaleDataError, ProgrammingError) as exc:
        session.rollback()
        # La base rechazó modificar trabajo de otra persona (políticas de supervisión).
        if isinstance(exc, StaleDataError) or "row-level security" in str(exc):
            raise HTTPException(403, READ_ONLY) from exc
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


READ_ONLY = "Puedes ver el trabajo de tu equipo, pero solo su dueño puede modificarlo."


Db = Annotated[Session, Depends(get_db, scope="function")]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    secure = settings.is_deployed
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax", path="/",
        max_age=settings.access_token_minutes * 60,
    )  # fmt: skip
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=secure, samesite="lax", path="/api/auth",
        max_age=settings.refresh_token_days * 86400,
    )  # fmt: skip


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")


@dataclass
class Principal:
    user: User
    tenant: Tenant | None
    membership: Membership | None

    @property
    def tenant_id(self) -> uuid.UUID:
        if self.tenant is None:
            raise HTTPException(409, "Primero crea o elige un espacio de trabajo.")
        return self.tenant.id

    @property
    def role(self) -> str | None:
        return self.membership.role if self.membership else None

    def can_modify(self, owner: uuid.UUID | None) -> bool:
        """Solo la administración modifica trabajo ajeno; la jefatura solo lo ve."""
        return self.role == "admin" or owner is None or owner == self.user.id


def current_principal(request: Request, db: Db) -> Principal:
    claims = read_access_token(request.cookies.get(ACCESS_COOKIE, ""))
    if claims is None:
        raise HTTPException(401, "Inicia sesión.")
    user = db.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Inicia sesión.")
    set_context(db, tenant_id=claims.tenant_id, user_id=user.id)
    tenant = membership = None
    if claims.tenant_id is not None:
        # El rol se lee de la base en cada solicitud: un cambio de rol o una baja rige al instante.
        membership = db.scalar(
            select(Membership).where(
                Membership.tenant_id == claims.tenant_id,
                Membership.user_id == user.id,
                Membership.status == "active",
            )
        )
        if membership is None:
            raise HTTPException(401, "Ya no tienes acceso a este espacio de trabajo.")
        tenant = db.get(Tenant, claims.tenant_id)
        set_scope(db, visible_users(db, membership))
    return Principal(user=user, tenant=tenant, membership=membership)


def visible_users(db: Session, membership: Membership) -> list[uuid.UUID] | None:
    """De quién es el trabajo que ve esta persona. ``None``: toda la empresa (administración).

    - Administrador: todo.
    - Jefatura: lo suyo y lo de las personas que tiene a cargo (sin poder modificarlo).
    - Vendedor: solo lo suyo."""
    if membership.role == "admin":
        return None
    users = [membership.user_id]
    if membership.role == "supervisor":
        users += db.scalars(
            select(TeamAssignment.member_id).where(
                TeamAssignment.tenant_id == membership.tenant_id,
                TeamAssignment.manager_id == membership.user_id,
            )
        ).all()
    return users


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_roles(*roles: str) -> Callable[[Principal, Session], Principal]:
    def dependency(principal: CurrentPrincipal, db: Db) -> Principal:
        if principal.tenant is None:
            raise HTTPException(409, "Primero crea o elige un espacio de trabajo.")
        if principal.role not in roles:
            raise HTTPException(403, "Tu rol no tiene permiso para esta acción.")
        if not principal.user.platform_role:
            if principal.tenant.status == "suspended":
                raise HTTPException(
                    423, "Este espacio de trabajo está suspendido. Escríbenos para reactivarlo."
                )
            from orkesta.admin.service import app_settings

            app = app_settings(db)
            if app["maintenance"]:
                raise HTTPException(503, app["maintenance_message"])
        return principal

    return dependency


def require_platform_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.user.is_platform_admin:
        raise HTTPException(403, "Solo para administradores de la plataforma.")
    return principal


class RateLimiter:
    """Límite simple por IP en memoria (una instancia). Suficiente para formularios públicos."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self.hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def check(self, key: str) -> None:
        if not self.allow(key):
            raise HTTPException(429, "Demasiadas solicitudes. Intenta de nuevo en unos minutos.")
