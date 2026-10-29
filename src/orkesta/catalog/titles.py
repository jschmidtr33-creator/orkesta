"""Cargo → nivel y área, en español e inglés (como "Seniority" y "Function" en Sales Navigator).

"Gerente de Compras", "Purchasing Manager" y "Jefe de Abastecimiento" quedan en el área
``compras``; "CIO", "Gerente de Sistemas" e "IT Director" en ``tecnologia``. Se clasifica por
palabras clave sobre el cargo plegado (sin tildes ni mayúsculas).
"""

from __future__ import annotations

import re
from functools import lru_cache

from orkesta.engine.normalize.text import fold

# Nivel → (nombre visible, palabras clave). Gana el primero de la lista que aparezca: los
# cargos de dirección general ("gerente general") van antes que "gerente".
LEVELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "c_level": (
        "C-Level",
        (
            "ceo",
            "cfo",
            "cio",
            "cto",
            "coo",
            "cmo",
            "cdo",
            "ciso",
            "cso",
            "chro",
            "cco",
            "cro",
            "cpo",
            "cxo",
            "caio",
            "chief",
            "presidente",
            "presidenta",
            "president",
            "gerente general",
            "director general",
            "directora general",
            "general manager",
            "managing director",
            "country manager",
            "director ejecutivo",
            "directora ejecutiva",
            "executive director",
            "rector",
            "rectora",
        ),
    ),
    "duenio_socio": (
        "Dueño / Socio / Fundador",
        (
            "fundador",
            "fundadora",
            "cofundador",
            "cofundadora",
            "founder",
            "co-founder",
            "cofounder",
            "socio",
            "socia",
            "partner",
            "propietario",
            "propietaria",
            "owner",
            "dueno",
            "duena",
        ),
    ),
    "vp": (
        "Vicepresidente",
        (
            "vicepresidente",
            "vicepresidenta",
            "vice president",
            "vicepresident",
            "vp",
            "svp",
            "evp",
            "avp",
        ),
    ),
    "director": (
        "Director",
        (
            "director",
            "directora",
            "head of",
            "head",
            "subdirector",
            "subdirectora",
            "directivo",
            "directiva",
        ),
    ),
    "gerente": (
        "Gerente",
        ("gerente", "manager", "subgerente", "gerencia", "mgr"),
    ),
    "jefe": (
        "Jefe / Líder",
        ("jefe", "jefa", "lider", "leader", "lead", "encargado", "encargada", "responsable"),
    ),
    "coordinador": (
        "Coordinador / Supervisor",
        (
            "coordinador",
            "coordinadora",
            "coordinator",
            "supervisor",
            "supervisora",
            "superintendente",
        ),
    ),
    "especialista": (
        "Especialista / Analista",
        (
            "especialista",
            "specialist",
            "analista",
            "analyst",
            "ingeniero",
            "ingeniera",
            "engineer",
            "consultor",
            "consultora",
            "consultant",
            "asesor",
            "asesora",
            "advisor",
            "ejecutivo",
            "ejecutiva",
            "executive",
            "arquitecto",
            "arquitecta",
            "architect",
            "desarrollador",
            "developer",
            "asistente",
            "assistant",
            "tecnico",
            "tecnica",
            "auxiliar",
            "auditor",
            "auditora",
            "abogado",
            "abogada",
            "kam",
            "comprador",
            "compradora",
            "buyer",
            "contador",
            "contadora",
            "docente",
            "profesor",
            "profesora",
            "representante",
            "officer",
            "oficial",
        ),
    ),
}

