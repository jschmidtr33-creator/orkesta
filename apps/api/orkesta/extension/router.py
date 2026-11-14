"""Extensión de Chrome «Guardar en ORKESTA».

- Desde el panel de administración (solo la plataforma): crear, ver y revocar claves y descargar
  la extensión. No es una función de la app para los usuarios.
- Con la clave (``Authorization: Bearer ork_…``): quién soy, guardar un contacto que la persona
  eligió en una página, agregar un perfil por su dirección de LinkedIn, marcar quién de una lista
  en pantalla ya está en Contactos y guardar de una vez (hasta 100) los que la persona eligió.

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


def _owner() -> Any:
    from orkesta.admin.router import require

    return require("configuracion")


# Solo la administración de la plataforma (desde el panel): no es una función de la app.
Owner = Annotated[Principal, Depends(_owner())]
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


@router.get("/admin/extension/tokens")
def list_tokens(principal: Owner, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == principal.user.id, ApiToken.tenant_id == principal.tenant_id,
               ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )  # fmt: skip
    return [_token_dict(t) for t in rows]


class TokenIn(BaseModel):
    name: str = Field(default="Extensión de Chrome", max_length=80)


@router.post("/admin/extension/tokens", status_code=201)
def create_token(body: TokenIn, request: Request, principal: Owner, db: Db) -> dict[str, Any]:
    if principal.tenant_id is None:
        raise HTTPException(409, "Entra a una empresa en la app antes de generar la clave.")
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


@router.delete("/admin/extension/tokens/{token_id}")
def revoke_token(token_id: uuid.UUID, request: Request, principal: Owner, db: Db) -> dict[str, Any]:
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != principal.user.id:
        raise HTTPException(404, "Esa clave no existe.")
    row.revoked_at = datetime.now(UTC)
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.token_revoked", entity_type="api_token", entity_id=row.id,
          ip=client_ip(request))  # fmt: skip
    return {"revoked": True}


@router.get("/admin/extension/download")
def download(principal: Owner) -> Response:
    """La extensión lista para cargar en Chrome (modo desarrollador), en un .zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(FILES.rglob("*")):
            if path.is_file():
                # manifest.json en la raíz: así lo exige la Chrome Web Store.
                z.write(path, str(path.relative_to(FILES)))
    name = f"orkesta-extension-{version()}.zip"
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


# --- Conectar la extensión con la cuenta (cualquier usuario, desde orkestaapp.com) -------------

Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
CONNECT_NAME = "Extensión de Chrome"


@router.post("/extension/connect")
def connect(request: Request, principal: Member, db: Db) -> dict[str, Any]:
    """La persona inició sesión en ORKESTA y conecta la extensión: se crea su clave (la anterior
    de la extensión queda revocada) y la página se la entrega a la extensión instalada."""
    now = datetime.now(UTC)
    for old in db.scalars(select(ApiToken).where(
        ApiToken.user_id == principal.user.id, ApiToken.name == CONNECT_NAME,
        ApiToken.revoked_at.is_(None),
    )):  # fmt: skip
        old.revoked_at = now
    token = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(tenant_id=principal.tenant_id, user_id=principal.user.id, name=CONNECT_NAME,
                   token_hash=_hash(token), prefix=token[:10])  # fmt: skip
    db.add(row)
    db.flush()
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.connected", entity_type="api_token", entity_id=row.id,
          ip=client_ip(request))  # fmt: skip
    tenant = principal.tenant
    return {"token": token, "company": tenant.name if tenant else ""}


# --- Lo que usa la extensión (con la clave) ----------------------------------------------------


