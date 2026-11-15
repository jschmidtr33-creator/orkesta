"""Buscador de respaldo: Brave Search (índice propio, no depende de Google).

Se usa cuando Serper no está configurado, se quedó sin créditos o su clave falla. Devuelve los
resultados con la misma forma que Serper ({"organic": [{"title", "link", "snippet"}]}), así el
resto de ORKESTA no cambia. La clave se carga desde Administración → Integraciones, cifrada.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
KEY = "buscador_respaldo"
URL = "https://api.search.brave.com/res/v1/web/search"
_TAGS = re.compile(r"<[^>]+>")
_KEY_CACHE: tuple[float, str] = (0.0, "")
DOWN_UNTIL = 0.0


def configured_key(session: Session) -> str:
    from orkesta.admin.service import _get
    from orkesta.campaigns.gmail import decrypt

    token = str(dict(_get(session, KEY, {})).get("key") or "")
    try:
        return decrypt(token) if token else ""
    except Exception:
        return ""


def save_key(session: Session, key: str) -> None:
    from orkesta.admin.service import _put
    from orkesta.campaigns.gmail import encrypt

    global _KEY_CACHE
    _put(session, KEY, {"provider": "brave", "key": encrypt(key) if key else ""})
    _KEY_CACHE = (0.0, "")


def key() -> str:
    """Clave vigente (se relee de la base cada 5 minutos)."""
    global _KEY_CACHE
    now = time.monotonic()
    if now - _KEY_CACHE[0] < 300:
        return _KEY_CACHE[1]
    value = ""
    try:
        from orkesta.core.db import get_session_factory

        with get_session_factory()() as session:
            value = configured_key(session)
    except Exception:
        logger.exception("brave_key")
    _KEY_CACHE = (now, value)
    return value


def available() -> bool:
    return bool(key()) and time.monotonic() >= DOWN_UNTIL


def _clean(text: str) -> str:
    return " ".join(_TAGS.sub("", text or "").replace("&#x27;", "'").replace("&quot;", '"').split())


def test(value: str) -> bool:
    """¿La clave funciona? (una búsqueda de prueba)."""
    try:
        r = httpx.get(URL, params={"q": "orkesta", "count": 1},
                      headers={"X-Subscription-Token": value, "Accept": "application/json"},
                      timeout=15)  # fmt: skip
    except httpx.HTTPError:
        return False
    return r.status_code == 200


def search(fetcher: Any, payload: dict[str, Any], stats: Any) -> dict[str, Any] | None:
    """Una búsqueda con la forma de Serper. Usa la misma caché de páginas de ORKESTA."""
    global DOWN_UNTIL
    from orkesta.research import fetch
    from orkesta.research.fetch import Page

    token = key()
    if not token or time.monotonic() < DOWN_UNTIL:
        return None
    count = min(int(payload.get("num", 10) or 10), 20)
    lang = "es" if payload.get("hl", "es") == "es" else "en"
    params: dict[str, Any] = {"q": str(payload.get("q", ""))[:400], "count": count,
                              "search_lang": lang}  # fmt: skip
    page = int(payload.get("page", 1) or 1)
    if page > 1:
        params["offset"] = min(page - 1, 9)
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    cache_url = f"brave://search/{digest}"
    cached = fetcher._cached(cache_url, timedelta_days(14))
    if cached is not None and cached.status == 200:
        try:
            with stats.lock:
                stats.cached += 1
            loaded: dict[str, Any] = json.loads(cached.text)
            return loaded
        except ValueError:
            pass
    with stats.lock:
        if stats.calls >= stats.budget:
            stats.errors["tope"] = stats.errors.get("tope", 0) + 1
            return None
        stats.calls += 1
    response = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=20.0, transport=fetch.TRANSPORT) as client:
                response = client.get(URL, params=params, headers={
                    "X-Subscription-Token": token, "Accept": "application/json"})  # fmt: skip
        except httpx.HTTPError:
            stats.error("red")
            return None
        if response.status_code != 429:
            break
        time.sleep(1.2 * (attempt + 1))  # el plan gratuito permite una búsqueda por segundo
    if response is None or response.status_code != 200:
        code = response.status_code if response is not None else 0
        stats.error(f"respaldo_{code}")
        if code in (401, 402, 403):
            DOWN_UNTIL = time.monotonic() + 1800
            from orkesta.admin import alerts

            alerts.record(fetcher.factory, "respaldo")
        return None
    try:
        raw = response.json()
    except ValueError:
        stats.error("formato")
        return None
    rows = [{"title": _clean(r.get("title", "")), "link": str(r.get("url", "")),
             "snippet": _clean(r.get("description", ""))}
            for r in (raw.get("web") or {}).get("results", []) if isinstance(r, dict)]  # fmt: skip
    data = {"organic": rows, "source": "brave"}
    fetcher._store(Page(cache_url, cache_url, 200, "application/json", json.dumps(data)))
    return data


def timedelta_days(days: int) -> Any:
    from datetime import timedelta

    return timedelta(days=days)
