"""Etapa 2: Prospección con IA. Internet se simula con un transporte HTTP de pruebas que imita
las respuestas reales de Wikidata (SPARQL y búsqueda), GDELT y sitios de empresas."""

from __future__ import annotations

import json
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.db import set_context
from orkesta.core.jobs import run_once
from orkesta.data.models import Contact, DataPoint
from orkesta.research import fetch, news
from orkesta.research.extract import parse_html, people_in
from orkesta.research.fetch import FetchError, check_public_url
from tests.conftest import register

W = "http://www.wikidata.org/entity/"


def _b(**values: str) -> dict[str, dict[str, str]]:
    return {k: {"type": "literal", "value": v} for k, v in values.items()}


CATALOG = {
    "head": {"vars": []},
    "results": {
        "bindings": [
            _b(c=W + "Q100", name="Banco Andino", website="https://www.bancoandino.co/",
               employees="12000", city="Bogotá", industries="banca|servicios financieros",
               types="banco", description="banco colombiano", linkedin="banco-andino"),
            _b(c=W + "Q200", name="Lácteos del Valle", website="https://lacteosvalle.com.co",
               employees="800", city="Cali", industries="industria láctea", types="empresa",
               description="empresa de alimentos"),
            _b(c=W + "Q300", name="Banco Sin Sitio Accesible",
               website="https://www.bancocaido.co", city="Medellín",
               industries="banca", types="banco"),
        ]
    },
}  # fmt: skip
PEOPLE = {
    "head": {"vars": []},
    "results": {
        "bindings": [
            _b(c=W + "Q100", p=W + "Q901", pLabel="Laura Gómez Ruiz", prop="P169",
               li="laura-gomez-ruiz"),
            _b(c=W + "Q100", p=W + "Q902", pLabel="Q902", prop="P488"),
        ]
    },
}  # fmt: skip
HOME = """<html><head><title>Banco Andino | Inicio</title>
<meta property="og:site_name" content="Banco Andino"></head><body>
<nav><a href="/nosotros/equipo-directivo">Equipo directivo</a> <a href="/blog">Blog</a>
<a href="/contacto">Contáctenos</a> <a href="https://otro-sitio.com/equipo">Externo</a></nav>
<p>Bienvenido</p></body></html>"""
TEAM = """<html><body><h1>Nuestro equipo</h1>
<div><h3>Laura Gómez</h3><p>Presidente</p></div>
<div><h3>ANDRÉS MARTÍNEZ</h3><p>Vicepresidente de Tecnología</p></div>
<div><h3>Diana Castro Pérez</h3><p>Gerente de Mercadeo</p></div>
<div><h3>Felipe Torres</h3><p>Analista de datos</p></div>
<p>Escríbele a <a href="mailto:dcastro@bancoandino.co">Diana</a></p></body></html>"""
CONTACT = """<html><body><p>info@bancoandino.co</p>
<a href="tel:+57 601 555 1234">Llámanos</a></body></html>"""
GDELT = {
    "articles": [
        {"url": "https://www.portafolio.co/nota-1", "title":
         "Banco Andino nombra a Camila Rojas como nueva directora de riesgos",
         "seendate": "20261012T120000Z", "domain": "portafolio.co"},
        {"url": "https://www.portafolio.co/nota-2", "title":
         "Renuncia Jorge Pinto, presidente de Banco Andino",
         "seendate": "20261001T120000Z", "domain": "portafolio.co"},
    ]
}  # fmt: skip


def _internet(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host = request.url.host
    if host == "query.wikidata.org":
        query = parse_qs(urlsplit(url).query)["query"][0]
        return httpx.Response(200, json=PEOPLE if "?claim" in query else CATALOG)
    if host == "www.wikidata.org":
        return httpx.Response(200, json={"search": [{"id": "Q100"}]})
    if host == "api.gdeltproject.org":
        return httpx.Response(200, json=GDELT)
    if host == "www.bancoandino.co":
        pages = {
            "/": HOME,
            "/nosotros/equipo-directivo": TEAM,
            "/contacto": CONTACT,
            "/robots.txt": "User-agent: *\nDisallow: /privado",
        }
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="no")
        ctype = "text/plain" if request.url.path.endswith(".txt") else "text/html; charset=utf-8"
        return httpx.Response(200, text=body, headers={"content-type": ctype})
    if host == "lacteosvalle.com.co":
        return httpx.Response(301, headers={"location": "http://127.0.0.1/admin"})
    return httpx.Response(503, text="caído")