@router.post("/ext/logout")
def logout(request: Request, db: Db) -> dict[str, Any]:
    """«Desconectar» en la extensión: la clave deja de servir."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    return {"ok": True}


def version() -> str:
    import json

    try:
        return str(json.loads((FILES / "manifest.json").read_text())["version"])
    except (OSError, ValueError, KeyError):
        return ""


@router.get("/admin/extension/info")
def info(principal: Owner) -> dict[str, Any]:
    return {"version": version()}


@router.get("/ext/me")
def me(principal: Extension) -> dict[str, Any]:
    tenant = principal.tenant
    return {"name": principal.user.full_name or principal.user.email,
            "company": tenant.name if tenant else "", "role": principal.role,
            "version": version()}  # fmt: skip


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
    list_id: uuid.UUID | None = None


def _to_list(db: Db, principal: Principal, list_id: uuid.UUID | None,
             ids: list[uuid.UUID]) -> int:  # fmt: skip
    """Agrega a una lista los contactos guardados (o que ya estaban) desde la extensión."""
    from orkesta.data import service as svc

    if list_id is None or not ids:
        return 0
    try:
        return svc.add_to_list(db, tenant_id=principal.tenant_id, list_id=list_id,
                               contact_ids=ids)  # fmt: skip
    except svc.DataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class ExtNewList(BaseModel):
    name: str = Field(min_length=1, max_length=120)


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
    _remember(db, [ExtPersonIn(first_name=body.first_name, last_name=body.last_name,
                               job_title=body.job_title, company=body.company, email=body.email,
                               location=body.country)])  # fmt: skip
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
    _to_list(db, principal, body.list_id, [contact.id])
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.contact_saved", entity_type="contact", entity_id=contact.id,
          ip=client_ip(request))  # fmt: skip
    return {"id": str(contact.id), "name": contact.full_name, "email": contact.email,
            "enrichment_id": enrichment}  # fmt: skip


class ExtPersonIn(BaseModel):
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    job_title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    location: str = Field(default="", max_length=200)
    linkedin_url: str = Field(default="", max_length=600)


class ExtListIn(BaseModel):
    people: list[ExtPersonIn] = Field(max_length=100)
    source_url: str = Field(default="", max_length=600)
    enrich: bool = True
    list_id: uuid.UUID | None = None


def _person_input(p: ExtPersonIn) -> Any:
    from orkesta.data import service as svc
    from orkesta.engine.normalize import parse_location

    first, last = p.first_name.strip(), p.last_name.strip()
    if first and not last and " " in first:
        first, _, last = first.partition(" ")
    email = p.email.strip()
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        email = ""  # lo que se ve en pantalla a veces es «Ver correo» o un correo cortado
    place = p.location.strip()
    if place and not parse_location(place).country_code:
        place = ""  # «Área metropolitana», «Remoto»…: mejor sin país que con error
    return svc.ContactInput(first_name=first, last_name=last, job_title=p.job_title,
                            company=p.company, email=email, phone=p.phone,
                            country=place)  # fmt: skip


def _profile_url(url: str) -> str:
    from orkesta.data.linkedin import parse

    profiles, _ = parse(url) if "linkedin.com/in/" in url else ([], [])
    return profiles[0].url if profiles else ""


def _remember(db: Db, people: list[ExtPersonIn]) -> int:
    """Suma al índice de personas de ORKESTA (el que alimenta Prospección) los perfiles que la
    extensión leyó en pantalla: nombre, cargo, empresa, ubicación, LinkedIn y correo
    corporativo. Respeta la lista de exclusión; sin país o sin empresa no se guarda."""
    from sqlalchemy.orm import sessionmaker

    from orkesta.engine.net.domains import is_non_corporate
    from orkesta.engine.normalize import parse_location
    from orkesta.engine.normalize.location import COUNTRIES
    from orkesta.engine.normalize.names import split_full_name
    from orkesta.engine.taxonomy.titles import classify_title
    from orkesta.research import people as index
    from orkesta.research.directory import name_key
    from orkesta.research.extract import is_generic_email

    rows = []
    for p in people:
        full = " ".join(f"{p.first_name} {p.last_name}".split())[:200]
        company = " ".join(p.company.split())[:300]
        place = parse_location(p.location) if p.location.strip() else None
        country = place.country_code if place else ""
        if not country and p.location.strip().upper() in COUNTRIES:
            country = p.location.strip().upper()
        if not full or " " not in full or not company or not country:
            continue
        split = split_full_name(full)
        ckey = name_key(company)
        url = _profile_url(p.linkedin_url)
        email = p.email.strip().lower()
        domain = email.split("@", 1)[1] if "@" in email else ""
        if not domain or "." not in domain or is_non_corporate(domain) or is_generic_email(email):
            email = ""
        level, area = classify_title(p.job_title)
        key = index.person_key(country, ckey, full, url)
        rows.append({
            "key": key, "country": country, "full_name": full,
            "first_name": (split.first or full.split()[0])[:120],
            "last_name": (split.last or " ".join(full.split()[1:]))[:160],
            "title": p.job_title.strip()[:300], "level": level, "area": area,
            "company": company, "company_key": ckey, "domain": domain or None,
            "location": p.location.strip()[:200], "linkedin": url[:300] or None,
            "email": email or None, "email_status": "estimado" if email else None,
            "source": "extension",
        })  # fmt: skip
    if not rows:
        return 0
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    _learn_companies(factory, rows)
    return index.upsert(factory, rows)


def _learn_companies(factory: Any, rows: list[dict[str, Any]]) -> None:
    """Las empresas de esos perfiles también pasan al directorio de empresas."""
    from types import SimpleNamespace

    from orkesta.research import directory

    by_country: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_country.setdefault(r["country"], {})[r["company_key"]] = SimpleNamespace(
            name=r["company"], domain=r.get("domain") or "", website="", linkedin="",
            industry_code="", industry="", city="", employees=None,
        )  # fmt: skip
    for country, companies in by_country.items():
        directory.learn(factory, country, companies.values())


@router.get("/ext/lists")
def ext_lists(principal: Extension, db: Db) -> list[dict[str, Any]]:
    """Las listas de la empresa, para elegir dónde guardar desde la extensión."""
    from orkesta.data import service as svc

    return [{"id": str(item["id"]), "name": item["name"]}
            for item in svc.lists_with_counts(db, principal.tenant_id)]  # fmt: skip


@router.post("/ext/lists", status_code=201)
def ext_new_list(body: ExtNewList, principal: Extension, db: Db) -> dict[str, str]:
    from orkesta.data import service as svc

    try:
        item = svc.create_list(db, tenant_id=principal.tenant_id, user_id=principal.user.id,
                               name=body.name)  # fmt: skip
    except svc.DataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"id": str(item.id), "name": item.name}


@router.post("/ext/contacts/check")
def check_people(body: ExtListIn, principal: Extension, db: Db) -> dict[str, Any]:
    """¿Cuáles de las personas de la pantalla ya están en Contactos? (no las agrega a Contactos;
    sí las suma al índice de personas que alimenta Prospección)."""
    from orkesta.core.db import unscoped
    from orkesta.data import service as svc
    from orkesta.data.models import Contact

    keys, urls = [], []
    for p in body.people:
        try:
            values = svc._clean_input(_person_input(p))
        except svc.DataError:
            values = {"email": None, "full_name": "", "company_key": ""}
        keys.append(svc.dedup_key(values["email"], values["full_name"], values["company_key"])
                    if values["full_name"] else "-")  # fmt: skip
        urls.append(_profile_url(p.linkedin_url))
    with unscoped(db):
        found_keys = set(db.scalars(select(Contact.dedup_key).where(
            Contact.tenant_id == principal.tenant_id, Contact.dedup_key.in_(keys))))  # fmt: skip
        wanted = [u for u in urls if u]
        found_urls = set(db.scalars(select(Contact.linkedin_url).where(
            Contact.tenant_id == principal.tenant_id, Contact.linkedin_url.in_(wanted)))
        ) if wanted else set()  # fmt: skip
    _remember(db, body.people)
    return {
        "exists": [
            k in found_keys or bool(u and u in found_urls) for k, u in zip(keys, urls, strict=True)
        ]
    }


@router.post("/ext/contacts/bulk")
def save_people(body: ExtListIn, request: Request, principal: Extension,
                db: Db) -> dict[str, Any]:  # fmt: skip
    """Guarda las personas que la persona eligió de la lista en pantalla (máximo 100) y las que
    no tienen correo pasan juntas a un solo enriquecimiento."""
    from orkesta.data import service as svc
    from orkesta.data.models import Contact, DataPoint

    _remember(db, body.people)
    created: list[uuid.UUID] = []
    already: list[uuid.UUID] = []
    existing = 0
    errors: list[str] = []
    for p in body.people:
        try:
            contact = svc.create_contact(db, tenant_id=principal.tenant_id, user=principal.user,
                                         data=_person_input(p))  # fmt: skip
        except svc.DataError as exc:
            if exc.status == 409:
                existing += 1
                if body.list_id is not None:
                    values = svc._clean_input(_person_input(p))
                    key = svc.dedup_key(values["email"], values["full_name"], values["company_key"])
                    found = db.scalar(select(Contact.id).where(
                        Contact.tenant_id == principal.tenant_id,
                        Contact.dedup_key == key))  # fmt: skip
                    if found is not None:
                        already.append(found)
            else:
                errors.append(f"{p.first_name} {p.last_name}: {exc}"[:160])
                if exc.status in (402, 403):
                    break  # límite del plan: no tiene sentido seguir
            continue
        if url := _profile_url(p.linkedin_url):
            contact.linkedin_url = url[:300]
        if body.source_url:
            db.add(DataPoint(tenant_id=contact.tenant_id, contact_id=contact.id, field="fuente",
                             value=body.source_url[:1000], source_type="manual",
                             source_label="Guardado con la extensión de Chrome (lista)",
                             confidence=100, actor_user_id=principal.user.id))  # fmt: skip
        created.append(contact.id)
    db.flush()
    pending = [c for c in created if (x := db.get(Contact, c)) is not None
               and not x.email and x.company_name]  # fmt: skip
    enrichment = _start_enrichment(db, principal, pending) if body.enrich else None
    in_list = _to_list(db, principal, body.list_id, [*created, *already])
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.list_saved", entity_type="contact", entity_id=None,
          ip=client_ip(request))  # fmt: skip
    return {"created": len(created), "existing": existing, "errors": errors[:10],
            "in_list": in_list,
            "enrichment_id": enrichment, "pending": len(pending)}  # fmt: skip


class ExtLinkedInIn(BaseModel):
    url: str = Field(max_length=600)
    company: str = Field(default="", max_length=200)
    country: str = Field(default="", max_length=2)
    list_id: uuid.UUID | None = None
    # Lo que la persona tiene en pantalla del perfil (lo lee la extensión al hacer clic).
    name: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=200)


@router.post("/ext/linkedin")
def save_linkedin(body: ExtLinkedInIn, request: Request, principal: Extension,
                  db: Db) -> dict[str, Any]:  # fmt: skip
    """Agrega el perfil abierto: lo que la persona ve en pantalla (nombre, cargo, empresa y
    ubicación) y, si falta algo, el índice propio y el resultado público de Google."""
    if body.name:
        _remember(db, [ExtPersonIn(first_name=body.name, job_title=body.title,
                                   company=body.company, location=body.location or body.country,
                                   linkedin_url=body.url)])  # fmt: skip
    from orkesta.data import linkedin
    from orkesta.data import service as svc

    try:
        result = linkedin.add(
            db, tenant_id=principal.tenant_id, user=principal.user, text=body.url,
            company=body.company, country=body.country,
            seen={"name": body.name, "title": body.title, "company": body.company,
                  "location": body.location},
        )  # fmt: skip
    except svc.DataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    pending = result.pop("to_enrich")
    result["enrichment_id"] = _start_enrichment(db, principal, pending)
    if body.list_id is not None:
        from orkesta.data.linkedin import parse
        from orkesta.data.models import Contact

        urls = [p.url for p in parse(body.url)[0]]
        ids = list(db.scalars(select(Contact.id).where(
            Contact.tenant_id == principal.tenant_id, Contact.linkedin_url.in_(urls))))  # fmt: skip
        result["in_list"] = _to_list(db, principal, body.list_id, ids)
    audit(db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
          action="extension.linkedin_saved", entity_type="contact", entity_id=None,
          ip=client_ip(request))  # fmt: skip
    return result
