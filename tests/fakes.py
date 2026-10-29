"""Dobles de prueba para la red: DNS y descargas sin salir a internet."""

from __future__ import annotations

from dataclasses import dataclass, field

from orkesta.engine.net.dns import DnsError, MxInfo
from orkesta.engine.net.fetch import FetchResult


@dataclass
class FakeResolver:
    ips: dict[str, list[str]] = field(default_factory=dict)
    mx_hosts: dict[str, list[str]] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    queries: list[str] = field(default_factory=list)
    is_healthy: bool = True

    def resolve_ips(self, host: str) -> list[str]:
        self.queries.append(host)
        if host in self.failing:
            raise DnsError(f"DNS sin respuesta para {host}")
        return list(self.ips.get(host, []))

    def mx(self, domain: str) -> MxInfo:
        if domain in self.failing:
            raise DnsError(f"DNS sin respuesta para {domain}")
        return MxInfo(hosts=tuple(self.mx_hosts.get(domain, [])))

    def healthy(self) -> bool:
        return self.is_healthy


@dataclass
class FakeFetcher:
    """Devuelve páginas predefinidas por URL; cualquier otra URL falla."""

    pages: dict[str, FetchResult] = field(default_factory=dict)
    disallowed: set[str] = field(default_factory=set)
    requested: list[str] = field(default_factory=list)

    def get(self, url: str) -> FetchResult:
        self.requested.append(url)
        if url in self.pages:
            return self.pages[url]
        return FetchResult(url, url, None, "", None, (), "error HTTP: ConnectError")

    def allowed_by_robots(self, url: str) -> bool:
        return not any(url.startswith(prefix) for prefix in self.disallowed)


def page(url: str, html: str, final_url: str | None = None, status: int = 200) -> FetchResult:
    return FetchResult(url, final_url or url, status, html, "text/html; charset=utf-8")
