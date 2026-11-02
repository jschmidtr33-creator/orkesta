"""Verificación de correos sin APIs pagas.

Capas, de la más barata a la más costosa:
1. Sintaxis (RFC 5322 vía ``email-validator``).
2. Dominio: desechable, proveedor gratuito, registros MX (o A implícito) en DNS.
3. SMTP: se conecta al MX y pregunta si el buzón existe (``RCPT TO``) sin enviar nada.
   Antes prueba una dirección aleatoria: si el servidor la acepta, el dominio es «catch-all»
   (acepta todo) y no se puede confirmar ningún buzón.

Muchas nubes bloquean la salida por el puerto 25. Si las conexiones fallan de forma repetida
se marca SMTP como no disponible y el resultado queda en «desconocido» (nunca «válido»).
"""

from __future__ import annotations

import secrets
import smtplib
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email

DISPOSABLE = frozenset(
    [
        "mailinator.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "10minutemail.com",
        "tempmail.com",
        "temp-mail.org",
        "yopmail.com",
        "trashmail.com",
        "getnada.com",
        "dispostable.com",
        "maildrop.cc",
        "sharklasers.com",
        "throwawaymail.com",
        "fakeinbox.com",
        "mintemail.com",
        "mailnesia.com",
        "moakt.com",
        "emailondeck.com",
        "tempail.com",
        "tempr.email",
        "spamgourmet.com",
        "mohmal.com",
        "burnermail.io",
        "mailcatch.com",
        "inboxkitten.com",
        "mytemp.email",
        "33mail.com",
    ]
)
FREE = frozenset(
    [
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "hotmail.es",
        "outlook.com",
        "outlook.es",
        "live.com",
        "live.com.mx",
        "msn.com",
        "yahoo.com",
        "yahoo.es",
        "yahoo.com.mx",
        "yahoo.com.ar",
        "icloud.com",
        "me.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "gmx.es",
        "zoho.com",
        "mail.com",
    ]
)
ROLE = frozenset(
    [
        "info",
        "contacto",
        "contact",
        "hola",
        "hello",
        "ventas",
        "sales",
        "comercial",
        "servicio",
        "soporte",
        "support",
        "admin",
        "administracion",
        "prensa",
        "press",
        "marketing",
        "rrhh",
        "talento",
        "empleo",
        "careers",
        "jobs",
        "facturacion",
        "noreply",
        "no-reply",
        "webmaster",
        "gerencia",
        "recepcion",
        "compras",
        "proveedores",
        "pqrs",
    ]
)

STATUSES = ("sin_validar", "estimado", "valido", "invalido", "catch_all", "riesgoso", "desconocido")
STATUS_LABELS = {
    "sin_validar": "Sin validar",
    "estimado": "Estimado",
    "valido": "Válido",
    "invalido": "Inválido",
    "catch_all": "Acepta todo",
    "riesgoso": "Riesgoso",
    "desconocido": "Sin confirmar",
}


@dataclass
class Verdict:
    email: str
    status: str
    reason: str
    mx: str = ""
    free: bool = False
    role: bool = False


@dataclass
class DomainInfo:
    domain: str
    mx_hosts: list[str] = field(default_factory=list)
    disposable: bool = False
    free: bool = False
    catch_all: bool | None = None  # None: no se pudo saber
    smtp_ok: bool = False
    error: str = ""


# --- DNS ------------------------------------------------------------------------------------

Resolver = Callable[[str], list[str]]


