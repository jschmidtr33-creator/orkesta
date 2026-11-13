"""Dominio oficial de una empresa sin buscadores pagos.

1. El directorio propio de ORKESTA (registros públicos y sitios ya rastreados).
2. Candidatos armados con el nombre («Alpina Productos Alimenticios» → alpina.com.co,
   alpina.com, alpinaproductos.com…) que se comprueban de verdad: que el dominio reciba correo
   (registro MX) y que su página principal mencione la empresa. Si nada se confirma, no se
   inventa un dominio.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.engine.normalize.company import normalize_company
from orkesta.engine.normalize.text import fold
from orkesta.research.fetch import Fetcher

TLDS: dict[str, tuple[str, ...]] = {
    "CO": ("com.co", "com", "co"), "MX": ("com.mx", "com", "mx"), "PE": ("com.pe", "pe", "com"),
    "CL": ("cl", "com"), "AR": ("com.ar", "com"), "EC": ("com.ec", "ec", "com"),
    "BR": ("com.br", "com"), "PA": ("com.pa", "com"), "CR": ("cr", "co.cr", "com"),
    "GT": ("com.gt", "com"), "DO": ("com.do", "do", "com"), "UY": ("com.uy", "uy", "com"),
    "PY": ("com.py", "com"), "BO": ("com.bo", "com"), "SV": ("com.sv", "com"),
    "HN": ("hn", "com"), "US": ("com",), "ES": ("es", "com"),
}  # fmt: skip
_STOP = {"de", "del", "la", "las", "el", "los", "y", "e", "s", "a", "sa", "sas", "ltda", "cia",
         "compania", "grupo", "corporacion", "inc", "sociedad", "anonima", "cv", "spa", "srl",
         "the", "and"}  # fmt: skip


def from_directory(factory: sessionmaker[Session], country: str, name: str) -> str:
    from orkesta.research.directory import name_key
    from orkesta.research.models import CompanyDirectory

    try:
        with factory() as session:
            return session.scalar(
                select(CompanyDirectory.domain).where(
                    CompanyDirectory.country == country,
                    CompanyDirectory.name_key == name_key(name),
                    CompanyDirectory.domain.is_not(None),
                ).limit(1)
            ) or ""  # fmt: skip
    except Exception:
        return ""


# Palabras que solas no identifican a nadie: banco.com no es «Banco de Occidente».
_GENERIC = {
    "banco", "industrias", "industria", "comercializadora", "distribuidora", "inversiones",
    "servicios", "soluciones", "constructora", "laboratorios", "clinica", "hotel", "transportes",
    "seguros", "consultores", "consultoria", "fundacion", "universidad", "colegio", "productos",
    "alimentos", "tecnologia", "global", "internacional",
}  # fmt: skip


def candidates(name: str, country: str) -> list[str]:
    base = normalize_company(name).display or name
    every = re.findall(r"[a-z0-9]+", fold(base))
    words = [w for w in every if w not in _STOP]
    if not words:
        return []
    labels = [] if words[0] in _GENERIC else [words[0]]
    full = "".join(w for w in every if w not in {"s", "a", "sa", "sas", "ltda"})[:40]
    labels += ["".join(words[:2]), full, "".join(words[:3]), "-".join(words[:2])]
    if len(words) >= 3:
        labels.append("".join(w[0] for w in words))  # siglas
    seen: list[str] = []
    for label in labels:
        if 3 <= len(label) <= 40:
            for tld in TLDS.get(country, ("com",)):
                host = f"{label}.{tld}"
                if host not in seen:
                    seen.append(host)
    return seen[:14]


def _mentions(text: str, name: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", fold(name)) if w not in _STOP and len(w) > 2]
    page = fold(text[:200_000])
    return bool(words) and sum(1 for w in words[:3] if w in page) >= min(2, len(words[:3]))


# Se reemplaza en las pruebas para no consultar DNS reales.
MX: Callable[[str], list[str]] | None = None


def guess(
    fetcher: Fetcher, name: str, country: str, mx: Callable[[str], list[str]] | None = None
) -> str:
    """El primer candidato que recibe correo y cuya página menciona a la empresa."""
    from orkesta.validation.checks import dns_mx

    resolve = mx or MX or dns_mx
    for host in candidates(name, country):
        try:
            receives_mail = bool(resolve(host))
        except Exception:
            receives_mail = False
        if not receives_mail:
            continue
        page = fetcher.get(f"https://{host}/", timeout=8) or fetcher.get(
            f"http://{host}/", timeout=8
        )
        if page is not None and page.status < 400 and _mentions(page.text, name):
            return host
    return ""
