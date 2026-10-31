"""Búsqueda web con IA: Gemini con «Grounding with Google Search» (plan gratuito).

La IA propone; la evidencia la pone la web. Cada persona pasa por una escalera de verificación
y solo entra si supera un peldaño:

1. Su línea está respaldada por resultados de Google (``groundingSupports``)  → confianza 75.
2. La página que el modelo cita existe y menciona su nombre (ORKESTA la descarga) → 80.
3. El perfil público citado (LinkedIn) lleva su nombre en la dirección y ese dominio
   aparece en los resultados de búsqueda                                       → 60.

Lo que no supera ningún peldaño se descarta y se cuenta («sin evidencia»): no entran nombres
inventados. Las respuestas se leen aunque vengan en Markdown, tablas o prosa.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from orkesta.core.config import get_settings
from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.normalize.text import clean, fold
from orkesta.research import fetch
from orkesta.research.extract import (
    Document,
    looks_like_person,
    parse_html,
    people_in,
)
from orkesta.research.fetch import Fetcher, Page

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FALLBACK_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest")
MIN_INTERVAL = 4.5  # plan gratuito: ~15 solicitudes por minuto entre todas las corridas
_lock = threading.Lock()
_last = 0.0


@dataclass(frozen=True)
class FoundPerson:
    full_name: str
    first_name: str
    last_name: str
    title: str
    url: str
    source_label: str
    confidence: int
    linkedin: str = ""


@dataclass(frozen=True)
class FoundCompany:
    name: str
    domain: str
    website: str
    url: str


@dataclass
class AiStats:
    """Lo que pasó con la IA en una corrida, para mostrarlo y diagnosticar."""

    calls: int = 0
    answered: int = 0
    no_search: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    fatal: str = ""  # «clave» o «cuota»: no tiene sentido seguir preguntando
    # Tope de consultas por corrida para no gastar en una sola búsqueda la cuota diaria gratuita.
    budget: int = 70
    lock: threading.Lock = field(default_factory=threading.Lock)

    def error(self, kind: str) -> None:
        with self.lock:
            self.errors[kind] = self.errors.get(kind, 0) + 1

    def totals(self) -> dict[str, int]:
        return {
            "ai_calls": self.calls,
            "ai_answered": self.answered,
            "ai_no_search": self.no_search,
            "ai_accepted": self.accepted,
            "ai_rejected": self.rejected,
            **{f"ai_error_{k}": v for k, v in self.errors.items()},
            **({f"ai_fatal_{self.fatal}": 1} if self.fatal else {}),
        }


def enabled() -> bool:
    return bool(get_settings().gemini_api_key)


def _wait() -> None:
    global _last
    with _lock:
        delay = _last + MIN_INTERVAL - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last = time.monotonic()


# --- Respuesta con respaldo de búsqueda ------------------------------------------------------


@dataclass
class Grounded:
    text: str
    chunks: list[dict[str, str]]
    # (texto del fragmento, índices de los resultados que lo respaldan)
    supports: list[tuple[str, list[int]]]

    def support_for(self, value: str) -> dict[str, str] | None:
        """El resultado de Google que respalda el fragmento donde aparece ``value``."""
        needle = fold(value)
        if not needle:
            return None
        for text, indices in self.supports:
            if needle in fold(text):
                for i in indices:
                    if 0 <= i < len(self.chunks):
                        return self.chunks[i]
        return None

    def domain_in_results(self, url: str) -> bool:
        host = normalize_host(url) if url else None
        reg = registrable_domain(host) if host else None
        if not reg:
            return False
        return any(
            reg in c.get("title", "").lower() or reg in c.get("uri", "") for c in self.chunks
        )


def _parse(payload: dict[str, Any]) -> tuple[Grounded | None, str]:
    """(respuesta, motivo si no sirve)."""
    try:
        candidate = payload["candidates"][0]
    except (KeyError, IndexError, TypeError):
        return None, "vacia"
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    meta = candidate.get("groundingMetadata") or {}
    chunks = [
        {"uri": str(c["web"].get("uri", "")), "title": str(c["web"].get("title", ""))}
        for c in meta.get("groundingChunks", [])
        if isinstance(c, dict) and isinstance(c.get("web"), dict)
    ]
    supports = [
        (str(s.get("segment", {}).get("text", "")), [int(i) for i in s["groundingChunkIndices"]])
        for s in meta.get("groundingSupports", [])
        if isinstance(s, dict) and s.get("groundingChunkIndices")
    ]
    if not text:
        return None, "vacia"
    if not chunks:
        return None, "sin_busqueda"  # el modelo respondió de memoria: no se usa
    return Grounded(text, chunks, supports), ""


def _models() -> list[str]:
    configured = get_settings().gemini_model
    return list(dict.fromkeys([configured, *FALLBACK_MODELS]))


def ask(fetcher: Fetcher, prompt: str, stats: AiStats) -> Grounded | None:
    settings = get_settings()
    if not settings.gemini_api_key or stats.fatal:
        return None
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }
    for model in _models():
        key = hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()
        cache_url = f"gemini://{model}/{key}"
        cached = fetcher._cached(cache_url, timedelta(days=5))
        if cached is not None and cached.status == 200:
            try:
                grounded, _ = _parse(json.loads(cached.text))
            except ValueError:
                grounded = None
            if grounded is not None:
                return grounded
        with stats.lock:
            if stats.calls >= stats.budget:
                stats.errors["tope"] = stats.errors.get("tope", 0) + 1
                return None
            stats.calls += 1
        _wait()
        try:
            with httpx.Client(timeout=60.0, transport=fetch.TRANSPORT) as client:
                response = client.post(
                    API.format(model=model),
                    params={"key": settings.gemini_api_key},
                    json=body,
                )
        except httpx.HTTPError:
            stats.error("red")
            return None
        status, text = response.status_code, response.text
        lowered = text.lower()
        if status in (400, 401, 403) and ("api key" in lowered or "permission" in lowered):
            stats.error("clave")
            stats.fatal = "clave"
            return None
        if status == 404 or (status == 400 and "model" in lowered):
            stats.error("modelo")
            continue  # el modelo no existe o no admite búsqueda: probar el siguiente
        if status == 429:
            stats.error("cuota")
            continue  # cuota de este modelo agotada: probar el siguiente
        if status != 200:
            stats.error(str(status))
            return None
        try:
            grounded, reason = _parse(json.loads(text))
        except ValueError:
            stats.error("formato")
            return None
        with stats.lock:
            stats.answered += 1
            if reason == "sin_busqueda":
                stats.no_search += 1
        if grounded is not None:
            fetcher._store(Page(cache_url, cache_url, 200, "application/json", text))
        return grounded
    if stats.errors.get("cuota"):
        stats.fatal = "cuota"
    return None


# --- Lectura tolerante de la respuesta -------------------------------------------------------

_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://[^\s)\]|>,;\"']+")
_CITATION = re.compile(r"\[(?:cite:\s*)?[\d,\s]+\]|\[\^?\d+\]")
_LIST_MARK = re.compile(r"^\s*(?:[-*•·]+|\d+[.)])\s*")


_GENERIC_LABELS = frozenset(
    {
        "fuente", "fuentes", "source", "link", "enlace", "ver", "aqui", "here", "sitio", "web",
        "perfil", "nota", "articulo", "linkedin", "pagina", "pdf", "informe", "noticia",
        "comunicado",
    }
)  # fmt: skip


def _link_label(match: re.Match[str]) -> str:
    """Conserva el texto de un enlace Markdown salvo que sea genérico («fuente», «LinkedIn»)."""
    label = match.group(1)
    if label.startswith("http") or fold(label) in _GENERIC_LABELS or not label.strip():
        return ""
    return label


def answer_lines(text: str) -> list[tuple[str, list[str]]]:
    """(línea limpia, URLs de la línea). Quita Markdown, viñetas, citas y tablas."""
    return [(line, urls) for line, urls, _ in answer_rows(text)]


def answer_rows(text: str) -> list[tuple[str, list[str], str]]:
    """Como ``answer_lines`` y, además, la tercera columna («Empresa») si la hay."""
    out: list[tuple[str, list[str], str]] = []
    for raw in text.replace("```", "\n").splitlines():
        line = raw.strip()
        if not line or set(line) <= set("|-: "):
            continue  # vacía o separador de tabla
        urls = [m.group(2) for m in _MD_LINK.finditer(line)]
        line = _MD_LINK.sub(_link_label, line)
        urls += _URL.findall(line)
        line = _URL.sub("", line)
        line = _CITATION.sub("", line).replace("**", "").replace("__", "").replace("`", "")
        line = _LIST_MARK.sub("", line.strip())
        cells = [c.strip(" .:-–—") for c in line.strip("| ").split("|")]
        cells = [c for c in cells if c]
        # «Nombre | Cargo | …» → «Nombre – Cargo» para el lector de personas.
        line = " – ".join(cells[:2]) if len(cells) >= 2 else (cells[0] if cells else "")
        line = clean(line.replace("(", " ").replace(")", " "))
        employer = clean(cells[2]) if len(cells) >= 3 else ""
        if line:
            out.append((line, [u.rstrip(".") for u in urls], employer))
    return out


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
    """¿La empresa que el modelo escribió junto a la persona es la que se investiga?"""
    if not employer:
        return True  # sin columna de empresa no se puede contradecir
    said = {w for w in fold(employer).split() if len(w) > 2 and w not in _COMPANY_STOP}
    target = {w for w in fold(company).split() if len(w) > 2 and w not in _COMPANY_STOP}
    label = fold(domain.split(".")[0]) if domain else ""
    if not said:
        return True
    return bool(said & target) or bool(label and any(w in label or label in w for w in said))


def _linkedin_matches(url: str, first: str, last: str) -> bool:
    parts = urlsplit(url)
    if "linkedin.com" not in (parts.hostname or "") or "/in/" not in parts.path:
        return False
    slug = fold(unquote(parts.path.split("/in/", 1)[1]).replace("-", " "))
    tokens = [t for t in fold(f"{first} {last}").split() if len(t) > 2]
    return sum(1 for t in tokens if t in slug.split()) >= min(2, len(tokens))


def _page_mentions(fetcher: Fetcher, url: str, name: str) -> bool:
    if not url.startswith("http") or "linkedin.com" in url:
        return False
    page = fetcher.get(url, respect_robots=True)
    if page is None or not page.ok:
        return False
    if "html" in (page.content_type or "text/html"):
        text = " ".join(parse_html(page.text).lines)
    else:
        text = page.text
    folded = fold(text)
    tokens = fold(name).split()
    return fold(name) in folded or (len(tokens) >= 2 and f"{tokens[0]} {tokens[-1]}" in folded)


def _site_matches(fetcher: Fetcher, url: str, name: str) -> bool:
    """El sitio existe y su título o nombre de sitio coincide con la empresa."""
    page = fetcher.get(url, respect_robots=True)
    if page is None or not page.ok:
        return False
    doc = parse_html(page.text)
    label = fold(f"{doc.title} {doc.site_name}")
    words = [w for w in fold(name).split() if len(w) > 3]
    return bool(words) and any(w in label for w in words)


ROLE_HINTS = {
    "c_level": "CEO, presidente, gerente general, CFO, CTO, CIO, COO, CMO",
    "duenio_socio": "fundadores, socios, dueños",
    "vp": "vicepresidentes",
    "director": "directores de área",
    "gerente": "gerentes de área",
    "jefe": "jefes de área",
}


def decision_makers(
    fetcher: Fetcher,
    stats: AiStats,
    *,
    company: str,
    domain: str,
    country: str,
    levels: list[str],
    areas: list[str],
    variant: int = 0,
) -> list[FoundPerson]:
    roles = "; ".join(ROLE_HINTS[lv] for lv in levels if lv in ROLE_HINTS) or (
        "CEO, presidente, gerente general, vicepresidentes, directores y gerentes de área"
    )
    area_text = f" Prioriza las áreas: {', '.join(areas)}." if areas else ""
    site = f" ({domain})" if domain else ""
    if variant == 0:
        prompt = (
            f"Busca en Google quiénes ocupan actualmente cargos directivos en «{company}»{site}, "
            f"{country}. Cargos de interés: {roles}.{area_text} Revisa el sitio oficial, "
            "informes anuales, notas de prensa, directorios empresariales y perfiles "
            "profesionales públicos. Excluye a quienes ya dejaron el cargo. Entrega hasta 15 "
            "personas, una por línea, sin introducción, con este formato exacto:\n"
            "Nombre Apellido | Cargo | Empresa | URL de la página donde aparece\n"
            "Si no encuentras a nadie con fuente pública, escribe solo: NINGUNO"
        )
    else:
        # Segundo intento con otra formulación (en inglés encuentra notas y perfiles distintos).
        prompt = (
            f"Search Google for the current leadership team and senior managers of "
            f"«{company}»{site} in {country} (CEO, general manager, CFO, COO, CTO, CIO, CMO, "
            "VPs, directors). Use the company website, press releases, news and public "
            "professional profiles. Up to 15 people, one per line, no intro, exactly:\n"
            "Full Name | Job title | Company | URL where it appears\n"
            "If none is found with a public source, write only: NONE"
        )
    grounded = ask(fetcher, prompt, stats)
    if grounded is None:
        return []
    found: dict[str, FoundPerson] = {}
    for line, urls, employer in answer_rows(grounded.text):
        if not same_employer(employer, company, domain):
            if people_in(Document(lines=[line])):
                with stats.lock:
                    stats.rejected += 1
            continue
        for person in people_in(Document(lines=[line])):
            key = fold(person.full_name)
            if key in found:
                continue
            verdict = _verify(fetcher, grounded, person.full_name, person.first_name,
                              person.last_name, urls)  # fmt: skip
            with stats.lock:
                if verdict is None:
                    stats.rejected += 1
                    continue
                stats.accepted += 1
            url, label, confidence = verdict
            linkedin = next((u for u in urls if "linkedin.com/in/" in u), "")
            if linkedin and not _linkedin_matches(linkedin, person.first_name, person.last_name):
                linkedin = ""
            found[key] = FoundPerson(
                person.full_name, person.first_name, person.last_name, person.title,
                url, label, confidence, linkedin,
            )  # fmt: skip
    return list(found.values())


def _verify(
    fetcher: Fetcher,
    grounded: Grounded,
    full_name: str,
    first: str,
    last: str,
    urls: list[str],
) -> tuple[str, str, int] | None:
    chunk = grounded.support_for(full_name) or grounded.support_for(f"{first} {last}")
    if chunk is not None:
        claimed = next((u for u in urls if grounded.domain_in_results(u)), "")
        if claimed:
            return claimed, f"Búsqueda web · {short_host(claimed)}", 75
        return chunk.get("uri", ""), f"Búsqueda web · {chunk.get('title', 'Google')}", 75
    for url in urls[:2]:
        if _page_mentions(fetcher, url, full_name):
            return url, f"Página citada · {short_host(url)}", 80
    for url in urls:
        if _linkedin_matches(url, first, last) and grounded.domain_in_results(url):
            return url, "Perfil profesional público", 60
    return None


def companies(
    fetcher: Fetcher,
    stats: AiStats,
    *,
    country: str,
    industries: list[str],
    city: str,
    keywords: str,
    limit: int,
) -> list[FoundCompany]:
    what = ", ".join(industries) if industries else "cualquier sector"
    where = f"{city}, {country}" if city else country
    extra = f" Relacionadas con: {keywords}." if keywords else ""
    prompt = (
        f"Busca en Google hasta {limit} empresas reales que operan en {where}, sector {what}."
        f"{extra} Prioriza empresas medianas y grandes con sitio web propio. Una por línea, sin "
        "introducción, con este formato exacto:\nNombre de la empresa | https://sitio-web-oficial"
    )
    grounded = ask(fetcher, prompt, stats)
    if grounded is None:
        return []
    found: dict[str, FoundCompany] = {}
    for line, urls in answer_lines(grounded.text):
        name = clean(line.split(" – ")[0])
        website = next((u for u in urls if not is_non_corporate(registrable_domain(
            normalize_host(u) or "") or "")), "")  # fmt: skip
        host = normalize_host(website) if website else None
        domain = registrable_domain(host) if host else None
        if not name or not domain or len(name) > 120 or looks_like_person(name):
            continue
        backed = grounded.support_for(name) is not None or grounded.domain_in_results(website)
        if not backed and not _site_matches(fetcher, f"https://{host}/", name):
            continue
        found.setdefault(domain, FoundCompany(name[:200], domain, f"https://{host}", website))
    return list(found.values())[:limit]


def short_host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")
