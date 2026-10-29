"""Decisión del patrón de correo de una empresa y generación de correos por persona.

Combina lo observado en el sitio con un conocimiento previo (``priors``) por país y proveedor
de correo. El previo inicial es una estimación honesta que se reemplaza automáticamente por el
aprendido de los propios datos recolectados (``orkesta.evaluation.patterns``): a más empresas
observadas, mejor previo para las empresas sin correos públicos.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from orkesta.engine.email.harvest import Harvest
from orkesta.engine.email.observe import Observation
from orkesta.engine.email.patterns import PATTERNS, render
from orkesta.engine.net.domains import registrable_domain
from orkesta.engine.normalize.names import PersonName

MODEL_VERSION = "patterns-v1"
PRIORS_FILE = Path(__file__).parent / "priors.json"

# Previo inicial (antes de aprender de datos propios): distribución aproximada en empresas
# medianas y grandes de LATAM. Solo decide cuando no hay ninguna observación.
DEFAULT_PRIOR: dict[str, float] = {
    "first.last": 0.40,
    "flast": 0.22,
    "first": 0.08,
    "firstlast": 0.06,
    "flastl2": 0.05,
    "f.last": 0.04,
    "first_last": 0.03,
    "first.last.last2": 0.02,
    "firstl": 0.02,
    "last.first": 0.02,
    "first.l": 0.01,
    "lastf": 0.01,
    "first-last": 0.01,
    "first.lastl2": 0.01,
    "lastfirst": 0.01,
    "last": 0.01,
}


@lru_cache(maxsize=1)
def learned_priors() -> dict[str, dict[str, float]]:
    """Previos aprendidos por clave (``MX``, ``MX|google_workspace``, ``*``), si existen."""
    if not PRIORS_FILE.exists():
        return {}
    data = json.loads(PRIORS_FILE.read_text(encoding="utf-8"))
    priors: dict[str, dict[str, float]] = data.get("priors", {})
    return priors


def prior_for(country: str | None, mx_provider: str | None) -> dict[str, float]:
    learned = learned_priors()
    for key in (f"{country}|{mx_provider}", country or "", "*"):
        if key in learned:
            return learned[key]
    return DEFAULT_PRIOR


@dataclass
class PatternDecision:
    email_domain: str | None
    ranked: list[tuple[str, float]]  # (patrón, probabilidad) de mayor a menor
    confidence_score: int
    confidence_level: str
    reasons: list[str] = field(default_factory=list)
    named: int = 0
    structural: int = 0
    exceptions: int = 0

    @property
    def pattern(self) -> str | None:
        return self.ranked[0][0] if self.ranked else None

    @property
    def origin(self) -> str:
        return "observed" if self.named or self.structural else "learned"


def choose_email_domain(site_domain: str, harvest: Harvest | None, has_mx: bool) -> str | None:
    """Dominio que recibe el correo: el que usan los correos publicados, si difiere del sitio
    (samsung.com.mx → @samsung.com), y si no, el del sitio cuando tiene MX."""
    if harvest is not None:
        counts = harvest.email_domains
        if counts:
            best, n = next(iter(counts.items()))
            if best == site_domain or n >= 2 or site_domain not in counts:
                return best
    return site_domain if has_mx else None


def decide(
    site_domain: str,
    observations: list[Observation],
    *,
    country: str | None,
    mx_provider: str | None,
    has_mx: bool,
    harvest: Harvest | None = None,
) -> PatternDecision:
    email_domain = choose_email_domain(site_domain, harvest, has_mx)
    prior = prior_for(country, mx_provider)
    if email_domain is None:
        return PatternDecision(None, [], 0, "low", ["NO_MX"])
    relevant = [
        o for o in observations if (registrable_domain(o.email_domain) or "") == email_domain
    ]
    votes: dict[str, float] = defaultdict(float)
    for obs in relevant:
        votes[obs.pattern] += obs.weight
    named = sum(1 for o in relevant if o.kind == "named")
    structural = len(relevant) - named

    # Posterior simple: votos observados más el previo como pseudo-observación (peso 1).
    total = sum(votes.values()) + 1.0
    posterior = {p: (votes.get(p, 0.0) + prior.get(p, 0.0)) / total for p in PATTERNS}
    ranked = sorted(posterior.items(), key=lambda kv: (-kv[1], kv[0]))
    top, top_p = ranked[0]
    agreeing = Counter(o.pattern for o in relevant)
    exceptions = len(relevant) - agreeing.get(top, 0)

    reasons: list[str] = []
    named_top = sum(1 for o in relevant if o.kind == "named" and o.pattern == top)
    if named_top >= 2 and top_p >= 0.6:
        level, score = "high", min(95, 70 + 8 * named_top)
        reasons.append("PATTERN_NAMED_MULTIPLE")
    elif named_top >= 1 and top_p >= 0.45:
        level, score = "medium", 65
        reasons.append("PATTERN_NAMED_ONE")
    elif agreeing.get(top, 0) >= 2 and top_p >= 0.45:
        level, score = "medium", 55
        reasons.append("PATTERN_STRUCTURAL_MULTIPLE")
    elif relevant:
        level, score = "low", 40
        reasons.append("PATTERN_WEAK_EVIDENCE")
    else:
        level, score = "low", round(30 * prior.get(top, 0.0) / max(prior.values()))
        reasons.append("PATTERN_PRIOR")
    if email_domain != site_domain:
        reasons.append("EMAIL_DOMAIN_FROM_SITE")
    if exceptions:
        reasons.append("PATTERN_EXCEPTIONS")
    return PatternDecision(
        email_domain, ranked, score, level, reasons, named, structural, exceptions
    )


@dataclass(frozen=True)
class GeneratedEmail:
    address: str
    rank: int
    pattern: str
    label: str  # publicly_found | inferred
    confidence_score: int
    confidence_level: str


def generate_emails(
    person: PersonName,
    decision: PatternDecision,
    published: set[str] | None = None,
    limit: int = 3,
) -> list[GeneratedEmail]:
    """Hasta ``limit`` correos para una persona, del más al menos probable."""
    if decision.email_domain is None or not decision.ranked:
        return []
    published = published or set()
    out: list[GeneratedEmail] = []
    seen: set[str] = set()
    top_p = decision.ranked[0][1]
    for pattern, probability in decision.ranked:
        local = render(pattern, person)
        if not local:
            continue
        address = f"{local}@{decision.email_domain}".lower()
        if address in seen:
            continue
        seen.add(address)
        rank = len(out)
        if address in published:
            score, level, label = 98, "high", "publicly_found"
        elif rank == 0:
            score, level, label = decision.confidence_score, decision.confidence_level, "inferred"
        else:
            # Alternativas: confianza proporcional a su probabilidad frente a la primera.
            score = max(5, round(decision.confidence_score * probability / max(top_p, 1e-9)))
            level, label = "low", "inferred"
        out.append(GeneratedEmail(address, rank, pattern, label, min(score, 100), level))
        if len(out) == limit:
            break
    # Un correo publicado de la propia persona siempre va primero.
    out.sort(key=lambda e: (e.label != "publicly_found", e.rank))
    return [
        GeneratedEmail(e.address, i, e.pattern, e.label, e.confidence_score, e.confidence_level)
        for i, e in enumerate(out)
    ]