def dns_mx(domain: str) -> list[str]:
    """Servidores MX por preferencia; si no hay MX pero sí A, el propio dominio (RFC 5321)."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 6.0
    try:
        answers = resolver.resolve(domain, "MX")
        records = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        hosts = [h for _, h in records if h and h != "."]
        return hosts
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return []
    except (dns.resolver.NoAnswer, dns.exception.Timeout):
        try:
            resolver.resolve(domain, "A")
            return [domain]
        except dns.exception.DNSException:
            return []


# --- SMTP -----------------------------------------------------------------------------------


class SmtpUnavailable(Exception):
    """No se pudo conectar al puerto 25 (bloqueo de la red o del servidor)."""


class SmtpProber:
    """Conversación SMTP sin enviar correo: EHLO, MAIL FROM, varios RCPT TO, QUIT."""

    def __init__(self, helo: str, sender: str, timeout: float = 12.0):
        self.helo, self.sender, self.timeout = helo, sender, timeout

    def probe(self, mx: str, addresses: list[str]) -> dict[str, tuple[int, str]]:
        try:
            server = smtplib.SMTP(timeout=self.timeout, local_hostname=self.helo)
            server.connect(mx, 25)
        except (OSError, smtplib.SMTPException) as exc:
            raise SmtpUnavailable(str(exc)) from exc
        results: dict[str, tuple[int, str]] = {}
        try:
            server.ehlo_or_helo_if_needed()
            if server.has_extn("starttls"):
                try:
                    server.starttls()
                    server.ehlo()
                except (smtplib.SMTPException, OSError):
                    pass
            code, _ = server.mail(self.sender)
            if code >= 400:
                return {a: (code, "remitente rechazado") for a in addresses}
            for address in addresses:
                code, message = server.rcpt(address)
                results[address] = (code, message.decode(errors="replace")[:200])
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPException, OSError) as exc:
            for address in addresses:
                results.setdefault(address, (0, f"conexión interrumpida: {exc}"[:200]))
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                server.close()
        return results


class RemoteProber:
    """Pide la conversación SMTP al verificador propio de ORKESTA (otro servidor con el puerto 25
    abierto). HTTPS con el certificado del verificador fijado por su huella SHA-256."""

    def __init__(self, url: str, token: str, fingerprint: str, timeout: float = 40.0):
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        self.host, self.port = parts.hostname or "", parts.port or 8443
        self.token, self.timeout = token, timeout
        self.fingerprint = fingerprint.replace(":", "").lower()

    def probe(self, mx: str, addresses: list[str]) -> dict[str, tuple[int, str]]:
        import hashlib
        import http.client
        import json
        import ssl

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # se verifica abajo por huella fijada
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout,
                                           context=context)  # fmt: skip
        try:
            conn.connect()
            cert = conn.sock.getpeercert(binary_form=True) if conn.sock else None
            if not cert or hashlib.sha256(cert).hexdigest() != self.fingerprint:
                raise SmtpUnavailable("La huella del verificador no coincide.")
            body = json.dumps({"mx": mx, "addresses": addresses})
            conn.request("POST", "/probe", body, {"Authorization": f"Bearer {self.token}",
                                                  "Content-Type": "application/json"})  # fmt: skip
            response = conn.getresponse()
            data = json.loads(response.read() or b"{}")
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise SmtpUnavailable(f"verificador: {exc}") from exc
        finally:
            conn.close()
        if response.status != 200:
            raise SmtpUnavailable(f"verificador respondió {response.status}")
        results = data.get("results", {})
        if "__error__" in results:
            raise SmtpUnavailable(str(results["__error__"][1]))
        return {a: (int(v[0]), str(v[1])) for a, v in results.items() if a in addresses}


class SmtpHealth:
    """Detecta si esta red puede hablar SMTP. Tras 3 fallos de conexión en dominios distintos
    se desactiva por 1 hora para no hacer esperar a nadie."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.failures: set[str] = set()
        self.successes = 0
        self.disabled_until = 0.0

    def available(self) -> bool:
        with self.lock:
            return time.monotonic() >= self.disabled_until

    def record(self, mx: str, ok: bool) -> None:
        with self.lock:
            if ok:
                self.successes += 1
                self.failures.clear()
                return
            self.failures.add(mx)
            if len(self.failures) >= 3 and self.successes == 0:
                self.disabled_until = time.monotonic() + 3600


HEALTH = SmtpHealth()


def _accepted(code: int) -> bool:
    return 200 <= code < 300


def _rejected(code: int, message: str) -> bool:
    # 550 5.1.1 «user unknown»; 553/554 a veces. 552 es buzón lleno (existe).
    text = message.lower()
    if code in (550, 551, 553, 554) or text.startswith("5.1."):
        return not any(w in text for w in ("spam", "blocked", "blacklist", "policy", "reputation"))
    return False


# --- Orquestación por dominio ---------------------------------------------------------------


