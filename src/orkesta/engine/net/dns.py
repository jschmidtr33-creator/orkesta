"""Consultas DNS: resolución de direcciones y registros MX.

La existencia de un dominio, la presencia de MX y la existencia de un buzón concreto son cosas
distintas: este módulo solo responde las dos primeras.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import dns.exception
import dns.resolver

# Sufijo del servidor MX → proveedor de correo. Se usa como señal para el patrón de correo.
MX_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("google.com", "google_workspace"),
    ("googlemail.com", "google_workspace"),
    ("protection.outlook.com", "microsoft_365"),
    ("outlook.com", "microsoft_365"),
    ("pphosted.com", "proofpoint"),
    ("mimecast.com", "mimecast"),
    ("zoho.com", "zoho"),
    ("secureserver.net", "godaddy"),
    ("barracudanetworks.com", "barracuda"),
    ("messagelabs.com", "broadcom"),
    ("iphmx.com", "cisco"),
)


@dataclass(frozen=True)
class MxInfo:
    hosts: tuple[str, ...]

    @property
    def has_mx(self) -> bool:
        return bool(self.hosts)

    @property
    def provider(self) -> str | None:
        for host in self.hosts:
            for suffix, provider in MX_PROVIDERS:
                if host == suffix or host.endswith("." + suffix):
                    return provider
        return "other" if self.hosts else None


class DnsError(Exception):
    """Fallo transitorio de DNS (tiempo agotado, servidor sin respuesta)."""


class Resolver(Protocol):
    def resolve_ips(self, host: str) -> list[str]:
        """Direcciones IPv4/IPv6 del host; lista vacía si no existe."""

    def mx(self, domain: str) -> MxInfo:
        """Servidores MX del dominio, del más preferido al menos."""

    def healthy(self) -> bool:
        """``True`` si el DNS responde bien a un dominio que siempre existe."""


# Dominios de control: si ninguno resuelve, el DNS del entorno está roto (no la empresa).
CANARY_DOMAINS = ("wikidata.org", "iana.org")


class DnsPythonResolver:
    def __init__(self, timeout: float = 5.0) -> None:
        self._resolver = dns.resolver.Resolver()
        self._resolver.lifetime = timeout
        self._resolver.timeout = timeout

    def _query(self, name: str, rdtype: str) -> list[str]:
        try:
            answer = self._resolver.resolve(name, rdtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.resolver.NoNameservers as exc:
            raise DnsError(f"ningún servidor DNS respondió para {name}") from exc
        except dns.exception.Timeout as exc:
            raise DnsError(f"DNS sin respuesta para {name}") from exc
        except dns.exception.DNSException as exc:
            raise DnsError(f"Error DNS para {name}: {type(exc).__name__}") from exc
        return [record.to_text() for record in answer]

    def resolve_ips(self, host: str) -> list[str]:
        return self._query(host, "A") + self._query(host, "AAAA")

    def healthy(self) -> bool:
        for domain in CANARY_DOMAINS:
            try:
                if self._query(domain, "A"):
                    return True
            except DnsError:
                continue
        return False

    def mx(self, domain: str) -> MxInfo:
        records = []
        for text in self._query(domain, "MX"):
            preference, _, exchange = text.partition(" ")
            host = exchange.rstrip(".").lower()
            if host:  # "0 ." = MX nulo (RFC 7505): el dominio no acepta correo
                records.append((int(preference), host))
        return MxInfo(hosts=tuple(host for _, host in sorted(records)))
