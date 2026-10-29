"""Descarga segura de páginas públicas.

Protecciones:
- Solo ``http``/``https`` en los puertos 80 y 443.
- SSRF: antes de cada petición, y en cada redirección, el host se resuelve y se rechaza si
  alguna dirección no es pública (privadas, locales, enlace local, metadatos, multicast...).
- Redirecciones seguidas a mano (máximo 5), cada una verificada.
- Límite de bytes leídos y de tiempo; solo se decodifica HTML o texto.
- robots.txt respetado por host, con un agente identificable.

Riesgo residual documentado: entre la verificación y la conexión, httpx vuelve a resolver el
host (DNS rebinding). La ventana es de milisegundos y el contenido nunca se devuelve a un
usuario externo; si ORKESTA se abre a terceros, se fijará la IP verificada en la conexión.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Protocol
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx

from orkesta import __version__
from orkesta.engine.net.dns import DnsError, Resolver

USER_AGENT = f"ORKESTA-bot/{__version__} (+verificacion de dominios corporativos)"
MAX_REDIRECTS = 5
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str
    content_type: str | None = None
    redirects: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


class Fetcher(Protocol):
    def get(self, url: str) -> FetchResult: ...


class RobotsAwareFetcher(Fetcher, Protocol):
    def allowed_by_robots(self, url: str) -> bool: ...


class UnsafeUrlError(ValueError):
    """La URL apunta a un destino no permitido."""


def _failure(url: str, current: str, redirects: list[str], error: str) -> FetchResult:
    return FetchResult(url, current, None, "", None, tuple(redirects), error)


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


@dataclass
class SafeFetcher:
    resolver: Resolver
    timeout: float = 10.0
    max_bytes: int = 1_000_000
    transport: httpx.BaseTransport | None = None
    _robots: dict[str, robotparser.RobotFileParser | None] = field(default_factory=dict)

    def _check(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise UnsafeUrlError(f"esquema no permitido: {parts.scheme}")
        if parts.port not in (None, 80, 443):
            raise UnsafeUrlError(f"puerto no permitido: {parts.port}")
        host = parts.hostname
        if not host:
            raise UnsafeUrlError("URL sin host")
        try:
            ips = self.resolver.resolve_ips(host)
        except DnsError as exc:
            raise UnsafeUrlError(str(exc)) from exc
        if not ips:
            raise UnsafeUrlError(f"el host {host} no resuelve")
        blocked = [ip for ip in ips if not is_public_ip(ip)]
        if blocked:
            raise UnsafeUrlError(f"el host {host} apunta a una dirección no pública")

    def _client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=False,
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es,en;q=0.8"},
            transport=self.transport,
        )

    def _read(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith(_TEXT_TYPES):
            return ""
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= self.max_bytes:
                break
        body = b"".join(chunks)[: self.max_bytes]
        encoding = response.encoding or "utf-8"
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    def get(self, url: str) -> FetchResult:
        current = url
        redirects: list[str] = []
        try:
            with self._client() as client:
                for _ in range(MAX_REDIRECTS + 1):
                    self._check(current)
                    with client.stream("GET", current) as response:
                        if response.is_redirect and "location" in response.headers:
                            current = urljoin(current, response.headers["location"])
                            redirects.append(current)
                            continue
                        return FetchResult(
                            url=url,
                            final_url=str(response.url),
                            status=response.status_code,
                            text=self._read(response),
                            content_type=response.headers.get("content-type"),
                            redirects=tuple(redirects),
                        )
                return _failure(url, current, redirects, "demasiadas redirecciones")
        except UnsafeUrlError as exc:
            return _failure(url, current, redirects, f"bloqueada: {exc}")
        except httpx.TimeoutException:
            return _failure(url, current, redirects, "tiempo agotado")
        except httpx.HTTPError as exc:
            return _failure(url, current, redirects, f"error HTTP: {type(exc).__name__}")

    def allowed_by_robots(self, url: str) -> bool:
        """Aplica robots.txt según RFC 9309.

        - 2xx: se respetan sus reglas.
        - 4xx (no existe, prohibido): se permite todo.
        - 5xx o sitio inaccesible: se asume que no se permite nada.
        """
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            result = self.get(origin + "/robots.txt")
            parser = robotparser.RobotFileParser()
            if result.ok:
                parser.parse(result.text.splitlines())
            elif result.status is not None and 400 <= result.status < 500:
                parser.parse([])
            else:
                parser.parse(["User-agent: *", "Disallow: /"])
            self._robots[origin] = parser
        rules = self._robots[origin]
        return True if rules is None else rules.can_fetch(USER_AGENT, url)
