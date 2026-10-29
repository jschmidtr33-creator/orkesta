"""Capa de red con transporte HTTP simulado: SSRF, redirecciones, límites, robots.txt, MX."""

from __future__ import annotations

import httpx
import pytest

from fakes import FakeResolver
from orkesta.engine.net.dns import MxInfo
from orkesta.engine.net.domains import is_non_corporate, normalize_host, registrable_domain
from orkesta.engine.net.fetch import SafeFetcher, is_public_ip

PUBLIC = "93.184.216.34"


def _fetcher(handler: httpx.MockTransport, **ips: list[str]) -> SafeFetcher:
    resolver = FakeResolver(ips={k.replace("_", "."): v for k, v in ips.items()})
    return SafeFetcher(resolver=resolver, transport=handler, max_bytes=1000)


def _html(body: str, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html", **headers})


# --- dominios ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.GrupoModelo.com.mx/contacto", "grupomodelo.com.mx"),
        ("www.cuervo.com", "cuervo.com"),
        ("ventas.empresa.com.co", "empresa.com.co"),
        ("sub.dominio.mx", "dominio.mx"),
        ("bimbo.com", "bimbo.com"),
        ("pastelería.mx", "pastelería".encode("idna").decode() + ".mx"),
    ],
)
def test_registrable_domain(raw: str, expected: str) -> None:
    assert registrable_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", "localhost", "-mala.com", "a" * 64 + ".com", "1.2.3.4"])
def test_invalid_hosts(raw: str) -> None:
    assert normalize_host(raw) is None


def test_non_corporate_hosts() -> None:
    assert is_non_corporate("mx.linkedin.com")
    assert is_non_corporate("sedo.com")
    assert not is_non_corporate("grupomodelo.com")


def test_mx_provider_classification() -> None:
    assert MxInfo(("aspmx.l.google.com",)).provider == "google_workspace"
    assert MxInfo(("empresa-com.mail.protection.outlook.com",)).provider == "microsoft_365"
    assert MxInfo(("mx1.propio.com",)).provider == "other"
    assert MxInfo(()).provider is None and not MxInfo(()).has_mx


# --- SSRF ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1", "0.0.0.0"],
)
def test_private_addresses_are_not_public(ip: str) -> None:
    assert not is_public_ip(ip)


def test_public_address() -> None:
    assert is_public_ip(PUBLIC)


def test_blocks_host_resolving_to_private_ip() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe llegar a conectarse")

    fetcher = _fetcher(httpx.MockTransport(handler), interno_empresa_com=["10.1.2.3"])
    result = fetcher.get("https://interno.empresa.com/")
    assert not result.ok and result.error is not None and "no pública" in result.error


def test_blocks_redirect_to_private_ip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "empresa.com":
            return httpx.Response(302, headers={"location": "http://metadatos.local/latest"})
        raise AssertionError("no debe seguir la redirección")

    fetcher = _fetcher(
        httpx.MockTransport(handler), empresa_com=[PUBLIC], metadatos_local=["169.254.169.254"]
    )
    result = fetcher.get("https://empresa.com/")
    assert not result.ok and result.redirects == ("http://metadatos.local/latest",)
    assert result.error is not None and result.error.startswith("bloqueada")


@pytest.mark.parametrize(
    "url", ["ftp://empresa.com/", "file:///etc/passwd", "https://empresa.com:8080/"]
)
def test_blocks_disallowed_schemes_and_ports(url: str) -> None:
    fetcher = _fetcher(httpx.MockTransport(lambda _: _html("x")), empresa_com=[PUBLIC])
    result = fetcher.get(url)
    assert result.error is not None and result.error.startswith("bloqueada")


def test_unresolvable_host() -> None:
    fetcher = _fetcher(httpx.MockTransport(lambda _: _html("x")))
    assert "no resuelve" in (fetcher.get("https://no-existe.com/").error or "")


# --- descarga ------------------------------------------------------------------------------


def test_follows_safe_redirects_and_reports_final_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "modelo.com":
            return httpx.Response(301, headers={"location": "https://www.grupomodelo.com/"})
        return _html("<title>Grupo Modelo</title>")

    fetcher = _fetcher(
        httpx.MockTransport(handler), modelo_com=[PUBLIC], www_grupomodelo_com=[PUBLIC]
    )
    result = fetcher.get("https://modelo.com/")
    assert result.ok and result.final_url == "https://www.grupomodelo.com/"
    assert "Grupo Modelo" in result.text


def test_stops_after_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url) + "x"})

    result = _fetcher(httpx.MockTransport(handler), bucle_com=[PUBLIC]).get("https://bucle.com/")
    assert result.error == "demasiadas redirecciones"


def test_body_is_capped_and_non_text_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return _html("a" * 5000)

    fetcher = _fetcher(httpx.MockTransport(handler), empresa_com=[PUBLIC])
    assert len(fetcher.get("https://empresa.com/").text) == 1000
    pdf = fetcher.get("https://empresa.com/pdf")
    assert pdf.ok and pdf.text == ""


def test_timeout_is_reported() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento")

    result = _fetcher(httpx.MockTransport(handler), lento_com=[PUBLIC]).get("https://lento.com/")
    assert result.error == "tiempo agotado"


def test_sends_identifiable_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return _html("ok")

    _fetcher(httpx.MockTransport(handler), empresa_com=[PUBLIC]).get("https://empresa.com/")
    assert seen and seen[0].startswith("ORKESTA-bot/")


# --- robots.txt ----------------------------------------------------------------------------


def _robots_fetcher(status: int, body: str = "") -> SafeFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body, headers={"content-type": "text/plain"})
        return _html("ok")

    return _fetcher(httpx.MockTransport(handler), empresa_com=[PUBLIC])


def test_robots_rules_are_respected() -> None:
    fetcher = _robots_fetcher(200, "User-agent: *\nDisallow: /privado\n")
    assert fetcher.allowed_by_robots("https://empresa.com/contacto")
    assert not fetcher.allowed_by_robots("https://empresa.com/privado/equipo")


def test_missing_robots_allows_everything() -> None:
    assert _robots_fetcher(404).allowed_by_robots("https://empresa.com/contacto")


def test_server_error_on_robots_disallows_everything() -> None:
    assert not _robots_fetcher(503).allowed_by_robots("https://empresa.com/contacto")


def test_robots_is_fetched_once_per_origin() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404)

    fetcher = _fetcher(httpx.MockTransport(handler), empresa_com=[PUBLIC])
    fetcher.allowed_by_robots("https://empresa.com/a")
    fetcher.allowed_by_robots("https://empresa.com/b")
    assert calls.count("/robots.txt") == 1