# Área → (nombre visible, palabras clave). Gana la palabra que aparece primero en el cargo;
# las áreas de ``FALLBACK_AREAS`` solo cuentan si no hay ninguna específica ("Founder & CTO" es
# tecnología, no dirección; "Jefe de Operaciones TI" es tecnología).
AREAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "seguridad": (
        "Ciberseguridad",
        (
            "ciso",
            "ciberseguridad",
            "cybersecurity",
            "seguridad de la informacion",
            "information security",
            "seguridad informatica",
            "infosec",
            "security",
            "seguridad tecnologica",
            "riesgo tecnologico",
        ),
    ),
    "datos": (
        "Datos y analítica",
        (
            "datos",
            "data",
            "analytics",
            "analitica",
            "business intelligence",
            "bi",
            "inteligencia de negocios",
            "big data",
            "cdo",
            "ciencia de datos",
            "data science",
        ),
    ),
    "tecnologia": (
        "Tecnología / TI",
        (
            "ti",
            "it",
            "tic",
            "tics",
            "cio",
            "cto",
            "tecnologia",
            "technology",
            "sistemas",
            "systems",
            "informatica",
            "infraestructura",
            "infrastructure",
            "software",
            "desarrollo de software",
            "aplicaciones",
            "applications",
            "cloud",
            "arquitectura",
            "architecture",
            "devops",
            "redes",
            "network",
            "telecomunicaciones",
            "soporte",
            "helpdesk",
            "ingenieria",
            "engineering",
            "developer",
            "desarrollador",
            "ia",
            "inteligencia artificial",
            "artificial intelligence",
            "ai",
        ),
    ),
    "transformacion_digital": (
        "Transformación digital e innovación",
        (
            "transformacion digital",
            "digital transformation",
            "innovacion",
            "innovation",
            "digital",
            "e-commerce",
            "ecommerce",
            "comercio electronico",
            "canales digitales",
            "omnicanal",
            "experiencia digital",
        ),
    ),
    "finanzas": (
        "Finanzas",
        (
            "cfo",
            "finanzas",
            "finance",
            "financial",
            "financiero",
            "financiera",
            "contralor",
            "contraloria",
            "controller",
            "controlling",
            "tesoreria",
            "tesorero",
            "treasury",
            "contabilidad",
            "contador",
            "accounting",
            "fiscal",
            "impuestos",
            "tax",
            "riesgos",
            "risk",
            "credito",
            "cobranza",
            "inversiones",
            "planeacion financiera",
            "fp&a",
        ),
    ),
    "compras": (
        "Compras y abastecimiento",
        (
            "compras",
            "procurement",
            "purchasing",
            "abastecimiento",
            "adquisiciones",
            "sourcing",
            "buyer",
            "comprador",
            "compradora",
            "suministros",
        ),
    ),
    "supply_chain": (
        "Logística y cadena de suministro",
        (
            "logistica",
            "logistics",
            "supply chain",
            "cadena de suministro",
            "distribucion",
            "almacen",
            "warehouse",
            "transporte",
            "inventarios",
            "comercio exterior",
            "aduanas",
        ),
    ),
    "comercial": (
        "Ventas y desarrollo de negocio",
        (
            "ventas",
            "sales",
            "comercial",
            "commercial",
            "business development",
            "desarrollo de negocios",
            "desarrollo de negocio",
            "cuentas",
            "account",
            "kam",
            "key account",
            "preventa",
            "presales",
            "canales",
            "channel",
            "alianzas",
            "partnerships",
            "bdr",
            "sdr",
            "revenue",
            "cro",
            "negocios",
            "business",
        ),
    ),
    "marketing": (
        "Marketing y comunicación",
        (
            "cmo",
            "marketing",
            "mercadotecnia",
            "mercadeo",
            "comunicacion",
            "communications",
            "marca",
            "brand",
            "publicidad",
            "advertising",
            "growth",
            "contenido",
            "content",
            "relaciones publicas",
            "public relations",
            "pr",
            "prensa",
        ),
    ),
    "rrhh": (
        "Recursos humanos",
        (
            "recursos humanos",
            "rrhh",
            "rh",
            "human resources",
            "hr",
            "chro",
            "talento",
            "talent",
            "people",
            "personas",
            "capacitacion",
            "training",
            "desarrollo organizacional",
            "nomina",
            "payroll",
            "reclutamiento",
            "recruiting",
            "cultura",
        ),
    ),
    "legal": (
        "Legal y cumplimiento",
        (
            "legal",
            "juridico",
            "juridica",
            "abogado",
            "abogada",
            "lawyer",
            "counsel",
            "compliance",
            "cumplimiento",
            "regulatorio",
            "regulatory",
            "gobierno corporativo",
        ),
    ),
    "operaciones": (
        "Operaciones y producción",
        (
            "coo",
            "operaciones",
            "operations",
            "produccion",
            "production",
            "planta",
            "plant",
            "manufactura",
            "manufacturing",
            "calidad",
            "quality",
            "mantenimiento",
            "maintenance",
            "procesos",
            "process",
            "proyectos",
            "projects",
            "pmo",
        ),
    ),
    "atencion_cliente": (
        "Atención y experiencia de cliente",
        (
            "servicio al cliente",
            "customer service",
            "atencion al cliente",
            "customer experience",
            "experiencia del cliente",
            "cx",
            "customer success",
            "contact center",
            "call center",
        ),
    ),
    "direccion": (
        "Dirección general",
        (
            "ceo",
            "gerente general",
            "director general",
            "directora general",
            "general manager",
            "managing director",
            "country manager",
            "presidente",
            "presidenta",
            "president",
            "director ejecutivo",
            "directora ejecutiva",
            "fundador",
            "founder",
            "propietario",
            "owner",
            "rector",
            "administracion",
            "administrativo",
            "administrativa",
        ),
    ),
}

