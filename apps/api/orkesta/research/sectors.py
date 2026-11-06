"""Sector de cada empresa con el motor propio de ORKESTA (sin servicios externos).

Las bases que se suben traen nombre, cargo y empresa, pero casi nunca el sector. Sin sector, una
búsqueda filtrada por industria descartaba miles de perfiles propios. Este motor lo deduce, del
dato más confiable al menos confiable, y guarda de dónde salió:

1. «registro»: el sector viene de un registro público importado (no se toca).
2. «curado»: empresa conocida (Colbún, Bimbo, Ecopetrol…), `data/known_companies.py`.
3. «dominio»: dominios educativos o de gobierno (.edu, .gob, .gov, .mil).
4. «nombre»: el nombre dice a qué se dedica («Banco…», «Seguros…», «Constructora…»).
5. «sitio»: el rastreador leyó el sitio oficial y su descripción lo dice (ver crawler.py).
6. «mismo dominio»: otra empresa con el mismo dominio ya tiene sector.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.engine.normalize.text import fold
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS, classify
from orkesta.research.data.known_companies import KNOWN
from orkesta.research.models import CompanyDirectory

# Palabras que no distinguen a una empresa (forma jurídica, país, «grupo»).
NOISE = frozenset({
    "sa", "s", "a", "sas", "spa", "ltda", "limitada", "sab", "de", "cv", "sapi", "rl", "srl",
    "inc", "llc", "corp", "corporation", "co", "company", "group", "grupo", "corporativo",
    "holding", "cia", "y", "el", "la", "los", "las", "del", "chile", "mexico", "colombia",
    "argentina", "peru", "uruguay", "paraguay", "bolivia", "ecuador", "panama", "brasil",
    "brazil", "espana", "spain", "latam", "latin", "america", "en", "oficial", "cuenta",
})  # fmt: skip
GOV_DOMAIN = re.compile(r"\.(gob|gov|mil)(\.[a-z]{2})?$|^gob\.|\.gouv\.")
EDU_DOMAIN = re.compile(r"\.edu(\.[a-z]{2})?$|\.ac\.[a-z]{2}$")
# El nombre trae palabras de un sector, pero la entidad es otra cosa.
NAME_FIXES = (
    (re.compile(r"\bcamara de comercio\b"), "servicios_profesionales"),
    (re.compile(r"\b(ministerio|secretaria de|alcaldia|gobernacion|municipalidad)\b"), "gobierno"),
    (re.compile(r"\b(universidad|instituto profesional|colegio|escuela)\b"), "educacion"),
)
TRUSTED = ("registro",)
# Marcas que también son palabras comunes: solo valen si son todo el nombre («Consorcio»,
# pero no «Consorcio Constructor Andino»).
COMMON_WORDS = frozenset(
    {
        "consorcio",
        "principal",
        "lider",
        "jumbo",
        "macro",
        "exito",
        "liberty",
        "positiva",
        "posadas",
        "vanti",
        "personal",
        "sura",
        "claro",
        "precision",
        "abastible",
    }
)


def _words(name: str) -> list[str]:
    return fold(name).replace("&", " ").split()


@lru_cache(maxsize=1)
def _brands() -> list[tuple[tuple[str, ...], str]]:
    out = [(tuple(brand.split()), code) for code, brands in KNOWN.items() for brand in brands]
    return sorted(out, key=lambda b: -len(b[0]))  # las marcas más largas primero


def known(name: str) -> str | None:
    """Sector de una empresa conocida. Marcas cortas o genéricas solo valen si son todo el
    nombre («TP México», «Consorcio»), para no confundir «Consorcio Constructor X»."""
    words = _words(name)
    core = [w for w in words if w not in NOISE]
    text = f" {' '.join(words)} "
    for brand, code in _brands():
        if len(brand) == 1:
            # Una sola palabra: debe encabezar el nombre («Claro Colombia» sí, «Forestal Río
            # Claro» no); las marcas cortas («TP», «EY») además deben ser todo el nombre.
            word = brand[0]
            if len(word) < 5 or (word in COMMON_WORDS and word != "claro"):
                if core == [word]:
                    return code
            elif core and (core[0] == word or (len(core) <= 2 and word in core)):
                return code
            continue
        if f" {' '.join(brand)} " in text:
            return code
    return None


def infer(name: str, domain: str | None = None) -> tuple[str | None, str]:
    """(sector, fuente) para una empresa, o (None, "")."""
    folded = " ".join(_words(name))
    for pattern, code in NAME_FIXES:
        if pattern.search(folded):
            return code, "nombre"
    curated = known(name)
    if curated:
        return curated, "curado"
    host = (domain or "").lower().strip(".")
    if host:
        if GOV_DOMAIN.search(host):
            return "gobierno", "dominio"
        if EDU_DOMAIN.search(host):
            return "educacion", "dominio"
    by_name = classify(name)
    if by_name:
        return by_name, "nombre"
    return None, ""


def from_description(text: str) -> str | None:
    """Sector a partir de lo que una empresa dice de sí misma en su sitio."""
    if not text:
        return None
    votes = Counter(c for c in (classify(part) for part in re.split(r"[.;|\n]", text)) if c)
    return votes.most_common(1)[0][0] if votes else None


def backfill(factory: sessionmaker[Session], *, force: bool = False) -> dict[str, int]:
    """Deduce el sector de las empresas del directorio que no lo tienen (o recalcula los
    deducidos con `force`). Nunca toca un sector que vino de un registro público."""
    counts: Counter[str] = Counter()
    with factory.begin() as session:
        cd = CompanyDirectory
        query = select(cd.key, cd.name, cd.domain, cd.industry_code, cd.industry_source)
        changes = []
        by_domain: dict[str, str] = {}
        rows = session.execute(query).all()
        for key, name, domain, code, source in rows:
            if code and (source in TRUSTED or not force):
                if domain:
                    by_domain.setdefault(domain, code)
                continue
            found, origin = infer(name, domain)
            if found:
                changes.append({"k": key, "c": found, "s": origin})
                counts[origin] += 1
                if domain:
                    by_domain.setdefault(domain, found)
        # Empresas con el mismo dominio comparten sector («3IT» = «3IT Ingeniería SPA»).
        pending = {c["k"] for c in changes}
        for key, _name, domain, code, _source in rows:
            if not code and key not in pending and domain and domain in by_domain:
                changes.append({"k": key, "c": by_domain[domain], "s": "mismo dominio"})
                counts["mismo dominio"] += 1
        if changes:
            from typing import cast

            from sqlalchemy import Table, bindparam

            table = cast(Table, CompanyDirectory.__table__)
            session.execute(
                update(table).where(table.c.key == bindparam("k"))
                .values(industry_code=bindparam("c"), industry_source=bindparam("s")),
                changes,
            )  # fmt: skip
    return dict(counts)


def coverage(session: Session) -> dict[str, int]:
    from sqlalchemy import func

    rows = session.execute(
        select(CompanyDirectory.industry_source, func.count())
        .where(CompanyDirectory.industry_code.is_not(None))
        .group_by(CompanyDirectory.industry_source)
    ).all()  # fmt: skip
    total = int(session.scalar(select(func.count()).select_from(CompanyDirectory)) or 0)
    return {"total": total, **{str(s or "registro"): int(n) for s, n in rows}}


LABELS = INDUSTRY_LABELS
