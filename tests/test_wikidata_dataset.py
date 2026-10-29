"""Conjunto automático desde Wikidata (sin red: respuestas SPARQL simuladas)."""

from __future__ import annotations

import csv
from pathlib import Path

import httpx

from orkesta.evaluation.wikidata_dataset import (
    fetch_country,
    parse_bindings,
    parse_quotas,
    sample,
    write_csv,
)


def _row(qid: str, label: str, website: str, city: str | None = None) -> dict[str, dict[str, str]]:
    row = {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "itemLabel": {"value": label},
        "website": {"value": website},
    }
    if city:
        row["cityLabel"] = {"value": city}
    return row


BINDINGS = [
    _row("Q1", "Lácteos Inventados", "https://www.lacteos.com.mx/", "Monterrey"),
    _row("Q1", "Lácteos Inventados", "http://lacteos-inventados.com"),
    _row("Q2", "Q2", "https://sinetiqueta.mx"),  # sin etiqueta legible
    _row("Q3", "Solo Redes", "https://www.facebook.com/soloredes"),  # sitio no corporativo
    _row("Q4", "Ciudad Rara", "https://rara.mx", "Q12345"),
]


def test_parse_bindings_groups_domains_and_filters_noise() -> None:
    companies = {c.qid: c for c in parse_bindings(BINDINGS, "MX")}
    assert set(companies) == {"Q1", "Q4"}
    assert companies["Q1"].domains == ("lacteos-inventados.com", "lacteos.com.mx")
    assert companies["Q1"].location == "Monterrey, México"
    assert companies["Q4"].location == "México"


def test_sample_is_deterministic() -> None:
    companies = parse_bindings(BINDINGS, "MX")
    assert sample(companies, 1) == sample(list(reversed(companies)), 1)


def test_quotas() -> None:
    assert parse_quotas("MX=300, co=50,XX=9") == {"MX": 300, "CO": 50}


def test_fetch_country_and_write_csv(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "wd:Q96" in request.url.params["query"]
        return httpx.Response(200, json={"results": {"bindings": BINDINGS}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        companies = fetch_country("MX", 100, client)
    out = tmp_path / "wd.csv"
    write_csv(companies, out)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert {r["fuente"] for r in rows} == {"wikidata:Q1", "wikidata:Q4"}
    assert any(r["dominio_esperado"] == "lacteos-inventados.com|lacteos.com.mx" for r in rows)