@pytest.fixture(autouse=True)
def fake_internet() -> Iterator[None]:
    fetch.TRANSPORT = httpx.MockTransport(_internet)

    def resolve(host: str) -> list[str]:
        return ["127.0.0.1"] if host == "127.0.0.1" else ["93.184.216.34"]

    fetch.RESOLVER = resolve
    previous = news.MIN_INTERVAL
    news.MIN_INTERVAL = 0
    from orkesta.research import router

    router.run_limiter.hits.clear()
    yield
    fetch.TRANSPORT, fetch.RESOLVER, news.MIN_INTERVAL = None, None, previous


def _run(client: TestClient, factory: sessionmaker[Session], **filters: object) -> dict:
    response = client.post("/api/research/runs", json={"country": "CO", **filters})
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    while run_once(factory, "pruebas"):
        pass
    return client.get(f"/api/research/runs/{run_id}").json()


def test_filters_lead_to_companies_and_decision_makers_with_sources(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    options = client.get("/api/research/options").json()
    assert {"value": "CO", "label": "Colombia"} in options["countries"]

    run = _run(client, session_factory, industries=["banca_finanzas"])
    assert run["status"] == "completed", run
    names = [c["data"]["name"] for c in run["companies"]]
    # La industria filtra: la láctea no aparece.
    assert "Banco Andino" in names and "Lácteos del Valle" not in names
    andino = next(c for c in run["companies"] if c["data"]["name"] == "Banco Andino")
    assert andino["data"]["employees"] == 12000
    assert andino["data"]["emails"] == ["info@bancoandino.co"]
    assert andino["data"]["phones"] == ["+576015551234"]
    assert andino["data"]["linkedin"] == "https://www.linkedin.com/company/banco-andino"

    people = {p["data"]["full_name"]: p for p in run["people"]}
    # Wikidata y el sitio describen a la misma persona: se unen y se conserva el nombre completo.
    laura = people["Laura Gómez Ruiz"]
    assert {s["type"] for s in laura["sources"]} == {"wikidata", "sitio"}
    assert laura["data"]["title"] == "Presidente"
    assert laura["provenance"]["title"]["url"].endswith("/nosotros/equipo-directivo")
    assert laura["data"]["linkedin"] == "https://www.linkedin.com/in/laura-gomez-ruiz"

    diana = people["Diana Castro Pérez"]
    assert diana["data"]["email"] == "dcastro@bancoandino.co"
    assert diana["data"]["email_status"] == "publicado"
    # Con un correo publicado se deduce el patrón y se marca como estimado, nunca como real.
    andres = people["Andrés Martínez"]
    assert andres["data"]["email"] == "amartinez@bancoandino.co"
    assert andres["data"]["email_status"] == "estimado"
    assert andres["provenance"]["email"]["confidence"] == 40

    # Noticia de nombramiento sí; la de renuncia no.
    camila = people["Camila Rojas"]
    assert camila["sources"][0]["type"] == "noticia"
    assert camila["sources"][0]["date"] == "2026-10-12"
    assert "Jorge Pinto" not in people
    # Un analista no es decisor: no se muestra con los filtros por defecto.
    assert "Felipe Torres" not in people
    assert run["totals"]["filtered_out"] >= 1
    # La entidad sin nombre en Wikidata (solo «Q902») se descarta.
    assert all(not name.startswith("Q") for name in people)


def test_level_and_area_filters(client: TestClient, session_factory: sessionmaker[Session]) -> None:
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, companies=["Banco Andino"], areas=["tecnologia"])
    assert [p["data"]["full_name"] for p in run["people"]] == ["Andrés Martínez"]
    assert run["summary"].startswith("Banco Andino")


