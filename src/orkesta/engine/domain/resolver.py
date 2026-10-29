"""Orquestación: de los nombres de una empresa a su dominio corporativo con evidencias.

No usa la base de datos: recibe nombres y país, y devuelve una ``DomainResolution`` que el
worker persiste o que la evaluación en vivo compara con el dominio correcto.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from orkesta.engine.domain.candidates import Candidate, generate_candidates
from orkesta.engine.domain.model import DomainModel, apply_model
from orkesta.engine.domain.scoring import CandidateEvidence, ScoredCandidate, score
from orkesta.engine.domain.signals import analyze_page
from orkesta.engine.domain.wikidata import CompanySource, WikidataError, WikidataHit
from orkesta.engine.net.dns import DnsError, Resolver
from orkesta.engine.net.domains import is_non_corporate, registrable_domain
from orkesta.engine.net.fetch import FetchResult, RobotsAwareFetcher
from orkesta.engine.normalize.company import CompanyName
from orkesta.engine.normalize.text import fold

logger = logging.getLogger("orkesta.engine.domain")

MAX_SITE_VISITS = 6
WORKERS = 6


class TransientResolutionError(Exception):
    """La red falló para todos los candidatos: conviene reintentar más tarde."""


@dataclass
class DomainResolution:
    selected: ScoredCandidate | None
    candidates: list[ScoredCandidate]
    wikidata_hits: list[WikidataHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.selected.status if self.selected else "unknown"


def _wikidata_candidates(
    source: CompanySource | None, companies: list[CompanyName], country: str | None
) -> tuple[list[WikidataHit], list[str]]:
    if source is None:
        return [], []
    hits: list[WikidataHit] = []
    notes: list[str] = []
    names = {c.display for c in companies if c.display}
    for name in list(names)[:2]:
        try:
            found = source.lookup(name)
        except WikidataError as exc:
            notes.append(f"wikidata: {exc}")
            continue
        wanted = {fold(c.display) for c in companies} | {c.match_key for c in companies}
        for hit in found:
            label = fold(hit.label)
            # Solo cuentan entidades cuyo nombre coincide con alguna variante del de la empresa.
            if label in wanted or any(w and (w in label or label in w) for w in wanted):
                hits.append(hit)
    # Primero las del país de la empresa.
    hits.sort(key=lambda h: (country not in h.countries, h.qid))
    return hits, notes


def _resolves(resolver: Resolver, domain: str) -> tuple[bool, bool]:
    """(resuelve, error_transitorio)."""
    try:
        return bool(resolver.resolve_ips(domain) or resolver.resolve_ips("www." + domain)), False
    except DnsError:
        return False, True


def _visit(fetcher: RobotsAwareFetcher, domain: str) -> tuple[FetchResult | None, bool]:
    """Visita la portada (https y después http con www). Devuelve (resultado, bloqueado)."""
    blocked = False
    last: FetchResult | None = None
    for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://www.{domain}/"):
        if not fetcher.allowed_by_robots(url):
            blocked = True
            continue
        result = fetcher.get(url)
        last = result
        if result.ok:
            return result, False
    return last, blocked


def resolve_domain(
    companies: list[CompanyName],
    country: str | None,
    *,
    resolver: Resolver,
    fetcher: RobotsAwareFetcher,
    wikidata: CompanySource | None = None,
    model: DomainModel | None = None,
) -> DomainResolution:
    hits, notes = _wikidata_candidates(wikidata, companies, country)
    extra = [Candidate(h.domain, "wikidata") for h in hits]
    candidates = generate_candidates(companies, country, extra)
    if not candidates:
        return DomainResolution(None, [], hits, [*notes, "sin candidatos"])

    evidence: dict[str, CandidateEvidence] = {
        c.domain: CandidateEvidence(domain=c.domain, method=c.method) for c in candidates
    }
    for hit in hits:
        item = evidence.get(hit.domain)
        if item is not None:
            item.wikidata_qids.append(hit.qid)
            item.wikidata_country_match = item.wikidata_country_match or country in hit.countries

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        dns_results = dict(
            zip(evidence, pool.map(lambda d: _resolves(resolver, d), evidence), strict=True)
        )
    transient = [d for d, (_, failed) in dns_results.items() if failed]
    if transient and len(transient) == len(dns_results):
        raise TransientResolutionError("DNS sin respuesta para todos los candidatos")
    for domain, (ok, _) in dns_results.items():
        evidence[domain].resolves = ok
    # Si nada resuelve, se descarta que el problema sea el DNS del entorno antes de concluir
    # que la empresa no tiene dominio: un DNS roto no debe producir "no encontrado".
    if not any(ok for ok, _ in dns_results.values()) and not resolver.healthy():
        raise TransientResolutionError("el DNS no responde correctamente (dominio de control)")

    to_visit = [d for d in evidence if evidence[d].resolves][:MAX_SITE_VISITS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        visits = dict(zip(to_visit, pool.map(lambda d: _visit(fetcher, d), to_visit), strict=True))

    for domain, (result, blocked) in visits.items():
        item = evidence[domain]
        item.robots_blocked = blocked
        if result is None or not result.ok:
            continue
        final_root = registrable_domain(result.final_url) or domain
        if is_non_corporate(final_root):
            item.rejected_reason = "REDIRECTS_TO_NON_CORPORATE"
            continue
        if final_root != domain:
            # Redirige a otro dominio (modelo.com → grupomodelo.com): el bueno es el destino.
            item.rejected_reason = f"REDIRECTS_TO:{final_root}"
            if final_root not in evidence:
                evidence[final_root] = CandidateEvidence(
                    domain=final_root, method=f"redirect:{domain}", resolves=True
                )
            target = evidence[final_root]
            target.wikidata_qids.extend(
                q for q in item.wikidata_qids if q not in target.wikidata_qids
            )
            target.wikidata_country_match |= item.wikidata_country_match
            item = target
        page = analyze_page(result.text, companies, country)
        if page.parked:
            item.rejected_reason = "PARKED_DOMAIN"
            continue
        item.site_ok = True
        item.final_url = result.final_url
        item.page = page

    for item in evidence.values():
        if item.resolves and not item.rejected_reason:
            try:
                mx = resolver.mx(item.domain)
            except DnsError:
                notes.append(f"mx: sin respuesta para {item.domain}")
                continue
            item.has_mx, item.mx_provider = mx.has_mx, mx.provider

    rule_scores = [score(item, companies, country) for item in evidence.values()]
    if model is not None:
        rule_scores = [apply_model(model, s, companies, country) for s in rule_scores]
    scored = sorted(
        rule_scores,
        key=lambda s: (-s.score, s.domain),
    )
    best = scored[0] if scored else None
    selected = best if best and best.status in ("confirmed", "probable") else None
    return DomainResolution(selected, scored, hits, notes)