# Palabras genéricas que solo deciden el área si no aparece ninguna otra.
FALLBACK_AREAS: dict[str, tuple[str, ...]] = {
    "operaciones": ("procesos", "process", "proyectos", "projects", "pmo"),
    "finanzas": ("auditoria", "auditor", "auditora", "audit"),
}
LOW_PRIORITY_AREAS = frozenset({"direccion", "operaciones"})

# Decisores: los cargos que importan para prospectar con ORKESTA.
DECISION_LEVELS: tuple[str, ...] = ("c_level", "duenio_socio", "vp", "director", "jefe")

LEVEL_LABELS = {code: label for code, (label, _) in LEVELS.items()}
AREA_LABELS = {code: label for code, (label, _) in AREAS.items()}


def _compile(
    table: dict[str, tuple[str, tuple[str, ...]]],
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    # Palabras completas (con plural opcional): "ti" no es "tienda", "it" no es "audit".
    return tuple(
        (
            code,
            re.compile(
                r"(?<![a-z0-9])(?:" + "|".join(re.escape(k) for k in keys) + r")s?(?![a-z0-9])"
            ),
        )
        for code, (_, keys) in table.items()
    )


_LEVEL_PATTERNS = _compile(LEVELS)
_AREA_PATTERNS = _compile(AREAS)
_FALLBACK_PATTERNS = _compile({code: ("", keys) for code, keys in FALLBACK_AREAS.items()})


def _prepare(title: str) -> str:
    # "Gerente de TI/Sistemas" → separar por barras y guiones; "C-Level" y "e-commerce" se
    # conservan porque sus claves llevan guion.
    text = " ".join(fold(title).replace("/", " ").replace("|", " ").replace(",", " ").split())
    # "Business partner" es un rol (HRBP), no un socio de la empresa.
    text = text.replace("business partner", "bp")
    # "Vice President" no es presidente.
    return text.replace("vice president", "vicepresident").replace(
        "vice presidente", "vicepresidente"
    )


@lru_cache(maxsize=50_000)
def classify_title(title: str | None) -> tuple[str | None, str | None]:
    """(nivel, área) de un cargo; ``None`` donde no se reconoce."""
    if not title:
        return None, None
    text = _prepare(title)
    level = next((code for code, p in _LEVEL_PATTERNS if p.search(text)), None)
    best: tuple[int, int, int, str] | None = None
    for order, (code, pattern) in enumerate(_AREA_PATTERNS):
        match = pattern.search(text)
        if match:
            key = (int(code in LOW_PRIORITY_AREAS), match.start(), order, code)
            if best is None or key < best:
                best = key
    area = best[3] if best else None
    if area is None:
        area = next((code for code, p in _FALLBACK_PATTERNS if p.search(text)), None)
    # Un C-Level sin área propia (CEO, presidente, gerente general) dirige la empresa.
    if level == "c_level" and area is None:
        area = "direccion"
    return level, area


def is_decision_maker(title: str | None) -> bool:
    """C-Level, dueño/socio/fundador, vicepresidente, director o jefe."""
    level, _ = classify_title(title)
    return level in DECISION_LEVELS
