"""Wikidata: catálogo de empresas por país y directivos vigentes (CEO, presidente, junta).

Datos abiertos (CC0), sin clave de API. Se consulta el servicio SPARQL público con un
User-Agent identificable, y las respuestas quedan en caché.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.normalize.text import clean
from orkesta.research.fetch import Fetcher

SPARQL = "https://query.wikidata.org/sparql"
API = "https://www.wikidata.org/w/api.php"
ENTITY = "https://www.wikidata.org/wiki/"
CATALOG_TTL = timedelta(days=7)

COUNTRY_QIDS: dict[str, str] = {
    "CO": "Q739", "MX": "Q96", "PE": "Q419", "CL": "Q298", "AR": "Q414", "EC": "Q736",
    "VE": "Q717", "BO": "Q750", "PY": "Q733", "UY": "Q77", "BR": "Q155", "PA": "Q804",
    "CR": "Q800", "GT": "Q774", "HN": "Q783", "SV": "Q792", "NI": "Q811", "DO": "Q786",
    "PR": "Q1183", "CU": "Q241", "ES": "Q29", "US": "Q30", "CA": "Q16", "PT": "Q45",
    "GB": "Q145",
}  # fmt: skip

# Tipos de entidad que cuentan como empresa aunque no declaren industria.
COMPANY_TYPES = [
    "Q4830453",
    "Q891723",
    "Q6881511",
    "Q783794",
    "Q22687",
    "Q1058914",
    "Q161726",
    "Q1589009",
    "Q219577",
    "Q18388277",
]

# Propiedad → cargo visible.
ROLES: dict[str, str] = {
    "P169": "CEO",
    "P488": "Presidente de la junta directiva",
    "P1037": "Director/a",
    "P3320": "Miembro de la junta directiva",
    "P112": "Fundador/a",
}


@dataclass
class WikiCompany:
    qid: str
    name: str
    website: str
    domain: str
    employees: int | None = None
    city: str = ""
    description: str = ""
    industries: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    linkedin: str = ""

    @property
    def url(self) -> str:
        return ENTITY + self.qid


@dataclass
class WikiPerson:
    company_qid: str
    qid: str
    name: str
    role: str
    linkedin: str = ""

    @property
    def url(self) -> str:
        return ENTITY + self.qid


def _company_query(*, country_qid: str | None = None, qids: list[str] | None = None) -> str:
    if qids:
        scope = "VALUES ?c { " + " ".join(f"wd:{q}" for q in qids) + " }"
    else:
        scope = (
            f"{{ ?c wdt:P17 wd:{country_qid} }} UNION "
            f"{{ ?c wdt:P159 ?hq0 . ?hq0 wdt:P17 wd:{country_qid} }}"
        )
    types = " ".join(f"wd:{t}" for t in COMPANY_TYPES)
    return f"""
SELECT ?c (SAMPLE(?label) AS ?name) (SAMPLE(?site) AS ?website) (MAX(?emp) AS ?employees)
  (SAMPLE(?hqLabel) AS ?city) (SAMPLE(?desc) AS ?description) (SAMPLE(?li) AS ?linkedin)
  (GROUP_CONCAT(DISTINCT ?indLabel; separator="|") AS ?industries)
  (GROUP_CONCAT(DISTINCT ?typeLabel; separator="|") AS ?types)
WHERE {{
  {scope}
  ?c wdt:P856 ?site .
  OPTIONAL {{ ?c wdt:P452 ?ind . }}
  OPTIONAL {{ ?c wdt:P31 ?type . }}
  FILTER(BOUND(?ind) || ?type IN ({types.replace(" ", ", ")}))
  FILTER NOT EXISTS {{ ?c wdt:P576 ?dissolved . }}
  OPTIONAL {{ ?c wdt:P1128 ?emp . }}
  OPTIONAL {{ ?c wdt:P4264 ?li . }}
  OPTIONAL {{ ?c wdt:P159 ?hq . }}
  OPTIONAL {{ ?c schema:description ?desc . FILTER(LANG(?desc) = "es") }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "es,en" .
    ?c rdfs:label ?label . ?ind rdfs:label ?indLabel .
    ?type rdfs:label ?typeLabel . ?hq rdfs:label ?hqLabel .
  }}
}}
GROUP BY ?c
LIMIT 5000
"""


def _people_query(qids: list[str]) -> str:
    values = " ".join(f"wd:{q}" for q in qids)
    pairs = " ".join(f'(p:{p} ps:{p} "{p}")' for p in ROLES)
    return f"""
