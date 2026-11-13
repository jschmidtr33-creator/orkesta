"""Extensión de Chrome «Guardar en ORKESTA».

- Con la sesión de la app: crear, ver y revocar claves personales, y descargar la extensión.
- Con la clave (``Authorization: Bearer ork_…``): quién soy, guardar un contacto que la persona
  eligió en una página, y agregar un perfil por su dirección de LinkedIn (sin leer LinkedIn).

La clave solo se guarda como huella; solo sirve para crear y consultar contactos de su dueño, y
cada uso queda en la bitácora.
"""

from __future__ import annotations

import hashlib
import io
import secrets
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from orkesta.core.web import (
    Db,
    Principal,
    RateLimiter,
    client_ip,
    principal_for,
    require_roles,
)
from orkesta.platform.models import ApiToken, User
from orkesta.platform.service import audit

router = APIRouter(prefix="/api")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
limiter = RateLimiter(limit=600, window_seconds=3600)
FILES = Path(__file__).resolve().parent / "chrome"
PREFIX = "ork_"
MAX_TOKENS = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_principal(request: Request, db: Db) -> Principal:
    """Quién usa la extensión, por su clave personal."""
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip()
    if not token.startswith(PREFIX):
        raise HTTPException(401, "Conecta la extensión con tu clave de ORKESTA.")
    if not limiter.allow(f"ext:{_hash(token)[:16]}"):
        raise HTTPException(429, "Demasiadas solicitudes. Espera unos minutos.")
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == _hash(token),
                                           ApiToken.revoked_at.is_(None)))  # fmt: skip
    if row is None:
        raise HTTPException(401, "Esa clave no existe o fue revocada. Genera una nueva en ORKESTA.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Tu usuario ya no está activo.")
    row.last_used_at = datetime.now(UTC)
    principal = principal_for(db, user, row.tenant_id)
    return require_roles("admin", "supervisor", "vendedor")(principal, db)


Extension = Annotated[Principal, Depends(token_principal)]


# --- Claves (desde la app) --------------------------------------------------------------------


def _token_dict(t: ApiToken) -> dict[str, Any]:
    return {"id": str(t.id), "name": t.name, "prefix": t.prefix,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None}  # fmt: skip


@router.get("/extension/tokens")
def list_tokens(principal: Member, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == principal.user.id, ApiToken.tenant_id == principal.tenant_id,
               ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )  # fmt: skip
    return [_token_dict(t) for t in rows]


class TokenIn(BaseModel):
    name: str = Field(default="Extensión de Chrome", max_length=80)


@router.post("/extension/tokens", status_code=201)
def create_token(body: TokenIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    active = db.scalar(select(func.count()).where(
        ApiToken.user_id == principal.user.id, ApiToken.revoked_at.is_(None)))  # fmt: skip
    if (active or 0) >= MAX_TOKENS:
        raise HTTPException(409, f"Ya tienes {MAX_TOKENS} claves activas. Revoca una que no uses.")
    token = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(tenant_id=principal.tenant_id, user_id=principal.user.id,
                   name=" ".join(body.name.split())[:80] or "Extensión de Chrome",
                   token_hash=_hash(token), prefix=token[:10])  # fmt: skip
    db.add(row)
    db.flush()
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.token_created", entity_type="api_token", entity_id=row.id,
          ip=client_ip(request))  # fmt: skip
    return {**_token_dict(row), "token": token}


@router.delete("/extension/tokens/{token_id}")
def revoke_token(
    token_id: uuid.UUID, request: Request, principal: Member, db: Db
) -> dict[str, Any]:
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != principal.user.id:
        raise HTTPException(404, "Esa clave no existe.")
    row.revoked_at = datetime.now(UTC)
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.token_revoked", entity_type="api_token", entity_id=row.id,
          ip=client_ip(request))  # fmt: skip
    return {"revoked": True}


