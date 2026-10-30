"""Base de prospectos en la interfaz: cargar bases, buscar con filtros y exportar."""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select

from orkesta.audit import audit
from orkesta.catalog.discovery import (
    AUTO_COMPANIES,
    DiscoveryError,
    ensure_discovery,
    start_discovery,
    wants_web_search,
)
from orkesta.catalog.industries import INDUSTRY_LABELS, SIZE_BANDS
from orkesta.catalog.prospects import MAX_BYTES
from orkesta.catalog.service import ProspectFilters, create_upload, iter_search, search_prospects
from orkesta.catalog.titles import AREA_LABELS, DECISION_LEVELS, LEVEL_LABELS
from orkesta.db.models import (
    DiscoveryRun,
    DomainPattern,
    Prospect,
    ProspectList,
    ProspectUpload,
    User,
)
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.imports.export import sanitize_cell
from orkesta.web.deps import DbSession, check_csrf, current_user, flash, render

router = APIRouter(include_in_schema=False)
CurrentUser = Annotated[User, Depends(current_user)]
CsrfField = Annotated[str | None, Form(alias="csrf_token")]
PAGE_SIZE = 50
SIZE_LABELS = [band for band, _, _ in SIZE_BANDS]
COUNTRY_CHOICES = sorted(
    ((code, name) for code, (name, _) in COUNTRIES.items()), key=lambda c: c[1]
)
EXPORT_COLUMNS = (
    ("full_name", "Nombre completo"),
    ("title", "Cargo"),
    ("company", "Empresa"),
    ("email", "Correo"),
    ("phone", "Teléfono"),
    ("city", "Ciudad"),
    ("country_code", "País"),
    ("industry_label", "Industria"),
)


def _filters(
    cargo: str = "",
    empresa: str = "",
    industria: list[str] | None = None,
    pais: list[str] | None = None,
    ciudad: str = "",
    con_correo: str = "",
    nivel: list[str] | None = None,
    area: list[str] | None = None,
    web: str = "",
    tamano: list[str] | None = None,
) -> ProspectFilters:
    return ProspectFilters(
        sizes=[t for t in (tamano or []) if t in SIZE_LABELS],
        only_web=web == "1",
        levels=[n for n in (nivel or []) if n in LEVEL_LABELS],
        areas=[a for a in (area or []) if a in AREA_LABELS],
        title=cargo.strip()[:200],
        company=empresa.strip()[:200],
        industries=[i for i in (industria or []) if i in INDUSTRY_LABELS],
        countries=[p for p in (pais or []) if p in COUNTRIES],
        city=ciudad.strip()[:120],
        with_email=con_correo == "1",
    )


def _query_pairs(f: ProspectFilters) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = [("cargo", f.title), ("empresa", f.company), ("ciudad", f.city)]
    pairs += [("industria", i) for i in f.industries] + [("pais", p) for p in f.countries]
    pairs += [("nivel", n) for n in f.levels] + [("area", a) for a in f.areas]
    pairs += [("tamano", t) for t in f.sizes]
    if f.with_email:
        pairs.append(("con_correo", "1"))
    if f.only_web:
        pairs.append(("web", "1"))
    return [("buscar", "1")] + [(k, v) for k, v in pairs if v]


def _query_string(f: ProspectFilters) -> str:
    return urlencode(_query_pairs(f))


