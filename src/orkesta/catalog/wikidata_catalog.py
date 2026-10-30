"""Catálogo de empresas LATAM desde Wikidata (datos públicos CC0): base de los filtros de empresa.

Por país y tipo de empresa: nombre, sede, sitio oficial (P856), industria (P452) y número de
empleados (P1128). La industria se clasifica en la taxonomía de ORKESTA y los empleados en los
rangos de Sales Navigator. Solo empresas: nunca personas.

Uso: ``python -m orkesta.catalog.wikidata_catalog --out catalogo.csv.gz --countries MX,CO,CL``
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from orkesta.catalog.industries import classify_all, size_band
from orkesta.engine.net.domains import is_non_corporate, registrable_domain
from orkesta.engine.net.fetch import USER_AGENT
from orkesta.evaluation.wikidata_dataset import (
    BUSINESS_TYPES,
    COUNTRY_ENTITY,
    PUBLIC_SECTOR,
    _query,
)

QUERY = """
SELECT ?item ?itemLabel ?website ?cityLabel ?industryLabel ?employees WHERE {{
  ?item wdt:P31 {type} ; wdt:P17 wd:{country} .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?dissolved }}
  OPTIONAL {{ ?item wdt:P856 ?website . }}
  OPTIONAL {{ ?item wdt:P159 ?city . }}
  OPTIONAL {{ ?item wdt:P452 ?industry . }}
  OPTIONAL {{ ?item wdt:P1128 ?employees . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
LIMIT {limit}
"""
# Consultas complementarias: el tipo "negocio" es enorme y Wikidata corta por tiempo. Estas
# capturan empresas tipadas con clases específicas (banco, aerolínea, cadena de supermercados…)
# a través de lo que solo tienen las empresas: industria declarada con sitio oficial, o un CEO.
EXTRA_QUERIES = (
    """
SELECT ?item ?itemLabel ?website ?cityLabel ?industryLabel ?employees WHERE {{
  ?item wdt:P17 wd:{country} ; wdt:P452 ?industry ; wdt:P856 ?website .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?dissolved }}
  OPTIONAL {{ ?item wdt:P159 ?city . }}
  OPTIONAL {{ ?item wdt:P1128 ?employees . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
LIMIT {limit}
""",
    """
SELECT ?item ?itemLabel ?website ?cityLabel ?industryLabel ?employees WHERE {{
  ?item wdt:P17 wd:{country} ; wdt:P169 ?ceo .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?dissolved }}
  OPTIONAL {{ ?item wdt:P856 ?website . }}
  OPTIONAL {{ ?item wdt:P159 ?city . }}
  OPTIONAL {{ ?item wdt:P452 ?industry . }}
  OPTIONAL {{ ?item wdt:P1128 ?employees . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
LIMIT {limit}
""",
)
COLUMNS = ("qid", "name", "country", "city", "domain", "industry_code", "industry_labels",
           "employees", "size_band")  # fmt: skip


@dataclass
class CatalogCompany:
    qid: str
    name: str
    country: str
    city: str | None = None
    domains: set[str] = field(default_factory=set)
    industries: set[str] = field(default_factory=set)
    employees: int | None = None

    def row(self) -> dict[str, str]:
        labels = sorted(self.industries)
        codes = classify_all(labels)
        return {
            "qid": self.qid,
            "name": self.name,
            "country": self.country,
            "city": self.city or "",
            "domain": sorted(self.domains)[0] if self.domains else "",
            "industry_code": codes[0] if codes else "",
            "industry_labels": "|".join(labels)[:400],
            "employees": str(self.employees or ""),
            "size_band": size_band(self.employees) or "",
        }


def merge_bindings(
    companies: dict[str, CatalogCompany], bindings: list[dict[str, Any]], country: str
) -> None:
    for row in bindings:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        label = row.get("itemLabel", {}).get("value", "")
        if not label or label == qid or (label.startswith("Q") and label[1:].isdigit()):
            continue
        company = companies.setdefault(qid, CatalogCompany(qid, label, country))
        city = row.get("cityLabel", {}).get("value")
        if city and not (city.startswith("Q") and city[1:].isdigit()):
            company.city = company.city or city
        domain = registrable_domain(row.get("website", {}).get("value", ""))
        if domain and not is_non_corporate(domain) and not PUBLIC_SECTOR.search(domain):
            company.domains.add(domain)
        industry = row.get("industryLabel", {}).get("value")
        if industry and not (industry.startswith("Q") and industry[1:].isdigit()):
            company.industries.add(industry)
        employees = row.get("employees", {}).get("value")
        if employees:
            try:
                value = int(float(employees))
            except ValueError:
                value = 0
            if value > 0:
                company.employees = max(company.employees or 0, value)


def fetch_country(country: str, client: httpx.Client, limit: int = 4000) -> list[CatalogCompany]:
    companies: dict[str, CatalogCompany] = {}
    for business_type in BUSINESS_TYPES.split():
        query = QUERY.format(type=business_type, country=COUNTRY_ENTITY[country], limit=limit)
        try:
            merge_bindings(companies, _query(client, query), country)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"{country} {business_type}: {exc!r}", file=sys.stderr)
        time.sleep(1)  # cortesía con el servicio público
    for extra in EXTRA_QUERIES:
        query = extra.format(country=COUNTRY_ENTITY[country], limit=limit * 2)
        try:
            merge_bindings(companies, _query(client, query), country)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"{country} consulta complementaria: {exc!r}", file=sys.stderr)
        time.sleep(1)
    return list(companies.values())


def write_catalog(companies: list[CatalogCompany], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for company in sorted(companies, key=lambda c: (c.country, c.name)):
            writer.writerow(company.row())


def read_catalog(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.catalog.wikidata_catalog")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--countries", default=",".join(COUNTRY_ENTITY))
    args = parser.parse_args(argv)
    countries = [
        c.strip().upper() for c in args.countries.split(",") if c.strip().upper() in COUNTRY_ENTITY
    ]
    everything: list[CatalogCompany] = []
    counts: dict[str, int] = defaultdict(int)
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT}) as client:
        for country in countries:
            found = fetch_country(country, client)
            everything.extend(found)
            counts[country] = len(found)
            with_domain = sum(1 for c in found if c.domains)
            print(f"{country}: {len(found)} empresas, {with_domain} con sitio oficial")
    if not everything:
        print("Wikidata no devolvió empresas", file=sys.stderr)
        return 1
    write_catalog(everything, args.out)
    print(f"Total: {len(everything)} empresas → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
