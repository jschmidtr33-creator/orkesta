"""Lectura de HTML y detección de personas, cargos, correos y teléfonos en texto público."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any

from orkesta.engine.normalize.first_names import FIRST_NAMES
from orkesta.engine.normalize.names import HONORIFICS, JOB_WORDS, PARTICLES, split_full_name
from orkesta.engine.normalize.text import clean, fold
from orkesta.engine.taxonomy.titles import classify_title

BLOCK_TAGS = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    ]
)
SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "select"})


@dataclass
class Document:
    title: str = ""
    lines: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (href, texto)
    json_ld: list[Any] = field(default_factory=list)
    site_name: str = ""


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = Document()
        self._buffer: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_json_ld = False
        self._json_buffer: list[str] = []
        self._link: tuple[str, list[str]] | None = None

    def _flush(self) -> None:
        text = clean(" ".join(self._buffer))
        if text:
            self.doc.lines.append(text[:500])
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_json_ld, self._json_buffer = True, []
            return
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and values.get("property") == "og:site_name":
            self.doc.site_name = clean(values.get("content"))[:200]
        elif tag == "a" and values.get("href"):
            if values["href"].startswith(("mailto:", "tel:")):
                self.doc.links.append((values["href"], ""))
            else:
                self._link = (values["href"], [])
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            with contextlib.suppress(ValueError):
                self.doc.json_ld.append(json.loads("".join(self._json_buffer)))
            return
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._link is not None:
            href, parts = self._link
            self.doc.links.append((href, clean(" ".join(parts))[:200]))
            self._link = None
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_buffer.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.doc.title = clean(self.doc.title + " " + data)[:300]
            return
        self._buffer.append(data)
        if self._link is not None:
            self._link[1].append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def parse_html(html: str) -> Document:
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        pass
    return parser.doc


# --- Personas ---------------------------------------------------------------------------------

_TOKEN = re.compile(r"^[A-ZÁÉÍÓÚÑÜÀÈÌÒÙÇ][a-záéíóúñüàèìòùçA-ZÁÉÍÓÚÑÜ'’\-]*\.?$")
_NOT_NAME = re.compile(r"[\d@/\\:;!?¿¡()\[\]{}<>#$%&*+=_|\"“”]")
_SEPARATOR = re.compile(r"\s+[-–—|·•]\s+|\s*:\s+|\s*,\s+|\s*\|\s*")
_TITLE_LEAD = re.compile(r"^(?:el|la|nuestro|nuestra|our|the)\s+", re.IGNORECASE)


# Palabras que delatan que un texto en mayúsculas iniciales no es un nombre de persona.
NON_NAME_WORDS = frozenset(
    [
        "equipo",
        "junta",
        "directiva",
        "directivo",
        "directivos",
        "gobierno",
        "corporativo",
        "nuestro",
        "nuestra",
        "nuestros",
        "nuestras",
        "conoce",
        "conozca",
        "somos",
        "quienes",
        "historia",
        "mision",
        "vision",
        "valores",
        "contacto",
        "contactenos",
        "noticias",
        "servicios",
        "servicio",
        "productos",
        "producto",
        "banco",
        "grupo",
        "empresa",
        "compania",
        "corporacion",
        "sociedad",
        "fundacion",
        "asociacion",
        "holding",
        "inicio",
        "bienvenido",
        "bienvenidos",
        "politica",
        "politicas",
        "privacidad",
        "terminos",
        "aviso",
        "legal",
        "sostenibilidad",
        "informe",
        "anual",
        "reporte",
        "inversionistas",
        "accionistas",
        "comite",
        "consejo",
        "presidencia",
        "gerencia",
        "administracion",
        "oficina",
        "sede",
        "sucursal",
        "seguros",
        "financiera",
        "capital",
        "cooperativa",
        "universidad",
        "colegio",
        "hospital",
        "clinica",
        "ministerio",
        "the",
        "our",
        "team",
        "board",
        "about",
        "us",
        "leadership",
        "management",
        "company",
        "group",
        "services",
        "news",
    ]
)


def looks_like_person(text: str, *, strict: bool = True) -> bool:
    """«Juan Carlos Pérez Gómez» sí; «Gerente General», «Nuestro Equipo» o «Banco Andino» no.

    ``strict``: el primer nombre debe estar en el diccionario de nombres de pila. Sin él (cuando
    hay otra señal fuerte, como un cargo al lado o una fuente de búsqueda) basta con que tenga
    forma de nombre y ninguna palabra típica de títulos de página o empresas.
    """
    value = clean(text)
    if not 5 <= len(value) <= 60 or _NOT_NAME.search(value):
        return False
    tokens = value.split()
    while tokens and fold(tokens[0]).rstrip(".") in HONORIFICS:
        tokens = tokens[1:]
    if not 2 <= len(tokens) <= 5:
        return False
    if strict and fold(tokens[0]) not in FIRST_NAMES:
        return False
    for token in tokens:
        folded = fold(token).rstrip(".")
        if folded in PARTICLES:
            continue
        if folded in JOB_WORDS or folded in NON_NAME_WORDS or not _TOKEN.match(token):
            return False
        if not strict and len(folded) < 2:
            return False
    level, area = classify_title(value)
    return level is None and area is None


# Cargos que ya no se ocupan: no sirven para prospectar.
_FORMER = re.compile(
    r"(?:^|\s)(?:ex|ex-|former|anterior|antiguo|antigua|retirad[oa]|hasta 20\d\d)(?:\s|$)"
    r"|^ex(?:gerente|presidente|presidenta|director|directora|vicepresidente|jefe|socio|ceo)"
)


def looks_like_title(text: str) -> bool:
    value = clean(text)
    if not 2 <= len(value) <= 120 or "@" in value or looks_like_person(value):
        return False
    if _FORMER.search(fold(value)):
        return False
    level, area = classify_title(value)
    return level is not None or (area is not None and len(value.split()) <= 8)


def clean_title(text: str) -> str:
    value = _TITLE_LEAD.sub("", clean(text)).strip(" .,:-–")
    return value[:1].upper() + value[1:] if value else value


def strip_honorifics(name: str) -> str:
    tokens = clean(name).split()
    while tokens and fold(tokens[0]).rstrip(".") in HONORIFICS:
        tokens = tokens[1:]
    return " ".join(tokens)


@dataclass(frozen=True)
class Person:
    full_name: str
    first_name: str
    last_name: str
    title: str


def make_person(name: str, title: str) -> Person | None:
    full = strip_honorifics(name)
    split = split_full_name(full)
    if not split.first or not split.last:
        return None
    first = " ".join(t if not t.isupper() else t.title() for t in split.first.split())
    last = " ".join(
        t if not t.isupper() or fold(t) in PARTICLES else t.title() for t in split.last.split()
    )
    return Person(f"{first} {last}", first, last, clean_title(title))


def _from_json_ld(data: Any) -> Iterator[tuple[str, str]]:
    if isinstance(data, list):
        for item in data:
            yield from _from_json_ld(item)
    elif isinstance(data, dict):
        kind = data.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "Person" in kinds and isinstance(data.get("name"), str):
            yield data["name"], str(data.get("jobTitle") or "")
        for key in ("@graph", "employee", "employees", "founder", "founders", "member"):
            if key in data:
                yield from _from_json_ld(data[key])


def people_in(doc: Document) -> list[Person]:
    """Personas con cargo en una página: bloques nombre/cargo contiguos, «Nombre – Cargo»,
    «Cargo: Nombre» y datos estructurados (JSON-LD)."""
    found: dict[str, Person] = {}

    def add(name: str, title: str) -> None:
        person = make_person(name, title)
        if person is not None:
            key = fold(person.full_name)
            if key not in found or (not found[key].title and person.title):
                found[key] = person

    for data in doc.json_ld:
        for name, title in _from_json_ld(data):
            if looks_like_person(name):
                add(name, title if looks_like_title(title) else "")

    lines = doc.lines
    for i, line in enumerate(lines):
        pieces = [p for p in _SEPARATOR.split(line, maxsplit=1) if p.strip()]
        if len(pieces) == 2:
            left, right = pieces
            if (
                looks_like_person(left, strict=False)
                and looks_like_title(right)
                and (looks_like_person(left) or classify_title(right)[0])
            ):
                add(left, right)
                continue
            if (
                looks_like_title(left)
                and looks_like_person(right, strict=False)
                and (looks_like_person(right) or classify_title(left)[0])
            ):
                add(right, left)
                continue
        strict = looks_like_person(line)
        if not strict and not looks_like_person(line, strict=False):
            continue
        # El cargo suele ir justo después del nombre; a veces justo antes. Un nombre poco común
        # (fuera del diccionario) solo se acepta con un cargo directivo inmediatamente al lado.
        neighbors = (i + 1, i + 2, i - 1) if strict else (i + 1, i - 1)
        for j in neighbors:
            if 0 <= j < len(lines) and looks_like_title(lines[j]):
                if j == i + 2 and looks_like_person(lines[i + 1], strict=False):
                    continue
                if not strict and classify_title(lines[j])[0] is None:
                    continue
                add(line, lines[j])
                break
    return list(found.values())


# --- Correos y teléfonos --------------------------------------------------------------------

EMAIL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._%+\-]{0,63}@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}")
GENERIC_LOCAL = frozenset(
    [
        "info",
        "contacto",
        "contact",
        "hola",
        "hello",
        "ventas",
        "sales",
        "comercial",
        "servicio",
        "servicioalcliente",
        "atencion",
        "atencionalcliente",
        "soporte",
        "support",
        "admin",
        "administracion",
        "prensa",
        "press",
        "comunicaciones",
        "marketing",
        "rrhh",
        "talento",
        "trabajo",
        "empleo",
        "careers",
        "jobs",
        "facturacion",
        "notificaciones",
        "noreply",
        "no-reply",
        "webmaster",
        "privacidad",
        "privacy",
        "datos",
        "legal",
        "gerencia",
        "recepcion",
        "compras",
        "proveedores",
        "pqrs",
        "pqr",
        "inversionistas",
        "investors",
    ]
)
PHONE = re.compile(r"^\+?[\d\s().\-]{7,22}$")


def emails_in(doc: Document, domain: str) -> set[str]:
    """Correos del dominio de la empresa publicados en la página."""
    found: set[str] = set()
    texts = [href[7:].split("?")[0] for href, _ in doc.links if href.lower().startswith("mailto:")]
    texts += [m.group(0) for line in doc.lines for m in EMAIL.finditer(line)]
    for value in texts:
        email = unescape(value).strip().lower().strip(".")
        if EMAIL.fullmatch(email) and (
            email.endswith("@" + domain) or email.endswith("." + domain)
        ):
            found.add(email)
    return found


def phones_in(doc: Document) -> set[str]:
    found: set[str] = set()
    for href, _ in doc.links:
        if href.lower().startswith("tel:"):
            value = unescape(href[4:]).strip()
            digits = re.sub(r"\D", "", value)
            if PHONE.match(value) and 7 <= len(digits) <= 15:
                found.add(("+" if value.startswith("+") else "") + digits)
    return found


def is_generic_email(email: str) -> bool:
    local = email.split("@", 1)[0]
    return local in GENERIC_LOCAL or any(local.startswith(g) for g in ("info", "contact"))


# --- Patrones de correo ---------------------------------------------------------------------

PATTERNS: dict[str, str] = {
    "first.last": "{f}.{l}",
    "firstlast": "{f}{l}",
    "flast": "{fi}{l}",
    "f.last": "{fi}.{l}",
    "first_last": "{f}_{l}",
    "first": "{f}",
    "lastf": "{l}{fi}",
    "last.first": "{l}.{f}",
}


def _parts(first: str, last: str) -> dict[str, str] | None:
    f = re.sub(r"[^a-z]", "", fold(first.split()[0])) if first.split() else ""
    tokens = [t for t in last.split() if fold(t) not in PARTICLES]
    lt = re.sub(r"[^a-z]", "", fold(tokens[0])) if tokens else ""
    if not f or not lt:
        return None
    return {"f": f, "l": lt, "fi": f[0]}


def detect_pattern(first: str, last: str, email: str) -> str | None:
    parts = _parts(first, last)
    if parts is None:
        return None
    local = email.split("@", 1)[0]
    for name, template in PATTERNS.items():
        if template.format(**parts) == local:
            return name
    return None


def apply_pattern(pattern: str, first: str, last: str, domain: str) -> str | None:
    parts = _parts(first, last)
    if parts is None or pattern not in PATTERNS:
        return None
    return f"{PATTERNS[pattern].format(**parts)}@{domain}"


def email_matches_person(email: str, first: str, last: str) -> bool:
    return detect_pattern(first, last, email) is not None
