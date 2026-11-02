"""Mi perfil: nombre, cargo, teléfono, correo de recuperación, zona horaria, foto y contraseña.

La foto se guarda en la base (máx. 512 KB, ya reducida por el navegador) y se muestra en la
barra lateral, en el equipo y junto a todo lo que la persona registra (actividad, auditoría).
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any
from zoneinfo import available_timezones

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from orkesta.core.security import (
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from orkesta.core.web import CurrentPrincipal, Db, client_ip
from orkesta.platform import service as svc
from orkesta.platform.models import Membership, User

router = APIRouter(prefix="/api")

MAX_AVATAR = 512 * 1024
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
_SIGNATURES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


def profile_dict(user: User) -> dict[str, Any]:
    return {
        "full_name": user.full_name,
        "email": user.email,
        "job_title": user.job_title or "",
        "phone": user.phone or "",
        "recovery_email": user.recovery_email or "",
        "timezone": user.timezone or "",
        "avatar_url": user.avatar_url,
        "has_password": user.password_hash is not None,
        "mfa_enabled": user.mfa_enabled,
        "recovery_left": len(user.mfa_recovery or []) if user.mfa_enabled else 0,
        "digest_enabled": user.digest_enabled,
    }


def _audit(db: Db, principal: CurrentPrincipal, request: Request, action: str) -> None:
    if principal.tenant is not None:
        svc.audit(
            db,
            tenant_id=principal.tenant.id,
            actor_id=principal.user.id,
            action=action,
            entity_type="user",
            entity_id=principal.user.id,
            ip=client_ip(request),
        )


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    recovery_email: str | None = Field(default=None, max_length=320)
    timezone: str | None = Field(default=None, max_length=60)


@router.get("/profile")
def get_profile(principal: CurrentPrincipal) -> dict[str, Any]:
    return profile_dict(principal.user)


@router.patch("/profile")
def update_profile(
    body: ProfileIn, request: Request, principal: CurrentPrincipal, db: Db
) -> dict[str, Any]:
    user = principal.user
    if body.full_name is not None:
        name = " ".join(body.full_name.split())
        if len(name) < 2:
            raise HTTPException(422, "Escribe tu nombre.")
        user.full_name = name
    if body.job_title is not None:
        user.job_title = " ".join(body.job_title.split()) or None
    if body.phone is not None:
        phone = re.sub(r"[^\d+()\s-]", "", body.phone).strip()
        user.phone = phone or None
    if body.recovery_email is not None:
        email = body.recovery_email.strip().lower()
        if email and not _EMAIL.match(email):
            raise HTTPException(422, "El correo de recuperación no es válido.")
        if email and email == user.email:
            raise HTTPException(422, "Usa un correo distinto al de tu cuenta.")
        user.recovery_email = email or None
    if body.timezone is not None:
        if body.timezone and body.timezone not in available_timezones():
            raise HTTPException(422, "Zona horaria desconocida.")
        user.timezone = body.timezone or None
    _audit(db, principal, request, "profile.updated")
    return profile_dict(user)


@router.put("/profile/avatar")
async def upload_avatar(
    request: Request, principal: CurrentPrincipal, db: Db, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    raw = await file.read(MAX_AVATAR + 1)
    if len(raw) > MAX_AVATAR:
        raise HTTPException(413, "La foto supera 512 KB.")
    kind = next((t for sig, t in _SIGNATURES.items() if raw.startswith(sig)), "")
    if not kind and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        kind = "image/webp"
    if not kind:
        raise HTTPException(422, "Sube una foto JPG, PNG o WebP.")
    user = principal.user
    user.avatar, user.avatar_type = raw, kind
    user.avatar_version = (user.avatar_version or 0) + 1
    _audit(db, principal, request, "profile.photo")
    return profile_dict(user)


@router.delete("/profile/avatar")
def delete_avatar(request: Request, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    user = principal.user
    user.avatar, user.avatar_type = None, None
    user.avatar_version = (user.avatar_version or 0) + 1
    return profile_dict(user)


# --- Doble verificación ---------------------------------------------------------------------


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


@router.post("/profile/mfa/setup")
def mfa_setup(principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    """Nuevo secreto (aún sin activar): se muestra como código QR y como texto."""
    from orkesta.campaigns.gmail import encrypt
    from orkesta.platform import mfa

    user = principal.user
    if user.mfa_enabled:
        raise HTTPException(409, "La doble verificación ya está activa.")
    secret = mfa.new_secret()
    user.mfa_secret = encrypt(secret)
    uri = mfa.otpauth_uri(secret, user.email)
    return {"secret": secret, "uri": uri, "qr": mfa.qr_data_uri(uri)}


@router.post("/profile/mfa/enable")
def mfa_enable(
    body: CodeIn, request: Request, principal: CurrentPrincipal, db: Db
) -> dict[str, Any]:
    from orkesta.campaigns.gmail import decrypt
    from orkesta.platform import mfa

    user = principal.user
    if not user.mfa_secret or user.mfa_enabled:
        raise HTTPException(409, "Primero genera el código QR.")
    step = mfa.verify(decrypt(user.mfa_secret), body.code)
    if step is None:
        raise HTTPException(400, "El código no es correcto. Revisa la hora de tu celular.")
    codes = mfa.recovery_codes()
    user.mfa_enabled, user.mfa_last_step = True, step
    user.mfa_recovery = [mfa.hash_code(c) for c in codes]
    _audit(db, principal, request, "profile.mfa_on")
    return {"ok": True, "recovery_codes": codes}


@router.post("/profile/mfa/disable")
def mfa_disable(
    body: CodeIn, request: Request, principal: CurrentPrincipal, db: Db
) -> dict[str, Any]:
    from orkesta.platform.service import check_mfa

    user = principal.user
    if user.mfa_enabled and not check_mfa(user, body.code):
        raise HTTPException(400, "El código no es correcto.")
    user.mfa_enabled, user.mfa_secret, user.mfa_recovery, user.mfa_last_step = False, None, [], None
    _audit(db, principal, request, "profile.mfa_off")
    return profile_dict(user)


@router.post("/profile/mfa/recovery")
def mfa_recovery(body: CodeIn, principal: CurrentPrincipal) -> dict[str, Any]:
    from orkesta.platform import mfa
    from orkesta.platform.service import check_mfa

    user = principal.user
    if not user.mfa_enabled or not check_mfa(user, body.code):
        raise HTTPException(400, "El código no es correcto.")
    codes = mfa.recovery_codes()
    user.mfa_recovery = [mfa.hash_code(c) for c in codes]
    return {"recovery_codes": codes}


# --- Resumen diario --------------------------------------------------------------------------


class DigestIn(BaseModel):
    enabled: bool


@router.put("/profile/digest")
def set_digest(body: DigestIn, principal: CurrentPrincipal) -> dict[str, Any]:
    principal.user.digest_enabled = body.enabled
    return profile_dict(principal.user)


@router.post("/profile/digest/test")
def test_digest(principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    """Envía ahora el resumen del día a tu correo, desde tu buzón conectado."""
    from orkesta.notifications import digest

    if principal.tenant is None:
        raise HTTPException(409, "Primero elige un espacio de trabajo.")
    try:
        sent_to = digest.send(db, principal.tenant.id, principal.user)
    except digest.DigestError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "to": sent_to}


class PasswordIn(BaseModel):
    current: str = Field(default="", max_length=200)
    new: str = Field(max_length=200)


@router.post("/profile/password")
def change_password(
    body: PasswordIn, request: Request, principal: CurrentPrincipal, db: Db
) -> dict[str, bool]:
    user = principal.user
    if user.password_hash is not None and not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "La contraseña actual no es correcta.")
    try:
        validate_password(body.new)
    except PasswordPolicyError as exc:
        raise HTTPException(422, str(exc)) from exc
    user.password_hash = hash_password(body.new)
    _audit(db, principal, request, "profile.password")
    return {"ok": True}


@router.get("/avatars/{user_id}")
def avatar(user_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> Response:
    """Foto de perfil: visible para quien comparte un espacio de trabajo con esa persona."""
    if user_id != principal.user.id:
        mine = select(Membership.tenant_id).where(Membership.user_id == principal.user.id)
        shared = db.scalar(
            select(Membership.id)
            .where(Membership.user_id == user_id, Membership.tenant_id.in_(mine))
            .limit(1)
        )
        if shared is None and not principal.user.platform_role:
            raise HTTPException(404, "Sin foto.")
    row = db.execute(select(User.avatar, User.avatar_type).where(User.id == user_id)).first()
    if row is None or not row[0] or not row[1]:
        raise HTTPException(404, "Sin foto.")
    return Response(row[0], media_type=row[1],
                    headers={"Cache-Control": "private, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff"})  # fmt: skip
