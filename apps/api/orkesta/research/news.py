"""Noticias públicas (GDELT DOC 2.0): nombramientos recientes de directivos.

GDELT indexa noticias de todo el mundo y es gratuito, sin clave. Su API pide como máximo una
consulta cada 5 segundos, así que se agrupan varias empresas por consulta y se respeta la pausa
de forma global.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from orkesta.engine.normalize.text import fold
from orkesta.research.extract import looks_like_person, make_person
from orkesta.research.fetch import Fetcher

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL = 5.5
ROLE_WORDS = (
    "presidente OR presidenta OR CEO OR gerente OR director OR directora OR "
    "vicepresidente OR nombra OR designa OR nombramiento"
)
# Titulares que hablan de alguien que se va, ya se fue o falleció: no sirven como decisor actual.
LEAVING = re.compile(
    r"\b(renuncia|renuncio|dimite|dimision|deja el cargo|deja la|sale de|salida de|"
    r"destituid|destituye|exgerente|expresidente|ex presidente|ex ceo|exdirector|fallece|"
    r"murio|muere|capturad|imputad|condenad|investigad)",
)
ROLE = re.compile(
    r"\b((?:nuev[oa] )?(?:presidente|presidenta|ceo|gerente general|director(?:a)? general|"
    r"director(?:a)? ejecutiv[oa]|vicepresident[ea](?: de [a-z ]{3,30}?)?|"
    r"director(?:a)? de [a-z ]{3,30}?|gerente de [a-z ]{3,30}?|cfo|cto|cmo|coo|cio|"
    r"country manager))(?= de | en | para |,|\.|$| y | tras | a partir)"
)
_CAPS = re.compile(
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+(?:\s+(?:de|del|la|los|y)?\s*[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+){1,3}"
)

_lock = threading.Lock()
_last_call = 0.0


@dataclass(frozen=True)
class NewsPerson:
    company_key: str
    full_name: str
    first_name: str
    last_name: str
    title: str
    url: str
    headline: str
    date: str
    outlet: str


def _wait() -> None:
    global _last_call
    with _lock:
        delay = _last_call + MIN_INTERVAL - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last_call = time.monotonic()


def _date(seen: str) -> str:
    # "20261012T143000Z" → "2026-10-12"
    return f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""


def people_in_headline(headline: str) -> list[tuple[str, str]]:
    """(nombre, cargo) mencionados en un titular de nombramiento."""
    folded = fold(headline)
    if LEAVING.search(folded):
        return []
    role = ROLE.search(folded + ".")
    if role is None:
        return []
    title = role.group(1).removeprefix("nuevo ").removeprefix("nueva ")
    title = title.upper() if len(title) <= 3 else title[:1].upper() + title[1:]
    found = []
    for match in _CAPS.finditer(headline):
        name = match.group(0)
        if looks_like_person(name):
            found.append((name, title))
    return found


def search(
    fetcher: Fetcher, companies: dict[str, str], *, sourcecountry: str = ""
) -> list[NewsPerson]:
    """``companies``: {clave de empresa: nombre visible}. Una consulta por grupo de 6."""
    results: list[NewsPerson] = []
    names = [(k, n) for k, n in companies.items() if len(n) >= 4 and '"' not in n]
    for start in range(0, len(names), 6):
        group = names[start : start + 6]
        phrase = " OR ".join(f'"{n}"' for _, n in group)
        query = f"({phrase}) ({ROLE_WORDS})" if len(group) > 1 else f"{phrase} ({ROLE_WORDS})"
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": "75",
            "sort": "datedesc",
            "timespan": "3months",
        }
        url = f"{DOC_API}?{urlencode(params)}"
        cached = fetcher._cached(url, timedelta(hours=12))
        if cached is None:
            _wait()
        page = fetcher.get(url, ttl=timedelta(hours=12), accept="application/json", timeout=25)
        if page is None or not page.ok:
            continue
        try:
            articles = json.loads(page.text).get("articles", [])
        except ValueError:
            continue  # GDELT responde texto plano cuando la consulta no le gusta
        for article in articles:
            if not isinstance(article, dict):
                continue
            headline = str(article.get("title", ""))
            folded = fold(headline)
            for key, name in group:
                if fold(name) not in folded:
                    continue
                for person_name, title in people_in_headline(headline):
                    person = make_person(person_name, title)
                    if person is None:
                        continue
                    results.append(
                        NewsPerson(
                            company_key=key,
                            full_name=person.full_name,
                            first_name=person.first_name,
                            last_name=person.last_name,
                            title=person.title,
                            url=str(article.get("url", "")),
                            headline=headline[:300],
                            date=_date(str(article.get("seendate", ""))),
                            outlet=str(article.get("domain", "")),
                        )
                    )
    return results
