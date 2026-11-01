"""Emisión de eventos: los módulos avisan «pasó X» y aquí se encolan las reglas que aplican.

Este archivo solo depende de la cola y del modelo de reglas para que cualquier módulo pueda
importarlo sin dependencias circulares. Las acciones corren después, en ``engine``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.automation.models import Rule
from orkesta.core.jobs import enqueue

MAX_DEPTH = 3  # una regla puede disparar otra, pero no en cadena infinita
MAX_BATCH = 2000


def _matches(rule: Rule, ctx: dict[str, Any]) -> bool:
    cond = rule.conditions or {}
    for key in ("campaign_id", "stage_id", "source"):
        if cond.get(key) and str(cond[key]) != str(ctx.get(key) or ""):
            return False
    if rule.trigger == "campaign.opened":
        return int(ctx.get("open_count") or 0) == max(1, int(cond.get("min_opens") or 1))
    return True


def _clean(ctx: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ctx.items() if v is not None}


def emit(
    session: Session, tenant_id: uuid.UUID, trigger: str, *, depth: int = 0, **ctx: Any
) -> int:
    """Encola una ejecución por cada regla activa que coincide. Devuelve cuántas."""
    if depth >= MAX_DEPTH:
        return 0
    rules = session.scalars(
        select(Rule).where(Rule.tenant_id == tenant_id, Rule.trigger == trigger, Rule.enabled)
    ).all()
    queued = 0
    for rule in rules:
        if _matches(rule, ctx):
            job = enqueue(
                session,
                tenant_id=tenant_id,
                kind="automation.run",
                max_attempts=2,
                payload={
                    "rule_id": str(rule.id),
                    "trigger": trigger,
                    "depth": depth,
                    "ctx": _clean(ctx),
                },
            )
            if trigger == "campaign.finished":
                # Se espera unos días: si responde antes, la regla no corre.
                days = max(0, min(30, int((rule.conditions or {}).get("wait_days") or 3)))
                job.run_after = datetime.now(UTC) + timedelta(days=days)
            queued += 1
    return queued


def emit_many(
    session: Session, tenant_id: uuid.UUID, trigger: str, contact_ids: list[uuid.UUID], **ctx: Any
) -> int:
    """Lo mismo para muchos contactos a la vez (importaciones y prospección)."""
    if not contact_ids:
        return 0
    rules = session.scalars(
        select(Rule).where(Rule.tenant_id == tenant_id, Rule.trigger == trigger, Rule.enabled)
    ).all()
    queued = 0
    for rule in rules:
        if not _matches(rule, ctx):
            continue
        for start in range(0, len(contact_ids), MAX_BATCH):
            enqueue(
                session,
                tenant_id=tenant_id,
                kind="automation.run",
                max_attempts=2,
                payload={
                    "rule_id": str(rule.id),
                    "trigger": trigger,
                    "depth": 0,
                    "ctx": _clean(ctx),
                    "contact_ids": [str(c) for c in contact_ids[start : start + MAX_BATCH]],
                },
            )
            queued += 1
    return queued
