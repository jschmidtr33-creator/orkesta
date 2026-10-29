"""Textos legibles de los estados y códigos de razón de un dominio."""

from __future__ import annotations

DOMAIN_STATUS_LABELS: dict[str, str] = {
    "confirmed": "confirmado",
    "probable": "probable",
    "candidate": "candidato",
    "rejected": "descartado",
    "unknown": "no encontrado",
    "error": "error al buscar",
}

REASON_LABELS: dict[str, str] = {
    "DNS_RESOLVES": "el dominio existe",
    "DNS_NO_RESOLVE": "el dominio no existe",
    "HAS_MX": "recibe correo (MX)",
    "SITE_OK": "sitio web activo",
    "NAME_IN_TITLE": "el nombre de la empresa aparece en el título del sitio",
    "NAME_PARTIAL_IN_TITLE": "parte del nombre aparece en el título del sitio",
    "BRAND_IN_TITLE": "el dominio es la marca y aparece en el título del sitio",
    "NAME_IN_BODY": "el nombre aparece en el contenido del sitio",
    "WIKIDATA_WEBSITE": "Wikidata lo registra como sitio oficial",
    "WIKIDATA_COUNTRY_MATCH": "Wikidata ubica a la empresa en el país",
    "COUNTRY_SIGNALS": "el sitio menciona el país (dirección, teléfono, aviso legal)",
    "COUNTRY_TLD": "extensión del país",
    "OTHER_COUNTRY_TLD": "extensión de otro país",
    "LABEL_MATCHES_NAME": "el dominio coincide con el nombre",
    "EMBEDDED_IN_NAME": "el dominio aparece en el nombre de la empresa",
    "SHORT_GENERIC_NAME": "nombre corto con posibles homónimos",
    "ROBOTS_BLOCKED": "el sitio no permite su revisión automática",
    "PARKED_DOMAIN": "dominio aparcado o en venta",
    "REDIRECTS_TO_NON_CORPORATE": "redirige a una red social o directorio",
    "HISTORY": "reutilizado de una búsqueda reciente",
}


def describe_reasons(codes: list[str] | tuple[str, ...]) -> str:
    parts = []
    for code in codes:
        if code.startswith("REDIRECTS_TO:"):
            parts.append(f"redirige a {code.split(':', 1)[1]}")
        else:
            parts.append(REASON_LABELS.get(code, code))
    return "; ".join(parts)
