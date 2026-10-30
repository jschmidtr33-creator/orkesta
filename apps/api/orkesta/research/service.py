"""Corridas de investigación: crear, consultar, cancelar y guardar resultados en Contactos."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS, SIZE_BANDS, classify
from orkesta.engine.taxonomy.titles import AREA_LABELS, LEVEL_LABELS, classify_title
from orkesta.research.agent import RESEARCH_JOB, company_key
from orkesta.research.models import ResearchItem, ResearchRun
from orkesta.research.wikidata import COUNTRY_QIDS

MAX_ACTIVE_RUNS = 2
LIMITS = (10, 25, 50)
LEVEL_CHOICES = ("c_level", "duenio_socio", "vp", "director", "gerente", "jefe")


def options() -> dict[str, Any]:
    return {
        "countries": sorted(
            ({"value": k, "label": COUNTRIES[k][0]} for k in COUNTRY_QIDS if k in COUNTRIES),
            key=lambda c: c["label"],
        ),
        "industries": [{"value": k, "label": v} for k, v in INDUSTRY_LABELS.items()],
        "sizes": [{"value": band, "label": f"{band} empleados"} for band, _, _ in SIZE_BANDS],
        "levels": [{"value": k, "label": LEVEL_LABELS[k]} for k in LEVEL_CHOICES],
        "areas": [{"value": k, "label": v} for k, v in AREA_LABELS.items()],
        "limits": list(LIMITS),
    }


def _summary(filters: dict[str, Any]) -> str:
    parts = [COUNTRIES.get(filters["country"], (filters["country"],))[0]]
    if filters["companies"]:
        names = ", ".join(filters["companies"][:3])
        more = len(filters["companies"]) - 3
        parts = [names + (f" y {more} más" if more > 0 else "")]
    parts += [INDUSTRY_LABELS.get(i, i) for i in filters["industries"][:2]]
    if filters["city"]:
        parts.append(filters["city"])
    if filters["levels"]:
        parts.append(", ".join(LEVEL_LABELS.get(lv, lv) for lv in filters["levels"][:2]))
    if filters["areas"]:
        parts.append(", ".join(AREA_LABELS.get(a, a) for a in filters["areas"][:2]))
    if filters["keywords"]:
        parts.append(f"«{filters['keywords']}»")
    return " · ".join(parts)[:300]


def validate_filters(data: dict[str, Any]) -> dict[str, Any]:
    country = str(data.get("country") or "").upper()
    if country not in COUNTRY_QIDS:
        raise DataError("Elige un país.")

    def pick(name: str, allowed: Any) -> list[str]:
        values = data.get(name) or []
        if not isinstance(values, list):
            raise DataError("Filtros no válidos.")
        return [str(v) for v in values if str(v) in allowed][:20]

    companies = data.get("companies") or []
    if not isinstance(companies, list):
        raise DataError("Filtros no válidos.")
    companies = [" ".join(str(c).split())[:120] for c in companies if str(c).strip()][:25]
    limit = int(data.get("limit") or 25)
    return {
        "country": country,
        "industries": pick("industries", INDUSTRY_LABELS),
        "sizes": pick("sizes", {band for band, _, _ in SIZE_BANDS}),
        "levels": pick("levels", LEVEL_CHOICES),
        "areas": pick("areas", AREA_LABELS),
        "city": " ".join(str(data.get("city") or "").split())[:80],
        "keywords": " ".join(str(data.get("keywords") or "").split())[:80],
        "companies": companies,
        "include_base": bool(data.get("include_base", True)),
        "limit": limit if limit in LIMITS else 25,
    }


def create_run(
    session: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, filters: dict[str, Any]
) -> ResearchRun:
    clean = validate_filters(filters)
    active = session.scalar(
        select(func.count())
        .select_from(ResearchRun)
        .where(ResearchRun.tenant_id == tenant_id, ResearchRun.status.in_(("queued", "running")))
    )
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
        "progress": run.progress,
        "totals": run.totals,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def recent_runs(session: Session, tenant_id: uuid.UUID, limit: int = 15) -> list[ResearchRun]:
    return list(
        session.scalars(
            select(ResearchRun)
            .where(ResearchRun.tenant_id == tenant_id)
            .order_by(ResearchRun.created_at.desc())
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
    created = merged = 0
    contact_ids: list[uuid.UUID] = []
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
        if contact is None:
            contact = Contact(
                tenant_id=tenant_id,
                company_id=company_ids.get(ckey),
                owner_user_id=user_id,
                dedup_key=dedup_key(email, full_name, ckey),
                source="investigacion",
                status="nuevo",
                **values,
            )
            session.add(contact)
            created += 1
        else:
            for key, value in values.items():
                if value and not getattr(contact, key):
                    setattr(contact, key, value)
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
        "list": {"id": str(target.id), "name": target.name} if target else None,
        "at": now().isoformat(),
    }