@dataclass
class Checker:
    prober: SmtpProber | RemoteProber | None
    resolver: Resolver = dns_mx
    health: SmtpHealth = HEALTH
    domains: dict[str, DomainInfo] = field(default_factory=dict)

    def syntax(self, email: str) -> tuple[str, str] | None:
        """(local, dominio) normalizados o ``None`` si la sintaxis no es válida."""
        try:
            info = validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            return None
        return info.local_part.lower(), info.domain.lower()

    def domain(self, domain: str) -> DomainInfo:
        if domain in self.domains:
            return self.domains[domain]
        info = DomainInfo(domain, disposable=domain in DISPOSABLE, free=domain in FREE)
        try:
            info.mx_hosts = self.resolver(domain)
        except Exception as exc:  # DNS caído: no concluir nada
            info.error = f"DNS: {exc}"[:200]
        self.domains[domain] = info
        return info

    def _smtp(self, info: DomainInfo, addresses: list[str]) -> dict[str, tuple[int, str]] | None:
        if self.prober is None or not self.health.available() or not info.mx_hosts:
            return None
        canary = f"zz-{secrets.token_hex(6)}@{info.domain}"
        ask = ([canary] if info.catch_all is None else []) + addresses
        for mx in info.mx_hosts[:2]:
            try:
                results = self.prober.probe(mx, ask)
            except SmtpUnavailable:
                self.health.record(mx, ok=False)
                continue
            self.health.record(mx, ok=True)
            info.smtp_ok = True
            if canary in results:
                code, _ = results.pop(canary)
                info.catch_all = _accepted(code) if code else None
            return results
        return None

    def verify(self, emails: list[str]) -> list[Verdict]:
        """Verifica correos (idealmente del mismo dominio, en una sola conexión SMTP)."""
        verdicts: dict[str, Verdict] = {}
        by_domain: dict[str, list[str]] = {}
        for email in emails:
            parsed = self.syntax(email)
            if parsed is None:
                verdicts[email] = Verdict(email, "invalido", "Formato de correo no válido")
                continue
            local, domain = parsed
            by_domain.setdefault(domain, []).append(email)
        for domain, addresses in by_domain.items():
            info = self.domain(domain)
            for email in addresses:
                local = email.split("@", 1)[0].lower()
                verdicts[email] = Verdict(
                    email, "desconocido", "", free=info.free, role=local in ROLE
                )
            if info.error:
                for email in addresses:
                    verdicts[email].reason = "No se pudo consultar el DNS del dominio"
                continue
            if not info.mx_hosts:
                for email in addresses:
                    verdicts[email].status = "invalido"
                    verdicts[email].reason = "El dominio no recibe correo (sin MX)"
                continue
            if info.disposable:
                for email in addresses:
                    verdicts[email].status = "riesgoso"
                    verdicts[email].reason = "Dominio de correo desechable"
                continue
            results = self._smtp(info, addresses)
            for email in addresses:
                verdict = verdicts[email]
                verdict.mx = info.mx_hosts[0]
                if results is None:
                    verdict.reason = (
                        "El dominio recibe correo; el buzón no se pudo confirmar por SMTP"
                    )
                    continue
                code, message = results.get(email, (0, ""))
                if info.catch_all:
                    verdict.status = "catch_all"
                    verdict.reason = "El servidor acepta cualquier dirección: no se puede confirmar"
                elif _accepted(code):
                    verdict.status = "valido"
                    verdict.reason = f"Buzón confirmado por {info.mx_hosts[0]}"
                elif _rejected(code, message):
                    verdict.status = "invalido"
                    verdict.reason = f"El servidor rechazó el buzón ({code})"
                else:
                    verdict.reason = (
                        f"Respuesta temporal o ambigua del servidor ({code})"
                        if code
                        else "El servidor cortó la conexión"
                    )
                if verdict.status == "valido" and verdict.role:
                    verdict.reason += " · buzón genérico, no personal"
        return [verdicts[e] for e in emails]


def helo_identity(public_base_url: str) -> tuple[str, str]:
    host = public_base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    host = host or socket.getfqdn()
    return host, f"verificacion@{host}"
