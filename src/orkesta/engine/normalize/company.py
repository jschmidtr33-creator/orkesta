"""Normalización de nombres de empresa.

Se retira la forma jurídica (S.A.S., S.A. de C.V., Ltda., Inc.) solo para comparar; el nombre
original se conserva siempre. Los tokens distintivos excluyen palabras genéricas ("grupo",
"servicios") y nombres de país, y son la base para generar dominios candidatos en la Etapa 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from orkesta.engine.normalize.text import clean, fold

# Formas jurídicas en forma plegada, de la más larga a la más corta para que gane la más específica.
LEGAL_FORMS: tuple[str, ...] = tuple(
    sorted(
        {
            "s a de c v",
            "s a de cv",
            "sa de c v",
            "s de r l de cv",
            "s a p i de cv",
            "s a b de cv",
            "sa de cv",
            "s de r l de c v",
            "s de rl de cv",
            "s de r l",
            "s de rl",
            "s a p i de c v",
            "sapi de cv",
            "s a b de c v",
            "sab de cv",
            "s c",
            "a c",
            "s c p",
            "s a s",
            "sas",
            "s a",
            "sa",
            "s a c",
            "sac",
            "s a a",
            "saa",
            "s r l",
            "srl",
            "e i r l",
            "eirl",
            "s a e c a",
            "saeca",
            "s en c",
            "s en c s",
            "ltda",
            "limitada",
            "cia ltda",
            "y cia",
            "y cia ltda",
            "c a",
            "s l",
            "sl",
            "s l u",
            "slu",
            "s p a",
            "spa",
            "inc",
            "incorporated",
            "llc",
            "l l c",
            "corp",
            "corporation",
            "co",
            "ltd",
            "limited",
            "gmbh",
            "ag",
            "plc",
            "bv",
            "b v",
            "nv",
            "n v",
            "s a u",
            "sau",
            "lp",
            "llp",
        },
        key=len,
        reverse=True,
    )
)

GENERIC_WORDS = frozenset(
    {
        "grupo",
        "group",
        "servicios",
        "services",
        "service",
        "comercial",
        "comercializadora",
        "corporacion",
        "corporativo",
        "compania",
        "company",
        "empresa",
        "empresas",
        "soluciones",
        "solutions",
        "industrias",
        "industria",
        "industrial",
        "internacional",
        "international",
        "global",
        "consultores",
        "consultoria",
        "consulting",
        "asociados",
        "associates",
        "holding",
        "holdings",
        "inversiones",
        "investments",
        "sistemas",
        "systems",
        "tecnologia",
        "tecnologias",
        "technology",
        "technologies",
        "latam",
        "latinoamerica",
        "america",
        "americas",
        "de",
        "del",
        "la",
        "las",
        "los",
        "el",
        "y",
        "and",
        "the",
        "of",
        "en",
        "colombia",
        "mexico",
        "peru",
        "chile",
        "argentina",
        "ecuador",
        "venezuela",
        "bolivia",
        "paraguay",
        "uruguay",
        "brasil",
        "brazil",
        "panama",
        "guatemala",
        "honduras",
        "salvador",
        "nicaragua",
        "costa",
        "rica",
        "dominicana",
        "espana",
        "spain",
        "usa",
        "com",
        "oficial",
    }
)

# Calificativos que acompañan a la forma jurídica: BIC (Colombia), E.S.P. (servicios públicos).
QUALIFIERS: tuple[str, ...] = (
    "e s p",
    "esp",
    "bic",
    "s a s bic",
    "en liquidacion",
    "en reorganizacion",
)

# Valores que no identifican a una empresa con dominio corporativo propio.
NOT_A_COMPANY = frozenset(
    {
        "",
        "n a",
        "na",
        "ninguna",
        "ninguno",
        "none",
        "null",
        "nan",
        "independiente",
        "freelance",
        "freelancer",
        "self employed",
        "selfemployed",
        "autonomo",
        "autonoma",
        "confidencial",
        "confidential",
        "stealth",
        "stealth mode",
        "stealth startup",
        "varios",
        "particular",
        "desempleado",
        "desempleada",
        "unemployed",
        "sin empresa",
        "private",
        "privado",
        "undisclosed",
        "consultor independiente",
        "independent consultant",
    }
)


@dataclass(frozen=True)
class CompanyName:
    original: str
    display: str
    normalized: str
    legal_form: str | None
    tokens: tuple[str, ...]
    distinctive_tokens: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return bool(self.normalized) and self.normalized not in NOT_A_COMPANY


def _suffix_pattern(suffixes: tuple[str, ...]) -> re.Pattern[str]:
    ordered = sorted(suffixes, key=len, reverse=True)
    return re.compile(r"(?:^|\s)(" + "|".join(re.escape(s) for s in ordered) + r")$")


_LEGAL_PATTERN = _suffix_pattern(LEGAL_FORMS)
_QUALIFIER_PATTERN = _suffix_pattern(QUALIFIERS)


def _strip_with(pattern: re.Pattern[str], folded: str) -> tuple[str, str | None]:
    match = pattern.search(folded)
    # Un sufijo nunca es el nombre entero ("SA" sola no es una empresa).
    if match and match.start() > 0:
        return folded[: match.start()].strip(), match.group(1)
    return folded, None


def _strip_legal_form(folded: str) -> tuple[str, str | None]:
    return _strip_with(_LEGAL_PATTERN, folded)


def _strip_display_legal_form(display: str, legal_form: str | None) -> str:
    """Quita la forma jurídica del nombre visible conservando mayúsculas y tildes."""
    if legal_form is None:
        return display
    words = display.split()
    target = legal_form.replace(" ", "")
    for cut in range(len(words) - 1, 0, -1):
        tail = fold(" ".join(words[cut:])).replace(" ", "")
        if tail == target:
            return " ".join(words[:cut]).rstrip(" ,.-&")
    return display


def _strip_suffixes(folded: str) -> tuple[str, str | None]:
    """Retira capas de sufijos: "alpina productos alimenticios s a bic" → forma "s a bic"."""
    base = folded
    removed: list[str] = []
    for _ in range(4):
        stripped: tuple[str, str] | None = None
        candidate, qualifier = _strip_with(_QUALIFIER_PATTERN, base)
        if qualifier is not None:
            stripped = (candidate, qualifier)
        if stripped is None:
            candidate, form = _strip_legal_form(base)
            if form is not None:
                stripped = (candidate, form)
        if stripped is None:
            break
        base = stripped[0]
        removed.insert(0, stripped[1])
    return base, (" ".join(removed) if removed else None)


def _fold_company(value: str) -> str:
    # "AT&T" → "att"; "Procter & Gamble" → "procter y gamble".
    return fold(re.sub(r"(?<=\w)&(?=\w)", "", value))


@lru_cache(maxsize=50_000)
def normalize_company(name: str | None) -> CompanyName:
    original = clean(name)
    folded = _fold_company(original)
    base, legal_form = _strip_suffixes(folded)
    tokens = tuple(base.split())
    distinctive = tuple(t for t in tokens if t not in GENERIC_WORDS and len(t) > 1)
    display = _strip_display_legal_form(original, legal_form).strip() or original
    return CompanyName(
        original=original,
        display=display,
        normalized=base,
        legal_form=legal_form,
        tokens=tokens,
        distinctive_tokens=distinctive,
    )
