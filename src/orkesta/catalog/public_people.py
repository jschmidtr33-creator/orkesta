"""Directivos públicos desde Wikidata (CC0): decisores reales disponibles desde el primer día.

Por país y por cargo vigente en la empresa: CEO (P169), presidente del directorio (P488),
director general (P1037) y fundador vivo (P112). Solo cargos sin fecha de término, personas
vivas y empresas no disueltas. Se guarda únicamente nombre, cargo, empresa y país: datos que la
propia empresa hace públicos.

Uso: ``python -m orkesta.catalog.public_people --out directivos.csv.gz``
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from orkesta.engine.net.domains import is_non_corporate, registrable_domain
from orkesta.engine.net.fetch import USER_AGENT
from orkesta.evaluation.wikidata_dataset import COUNTRY_ENTITY, PUBLIC_SECTOR, _query

ROLES: dict[str, str] = {
    "P169": "CEO",
    "P488": "Presidente del directorio",
    "P1037": "Director general",
    "P112": "Fundador",
}
QUERY = """
SELECT ?company ?companyLabel ?website ?person ?personLabel WHERE {{
  ?company wdt:P17 wd:{country} ; p:{prop} ?statement .
  ?statement ps:{prop} ?person .
  ?person wdt:P31 wd:Q5 .
  FILTER NOT EXISTS {{ ?statement pq:P582 ?end }}
  FILTER NOT EXISTS {{ ?person wdt:P570 ?death }}
  FILTER NOT EXISTS {{ ?company wdt:P576 ?dissolved }}
  OPTIONAL {{ ?company wdt:P856 ?website . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
LIMIT {limit}
"""
COLUMNS = ("person_qid", "name", "title", "company_qid", "company", "country", "domain")


@dataclass(frozen=True)
class PublicPerson:
    person_qid: str
    name: str
    title: str
    company_qid: str
    company: str
    country: str
    domain: str

    def row(self) -> dict[str, str]:
        return {
            "person_qid": self.person_qid,
            "name": self.name,
            "title": self.title,
            "company_qid": self.company_qid,
            "company": self.company,
            "country": self.country,
            "domain": self.domain,
        }


def _is_qid(value: str) -> bool:
    return value.startswith("Q") and value[1:].isdigit()


def parse_bindings(bindings: list[dict[str, Any]], title: str, country: str) -> list[PublicPerson]:
    found: dict[tuple[str, str], PublicPerson] = {}
    for row in bindings:
        person_qid = row["person"]["value"].rsplit("/", 1)[-1]
        company_qid = row["company"]["value"].rsplit("/", 1)[-1]
        name = row.get("personLabel", {}).get("value", "").strip()
        company = row.get("companyLabel", {}).get("value", "").strip()
        # Sin etiqueta legible (solo el QID) no sirve: ni nombre ni empresa reconocibles.
        if not name or not company or _is_qid(name) or _is_qid(company) or " " not in name:
            continue
        domain = registrable_domain(row.get("website", {}).get("value", "")) or ""
        if domain and (is_non_corporate(domain) or PUBLIC_SECTOR.search(domain)):
            domain = ""
        key = (person_qid, company_qid)
        previous = found.get(key)
        if previous is None or (domain and not previous.domain):
            found[key] = PublicPerson(
                person_qid, name, title, company_qid, company, country, domain
            )
    return list(found.values())


def fetch_country(country: str, client: httpx.Client, limit: int = 5000) -> list[PublicPerson]:
    people: list[PublicPerson] = []
    for prop, title in ROLES.items():
        query = QUERY.format(country=COUNTRY_ENTITY[country], prop=prop, limit=limit)
        try:
            people.extend(parse_bindings(_query(client, query), title, country))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"{country} {prop}: {exc!r}", file=sys.stderr)
        time.sleep(1)  # cortesía con el servicio público
    return people


def write_people(people: list[PublicPerson], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for person in sorted(people, key=lambda p: (p.country, p.company, p.name)):
            writer.writerow(person.row())


def read_people(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.catalog.public_people")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--countries", default=",".join(COUNTRY_ENTITY))
    args = parser.parse_args(argv)
    countries = [
        c.strip().upper() for c in args.countries.split(",") if c.strip().upper() in COUNTRY_ENTITY
    ]
    everything: list[PublicPerson] = []
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT}) as client:
        for country in countries:
            found = fetch_country(country, client)
            everything.extend(found)
            print(
                f"{country}: {len(found)} directivos, {sum(1 for p in found if p.domain)} con sitio"
            )
    if not everything:
        print("Wikidata no devolvió directivos", file=sys.stderr)
        return 1
    write_people(everything, args.out)
    print(f"Total: {len(everything)} directivos → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
