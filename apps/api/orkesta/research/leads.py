# ruff: noqa: E501  (tablas de cargos y ciudades)
"""Motor de búsqueda de leads v2 (IA propia de ORKESTA).

1. **Planificador de cargos**: convierte Nivel de responsabilidad + Función en los cargos reales
   que la gente escribe en su perfil, en español e inglés («Gerente de Compras», «Jefe de
   Abastecimiento», «Procurement Manager», «CPO»…).
2. **Abanico de consultas**: cada grupo de cargos se busca por separado y, si una consulta se
   satura (Google entrega como máximo ~100 resultados), se divide por ciudades principales.
3. **Cuentas → personas**: con un sector elegido, primero se encuentran empresas del sector
   (páginas de empresa y Google Maps, que traen su sitio web) y luego sus decisores.
4. **Correos**: dominio desde la memoria, Maps, el buscador o deduciéndolo y verificando el
   sitio; formato desde la memoria (aprendida de respuestas y rebotes), correos públicos o el
   formato más común. Todo en paralelo y con un tope de consultas por búsqueda.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.normalize.text import fold
from orkesta.research import serp
from orkesta.research.extract import looks_like_person, looks_like_title, make_person
from orkesta.research.fetch import Fetcher

# --- 1. Cargos -------------------------------------------------------------------------------

# función → (formas en español que siguen al nivel, sustantivo en inglés, cargos C propios)
FUNCTION_TITLES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "compras": (("de Compras", "de Abastecimiento", "de Procurement"), ("Procurement", "Purchasing"), ("CPO",)),
    "ti": (("de Tecnología", "de TI", "de Sistemas", "de IT"), ("IT", "Technology"), ("CIO", "CTO")),
    "ventas": (("Comercial", "de Ventas"), ("Sales",), ("CCO", "CSO")),
    "marketing": (("de Marketing", "de Mercadeo"), ("Marketing",), ("CMO",)),
    "finanzas": (("Financiero", "Financiera", "de Finanzas"), ("Finance",), ("CFO",)),
    "contabilidad": (("de Contabilidad", "Contable"), ("Accounting",), ()),
    "recursos_humanos": (("de Recursos Humanos", "de Talento Humano", "de Gestión Humana"), ("HR", "People"), ("CHRO",)),
    "operaciones": (("de Operaciones",), ("Operations",), ("COO",)),
    "legal": (("Jurídico", "Legal"), ("Legal",), ("General Counsel",)),
    "atencion_cliente": (("de Servicio al Cliente", "de Experiencia del Cliente"), ("Customer Experience", "Customer Service"), ("CXO",)),
    "administrativo": (("Administrativo", "Administrativa"), ("Administration",), ()),
    "desarrollo_empresarial": (("de Desarrollo de Negocios", "de Nuevos Negocios"), ("Business Development",), ()),
    "ingenieria": (("de Ingeniería",), ("Engineering",), ()),
    "proyectos": (("de Proyectos", "de PMO"), ("Projects", "PMO"), ()),
    "gestion_productos": (("de Producto",), ("Product",), ("CPO",)),
    "calidad": (("de Calidad",), ("Quality",), ()),
    "investigacion": (("de Investigación", "de Innovación"), ("Research", "Innovation"), ()),
    "medios_comunicacion": (("de Comunicaciones",), ("Communications",), ()),
    "arte_diseno": (("de Diseño", "Creativo"), ("Design", "Creative"), ()),
    "consultoria": (("de Consultoría",), ("Consulting",), ()),
    "educacion": (("Académico", "Académica"), ("Academic",), ("Rector",)),
    "servicios_sanitarios": (("Médico", "Médica"), ("Medical",), ()),
    "inmobiliaria": (("Inmobiliario",), ("Real Estate",), ()),
    "emprendimiento": ((), (), ("Fundador", "Founder", "Cofundador")),
    "servicios_sociales": (("de Sostenibilidad", "Social"), ("Sustainability",), ()),
    "militar_proteccion": (("de Seguridad",), ("Security",), ()),
}  # fmt: skip

# nivel → (cabezas en español, plantillas en inglés con {n} = sustantivo)
SENIORITY_HEADS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "vp": (("Vicepresidente", "Vicepresidenta"), ("VP {n}", "Vice President {n}")),
    "director": (("Director", "Directora"), ("{n} Director", "Head of {n}")),
    "gestion_experiencia": (("Gerente", "Subgerente"), ("{n} Manager",)),
    "gestion_inicial": (("Jefe", "Jefa", "Coordinador", "Coordinadora", "Líder"), ("{n} Lead", "{n} Supervisor")),
    "senior": (("Especialista", "Analista Senior"), ("Senior {n} Specialist",)),
    "sin_experiencia": (("Analista", "Asistente"), ("{n} Analyst",)),
    "practicas": (("Practicante", "Pasante"), ("{n} Intern",)),
}  # fmt: skip
GENERIC_SENIORITY: dict[str, tuple[str, ...]] = {
    "propietario": ("Fundador", "Socio", "Propietario", "Founder", "Owner", "Cofundador"),
    "cxo": ("Gerente General", "CEO", "Presidente", "Director General", "Country Manager", "Managing Director", "Director Ejecutivo"),
    "vp": ("Vicepresidente", "VP"),
    "director": ("Director", "Directora", "Head of"),
    "gestion_experiencia": ("Gerente", "Manager", "Subgerente"),
    "gestion_inicial": ("Jefe", "Coordinador", "Líder", "Supervisor"),
    "senior": ("Especialista", "Senior"),
    "sin_experiencia": ("Analista", "Asistente", "Junior"),
    "practicas": ("Practicante", "Pasante", "Intern"),
}  # fmt: skip
DEFAULT_SENIORITY = (
    "propietario",
    "cxo",
    "vp",
    "director",
    "gestion_experiencia",
    "gestion_inicial",
)


def title_phrases(seniority: list[str], functions: list[str], titles: list[str]) -> list[str]:
    """Cargos concretos a buscar, del más específico al más amplio."""
    if titles:
        return list(dict.fromkeys(titles))
    levels = seniority or list(DEFAULT_SENIORITY)
    out: list[str] = []
    if functions:
        for fn in functions:
            es_forms, en_nouns, c_titles = FUNCTION_TITLES.get(fn, ((), (), ()))
            for lv in levels:
                if lv == "cxo":
                    out += list(c_titles)
                    continue
                if lv == "propietario":
                    out += ["Fundador", "Founder"] if fn == "emprendimiento" else []
                    continue
                heads, en_templates = SENIORITY_HEADS.get(lv, ((), ()))
                out += [f"{h} {f}" for h in heads[:2] for f in es_forms]
                out += [t.format(n=n) for t in en_templates[:2] for n in en_nouns[:1]]
        if not out:  # funciones sin cargos propios: nivel genérico + palabra de la función
            out = [t for lv in levels for t in GENERIC_SENIORITY.get(lv, ())]
    else:
        out = [t for lv in levels for t in GENERIC_SENIORITY.get(lv, ())]
    return list(dict.fromkeys(out))


# --- 2. Ciudades para dividir consultas saturadas -------------------------------------------

CITIES: dict[str, tuple[str, ...]] = {
    "CO": ("Bogotá", "Medellín", "Cali", "Barranquilla", "Bucaramanga", "Cartagena"),
    "MX": ("Ciudad de México", "Monterrey", "Guadalajara", "Querétaro", "Puebla", "Tijuana"),
    "PE": ("Lima", "Arequipa", "Trujillo", "Piura"),
    "CL": ("Santiago", "Valparaíso", "Concepción", "Antofagasta"),
    "AR": ("Buenos Aires", "Córdoba", "Rosario", "Mendoza"),
    "EC": ("Quito", "Guayaquil", "Cuenca"),
    "BR": ("São Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Porto Alegre"),
    "PA": ("Panamá",),
    "CR": ("San José", "Heredia", "Alajuela"),
    "GT": ("Guatemala",),
    "DO": ("Santo Domingo", "Santiago de los Caballeros"),
    "UY": ("Montevideo",),
    "PY": ("Asunción",),
    "BO": ("La Paz", "Santa Cruz", "Cochabamba"),
    "ES": ("Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao"),
    "US": ("Miami", "New York", "Houston", "Los Angeles", "Chicago", "Dallas"),
}


# --- 3. Abanico de consultas con tope --------------------------------------------------------


@dataclass
class Plan:
    queries: list[str]
    splits: dict[str, list[str]] = field(default_factory=dict)  # consulta → variantes por ciudad


def _quoted(value: str) -> str:
    value = value.replace('"', "").strip()
    return f'"{value}"' if value else ""


def _group(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def plan_people(
    *, country: str, country_name: str, cities: list[str], phrases: list[str],
    sector_terms: list[str], keywords: str, companies: list[str], exclude: list[str],
    first_last: str = "",
) -> Plan:  # fmt: skip
    """Consultas al buscador: Y entre filtros, O dentro de cada uno, una por grupo de cargos y
    lugar; las saturadas se dividen después por ciudad."""
    common = []
    if sector_terms:
        common.append(
            "(" + " OR ".join(sector_terms[:6]) + ")" if len(sector_terms) > 1 else sector_terms[0]
        )
    if companies:
        common.append("(" + " OR ".join(_quoted(c) for c in companies[:5]) + ")")
    if first_last:
        common.append(_quoted(first_last))
    if keywords:
        common.append(keywords)
    common += [f"-{_quoted(t)}" for t in exclude[:6]]
    places = [_quoted(c) for c in cities] or [_quoted(country_name)]
    plan = Plan(queries=[])
    for chunk in _group(phrases, 3) or [[]]:
        roles = " OR ".join(_quoted(t) for t in chunk)
        roles = f"({roles})" if len(chunk) > 1 else roles
        for place in places:
            q = " ".join(f"site:linkedin.com/in {roles} {' '.join(common)} {place}".split())
            plan.queries.append(q)
            if not cities:
                plan.splits[q] = [
                    " ".join(
                        f"site:linkedin.com/in {roles} {' '.join(common)} {_quoted(city)}".split()
                    )
                    for city in CITIES.get(country, ())
                ]
    plan.queries = list(dict.fromkeys(plan.queries))
    return plan


@dataclass
class Hit:
    full_name: str
    first_name: str
    last_name: str
    title: str
    employer: str
    url: str
    snippet: str
    location: str
    domain: str = ""  # cuando la empresa vino de Maps


Accept = Callable[[str, str, str, str], bool]


class Collector:
    """Recorre las consultas en paralelo, página a página, hasta el objetivo o el tope."""

    def __init__(self, fetcher: Fetcher, stats: serp.SerpStats, *, country: str, country_name: str,
                 cities: list[str], accept: Accept, industries: list[str], excluded: set[str],
                 target: int, on_hit: Callable[[Hit], None] | None = None,
                 expired: Callable[[], bool] | None = None, max_calls: int = 0) -> None:  # fmt: skip
        self.fetcher, self.stats = fetcher, stats
        self.country, self.country_name, self.cities = country, country_name, cities
        self.accept, self.industries, self.excluded = accept, set(industries), excluded
        self.target, self.on_hit, self.expired = target, on_hit, expired
        self.max_calls = max_calls or stats.budget  # reserva el resto para dominios y correos
        self.found: dict[str, Hit] = {}
        self.lock = threading.Lock()

    def full(self) -> bool:
        return (
            len(self.found) >= self.target
            or bool(self.stats.fatal)
            or self.stats.calls >= min(self.max_calls, self.stats.budget)
            or bool(self.expired and self.expired())
        )

    def _row(self, row: dict[str, Any], *, company: str = "", domain: str = "") -> bool:
        from orkesta.engine.taxonomy.industries import classify_all

        link = str(row.get("link", "")).split("?")[0]
        if "linkedin.com/in/" not in link:
            return False
        title_text, snippet = str(row.get("title", "")), str(row.get("snippet", ""))
        name, role, employer = serp.parse_profile(title_text, snippet)
        if company and (not employer or not serp.same_employer(employer, company, domain)):
            if employer:
                return False
            employer = company  # X-ray de una empresa concreta: el perfil la menciona
        person = make_person(name, role) if looks_like_person(name, strict=False) else None
        if person is None or not employer or not role or not looks_like_title(role):
            return False
        text = f"{title_text} {snippet}"
        places = self.cities or [""]
        if not any(
            serp._location_ok(link, text, self.country, self.country_name, c) for c in places
        ):
            return False
        if (self.industries or self.excluded) and not company:
            codes = set(classify_all([employer, role, snippet]))
            if self.industries and codes and not codes & self.industries:
                return False
            if self.excluded and codes and codes <= self.excluded:
                return False
        if not self.accept(role, employer, person.first_name, person.last_name):
            return False
        key = fold(f"{person.full_name}|{employer}")
        hit = Hit(person.full_name, person.first_name, person.last_name, person.title, employer,
                  link, snippet, serp.lead_location(snippet, link, self.country, self.country_name),
                  domain)  # fmt: skip
        with self.lock:
            if key in self.found:
                return False
            self.found[key] = hit
        with self.stats.lock:
            self.stats.accepted += 1
        if self.on_hit:
            self.on_hit(hit)
        return True

    def run_query(
        self, query: str, *, max_pages: int = 10, company: str = "", domain: str = ""
    ) -> int:
        """Pagina una consulta; devuelve cuántas páginas llenas trajo (para dividir saturadas)."""
        gl = serp.COUNTRY_GL.get(self.country, "")
        full_pages = stale = 0
        for page in range(1, max_pages + 1):
            if self.full():
                break
            payload: dict[str, Any] = {"q": query, "num": 10, "hl": "es", "page": page}
            if gl:
                payload["gl"] = gl
            rows = (serp.call(self.fetcher, "search", payload, self.stats) or {}).get(
                "organic", []
            ) or []
            new = sum(
                1
                for r in rows
                if isinstance(r, dict) and self._row(r, company=company, domain=domain)
            )
            rejected = len(rows) - new
            if rejected:
                with self.stats.lock:
                    self.stats.rejected += rejected
            if len(rows) >= 10:
                full_pages += 1
            if len(rows) < 10:
                break
            stale = stale + 1 if new == 0 else 0
            if stale >= 2:  # dos páginas sin nada nuevo: la consulta ya no rinde
                break
        return full_pages

    def run_all(self, plan: Plan, workers: int = 6) -> None:
        saturated: list[str] = []

        def work(q: str) -> None:
            if self.full():
                return
            if self.run_query(q) >= 8:
                with self.lock:
                    saturated.append(q)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, plan.queries))
        extra = [s for q in saturated for s in plan.splits.get(q, [])]
        if extra and not self.full():
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda q: None if self.full() else self.run_query(q), extra))


# --- 4. Dominios ------------------------------------------------------------------------------

_LEGAL = re.compile(
    r"\b(?:s\.?a\.?s?|ltda|s\.?a\.?\s*de\s*c\.?v|inc|corp|llc|sa|cia|y\s+cia)\b\.?", re.I
)
COUNTRY_TLDS = {"CO": ("com.co", "co"), "MX": ("com.mx", "mx"), "PE": ("com.pe", "pe"), "CL": ("cl",),
                "AR": ("com.ar",), "EC": ("com.ec", "ec"), "BR": ("com.br",), "ES": ("es",),
                "PA": ("com.pa",), "CR": ("cr", "co.cr"), "UY": ("com.uy",), "GT": ("com.gt",),
                "DO": ("com.do",), "BO": ("com.bo",), "PY": ("com.py",)}  # fmt: skip


def guess_domain(fetcher: Fetcher, company: str, country: str) -> str:
    """Deduce el dominio probando nombre.com / nombre.com.co… y verifica que el sitio exista y
    mencione a la empresa (no gasta consultas al buscador)."""
    base = fold(_LEGAL.sub(" ", company))
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", base)
        if w not in {"de", "del", "la", "el", "y", "the", "grupo", "group"}
    ]
    if not words:
        return ""
    slugs = list(dict.fromkeys(["".join(words), "".join(words[:2]), words[0]]))
    slugs = [s for s in slugs if len(s) >= 4][:2]
    tlds = ("com", *COUNTRY_TLDS.get(country, ()))
    for slug in slugs:
        for tld in tlds:
            domain = f"{slug}.{tld}"
            page = fetcher.get(f"https://{domain}", timeout=6.0)
            if page is None or page.status >= 400:
                continue
            host = normalize_host(page.final_url) or domain
            final = registrable_domain(host) or domain
            if is_non_corporate(final):
                continue
            text = fold(page.text[:20000])
            if any(w in text for w in words if len(w) >= 4) or slug in fold(page.final_url):
                return final
    return ""
