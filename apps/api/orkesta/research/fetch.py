"""Descarga segura de páginas públicas con caché en PostgreSQL.

- Solo http/https hacia direcciones públicas: se resuelve el host y se rechazan IP privadas,
  locales o reservadas en cada salto de redirección (protección SSRF, porque el usuario puede
  escribir dominios).
- Respeta robots.txt en los sitios de empresas.
- Límite de tamaño y de tiempo; espera mínima entre peticiones al mismo host.
- Las respuestas (también los fallos) se guardan en ``web_cache`` para no repetir trabajo.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research.models import WebPage

USER_AGENT = "ORKESTA-Research/1.0 (+https://orkesta-web.onrender.com/; investigacion B2B)"
MAX_BYTES = 1_500_000
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_REDIRECTS = 5

# Las pruebas reemplazan el transporte para no salir a internet.
TRANSPORT: httpx.BaseTransport | None = None
# Y la resolución DNS, para que los hosts de prueba se consideren públicos.
RESOLVER: Callable[[str], list[str]] | None = None


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class Page:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and bool(self.text)


def _resolve(host: str) -> list[str]:
    if RESOLVER is not None:
        return RESOLVER(host)
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list({str(info[4][0]) for info in infos})


def check_public_url(url: str) -> str:
    """Devuelve el host si la URL apunta a internet público; si no, ``FetchError``."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("URL no permitida")
    if parts.port not in (None, 80, 443):
        raise FetchError("Puerto no permitido")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise FetchError("Host no permitido")
    try:
        addresses = _resolve(host)
    except OSError as exc:
        raise FetchError("El dominio no existe") from exc
    if not addresses:
        raise FetchError("El dominio no existe")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError("Dirección no pública")
    return host


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class Fetcher:
    """Un cliente HTTP por corrida del agente, seguro para usar desde varios hilos."""

    def __init__(self, factory: sessionmaker[Session], *, host_interval: float = 0.6):
        self.factory = factory
        self.host_interval = host_interval
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es,en;q=0.8"},
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=TRANSPORT,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()
        self.requests = 0

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- caché ---------------------------------------------------------------------------

    def _cached(self, url: str, ttl: timedelta) -> Page | None:
        with self.factory() as session:
            row = session.scalar(select(WebPage).where(WebPage.url_hash == _hash(url)))
            if row is None:
                return None
            fresh = row.fetched_at >= datetime.now(UTC) - (ttl if row.status else ttl / 7)
            if not fresh:
                return None
            return Page(url, row.final_url or url, row.status, row.content_type, row.body)

    def _store(self, page: Page) -> None:
        values = {
            "url_hash": _hash(page.url),
            "url": page.url[:4000],
            "final_url": page.final_url[:4000],
            "status": page.status,
            "content_type": page.content_type[:120],
            "body": page.text.replace("\x00", ""),
            "fetched_at": datetime.now(UTC),
        }
        stmt = pg_insert(WebPage).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["url_hash"],
            set_={k: stmt.excluded[k] for k in values if k != "url_hash"},
        )
        with self.factory.begin() as session:
            session.execute(stmt)

    # --- red -----------------------------------------------------------------------------

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            delay = self._last_hit.get(host, 0) + self.host_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last_hit[host] = time.monotonic()

    def _download(self, url: str, accept: str, timeout: float | None) -> Page:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            host = check_public_url(current)
            self._wait_turn(host)
            self.requests += 1
            with self.client.stream(
                "GET",
                current,
                headers={"Accept": accept},
                timeout=httpx.Timeout(timeout, connect=5.0) if timeout else TIMEOUT,
            ) as response:
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
                text = ""
                if ctype.startswith("text/") or "json" in ctype or "xml" in ctype or not ctype:
                    text = raw.decode(response.encoding or "utf-8", errors="replace")
                return Page(url, current, response.status_code, ctype, text)
        raise FetchError("Demasiadas redirecciones")

    def get(
        self,
        url: str,
        *,
        ttl: timedelta = timedelta(days=7),
        accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        respect_robots: bool = False,
        timeout: float | None = None,
    ) -> Page | None:
        """La página (de caché o de internet), o ``None`` si no se pudo o no se permite."""
        cached = self._cached(url, ttl)
        if cached is not None:
            return cached if cached.status else None
        if respect_robots and not self.allowed(url):
            return None
        try:
            page = self._download(url, accept, timeout)
        except (httpx.HTTPError, FetchError, ValueError, UnicodeError):
            page = Page(url, url, 0, "", "")
        self._store(page)
        return page if page.status else None

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = None
            page = self.get(f"{origin}/robots.txt", accept="text/plain")
            if page is not None and page.status == 200 and "html" not in page.content_type:
                parser = RobotFileParser()
                parser.parse(page.text.splitlines())
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)
