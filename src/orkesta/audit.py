"""Registro de auditoría: accesos, importaciones, exportaciones y borrados."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from orkesta.db.models import AuditLog


def audit(
    session: Session,
    *,
    organization_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    **detail: Any,
) -> None:
    """Añade una entrada a ``audit_logs``. Nunca incluir datos de contacto en ``detail``."""
    session.add(
        AuditLog(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
