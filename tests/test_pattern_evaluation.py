"""Evaluación autónoma de patrones (red simulada, datos inventados)."""

from __future__ import annotations

from pathlib import Path

from fakes import FakeFetcher, FakeResolver, page
from orkesta.engine.email.observe import Observation
from orkesta.enrichment.service import EngineDeps
from orkesta.evaluation.patterns import (
    MIN_DOMAINS_FOR_PRIOR,
    DomainResult,
    evaluate_domain,
    learn_priors,
    leave_one_out,
    write_outputs,
)


def _engine() -> EngineDeps:
    team = """
    <p>Laura Méndez — lmendez@acme.mx</p>
    <p>Pedro Ramírez — pramirez@acme.mx</p>
    <p>Sofía Torres — storres@acme.mx</p>
    <p>ventas@acme.mx</p>
    """
    fetcher = FakeFetcher(
        pages={
            "https://acme.mx/": page("https://acme.mx/", '<a href="/equipo">Equipo</a>'),
            "https://acme.mx/equipo": page("https://acme.mx/equipo", team),
        }
    )
    resolver = FakeResolver(mx_hosts={"acme.mx": ["acme-mx.mail.protection.outlook.com"]})
    return EngineDeps(resolver, fetcher, None)


def test_evaluate_domain_end_to_end() -> None:
    result = evaluate_domain("acme.mx", "MX", _engine())
    assert result.has_mx and result.mx_provider == "microsoft_365"
    assert result.emails == 4 and result.role == 1
    assert [o.kind for o in result.observations] == ["named"] * 3
    assert result.pattern == "flast" and result.confidence == "high"
    assert leave_one_out([result]) == {"pruebas": 3, "aciertos": 3, "exactitud_pct": 100.0}


def test_priors_need_enough_companies() -> None:
    few = [
        DomainResult(f"e{i}.mx", "MX", observations=[Observation("flast", "named", f"e{i}.mx")])
        for i in range(MIN_DOMAINS_FOR_PRIOR - 1)
    ]
    assert learn_priors(few) == {}
    enough = [*few, DomainResult("z.mx", "MX", observations=[Observation("flast", "named", "z")])]
    priors = learn_priors(enough)
    assert max(priors["MX"], key=lambda p: priors["MX"][p]) == "flast"
    assert abs(sum(priors["MX"].values()) - 1) < 0.01


def test_reports_never_contain_addresses_or_names(tmp_path: Path) -> None:
    result = evaluate_domain("acme.mx", "MX", _engine())
    write_outputs([result], tmp_path)
    everything = " ".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert "@" not in everything
    for secret in ("lmendez", "Laura", "Méndez", "pramirez", "Ramírez"):
        assert secret not in everything
