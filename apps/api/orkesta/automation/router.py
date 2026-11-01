"""API de automatización: /api/automations."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from orkesta.automation import engine
from orkesta.automation.models import Rule, Run
from orkesta.campaigns.models import Campaign
from orkesta.core.web import Db, Principal, client_ip, require_roles
from orkesta.crm import service as crm
from orkesta.data.models import STATUS_LABELS, Contact, ContactList
from orkesta.platform.service import audit

router = APIRouter(prefix="/api/automations")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
Manager = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]


def _fail(exc: engine.AutomationError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class RuleIn(BaseModel):
    name: str = Field(default="", max_length=160)
    trigger: str = Field(max_length=40)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    enabled: bool = True


class RulePatch(BaseModel):
    enabled: bool | None = None


def _rule(r: Rule) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "enabled": r.enabled,
        "trigger": r.trigger,
        "conditions": r.conditions,
        "actions": r.actions,
        "run_count": r.run_count,
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
        "created_at": r.created_at.isoformat(),
    }


def _get(db: Db, principal: Principal, rule_id: uuid.UUID) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None or rule.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Esa regla no existe.")
    return rule


@router.get("")
def rules(principal: Member, db: Db) -> dict[str, Any]:
    tid = principal.tenant_id
    rows = db.scalars(select(Rule).where(Rule.tenant_id == tid).order_by(Rule.created_at)).all()
    stages = crm.ensure_stages(db, tid)
    campaigns = db.scalars(
        select(Campaign).where(Campaign.tenant_id == tid).order_by(Campaign.created_at.desc())
    ).all()
    lists = db.scalars(
        select(ContactList).where(ContactList.tenant_id == tid).order_by(ContactList.name)
    ).all()
    installed = {r.name for r in rows}
    return {
        "items": [_rule(r) for r in rows],
        "triggers": engine.TRIGGERS,
        "actions": engine.ACTIONS,
        "recipes": [
            {
                "key": r["key"],
                "name": r["name"],
                "trigger": r["trigger"],
                "installed": r["name"] in installed,
            }
            for r in engine.RECIPES
        ],
        "options": {
            "stages": [{"value": str(s.id), "label": s.name} for s in stages],
            "campaigns": [
                {"value": str(c.id), "label": c.name, "status": c.status} for c in campaigns
            ],
            "lists": [{"value": str(lst.id), "label": lst.name} for lst in lists],
            "statuses": [{"value": k, "label": v} for k, v in STATUS_LABELS.items()],
            "sources": [{"value": k, "label": v} for k, v in engine.SOURCES.items()],
        },
        "can_edit": principal.role in ("admin", "supervisor"),
    }


@router.post("", status_code=201)
def create(body: RuleIn, request: Request, principal: Manager, db: Db) -> dict[str, Any]:
    try:
        data = engine.clean_rule(db, principal.tenant_id, body.model_dump())
    except engine.AutomationError as exc:
        raise _fail(exc) from exc
    rule = Rule(
        tenant_id=principal.tenant_id, created_by=principal.user.id, enabled=body.enabled, **data
    )
    db.add(rule)
    db.flush()
    audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user.id,
        action="automation.created",
        entity_type="automation",
        entity_id=rule.id,
        ip=client_ip(request),
        name=rule.name,
    )
    return _rule(rule)


@router.put("/{rule_id}")
def replace(rule_id: uuid.UUID, body: RuleIn, principal: Manager, db: Db) -> dict[str, Any]:
    rule = _get(db, principal, rule_id)
    try:
        data = engine.clean_rule(db, principal.tenant_id, body.model_dump())
    except engine.AutomationError as exc:
        raise _fail(exc) from exc
    for key, value in data.items():
        setattr(rule, key, value)
    rule.enabled = body.enabled
    return _rule(rule)


@router.patch("/{rule_id}")
def toggle(rule_id: uuid.UUID, body: RulePatch, principal: Manager, db: Db) -> dict[str, Any]:
    rule = _get(db, principal, rule_id)
    if body.enabled is not None:
        rule.enabled = body.enabled
    return _rule(rule)


@router.delete("/{rule_id}")
def delete(rule_id: uuid.UUID, principal: Manager, db: Db) -> dict[str, bool]:
    db.delete(_get(db, principal, rule_id))
    return {"ok": True}


@router.post("/recipes/{key}", status_code=201)
def recipe(key: str, request: Request, principal: Manager, db: Db) -> dict[str, Any]:
    try:
        rule = engine.install_recipe(db, principal.tenant_id, principal.user.id, key)
    except engine.AutomationError as exc:
        raise _fail(exc) from exc
    audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user.id,
        action="automation.created",
        entity_type="automation",
        entity_id=rule.id,
        ip=client_ip(request),
        name=rule.name,
    )
    return _rule(rule)


@router.get("/runs")
def runs(principal: Member, db: Db, rule_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    query = (
        select(Run, Rule.name, Contact.full_name)
        .join(Rule, Rule.id == Run.rule_id)
        .outerjoin(Contact, Contact.id == Run.contact_id)
        .where(Run.tenant_id == principal.tenant_id)
        .order_by(Run.created_at.desc())
        .limit(100)
    )
    if rule_id:
        query = query.where(Run.rule_id == rule_id)
    return [
        {
            "id": str(r.id),
            "rule": name,
            "trigger": r.trigger,
            "status": r.status,
            "contact": {"id": str(r.contact_id), "full_name": full} if r.contact_id else None,
            "results": r.results,
            "created_at": r.created_at.isoformat(),
        }
        for r, name, full in db.execute(query).tuples()
    ]
