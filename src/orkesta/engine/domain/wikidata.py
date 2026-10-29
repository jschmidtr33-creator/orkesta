"""Adaptador de Wikidata: sitio web oficial (P856) y país (P17) de una empresa.

API pública y gratuita. Se respeta su política de uso: agente identificable, pocas peticiones
por empresa (una búsqueda y una consulta de entidades) y tiempos de espera cortos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from orkesta.engine.net.domains import is_non_corporate, registrable_domain
from orkesta.engine.net.fetch import USER_AGENT

API = "https://www.wikidata.org/w/api.php"

# Entidad de país en Wikidata → código ISO.
COUNTRY_QIDS: dict[str, str] = {
    "Q96": "MX", "Q739": "CO", "Q419": "PE", "Q298": "CL", "Q414": "AR", "Q736": "EC",
    "Q717": "VE", "Q750": "BO", "Q733": "PY", "Q77": "UY", "Q155": "BR", "Q804": "PA",
    "Q800": "CR", "Q774": "GT", "Q783": "HN", "Q792": "SV", "Q811": "NI", "Q786": "DO",
    "Q1183": "PR", "Q241": "CU", "Q29": "ES", "Q30": "US", "Q16": "CA", "Q45": "PT",
    "Q145": "GB", "Q183": "DE", "Q142": "FR", "Q17": "JP", "Q148": "CN", "Q884": "KR",
    "Q38": "IT", "Q39": "CH", "Q55": "NL", "Q34": "SE",
}  # fmt: skip


@dataclass(frozen=True)
class WikidataHit:
    qid: str
    label: str
    domain: str
    countries: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"https://www.wikidata.org/wiki/{self.qid}"


class WikidataError(Exception):
    """Wikidata no respondió o devolvió algo inesperado."""


class CompanySource(Protocol):
    def lookup(self, name: str) -> list[WikidataHit]: ...


class WikidataSource:
    def __init__(self, timeout: float = 8.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport
        )

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.get(API, params={**params, "format": "json"})
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WikidataError(f"Wikidata no disponible: {type(exc).__name__}") from exc
        return data

    def lookup(self, name: str) -> list[WikidataHit]:
        """Entidades cuyo nombre coincide y que declaran un sitio web oficial."""
        search = self._get(
            {
                "action": "wbsearchentities",
                "search": name,
                "language": "es",
                "uselang": "es",
                "type": "item",
                "limit": "5",
            }
        )
        ids = [item["id"] for item in search.get("search", []) if "id" in item]
        if not ids:
            return []
        entities = self._get(
            {"action": "wbgetentities", "ids": "|".join(ids), "props": "claims|labels"}
        ).get("entities", {})
        hits = []
        for qid in ids:
            entity = entities.get(qid, {})
            claims = entity.get("claims", {})
            countries = tuple(
                code
                for claim in claims.get("P17", [])
                if (code := COUNTRY_QIDS.get(_entity_id(claim) or ""))
            )
            labels = entity.get("labels", {})
            label = (labels.get("es") or labels.get("en") or {}).get("value", qid)
            for claim in claims.get("P856", []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                domain = registrable_domain(value) if isinstance(value, str) else None
                if domain and not is_non_corporate(domain):
                    hits.append(WikidataHit(qid, label, domain, countries))
        return hits


def _entity_id(claim: dict[str, Any]) -> str | None:
    value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
    return value.get("id") if isinstance(value, dict) else None