SELECT ?c ?p ?pLabel ?prop ?li WHERE {{
  VALUES ?c {{ {values} }}
  VALUES (?claim ?value ?prop) {{ {pairs} }}
  ?c ?claim ?st .
  ?st ?value ?p .
  ?st wikibase:rank ?rank . FILTER(?rank != wikibase:DeprecatedRank)
  FILTER NOT EXISTS {{ ?st pq:P582 ?end . }}
  ?p wdt:P31 wd:Q5 .
  FILTER NOT EXISTS {{ ?p wdt:P570 ?death . }}
  OPTIONAL {{ ?p wdt:P6634 ?li . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en" . }}
}}
LIMIT 2000
"""


def _value(row: dict[str, Any], key: str) -> str:
    cell = row.get(key)
    return str(cell.get("value", "")) if isinstance(cell, dict) else ""


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _sparql(fetcher: Fetcher, query: str, ttl: timedelta) -> list[dict[str, Any]]:
    url = f"{SPARQL}?{urlencode({'query': ' '.join(query.split()), 'format': 'json'})}"
    page = fetcher.get(url, ttl=ttl, accept="application/sparql-results+json", timeout=55)
    if page is None or not page.ok:
        raise RuntimeError("Wikidata no respondió")
    try:
        bindings = json.loads(page.text)["results"]["bindings"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Respuesta de Wikidata no válida") from exc
    return [b for b in bindings if isinstance(b, dict)]


def parse_companies(rows: list[dict[str, Any]]) -> list[WikiCompany]:
    companies: list[WikiCompany] = []
    for row in rows:
        website = _value(row, "website")
        host = normalize_host(website) if website else None
        domain = registrable_domain(host) if host else None
        name = clean(_value(row, "name"))
        qid = _qid(_value(row, "c"))
        if not domain or is_non_corporate(domain) or not name or name == qid:
            continue
        employees = None
        with contextlib.suppress(ValueError):
            employees = int(float(_value(row, "employees"))) or None
        linkedin = _value(row, "linkedin")
        companies.append(
            WikiCompany(
                qid=qid,
                name=name,
                website=website,
                domain=domain,
                employees=employees,
                city=clean(_value(row, "city")),
                description=clean(_value(row, "description")),
                industries=[clean(x) for x in _value(row, "industries").split("|") if x.strip()],
                types=[clean(x) for x in _value(row, "types").split("|") if x.strip()],
                linkedin=f"https://www.linkedin.com/company/{linkedin}" if linkedin else "",
            )
        )
    return companies


def companies_in_country(fetcher: Fetcher, country: str) -> list[WikiCompany]:
    qid = COUNTRY_QIDS.get(country)
    if qid is None:
        return []
    return parse_companies(_sparql(fetcher, _company_query(country_qid=qid), CATALOG_TTL))


def companies_by_qid(fetcher: Fetcher, qids: list[str]) -> list[WikiCompany]:
    if not qids:
        return []
    return parse_companies(_sparql(fetcher, _company_query(qids=qids), CATALOG_TTL))


def search_company(fetcher: Fetcher, name: str) -> list[str]:
    """Candidatos (QID) para un nombre de empresa escrito por el usuario."""
    params = {
        "action": "wbsearchentities",
        "search": name[:100],
        "language": "es",
        "uselang": "es",
        "type": "item",
        "limit": "5",
        "format": "json",
    }
    page = fetcher.get(f"{API}?{urlencode(params)}", accept="application/json")
    if page is None or not page.ok:
        return []
    try:
        results = json.loads(page.text).get("search", [])
    except ValueError:
        return []
    return [str(r["id"]) for r in results if isinstance(r, dict) and r.get("id")]


def parse_people(rows: list[dict[str, Any]]) -> list[WikiPerson]:
    people: list[WikiPerson] = []
    for row in rows:
        name = clean(_value(row, "pLabel"))
        qid = _qid(_value(row, "p"))
        if not name or name == qid:
            continue
        linkedin = _value(row, "li")
        people.append(
            WikiPerson(
                company_qid=_qid(_value(row, "c")),
                qid=qid,
                name=name,
                role=ROLES.get(_value(row, "prop"), ""),
                linkedin=f"https://www.linkedin.com/in/{linkedin}" if linkedin else "",
            )
        )
    return people


def people_of(fetcher: Fetcher, qids: list[str]) -> list[WikiPerson]:
    people: list[WikiPerson] = []
    for start in range(0, len(qids), 60):
        chunk = qids[start : start + 60]
        people += parse_people(_sparql(fetcher, _people_query(chunk), timedelta(days=3)))
    return people