@router.get("/prospectos")
def prospects_page(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    cargo: str = "",
    empresa: str = "",
    industria: Annotated[list[str] | None, Query()] = None,
    pais: Annotated[list[str] | None, Query()] = None,
    ciudad: str = "",
    con_correo: str = "",
    nivel: Annotated[list[str] | None, Query()] = None,
    area: Annotated[list[str] | None, Query()] = None,
    web: str = "",
    tamano: Annotated[list[str] | None, Query()] = None,
    pagina: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    # Primera visita (sin parámetros): solo decisores.
    if not request.query_params:
        nivel = list(DECISION_LEVELS)
    filters = _filters(
        cargo, empresa, industria, pais, ciudad, con_correo, nivel, area, web, tamano
    )
    # Con filtros de empresa o de cargo, la búsqueda web arranca sola (o se reutiliza una
    # reciente con los mismos filtros) y sus resultados llegan en vivo.
    live_run = None
    web_exhausted = False
    if wants_web_search(filters) and pagina == 1:
        live_run = ensure_discovery(
            db, organization_id=user.organization_id, user_id=user.id, filters=filters
        )
        web_exhausted = live_run is None
    rows, total = search_prospects(
        db, user.organization_id, filters, page=pagina, page_size=PAGE_SIZE
    )
    base_total = int(
        db.scalar(
            select(func.count())
            .select_from(Prospect)
            .where(Prospect.organization_id == user.organization_id)
        )
        or 0
    )
    patterns = int(
        db.scalar(
            select(func.count())
            .select_from(DomainPattern)
            .where(DomainPattern.organization_id == user.organization_id)
        )
        or 0
    )
    lists = db.scalars(
        select(ProspectList)
        .where(ProspectList.organization_id == user.organization_id)
        .order_by(ProspectList.name)
    ).all()
    uploads = db.scalars(
        select(ProspectUpload)
        .where(ProspectUpload.organization_id == user.organization_id)
        .order_by(ProspectUpload.created_at.desc())
        .limit(8)
    ).all()
    return render(
        request,
        "prospects.html",
        {
            "user": user,
            "rows": rows,
            "total": total,
            "base_total": base_total,
            "patterns": patterns,
            "uploads": uploads,
            "live_run": live_run,
            "web_exhausted": web_exhausted,
            "searching": wants_web_search(filters) or bool(filters.city or filters.areas),
            "lists": lists,
            "sizes": SIZE_LABELS,
            "decision_levels": DECISION_LEVELS,
            "filters": filters,
            "query": _query_string(filters),
            "query_pairs": _query_pairs(filters),
            "page": min(pagina, max(1, math.ceil(total / PAGE_SIZE))),
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "industries": INDUSTRY_LABELS,
            "levels": LEVEL_LABELS,
            "areas": AREA_LABELS,
            "countries": COUNTRY_CHOICES,
            "max_mb": MAX_BYTES // (1024 * 1024),
            # Solo mientras se procesa una base; la búsqueda web se actualiza con live.js.
            "refresh": any(u.status in ("queued", "running") for u in uploads),
        },
    )


@router.get("/prospectos/busqueda/{run_id}")
def discovery_status(run_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    run = db.get(DiscoveryRun, run_id)
    if run is None or run.organization_id != user.organization_id:
        return JSONResponse({"error": "no encontrada"}, status_code=404)
    return JSONResponse(
        {
            "done": run.done,
            "total": run.total,
            "people_found": run.people_found,
            "running": run.status in ("queued", "running"),
        }
    )


@router.post("/prospectos/cargar")
def upload_base(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    archivo: Annotated[UploadFile, File()],
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    content = archivo.file.read(MAX_BYTES + 1)
    filename = archivo.filename or "base.xlsx"
    if not content:
        flash(request, "El archivo está vacío.", "error")
    elif len(content) > MAX_BYTES:
        flash(request, f"El archivo supera el máximo de {MAX_BYTES // (1024 * 1024)} MB.", "error")
    elif not filename.lower().endswith((".csv", ".xlsx", ".txt")):
        flash(request, "Sube un archivo .xlsx o .csv.", "error")
    else:
        create_upload(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            filename=filename,
            content=content,
        )
        flash(request, f"Cargando «{filename}». La base se actualiza en unos segundos.", "success")
    return RedirectResponse("/prospectos", status_code=303)


@router.get("/prospectos/exportar")
def export_prospects(
    db: DbSession,
    user: CurrentUser,
    cargo: str = "",
    empresa: str = "",
    industria: Annotated[list[str] | None, Query()] = None,
    pais: Annotated[list[str] | None, Query()] = None,
    ciudad: str = "",
    con_correo: str = "",
    nivel: Annotated[list[str] | None, Query()] = None,
    area: Annotated[list[str] | None, Query()] = None,
    web: str = "",
) -> Response:
    filters = _filters(cargo, empresa, industria, pais, ciudad, con_correo, nivel, area, web)
    rows = iter_search(db, user.organization_id, filters)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for p in rows:
        values = {
            "full_name": p.full_name,
            "title": p.title,
            "company": p.company,
            "email": p.email,
            "phone": p.phone,
            "city": p.city,
            "country_code": COUNTRIES[p.country_code][0] if p.country_code in COUNTRIES else "",
            "industry_label": INDUSTRY_LABELS.get(p.industry_code or "", p.industry or ""),
        }
        writer.writerow([sanitize_cell(values[key]) for key, _ in EXPORT_COLUMNS])
    audit(
        db,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action="prospects.exported",
        entity_type="prospect",
        rows=len(rows),
    )
    name = f"ORKESTA_prospectos_{datetime.now(UTC):%Y%m%d}.csv"
    disposition = f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"
    return Response(
        buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.post("/prospectos/descubrir")
def discover(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    cargo: Annotated[str, Form()] = "",
    empresa: Annotated[str, Form()] = "",
    industria: Annotated[list[str] | None, Form()] = None,
    pais: Annotated[list[str] | None, Form()] = None,
    ciudad: Annotated[str, Form()] = "",
    con_correo: Annotated[str, Form()] = "",
    nivel: Annotated[list[str] | None, Form()] = None,
    area: Annotated[list[str] | None, Form()] = None,
    web: Annotated[str, Form()] = "",
    tamano: Annotated[list[str] | None, Form()] = None,
    csrf: CsrfField = None,
) -> Response:
    """Siguiente lote de empresas por explorar con los mismos filtros."""
    check_csrf(request, csrf)
    filters = _filters(
        cargo, empresa, industria, pais, ciudad, con_correo, nivel, area, web, tamano
    )
    try:
        run = start_discovery(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            filters=filters,
            limit=AUTO_COMPANIES,
        )
    except DiscoveryError as exc:
        db.rollback()
        flash(request, str(exc), "warning")
    else:
        audit(
            db,
            organization_id=user.organization_id,
            actor_user_id=user.id,
            action="prospects.discovery_started",
            entity_type="discovery_run",
            entity_id=run.id,
            companies=run.total,
        )
        flash(request, f"Buscando decisores en los sitios de {run.total} empresas más.", "success")
    return RedirectResponse(f"/prospectos?{_query_string(filters)}", status_code=303)
