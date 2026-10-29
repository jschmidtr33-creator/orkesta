"""Normalización de nombres de personas, con convenciones hispanas.

- Nombres compuestos: "María José" → nombre principal "maria", completo "mariajose".
- Dos apellidos: el primero es el principal (convención LATAM); el segundo se conserva.
- Partículas: "de la Cruz" forma un solo apellido; se generan las variantes con y sin partícula.
- Se retiran tratamientos y credenciales ("Ing.", "Dra.", "MBA"), apodos entre paréntesis y emojis.
- ``split_full_name`` separa nombre y apellidos cuando la base trae el nombre completo en una
  sola columna (exportaciones de LinkedIn o Sales Navigator).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orkesta.engine.normalize.first_names import FIRST_NAMES
from orkesta.engine.normalize.text import clean, fold, has_letters

PARTICLES = frozenset(
    {
        "de",
        "del",
        "la",
        "las",
        "los",
        "y",
        "e",
        "i",
        "da",
        "das",
        "do",
        "dos",
        "di",
        "du",
        "van",
        "von",
        "der",
        "den",
        "le",
        "lo",
        "san",
        "santa",
        "mac",
        "st",
    }
)

# Tratamientos y credenciales que se retiran del nombre (comparados en forma plegada).
HONORIFICS = frozenset(
    {
        "sr",
        "sra",
        "srta",
        "dr",
        "dra",
        "ing",
        "inga",
        "lic",
        "licda",
        "arq",
        "abg",
        "abog",
        "mtro",
        "mtra",
        "prof",
        "phd",
        "mba",
        "msc",
        "cpa",
        "pmp",
        "mr",
        "mrs",
        "ms",
        "miss",
        "eng",
        "esq",
        "jr",
        "sr.",
        "ii",
        "iii",
        "cfa",
        "ceo",
        "cfo",
        "cto",
        "dds",
        "md",
    }
)

_PARENTHESES = re.compile(r"\([^)]*\)|\[[^\]]*\]|\"[^\"]*\"|“[^”]*”")
# Todo lo que no sea letra, marca diacrítica, espacio, guion o apóstrofo.
_NOT_NAME_CHAR = re.compile(r"[^\w\s'’\-]|[\d_]", re.UNICODE)
_AFTER_COMMA_CREDENTIALS = re.compile(r",.*$")


@dataclass(frozen=True)
class PersonName:
    first_original: str
    last_original: str
    first_display: str
    last_display: str
    # Formas plegadas (sin tildes, minúsculas) usadas para deduplicar.
    first_normalized: str
    last_normalized: str
    # Piezas para construir correos en la Etapa 3.
    first_primary: str
    first_compact: str
    last_primary: str
    last_primary_core: str
    last_second: str | None
    removed: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return bool(self.first_primary) and bool(self.last_primary_core)

    @property
    def last_is_initial(self) -> bool:
        """El apellido solo trae la inicial ("Marvin N."): no basta para construir un correo."""
        return len(self.last_primary_core) == 1


def _strip_extras(value: str) -> tuple[str, list[str]]:
    removed: list[str] = []
    text = clean(value)
    if _PARENTHESES.search(text):
        removed.extend(m.group(0) for m in _PARENTHESES.finditer(text))
        text = _PARENTHESES.sub(" ", text)
    # "Juan Pérez, MBA" → credenciales tras la coma.
    match = _AFTER_COMMA_CREDENTIALS.search(text)
    if match:
        removed.append(match.group(0).lstrip(", "))
        text = text[: match.start()]
    cleaned = _NOT_NAME_CHAR.sub(" ", text)
    if cleaned != text:
        removed.append("símbolos")
    tokens = [t.strip("-'’") for t in cleaned.split()]
    kept = []
    for token in tokens:
        if not token:
            continue
        if fold(token).replace(" ", "") in HONORIFICS:
            removed.append(token)
            continue
        kept.append(token)
    return " ".join(kept), removed


def _display_case(tokens: list[str]) -> str:
    """Capitaliza respetando partículas en minúscula y guiones: "PÉREZ-GÓMEZ DE LA CRUZ"."""
    out = []
    for index, token in enumerate(tokens):
        lower = token.lower()
        if index > 0 and fold(token) in PARTICLES:
            out.append(lower)
            continue
        parts = re.split(r"([-'’])", lower)
        out.append("".join(p[:1].upper() + p[1:] if p not in "-'’" else p for p in parts))
    return " ".join(out)


def _compact(value: str) -> str:
    return fold(value).replace(" ", "")


def _split_surnames(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Separa el primer apellido (con sus partículas) del resto.

    "de la Cruz Pérez" → (["de", "la", "Cruz"], ["Pérez"]); "Pérez Gómez" → (["Pérez"], ["Gómez"]).
    """
    first: list[str] = []
    index = 0
    while index < len(tokens) and fold(tokens[index]) in PARTICLES:
        first.append(tokens[index])
        index += 1
    if index < len(tokens):
        first.append(tokens[index])
        index += 1
    return first, tokens[index:]


def normalize_person(first: str | None, last: str | None) -> PersonName:
    first_original, last_original = clean(first), clean(last)
    first_text, removed_first = _strip_extras(first_original)
    last_text, removed_last = _strip_extras(last_original)

    first_tokens = first_text.split()
    last_tokens = last_text.split()

    first_display = _display_case(first_tokens)
    last_display = _display_case(last_tokens)

    primary_tokens, second_tokens = _split_surnames(last_tokens)
    core_tokens = [t for t in primary_tokens if fold(t) not in PARTICLES]

    first_names = [t for t in first_tokens if fold(t) not in PARTICLES]
    return PersonName(
        first_original=first_original,
        last_original=last_original,
        first_display=first_display,
        last_display=last_display,
        first_normalized=fold(first_text),
        last_normalized=fold(last_text),
        first_primary=_compact(first_names[0]) if first_names else "",
        first_compact=_compact(" ".join(first_names)),
        last_primary=_compact(" ".join(primary_tokens)),
        last_primary_core=_compact(" ".join(core_tokens)),
        last_second=_compact(" ".join(second_tokens)) or None,
        removed=tuple(removed_first + removed_last),
    )


