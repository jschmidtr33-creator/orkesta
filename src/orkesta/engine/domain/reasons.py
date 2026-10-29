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
    "COMMON_WORD_LABEL": "el dominio es una palabra común o un lugar, no el nombre completo",
    "SITE_PROTECTED": "hay un sitio web activo que bloquea visitas automáticas",
    "MODEL_DECISION": "decidido por el modelo propio entrenado",
    "PARKED_DOMAIN": "dominio aparcado o en venta",
    "REDIRECTS_TO_NON_CORPORATE": "redirige a una red social o directorio",
    "HISTORY": "reutilizado de una búsqueda reciente",
    "PATTERN_NAMED_MULTIPLE": "varios correos publicados de personas siguen el patrón",
    "PATTERN_NAMED_ONE": "un correo publicado de una persona sigue el patrón",
    "PATTERN_STRUCTURAL_MULTIPLE": "varios correos publicados tienen esa forma",
    "PATTERN_WEAK_EVIDENCE": "pocos correos publicados; patrón poco seguro",
    "PATTERN_PRIOR": "sin correos publicados: patrón más frecuente en empresas similares",
    "PATTERN_EXCEPTIONS": "algunos correos publicados usan otra forma",
    "EMAIL_DOMAIN_FROM_SITE": "el correo usa otro dominio que el sitio web",
    "NO_MX": "el dominio no recibe correo",
}


def describe_reasons(codes: list[str] | tuple[str, ...]) -> str:
    parts = []
    for code in codes:
        if code.startswith("REDIRECTS_TO:"):
            parts.append(f"redirige a {code.split(':', 1)[1]}")
        else:
            parts.append(REASON_LABELS.get(code, code))
    return "; ".join(parts)
