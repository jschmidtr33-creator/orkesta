"""Extracción de personas (nombre + cargo) de páginas públicas de equipo, directivos y prensa.

Tres fuentes dentro de una página:

1. **Datos estructurados** schema.org ``Person`` (``name`` y ``jobTitle``) en JSON-LD.
2. **Fichas:** una línea con un nombre de persona junto a una línea con un cargo
   ("Mariana Torres" / "Directora de Finanzas"), en cualquier orden.
3. **Texto:** "Mariana Torres, Directora de Finanzas", "Mariana Torres – CFO" y frases de
   nombramiento en prensa ("designó a Mariana Torres como nueva directora de finanzas").

Para no inventar personas: el nombre debe empezar con un nombre de pila conocido y tener al
menos un apellido; el cargo debe tener un nivel o un área reconocibles.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from orkesta.catalog.titles import classify_title
from orkesta.engine.normalize.first_names import FIRST_NAMES
from orkesta.engine.normalize.text import clean, fold

MAX_LINE = 160
MAX_TITLE = 120
PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "da", "dos", "van", "von", "di"})
_UPPER_WORD = re.compile(r"^[A-ZÁÉÍÓÚÑÜ][a-záéíóúñüA-ZÁÉÍÓÚÑÜ'’.-]*$")
_SEPARATOR = re.compile(r"\s*(?:,|\s[-–—|]\s|:)\s*")
_APPOINTMENT = re.compile(
    r"(?:nombr[oó]|design[oó]|nombramiento de|incorpor[oó]|incorpora a|se une|se suma"
    r"|appointed|named|has appointed|welcomes)\s+(?:a\s+)?"
    r"(?P<name>[A-ZÁÉÍÓÚÑÜ][\wáéíóúñü'’-]+(?:\s+(?:de|del|la|los|y)?\s*[A-ZÁÉÍÓÚÑÜ][\wáéíóúñü'’-]+){1,4})"
    r"\s*,?\s+(?:como|en el cargo de|al cargo de|as|to the role of)\s+(?:su\s+|nuev[oa]\s+|new\s+)*"
    r"(?P<title>[^.;\n]{3,120})",
    re.IGNORECASE,
)
_BLOCK_TAGS = frozenset(
    "p div li tr td th h1 h2 h3 h4 h5 h6 section article header footer figcaption span strong "
    "b em small dt dd blockquote br".split()
)


@dataclass(frozen=True)
class FoundPerson:
    full_name: str
    first_name: str
    last_name: str
    title: str
    method: str  # jsonld | card | inline | press
    url: str


class _Lines(HTMLParser):
    """Texto en líneas por bloque y los JSON-LD de la página."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.jsonld: list[str] = []
        self._buffer: list[str] = []
        self._skip = 0
        self._in_jsonld = False
        self._jsonld_buffer: list[str] = []

    def _flush(self) -> None:
        text = clean(" ".join(self._buffer))
        if text:
            self.lines.append(text[: MAX_LINE * 4])
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
            self._in_jsonld, self._jsonld_buffer = True, []
        elif tag in ("script", "style", "noscript", "svg", "template", "nav"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_buffer))
            self._in_jsonld = False
        elif tag in ("script", "style", "noscript", "svg", "template", "nav"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buffer.append(data)
        elif not self._skip:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def person_name(text: str) -> tuple[str, str] | None:
    """(nombre, apellidos) si ``text`` parece el nombre de una persona; si no, ``None``."""
    text = clean(text).strip(" .,;:-–—|")
    if not text or len(text) > 60 or any(c.isdigit() for c in text) or "@" in text:
        return None
    words = text.split()
    if not 2 <= len(words) <= 6:
        return None
    if any(w.lower() not in PARTICLES and not _UPPER_WORD.match(w) for w in words):
        return None
    if words[0].lower() in PARTICLES or words[-1].lower() in PARTICLES:
        return None
    folded = [fold(w).strip(".'’") for w in words]
    # Tratamientos al inicio ("Ing.", "Dra.", "Lic.") se descartan.
    while folded and folded[0] in {"ing", "dr", "dra", "lic", "mtro", "mtra", "sr", "sra", "cp"}:
        words, folded = words[1:], folded[1:]
    if len(words) < 2 or folded[0] not in FIRST_NAMES:
        return None
    # Un "nombre" que es un cargo ("Director General") o tiene palabras de cargo no sirve.
    if classify_title(" ".join(words)) != (None, None):
        return None
    first_count = 2 if len(words) >= 4 and folded[1] in FIRST_NAMES else 1
    first = " ".join(words[:first_count])
    last = " ".join(words[first_count:])
    return (first, last) if last else None


def job_title(text: str) -> str | None:
    text = clean(text).strip(" .,;:-–—|")
    if not text or len(text) > MAX_TITLE or "@" in text or "http" in text:
        return None
    level, area = classify_title(text)
    if level is None and area is None:
        return None
    return text


def _pure_title(line: str) -> bool:
    if not job_title(line) or person_name(line):
        return False
    head = _SEPARATOR.split(line, maxsplit=1)[0]
    return person_name(head) is None


_TITLE_TAIL = re.compile(
    r"(?:,|\s+)(?:para|en|a partir|desde|con efecto|for|at|in|effective|of the company)\b.*$",
    re.IGNORECASE,
)


def _jsonld_people(blobs: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Person" in kinds and isinstance(node.get("name"), str):
                title = node.get("jobTitle")
                if isinstance(title, list):
                    title = title[0] if title else None
                if isinstance(title, str):
                    found.append((node["name"], title))
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    for blob in blobs:
        try:
            walk(json.loads(blob))
        except (ValueError, RecursionError):
            continue
    return found


def extract_people(html: str, url: str) -> list[FoundPerson]:
    parser = _Lines()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # HTML muy roto: se devuelve lo que se haya podido leer
        parser._flush()
    found: dict[str, FoundPerson] = {}

    def add(name_text: str, title_text: str, method: str) -> None:
        name = person_name(name_text)
        title = job_title(title_text)
        if name and title:
            full = f"{name[0]} {name[1]}"
            key = fold(full)
            if key not in found:
                found[key] = FoundPerson(full, name[0], name[1], title, method, url)

    for name_text, title_text in _jsonld_people(parser.jsonld):
        add(name_text, title_text, "jsonld")

    lines = [line for line in parser.lines if len(line) <= MAX_LINE]
    used: set[int] = set()
    for i, line in enumerate(lines):
        if i in used or not person_name(line):
            continue
        # Ficha: el cargo justo después (o antes) del nombre. Una línea que empieza con otro
        # nombre ("Ana Pérez, Jefa de Compras") es otra persona, no un cargo de esta ficha.
        for j in (i + 1, i - 1, i + 2):
            if 0 <= j < len(lines) and j not in used and _pure_title(lines[j]):
                add(line, lines[j], "card")
                used.update({i, j})
                break

    for line in parser.lines:
        for match in _APPOINTMENT.finditer(line):
            add(match.group("name"), _TITLE_TAIL.sub("", match.group("title")), "press")
        if len(line) <= MAX_LINE:
            parts = _SEPARATOR.split(line, maxsplit=1)
            if len(parts) == 2:
                add(parts[0], parts[1], "inline")
                add(parts[1], parts[0], "inline")
    return list(found.values())