def looks_like_name(value: str) -> bool:
    """Un nombre válido tiene letras y no parece un correo ni una URL."""
    text = clean(value)
    return has_letters(text) and "@" not in text and "://" not in text


# Palabras de cargo que LinkedIn deja pegadas al nombre ("YANIRA MUÑOZ Marketing").
JOB_WORDS = frozenset(
    "marketing sales ventas manager gerente director directora hr rrhh recruiter reclutador "
    "reclutadora consultant consultor consultora coach founder fundador fundadora ceo cfo cto "
    "coo cmo cio cso vp mentor speaker headhunter abogado abogada ingeniero ingeniera "
    "contador contadora lead head".split()
)
_SEGMENT_BREAK = re.compile(r"\s[-–—|·•]\s|\||,|\s/\s")
_INITIAL = re.compile(r"^[^\W\d_]\.?$", re.UNICODE)


@dataclass(frozen=True)
class SplitName:
    first: str
    last: str
    removed: tuple[str, ...]


def _is_initial(token: str) -> bool:
    # "N." o "J" son iniciales; "y" o "e" en minúscula son conjunciones ("Cal y Mayor").
    return bool(_INITIAL.match(token)) and (token.endswith(".") or token[0].isupper())


def _looks_like_surname(token: str) -> bool:
    folded = fold(token)
    # Patronímicos en "-ez" (Martínez, Pérez, Vázquez) casi nunca son nombres de pila.
    return folded not in FIRST_NAMES and folded.endswith("ez") and len(folded) > 3


def split_full_name(value: str | None) -> SplitName:
    """Separa "Rodrigo Alonso Quintero Mora" en ("Rodrigo Alonso", "Quintero Mora").

    Reglas, en orden:
    1. Se corta en el primer separador ("Ana López - Blindajes" → "Ana López").
    2. Se retiran paréntesis, tratamientos, palabras de cargo e iniciales intermedias.
    3. Una partícula temprana marca el inicio del apellido ("Ernesto De Jesús").
    4. 2 palabras: 1 + 1. 3 palabras: 2 + 1 si la segunda es un nombre conocido; si no, 1 + 2.
       4 palabras: 2 + 2, salvo que la segunda parezca apellido ("Luis Martínez Lavín Casta").
       5 o más: tantos nombres conocidos al inicio como haya (1 o 2).
    """
    removed: list[str] = []
    text = clean(value)
    pieces = _SEGMENT_BREAK.split(text, maxsplit=1)
    if len(pieces) > 1 and pieces[0].strip():
        removed.append(pieces[1].strip())
        text = pieces[0]
    if _PARENTHESES.search(text):
        removed.extend(m.group(0) for m in _PARENTHESES.finditer(text))
        text = _PARENTHESES.sub(" ", text)

    raw_tokens = [t.strip("-'’") for t in re.sub(r"[^\w\s.'’\-]|[\d_]", " ", text).split()]
    raw_tokens = [t for t in raw_tokens if t and t != "."]
    all_caps = all(t.isupper() for t in raw_tokens if len(t) > 1)

    tokens: list[str] = []
    for token in raw_tokens:
        folded = fold(token).replace(" ", "")
        if folded in HONORIFICS or (folded in JOB_WORDS and tokens):
            removed.append(token)
            continue
        tokens.append(token)

    # Siglas finales en mayúsculas cuando el resto no lo está: "Lorena Trujillo RM".
    if len(tokens) > 2 and not all_caps:
        last = tokens[-1].rstrip(".")
        if last.isupper() and 2 <= len(last) <= 3:
            removed.append(tokens.pop())

    # Iniciales intermedias ("Teresa N. Suarez") y la inicial de un segundo apellido
    # ("Andrea Zamarripa J."): no aportan al correo.
    kept: list[str] = []
    for index, token in enumerate(tokens):
        is_last = index == len(tokens) - 1
        if _is_initial(token) and not is_last and index > 0:
            removed.append(token)
            continue
        if _is_initial(token) and is_last and len(kept) >= 2 and fold(kept[-1]) not in FIRST_NAMES:
            removed.append(token)
            continue
        kept.append(token)
    tokens = [t.rstrip(".") if _is_initial(t) else t for t in kept]

    count = len(tokens)
    if count == 0:
        return SplitName("", "", tuple(removed))
    if count == 1:
        return SplitName(tokens[0], "", tuple(removed))

    particle_at = next(
        (i for i, t in enumerate(tokens[:3]) if i > 0 and fold(t) in PARTICLES), None
    )
    # "Ernesto De Jesús" (partícula tras el nombre) o "Ana Gabriela de la Peña" (tras dos
    # nombres conocidos). En "Gabriel Morales del Portillo" la partícula va tras un apellido.
    if particle_at == 1 or (particle_at == 2 and fold(tokens[1]) in FIRST_NAMES):
        names = particle_at
    elif particle_at == 2 or count == 2:
        names = 1
    elif count == 3:
        names = 2 if fold(tokens[1]) in FIRST_NAMES else 1
    elif count == 4:
        names = 1 if _looks_like_surname(tokens[1]) else 2
    else:
        names = 2 if fold(tokens[1]) in FIRST_NAMES else 1

    return SplitName(" ".join(tokens[:names]), " ".join(tokens[names:]), tuple(removed))
