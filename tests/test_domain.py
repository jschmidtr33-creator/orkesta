"""Motor de dominios con red simulada. Empresas y dominios ficticios salvo donde se indica."""

from __future__ import annotations

import json

import httpx
import pytest

from fakes import FakeFetcher, FakeResolver, page
from orkesta.engine.domain.candidates import MAX_CANDIDATES, generate_candidates
from orkesta.engine.domain.resolver import TransientResolutionError, resolve_domain
from orkesta.engine.domain.scoring import CandidateEvidence, score
from orkesta.engine.domain.signals import analyze_page
from orkesta.engine.domain.wikidata import WikidataHit, WikidataSource
from orkesta.engine.normalize import normalize_company

IP = ["93.184.216.34"]


def _names(*raw: str) -> list:  # type: ignore[type-arg]
    return [normalize_company(r) for r in raw]


# --- candidatos ----------------------------------------------------------------------------


def test_candidates_follow_country_tlds_and_priority() -> None:
    domains = [c.domain for c in generate_candidates(_names("Grupo Modelo S.A. de C.V."), "MX")]
    assert domains[:3] == ["grupomodelo.com.mx", "grupomodelo.com", "grupomodelo.mx"]
    assert "modelo.com.mx" in domains


def test_candidates_from_all_name_variants() -> None:
    domains = [
        c.domain for c in generate_candidates(_names("Laboratorios Sanfer", "Sanfer S.A."), "MX")
    ]
    assert domains[0] == "sanfer.com.mx"
    assert "laboratoriossanfer.com.mx" in domains


def test_embedded_domain_comes_first() -> None:
    candidates = generate_candidates(_names("Kavak.com"), "MX")
    assert (candidates[0].domain, candidates[0].method) == ("kavak.com", "embedded")


def test_colombian_tlds() -> None:
    domains = [c.domain for c in generate_candidates(_names("Alpina Productos"), "CO")]
    assert domains[0] == "alpinaproductos.com.co" or domains[0] == "alpina.com.co"
    assert any(d.endswith(".com.co") for d in domains)


def test_candidate_limit() -> None:
    candidates = generate_candidates(
        _names("Compañía Uno Dos Tres Cuatro", "Otra Variante Larga Del Nombre"), "MX"
    )
    assert len(candidates) <= MAX_CANDIDATES


# --- señales de página ---------------------------------------------------------------------


def test_page_with_company_name_in_title_and_country_markers() -> None:
    html = """<html><head><title>Lácteos Inventados | Inicio</title>
    <meta property="og:site_name" content="Lácteos Inventados"></head>
    <body><script>var x = "mexico";</script>
    <footer>Lácteos Inventados S.A. de C.V. · Ciudad de México · Tel. +52 55 1234 5678 ·
    Aviso de privacidad</footer></body></html>"""
    signals = analyze_page(html, _names("Lácteos Inventados"), "MX")
    assert signals.name_in_header == 1.0 and signals.name_in_body
    assert {"ciudad de mexico", "+52", "aviso de privacidad"} <= set(signals.country_markers)
    assert not signals.parked


def test_parked_page_is_detected() -> None:
    html = "<html><title>lacteosinventados.mx</title><body>This domain is for sale!</body></html>"
    assert analyze_page(html, _names("Lácteos Inventados"), "MX").parked


def test_broken_html_does_not_crash() -> None:
    signals = analyze_page("<html><title>Empresa Demo<<<</ti", _names("Empresa Demo"), "MX")
    assert signals.title.startswith("Empresa Demo")


# --- puntuación ----------------------------------------------------------------------------


def test_non_resolving_domain_is_rejected() -> None:
    result = score(CandidateEvidence("nada.com.mx", "match_key"), _names("Nada"), "MX")
    assert result.status == "rejected" and result.reasons == ("DNS_NO_RESOLVE",)


def test_existing_domain_without_identity_cannot_be_confirmed() -> None:
    evidence = CandidateEvidence("demoindustrial.com.mx", "match_key", resolves=True, has_mx=True)
    result = score(evidence, _names("Demo Industrial"), "MX")
    assert result.status in ("candidate", "rejected")
    assert "NAME_IN_TITLE" not in result.reasons


def test_foreign_tld_is_penalized() -> None:
    evidence = CandidateEvidence("demoindustrial.co", "match_key", resolves=True)
    assert "OTHER_COUNTRY_TLD" in score(evidence, _names("Demo Industrial"), "MX").reasons


# --- Wikidata ------------------------------------------------------------------------------


