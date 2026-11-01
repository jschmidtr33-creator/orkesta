"""API del asistente de redacción: opciones, redactar secuencias, revisar correos y buyer
personas guardados."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from orkesta.campaigns.templating import html_to_text
from orkesta.copywriter import analyzer, engine
from orkesta.copywriter.models import BuyerPersona
from orkesta.core.web import Db, Principal, require_roles
from orkesta.proposals.models import Product

router = APIRouter(prefix="/api/copy")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]


class ComposeIn(BaseModel):
    product_id: uuid.UUID | None = None
    product: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=2000)
    benefits: list[str] = Field(default_factory=list, max_length=8)
    proof: str = Field(default="", max_length=300)
    persona_id: uuid.UUID | None = None
    area: str = Field(default="auto", max_length=40)
    persona_label: str = Field(default="", max_length=120)
    pains: list[str] = Field(default_factory=list, max_length=6)
    goals: list[str] = Field(default_factory=list, max_length=6)
    industry: str = Field(default="", max_length=40)
    goal: str = Field(default="reunion", max_length=20)
    tone: str = Field(default="cercano", max_length=20)
    framework: str = Field(default="pas", max_length=20)
    language: str = Field(default="es", max_length=2)
    steps: int = Field(default=3, ge=1, le=6)
    link: str = Field(default="", max_length=300)
    variant: int = Field(default=0, ge=0, le=10_000)


class AnalyzeIn(BaseModel):
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=100_000)
    step: int = Field(default=0, ge=0, le=10)
    language: str = Field(default="es", max_length=2)


class PersonaIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    area: str = Field(default="auto", max_length=40)
    industry: str = Field(default="", max_length=40)
    pains: list[str] = Field(default_factory=list, max_length=6)
    goals: list[str] = Field(default_factory=list, max_length=6)
    objections: list[str] = Field(default_factory=list, max_length=6)
    notes: str = Field(default="", max_length=1000)


def _lines(values: list[str], size: int = 200) -> list[str]:
    return [" ".join(v.split())[:size] for v in values if v and v.strip()]


def _persona(p: BuyerPersona) -> dict[str, Any]:
    return {"id": str(p.id), "name": p.name, **p.data}


@router.get("/options")
def options(principal: Member, db: Db, language: str = "es") -> dict[str, Any]:
    saved = db.scalars(
        select(BuyerPersona)
        .where(BuyerPersona.tenant_id == principal.tenant_id)
        .order_by(BuyerPersona.name)
    )
    return {**engine.options(language), "saved_personas": [_persona(p) for p in saved]}


@router.post("/compose")
def compose(body: ComposeIn, principal: Member, db: Db) -> dict[str, Any]:
    product, summary = body.product, body.summary
    if body.product_id:
        row = db.scalar(
            select(Product).where(
                Product.tenant_id == principal.tenant_id, Product.id == body.product_id
            )
        )
        if row is None:
            raise HTTPException(404, "Ese producto no existe.")
        product = product or row.name
        summary = summary or html_to_text(row.description)
    area, label, pains, goals, industry = (body.area, body.persona_label, body.pains,
                                           body.goals, body.industry)  # fmt: skip
    if body.persona_id:
        saved = db.scalar(
            select(BuyerPersona).where(
                BuyerPersona.tenant_id == principal.tenant_id, BuyerPersona.id == body.persona_id
            )
        )
        if saved is None:
            raise HTTPException(404, "Ese buyer persona no existe.")
        d = saved.data
        area = d.get("area") or area
        label = label or saved.name
        pains = pains or list(d.get("pains") or [])
        goals = goals or list(d.get("goals") or [])
        industry = industry or d.get("industry") or ""
    if not product.strip():
        raise HTTPException(400, "Elige un producto del catálogo o escribe qué ofreces.")
    company = principal.tenant.name if principal.tenant else ""
    brief = engine.Brief(
        product=product, summary=summary, benefits=_lines(body.benefits), proof=body.proof,
        company=company, area=area, persona_label=label, pains=_lines(pains),
        goals=_lines(goals, 80), industry=industry, goal=body.goal, tone=body.tone,
        framework=body.framework, language=body.language, steps=body.steps, link=body.link,
        variant=body.variant,
    )  # fmt: skip
    result = engine.compose(brief)
    for i, step in enumerate(result["steps"]):
        step["review"] = analyzer.analyze(step["subject"], step["body"], step=i,
                                          language=body.language)  # fmt: skip
    return result


@router.post("/analyze")
def analyze(body: AnalyzeIn, _: Member) -> dict[str, Any]:
    return analyzer.analyze(body.subject, body.body, step=body.step, language=body.language)


@router.post("/personas", status_code=201)
def create_persona(body: PersonaIn, principal: Member, db: Db) -> dict[str, Any]:
    name = " ".join(body.name.split())
    exists = db.scalar(
        select(BuyerPersona.id).where(
            BuyerPersona.tenant_id == principal.tenant_id, BuyerPersona.name == name
        )
    )
    if exists:
        raise HTTPException(409, "Ya tienes un buyer persona con ese nombre.")
    persona = BuyerPersona(tenant_id=principal.tenant_id, name=name, data=_data(body))
    db.add(persona)
    db.flush()
    return _persona(persona)


@router.put("/personas/{persona_id}")
def update_persona(
    persona_id: uuid.UUID, body: PersonaIn, principal: Member, db: Db
) -> dict[str, Any]:
    persona = db.scalar(
        select(BuyerPersona).where(
            BuyerPersona.tenant_id == principal.tenant_id, BuyerPersona.id == persona_id
        )
    )
    if persona is None:
        raise HTTPException(404, "Ese buyer persona no existe.")
    persona.name, persona.data = " ".join(body.name.split()), _data(body)
    return _persona(persona)


@router.delete("/personas/{persona_id}")
def delete_persona(persona_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    persona = db.scalar(
        select(BuyerPersona).where(
            BuyerPersona.tenant_id == principal.tenant_id, BuyerPersona.id == persona_id
        )
    )
    if persona is not None:
        db.delete(persona)
    return {"ok": True}


def _data(body: PersonaIn) -> dict[str, Any]:
    return {
        "area": body.area,
        "industry": body.industry,
        "pains": _lines(body.pains),
        "goals": _lines(body.goals, 80),
        "objections": _lines(body.objections),
        "notes": " ".join(body.notes.split())[:1000],
    }
