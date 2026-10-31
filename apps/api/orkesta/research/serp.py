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
    if not role:
        # «Gerente General en Banco Andino · Experiencia: …» en el fragmento.
        match = _AT.match(clean(snippet).split("·")[0].split(". ")[0])
        if match and looks_like_title(match.group(1)):
            role = match.group(1)
            employer = employer or match.group(2)
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


def _location_ok(url: str, text: str, country: str, country_name: str, city: str) -> bool:
    host = urlsplit(url).hostname or ""
    sub = LINKEDIN_SUBDOMAIN.get(country, "")
    folded = fold(text)
    if city and fold(city) not in folded and not host.startswith(f"{sub}."):
        return False
    if sub and sub != "www" and host.startswith(f"{sub}."):
        return True
    return fold(country_name) in folded or bool(city and fold(city) in folded)


def people_queries(
    *, levels: list[str], areas: list[str], industries: list[str], keywords: str,
    country_name: str, city: str,
) -> list[str]:  # fmt: skip
    chosen = levels or ["c_level", "vp", "director", "gerente"]
    role_groups = [LEVEL_TERMS[lv] for lv in chosen if lv in LEVEL_TERMS]
    industry = list(dict.fromkeys(t for c in industries for t in INDUSTRY_TERMS.get(c, [])))[:5]
    area = list(dict.fromkeys(t for a in areas for t in AREA_TERMS.get(a, [])))[:5]
    place = f'"{city}"' if city else f'"{country_name}"'
    parts_common = []
    if industry:
        parts_common.append(f"({' OR '.join(industry)})")
    if area:
        parts_common.append(f"({' OR '.join(area)})")
    if keywords:
        parts_common.append(keywords)
    queries = []
    for roles in role_groups:
        roles = roles[:6]
        q = f"site:linkedin.com/in ({' OR '.join(roles)}) {' '.join(parts_common)} {place}"
        queries.append(" ".join(q.split()))
    return queries


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
    max_pages: int = 6,
) -> list[Lead]:
    """Leads de perfiles públicos que cumplen los filtros, de muchas empresas a la vez."""
    from orkesta.engine.taxonomy.industries import classify_all

    queries = people_queries(
        levels=levels, areas=areas, industries=industries, keywords=keywords,
        country_name=country_name, city=city,
    )  # fmt: skip
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
                ok = ok and _location_ok(link, f"{title_text} {snippet}", country, country_name,
                                         city)  # fmt: skip
                if ok and industries:
                    codes = classify_all([employer, role, snippet])
                    ok = not codes or bool(set(codes) & set(industries))
                if not ok or person is None:
                    with stats.lock:
                        stats.rejected += 1
                    continue
                key = fold(f"{person.full_name}|{employer}")
                if key not in found:
                    found[key] = Lead(person.full_name, person.first_name, person.last_name,
                                      person.title, employer, link, snippet)  # fmt: skip
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
_NAME_PAIR = re.compile(r"([A-ZÁÉÍÓÚÑ][a-záéíóúñü]+)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñü]+)")


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
            for first, last in _NAME_PAIR.findall(text):
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
