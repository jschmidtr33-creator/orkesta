"""Sitio web oficial: páginas de equipo/directivos, correos y teléfonos publicados."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from orkesta.engine.net.domains import normalize_host, registrable_domain
from orkesta.engine.normalize.text import fold
from orkesta.research.extract import (
    Document,
    Person,
    emails_in,
    parse_html,
    people_in,
    phones_in,
)
from orkesta.research.fetch import Fetcher

TEAM_WORDS = (
    "equipo", "nuestro equipo", "nosotros", "quienes somos", "quien somos", "acerca de",
    "about", "team", "leadership", "liderazgo", "directivos", "directiva", "junta directiva",
    "gobierno corporativo", "management", "our people", "nuestra gente", "organigrama",
    "alta direccion", "comite", "executive", "board", "directorio", "lideres", "staff",
    "presidencia", "la empresa", "company",
)  # fmt: skip
CONTACT_WORDS = ("contacto", "contactenos", "contactanos", "contact", "escribenos")
SKIP_WORDS = (
    "blog", "noticia", "news", "producto", "product", "tienda", "shop", "login", "ingresar",
    "politica", "privacy", "terminos", "cookies", "trabaja", "empleo", "careers", "vacante",
)  # fmt: skip
MAX_TEAM_PAGES = 4


@dataclass
class SiteResult:
    domain: str
    home_url: str = ""
    name: str = ""
    people: list[tuple[Person, str]] = field(default_factory=list)  # (persona, url)
    emails: dict[str, str] = field(default_factory=dict)  # correo → url
    phones: dict[str, str] = field(default_factory=dict)
    pages_read: int = 0
    blocked: bool = False


def _same_site(url: str, domain: str) -> bool:
    host = normalize_host(url)
    return bool(host) and registrable_domain(host or "") == domain


def _score(href: str, text: str, words: tuple[str, ...]) -> int:
    path = fold(urlsplit(href).path.replace("-", " ").replace("_", " ").replace("/", " "))
    label = fold(text)
    if any(w in path or w in label for w in SKIP_WORDS):
        return 0
    score = 0
    for i, word in enumerate(words):
        if word in label:
            score = max(score, 100 - i)
        elif word in path:
            score = max(score, 80 - i)
    return score


def candidate_pages(doc: Document, base_url: str, domain: str) -> tuple[list[str], list[str]]:
    team: dict[str, int] = {}
    contact: dict[str, int] = {}
    for href, text in doc.links:
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not _same_site(url, domain) or url.rstrip("/") == base_url.rstrip("/"):
            continue
        if url.lower().endswith((".pdf", ".jpg", ".png", ".zip", ".docx", ".xlsx")):
            continue
        t = _score(url, text, TEAM_WORDS)
        if t:
            team[url] = max(team.get(url, 0), t)
        c = _score(url, text, CONTACT_WORDS)
        if c:
            contact[url] = max(contact.get(url, 0), c)
    by_score = sorted(team, key=lambda u: (-team[u], len(u)))
    return by_score[:MAX_TEAM_PAGES], sorted(contact, key=lambda u: (-contact[u], len(u)))[:1]


def _collect(result: SiteResult, doc: Document, url: str) -> None:
    result.pages_read += 1
    for person in people_in(doc):
        result.people.append((person, url))
    for email in emails_in(doc, result.domain):
        result.emails.setdefault(email, url)
    for phone in phones_in(doc):
        result.phones.setdefault(phone, url)


def investigate(fetcher: Fetcher, domain: str, website: str = "") -> SiteResult:
    result = SiteResult(domain=domain)
    starts = [website] if website else []
    starts += [f"https://www.{domain}/", f"https://{domain}/"]
    home = None
    for url in dict.fromkeys(starts):
        home = fetcher.get(url, respect_robots=True)
        if home is not None and home.ok and "html" in (home.content_type or "text/html"):
            break
        home = None
    if home is None:
        return result
    result.home_url = home.final_url
    doc = parse_html(home.text)
    result.name = doc.site_name or doc.title.split("|")[0].split(" - ")[0].strip()
    _collect(result, doc, home.final_url)
    team, contact = candidate_pages(doc, home.final_url, domain)
    visited = {home.final_url.rstrip("/")}
    for url in team + contact:
        if url.rstrip("/") in visited:
            continue
        visited.add(url.rstrip("/"))
        if not fetcher.allowed(url):
            result.blocked = True
            continue
        page = fetcher.get(url, respect_robots=True)
        if page is None or not page.ok or "html" not in (page.content_type or "text/html"):
            continue
        sub = parse_html(page.text)
        _collect(result, sub, page.final_url)
        # Un nivel más: «Nosotros» suele enlazar a «Equipo directivo».
        if url in team and len(visited) < MAX_TEAM_PAGES + 3:
            deeper, _ = candidate_pages(sub, page.final_url, domain)
            for extra in deeper[:2]:
                if extra.rstrip("/") in visited:
                    continue
                visited.add(extra.rstrip("/"))
                extra_page = fetcher.get(extra, respect_robots=True)
                if extra_page is not None and extra_page.ok:
                    _collect(result, parse_html(extra_page.text), extra_page.final_url)
    return result
