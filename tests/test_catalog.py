"""Catálogo público de empresas: Wikidata → archivo → base → candidatos para explorar."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta import cli
from orkesta.catalog.companies import load_catalog
from orkesta.catalog.discovery import candidate_companies
from orkesta.catalog.prospects import records_from_file
from orkesta.catalog.service import ProspectFilters, upsert_prospects
from orkesta.catalog.wikidata_catalog import (
    CatalogCompany,
    merge_bindings,
    read_catalog,
    write_catalog,
)
from orkesta.db.models import CatalogCompany as CatalogRow
from orkesta.db.models import Organization


def _b(qid: str, label: str, **extra: str) -> dict[str, dict[str, str]]:
    row = {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "itemLabel": {"value": label},
    }
    for key, value in extra.items():
        row[key] = {"value": value}
    return row


def test_bindings_merge_industries_size_and_domain(tmp_path: Path) -> None:
    companies: dict[str, CatalogCompany] = {}
    merge_bindings(
        companies,
        [
            _b(
                "Q1",
                "Banco Ficticio",
                website="https://www.bancoficticio.com.co/",
                industryLabel="banca",
                employees="12000",
                cityLabel="Bogotá",
            ),
            _b("Q1", "Banco Ficticio", industryLabel="servicios financieros", employees="11500"),
            _b("Q2", "Q2"),  # sin etiqueta legible
            _b("Q3", "Ministerio X", website="https://www.minx.gov.co"),
        ],
        "CO",
    )
    rows = {c.qid: c.row() for c in companies.values()}
    assert set(rows) == {"Q1", "Q3"}
    bank = rows["Q1"]
    assert (bank["domain"], bank["industry_code"], bank["size_band"], bank["city"]) == (
        "bancoficticio.com.co",
        "banca_finanzas",
        "10001+",
        "Bogotá",
    )
    assert rows["Q3"]["domain"] == ""  # sector público fuera
    path = tmp_path / "c.csv.gz"
    write_catalog(list(companies.values()), path)
    assert [r["qid"] for r in read_catalog(path)] == ["Q1", "Q3"]


pytestmark_db = pytest.mark.db
ROWS = [
    {
        "qid": "Q1",
        "name": "Banco Ficticio",
        "country": "CO",
        "city": "Bogotá",
        "domain": "bancoficticio.com.co",
        "industry_code": "banca_finanzas",
        "industry_labels": "banca",
        "employees": "12000",
        "size_band": "10001+",
    },
    {
        "qid": "Q2",
        "name": "Retail Pequeño",
        "country": "CO",
        "city": "",
        "domain": "retailpeq.co",
        "industry_code": "retail_consumo",
        "industry_labels": "retail",
        "employees": "80",
        "size_band": "51-200",
    },
    {
        "qid": "Q3",
        "name": "Sin Sitio",
        "country": "CO",
        "city": "",
        "domain": "",
        "industry_code": "banca_finanzas",
        "industry_labels": "",
        "employees": "",
        "size_band": "",
    },
]


@pytestmark_db
def test_load_is_idempotent_and_candidates_combine_base_and_catalog(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        assert load_catalog(session, ROWS) == 3
        assert load_catalog(session, ROWS) == 3  # actualiza, no duplica
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        base = (
            b"email,first_name,last_name,company,country,company_industry\n"
            b"ana@miempresa.co,Ana,Lopez,Mi Empresa,Colombia,Banking\n"
        )
        upsert_prospects(session, org.id, records_from_file(base, "b.csv"), source="base")
        org_id = org.id
    with session_factory() as session:
        assert session.scalar(select(CatalogRow).where(CatalogRow.qid == "Q1")) is not None
        banks = candidate_companies(session, org_id, ProspectFilters(industries=["banca_finanzas"]))
        # Primero la empresa de la base; luego el catálogo (solo con sitio oficial).
        assert [c["email_domain"] for c in banks] == ["miempresa.co", "bancoficticio.com.co"]
        big = candidate_companies(session, org_id, ProspectFilters(sizes=["10001+"]))
        assert [c["email_domain"] for c in big] == ["bancoficticio.com.co"]
        retail = candidate_companies(
            session, org_id, ProspectFilters(countries=["CO"], industries=["retail_consumo"])
        )
        assert [c["company"] for c in retail] == ["Retail Pequeño"]


@pytestmark_db
def test_cli_catalog_load(
    tmp_path: Path, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    companies: dict[str, CatalogCompany] = {}
    merge_bindings(companies, [_b("Q9", "Empresa CLI", website="https://empresacli.mx")], "MX")
    path = tmp_path / "catalogo.csv.gz"
    write_catalog(list(companies.values()), path)
    assert cli.main(["catalog-load", str(tmp_path / "no-existe.csv.gz")]) == 0
    assert cli.main(["catalog-load", str(path)]) == 0
    with session_factory() as session:
        row = session.scalar(select(CatalogRow).where(CatalogRow.qid == "Q9"))
        assert row is not None and row.domain == "empresacli.mx"
        session.delete(row)
        session.commit()
