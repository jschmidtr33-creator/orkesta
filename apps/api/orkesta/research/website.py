"""Sitio web oficial: páginas de equipo/directivos, correos y teléfonos publicados."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from orkesta.engine.net.domains import normalize_host, registrable_domain
from orkesta.engine.normalize.text import clean, fold
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
_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)
# Rutas típicas cuando el inicio no enlaza a la página de equipo (menús hechos con JavaScript).
COMMON_PATHS = (
    "/nosotros", "/quienes-somos", "/equipo", "/nuestro-equipo", "/gobierno-corporativo",
    "/junta-directiva", "/directivos", "/about", "/about-us", "/team", "/leadership",
    "/es/nosotros", "/en/about-us",
)  # fmt: skip
MAX_GUESSES = 6


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
    description: str = ""
    linkedin: str = ""
    employees: int | None = None


_LINKEDIN_CO = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/[A-Za-z0-9_\-%.]+",
                          re.IGNORECASE)  # fmt: skip


def _organization(result: SiteResult, doc: Document) -> None:
    """Datos de la empresa publicados por el propio sitio (JSON-LD, metadatos, enlaces)."""
    result.description = result.description or doc.description
    for href, _ in doc.links:
        match = _LINKEDIN_CO.match(href)
        if match and not result.linkedin:
            result.linkedin = match.group(0).split("?")[0].rstrip("/")[:300]
    stack: list[object] = list(doc.json_ld)
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        stack.extend(v for k, v in item.items() if k == "@graph")
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(str(k).endswith(("Organization", "Corporation", "LocalBusiness", "Company"))
                   for k in kinds):  # fmt: skip
            continue
        same = item.get("sameAs") or []
        for url in same if isinstance(same, list) else [same]:
            if isinstance(url, str) and _LINKEDIN_CO.match(url) and not result.linkedin:
                result.linkedin = url.split("?")[0].rstrip("/")[:300]
        staff = item.get("numberOfEmployees")
        if isinstance(staff, dict):
            staff = staff.get("value") or staff.get("maxValue") or staff.get("minValue")
        if isinstance(staff, int | str) and str(staff).isdigit() and result.employees is None:
            result.employees = int(staff)
        if isinstance(item.get("description"), str) and not result.description:
            result.description = clean(item["description"])[:400]
        email = item.get("email")
        if isinstance(email, str) and "@" in email:
            address = email.removeprefix("mailto:").strip().lower()
            if address.endswith("@" + result.domain):
                result.emails.setdefault(address, result.home_url)
        phone = item.get("telephone")
        if isinstance(phone, str):
            digits = re.sub(r"\D", "", phone)
            if 7 <= len(digits) <= 15:
                result.phones.setdefault(("+" if phone.strip().startswith("+") else "") + digits,
                                         result.home_url)  # fmt: skip


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
    _organization(result, doc)
    team, contact = candidate_pages(doc, home.final_url, domain)
    if len(team) < 2:
        team += [u for u in _from_sitemap(fetcher, home.final_url, domain) if u not in team]
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
    if not result.people:
        _guess_paths(fetcher, result, home.final_url, visited)
    return result


def _from_sitemap(fetcher: Fetcher, base_url: str, domain: str) -> list[str]:
    """URLs de equipo/directivos listadas en sitemap.xml (también las de sitemaps anidados)."""
    parts = urlsplit(base_url)
    root = f"{parts.scheme}://{parts.netloc}"
    found: list[str] = []
    queue = [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"]
    seen: set[str] = set()
    while queue and len(seen) < 4:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page = fetcher.get(url, accept="application/xml,text/xml;q=0.9,*/*;q=0.5")
        if page is None or not page.ok or "<loc>" not in page.text:
            continue
        for loc in _LOC.findall(page.text)[:5000]:
            loc = loc.strip()
            if not _same_site(loc, domain):
                continue
            if loc.endswith(".xml"):
                if any(w in loc for w in ("page", "pagina", "post")):
                    queue.append(loc)
                continue
            if _score(loc, "", TEAM_WORDS) >= 60 and loc not in found:
                found.append(loc)
    return sorted(found, key=lambda u: (-_score(u, "", TEAM_WORDS), len(u)))[:MAX_TEAM_PAGES]


def _guess_paths(fetcher: Fetcher, result: SiteResult, home_url: str, visited: set[str]) -> None:
    parts = urlsplit(home_url)
    root = f"{parts.scheme}://{parts.netloc}"
    tries = 0
    for path in COMMON_PATHS:
        url = root + path
        if url.rstrip("/") in visited or tries >= MAX_GUESSES:
            continue
        visited.add(url.rstrip("/"))
        tries += 1
        page = fetcher.get(url, respect_robots=True)
        if page is None or not page.ok or "html" not in (page.content_type or "text/html"):
            continue
        before = len(result.people)
        _collect(result, parse_html(page.text), page.final_url)
        if len(result.people) > before:
            break


def site_patterns(result: SiteResult) -> Counter[str]:
    """Formatos de correo que confirma el propio sitio: correos publicados que coinciden con el
    nombre de una persona del sitio."""
    from orkesta.research.extract import detect_pattern, is_generic_email

    personal = [e for e in result.emails if not is_generic_email(e)]
    votes: Counter[str] = Counter()
    seen: set[str] = set()
    for person, _ in result.people:
        for email in personal:
            pattern = detect_pattern(person.first_name, person.last_name, email)
            if pattern and email not in seen:
                votes[pattern] += 1
                seen.add(email)
    return votes


def learn_patterns(fetcher: Fetcher, result: SiteResult) -> str:
    """Guarda en la memoria de ORKESTA el formato confirmado por el sitio; lo devuelve."""
    from orkesta.research import memory

    votes = site_patterns(result)
    if not votes:
        return ""
    pattern, count = votes.most_common(1)[0]
    memory.remember_pattern(fetcher.factory, result.domain, pattern, count, result.home_url)
    return pattern


def read_pages(fetcher: Fetcher, result: SiteResult, urls: list[str]) -> int:
    """Lee páginas adicionales del sitio (por ejemplo, las que encontró el buscador)."""
    before = len(result.people)
    for url in urls:
        page = fetcher.get(url, respect_robots=True)
        if page is None or not page.ok or "html" not in (page.content_type or "text/html"):
            continue
        _collect(result, parse_html(page.text), page.final_url)
    return len(result.people) - before
