"""Generación de dominios candidatos a partir del nombre de la empresa y su país."""

from __future__ import annotations

import re
from dataclasses import dataclass

from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.normalize.company import CompanyName

# Extensiones por país, de la más probable a la menos probable para una empresa local.
COUNTRY_TLDS: dict[str, tuple[str, ...]] = {
    "MX": ("com.mx", "com", "mx"),
    "CO": ("com.co", "com", "co"),
    "PE": ("com.pe", "com", "pe"),
    "CL": ("cl", "com"),
    "AR": ("com.ar", "com"),
    "EC": ("com.ec", "com", "ec"),
    "VE": ("com.ve", "com"),
    "BO": ("com.bo", "com"),
    "PY": ("com.py", "com"),
    "UY": ("com.uy", "com"),
    "BR": ("com.br", "com"),
    "PA": ("com.pa", "com"),
    "CR": ("co.cr", "com", "cr"),
    "GT": ("com.gt", "com"),
    "HN": ("com.hn", "com", "hn"),
    "SV": ("com.sv", "com"),
    "NI": ("com.ni", "com"),
    "DO": ("com.do", "com"),
    "ES": ("es", "com"),
    "US": ("com",),
}
DEFAULT_TLDS = ("com",)
MAX_CANDIDATES = 14

_EMBEDDED_DOMAIN = re.compile(
    r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|mx)(?:\.[a-z]{2})?)\b"
)


@dataclass(frozen=True)
class Candidate:
    domain: str
    method: str  # embedded, wikidata, match_key, full_name, hyphenated, token, acronym


def country_tlds(country: str | None) -> tuple[str, ...]:
    return COUNTRY_TLDS.get(country or "", DEFAULT_TLDS)


def _labels(company: CompanyName) -> list[tuple[str, str]]:
    """Etiquetas (parte izquierda del dominio) con el método que las origina, por prioridad."""
    out: list[tuple[str, str]] = []
    match_tokens = company.match_key.split()
    distinctive = list(company.distinctive_tokens)
    all_tokens = list(company.tokens)

    if match_tokens:
        out.append(("".join(match_tokens), "match_key"))
    if distinctive:
        out.append(("".join(distinctive), "full_name"))
    if all_tokens and len(all_tokens) <= 4:
        out.append(("".join(all_tokens), "full_name"))
    if len(distinctive) > 1:
        out.append(("-".join(distinctive), "hyphenated"))
    for token in distinctive[:3]:
        if len(token) >= 4:
            out.append((token, "token"))
    if len(all_tokens) >= 3:
        out.append(("".join(t[0] for t in all_tokens), "acronym"))

    seen: set[str] = set()
    unique = []
    for label, method in out:
        if 2 <= len(label) <= 63 and label not in seen:
            seen.add(label)
            unique.append((label, method))
    return unique


def embedded_domains(company: CompanyName) -> list[str]:
    """Dominios escritos en el propio nombre: "Kavak.com" → ``kavak.com``."""
    found = []
    for match in _EMBEDDED_DOMAIN.finditer(company.original.lower()):
        root = registrable_domain(match.group(1))
        if root and not is_non_corporate(root):
            found.append(root)
    return found


def generate_candidates(
    companies: list[CompanyName], country: str | None, extra: list[Candidate] | None = None
) -> list[Candidate]:
    """Candidatos de todas las variantes del nombre, sin duplicados y en orden de prioridad."""
    ordered: list[Candidate] = []
    for company in companies:
        ordered.extend(Candidate(d, "embedded") for d in embedded_domains(company))
    ordered.extend(extra or [])
    tlds = country_tlds(country)
    for company in companies:
        for label, method in _labels(company):
            for tld in tlds:
                host = normalize_host(f"{label}.{tld}")
                if host:
                    ordered.append(Candidate(host, method))

    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in ordered:
        root = registrable_domain(candidate.domain)
        if root is None or root in seen or is_non_corporate(root):
            continue
        seen.add(root)
        unique.append(Candidate(root, candidate.method))
    # Los candidatos con evidencia externa no cuentan para el límite.
    priority = [c for c in unique if c.method in ("embedded", "wikidata")]
    generated = [c for c in unique if c.method not in ("embedded", "wikidata")]
    return priority + generated[: max(0, MAX_CANDIDATES - len(priority))]
