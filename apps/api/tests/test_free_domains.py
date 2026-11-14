"""Dominio oficial sin buscador pago: directorio propio y Wikidata (datos sintéticos)."""

from __future__ import annotations

from typing import Any

import pytest

from orkesta.research import domain_guess, wikidata


class _Fetcher:
    factory: Any = None


def test_wikidata_gives_the_official_domain_only_for_the_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(domain_guess, "from_directory", lambda factory, country, name: "")
    monkeypatch.setattr(wikidata, "search_company", lambda fetcher, name: ["Q1", "Q2"])
    monkeypatch.setattr(wikidata, "companies_by_qid", lambda fetcher, qids: [
        wikidata.WikiCompany(qid="Q2", name="Lácteos Andinos Holding", website="", domain="otra.com"),
        wikidata.WikiCompany(qid="Q1", name="Lácteos Andinos S.A.S.", website="",
                             domain="lacteosandinos.com.co"),
    ])  # fmt: skip
    assert domain_guess.free_domain(_Fetcher(), "Lacteos Andinos SAS", "CO") == (  # type: ignore[arg-type]
        "lacteosandinos.com.co",
        "wikidata",
    )
    assert wikidata.company_domain(_Fetcher(), "Frutos del Sur", "CO") == ""  # type: ignore[arg-type]


def test_own_directory_comes_before_wikidata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(domain_guess, "from_directory", lambda factory, country, name: "propio.co")
    monkeypatch.setattr(wikidata, "search_company", lambda *a: pytest.fail("no debía consultar"))
    assert domain_guess.free_domain(_Fetcher(), "Propio", "CO") == ("propio.co", "directorio")  # type: ignore[arg-type]
