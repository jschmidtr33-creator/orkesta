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
from sqlalchemy.orm import Session

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.security import read_access_token
from orkesta.platform.models import Membership, Tenant, User

ACCESS_COOKIE = "ork_at"
REFRESH_COOKIE = "ork_rt"


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


Db = Annotated[Session, Depends(get_db)]


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
    return Principal(user=user, tenant=tenant, membership=membership)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_roles(*roles: str) -> Callable[[Principal], Principal]:
    def dependency(principal: CurrentPrincipal) -> Principal:
        if principal.tenant is None:
            raise HTTPException(409, "Primero crea o elige un espacio de trabajo.")
        if principal.role not in roles:
            raise HTTPException(403, "Tu rol no tiene permiso para esta acción.")
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

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self.hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise HTTPException(429, "Demasiadas solicitudes. Intenta de nuevo en unos minutos.")
        hits.append(now)