@router.get("/extension/download")
def download(principal: Member) -> Response:
    """La extensión lista para cargar en Chrome (modo desarrollador), en un .zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(FILES.rglob("*")):
            if path.is_file():
                z.write(path, f"orkesta-extension/{path.relative_to(FILES)}")
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="orkesta-extension.zip"'})  # fmt: skip


# --- Lo que usa la extensión (con la clave) ----------------------------------------------------


@router.get("/ext/me")
def me(principal: Extension) -> dict[str, Any]:
    tenant = principal.tenant
    return {"name": principal.user.full_name or principal.user.email,
            "company": tenant.name if tenant else "", "role": principal.role}  # fmt: skip


class ExtContactIn(BaseModel):
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    job_title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=60)
    website: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=600)
    enrich: bool = True


def _start_enrichment(db: Db, principal: Principal, ids: list[uuid.UUID]) -> str | None:
    from orkesta.core.jobs import enqueue
    from orkesta.enrichment.agent import ENRICH_JOB
    from orkesta.enrichment.models import EnrichmentRun

    busy = db.scalar(select(func.count()).where(
        EnrichmentRun.tenant_id == principal.tenant_id,
        EnrichmentRun.status.in_(("queued", "running"))))  # fmt: skip
    if busy or not ids:
        return None
    run = EnrichmentRun(tenant_id=principal.tenant_id, created_by=principal.user.id,
                        scope={"profiles": False, "ids": [str(i) for i in ids]},
                        summary=f"{len(ids)} contacto(s) desde la extensión",
                        progress={"done": 0, "total": 0, "log": []})  # fmt: skip
    db.add(run)
    db.flush()
    enqueue(db, tenant_id=principal.tenant_id, kind=ENRICH_JOB, payload={"run_id": str(run.id)},
            max_attempts=1)  # fmt: skip
    return str(run.id)


@router.post("/ext/contacts", status_code=201)
def save_contact(body: ExtContactIn, request: Request, principal: Extension,
                 db: Db) -> dict[str, Any]:  # fmt: skip
    """Guarda el contacto que la persona eligió en una página y, si falta el correo, lo
    enriquece (sitio web, formato de correo y verificación)."""
    from orkesta.data import service as svc
    from orkesta.data.models import Company, DataPoint
    from orkesta.engine.net.domains import registrable_domain

    first, last = body.first_name.strip(), body.last_name.strip()
    if first and not last and " " in first:
        first, _, last = first.partition(" ")
    data = svc.ContactInput(first_name=first, last_name=last, job_title=body.job_title,
                            company=body.company, email=body.email, phone=body.phone,
                            country=body.country)  # fmt: skip
    try:
        contact = svc.create_contact(db, tenant_id=principal.tenant_id, user=principal.user,
                                     data=data)  # fmt: skip
    except svc.DataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    host = body.website.strip().lower().removeprefix("https://").removeprefix("http://")
    domain = registrable_domain(host.split("/")[0].removeprefix("www.")) if host else ""
    if domain and contact.company_id:
        company = db.get(Company, contact.company_id)
        if company is not None and not company.domain:
            company.domain = domain[:253]
    if body.source_url:
        db.add(DataPoint(tenant_id=contact.tenant_id, contact_id=contact.id, field="fuente",
                         value=body.source_url[:1000], source_type="manual",
                         source_label="Guardado con la extensión de Chrome", confidence=100,
                         actor_user_id=principal.user.id))  # fmt: skip
    db.flush()
    enrichment = None
    if body.enrich and not contact.email and contact.company_name:
        enrichment = _start_enrichment(db, principal, [contact.id])
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.contact_saved", entity_type="contact", entity_id=contact.id,
          ip=client_ip(request))  # fmt: skip
    return {"id": str(contact.id), "name": contact.full_name, "email": contact.email,
            "enrichment_id": enrichment}  # fmt: skip


class ExtLinkedInIn(BaseModel):
    url: str = Field(max_length=600)
    company: str = Field(default="", max_length=200)
    country: str = Field(default="", max_length=2)


@router.post("/ext/linkedin")
def save_linkedin(body: ExtLinkedInIn, request: Request, principal: Extension,
                  db: Db) -> dict[str, Any]:  # fmt: skip
    """Agrega el perfil abierto usando solo su dirección (no se lee la página de LinkedIn)."""
    from orkesta.data import linkedin
    from orkesta.data import service as svc

    try:
        result = linkedin.add(
            db, tenant_id=principal.tenant_id, user=principal.user, text=body.url,
            company=body.company, country=body.country,
        )  # fmt: skip
    except svc.DataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    pending = result.pop("to_enrich")
    result["enrichment_id"] = _start_enrichment(db, principal, pending)
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.linkedin_saved", entity_type="contact", entity_id=None,
          ip=client_ip(request))  # fmt: skip
    return result
