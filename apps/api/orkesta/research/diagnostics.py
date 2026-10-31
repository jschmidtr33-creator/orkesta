"""Prueba en vivo de las conexiones que usa el motor de prospección y validación.

Sirve para saber, desde la propia app y sin acceso al servidor, qué fuente está fallando.
"""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from orkesta.core.config import get_settings
from orkesta.research import fetch
from orkesta.research.fetch import USER_AGENT
from orkesta.research.gemini import API, _models, _parse

Check = dict[str, Any]


def _client(timeout: float = 12.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        transport=fetch.TRANSPORT,
    )


def _timed(name: str, fn: Callable[[], tuple[bool, str]]) -> Check:
    start = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:200]
    return {"name": name, "ok": ok, "detail": detail, "ms": int((time.monotonic() - start) * 1000)}


def _wikidata() -> tuple[bool, str]:
    with _client() as c:
        r = c.get(
            "https://query.wikidata.org/sparql",
            params={"query": "ASK { wd:Q739 wdt:P31 wd:Q6256 }", "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
    return r.status_code == 200, f"HTTP {r.status_code}"


def _sites() -> tuple[bool, str]:
    with _client() as c:
        r = c.get("https://www.wikipedia.org/")
    return r.status_code == 200, f"HTTP {r.status_code}"


def _gdelt() -> tuple[bool, str]:
    with _client(20) as c:
        r = c.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": '"Ecopetrol" presidente',
                "mode": "artlist",
                "format": "json",
                "maxrecords": "5",
            },
        )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    try:
        n = len(json.loads(r.text).get("articles", []))
    except ValueError:
        return False, "respuesta no válida (posible límite de ritmo)"
    return True, f"{n} noticias de prueba"


def _gemini() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.gemini_api_key:
        return False, "Falta GEMINI_API_KEY en Render"
    body = {
        "contents": [{"role": "user", "parts": [{"text":
            "Busca en Google quién es el CEO actual de Ecopetrol. Responde en una línea."}]}],
        "tools": [{"google_search": {}}],
    }  # fmt: skip
    last = ""
    for model in _models():
        with _client(60) as c:
            r = c.post(API.format(model=model), params={"key": settings.gemini_api_key}, json=body)
        if r.status_code == 200:
            try:
                grounded, reason = _parse(json.loads(r.text))
            except ValueError:
                return False, f"{model}: respuesta no válida"
            if grounded is None:
                return False, f"{model}: respondió sin búsqueda de Google ({reason})"
            return True, f"{model}: búsqueda con {len(grounded.chunks)} resultados de Google"
        text = r.text.lower()
        if "api key" in text or r.status_code in (401, 403):
            return False, "La clave GEMINI_API_KEY no es válida"
        last = f"{model}: HTTP {r.status_code}" + (
            " (cuota agotada)" if r.status_code == 429 else ""
        )
    return False, last


def _smtp() -> tuple[bool, str]:
    try:
        with socket.create_connection(("gmail-smtp-in.l.google.com", 25), timeout=6) as conn:
            banner = conn.recv(120).decode(errors="replace").strip()
        return banner.startswith("220"), "Puerto 25 abierto: se pueden confirmar buzones"
    except OSError:
        return False, "Puerto 25 bloqueado: los buzones quedarán «Sin confirmar»"


def run_all() -> list[Check]:
    checks = {
        "Wikidata (catálogo de empresas y directivos)": _wikidata,
        "Sitios web de empresas": _sites,
        "Noticias (GDELT)": _gdelt,
        "Búsqueda web con IA (Gemini + Google)": _gemini,
        "Verificación de buzones (SMTP)": _smtp,
    }
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = [pool.submit(_timed, name, fn) for name, fn in checks.items()]
        return [f.result() for f in futures]
