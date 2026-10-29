"""Usuarios, contraseñas (Argon2id) y limitación de intentos de inicio de sesión."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.db.models import Organization, User

MIN_PASSWORD_LENGTH = 12
_hasher = PasswordHasher()
# Se verifica contra este hash cuando el correo no existe, para que el tiempo de respuesta no
# revele qué correos están registrados.
_DUMMY_HASH = _hasher.hash("orkesta-usuario-inexistente")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    """Error de validación de usuario o contraseña, con mensaje para mostrar."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not _EMAIL.match(value) or len(value) > 320:
        raise AuthError("El correo no es válido.")
    return value


def authenticate(session: Session, email: str, password: str) -> User | None:
    try:
        normalized = normalize_email(email)
    except AuthError:
        verify_password(_DUMMY_HASH, password)
        return None
    user = session.scalar(select(User).where(func.lower(User.email) == normalized))
    if user is None or not user.is_active:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = session.scalar(select(func.now()))
    return user


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "org")[:60]


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    organization_id: uuid.UUID,
    role: str = "operator",
) -> User:
    normalized = normalize_email(email)
    validate_new_password(password)
    exists = session.scalar(select(User.id).where(func.lower(User.email) == normalized))
    if exists is not None:
        raise AuthError("Ya existe un usuario con ese correo.")
    user = User(
        organization_id=organization_id,
        email=normalized,
        password_hash=hash_password(password),
        role=role,
    )
    session.add(user)
    session.flush()
    return user


def get_or_create_organization(session: Session, name: str) -> Organization:
    existing = session.scalar(select(Organization).order_by(Organization.created_at).limit(1))
    if existing is not None:
        return existing
    org = Organization(name=name.strip() or "ORKESTA", slug=slugify(name))
    session.add(org)
    session.flush()
    return org


@dataclass
class LoginThrottle:
    """Bloquea temporalmente un correo + IP tras varios intentos fallidos.

    Vive en memoria del proceso: con varias réplicas web cada una cuenta por separado. Basta
    para el MVP con una réplica; si se escala, mover el contador a PostgreSQL.
    """

    max_failures: int
    window_seconds: int
    _failures: dict[tuple[str, str], deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        attempts = self._failures.setdefault(key, deque())
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        return attempts

    def is_locked(self, email: str, ip: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            return len(self._recent((email.lower(), ip), current)) >= self.max_failures

    def record_failure(self, email: str, ip: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._recent((email.lower(), ip), current).append(current)

    def reset(self, email: str, ip: str) -> None:
        with self._lock:
            self._failures.pop((email.lower(), ip), None)
