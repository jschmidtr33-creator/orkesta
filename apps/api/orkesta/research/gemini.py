"""Búsqueda web con IA: Gemini con «Grounding with Google Search» (plan gratuito).

La IA solo propone; la evidencia la pone Google. Una persona se acepta únicamente si el texto
donde aparece está respaldado por resultados de búsqueda (``groundingSupports``) y se cita la
fuente. Si la respuesta no trae búsqueda que la respalde, se descarta completa: así no entran
nombres inventados.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from orkesta.core.config import get_settings
from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.normalize.text import clean, fold
from orkesta.research import fetch
from orkesta.research.extract import looks_like_person, looks_like_title, make_person
from orkesta.research.fetch import Fetcher, Page

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MIN_INTERVAL = 4.5  # plan gratuito: ~15 solicitudes por minuto
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


@dataclass(frozen=True)
class FoundCompany:
    name: str
    domain: str
    website: str
    url: str


def enabled() -> bool:
    return bool(get_settings().gemini_api_key)


def _wait() -> None:
    global _last
    with _lock:
        delay = _last + MIN_INTERVAL - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last = time.monotonic()


class Grounded:
    def __init__(self, text: str, chunks: list[dict[str, str]], supported: list[str]):
        self.text, self.chunks, self.supported = text, chunks, supported

    def backed(self, value: str) -> bool:
        """¿El valor aparece dentro de un fragmento respaldado por la búsqueda?"""
        needle = fold(value)
        return bool(needle) and any(needle in fold(s) for s in self.supported)

    def source_for(self, claimed: str) -> tuple[str, str]:
        """(url, etiqueta) a citar: la URL que dio el modelo si su dominio está entre los
        resultados de Google; si no, el primer resultado real de la búsqueda."""
        domains = {c.get("title", "").lower(): c for c in self.chunks}
        host = (normalize_host(claimed) or "") if claimed else ""
        reg = registrable_domain(host) if host else None
        if claimed.startswith("http") and reg and any(reg in d for d in domains):
            return claimed, f"Búsqueda web · {host.removeprefix('www.')}"
        chunk = self.chunks[0] if self.chunks else {"uri": "", "title": "Google"}
        return chunk.get("uri", ""), f"Búsqueda web · {chunk.get('title', 'Google')}"


def _parse(payload: dict[str, Any]) -> Grounded | None:
    try:
        candidate = payload["candidates"][0]
    except (KeyError, IndexError, TypeError):
        return None
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    meta = candidate.get("groundingMetadata") or {}
    chunks = [
        {"uri": str(c["web"].get("uri", "")), "title": str(c["web"].get("title", ""))}
        for c in meta.get("groundingChunks", [])
        if isinstance(c, dict) and isinstance(c.get("web"), dict)
    ]
    supported = [
        str(s.get("segment", {}).get("text", ""))
        for s in meta.get("groundingSupports", [])
        if isinstance(s, dict) and s.get("groundingChunkIndices")
    ]
    if not text or not chunks or not supported:
        return None  # sin búsqueda que lo respalde, no se usa
    return Grounded(text, chunks, supported)


def ask(fetcher: Fetcher, prompt: str) -> Grounded | None:
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    model = settings.gemini_model
    key = hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()
    cache_url = f"gemini://{model}/{key}"
    cached = fetcher._cached(cache_url, timedelta(days=5))
    if cached is not None:
        try:
            return _parse(json.loads(cached.text)) if cached.status == 200 else None
        except ValueError:
            return None
    _wait()
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }
    try:
        with httpx.Client(timeout=60.0, transport=fetch.TRANSPORT) as client:
            response = client.post(
                API.format(model=model),
                params={"key": settings.gemini_api_key},
                json=body,
            )
        status, text = response.status_code, response.text
    except httpx.HTTPError:
        return None
    if status == 429:
        return None  # cuota del plan gratuito agotada: no se guarda en caché
    if status != 200:
        return None  # clave inválida, cuota o error: no se guarda en caché
    fetcher._store(Page(cache_url, cache_url, status, "application/json", text))
    try:
        return _parse(json.loads(text))
    except ValueError:
        return None


def _rows(text: str, columns: int) -> list[list[str]]:
    """Líneas «a | b | c» de la respuesta (una por resultado)."""
    rows = []
    for line in text.replace("```", "").splitlines():
        line = line.strip().lstrip("-*•0123456789. ").strip()
        if line.count("|") < columns - 1:
            continue
        cells = [clean(c) for c in line.split("|")]
        rows.append(cells[:columns] + [""] * (columns - len(cells)))
    return rows


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
    *,
    company: str,
    domain: str,
    country: str,
    levels: list[str],
    areas: list[str],
) -> list[FoundPerson]:
    roles = "; ".join(ROLE_HINTS[lv] for lv in levels if lv in ROLE_HINTS) or (
        "CEO, presidente, gerente general, vicepresidentes, directores y gerentes de área"
    )
    area_text = f" Prioriza las áreas: {', '.join(areas)}." if areas else ""
    prompt = (
        f"Investiga en la web quiénes ocupan HOY cargos directivos en la empresa «{company}»"
        f"{f' (sitio {domain})' if domain else ''}, en {country}. Cargos de interés: {roles}."
        f"{area_text} Usa fuentes públicas: sitio oficial, informes anuales, notas de prensa, "
        "perfiles profesionales públicos y directorios. No incluyas personas que ya dejaron "
        "el cargo. Responde solo con una persona por línea, sin texto adicional, con el "
        "formato: Nombre Apellido | Cargo exacto | URL de la fuente. Si no encuentras a nadie "
        "con fuente, responde: NINGUNO."
    )
    grounded = ask(fetcher, prompt)
    if grounded is None:
        return []
    found: dict[str, FoundPerson] = {}
    for name, title, claimed in _rows(grounded.text, 3):
        if not looks_like_person(name, strict=False) or not looks_like_title(title):
            continue
        if not grounded.backed(name):
            continue
        person = make_person(name, title)
        if person is None:
            continue
        url, label = grounded.source_for(claimed)
        found.setdefault(
            fold(person.full_name),
            FoundPerson(
                person.full_name, person.first_name, person.last_name, person.title, url, label
            ),
        )
    return list(found.values())


def companies(
    fetcher: Fetcher,
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
        f"Lista hasta {limit} empresas reales que operan en {where} del sector {what}.{extra} "
        "Prioriza empresas medianas y grandes con sitio web propio. Responde solo con una "
        "empresa por línea, sin texto adicional, con el formato: Nombre | https://sitio-web"
    )
    grounded = ask(fetcher, prompt)
    if grounded is None:
        return []
    found: dict[str, FoundCompany] = {}
    for name, website in _rows(grounded.text, 2):
        host = normalize_host(website) if website else None
        domain = registrable_domain(host) if host else None
        if not name or not domain or is_non_corporate(domain) or not grounded.backed(name):
            continue
        url, _ = grounded.source_for(website)
        found.setdefault(domain, FoundCompany(name[:200], domain, f"https://{host}", url))
    return list(found.values())[:limit]


def short_host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")