def test_wikidata_source_reads_official_website_and_country() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("ORKESTA-bot/")
        action = request.url.params["action"]
        if action == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q1"}, {"id": "Q2"}]})
        entities = {
            "Q1": {
                "labels": {"es": {"value": "Lácteos Inventados"}},
                "claims": {
                    "P856": [{"mainsnak": {"datavalue": {"value": "https://www.lacteos.mx/"}}}],
                    "P17": [{"mainsnak": {"datavalue": {"value": {"id": "Q96"}}}}],
                },
            },
            "Q2": {"labels": {"es": {"value": "Sin web"}}, "claims": {}},
        }
        return httpx.Response(200, content=json.dumps({"entities": entities}))

    hits = WikidataSource(transport=httpx.MockTransport(handler)).lookup("Lácteos Inventados")
    assert hits == [WikidataHit("Q1", "Lácteos Inventados", "lacteos.mx", ("MX",))]
    assert hits[0].url == "https://www.wikidata.org/wiki/Q1"


class _FakeWikidata:
    def __init__(self, hits: list[WikidataHit]) -> None:
        self.hits = hits

    def lookup(self, name: str) -> list[WikidataHit]:
        return self.hits


# --- resolución completa -------------------------------------------------------------------

TITLE_PAGE = (
    "<html><head><title>Lácteos Inventados</title></head><body>Lácteos Inventados, "
    "México. Tel +52 55 0000 0000</body></html>"
)


def test_resolves_official_site_with_title_and_country_signals() -> None:
    resolver = FakeResolver(
        ips={"lacteosinventados.com.mx": IP, "lacteosinventados.com": IP},
        mx_hosts={"lacteosinventados.com.mx": ["aspmx.l.google.com"]},
    )
    fetcher = FakeFetcher(
        pages={
            "https://lacteosinventados.com.mx/": page(
                "https://lacteosinventados.com.mx/", TITLE_PAGE
            ),
            "https://lacteosinventados.com/": page(
                "https://lacteosinventados.com/",
                "<title>Inventados Dairy Inc.</title><body>Ohio, United States</body>",
            ),
        }
    )
    result = resolve_domain(_names("Lácteos Inventados"), "MX", resolver=resolver, fetcher=fetcher)
    assert result.selected is not None
    assert result.selected.domain == "lacteosinventados.com.mx"
    assert result.status == "confirmed"
    assert result.selected.evidence.mx_provider == "google_workspace"
    assert {"NAME_IN_TITLE", "COUNTRY_SIGNALS", "COUNTRY_TLD", "HAS_MX"} <= set(
        result.selected.reasons
    )


def test_redirect_moves_identity_to_final_domain() -> None:
    resolver = FakeResolver(ips={"modelo.com.mx": IP, "grupomodelo.com": IP})
    fetcher = FakeFetcher(
        pages={
            "https://modelo.com.mx/": page(
                "https://modelo.com.mx/",
                "<title>Grupo Modelo</title><body>México</body>",
                final_url="https://www.grupomodelo.com/inicio",
            ),
            "https://grupomodelo.com/": page(
                "https://grupomodelo.com/", "<title>Grupo Modelo</title><body>México</body>"
            ),
        }
    )
    result = resolve_domain(_names("Grupo Modelo"), "MX", resolver=resolver, fetcher=fetcher)
    assert result.selected is not None and result.selected.domain == "grupomodelo.com"
    redirected = next(c for c in result.candidates if c.domain == "modelo.com.mx")
    assert redirected.status == "rejected" and redirected.reasons[0].startswith("REDIRECTS_TO:")


def test_parked_domain_is_rejected_and_nothing_selected() -> None:
    resolver = FakeResolver(ips={"empresademo.com.mx": IP})
    fetcher = FakeFetcher(
        pages={
            "https://empresademo.com.mx/": page(
                "https://empresademo.com.mx/", "<title>empresademo</title>Buy this domain"
            )
        }
    )
    result = resolve_domain(_names("Empresa Demo"), "MX", resolver=resolver, fetcher=fetcher)
    parked = next(c for c in result.candidates if c.domain == "empresademo.com.mx")
    assert parked.reasons == ("PARKED_DOMAIN",)
    assert result.selected is None and result.status == "unknown"


