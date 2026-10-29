"""Listas de prospección en la interfaz: crear, agregar desde el buscador, revisar y exportar."""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from orkesta.audit import audit
from orkesta.catalog.industries import INDUSTRY_LABELS
from orkesta.catalog.lists import (
    ListError,
    add_to_list,
    create_list,
    get_list,
    list_counts,
    members_page,
    refresh_emails,
    remove_members,
)
from orkesta.catalog.service import iter_search
from orkesta.db.models import Prospect, ProspectList, ProspectListMember, User
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.imports.export import sanitize_cell
from orkesta.web.deps import DbSession, check_csrf, current_user, flash, render
from orkesta.web.prospects import _filters, _query_string

router = APIRouter(include_in_schema=False)
CurrentUser = Annotated[User, Depends(current_user)]
CsrfField = Annotated[str | None, Form(alias="csrf_token")]
PAGE_SIZE = 50
SOURCE_LABELS = {
    "known": "conocido",
    "real_pattern": "patrón real de la empresa",
    "country_prior": "patrón más probable del país",
}
EXPORT_COLUMNS = (
    "Nombre completo", "Cargo", "Empresa", "Correo", "Confianza correo", "Origen correo",
    "Correo alternativo 1", "Correo alternativo 2", "Teléfono", "Ciudad", "País", "Industria",
)  # fmt: skip


def _list_or_404(db: DbSession, user: User, list_id: uuid.UUID) -> ProspectList:
    try:
        return get_list(db, user.organization_id, list_id)
    except ListError as exc:
        raise HTTPException(status_code=404, detail="Lista no encontrada") from exc


@router.get("/listas")
def lists_page(request: Request, db: DbSession, user: CurrentUser) -> Response:
    lists = db.scalars(
        select(ProspectList)
        .where(ProspectList.organization_id == user.organization_id)
        .order_by(ProspectList.created_at.desc())
    ).all()
    return render(
        request,
        "lists.html",
        {"user": user, "lists": lists, "counts": list_counts(db, user.organization_id)},
    )