def test_save_people_to_contacts_with_provenance_and_list(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    me = register(client, "ana@lacteos.co")
    run = _run(client, session_factory, companies=["bancoandino.co"])
    ids = [p["id"] for p in run["people"]]
    assert ids
    result = client.post(
        f"/api/research/runs/{run['id']}/save", json={"ids": ids, "new_list": "Banca CO"}
    ).json()
    assert result["created"] == len(ids) and result["added_to_list"] == len(ids)
    # Guardar otra vez no duplica.
    again = client.post(f"/api/research/runs/{run['id']}/save", json={"ids": ids}).json()
    assert again["created"] == 0 and again["merged"] == len(ids)

    with session_factory() as session:
        set_context(session, tenant_id=me["workspace"]["id"])
        diana = session.scalar(select(Contact).where(Contact.email == "dcastro@bancoandino.co"))
        assert diana is not None and diana.source == "investigacion"
        assert diana.level_code == "gerente" and diana.area_code == "marketing"
        point = session.scalar(
            select(DataPoint).where(DataPoint.contact_id == diana.id, DataPoint.field == "email")
        )
        assert point is not None and point.source_type == "investigacion"
        assert "bancoandino.co/nosotros/equipo-directivo" in point.source_label
    listed = client.get("/api/contacts", params={"q": "castro"}).json()
    assert listed["total"] == 1


def test_runs_are_isolated_between_companies(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, companies=["bancoandino.co"])
    client.post("/api/auth/logout")
    register(client, "otro@otra.co", company="Otra Empresa")
    assert client.get(f"/api/research/runs/{run['id']}").status_code == 404
    assert client.get("/api/research/runs").json() == []


def test_invalid_filters_and_cancel(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    assert client.post("/api/research/runs", json={"country": "ZZ"}).status_code == 400
    created = client.post("/api/research/runs", json={"country": "CO"}).json()
    canceled = client.post(f"/api/research/runs/{created['id']}/cancel").json()
    assert canceled["status"] == "canceled"
    while run_once(session_factory, "pruebas"):
        pass
    assert client.get(f"/api/research/runs/{created['id']}").json()["status"] == "canceled"


def test_ssrf_protection() -> None:
    fetch.RESOLVER = None
    for url in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/",
        "file:///etc/passwd",
        "http://example.com:5432/",
    ):
        with pytest.raises(FetchError):
            check_public_url(url)


def test_redirect_to_private_address_is_blocked(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, companies=["lacteosvalle.com.co"])
    company = run["companies"][0]["data"]
    assert company["site_status"] == "sin_acceso" and run["people"] == []


def test_people_extraction_patterns() -> None:
    html = """<ul><li>Gerente Financiero: Felipe Torres</li><li>Ana María Ruiz – CEO</li>
    <li>Nuestro Equipo</li><li>Banco Andino</li><li>Gerente General</li></ul>"""
    found = {p.full_name: p.title for p in people_in(parse_html(html))}
    assert found == {"Felipe Torres": "Gerente Financiero", "Ana María Ruiz": "CEO"}
    assert news.people_in_headline("Ecopetrol designa a Ricardo Roa como presidente") == [
        ("Ricardo Roa", "Presidente")
    ]
    assert news.people_in_headline("Fallece Ricardo Roa, presidente de la compañía") == []


def _gemini_reply(text: str, supported: list[str], titles: list[str]) -> dict:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": f"https://vertexaisearch.cloud.google.com/r/{i}",
                                "title": t,
                            }
                        }
                        for i, t in enumerate(titles)
                    ],
                    "groundingSupports": [
                        {"segment": {"text": s}, "groundingChunkIndices": [0]} for s in supported
                    ],
                },
            }
        ]
    }


