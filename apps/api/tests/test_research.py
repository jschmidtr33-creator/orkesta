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


XRAY = {
    "organic": [
        {"title": "Carlos Ruiz Pardo - Gerente General - Banco Andino | LinkedIn",
         "link": "https://co.linkedin.com/in/carlos-ruiz-pardo-12ab", "snippet": "Bogotá"},
        {"title": "Lucía Méndez - Banco Andino | LinkedIn",
         "link": "https://co.linkedin.com/in/lucia-mendez",
         "snippet": "Directora de Riesgos en Banco Andino · Experiencia: Banco Andino"},
        {"title": "Pedro Otro - CEO - Otra Empresa SAS | LinkedIn",
         "link": "https://co.linkedin.com/in/pedro-otro", "snippet": ""},
        {"title": "Ana Pasado - Ex Gerente General - Banco Andino | LinkedIn",
         "link": "https://co.linkedin.com/in/ana-pasado", "snippet": ""},
        {"title": "Banco Andino | LinkedIn", "link": "https://co.linkedin.com/company/banco-andino"},
    ]
}  # fmt: skip
LEADS = {
    "organic": [
        {"title": "Carlos Ruiz Pardo - Gerente General - Banco Andino | LinkedIn",
         "link": "https://co.linkedin.com/in/carlos-ruiz-pardo-12ab", "snippet": "Bogotá"},
        {"title": "Sofía Lara - CFO - Financiera Austral | LinkedIn",
         "link": "https://co.linkedin.com/in/sofia-lara", "snippet": "Medellín, Antioquia"},
        {"title": "Raúl Mena - Director Comercial - Constructora Andes | LinkedIn",
         "link": "https://co.linkedin.com/in/raul-mena", "snippet": "Constructora de vivienda"},
        {"title": "Inés Soto - Gerente General - Banco del Norte | LinkedIn",
         "link": "https://mx.linkedin.com/in/ines-soto", "snippet": "Monterrey, México"},
        {"title": "Pablo Vega - Ex Gerente General - Banco Andino | LinkedIn",
         "link": "https://co.linkedin.com/in/pablo-vega", "snippet": ""},
    ]
}  # fmt: skip
PLACES = {
    "places": [
        {"title": "Banco Popular del Llano", "website": "https://www.bancollano.com.co/",
         "address": "Cra 7 #1-2, Villavicencio, Meta, Colombia", "category": "Banco",
         "phoneNumber": "+57 608 555 0000"},
        {"title": "Cajero sin web", "address": "Bogotá, Colombia", "category": "Cajero"},
    ]
}  # fmt: skip


