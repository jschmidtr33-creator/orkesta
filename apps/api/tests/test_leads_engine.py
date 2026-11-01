"""Motor de búsqueda v2: cargos reales, abanico de consultas, división por ciudades, dominios
deducidos y memoria de formatos de correo que aprende de las campañas."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import fetch, leads, memory, serp
from orkesta.research.fetch import Fetcher

FIRST = ["Ana", "Carlos", "Lucía", "Jorge", "María", "Andrés", "Laura", "Diego", "Paula", "Felipe",
         "Camila", "Santiago", "Valentina", "Juan", "Sofía", "Daniel", "Natalia", "Mateo", "Isabel",
         "Ricardo"]  # fmt: skip
LAST = ["Gómez", "Rodríguez", "Martínez", "López", "Pérez", "Sánchez", "Ramírez", "Torres",
        "Flores", "Rivera", "Vargas", "Castro", "Ortiz", "Rojas", "Moreno"]  # fmt: skip


@pytest.fixture()
def serper(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    from orkesta.core.config import get_settings

    monkeypatch.setattr(get_settings(), "serper_api_key", "clave")
    asked: list[str] = []
    counter = iter(range(100_000))

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            body = json.loads(request.content)
            asked.append(body["q"])
            rows = []
            for _ in range(10):
                n = next(counter)
                first, last = FIRST[n % len(FIRST)], LAST[(n // len(FIRST)) % len(LAST)]
                second = LAST[(n // (len(FIRST) * len(LAST))) % len(LAST)]
                rows.append({"title": f"{first} {last} {second} - Gerente de Compras - Empresa {n % 40} | LinkedIn",
                             "link": f"https://co.linkedin.com/in/p{n}", "snippet": "Bogotá, Colombia"})  # fmt: skip
            return httpx.Response(200, json={"organic": rows})
        if request.url.host == "www.alpina.com" or request.url.host == "alpina.com":
            return httpx.Response(200, text="<title>Alpina Productos Alimenticios</title> alpina")
        return httpx.Response(404, text="")

    fetch.TRANSPORT = httpx.MockTransport(route)
    fetch.RESOLVER = lambda host: ["93.184.216.34"]
    yield asked
    fetch.TRANSPORT, fetch.RESOLVER = None, None


def test_title_planner_speaks_like_linkedin() -> None:
    titles = leads.title_phrases(["gestion_experiencia", "director", "cxo"], ["compras"], [])
    assert {"Gerente de Compras", "Gerente de Abastecimiento", "Procurement Manager",
            "Director de Compras", "Head of Procurement", "CPO"} <= set(titles)  # fmt: skip
    generic = leads.title_phrases([], [], [])
    assert "Gerente General" in generic and "CEO" in generic and "Gerente" in generic
    assert leads.title_phrases(["cxo"], [], ["Gerente de Planta"]) == ["Gerente de Planta"]


def test_saturated_queries_split_by_city_and_collect_hundreds(
    serper: list[str], session_factory: sessionmaker[Session]
) -> None:
    plan = leads.plan_people(country="CO", country_name="Colombia", cities=[],
                             phrases=["Gerente de Compras"], sector_terms=[], keywords="",
                             companies=[], exclude=[])  # fmt: skip
    assert plan.queries == ['site:linkedin.com/in "Gerente de Compras" "Colombia"']
    stats = serp.SerpStats(budget=200)
    with Fetcher(session_factory) as fetcher:
        collector = leads.Collector(
            fetcher, stats, country="CO", country_name="Colombia", cities=[],
            accept=lambda *a: True, industries=[], excluded=set(), target=1000,
        )  # fmt: skip
        collector.run_all(plan)
    assert len(collector.found) > 150  # 10 páginas por consulta + 6 ciudades
    assert any('"Medellín"' in q for q in serper) and any('"Cali"' in q for q in serper)


def test_domain_guess_and_pattern_memory(
    serper: list[str], session_factory: sessionmaker[Session]
) -> None:
    with Fetcher(session_factory) as fetcher:
        assert leads.guess_domain(fetcher, "Alpina S.A.", "CO") == "alpina.com"
    assert memory.known_pattern(session_factory, "alpina.com") == ("", 0, "")
    memory.remember_pattern(session_factory, "alpina.com", "flast", 2, "https://x")
    pattern, confidence, label = memory.known_pattern(session_factory, "alpina.com")
    assert pattern == "flast" and confidence == 55 and "2 correo" in label
    with session_factory.begin() as s:
        memory.feedback(s, "agomez@alpina.com", "flast", "replied")
    assert memory.known_pattern(session_factory, "alpina.com")[1] == 75
    with session_factory.begin() as s:
        for _ in range(5):
            memory.feedback(s, "x@alpina.com", "flast", "bounced")
    assert memory.known_pattern(session_factory, "alpina.com") == ("", 0, "")
    memory.remember_domain(session_factory, "CO", "alpina", "alpina.com", "maps")
    assert memory.known_domain(session_factory, "CO", "alpina") == "alpina.com"


def test_full_search_brings_volume_and_emails(
    client: object, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    from fastapi.testclient import TestClient

    from orkesta.core.config import get_settings
    from orkesta.core.jobs import run_once
    from tests.conftest import register

    assert isinstance(client, TestClient)
    monkeypatch.setattr(get_settings(), "serper_api_key", "clave")
    counter = iter(range(100_000))

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host != "google.serper.dev":
            return httpx.Response(404, text="")
        body = json.loads(request.content)
        q = body["q"]
        if "sitio oficial" in q:
            n = re.search(r"Empresa (\d+)", q)
            return httpx.Response(
                200,
                json={
                    "knowledgeGraph": {
                        "website": f"https://www.empresa{n.group(1) if n else 0}.com"
                    }
                },
            )
        if q.startswith('"@'):
            domain = q.strip('"@')
            return httpx.Response(200, json={"organic": [{"title": "Contacto", "link": "https://dir.co",
                                                          "snippet": f"Ana Gómez agomez@{domain}"}]})  # fmt: skip
        if not q.startswith("site:linkedin.com/in"):
            return httpx.Response(200, json={"organic": []})
        rows = []
        for _ in range(10):
            n = next(counter)
            first, last = FIRST[n % len(FIRST)], LAST[(n // len(FIRST)) % len(LAST)]
            second = LAST[(n // (len(FIRST) * len(LAST))) % len(LAST)]
            rows.append({"title": f"{first} {last} {second} - Gerente Comercial - Empresa {(n // 20) % 30} | LinkedIn",
                         "link": f"https://co.linkedin.com/in/q{n}", "snippet": "Medellín, Antioquia, Colombia"})  # fmt: skip
        return httpx.Response(200, json={"organic": rows})

    fetch.TRANSPORT = httpx.MockTransport(route)
    fetch.RESOLVER = lambda host: ["93.184.216.34"]
    from orkesta.research import router

    router.run_limiter.hits.clear()
    try:
        register(client, "ana@lacteos.co")
        created = client.post("/api/research/runs", json={"country": "CO", "functions": ["ventas"],
                                                          "seniority": ["gestion_experiencia"]}).json()  # fmt: skip
        while run_once(session_factory, "pruebas"):
            pass
        run = client.get(f"/api/research/runs/{created['id']}").json()
    finally:
        fetch.TRANSPORT, fetch.RESOLVER = None, None
    assert run["status"] == "completed", run
    people = run["people"]
    assert len(people) >= 200
    with_email = [p for p in people if p["data"]["email"]]
    assert len(with_email) == len(people)  # cada lead con su empresa identificada trae correo
    sample = with_email[0]["data"]
    assert re.fullmatch(r"[a-z]+@empresa\d+\.com", sample["email"])  # formato inicial+apellido
    assert all(p["data"]["linkedin"].startswith("https://co.linkedin.com/in/") for p in people)
    assert memory.known_pattern(session_factory, "empresa1.com")[0] == "flast"
