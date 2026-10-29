"""Variables numéricas de un dominio candidato, para entrenar y aplicar el modelo de dominio.

Las mismas variables se calculan en la evaluación en vivo (para entrenar) y en producción
(para predecir), de modo que el modelo nunca ve en producción algo distinto de lo que aprendió.
Solo contienen datos de la empresa y del sitio, nunca de personas.
"""

from __future__ import annotations

from orkesta.engine.domain.scoring import (
    CandidateEvidence,
    _brand_in_title,
    _is_short_generic,
    _label,
    _label_similarity,
)
from orkesta.engine.normalize.company import CompanyName

METHODS = ("match_key", "full_name", "hyphenated", "token", "acronym", "wikidata", "embedded")

FEATURE_NAMES: tuple[str, ...] = (
    "resolves",
    "has_mx",
    "mx_google_or_microsoft",
    "site_ok",
    "robots_blocked",
    "rejected_redirect",
    "rejected_parked",
    "via_redirect",
    "name_in_header",
    "name_in_body",
    "brand_in_title",
    "country_markers",
    "wikidata",
    "wikidata_country_match",
    "label_similarity",
    "label_length",
    "country_tld",
    "dot_com",
    "other_country_tld",
    "short_generic_name",
    "name_tokens",
    *(f"method_{m}" for m in METHODS),
)


def candidate_features(
    evidence: CandidateEvidence, companies: list[CompanyName], country: str | None
) -> dict[str, float]:
    page = evidence.page
    site = evidence.site_ok and page is not None
    rejected = evidence.rejected_reason or ""
    tld = evidence.domain.split(".", 1)[1] if "." in evidence.domain else ""
    country_tld = (country or "").lower()
    last = tld.split(".")[-1]
    method = evidence.method.split(":", 1)[0]
    values: dict[str, float] = {
        "resolves": float(evidence.resolves),
        "has_mx": float(evidence.has_mx),
        "mx_google_or_microsoft": float(
            evidence.mx_provider in ("google_workspace", "microsoft_365")
        ),
        "site_ok": float(site),
        "robots_blocked": float(evidence.robots_blocked),
        "rejected_redirect": float(rejected.startswith("REDIRECTS_TO")),
        "rejected_parked": float(rejected == "PARKED_DOMAIN"),
        "via_redirect": float(method == "redirect"),
        "name_in_header": page.name_in_header if site and page else 0.0,
        "name_in_body": float(bool(site and page and page.name_in_body)),
        "brand_in_title": float(
            bool(site and page and _brand_in_title(evidence.domain, page, companies))
        ),
        "country_markers": float(len(page.country_markers)) if site and page else 0.0,
        "wikidata": float(bool(evidence.wikidata_qids)),
        "wikidata_country_match": float(evidence.wikidata_country_match),
        "label_similarity": _label_similarity(evidence.domain, companies),
        "label_length": float(len(_label(evidence.domain))),
        "country_tld": float(
            bool(country_tld) and (tld == country_tld or tld.endswith("." + country_tld))
        ),
        "dot_com": float(tld == "com"),
        "other_country_tld": float(len(last) == 2 and bool(country_tld) and last != country_tld),
        "short_generic_name": float(_is_short_generic(companies)),
        "name_tokens": float(max((len(c.distinctive_tokens) for c in companies), default=0)),
    }
    for m in METHODS:
        values[f"method_{m}"] = float(method == m)
    return {name: values[name] for name in FEATURE_NAMES}
