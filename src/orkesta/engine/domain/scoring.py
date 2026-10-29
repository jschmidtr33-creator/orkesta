"""Puntuación explicable de un dominio candidato (``rules-v1``).

Es el baseline de reglas del ADR 0001: decide mientras no haya un modelo entrenado y es la
referencia que cualquier modelo debe superar. Cada punto sumado o restado deja un código de
razón; los pesos están aquí, versionados, y la escala va de 0 a 100.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from orkesta.engine.domain.signals import PageSignals
from orkesta.engine.normalize.company import CompanyName
from orkesta.engine.normalize.text import fold

MODEL_VERSION = "rules-v1"

WEIGHTS: dict[str, int] = {
    "DNS_RESOLVES": 10,
    "HAS_MX": 5,
    "SITE_OK": 10,
    "NAME_IN_TITLE": 30,
    "NAME_PARTIAL_IN_TITLE": 15,
    "BRAND_IN_TITLE": 25,
    "NAME_IN_BODY": 8,
    "WIKIDATA_WEBSITE": 25,
    "WIKIDATA_COUNTRY_MATCH": 10,
    "COUNTRY_SIGNALS": 10,
    "COUNTRY_TLD": 5,
    "LABEL_MATCHES_NAME": 10,
    "EMBEDDED_IN_NAME": 30,
    "SHORT_GENERIC_NAME": -10,
    "OTHER_COUNTRY_TLD": -10,
}

CONFIRMED, PROBABLE, CANDIDATE = 70, 50, 30


@dataclass
class CandidateEvidence:
    domain: str
    method: str
    resolves: bool = False
    has_mx: bool = False
    mx_provider: str | None = None
    site_ok: bool = False
    final_url: str | None = None
    page: PageSignals | None = None
    wikidata_qids: list[str] = field(default_factory=list)
    wikidata_country_match: bool = False
    robots_blocked: bool = False
    rejected_reason: str | None = None


@dataclass(frozen=True)
class ScoredCandidate:
    evidence: CandidateEvidence
    score: int
    status: str
    reasons: tuple[str, ...]
    rules_score: int | None = None  # puntuación de reglas cuando decide el modelo
    model_version: str = MODEL_VERSION

    @property
    def domain(self) -> str:
        return self.evidence.domain

    @property
    def confidence_level(self) -> str:
        if self.status == "confirmed":
            return "high"
        return "medium" if self.status == "probable" else "low"


def _label(domain: str) -> str:
    return domain.split(".")[0].replace("-", "")


def _label_similarity(domain: str, companies: list[CompanyName]) -> float:
    label = _label(domain)
    best = 0.0
    for company in companies:
        for name in {company.match_key.replace(" ", ""), "".join(company.distinctive_tokens)}:
            if name:
                best = max(best, SequenceMatcher(None, label, name).ratio())
    return best


def _brand_in_title(domain: str, page: PageSignals, companies: list[CompanyName]) -> bool:
    """El dominio es exactamente la marca ("alpina.com") y la marca está en el título."""
    label = _label(domain)
    brands = {
        token
        for company in companies
        for token in (*company.distinctive_tokens, *company.match_key.split())
        if len(token) >= 4
    }
    header = fold(f"{page.title} {page.site_name}")
    return label in brands and re.search(rf"\b{re.escape(label)}\b", header) is not None


def _is_short_generic(companies: list[CompanyName]) -> bool:
    """Nombres de una sola palabra corta ("Sophia", "Envases") tienen muchos homónimos."""
    return all(len(c.distinctive_tokens) <= 1 and len(c.match_key) <= 7 for c in companies)


def score(
    evidence: CandidateEvidence, companies: list[CompanyName], country: str | None
) -> ScoredCandidate:
    if evidence.rejected_reason or not evidence.resolves:
        reason = evidence.rejected_reason or "DNS_NO_RESOLVE"
        return ScoredCandidate(evidence, 0, "rejected", (reason,))

    reasons = ["DNS_RESOLVES"]
    if evidence.has_mx:
        reasons.append("HAS_MX")
    if evidence.method == "embedded":
        reasons.append("EMBEDDED_IN_NAME")
    if evidence.wikidata_qids:
        reasons.append("WIKIDATA_WEBSITE")
        if evidence.wikidata_country_match:
            reasons.append("WIKIDATA_COUNTRY_MATCH")
    page = evidence.page
    if evidence.site_ok and page is not None:
        reasons.append("SITE_OK")
        if page.name_in_header >= 0.99:
            reasons.append("NAME_IN_TITLE")
        elif _brand_in_title(evidence.domain, page, companies):
            reasons.append("BRAND_IN_TITLE")
        elif page.name_in_header > 0:
            reasons.append("NAME_PARTIAL_IN_TITLE")
        if page.name_in_body:
            reasons.append("NAME_IN_BODY")
        if page.country_markers:
            reasons.append("COUNTRY_SIGNALS")
    if _label_similarity(evidence.domain, companies) >= 0.9:
        reasons.append("LABEL_MATCHES_NAME")

    tld = evidence.domain.split(".", 1)[1] if "." in evidence.domain else ""
    country_tld = (country or "").lower()
    if country_tld and (tld == country_tld or tld.endswith("." + country_tld)):
        reasons.append("COUNTRY_TLD")
    elif country_tld and len(tld.split(".")[-1]) == 2 and tld.split(".")[-1] != country_tld:
        reasons.append("OTHER_COUNTRY_TLD")
    if _is_short_generic(companies):
        reasons.append("SHORT_GENERIC_NAME")
    if evidence.robots_blocked:
        reasons.append("ROBOTS_BLOCKED")

    total = max(0, min(100, sum(WEIGHTS.get(code, 0) for code in reasons)))
    # Confirmado exige evidencia de identidad, no solo que el dominio exista y se parezca.
    has_identity = any(
        code in reasons
        for code in ("NAME_IN_TITLE", "BRAND_IN_TITLE", "WIKIDATA_WEBSITE", "EMBEDDED_IN_NAME")
    )
    if total >= CONFIRMED and has_identity:
        status = "confirmed"
    elif total >= PROBABLE:
        status = "probable"
    elif total >= CANDIDATE:
        status = "candidate"
    else:
        status = "rejected"
    return ScoredCandidate(evidence, total, status, tuple(reasons))
