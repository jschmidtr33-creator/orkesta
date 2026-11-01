"""Corridas de investigación: crear, consultar, cancelar y guardar resultados en Contactos."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.automation.events import emit_many
from orkesta.core.db import set_context
from orkesta.core.jobs import enqueue
from orkesta.data.models import Contact, DataPoint
from orkesta.data.service import (
    DataError,
    _upsert_companies,
    add_to_list,
    create_list,
    dedup_key,
    get_list,
    now,
    search_text,
)
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.engine.taxonomy import linkedin as li
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS, SIZE_BANDS, classify
from orkesta.engine.taxonomy.titles import AREA_LABELS, classify_title
from orkesta.research import serp
from orkesta.research.agent import RESEARCH_JOB, company_key
from orkesta.research.models import ResearchItem, ResearchRun
from orkesta.research.wikidata import COUNTRY_QIDS

MAX_ACTIVE_RUNS = 2
# Sin tope visible: cada búsqueda trae todos los resultados que encuentre (techo técnico).
MAX_RESULTS = 1000
LEVEL_CHOICES = ("c_level", "duenio_socio", "vp", "director", "gerente", "jefe")
UNSAVED_TTL = timedelta(hours=12)


def options() -> dict[str, Any]:
    return {
        "countries": sorted(
            ({"value": k, "label": COUNTRIES[k][0]} for k in COUNTRY_QIDS if k in COUNTRIES),
            key=lambda c: c["label"],
        ),
        "sector_groups": [
            {"label": group, "options": [{"value": c, "label": li.SECTORS[c][0]} for c in codes]}
            for group, codes in li.SECTOR_GROUPS
        ],
        "functions": [{"value": k, "label": v[0]} for k, v in li.FUNCTIONS.items()],
        "seniority": [{"value": k, "label": v[0]} for k, v in li.SENIORITY.items()],
        "default_seniority": list(li.DEFAULT_SENIORITY),
        "headcount": [{"value": k, "label": v} for k, v in li.HEADCOUNT],
        "web_search": serp.enabled(),
    }


def _names(values: list[str], table: dict[str, Any], n: int = 2) -> str:
    shown = [table[v][0] if v in table else v for v in values[:n]]
    more = len(values) - n
    return ", ".join(shown) + (f" y {more} más" if more > 0 else "")


def _summary(filters: dict[str, Any]) -> str:
    country = COUNTRIES.get(filters["country"], (filters["country"],))[0]
    parts = [", ".join(filters["cities"][:2]) or country]
    if filters["companies"]:
        parts.insert(0, _names(filters["companies"], {}, 3))
    if filters["titles"]:
        parts.append(_names(filters["titles"], {}))
    if filters["seniority"]:
        parts.append(_names(filters["seniority"], li.SENIORITY))
    if filters["functions"]:
        parts.append(_names(filters["functions"], li.FUNCTIONS))
    if filters["sectors"]:
        parts.append(_names(filters["sectors"], li.SECTORS))
    elif filters["industries"]:
        parts.append(", ".join(INDUSTRY_LABELS.get(i, i) for i in filters["industries"][:2]))
    if filters["first_name"] or filters["last_name"]:
        parts.append(f"{filters['first_name']} {filters['last_name']}".strip())
    if filters["keywords"]:
        parts.append(f"«{filters['keywords']}»")
    return " · ".join(parts)[:300]


def _text(value: Any, size: int = 80) -> str:
    return " ".join(str(value or "").split())[:size]


def validate_filters(data: dict[str, Any]) -> dict[str, Any]:
    """Filtros de Sales Navigator (Empresa, Rol, Personal, Palabras clave) ya limpios, más sus
    equivalentes internos para el agente (industrias, niveles y áreas)."""
    country = str(data.get("country") or "").upper()
    if country not in COUNTRY_QIDS:
        raise DataError("Elige un país.")

    def pick(name: str, allowed: Any) -> list[str]:
        values = data.get(name) or []
        if not isinstance(values, list):
            raise DataError("Filtros no válidos.")
        return list(dict.fromkeys(str(v) for v in values if str(v) in allowed))[:30]

    def texts(name: str, top: int, size: int = 120) -> list[str]:
        values = data.get(name) or []
        if not isinstance(values, list):
            raise DataError("Filtros no válidos.")
        return list(dict.fromkeys(_text(v, size) for v in values if _text(v, size)))[:top]

    sectors = pick("sectors", li.SECTORS)
    exclude_sectors = [s for s in pick("exclude_sectors", li.SECTORS) if s not in sectors]
    functions = pick("functions", li.FUNCTIONS)
    exclude_functions = [f for f in pick("exclude_functions", li.FUNCTIONS) if f not in functions]
    seniority = pick("seniority", li.SENIORITY)
    legacy_levels = pick("levels", LEVEL_CHOICES)
    legacy_areas = pick("areas", AREA_LABELS)
    cities = texts("cities", 5, 80) or ([_text(data.get("city"))] if data.get("city") else [])
    return {
        "country": country,
        "cities": cities,
        "city": cities[0] if len(cities) == 1 else "",
        "sectors": sectors,
        "exclude_sectors": exclude_sectors,
        "industries": li.sector_parents(sectors) or pick("industries", INDUSTRY_LABELS),
        "sizes": pick("sizes", {band for band, _, _ in SIZE_BANDS}),
        "seniority": seniority,
        "levels": li.seniority_levels(seniority) if seniority else legacy_levels,
        "functions": functions,
        "exclude_functions": exclude_functions,
        "areas": li.function_areas(functions) if functions else legacy_areas,
        "titles": texts("titles", 10, 80),
        "exclude_titles": texts("exclude_titles", 10, 80),
        "companies": texts("companies", 25),
        "exclude_companies": texts("exclude_companies", 25),
        "first_name": _text(data.get("first_name"), 60),
        "last_name": _text(data.get("last_name"), 60),
        "keywords": _text(data.get("keywords")),
        "include_base": bool(data.get("include_base", True)),
        "limit": MAX_RESULTS,
    }


def purge_unsaved(
    session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> int:
    """El historial solo guarda las búsquedas que el usuario guarda: las demás se borran al
    empezar otra búsqueda o pasadas unas horas."""
    query = delete(ResearchRun).where(
        ResearchRun.tenant_id == tenant_id,
        ResearchRun.saved_at.is_(None),
        ResearchRun.status.not_in(("queued", "running")),
    )
    if user_id is not None:
        query = query.where(ResearchRun.created_by == user_id)
    else:
        query = query.where(ResearchRun.created_at < now() - UNSAVED_TTL)
    result = session.execute(query)
    return int(getattr(result, "rowcount", 0) or 0)


def purge_all_unsaved(factory: sessionmaker[Session]) -> int:
    from orkesta.platform.models import Tenant

    with factory() as session:
        tenants = list(session.scalars(select(Tenant.id)))
    total = 0
    for tenant_id in tenants:
        with factory.begin() as session:
            set_context(session, tenant_id=tenant_id)
            total += purge_unsaved(session, tenant_id)
    return total


def save_search(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, name: str
) -> ResearchRun:
    run = get_run(session, tenant_id, run_id)
    clean = _text(name, 120)
    if len(clean) < 2:
        raise DataError("Ponle un nombre a la búsqueda.")
    run.saved_name, run.saved_at = clean, now()
    return run


def forget_search(session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    run = get_run(session, tenant_id, run_id)
    if run.status in ("queued", "running"):
        raise DataError("Detén la búsqueda antes de borrarla.", 409)
    session.delete(run)


def create_run(
    session: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, filters: dict[str, Any]
) -> ResearchRun:
    clean = validate_filters(filters)
    active = session.scalar(
        select(func.count())
        .select_from(ResearchRun)
        .where(ResearchRun.tenant_id == tenant_id, ResearchRun.status.in_(("queued", "running")))
    )
    purge_unsaved(session, tenant_id, user_id=user_id)
    if (active or 0) >= MAX_ACTIVE_RUNS:
        raise DataError("Ya hay 2 investigaciones en curso. Espera a que termine una.", 429)
    run = ResearchRun(
        tenant_id=tenant_id,
        created_by=user_id,
        filters=clean,
        summary=_summary(clean),
        progress={"step": "en_cola", "done": 0, "total": 0, "log": []},
    )
    session.add(run)
    session.flush()
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=RESEARCH_JOB,
        payload={"run_id": str(run.id)},
        max_attempts=1,
    )
    return run


def get_run(session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> ResearchRun:
    run = session.scalar(
        select(ResearchRun).where(ResearchRun.tenant_id == tenant_id, ResearchRun.id == run_id)
    )
    if run is None:
        raise DataError("Esa investigación no existe.", 404)
    return run


def run_dict(run: ResearchRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "filters": run.filters,
        "summary": run.summary,
        "saved_name": run.saved_name,
        "saved_at": run.saved_at.isoformat() if run.saved_at else None,
        "progress": run.progress,
        "totals": run.totals,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def saved_runs(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50
) -> list[ResearchRun]:
    """Búsquedas guardadas por el usuario (el historial no guarda las demás)."""
    return list(
        session.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.tenant_id == tenant_id,
                ResearchRun.created_by == user_id,
                ResearchRun.saved_at.is_not(None),
            )
            .order_by(ResearchRun.saved_at.desc())
            .limit(limit)
        )
    )


def items(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, kind: str
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ResearchItem)
        .where(
            ResearchItem.tenant_id == tenant_id,
            ResearchItem.run_id == run_id,
            ResearchItem.kind == kind,
        )
        .order_by(ResearchItem.score.desc(), ResearchItem.created_at)
        .limit(2000)
    ).all()
    return [
        {
            "id": str(r.id),
            "key": r.key,
            "company_key": r.company_key,
            "data": {k: v for k, v in r.data.items() if k != "provenance"},
            "provenance": r.data.get("provenance", {}),
            "sources": r.sources,
            "score": r.score,
            "saved_contact_id": str(r.saved_contact_id) if r.saved_contact_id else None,
        }
        for r in rows
    ]


# --- Guardar en Contactos -------------------------------------------------------------------

FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "job_title": "title",
    "company": "company",
    "email": "email",
    "phone": "phone",
    "city": "city",
    "country_code": "country",
    "industry": "industry",
}


def _points(
    contact: Contact, data: dict[str, Any], item: ResearchItem, user_id: uuid.UUID
) -> list[DataPoint]:
    provenance: dict[str, Any] = item.data.get("provenance", {})
    company_source = next(iter(item.sources), {"label": "Investigación ORKESTA", "url": ""})
    points = []
    for field, key in FIELD_MAP.items():
        value = data.get(key)
        if not value:
            continue
        if key in ("first_name", "last_name"):
            origin = provenance.get("full_name") or company_source
        elif key == "phone":
            origin = next((s for s in item.sources if s.get("type") == "sitio"), company_source)
        else:
            origin = provenance.get(key) or company_source
        label = origin.get("label", "Investigación ORKESTA")
        url = origin.get("url") or ""
        if url and url not in label:
            short = url.split("://", 1)[-1].removeprefix("www.").rstrip("/")[:120]
            label = label.replace(short, url) if short in label else f"{label} · {url}"
        points.append(
            DataPoint(
                tenant_id=contact.tenant_id,
                contact_id=contact.id,
                field=field,
                value=str(value)[:1000],
                source_type="investigacion",
                source_label=label[:300],
                source_ref=item.run_id,
                confidence=int(origin.get("confidence", 70)),
                actor_user_id=user_id,
            )
        )
    return points


def save_people(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    list_id: uuid.UUID | None = None,
    new_list: str = "",
) -> dict[str, Any]:
    """Pasa decisores encontrados a Contactos (sin duplicar) y, si se pide, a una lista."""
    get_run(session, tenant_id, run_id)
    target = None
    if new_list.strip():
        target = create_list(session, tenant_id=tenant_id, user_id=user_id, name=new_list)
    elif list_id:
        target = get_list(session, tenant_id, list_id)
    rows = session.scalars(
        select(ResearchItem).where(
            ResearchItem.tenant_id == tenant_id,
            ResearchItem.run_id == run_id,
            ResearchItem.kind == "person",
            ResearchItem.id.in_(item_ids[:1000]),
        )
    ).all()
    from orkesta.admin.limits import contact_capacity

    room = contact_capacity(session, tenant_id)
    created = merged = over_limit = 0
    contact_ids: list[uuid.UUID] = []
    new_contacts: list[Contact] = []
    for item in rows:
        data = item.data
        ckey = data.get("company_key") or company_key(data.get("company", ""))
        company_ids = _upsert_companies(
            session,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "name": data.get("company") or ckey,
                    "match_key": ckey,
                    "domain": data.get("domain") or None,
                    "industry": data.get("industry") or None,
                    "industry_code": classify(data.get("industry") or "") or None,
                    "city": data.get("city") or None,
                    "country_code": data.get("country") or None,
                }
            ],
        )
        email = (data.get("email") or "").lower() or None
        full_name = data.get("full_name") or ""
        keys = {dedup_key(None, full_name, ckey)}
        if email:
            keys.add(dedup_key(email, full_name, ckey))
        contact = session.scalar(
            select(Contact).where(Contact.tenant_id == tenant_id, Contact.dedup_key.in_(keys))
        )
        values = {
            "first_name": data.get("first_name") or "",
            "last_name": data.get("last_name") or "",
            "full_name": full_name[:300],
            "job_title": (data.get("title") or "")[:300] or None,
            "company_name": (data.get("company") or "")[:300] or None,
            "email": email,
            "email_domain": email.split("@", 1)[1] if email else None,
            "phone": (data.get("phone") or "")[:40] or None,
            "city": (data.get("city") or "")[:120] or None,
            "country_code": data.get("country") or None,
            "industry": (data.get("industry") or "")[:200] or None,
            "industry_code": classify(data.get("industry") or "") or None,
        }
        estimated = bool(email) and data.get("email_status") == "estimado"
        if contact is None and room is not None and created >= room:
            over_limit += 1
            continue
        if contact is None:
            contact = Contact(
                tenant_id=tenant_id,
                company_id=company_ids.get(ckey),
                owner_user_id=user_id,
                dedup_key=dedup_key(email, full_name, ckey),
                source="investigacion",
                status="nuevo",
                email_status="estimado" if estimated else "sin_validar",
                **values,
            )
            session.add(contact)
            created += 1
            new_contacts.append(contact)
        else:
            had_email = bool(contact.email)
            for key, value in values.items():
                if value and not getattr(contact, key):
                    setattr(contact, key, value)
            if not had_email and contact.email and estimated:
                contact.email_status = "estimado"
            contact.company_id = contact.company_id or company_ids.get(ckey)
            merged += 1
        contact.level_code, contact.area_code = classify_title(contact.job_title)
        contact.search_text = search_text(
            contact.full_name, contact.email, contact.company_name, contact.job_title, contact.city
        )
        session.flush()
        session.add_all(_points(contact, data, item, user_id))
        item.saved_contact_id = contact.id
        contact_ids.append(contact.id)
    emit_many(session, tenant_id, "contact.created", [c.id for c in new_contacts],
              source="investigacion", user_id=user_id)  # fmt: skip
    added = 0
    if target is not None and contact_ids:
        added = add_to_list(
            session, tenant_id=tenant_id, list_id=target.id, contact_ids=contact_ids
        )
    session.flush()
    return {
        "created": created,
        "merged": merged,
        "added_to_list": added,
        "over_limit": over_limit,
        "list": {"id": str(target.id), "name": target.name} if target else None,
        "at": now().isoformat(),
    }


def remove_items(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, item_ids: list[uuid.UUID]
) -> int:
    from sqlalchemy import delete

    get_run(session, tenant_id, run_id)
    result = session.execute(
        delete(ResearchItem).where(
            ResearchItem.tenant_id == tenant_id,
            ResearchItem.run_id == run_id,
            ResearchItem.id.in_(item_ids[:1000]),
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