PEOPLE_TEXT = """Mariela Solano Quirós | Gerente General | Banco Andino S.A. | https://cr.linkedin.com/in/mariela-solano
Inventado Pérez | CFO | Banco Andino | https://ejemplo.com
- Óscar Vindas | Director de Tecnología | Banco Andino | https://www.bancoandino.co/prensa
Pedro Ajeno | CEO | Otra Compañía Distinta | https://otra.com"""
COMPANIES_TEXT = """Coopeservidores | https://www.coopeservidores.fi.cr
Banco Fantasma | https://fantasma.cr"""


def _gemini(request: httpx.Request) -> httpx.Response:
    prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
    if "empresas reales" in prompt:
        return httpx.Response(200, json=_gemini_reply(
            COMPANIES_TEXT, ["Coopeservidores | https://www.coopeservidores.fi.cr"],
            ["coopeservidores.fi.cr"]))  # fmt: skip
    return httpx.Response(200, json=_gemini_reply(
        PEOPLE_TEXT,
        ["Mariela Solano Quirós | Gerente General", "Óscar Vindas | Director de Tecnología"],
        ["linkedin.com", "bancoandino.co"],
    ))  # fmt: skip


@pytest.fixture()
def with_gemini(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from orkesta.core.config import get_settings
    from orkesta.research import gemini

    monkeypatch.setattr(get_settings(), "gemini_api_key", "clave-de-prueba")
    monkeypatch.setattr(gemini, "MIN_INTERVAL", 0)

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            return _gemini(request)
        return _internet(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    yield


def test_ai_web_search_adds_grounded_people_and_companies(
    client: TestClient, session_factory: sessionmaker[Session], with_gemini: None
) -> None:
    register(client, "ana@lacteos.co")
    assert client.get("/api/research/options").json()["ai_search"] is True
    run = _run(client, session_factory, industries=["banca_finanzas"])
    assert run["status"] == "completed", run
    companies = {c["data"]["name"]: c for c in run["companies"]}
    # La IA suma empresas respaldadas por la búsqueda; la que no tiene respaldo no entra.
    assert "Coopeservidores" in companies and "Banco Fantasma" not in companies
    assert companies["Coopeservidores"]["sources"][0]["type"] == "web"

    people = {(p["data"]["company"], p["data"]["full_name"]): p for p in run["people"]}
    mariela = people[("Banco Andino", "Mariela Solano Quirós")]
    assert mariela["sources"][0]["type"] == "web"
    # La URL que dio el modelo se cita porque su dominio está entre los resultados de Google.
    assert mariela["data"]["linkedin"] == "https://cr.linkedin.com/in/mariela-solano"
    assert ("Banco Andino", "Óscar Vindas") in people
    # El nombre sin respaldo en la búsqueda se descarta: no se inventan personas.
    assert all(name != "Inventado Pérez" for _, name in people)
    # Si el modelo dice que la persona trabaja en otra empresa, no se le atribuye a esta.
    assert all(name != "Pedro Ajeno" for _, name in people)
    assert {c for c, n in people if n == "Mariela Solano Quirós"} == {"Banco Andino"}
    assert run["totals"]["source_web"] >= 2


def test_ai_reply_without_search_backing_is_ignored(
    client: TestClient, session_factory: sessionmaker[Session], with_gemini: None
) -> None:
    from orkesta.research import gemini

    reply = {"candidates": [{"content": {"parts": [{"text": PEOPLE_TEXT}]}}]}
    assert gemini._parse(reply) == (None, "sin_busqueda")


def test_ai_answers_in_markdown_or_prose_are_read() -> None:
    from orkesta.research.extract import Document, people_in
    from orkesta.research.gemini import answer_lines

    text = """Estos son los directivos encontrados:

| Nombre | Cargo | Fuente |
|---|---|---|
| **Mariela Solano Quirós** | Gerente General | [LinkedIn](https://cr.linkedin.com/in/mariela-solano) |
1. **Óscar Vindas** – Director de Tecnología ([fuente](https://www.bancoandino.co/prensa)) [1]
* Ronny Monge: Director Financiero https://www.nacion.com/economia/nota.html
"""
    lines = answer_lines(text)
    found = {}
    for line, urls in lines:
        for person in people_in(Document(lines=[line])):
            found[person.full_name] = (person.title, urls)
    assert found["Mariela Solano Quirós"] == (
        "Gerente General",
        ["https://cr.linkedin.com/in/mariela-solano"],
    )
    assert found["Óscar Vindas"] == (
        "Director de Tecnología",
        ["https://www.bancoandino.co/prensa"],
    )
    assert found["Ronny Monge"][0] == "Director Financiero"
    assert "Nombre" not in " ".join(found)


def test_verification_ladder(with_gemini: None, session_factory: sessionmaker[Session]) -> None:
    from orkesta.research.fetch import Fetcher
    from orkesta.research.gemini import Grounded, _verify

    grounded = Grounded(
        text="", chunks=[{"uri": "https://vertex/r/0", "title": "linkedin.com"}], supports=[]
    )
    with Fetcher(session_factory) as fetcher:
        # Peldaño 2: la página citada existe y menciona a la persona.
        ok = _verify(fetcher, grounded, "Laura Gómez", "Laura", "Gómez",
                     ["https://www.bancoandino.co/nosotros/equipo-directivo"])  # fmt: skip
        assert ok is not None and ok[2] == 80
        # Peldaño 3: perfil público con su nombre en la dirección y LinkedIn en los resultados.
        li = _verify(fetcher, grounded, "Mariela Solano", "Mariela", "Solano",
                     ["https://cr.linkedin.com/in/mariela-solano-1a2b"])  # fmt: skip
        assert li is not None and li[2] == 60
        # Sin evidencia: la página no la menciona y el perfil no lleva su nombre.
        assert _verify(fetcher, grounded, "Inventado Pérez", "Inventado", "Pérez",
                       ["https://www.bancoandino.co/", "https://cr.linkedin.com/in/otra-persona"]) is None  # fmt: skip


def test_invalid_key_stops_and_is_reported(
    client: TestClient, session_factory: sessionmaker[Session], with_gemini: None
) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(400, json={"error": {"message": "API key not valid."}})
        return _internet(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, industries=["banca_finanzas"])
    assert run["status"] == "completed"
    assert run["totals"]["ai_fatal_clave"] == 1
    assert run["totals"]["ai_calls"] == 1  # no insiste después de saber que la clave es mala
    assert any("GEMINI_API_KEY" in line["text"] for line in run["progress"]["log"])


def test_model_fallback_when_model_is_unavailable(
    client: TestClient, session_factory: sessionmaker[Session], with_gemini: None
) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            if "gemini-2.5-flash" in request.url.path:
                return httpx.Response(429, json={"error": {"message": "quota"}})
            return _gemini(request)
        return _internet(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, companies=["Banco Andino"])
    names = [p["data"]["full_name"] for p in run["people"]]
    assert "Mariela Solano Quirós" in names


def test_diagnostics_report_each_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, with_gemini: None
) -> None:
    from orkesta.research import diagnostics, router

    router.diag_limiter.hits.clear()

    def route(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "generativelanguage.googleapis.com":
            return _gemini(request)
        if host in ("query.wikidata.org", "www.wikipedia.org"):
            return httpx.Response(200, json={"boolean": True})
        if host == "api.gdeltproject.org":
            return httpx.Response(200, json=GDELT)
        return httpx.Response(503)

    fetch.TRANSPORT = httpx.MockTransport(route)
    monkeypatch.setattr(diagnostics, "_smtp", lambda: (False, "Puerto 25 bloqueado"))
    register(client, "ana@lacteos.co")
    checks = {c["name"]: c for c in client.get("/api/research/diagnostics").json()["checks"]}
    assert all(c["ok"] for n, c in checks.items() if "SMTP" not in n), checks
    assert "resultados de Google" in checks["Búsqueda web con IA (Gemini + Google)"]["detail"]
    assert checks["Verificación de buzones (SMTP)"]["ok"] is False