def test_wikidata_website_confirms_domain_without_name_match() -> None:
    hit = WikidataHit("Q42", "Lácteos Inventados", "li-corp.mx", ("MX",))
    resolver = FakeResolver(ips={"li-corp.mx": IP}, mx_hosts={"li-corp.mx": ["mx.li-corp.mx"]})
    fetcher = FakeFetcher(
        pages={
            "https://li-corp.mx/": page("https://li-corp.mx/", "<title>Lácteos Inventados</title>")
        }
    )
    result = resolve_domain(
        _names("Lácteos Inventados"),
        "MX",
        resolver=resolver,
        fetcher=fetcher,
        wikidata=_FakeWikidata([hit]),
    )
    assert result.selected is not None and result.selected.domain == "li-corp.mx"
    assert "WIKIDATA_WEBSITE" in result.selected.reasons
    assert "WIKIDATA_COUNTRY_MATCH" in result.selected.reasons
    assert result.status == "confirmed"


def test_wikidata_hits_with_unrelated_names_are_ignored() -> None:
    hit = WikidataHit("Q7", "Otra Cosa Totalmente Distinta", "otracosa.com", ("US",))
    result = resolve_domain(
        _names("Lácteos Inventados"),
        "MX",
        resolver=FakeResolver(),
        fetcher=FakeFetcher(),
        wikidata=_FakeWikidata([hit]),
    )
    assert result.wikidata_hits == []
    assert all(c.domain != "otracosa.com" for c in result.candidates)


def test_robots_blocked_site_is_not_visited() -> None:
    resolver = FakeResolver(ips={"empresademo.com.mx": IP})
    fetcher = FakeFetcher(
        pages={"https://empresademo.com.mx/": page("https://empresademo.com.mx/", TITLE_PAGE)},
        disallowed={"https://empresademo.com.mx", "https://www.empresademo.com.mx", "http://"},
    )
    result = resolve_domain(_names("Empresa Demo"), "MX", resolver=resolver, fetcher=fetcher)
    assert "https://empresademo.com.mx/" not in fetcher.requested
    candidate = next(c for c in result.candidates if c.domain == "empresademo.com.mx")
    assert "ROBOTS_BLOCKED" in candidate.reasons and "SITE_OK" not in candidate.reasons


def test_dns_outage_for_every_candidate_is_transient() -> None:
    names = _names("Empresa Demo")
    domains = {c.domain for c in generate_candidates(names, "MX")}
    resolver = FakeResolver(failing=domains | {f"www.{d}" for d in domains})
    with pytest.raises(TransientResolutionError):
        resolve_domain(names, "MX", resolver=resolver, fetcher=FakeFetcher())


def test_unknown_when_nothing_resolves() -> None:
    result = resolve_domain(
        _names("Empresa Inexistente"), "MX", resolver=FakeResolver(), fetcher=FakeFetcher()
    )
    assert result.selected is None and result.status == "unknown"
    assert all(c.status == "rejected" for c in result.candidates)


def test_brand_domain_with_brand_in_title_is_selected() -> None:
    """ "Alpina Productos Alimenticios" → alpina.com cuyo título solo dice "Alpina"."""
    resolver = FakeResolver(ips={"alpina.com": IP}, mx_hosts={"alpina.com": ["mx.alpina.com"]})
    fetcher = FakeFetcher(
        pages={
            "https://alpina.com/": page(
                "https://alpina.com/", "<title>Alpina | Inicio</title><body>Colombia +57</body>"
            )
        }
    )
    result = resolve_domain(
        _names("Alpina Productos Alimenticios S.A. BIC"), "CO", resolver=resolver, fetcher=fetcher
    )
    assert result.selected is not None and result.selected.domain == "alpina.com"
    assert "BRAND_IN_TITLE" in result.selected.reasons
    assert result.status in ("confirmed", "probable")


def test_brand_in_title_requires_domain_to_be_the_brand() -> None:
    resolver = FakeResolver(ips={"alpinaalimentos.com": IP})
    fetcher = FakeFetcher(
        pages={
            "https://alpinaalimentos.com/": page(
                "https://alpinaalimentos.com/", "<title>Alpina</title>"
            )
        }
    )
    result = resolve_domain(
        _names("Alpina Productos Alimenticios"), "CO", resolver=resolver, fetcher=fetcher
    )
    candidate = next((c for c in result.candidates if c.domain == "alpinaalimentos.com"), None)
    assert candidate is None or "BRAND_IN_TITLE" not in candidate.reasons


def test_broken_dns_is_not_reported_as_unknown() -> None:
    """Un DNS que responde NXDOMAIN a todo (red restringida) no debe dar "no encontrado"."""
    with pytest.raises(TransientResolutionError, match="control"):
        resolve_domain(
            _names("Empresa Demo"),
            "MX",
            resolver=FakeResolver(is_healthy=False),
            fetcher=FakeFetcher(),
        )
