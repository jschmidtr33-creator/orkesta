"""Base de prospectos en la interfaz: cargar bases, buscar con filtros y exportar."""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from orkesta.audit import audit
from orkesta.catalog.industries import INDUSTRY_LABELS
from orkesta.catalog.prospects import MAX_BYTES
from orkesta.catalog.service import ProspectFilters, create_upload, iter_search, search_prospects
from orkesta.catalog.titles import AREA_LABELS, LEVEL_LABELS
from orkesta.db.models import DomainPattern, Prospect, ProspectUpload, User
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.imports.export import sanitize_cell
from orkesta.web.deps import DbSession, check_csrf, current_user, flash, render

router = APIRouter(include_in_schema=False)
CurrentUser = Annotated[User, Depends(current_user)]
CsrfField = Annotated[str | None, Form(alias="csrf_token")]
PAGE_SIZE = 50
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
) -> ProspectFilters:
    return ProspectFilters(
        levels=[n for n in (nivel or []) if n in LEVEL_LABELS],
        areas=[a for a in (area or []) if a in AREA_LABELS],
        title=cargo.strip()[:200],
        company=empresa.strip()[:200],
        industries=[i for i in (industria or []) if i in INDUSTRY_LABELS],
        countries=[p for p in (pais or []) if p in COUNTRIES],
        city=ciudad.strip()[:120],
        with_email=con_correo == "1",
    )


def _query_string(f: ProspectFilters) -> str:
    pairs: list[tuple[str, str]] = [("cargo", f.title), ("empresa", f.company), ("ciudad", f.city)]
    pairs += [("industria", i) for i in f.industries] + [("pais", p) for p in f.countries]
    pairs += [("nivel", n) for n in f.levels] + [("area", a) for a in f.areas]
    if f.with_email:
        pairs.append(("con_correo", "1"))
    return urlencode([(k, v) for k, v in pairs if v])


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
    pagina: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    filters = _filters(cargo, empresa, industria, pais, ciudad, con_correo, nivel, area)
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
            "filters": filters,
            "query": _query_string(filters),
            "page": min(pagina, max(1, math.ceil(total / PAGE_SIZE))),
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "industries": INDUSTRY_LABELS,
            "levels": LEVEL_LABELS,
            "areas": AREA_LABELS,
            "countries": COUNTRY_CHOICES,
            "max_mb": MAX_BYTES // (1024 * 1024),
            "refresh": any(u.status in ("queued", "running") for u in uploads),
        },
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
) -> Response:
    filters = _filters(cargo, empresa, industria, pais, ciudad, con_correo, nivel, area)
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
