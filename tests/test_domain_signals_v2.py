"""Sitios protegidos contra bots, tokens genéricos y siglas de dos palabras."""

from __future__ import annotations

from orkesta.engine.domain.candidates import generate_candidates
from orkesta.engine.domain.features import candidate_features
from orkesta.engine.domain.scoring import CandidateEvidence, score
from orkesta.engine.normalize import normalize_company


def test_protected_site_counts_as_live_server() -> None:
    names = [normalize_company("Kavak.com")]
    base = CandidateEvidence("kavak.com", "embedded", resolves=True, has_mx=True)
    blocked = CandidateEvidence(
        "kavak.com", "embedded", resolves=True, has_mx=True, http_status=403
    )
    assert "SITE_PROTECTED" in score(blocked, names, "MX").reasons
    assert score(blocked, names, "MX").score > score(base, names, "MX").score
    assert candidate_features(blocked, names, "MX")["site_protected"] == 1.0


def test_timeout_is_not_protection() -> None:
    names = [normalize_company("Alpura")]
    failed = CandidateEvidence(
        "alpura.com", "match_key", resolves=True, fetch_error="tiempo agotado"
    )
    assert "SITE_PROTECTED" not in score(failed, names, "MX").reasons
    assert candidate_features(failed, names, "MX")["fetch_failed"] == 1.0


def test_generic_words_are_not_token_candidates() -> None:
    domains = {
        c.domain for c in generate_candidates([normalize_company("Mattel Latin America")], "MX")
    }
    assert "latin.com" not in domains
    assert "mattel.com" in domains


def test_two_word_acronym() -> None:
    domains = {
        c.domain for c in generate_candidates([normalize_company("General Motors de México")], "MX")
    }
    assert "gm.com" in domains
    assert "general.com.mx" not in {
        c.domain
        for c in generate_candidates([normalize_company("General Motors de México")], "MX")
        if c.method == "token"
    }
