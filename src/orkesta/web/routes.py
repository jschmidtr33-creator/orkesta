"""Páginas de la interfaz: acceso, lotes, mapeo y vista previa, detalle, exportación y borrado."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.audit import audit
from orkesta.auth import LoginThrottle, authenticate
from orkesta.config import get_settings
from orkesta.db.models import EnrichmentBatch, ImportRow, User
from orkesta.imports.export import to_csv, to_xlsx
from orkesta.imports.mapping import FIELDS, propose_mapping
from orkesta.imports.reader import ImportFileError, ReadLimits
from orkesta.imports.rows import ISSUES
from orkesta.imports.service import (
    ExportFilter,
    ImportStateError,
    build_preview,
    coerce_mapping,
    confirm_import,
    create_draft_batch,
    delete_batch,
    export_rows,
)
from orkesta.web.deps import DbSession, check_csrf, current_user, flash, render

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User, Depends(current_user)]
CsrfField = Annotated[str | None, Form(alias="csrf_token")]
PAGE_SIZE = 50
ISSUE_MESSAGES = {code: message for code, (_, message) in ISSUES.items()}


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _safe_next(path: str | None) -> str:
    # Solo rutas internas: evita redirecciones abiertas a otros dominios.
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return "/lotes"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def _get_batch(db: Session, user: User, batch_id: uuid.UUID) -> EnrichmentBatch:
    batch = db.get(EnrichmentBatch, batch_id)
    if batch is None or batch.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Lote no encontrado.")
    return batch


# --- acceso --------------------------------------------------------------------------------


@router.get("/login")
def login_page(request: Request, next: str | None = None) -> Response:
    if request.session.get("user_id"):
        return _redirect(_safe_next(next))
    return render(request, "login.html", {"next": _safe_next(next), "email": ""})


@router.post("/login")
def login(
    request: Request,
    db: DbSession,
    csrf: CsrfField = None,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/lotes",
) -> Response:
    check_csrf(request, csrf)
    throttle: LoginThrottle = request.app.state.login_throttle
    ip = _client_ip(request)
    context = {"next": _safe_next(next), "email": email}

    if throttle.is_locked(email, ip):
        minutes = get_settings().login_lockout_minutes
        context["error"] = f"Demasiados intentos. Espera {minutes} minutos e inténtalo de nuevo."
        return render(request, "login.html", context, status=429)

    user = authenticate(db, email, password)
    if user is None:
        throttle.record_failure(email, ip)
        context["error"] = "Correo o contraseña incorrectos."
        return render(request, "login.html", context, status=401)

    throttle.reset(email, ip)
    # Sesión nueva tras autenticar: evita fijación de sesión.
    request.session.clear()
    request.session["user_id"] = str(user.id)
    audit(db, organization_id=user.organization_id, actor_user_id=user.id, action="user.login")
    return _redirect(_safe_next(next))


@router.post("/logout")
def logout(request: Request, csrf: CsrfField = None) -> Response:
    check_csrf(request, csrf)
    request.session.clear()
    return _redirect("/login")


# --- lotes ---------------------------------------------------------------------------------


@router.get("/")
def home() -> Response:
    return _redirect("/lotes")


@router.get("/lotes")
def batches(request: Request, db: DbSession, user: CurrentUser) -> Response:
    rows = db.scalars(
        select(EnrichmentBatch)
        .where(EnrichmentBatch.organization_id == user.organization_id)
        .order_by(EnrichmentBatch.created_at.desc())
        .limit(200)
    ).all()
    return render(request, "batches.html", {"user": user, "batches": rows})


@router.get("/lotes/nuevo")
def upload_page(request: Request, user: CurrentUser) -> Response:
    settings = get_settings()
    return render(
        request,
        "upload.html",
        {"user": user, "max_mb": settings.max_upload_mb, "max_rows": settings.max_import_rows},
    )


@router.post("/lotes")
def upload(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    archivo: Annotated[UploadFile, File()],
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    settings = get_settings()
    limits = ReadLimits(
        max_bytes=settings.max_upload_mb * 1024 * 1024, max_rows=settings.max_import_rows
    )
    # Se lee como máximo un byte más del límite: basta para saber si lo supera.
    content = archivo.file.read(limits.max_bytes + 1)
    filename = archivo.filename or "archivo.csv"
    try:
        result = create_draft_batch(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            filename=filename,
            content=content,
            limits=limits,
        )
    except ImportFileError as exc:
        db.rollback()
        context = {
            "user": user,
            "error": str(exc),
            "max_mb": settings.max_upload_mb,
            "max_rows": settings.max_import_rows,
        }
        return render(request, "upload.html", context, status=400)
    if result.previous_batch is not None:
        when = result.previous_batch.created_at.astimezone().strftime("%d/%m/%Y %H:%M")
        flash(request, f"Este mismo archivo ya se subió el {when}.", "warning")
    return _redirect(f"/lotes/{result.batch.id}/mapeo")


@router.get("/lotes/{batch_id}/mapeo")
def mapping_page(
    request: Request, batch_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    batch = _get_batch(db, user, batch_id)
    if batch.status != "draft":
        return _redirect(f"/lotes/{batch.id}")
    submitted: dict[str, str | None] = {f: request.query_params.get(f) for f in FIELDS}
    if any(v is not None for v in submitted.values()):
        mapping = coerce_mapping(submitted, batch.headers)
    else:
        mapping = coerce_mapping(batch.column_mapping, batch.headers)
    preview = build_preview(db, batch, mapping)
    proposed = propose_mapping(list(batch.headers))
    return render(
        request,
        "mapping.html",
        {
            "user": user,
            "batch": batch,
            "mapping": mapping,
            "confidence": proposed.confidence,
            "preview": preview,
        },
    )


@router.post("/lotes/{batch_id}/confirmar")
def confirm(
    request: Request,
    batch_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    csrf: CsrfField = None,
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    full_name: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
) -> Response:
    check_csrf(request, csrf)
    batch = _get_batch(db, user, batch_id)
    values = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "title": title,
        "company": company,
        "location": location,
    }
    mapping = coerce_mapping(values, batch.headers)
    try:
        confirm_import(
            db,
            batch,
            mapping,
            user_id=user.id,
            max_attempts=get_settings().job_default_max_attempts,
        )
    except ImportStateError as exc:
        flash(request, str(exc), "error")
        query = "&".join(f"{key}={quote(value or '')}" for key, value in mapping.items())
        return _redirect(f"/lotes/{batch.id}/mapeo?{query}")
    flash(request, "Importación en marcha. La página se actualiza sola.", "info")
    return _redirect(f"/lotes/{batch.id}")


@router.get("/lotes/{batch_id}")
def batch_detail(
    request: Request,
    batch_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    estado: Annotated[Literal["todas", "valid", "invalid", "duplicate"], Query()] = "todas",
    pagina: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    batch = _get_batch(db, user, batch_id)
    if batch.status == "draft":
        return _redirect(f"/lotes/{batch.id}/mapeo")
    query = select(ImportRow).where(ImportRow.batch_id == batch.id)
    count_query = select(func.count()).select_from(ImportRow).where(ImportRow.batch_id == batch.id)
    if estado != "todas":
        query = query.where(ImportRow.status == estado)
        count_query = count_query.where(ImportRow.status == estado)
    total = int(db.scalar(count_query) or 0)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(pagina, pages)
    rows = db.scalars(
        query.order_by(ImportRow.row_number).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    return render(
        request,
        "batch.html",
        {
            "user": user,
            "batch": batch,
            "rows": rows,
            "estado": estado,
            "page": page,
            "pages": pages,
            "total_filtered": total,
            "refresh": batch.status == "importing",
        },
    )


@router.get("/lotes/{batch_id}/exportar")
def export(
    batch_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    formato: Annotated[Literal["csv", "xlsx"], Query()] = "xlsx",
    filas: Annotated[Literal["all", "valid", "issues"], Query()] = "all",
) -> Response:
    batch = _get_batch(db, user, batch_id)
    if batch.status not in ("imported", "completed"):
        raise HTTPException(status_code=409, detail="El lote todavía no está importado.")
    which: ExportFilter = filas
    rows = export_rows(db, batch, which)
    if formato == "csv":
        content, media = to_csv(batch.headers, rows, ISSUE_MESSAGES), "text/csv; charset=utf-8"
    else:
        content = to_xlsx(batch.headers, rows, ISSUE_MESSAGES)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    audit(
        db,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action="batch.exported",
        entity_type="enrichment_batch",
        entity_id=batch.id,
        file_format=formato,
        rows=filas,
    )
    stem = batch.source_filename.rsplit(".", 1)[0][:80] or "lote"
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    name = f"ORKESTA_{stem}_{filas}_{stamp}.{formato}"
    ascii_name = name.encode("ascii", "ignore").decode() or f"ORKESTA.{formato}"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(content, media_type=media, headers={"Content-Disposition": disposition})


@router.get("/lotes/{batch_id}/eliminar")
def delete_page(
    request: Request, batch_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    batch = _get_batch(db, user, batch_id)
    return render(request, "delete.html", {"user": user, "batch": batch})


@router.post("/lotes/{batch_id}/eliminar")
def delete(
    request: Request,
    batch_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    batch = _get_batch(db, user, batch_id)
    if batch.status == "importing":
        flash(request, "No se puede eliminar un lote mientras se importa.", "error")
        return _redirect(f"/lotes/{batch.id}")
    name = batch.source_filename
    removed = delete_batch(db, batch, user_id=user.id)
    flash(request, f"Lote «{name}» eliminado ({removed} contactos borrados).", "info")
    return _redirect("/lotes")
