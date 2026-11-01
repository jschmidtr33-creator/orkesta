"""API de reportes: /api/reports/overview."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from orkesta.core.web import Db, Principal, require_roles
from orkesta.reports import service as svc

router = APIRouter(prefix="/api/reports")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]


@router.get("/overview")
def overview(
    principal: Member, db: Db, desde: date | None = None, hasta: date | None = None,
    vendedor: uuid.UUID | None = None, tz: str = "America/Bogota",
) -> dict[str, Any]:  # fmt: skip
    # Un vendedor solo ve sus propias cifras; supervisores y administradores, todo el equipo.
    user_id = principal.user.id if principal.role == "vendedor" else vendedor
    data = svc.overview(db, principal.tenant_id, svc.period(desde, hasta, tz), user_id)
    data["scope"] = {"user_id": str(user_id) if user_id else None,
                     "can_filter": principal.role != "vendedor"}  # fmt: skip
    return data
