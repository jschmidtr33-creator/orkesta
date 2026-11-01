"""Contraseñas (Argon2), tokens de acceso (JWT corto) y tokens opacos (refresco, invitaciones)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from orkesta.core.config import get_settings

MIN_PASSWORD_LENGTH = 10
_hasher = PasswordHasher()
# Hash de referencia para gastar el mismo tiempo cuando el usuario no existe.
_DUMMY_HASH = _hasher.hash("orkesta-usuario-inexistente")


class PasswordPolicyError(ValueError):
    pass


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    if password.lower() == password or not any(c.isdigit() for c in password):
        raise PasswordPolicyError("Usa mayúsculas, minúsculas y al menos un número.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str | None
    platform_admin: bool


def issue_access_token(claims: AccessClaims) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(claims.user_id),
        "tid": str(claims.tenant_id) if claims.tenant_id else None,
        "role": claims.role,
        "pa": claims.platform_admin,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "typ": "access",
    }
    return jwt.encode(payload, settings.signing_key, algorithm="HS256")


def read_access_token(token: str) -> AccessClaims | None:
    try:
        data = jwt.decode(token, get_settings().signing_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "access":
        return None
    try:
        return AccessClaims(
            user_id=uuid.UUID(data["sub"]),
            tenant_id=uuid.UUID(data["tid"]) if data.get("tid") else None,
            role=data.get("role"),
            platform_admin=bool(data.get("pa")),
        )
    except (KeyError, ValueError):
        return None


def sign_state(data: dict[str, str], minutes: int = 10) -> str:
    """Estado firmado de corta vida (flujo OAuth de Google)."""
    payload = {**data, "exp": datetime.now(UTC) + timedelta(minutes=minutes), "typ": "state"}
    return jwt.encode(payload, get_settings().signing_key, algorithm="HS256")


def read_state(token: str) -> dict[str, str] | None:
    try:
        data = jwt.decode(token, get_settings().signing_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == "state" else None


ADMIN_UNLOCK_MINUTES = 30


def issue_admin_token(user_id: uuid.UUID, minutes: int = ADMIN_UNLOCK_MINUTES) -> str:
    """Pase del panel de administración: se emite solo tras confirmar la contraseña."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=minutes),
               "typ": "admin"}  # fmt: skip
    return jwt.encode(payload, get_settings().signing_key, algorithm="HS256")


def read_admin_token(token: str) -> uuid.UUID | None:
    try:
        data = jwt.decode(token, get_settings().signing_key, algorithms=["HS256"])
        return uuid.UUID(data["sub"]) if data.get("typ") == "admin" else None
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
