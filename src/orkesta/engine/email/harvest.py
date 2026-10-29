"""Recolección de correos publicados en el sitio de la empresa.

Visita la portada y unas pocas páginas internas donde las empresas suelen publicar correos
(contacto, aviso de privacidad, equipo, prensa, proveedores…), respetando robots.txt y los
límites de ``SafeFetcher``. Devuelve los correos del dominio con el texto que los rodea, para
poder asociarlos a un nombre. No guarda las páginas.
"""

from __future__ import annotations

import contextlib
import html as html_lib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from orkesta.engine.net.domains import registrable_domain
from orkesta.engine.net.fetch import RobotsAwareFetcher
from orkesta.engine.normalize.text import fold

MAX_PAGES = 7
CONTEXT_CHARS = 160

# Palabras en la ruta o el texto del enlace que indican páginas con correos, por prioridad.
PAGE_HINTS: tuple[tuple[str, int], ...] = (
    ("contacto", 10), ("contact", 10), ("contactanos", 10), ("aviso", 8), ("privacidad", 8),
    ("privacy", 8), ("datos-personales", 8), ("proteccion-de-datos", 8), ("habeas", 8),
    ("equipo", 7), ("team", 7), ("directorio", 7), ("nosotros", 6), ("about", 6),
    ("quienes-somos", 6), ("prensa", 6), ("press", 6), ("sala-de-prensa", 6), ("media", 4),
    ("proveedores", 5), ("suppliers", 5), ("inversionistas", 5), ("investor", 5),
    ("transparencia", 5), ("legal", 4), ("terminos", 3), ("sucursales", 3), ("oficinas", 3),
)  # fmt: skip

_EMAIL = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63})@([a-z0-9-]+(?:\.[a-z0-9-]+)+)")
# Ofuscaciones habituales: "juan [arroba] empresa [punto] com", "juan(at)empresa.com".
_OBFUSCATED_AT = re.compile(r"\s*(?:\[|\(|\{)\s*(?:at|arroba)\s*(?:\]|\)|\})\s*|\s+arroba\s+")
_OBFUSCATED_DOT = re.compile(r"\s*(?:\[|\(|\{)\s*(?:dot|punto)\s*(?:\]|\)|\})\s*")
_ASSET_SUFFIX = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$")


@dataclass(frozen=True)
class FoundEmail:
    local: str
    domain: str
    context: str  # texto alrededor (sin plegar) para buscar el nombre de la persona
    url: str

    @property
    def address(self) -> str:
        return f"{self.local}@{self.domain}"


@dataclass
class Harvest:
    domain: str
    emails: list[FoundEmail] = field(default_factory=list)
    pages_visited: list[str] = field(default_factory=list)
    blocked: bool = False

    @property
    def email_domains(self) -> dict[str, int]:
        """Dominios registrables que aparecen en los correos y cuántas direcciones distintas."""
        counts: dict[str, set[str]] = {}
        for item in self.emails:
            root = registrable_domain(item.domain) or item.domain
            counts.setdefault(root, set()).add(item.local)
        return {d: len(v) for d, v in sorted(counts.items(), key=lambda kv: -len(kv[1]))}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith("mailto:"):
                # El mailto cuenta como texto: así entra con su contexto.
                self.parts.append(" " + href[7:].split("?", 1)[0] + " ")
            else:
                self._href, self._anchor = href, []
        elif tag in ("br", "p", "div", "li", "tr", "td", "h1", "h2", "h3", "h4", "section"):
            self.parts.append(" \n ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor)))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data)


def deobfuscate(text: str) -> str:
    text = _OBFUSCATED_AT.sub("@", text)
    return _OBFUSCATED_DOT.sub(".", text)


def page_links(page_html: str) -> list[tuple[str, str]]:
    """Enlaces (href, texto) de una página, sin filtrar."""
    parser = _PageParser()
    with contextlib.suppress(Exception):
        parser.feed(page_html[:1_500_000])
    return parser.links


def extract_emails(page_html: str, url: str, domain: str) -> tuple[list[FoundEmail], list[str]]:
    """Correos del dominio (o sus subdominios) y enlaces internos candidatos de la página."""
    parser = _PageParser()
    # HTML roto: se usa lo que se alcanzó a leer.
    with contextlib.suppress(Exception):
        parser.feed(page_html[:1_500_000])
    text = deobfuscate(html_lib.unescape(" ".join(parser.parts)))
    lowered = text.lower()
    found: list[FoundEmail] = []
    for match in _EMAIL.finditer(lowered):
        local, host = match.group(1).strip("."), match.group(2).strip(".")
        if _ASSET_SUFFIX.search(host) or not local:
            continue
        root = registrable_domain(host)
        if root is None or not _same_brand(root, domain):
            continue
        start, end = max(0, match.start() - CONTEXT_CHARS), match.end() + CONTEXT_CHARS
        found.append(FoundEmail(local, host, " ".join(text[start:end].split()), url))
    return found, _rank_links(parser.links, url, domain)


def _same_brand(root: str, domain: str) -> bool:
    """El correo pertenece a la empresa: mismo dominio o misma marca con otra extensión."""
    return root == domain or root.split(".", 1)[0] == domain.split(".", 1)[0]


def _rank_links(
    links: list[tuple[str, str]],
    base: str,
    domain: str,
    hints: tuple[tuple[str, int], ...] = PAGE_HINTS,
) -> list[str]:
    scored: dict[str, int] = {}
    for href, anchor in links:
        absolute = urljoin(base, href.strip()).split("#", 1)[0]
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or registrable_domain(parts.netloc) != domain:
            continue
        haystack = fold(f"{parts.path} {anchor}").replace(" ", "-")
        score = max((weight for hint, weight in hints if hint in haystack), default=0)
        if score:
            scored[absolute] = max(score, scored.get(absolute, 0))
    return sorted(scored, key=lambda u: (-scored[u], len(u)))


def harvest(domain: str, fetcher: RobotsAwareFetcher, max_pages: int = MAX_PAGES) -> Harvest:
    result = Harvest(domain)
    queue = [f"https://{domain}/", f"https://www.{domain}/"]
    seen: set[str] = set()
    seen_addresses: set[str] = set()
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
        emails, links = extract_emails(page.text, page.final_url, domain)
        for item in emails:
            if item.address not in seen_addresses:
                seen_addresses.add(item.address)
                result.emails.append(item)
        if len(result.pages_visited) == 1:
            # Las páginas internas se descubren desde la portada.
            homes = {f"https://{domain}/", f"https://www.{domain}/"}
            queue = [u for u in links if u not in seen and u not in homes]
    return result