@router.post("/listas")
def new_list(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    nombre: Annotated[str, Form()] = "",
    descripcion: Annotated[str, Form()] = "",
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    try:
        item = create_list(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            name=nombre,
            description=descripcion,
        )
    except ListError as exc:
        db.rollback()
        flash(request, str(exc), "warning")
        return RedirectResponse("/listas", status_code=303)
    flash(request, f"Lista «{item.name}» creada. Agrega personas desde Prospectos.", "success")
    return RedirectResponse(f"/listas/{item.id}", status_code=303)


@router.post("/prospectos/a-lista")
def add_from_search(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    lista: Annotated[str, Form()] = "",
    nueva_lista: Annotated[str, Form()] = "",
    alcance: Annotated[Literal["seleccionados", "todos"], Form()] = "seleccionados",
    ids: Annotated[list[str] | None, Form()] = None,
    excluir_otras: Annotated[str, Form()] = "",
    cargo: Annotated[str, Form()] = "",
    empresa: Annotated[str, Form()] = "",
    industria: Annotated[list[str] | None, Form()] = None,
    pais: Annotated[list[str] | None, Form()] = None,
    ciudad: Annotated[str, Form()] = "",
    con_correo: Annotated[str, Form()] = "",
    nivel: Annotated[list[str] | None, Form()] = None,
    area: Annotated[list[str] | None, Form()] = None,
    web: Annotated[str, Form()] = "",
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    filters = _filters(cargo, empresa, industria, pais, ciudad, con_correo, nivel, area, web)
    back = f"/prospectos?{_query_string(filters)}"
    try:
        if nueva_lista.strip():
            target = create_list(
                db, organization_id=user.organization_id, user_id=user.id, name=nueva_lista
            )
        elif lista:
            target = get_list(db, user.organization_id, uuid.UUID(lista))
        else:
            raise ListError("Elige una lista o escribe el nombre de una nueva.")
    except (ListError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc) if isinstance(exc, ListError) else "Lista no válida.", "warning")
        return RedirectResponse(back, status_code=303)
    if alcance == "todos":
        prospect_ids = [p.id for p in iter_search(db, user.organization_id, filters, limit=5000)]
    else:
        prospect_ids = []
        for raw in ids or []:
            try:
                prospect_ids.append(uuid.UUID(raw))
            except ValueError:
                continue
    if not prospect_ids:
        flash(request, "No hay personas seleccionadas.", "warning")
        return RedirectResponse(back, status_code=303)
    result = add_to_list(
        db,
        organization_id=user.organization_id,
        list_id=target.id,
        prospect_ids=prospect_ids,
        exclude_in_other_lists=excluir_otras == "1",
    )
    audit(
        db,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action="list.members_added",
        entity_type="prospect_list",
        entity_id=target.id,
        **{"agregadas": result["agregadas"]},
    )
    parts = [f"{result['agregadas']} agregadas a «{target.name}»"]
    if result["ya_estaban"]:
        parts.append(f"{result['ya_estaban']} ya estaban")
    if result["en_otras_listas"]:
        parts.append(f"{result['en_otras_listas']} excluidas por estar en otras listas")
    if result["sin_correo"]:
        parts.append(f"{result['sin_correo']} sin correo (empresa sin dominio conocido)")
    flash(request, "; ".join(parts) + ".", "success")
    return RedirectResponse(back, status_code=303)


@router.get("/listas/{list_id}")
def list_detail(
    request: Request,
    list_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    pagina: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    item = _list_or_404(db, user, list_id)
    rows, total = members_page(db, item.id, page=pagina, page_size=PAGE_SIZE)
    counts = list_counts(db, user.organization_id).get(item.id, (0, 0))
    return render(
        request,
        "list_detail.html",
        {
            "user": user,
            "item": item,
            "rows": rows,
            "total": total,
            "with_email": counts[1],
            "page": pagina,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "sources": SOURCE_LABELS,
            "industries": INDUSTRY_LABELS,
        },
    )


@router.post("/listas/{list_id}/quitar")
def remove(
    request: Request,
    list_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    ids: Annotated[list[str] | None, Form()] = None,
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    item = _list_or_404(db, user, list_id)
    member_ids = []
    for raw in ids or []:
        try:
            member_ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    removed = remove_members(db, user.organization_id, item.id, member_ids)
    flash(request, f"{removed} personas quitadas de la lista.", "success")
    return RedirectResponse(f"/listas/{item.id}", status_code=303)


@router.post("/listas/{list_id}/actualizar-correos")
def refresh(
    request: Request, list_id: uuid.UUID, db: DbSession, user: CurrentUser, csrf: CsrfField = None
) -> Response:
    check_csrf(request, csrf)
    item = _list_or_404(db, user, list_id)
    count = refresh_emails(db, user.organization_id, item.id)
    flash(request, f"Correos recalculados para {count} personas.", "success")
    return RedirectResponse(f"/listas/{item.id}", status_code=303)


@router.post("/listas/{list_id}/eliminar")
def delete_list(
    request: Request, list_id: uuid.UUID, db: DbSession, user: CurrentUser, csrf: CsrfField = None
) -> Response:
    check_csrf(request, csrf)
    item = _list_or_404(db, user, list_id)
    name = item.name
    audit(
        db,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action="list.deleted",
        entity_type="prospect_list",
        entity_id=item.id,
    )
    db.delete(item)
    flash(request, f"Lista «{name}» eliminada. Las personas siguen en tu base.", "success")
    return RedirectResponse("/listas", status_code=303)


def _export_rows(db: DbSession, list_id: uuid.UUID) -> list[list[str]]:
    rows = db.execute(
        select(ProspectListMember, Prospect)
        .join(Prospect, Prospect.id == ProspectListMember.prospect_id)
        .where(ProspectListMember.list_id == list_id)
        .order_by(Prospect.company, Prospect.full_name)
    )
    out = []
    for m, p in rows:
        out.append(
            [
                p.full_name,
                p.title or "",
                p.company,
                m.email or "",
                str(m.email_confidence) if m.email_confidence is not None else "",
                SOURCE_LABELS.get(m.email_source or "", ""),
                m.alt_email_1 or "",
                m.alt_email_2 or "",
                p.phone or "",
                p.city or "",
                COUNTRIES[p.country_code][0] if p.country_code in COUNTRIES else "",
                INDUSTRY_LABELS.get(p.industry_code or "", p.industry or ""),
            ]
        )
    return out


@router.get("/listas/{list_id}/exportar")
def export_list(
    list_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    formato: Annotated[Literal["csv", "xlsx"], Query()] = "xlsx",
) -> Response:
    item = _list_or_404(db, user, list_id)
    rows = _export_rows(db, item.id)
    if formato == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow([sanitize_cell(v) for v in row])
        content = buffer.getvalue().encode("utf-8-sig")
        media = "text/csv; charset=utf-8"
    else:
        from openpyxl import Workbook

        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Lista")
        sheet.append(list(EXPORT_COLUMNS))
        for row in rows:
            sheet.append([sanitize_cell(v) for v in row])
        stream = io.BytesIO()
        workbook.save(stream)
        content = stream.getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    audit(
        db,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action="list.exported",
        entity_type="prospect_list",
        entity_id=item.id,
        file_format=formato,
        rows=len(rows),
    )
    name = f"ORKESTA_{item.name[:60]}_{datetime.now(UTC):%Y%m%d}.{formato}"
    ascii_name = name.encode("ascii", "ignore").decode() or f"ORKESTA.{formato}"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(content, media_type=media, headers={"Content-Disposition": disposition})
