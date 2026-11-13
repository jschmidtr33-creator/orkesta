"""Dominio oficial sin buscadores pagos: candidatos con el nombre, comprobados con MX y sitio."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import domain_guess, fetch


def test_guess_confirms_mail_and_homepage(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {
        "https://alpina.com.co/": "<title>Alpina</title> Alpina Productos Alimenticios S.A. BIC",
        "https://alpina.com/": "<title>Venta de dominios</title>",
    }

    def route(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body) if body else httpx.Response(404)

    fetch.TRANSPORT = httpx.MockTransport(route)
    monkeypatch.setattr(fetch, "RESOLVER", lambda host: ["93.184.216.34"])
    with fetch.Fetcher(session_factory) as fetcher:
        # alpina.com.co no recibe correo en este ejemplo: se salta aunque exista la página.
        found = domain_guess.guess(fetcher, "Alpina Productos Alimenticios S.A.", "CO",
                                   mx=lambda h: [] if h == "alpina.com.co" else [f"mx.{h}"])  # fmt: skip
        assert found == ""  # alpina.com no menciona a la empresa: no se inventa
        found = domain_guess.guess(fetcher, "Alpina Productos Alimenticios S.A.", "CO",
                                   mx=lambda h: [f"mx.{h}"])  # fmt: skip
        assert found == "alpina.com.co"
    assert "banco.com" not in domain_guess.candidates("Banco de Occidente", "CO")
    assert "bancodeoccidente.com.co" in domain_guess.candidates("Banco de Occidente", "CO")


def test_search_accepts_several_countries_or_all() -> None:
    from orkesta.research.service import validate_filters

    two = validate_filters({"countries": ["co", "MX", "XX"]})
    assert two["countries"] == ["CO", "MX"] and two["country"] == "CO"
    every = validate_filters({"countries": "ALL"})
    assert len(every["countries"]) > 5 and "PE" in every["countries"]
    assert validate_filters({"country": "PE"})["countries"] == ["PE"]
