"""Recorrido de un sitio corporativo en busca de personas publicadas (equipo, directivos, prensa).

Visita la portada y las páginas internas con más probabilidad de listar personas, respetando
robots.txt y los límites de ``SafeFetcher``. No guarda las páginas: solo nombre, cargo y la URL
donde se publicó cada persona.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orkesta.engine.email.harvest import _rank_links, _same_brand, page_links
from orkesta.engine.net.domains import registrable_domain
from orkesta.engine.net.fetch import RobotsAwareFetcher
from orkesta.engine.normalize.text import fold
from orkesta.engine.people.extract import FoundPerson, extract_people

MAX_PAGES = 8

PEOPLE_HINTS: tuple[tuple[str, int], ...] = (
    ("equipo", 10), ("team", 10), ("nuestro-equipo", 10), ("our-team", 10), ("liderazgo", 10),
    ("leadership", 10), ("directorio", 9), ("directivos", 10), ("management", 9),
    ("gobierno-corporativo", 9), ("corporate-governance", 9), ("consejo", 8), ("board", 8),
    ("junta-directiva", 9), ("alta-direccion", 10), ("quienes-somos", 7), ("nosotros", 7),
    ("about", 6), ("acerca", 6), ("organigrama", 9), ("ejecutivos", 9), ("executives", 9),
    ("prensa", 5), ("press", 5), ("noticias", 4), ("news", 4), ("sala-de-prensa", 5),
    ("nombramiento", 8), ("appointment", 8), ("inversionistas", 4), ("investors", 4),
)  # fmt: skip


@dataclass
class PeopleHarvest:
    domain: str
    people: list[FoundPerson] = field(default_factory=list)
    pages_visited: list[str] = field(default_factory=list)
    blocked: bool = False


def discover_people(
    domain: str, fetcher: RobotsAwareFetcher, max_pages: int = MAX_PAGES
) -> PeopleHarvest:
    result = PeopleHarvest(domain)
    queue = [f"https://{domain}/", f"https://www.{domain}/"]
    seen: set[str] = set()
    names: set[str] = set()
    while queue and len(result.pages_visited) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not fetcher.allowed_by_robots(url):
            result.blocked = True
            continue
        page = fetcher.get(url)
        if not page.ok or not page.text:
            continue
        final_root = registrable_domain(page.final_url)
        if final_root is not None and not _same_brand(final_root, domain):
            continue  # redirigió fuera de la empresa
        result.pages_visited.append(page.final_url)
        seen.add(page.final_url)
        for person in extract_people(page.text, page.final_url):
            key = fold(person.full_name)
            if key not in names:
                names.add(key)
                result.people.append(person)
        if len(result.pages_visited) == 1:
            homes = {f"https://{domain}/", f"https://www.{domain}/"}
            ranked = _rank_links(page_links(page.text), page.final_url, domain, PEOPLE_HINTS)
            queue = [u for u in ranked if u not in seen and u not in homes]
    return result