@pytest.fixture()
def with_serper(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    from orkesta.core.config import get_settings

    monkeypatch.setattr(get_settings(), "serper_api_key", "clave-serper")
    asked: list[dict] = []

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            body = json.loads(request.content)
            asked.append({"path": request.url.path, **body})
            if request.url.path == "/places":
                return httpx.Response(200, json=PLACES if body.get("page") == 1 else {"places": []})
            q = body["q"]
            if 'site:linkedin.com/in "Banco Andino"' in q:
                return httpx.Response(200, json=XRAY)
            if q.startswith("site:linkedin.com/in (") and body.get("page") == 1:
                return httpx.Response(200, json=LEADS)
            if q.startswith('"Banco Andino"') and "sitio oficial" in q:
                return httpx.Response(200, json={"knowledgeGraph": {
                    "title": "Banco Andino", "website": "https://www.bancoandino.co/"}})  # fmt: skip
            if q.startswith('"Financiera Austral"') and "sitio oficial" in q:
                return httpx.Response(200, json={"organic": [
                    {"link": "https://www.linkedin.com/company/financiera-austral"},
                    {"link": "https://www.financieraaustral.com.co/nosotros"}]})  # fmt: skip
            if q == '"@financieraaustral.com.co"':
                return httpx.Response(200, json={"organic": [{"title": "Contacto",
                    "link": "https://directorio.co/fa", "snippet":
                    "Escriba a Marta Ríos: marta.rios@financieraaustral.com.co o info@financieraaustral.com.co"}]})  # fmt: skip
            return httpx.Response(200, json={"organic": []})
        return _internet(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    yield asked


def test_leads_first_with_company_domain_and_email(
    client: TestClient, session_factory: sessionmaker[Session], with_serper: list[dict]
) -> None:
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, industries=["banca_finanzas"], limit=25)
    assert run["status"] == "completed", run
    people = {p["data"]["full_name"]: p["data"] for p in run["people"]}
    # Leads de varias empresas a la vez; fuera: otra industria, otro país y cargos anteriores.
    assert set(people) >= {"Carlos Ruiz Pardo", "Sofía Lara"}
    assert not {"Raúl Mena", "Inés Soto", "Pablo Vega"} & set(people)
    # Solo perfiles públicos: las personas leídas en sitios web no se suman como leads.
    assert "Diana Castro Pérez" not in people and "Andrés Martínez" not in people
    assert all(p["sources"][0]["type"] == "perfil" for p in run["people"])
    assert people["Sofía Lara"]["company"] == "Financiera Austral"
    assert people["Sofía Lara"]["domain"] == "financieraaustral.com.co"
    # El formato de correo se deduce de un correo público junto al nombre de su dueña.
    assert people["Sofía Lara"]["email"] == "sofia.lara@financieraaustral.com.co"
    assert people["Sofía Lara"]["email_status"] == "estimado"
    # En Banco Andino el sitio publica dcastro@ junto a «Diana Castro»: formato inicial+apellido.
    assert people["Carlos Ruiz Pardo"]["domain"] == "bancoandino.co"
    assert people["Carlos Ruiz Pardo"]["email"] == "cruiz@bancoandino.co"
    assert run["totals"]["domains"] == 2 and run["totals"]["estimated_emails"] >= 2
    queries = [a["q"] for a in with_serper if a["q"].startswith("site:linkedin.com/in (")]
    assert queries and all('"Colombia"' in q and "banco" in q for q in queries)


def test_search_engine_finds_decision_makers_without_ai(
    client: TestClient, session_factory: sessionmaker[Session], with_serper: list[dict]
) -> None:
    register(client, "ana@lacteos.co")
    assert client.get("/api/research/options").json()["web_search"] is True
    run = _run(client, session_factory, companies=["Banco Andino"])
    assert run["status"] == "completed", run
    people = {p["data"]["full_name"]: p for p in run["people"]}
    carlos = people["Carlos Ruiz Pardo"]
    assert carlos["data"]["title"] == "Gerente General"
    assert carlos["data"]["linkedin"] == "https://co.linkedin.com/in/carlos-ruiz-pardo-12ab"
    assert carlos["sources"][0]["type"] == "perfil"
    # El cargo puede venir en el fragmento («Directora de Riesgos en Banco Andino»).
    assert people["Lucía Méndez"]["data"]["title"] == "Directora de Riesgos"
    # Otra empresa y cargos anteriores se descartan.
    assert "Pedro Otro" not in people and "Ana Pasado" not in people
    assert run["totals"]["serp_calls"] >= 1 and run["totals"]["source_perfil"] >= 2
    xray = [a for a in with_serper if a["path"] == "/search" and "linkedin" in a["q"]]
    assert all(a.get("gl") == "co" for a in xray)

    # Repetir la búsqueda usa la caché: no gasta créditos otra vez.
    calls_before = len(with_serper)
    _run(client, session_factory, companies=["Banco Andino"])
    assert len(with_serper) == calls_before


def test_invalid_search_key_is_reported(
    client: TestClient, session_factory: sessionmaker[Session], with_serper: list[dict]
) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            return httpx.Response(403, json={"message": "Unauthorized."})
        return _internet(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, companies=["Banco Andino"])
    assert run["totals"]["serp_fatal_clave"] == 1
    assert any("SERPER_API_KEY" in line["text"] for line in run["progress"]["log"])


def test_profile_title_parsing() -> None:
    from orkesta.research.serp import parse_profile

    assert parse_profile("María Paz – CFO – Grupo Éxito | LinkedIn", "") == (
        "María Paz", "CFO", "Grupo Éxito")  # fmt: skip
    assert parse_profile("José Díaz - Gerente de TI en Alpina | LinkedIn", "") == (
        "José Díaz", "Gerente de TI", "Alpina")  # fmt: skip
    assert parse_profile("Laura Gil - Bavaria | LinkedIn", "Directora Comercial en Bavaria. …") == (
        "Laura Gil", "Directora Comercial", "Bavaria")  # fmt: skip


def test_diagnostics_report_each_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, with_serper: list[dict]
) -> None:
    from orkesta.research import diagnostics, router

    router.diag_limiter.hits.clear()

    def route(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "google.serper.dev":
            return httpx.Response(200, json={**XRAY, "credits": 1})
        if host in ("query.wikidata.org", "www.wikipedia.org"):
            return httpx.Response(200, json={"boolean": True})
        if host == "api.gdeltproject.org":
            return httpx.Response(200, json=GDELT)
        return httpx.Response(503)

    fetch.TRANSPORT = httpx.MockTransport(route)
    monkeypatch.setattr(diagnostics, "_smtp", lambda: (False, "Puerto 25 bloqueado"))
    register(client, "ana@lacteos.co")
    checks = {c["name"]: c for c in client.get("/api/research/diagnostics").json()["checks"]}
    assert not any("Gemini" in n for n in checks)
    assert all(c["ok"] for n, c in checks.items() if "SMTP" not in n), checks
    assert "resultados de Google" in checks["Buscador de Google (Serper)"]["detail"]


def test_sales_navigator_filters(
    client: TestClient, session_factory: sessionmaker[Session], with_serper: list[dict]
) -> None:
    register(client, "ana@lacteos.co")
    options = client.get("/api/research/options").json()
    groups = {g["label"]: g["options"] for g in options["sector_groups"]}
    assert "Tecnología" in groups and "Financieros y consultoría" in groups
    assert any(o["label"] == "Desarrollo de software" for o in groups["Tecnología"])
    assert {o["value"] for o in options["seniority"]} >= {"cxo", "vp", "practicas"}
    assert any(o["label"] == "Tecnología de la información" for o in options["functions"])

    run = _run(
        client, session_factory, sectors=["banca", "servicios_financieros"],
        seniority=["cxo"], exclude_companies=["Financiera Austral"], limit=25,
    )  # fmt: skip
    assert run["status"] == "completed", run
    names = {p["data"]["full_name"] for p in run["people"]}
    assert "Carlos Ruiz Pardo" in names and "Sofía Lara" not in names  # empresa excluida
    assert run["filters"]["industries"] == ["banca_finanzas"]
    queries = [a["q"] for a in with_serper if a["q"].startswith("site:linkedin.com/in")]
    assert any('"Gerente General"' in q and "banco" in q for q in queries)
    assert all('-"Financiera Austral"' in q for q in queries if "banco" in q)

    # El historial solo guarda lo que el usuario guarda.
    assert client.get("/api/research/runs").json() == []
    saved = client.post(f"/api/research/runs/{run['id']}/saved", json={"name": "Bancos CXO"})
    assert saved.status_code == 200 and saved.json()["saved_name"] == "Bancos CXO"
    assert [r["saved_name"] for r in client.get("/api/research/runs").json()] == ["Bancos CXO"]
    other = _run(client, session_factory, sectors=["banca"], titles=["Gerente General"])
    assert other["status"] == "completed"
    third = client.post("/api/research/runs", json={"country": "CO"}).json()
    # Al empezar otra búsqueda se borra la anterior que no se guardó; la guardada sigue.
    assert client.get(f"/api/research/runs/{other['id']}").status_code == 404
    assert client.get(f"/api/research/runs/{run['id']}").status_code == 200
    client.post(f"/api/research/runs/{third['id']}/cancel")
    assert client.delete(f"/api/research/runs/{run['id']}").status_code == 200
    assert client.get("/api/research/runs").json() == []


def test_title_seniority_and_functions() -> None:
    from orkesta.engine.taxonomy import linkedin as li
    from orkesta.research.agent import Filters

    assert li.title_seniority("Gerente General") == "cxo"
    assert li.title_seniority("Gerente de Compras") == "gestion_experiencia"
    assert li.title_seniority("Practicante de Marketing") == "practicas"
    assert li.title_seniority("Analista de datos") == "sin_experiencia"
    assert "compras" in li.title_functions("Jefe de Abastecimiento")
    assert "ti" in li.title_functions("Director de Tecnología")
    f = Filters.from_payload({"country": "CO", "functions": ["ti"], "seniority": ["director"],
                              "exclude_titles": ["interino"]})  # fmt: skip
    assert f.wants_title("Director de Sistemas")
    assert not f.wants_title("Director Comercial")
    assert not f.wants_title("Director de TI interino")
    titled = Filters.from_payload({"country": "CO", "titles": ["Gerente de Compras"]})
    assert titled.wants_title("Gerente Nacional de Compras")
    assert not titled.wants_title("Gerente de Ventas")


COMPANY_PAGES = {
    "organic": [
        {"title": "Banco Andino | LinkedIn", "link": "https://co.linkedin.com/company/banco-andino",
         "snippet": "Banco Andino | 120.000 seguidores en LinkedIn. Banca para personas y "
                    "empresas en Colombia. Sector: Banca · Tamaño de la empresa: 1.001-5.000 empleados"},
        {"title": "Fintech Mini | LinkedIn", "link": "https://co.linkedin.com/company/fintech-mini",
         "snippet": "Pagos digitales en Colombia. Tamaño de la empresa: 2-10 empleados"},
        {"title": "Empleos en Banco Andino", "link": "https://co.linkedin.com/company/banco-andino/jobs"},
        {"title": "Banco del Norte | LinkedIn", "link": "https://mx.linkedin.com/company/banco-norte",
         "snippet": "Banca en Monterrey, México"},
    ]
}  # fmt: skip


def test_account_search(
    client: TestClient, session_factory: sessionmaker[Session], with_serper: list[dict]
) -> None:
    inner = fetch.TRANSPORT
    assert inner is not None

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev" and request.url.path == "/search":
            body = json.loads(request.content)
            if body["q"].startswith("site:linkedin.com/company"):
                with_serper.append(body)
                return httpx.Response(
                    200, json=COMPANY_PAGES if body.get("page") == 1 else {"organic": []}
                )
        return inner.handle_request(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    register(client, "ana@lacteos.co")
    run = _run(client, session_factory, mode="accounts", sectors=["banca"],
               sizes=["1001-5000"], include_base=False)  # fmt: skip
    assert run["status"] == "completed", run
    assert run["summary"].startswith("Cuentas")
    accounts = {c["data"]["name"]: c for c in run["companies"]}
    # Fuera: otro país, otra plantilla y páginas internas de LinkedIn.
    assert "Banco Andino" in accounts and "Banco del Norte" not in accounts
    assert "Fintech Mini" not in accounts
    andino = accounts["Banco Andino"]
    assert andino["data"]["size"] == "1001-5000"
    assert andino["data"]["linkedin"] == "https://co.linkedin.com/company/banco-andino"
    assert andino["data"]["domain"] == "bancoandino.co"
    assert run["people"] == []
    saved = client.post(f"/api/research/runs/{run['id']}/accounts", json={"ids": [andino["id"]]})
    assert saved.json() == {"saved": 1}
