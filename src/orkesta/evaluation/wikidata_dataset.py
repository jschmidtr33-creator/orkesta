"""Conjunto de evaluación y entrenamiento construido automáticamente desde Wikidata.

Empresas (no personas) con país y sitio web oficial (P856). Sustituye la revisión humana: la
etiqueta es el sitio que Wikidata registra. Para evitar fuga de información, el motor se
evalúa con estas etiquetas **sin** consultar Wikidata.

Uso: ``python -m orkesta.evaluation.wikidata_dataset --out data.csv --per-country MX=300,CO=150``
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from orkesta.engine.net.domains import is_non_corporate, registrable_domain
from orkesta.engine.net.fetch import USER_AGENT
from orkesta.engine.normalize.location import COUNTRIES

SPARQL = "https://query.wikidata.org/sparql"
COUNTRY_ENTITY = {
    "MX": "Q96", "CO": "Q739", "PE": "Q419", "CL": "Q298", "AR": "Q414", "EC": "Q736",
    "UY": "Q77", "PA": "Q804", "CR": "Q800", "GT": "Q774", "DO": "Q786", "ES": "Q29",
}  # fmt: skip
# Clases de empresa: negocio, empresa, compañía, empresa cotizada, corporación, empresa
# privada, holding.
BUSINESS_TYPES = "wd:Q4830453 wd:Q6881511 wd:Q783794 wd:Q891723 wd:Q167037 wd:Q1589009 wd:Q219577"

QUERY = """
SELECT ?item ?itemLabel ?website ?cityLabel WHERE {{
  VALUES ?type {{ {types} }}
  ?item wdt:P31 ?type ; wdt:P17 wd:{country} ; wdt:P856 ?website .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?dissolved }}
  OPTIONAL {{ ?item wdt:P159 ?city . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
LIMIT {limit}
"""


@dataclass(frozen=True)
class WikidataCompany:
    qid: str
    name: str
    country: str
    city: str | None
    domains: tuple[str, ...]

    @property
    def location(self) -> str:
        country_name = COUNTRIES[self.country][0] if self.country in COUNTRIES else self.country
        return f"{self.city}, {country_name}" if self.city else country_name


def parse_bindings(bindings: list[dict[str, Any]], country: str) -> list[WikidataCompany]:
    names: dict[str, str] = {}
    cities: dict[str, str] = {}
    domains: dict[str, set[str]] = defaultdict(set)
    for row in bindings:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        label = row.get("itemLabel", {}).get("value", "")
        # Sin etiqueta legible Wikidata devuelve el propio identificador: no sirve.
        if not label or label == qid:
            continue
        names[qid] = label
        city = row.get("cityLabel", {}).get("value")
        if city and not city.startswith("Q"):
            cities.setdefault(qid, city)
        domain = registrable_domain(row.get("website", {}).get("value", ""))
        if domain and not is_non_corporate(domain):
            domains[qid].add(domain)
    return [
        WikidataCompany(qid, names[qid], country, cities.get(qid), tuple(sorted(domains[qid])))
        for qid in names
        if domains.get(qid)
    ]


def fetch_country(
    country: str, limit: int, client: httpx.Client, retries: int = 3
) -> list[WikidataCompany]:
    query = QUERY.format(types=BUSINESS_TYPES, country=COUNTRY_ENTITY[country], limit=limit)
    for attempt in range(retries):
        try:
            response = client.get(SPARQL, params={"query": query, "format": "json"})
            response.raise_for_status()
            return parse_bindings(response.json()["results"]["bindings"], country)
        except (httpx.HTTPError, ValueError, KeyError):
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return []


def sample(
    companies: list[WikidataCompany], n: int, seed: str = "orkesta"
) -> list[WikidataCompany]:
    """Muestra determinista: el mismo conjunto en cada ejecución mientras Wikidata no cambie."""
    ranked = sorted(companies, key=lambda c: hashlib.sha256(f"{seed}:{c.qid}".encode()).hexdigest())
    return ranked[:n]


def write_csv(companies: list[WikidataCompany], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["empresa", "ubicacion", "dominio_esperado", "fuente"])
        for c in companies:
            writer.writerow([c.name, c.location, "|".join(c.domains), f"wikidata:{c.qid}"])


def parse_quotas(text: str) -> dict[str, int]:
    quotas = {}
    for part in text.split(","):
        code, _, number = part.partition("=")
        if code.strip().upper() in COUNTRY_ENTITY:
            quotas[code.strip().upper()] = int(number or 100)
    return quotas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.evaluation.wikidata_dataset")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-country", default="MX=300,CO=120,PE=60,CL=60,AR=60")
    args = parser.parse_args(argv)

    quotas = parse_quotas(args.per_country)
    selected: list[WikidataCompany] = []
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT}) as client:
        for country, n in quotas.items():
            found = fetch_country(country, limit=max(n * 6, 600), client=client)
            chosen = sample(found, n)
            print(f"{country}: {len(found)} empresas con sitio oficial, {len(chosen)} elegidas")
            selected.extend(chosen)
            time.sleep(2)  # cortesía con el servicio público
    write_csv(selected, args.out)
    print(f"Total: {len(selected)} empresas → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
