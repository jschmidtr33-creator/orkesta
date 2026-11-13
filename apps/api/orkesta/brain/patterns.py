"""Formato de correo de empresas nuevas (IA propia, paso 4).

Para una empresa que ORKESTA nunca vio, predice cómo arma sus correos (nombre.apellido,
inicial + apellido…) con lo que sabe de ella: país y tipo de dominio, sector, tamaño y lo que
hacen empresas parecidas. Aprende de los formatos confirmados por respuestas y correos
públicos de todas las empresas (solo dominios y formatos, nunca datos de personas).

Reemplaza a la regla anterior («el formato más común del país») solo si acierta más que ella
en empresas que el modelo no vio al entrenar.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.brain import ml

logger = logging.getLogger(__name__)
TASK = "formato_correo"
_GOV = ("gov", "gob", "mil")
_EDU = ("edu", "ac")


def _size(employees: int | None) -> str:
    if not employees:
        return "?"
    for top, label in ((10, "micro"), (50, "pequena"), (250, "mediana"), (1000, "grande")):
        if employees < top:
            return label
    return "corporativa"


def features(domain: str, sector: str | None = None, employees: int | None = None,
             country: str | None = None) -> ml.Features:  # fmt: skip
    """Rasgos de una empresa a partir de su dominio y de lo que dice el directorio."""
    from orkesta.research.memory import domain_suffix

    domain = domain.lower().strip()
    parts = domain.split(".")
    suffix = domain_suffix(domain)
    name = parts[0] if parts else ""
    x: ml.Features = {f"suf:{suffix}": 1.0, f"tld:{parts[-1] if parts else ''}": 1.0}
    if any(p in _GOV for p in parts[1:]):
        x["tipo:gobierno"] = 1.0
    elif any(p in _EDU for p in parts[1:]):
        x["tipo:educacion"] = 1.0
    elif "org" in parts[1:]:
        x["tipo:organizacion"] = 1.0
    else:
        x["tipo:empresa"] = 1.0
    x[f"largo:{'corto' if len(name) <= 5 else 'medio' if len(name) <= 10 else 'largo'}"] = 1.0
    if "-" in name:
        x["guion"] = 1.0
    if sector:
        x[f"sector:{sector}"] = 1.0
        x[f"sector_suf:{sector}|{suffix}"] = 1.0
    x[f"tamano:{_size(employees)}"] = 1.0
    if country:
        x[f"pais:{country.upper()}"] = 1.0
    return x


def _directory(
    session: Session, domains: set[str]
) -> dict[str, tuple[str | None, int | None, str]]:
    from orkesta.research.models import CompanyDirectory

    if not domains:
        return {}
    out: dict[str, tuple[str | None, int | None, str]] = {}
    rows = session.execute(
        select(CompanyDirectory.domain, CompanyDirectory.industry_code,
               CompanyDirectory.employees, CompanyDirectory.country)
        .where(CompanyDirectory.domain.in_(domains))
    ).all()  # fmt: skip
    for domain, sector, employees, country in rows:
        out.setdefault(domain, (sector, employees, country))
    return out


def _label(stats: list[Any]) -> str | None:
    """El formato que la evidencia respalda en un dominio (o ninguno si no es clara)."""
    from orkesta.research.memory import _bad, weight

    good = [st for st in stats if not _bad(st) and (st.confirmed or st.seen or st.delivered)]
    if not good:
        return None
    best = max(good, key=lambda s: (weight(s), s.confirmed))
    return str(best.pattern)


def build(session: Session) -> list[ml.Example]:
    from orkesta.research.models import PatternStat

    by_domain: dict[str, list[Any]] = defaultdict(list)
    for st in session.scalars(select(PatternStat)):
        by_domain[st.domain].append(st)
    info = _directory(session, set(by_domain))
    examples = []
    for domain, stats in sorted(by_domain.items()):
        label = _label(stats)
        if label is None:
            continue
        sector, employees, country = info.get(domain, (None, None, ""))
        examples.append(
            ml.Example(f"d:{domain}", features(domain, sector, employees, country), label, domain)
        )
    return examples


def make() -> ml.NaiveBayes:
    return ml.NaiveBayes(alpha=0.5, min_count=1)


def predictor(model: Any, threshold: float, ex: ml.Example) -> tuple[str | None, float]:
    return ml.predict(model, ex.x)


def baseline(session: Session, test: list[ml.Example]) -> ml.Metrics:
    """La regla anterior: el formato más común en las empresas del mismo país (sin mirar las de
    prueba, para comparar en igualdad de condiciones)."""
    from orkesta.research.memory import FALLBACK_ORDER, domain_suffix

    held = {ex.key for ex in test}
    by_suffix: dict[str, Counter[str]] = defaultdict(Counter)
    overall: Counter[str] = Counter()
    for ex in build(session):
        if ex.key in held:
            continue
        by_suffix[domain_suffix(str(ex.raw))][ex.y] += 1
        overall[ex.y] += 1
    pairs = []
    for ex in test:
        counts = by_suffix.get(domain_suffix(str(ex.raw))) or overall
        guess = counts.most_common(1)[0][0] if counts else FALLBACK_ORDER[0]
        pairs.append((ex.y, guess, 1.0))
    return ml.score(pairs)


def ranking(session: Session, domain: str) -> list[tuple[str, int]] | None:
    """Formatos ordenados por probabilidad para una empresa sin evidencia propia, según el
    modelo activo (``None`` si no hay modelo activo: se usa la regla del país)."""
    from orkesta.brain import registry

    try:
        loaded = registry.active(session, TASK)
        if loaded is None:
            return None
        model = loaded[0]
        sector, employees, country = _directory(session, {domain}).get(domain, (None, None, ""))
        probs = model.predict_proba(features(domain, sector, employees, country))
    except Exception:
        logger.exception("brain_patterns_ranking")
        return None
    ordered = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    return [(label, round(100 * p)) for label, p in ordered if p > 0]
