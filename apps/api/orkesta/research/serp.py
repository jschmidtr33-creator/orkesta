"""Buscador de Google por API (Serper.dev): el motor propio de ORKESTA sin depender de IA.

Tres usos, todos con resultados públicos de Google:

1. Decisores por «X-ray»: ``site:linkedin.com/in "Empresa" (gerente OR director …)``. El título
   público de cada perfil suele ser «Nombre – Cargo – Empresa | LinkedIn». No se entra a
   LinkedIn: solo se lee lo que Google muestra.
2. Empresas por industria y ciudad en Google Maps (``/places``), con su sitio web.
3. Páginas de equipo dentro del sitio oficial: ``site:dominio (equipo OR "junta directiva" …)``.

Cada consulta cuesta un crédito; las respuestas se guardan en caché para no pagar dos veces.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from orkesta.core.config import get_settings
from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.normalize.text import clean, fold
from orkesta.engine.taxonomy.titles import classify_title
from orkesta.research import fetch
from orkesta.research.extract import (
    clean_title,
    looks_like_person,
    looks_like_title,
    make_person,
)
from orkesta.research.fetch import Fetcher, Page

BASE = "https://google.serper.dev"
CACHE_TTL = timedelta(days=14)


def enabled() -> bool:
    return bool(get_settings().serper_api_key)


@dataclass
class SerpStats:
    calls: int = 0
    cached: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    fatal: str = ""  # «clave» o «creditos»
    budget: int = 400
    lock: threading.Lock = field(default_factory=threading.Lock)

    def error(self, kind: str) -> None:
        with self.lock:
            self.errors[kind] = self.errors.get(kind, 0) + 1

    def totals(self) -> dict[str, int]:
        return {
            "serp_calls": self.calls,
            "serp_cached": self.cached,
            "serp_accepted": self.accepted,
            "serp_rejected": self.rejected,
            **{f"serp_error_{k}": v for k, v in self.errors.items()},
            **({f"serp_fatal_{self.fatal}": 1} if self.fatal else {}),
        }


def call(
    fetcher: Fetcher, endpoint: str, payload: dict[str, Any], stats: SerpStats
) -> dict[str, Any] | None:
    key = get_settings().serper_api_key
    if not key or stats.fatal:
        return None
    digest = hashlib.sha256(json.dumps([endpoint, payload], sort_keys=True).encode()).hexdigest()
    cache_url = f"serper://{endpoint}/{digest}"
    cached = fetcher._cached(cache_url, CACHE_TTL)
    if cached is not None and cached.status == 200:
        try:
            data = json.loads(cached.text)
            with stats.lock:
                stats.cached += 1
            return data if isinstance(data, dict) else None
        except ValueError:
            pass
    with stats.lock:
        if stats.calls >= stats.budget:
            stats.errors["tope"] = stats.errors.get("tope", 0) + 1
            return None
        stats.calls += 1
    try:
        with httpx.Client(timeout=20.0, transport=fetch.TRANSPORT) as client:
            response = client.post(
                f"{BASE}/{endpoint}",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError:
        stats.error("red")
        return None
    text = response.text
    if response.status_code in (401, 403):
        lowered = text.lower()
        kind = "creditos" if "credit" in lowered else "clave"
        stats.error(kind)
        stats.fatal = kind
        return None
    if response.status_code == 429:
        stats.error("ritmo")
        return None
    if response.status_code != 200:
        stats.error(str(response.status_code))
        return None
    try:
        data = json.loads(text)
    except ValueError:
        stats.error("formato")
        return None
    fetcher._store(Page(cache_url, cache_url, 200, "application/json", text))
    return data if isinstance(data, dict) else None


# --- 1. Decisores por X-ray -----------------------------------------------------------------

LEVEL_TERMS = {
    "c_level": ['"gerente general"', "CEO", "presidente", "CFO", "CTO", "CIO", "COO", "CMO"],
    "duenio_socio": ["fundador", "socio", "founder", "owner"],
    "vp": ["vicepresidente", "VP"],
    "director": ["director", "directora"],
    "gerente": ["gerente", "manager"],
    "jefe": ["jefe", "head"],
}
AREA_TERMS = {
    "tecnologia": ["tecnología", "TI", "sistemas", "IT"],
    "seguridad": ["ciberseguridad", "seguridad", "CISO"],
    "datos": ["datos", "data", "analytics"],
    "transformacion_digital": ["digital", "innovación", "transformación"],
    "finanzas": ["finanzas", "financiero", "CFO"],
    "compras": ["compras", "procurement"],
    "supply_chain": ["logística", "supply chain", "cadena de suministro"],
    "comercial": ["comercial", "ventas", "sales"],
    "marketing": ["marketing", "mercadeo"],
    "rrhh": ["recursos humanos", "talento", "RRHH", "HR"],
    "legal": ["legal", "jurídico"],
    "operaciones": ["operaciones", "operations"],
    "atencion_cliente": ["servicio al cliente", "experiencia del cliente", "CX"],
    "direccion": ['"gerente general"', "CEO", "presidente"],
}
COUNTRY_GL = {
    "CO": "co", "MX": "mx", "PE": "pe", "CL": "cl", "AR": "ar", "EC": "ec", "VE": "ve",
    "BO": "bo", "PY": "py", "UY": "uy", "BR": "br", "PA": "pa", "CR": "cr", "GT": "gt",
    "HN": "hn", "SV": "sv", "NI": "ni", "DO": "do", "PR": "pr", "CU": "cu", "ES": "es",
    "US": "us", "CA": "ca", "PT": "pt", "GB": "uk",
}  # fmt: skip


@dataclass(frozen=True)
class ProfileHit:
    full_name: str
    first_name: str
    last_name: str
    title: str
    url: str
    employer: str


_SUFFIX = re.compile(r"\s*[|·–-]\s*linkedin.*$", re.IGNORECASE)
_SPLIT = re.compile(r"\s+[-–—|]\s+")
_AT = re.compile(r"^(.*?)\s+(?:en|at|@|de la empresa)\s+(.+)$", re.IGNORECASE)


_EXPERIENCE = re.compile(
    r"(?:Experiencia|Experience|Experiência)\s*:\s*([^·|\n]{2,80})", re.IGNORECASE
)
_SNIPPET_SPLIT = re.compile(r"\s*(?:·|\||\.\s|\n)\s*")


def _from_snippet(snippet: str, role: str, employer: str) -> tuple[str, str]:
    """Completa cargo y empresa con el fragmento de Google («Gerente de Compras en Banco
    Andino · Experiencia: Banco Andino · Ubicación: Bogotá»)."""
    text = clean(snippet)
    if not employer:
        match = _EXPERIENCE.search(text)
        if match:
            employer = clean(match.group(1)).strip(" .,")
    for segment in _SNIPPET_SPLIT.split(text)[:6]:
        segment = segment.strip(" .,")
        if not segment or ":" in segment[:20]:
            continue
        at = _AT.match(segment)
        if at and looks_like_title(at.group(1)):
            role = role or at.group(1)
            employer = employer or at.group(2)
            break
        if not role and looks_like_title(segment) and len(segment.split()) <= 8:
            role = segment
    return role, employer


def parse_profile(title: str, snippet: str) -> tuple[str, str, str]:
    """(nombre, cargo, empresa) del título de un perfil público en Google."""
    text = _SUFFIX.sub("", clean(title))
    parts = [p.strip() for p in _SPLIT.split(text) if p.strip()]
    if not parts:
        return "", "", ""
    name, role, employer = parts[0], "", ""
    rest = parts[1:]
    if len(rest) >= 2:
        role, employer = rest[0], rest[1]
    elif len(rest) == 1:
        match = _AT.match(rest[0])
        if match:
            role, employer = match.group(1), match.group(2)
        elif looks_like_title(rest[0]):
            role = rest[0]
        else:
            employer = rest[0]
    if not role or not employer:
        role, employer = _from_snippet(snippet, role, employer)
    return name, clean_title(role)[:200], clean(employer)[:200]


def _slug_matches(url: str, first: str, last: str) -> bool:
    path = urlsplit(url).path
    if "/in/" not in path:
        return False
    slug = fold(unquote(path.split("/in/", 1)[1]).replace("-", " "))
    tokens = [t for t in fold(f"{first} {last}").split() if len(t) > 2]
    return bool(tokens) and sum(t in slug.split() for t in tokens) >= min(2, len(tokens))


_COMPANY_STOP = frozenset(
    [
        "sa",
        "s",
        "a",
        "sas",
        "ltda",
        "inc",
        "corp",
        "corporation",
        "grupo",
        "group",
        "banco",
        "bank",
        "compania",
        "company",
        "de",
        "del",
        "la",
        "el",
        "y",
        "the",
        "cia",
        "holding",
    ]
)


def same_employer(employer: str, company: str, domain: str) -> bool:
    said = {w for w in fold(employer).split() if len(w) > 2 and w not in _COMPANY_STOP}
    target = {w for w in fold(company).split() if len(w) > 2 and w not in _COMPANY_STOP}
    label = fold(domain.split(".")[0]) if domain else ""
    if not said:
        return False
    if said & target:
        return True
    return bool(label) and any(w in label or label in w for w in said if len(w) > 3)


def xray_queries(company: str, levels: list[str], areas: list[str]) -> list[str]:
    roles = [t for lv in (levels or ["c_level", "vp", "director", "gerente"]) for t in
             LEVEL_TERMS.get(lv, [])]  # fmt: skip
    roles = list(dict.fromkeys(roles))[:10]
    queries = [f'site:linkedin.com/in "{company}" ({" OR ".join(roles)})']
    if areas:
        terms = list(dict.fromkeys(t for a in areas for t in AREA_TERMS.get(a, [])))[:6]
        if terms:
            queries.insert(0, f'site:linkedin.com/in "{company}" ({" OR ".join(terms)})')
    return queries


def decision_makers(
    fetcher: Fetcher,
    stats: SerpStats,
    *,
    company: str,
    domain: str,
    country: str,
    levels: list[str],
    areas: list[str],
) -> list[ProfileHit]:
    found: dict[str, ProfileHit] = {}
    gl = COUNTRY_GL.get(country, "")
    for query in xray_queries(company, levels, areas):
        payload: dict[str, Any] = {"q": query, "num": 20, "hl": "es"}
        if gl:
            payload["gl"] = gl
        data = call(fetcher, "search", payload, stats)
        if data is None:
            break
        for row in data.get("organic", []) or []:
            if not isinstance(row, dict):
                continue
            link = str(row.get("link", ""))
            if "linkedin.com/in/" not in link:
                continue
            name, role, employer = parse_profile(str(row.get("title", "")),
                                                 str(row.get("snippet", "")))  # fmt: skip
            person = make_person(name, role) if looks_like_person(name, strict=False) else None
            ok = (
                person is not None
                and bool(role)
                and looks_like_title(role)
                and same_employer(employer or str(row.get("snippet", "")), company, domain)
            )
            if not ok or person is None:
                with stats.lock:
                    stats.rejected += 1
                continue
            if not _slug_matches(link, person.first_name, person.last_name) and (
                classify_title(role)[0] is None
            ):
                with stats.lock:
                    stats.rejected += 1
                continue
            key = fold(person.full_name)
            if key not in found:
                found[key] = ProfileHit(person.full_name, person.first_name, person.last_name,
                                        person.title, link, employer)  # fmt: skip
                with stats.lock:
                    stats.accepted += 1
        if len(found) >= 8:
            break
    return list(found.values())


# --- 2. Empresas en Google Maps -------------------------------------------------------------

PLACE_TERMS = {
    "seguros": ["aseguradoras"],
    "banca_finanzas": ["bancos", "financieras"],
    "telecomunicaciones": ["empresas de telecomunicaciones"],
    "tecnologia_software": ["empresas de software", "empresas de tecnología"],
    "farmaceutica_salud": ["laboratorios farmacéuticos", "clínicas"],
    "alimentos_bebidas": ["empresas de alimentos", "empresas de bebidas"],
    "agroindustria": ["agroindustria"],
    "mineria_petroleo": ["empresas mineras", "empresas petroleras"],
    "energia": ["empresas de energía"],
    "automotriz": ["concesionarios de autos", "empresas automotrices"],
    "transporte_logistica": ["empresas de logística", "empresas de transporte de carga"],
    "construccion_inmobiliario": ["constructoras", "inmobiliarias"],
    "retail_consumo": ["cadenas de tiendas", "supermercados"],
    "textil_moda": ["empresas textiles"],
    "quimica_materiales": ["empresas químicas"],
    "manufactura_industrial": ["fábricas", "empresas manufactureras"],
    "medios_entretenimiento": ["medios de comunicación"],
    "turismo_hoteleria": ["hoteles", "agencias de viajes"],
    "educacion": ["universidades", "colegios privados"],
    "gobierno": ["entidades públicas"],
    "ong": ["fundaciones", "ONG"],
    "servicios_profesionales": ["firmas de consultoría", "firmas de abogados"],
}


@dataclass(frozen=True)
class PlaceHit:
    name: str
    domain: str
    website: str
    city: str
    category: str
    phone: str


def companies(
    fetcher: Fetcher,
    stats: SerpStats,
    *,
    country: str,
    country_name: str,
    industries: list[str],
    city: str,
    keywords: str,
    limit: int,
) -> list[PlaceHit]:
    terms = [t for code in industries for t in PLACE_TERMS.get(code, [])] or (
        [keywords] if keywords else ["empresas"]
    )
    if keywords and industries:
        terms = [f"{t} {keywords}" for t in terms]
    where = f"{city}, {country_name}" if city else country_name
    gl = COUNTRY_GL.get(country, "")
    found: dict[str, PlaceHit] = {}
    for term in terms[:4]:
        for page in (1, 2):
            payload: dict[str, Any] = {"q": f"{term} en {where}", "hl": "es", "page": page}
            if gl:
                payload["gl"] = gl
            data = call(fetcher, "places", payload, stats)
            if not data:
                break
            places = data.get("places", []) or []
            for place in places:
                if not isinstance(place, dict):
                    continue
                website = str(place.get("website", "") or "")
                host = normalize_host(website) if website else None
                domain = registrable_domain(host) if host else None
                if not domain or is_non_corporate(domain) or domain in found:
                    continue
                address = clean(str(place.get("address", "")))
                found[domain] = PlaceHit(
                    name=clean(str(place.get("title", "")))[:200],
                    domain=domain,
                    website=f"https://{host}",
                    city=city
                    or (address.split(",")[-2].strip() if address.count(",") >= 2 else ""),
                    category=clean(str(place.get("category", "")))[:120],
                    phone=clean(str(place.get("phoneNumber", "")))[:40],
                )
            if len(found) >= limit or len(places) < 10:
                break
        if len(found) >= limit:
            break
    return list(found.values())[:limit]


# --- 2b. Cuentas: páginas de empresa de LinkedIn según Google -------------------------------


@dataclass(frozen=True)
class AccountHit:
    name: str
    linkedin: str
    industry: str
    size: str
    about: str
    location: str


_SIZE_RE = re.compile(
    r"(?:Tamaño de la empresa|Company size|Tamanho da empresa)\s*:?\s*"
    r"(\d[\d.,]*)\s*(?:-\s*(\d[\d.,]*)|\+)?",
    re.IGNORECASE,
)
_INDUSTRY_RE = re.compile(r"(?:Sector|Industria|Industry|Setor)\s*:\s*([^·|\n.]{3,80})", re.I)
_FOLLOWERS = re.compile(r"\|?\s*[\d.,]+\s+(?:seguidores|followers)[^.|]*[.|]?", re.IGNORECASE)


def _band(low: str, high: str | None) -> str:
    from orkesta.engine.taxonomy.industries import SIZE_BANDS

    lo = int(re.sub(r"[.,]", "", low) or 0)
    for band, start, _ in SIZE_BANDS:
        if lo == start or (lo == 2 and start == 1):
            return band
    return f"{lo}+" if not high else ""


def account_queries(
    *, sector_terms: list[str], keywords: str, places: list[str], exclude: list[str]
) -> list[str]:
    common = [_any(sector_terms)]
    if keywords:
        common.append(keywords)
    common += [f"-{_quoted(t.strip(chr(34)))}" for t in exclude[:4]]
    return [
        " ".join(f"site:linkedin.com/company {' '.join(common)} {place}".split())
        for place in places
    ]


def linkedin_companies(
    fetcher: Fetcher,
    stats: SerpStats,
    *,
    country: str,
    country_name: str,
    cities: list[str],
    sector_terms: list[str],
    exclude_terms: list[str],
    keywords: str,
    max_pages: int = 10,
) -> list[AccountHit]:
    """Empresas con página en LinkedIn que cumplen los filtros (sin entrar a LinkedIn)."""
    places = [_quoted(c) for c in cities] or [_quoted(country_name)]
    queries = account_queries(sector_terms=sector_terms, keywords=keywords, places=places,
                              exclude=exclude_terms)  # fmt: skip
    gl = COUNTRY_GL.get(country, "")
    found: dict[str, AccountHit] = {}
    for query in queries:
        for page in range(1, max_pages + 1):
            if stats.fatal:
                return list(found.values())
            payload: dict[str, Any] = {"q": query, "num": 10, "hl": "es", "page": page}
            if gl:
                payload["gl"] = gl
            rows = (call(fetcher, "search", payload, stats) or {}).get("organic", []) or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                link = str(row.get("link", "")).split("?")[0].rstrip("/")
                if "linkedin.com/company/" not in link or link.count("/") > 4:
                    continue  # páginas internas (empleos, publicaciones)
                title, snippet = str(row.get("title", "")), str(row.get("snippet", ""))
                name = clean(_SUFFIX.sub("", title).split(":")[0].split(" - ")[0])[:200]
                if len(name) < 2:
                    continue
                text = f"{title} {snippet}"
                if not _location_ok(link, text, country, country_name, ""):
                    with stats.lock:
                        stats.rejected += 1
                    continue
                size_match = _SIZE_RE.search(snippet)
                industry_match = _INDUSTRY_RE.search(snippet)
                about = clean(_FOLLOWERS.sub(" ", snippet.replace(name, "", 1)).strip(" |·.-"))
                key = fold(name)
                if key in found:
                    continue
                found[key] = AccountHit(
                    name=name, linkedin=link,
                    industry=clean(industry_match.group(1)) if industry_match else "",
                    size=_band(size_match.group(1), size_match.group(2)) if size_match else "",
                    about=about[:400], location=lead_location(snippet, link, country, country_name),
                )  # fmt: skip
                with stats.lock:
                    stats.accepted += 1
            if len(rows) < 10:
                break
    return list(found.values())


# --- 3. Páginas de equipo en el sitio oficial -----------------------------------------------


def team_pages(fetcher: Fetcher, stats: SerpStats, domain: str) -> list[str]:
    query = (
        f'site:{domain} (equipo OR directivos OR "junta directiva" OR "gobierno corporativo" '
        'OR liderazgo OR "alta dirección" OR leadership OR "our team")'
    )
    data = call(fetcher, "search", {"q": query, "num": 10, "hl": "es"}, stats)
    if not data:
        return []
    links = []
    for row in data.get("organic", []) or []:
        link = str(row.get("link", "")) if isinstance(row, dict) else ""
        host = normalize_host(link) if link else None
        if host and registrable_domain(host) == domain and not link.lower().endswith(".pdf"):
            links.append(link)
    return links[:4]


# --- 4. Leads primero: personas por filtros, sin partir de una lista de empresas -------------

INDUSTRY_TERMS = {
    "seguros": ["seguros", "aseguradora", "insurance"],
    "banca_finanzas": ["banco", "bank", "financiera", "financiero", "fintech"],
    "telecomunicaciones": ["telecomunicaciones", "telecom", "telco"],
    "tecnologia_software": ["software", "tecnología", "technology", "SaaS"],
    "farmaceutica_salud": ["farmacéutica", "pharma", "salud", "hospital", "clínica"],
    "alimentos_bebidas": ["alimentos", "bebidas", "food", "beverages"],
    "agroindustria": ["agroindustria", "agrícola", "agro"],
    "mineria_petroleo": ["minería", "mining", "petróleo", "oil gas"],
    "energia": ["energía", "energy", "eléctrica"],
    "automotriz": ["automotriz", "automotive", "vehículos"],
    "transporte_logistica": ["logística", "logistics", "transporte"],
    "construccion_inmobiliario": ["construcción", "inmobiliaria", "real estate"],
    "retail_consumo": ["retail", "consumo masivo", "supermercados"],
    "textil_moda": ["textil", "moda", "fashion"],
    "quimica_materiales": ["química", "chemicals", "materiales"],
    "manufactura_industrial": ["manufactura", "manufacturing", "industrial"],
    "medios_entretenimiento": ["medios", "media", "entretenimiento"],
    "turismo_hoteleria": ["hotel", "turismo", "hospitality"],
    "educacion": ["universidad", "educación", "education"],
    "gobierno": ["gobierno", "ministerio", "government"],
    "ong": ["fundación", "ONG", "nonprofit"],
    "servicios_profesionales": ["consultoría", "consulting", "abogados"],
}
# Subdominio de LinkedIn por país (cr.linkedin.com = Costa Rica).
LINKEDIN_SUBDOMAIN = {**COUNTRY_GL, "US": "www"}


@dataclass(frozen=True)
class Lead:
    full_name: str
    first_name: str
    last_name: str
    title: str
    employer: str
    url: str
    snippet: str
    location: str = ""


_LOCATION_LABEL = re.compile(r"(?:Ubicación|Location|Localización)\s*:\s*([^·|\n]+)", re.IGNORECASE)


def lead_location(snippet: str, url: str, country: str, country_name: str) -> str:
    """«Medellín, Colombia» a partir del fragmento de Google (o el país del perfil)."""
    from orkesta.engine.normalize.location import parse_location

    candidates = [m.group(1) for m in _LOCATION_LABEL.finditer(snippet)]
    candidates += [part for part in re.split(r"[·|]", snippet) if len(part) < 80]
    for text in candidates:
        loc = parse_location(text)
        if loc.country_code and loc.country_code != country:
            continue
        if loc.city:
            return f"{loc.city}, {country_name}"
    host = urlsplit(url).hostname or ""
    sub = LINKEDIN_SUBDOMAIN.get(country, "")
    if (sub and host.startswith(f"{sub}.")) or fold(country_name) in fold(snippet):
        return country_name
    return ""


def _location_ok(url: str, text: str, country: str, country_name: str, city: str) -> bool:
    host = urlsplit(url).hostname or ""
    sub = LINKEDIN_SUBDOMAIN.get(country, "")
    folded = fold(text)
    if city and fold(city) not in folded and not host.startswith(f"{sub}."):
        return False
    if sub and sub != "www" and host.startswith(f"{sub}."):
        return True
    return fold(country_name) in folded or bool(city and fold(city) in folded)


@dataclass(frozen=True)
class LeadSpec:
    """Filtros de Sales Navigator que se traducen a la búsqueda de perfiles."""

    cities: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    exclude_sectors: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    exclude_titles: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()
    first_name: str = ""
    last_name: str = ""
    accept: Any = None  # Callable[[str, str, str, str], bool]: cargo, empresa, nombre, apellido

    @classmethod
    def from_filters(cls, f: Any) -> LeadSpec:
        def accept(title: str, employer: str, first: str, last: str) -> bool:
            return bool(
                f.wants_title(title) and f.wants_employer(employer) and f.wants_name(first, last)
            )

        return cls(
            cities=tuple(f.cities), sectors=tuple(f.sectors),
            exclude_sectors=tuple(f.exclude_sectors), seniority=tuple(f.seniority),
            functions=tuple(f.functions), titles=tuple(f.titles),
            exclude_titles=tuple(f.exclude_titles), companies=tuple(f.companies),
            exclude_companies=tuple(f.exclude_companies), first_name=f.first_name,
            last_name=f.last_name, accept=accept,
        )  # fmt: skip


def _quoted(value: str) -> str:
    value = value.replace('"', "").strip()
    return f'"{value}"' if value else ""


def _any(terms: list[str]) -> str:
    terms = [t for t in terms if t]
    if not terms:
        return ""
    return terms[0] if len(terms) == 1 else f"({' OR '.join(terms)})"


def people_queries(
    *, levels: list[str], areas: list[str], industries: list[str], keywords: str,
    country_name: str, city: str, spec: LeadSpec | None = None,
) -> list[str]:  # fmt: skip
    """Consultas al buscador con la lógica de Sales Navigator: Y entre filtros, O dentro de
    cada filtro y exclusiones con «-»."""
    from orkesta.engine.taxonomy import linkedin as li

    spec = spec or LeadSpec()
    if spec.titles:
        role_groups = [[_quoted(t) for t in spec.titles[:6]]]
    elif spec.seniority:
        role_groups = [li.seniority_terms(code)[:6] for code in spec.seniority]
    else:
        chosen = levels or ["c_level", "vp", "director", "gerente"]
        role_groups = [LEVEL_TERMS[lv][:6] for lv in chosen if lv in LEVEL_TERMS]
    if spec.sectors:
        industry = li.sector_terms(list(spec.sectors), 6)
    else:
        industry = list(dict.fromkeys(t for c in industries for t in INDUSTRY_TERMS.get(c, [])))[:5]
    if spec.titles:
        area: list[str] = []  # el cargo escrito ya dice la función
    elif spec.functions:
        area = li.function_terms(list(spec.functions), 6)
    else:
        area = list(dict.fromkeys(t for a in areas for t in AREA_TERMS.get(a, [])))[:5]
    cities = list(spec.cities) or ([city] if city else [])
    # Una consulta por ciudad: el buscador entrega hasta 100 resultados por consulta.
    places = [_quoted(c) for c in cities] or [_quoted(country_name)]
    common = [_any(industry), _any(area), _any([_quoted(c) for c in spec.companies[:5]])]
    name = " ".join(p for p in (spec.first_name, spec.last_name) if p)
    if name:
        common.append(_quoted(name))
    if keywords:
        common.append(keywords)
    excluded = [*spec.exclude_titles, *spec.exclude_companies]
    excluded += [li.SECTORS[c][2][0] for c in spec.exclude_sectors if c in li.SECTORS]
    common += [f"-{_quoted(t.strip(chr(34)))}" for t in excluded[:6]]
    queries = []
    for place in places:
        for roles in role_groups:
            q = f"site:linkedin.com/in {_any(roles)} {' '.join(common)} {place}"
            queries.append(" ".join(q.split()))
    return list(dict.fromkeys(queries))


def people_search(
    fetcher: Fetcher,
    stats: SerpStats,
    *,
    country: str,
    country_name: str,
    city: str,
    levels: list[str],
    areas: list[str],
    industries: list[str],
    keywords: str,
    target: int,
    max_pages: int = 10,
    spec: LeadSpec | None = None,
) -> list[Lead]:
    """Leads de perfiles públicos que cumplen los filtros, de muchas empresas a la vez."""
    from orkesta.engine.taxonomy.industries import classify_all
    from orkesta.engine.taxonomy.linkedin import sector_parents

    spec = spec or LeadSpec()
    queries = people_queries(
        levels=levels, areas=areas, industries=industries, keywords=keywords,
        country_name=country_name, city=city, spec=spec,
    )  # fmt: skip
    excluded_parents = set(sector_parents(list(spec.exclude_sectors))) - set(industries)
    cities = list(spec.cities) or ([city] if city else [])
    gl = COUNTRY_GL.get(country, "")
    found: dict[str, Lead] = {}
    exhausted: set[str] = set()
    for page in range(1, max_pages + 1):
        for query in queries:
            if query in exhausted or len(found) >= target or stats.fatal:
                continue
            payload: dict[str, Any] = {"q": query, "num": 10, "hl": "es", "page": page}
            if gl:
                payload["gl"] = gl
            data = call(fetcher, "search", payload, stats)
            rows = (data or {}).get("organic", []) or []
            if len(rows) < 5:
                exhausted.add(query)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                link = str(row.get("link", ""))
                title_text, snippet = str(row.get("title", "")), str(row.get("snippet", ""))
                if "linkedin.com/in/" not in link:
                    continue
                name, role, employer = parse_profile(title_text, snippet)
                person = make_person(name, role) if looks_like_person(name, strict=False) else None
                ok = person is not None and bool(role) and looks_like_title(role) and bool(employer)
                text = f"{title_text} {snippet}"
                ok = ok and (
                    any(_location_ok(link, text, country, country_name, c) for c in cities)
                    if cities
                    else _location_ok(link, text, country, country_name, "")
                )
                if ok and (industries or excluded_parents):
                    codes = set(classify_all([employer, role, snippet]))
                    if industries:
                        ok = not codes or bool(codes & set(industries))
                    if ok and excluded_parents and codes:
                        ok = not codes <= excluded_parents
                if ok and person is not None and spec.accept is not None:
                    ok = spec.accept(role, employer, person.first_name, person.last_name)
                if not ok or person is None:
                    with stats.lock:
                        stats.rejected += 1
                    continue
                key = fold(f"{person.full_name}|{employer}")
                if key not in found:
                    found[key] = Lead(
                        person.full_name, person.first_name, person.last_name, person.title,
                        employer, link, snippet,
                        lead_location(snippet, link, country, country_name),
                    )  # fmt: skip
                    with stats.lock:
                        stats.accepted += 1
        if len(found) >= target or len(exhausted) == len(queries):
            break
    return list(found.values())[:target]


SOCIAL = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "bloomberg.com", "crunchbase.com", "glassdoor.com", "indeed.com",
    "zoominfo.com", "rocketreach.co", "dnb.com", "tiktok.com", "computrabajo.com",
)  # fmt: skip


def _domain_like(domain: str, employer: str) -> bool:
    label = fold(domain.split(".")[0]).replace("-", "")
    words = [w for w in fold(employer).split() if len(w) > 2 and w not in _COMPANY_STOP]
    if not words:
        return False
    initials = "".join(w[0] for w in fold(employer).split() if w not in _COMPANY_STOP)
    return any(w in label or label in w for w in words if len(w) > 3) or (
        len(initials) >= 2 and label.startswith(initials)
    )


def company_domain(
    fetcher: Fetcher, stats: SerpStats, employer: str, country: str, country_name: str
) -> str:
    """Dominio oficial de una empresa a partir de su nombre (panel de Google o resultados)."""
    payload: dict[str, Any] = {"q": f'"{employer}" {country_name} sitio oficial', "num": 10}
    gl = COUNTRY_GL.get(country, "")
    if gl:
        payload["gl"] = gl
    data = call(fetcher, "search", payload, stats)
    if not data:
        return ""
    panel = data.get("knowledgeGraph") or {}
    candidates = [str(panel.get("website", ""))] if isinstance(panel, dict) else []
    candidates += [str(r.get("link", "")) for r in data.get("organic", []) or []
                   if isinstance(r, dict)]  # fmt: skip
    for i, url in enumerate(candidates):
        host = normalize_host(url) if url else None
        domain = registrable_domain(host) if host else None
        if not domain or is_non_corporate(domain) or any(domain.endswith(s) for s in SOCIAL):
            continue
        if (i == 0 and panel) or _domain_like(domain, employer):
            return domain
    return ""


EMAIL_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._%+\-]{0,63}@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24})")
_CAP_WORD = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+\b")


def email_pattern(fetcher: Fetcher, stats: SerpStats, domain: str) -> tuple[str, int, str]:
    """(patrón, cantidad de evidencias, URL de ejemplo) visto en correos públicos del dominio."""
    from orkesta.research.extract import GENERIC_LOCAL, detect_pattern

    data = call(fetcher, "search", {"q": f'"@{domain}"', "num": 20}, stats)
    if not data:
        return "", 0, ""
    votes: dict[str, int] = {}
    example = ""
    for row in data.get("organic", []) or []:
        if not isinstance(row, dict):
            continue
        text = f"{row.get('title', '')} {row.get('snippet', '')}"
        for match in EMAIL_RE.finditer(text):
            email = match.group(0).lower().strip(".")
            if not email.endswith("@" + domain):
                continue
            local = email.split("@", 1)[0]
            if local in GENERIC_LOCAL or any(ch.isdigit() for ch in local):
                continue
            pattern = ""
            words = _CAP_WORD.findall(text)
            # Pares consecutivos y salteados («Ana María Gómez»): el nombre suele estar cerca.
            pairs = [(words[i], words[j]) for i in range(len(words))
                     for j in (i + 1, i + 2) if j < len(words)]  # fmt: skip
            for first, last in pairs:
                pattern = detect_pattern(first, last, email) or ""
                if pattern:
                    break
            if not pattern and re.fullmatch(r"[a-z]{2,}\.[a-z]{2,}", local):
                pattern = "first.last"
            elif not pattern and re.fullmatch(r"[a-z]{2,}_[a-z]{2,}", local):
                pattern = "first_last"
            if pattern:
                votes[pattern] = votes.get(pattern, 0) + 1
                example = example or str(row.get("link", ""))
    if not votes:
        return "", 0, ""
    best = max(votes, key=lambda k: votes[k])
    return best, votes[best], example


# --- 5. Perfil público de una persona concreta (enriquecimiento) ---------------------------


@dataclass(frozen=True)
class Profile:
    url: str
    title: str
    location: str


def find_profile(
    fetcher: Fetcher,
    stats: SerpStats,
    *,
    first: str,
    last: str,
    company: str,
    country: str,
    country_name: str,
) -> Profile | None:
    """Perfil público de LinkedIn de una persona según Google (sin entrar a LinkedIn)."""
    name = f"{first} {last}".strip()
    if len(name.split()) < 2:
        return None
    query = f'site:linkedin.com/in "{name}"' + (f' "{company}"' if company else "")
    payload: dict[str, Any] = {"q": query, "num": 10, "hl": "es"}
    gl = COUNTRY_GL.get(country, "")
    if gl:
        payload["gl"] = gl
    data = call(fetcher, "search", payload, stats)
    if not data:
        return None
    wanted = {t for t in fold(name).split() if len(t) > 2}
    for row in data.get("organic", []) or []:
        if not isinstance(row, dict):
            continue
        link = str(row.get("link", ""))
        if "linkedin.com/in/" not in link:
            continue
        found_name, role, employer = parse_profile(str(row.get("title", "")),
                                                   str(row.get("snippet", "")))  # fmt: skip
        tokens = {t for t in fold(found_name).split() if len(t) > 2}
        if len(wanted & tokens) < min(2, len(wanted)):
            continue
        snippet = str(row.get("snippet", ""))
        if company and employer and not same_employer(employer, company, ""):
            continue
        with stats.lock:
            stats.accepted += 1
        location = lead_location(snippet, link, country, country_name) if country else ""
        return Profile(link, role if looks_like_title(role) else "", location)
    return None
