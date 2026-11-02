"""Directorio propio de empresas: registros públicos (Socrata y CSV), columnas detectadas
solas, códigos CIIU/SCIAN, aprendizaje de las búsquedas y uso en la búsqueda de Cuentas."""

from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import directory
from tests.conftest import register
from tests.test_admin import _role, _unlock

SUPERSOC = [
    {"nit": "860002964", "raz_n_social": "BANCO DE BOGOTA S.A.", "ciiu": "6412",
     "departamento_domicilio": "BOGOTA D.C.", "ciudad_domicilio": "BOGOTA D.C.",
     "macrosector": "SERVICIOS", "ingresos_operacionales": "$12.345.678.000.000",
     "a_o_de_corte": "2025"},
    {"nit": "890900608-9", "raz_n_social": "ALMACENES EXITO S.A.", "ciiu": "4711",
     "departamento_domicilio": "ANTIOQUIA", "ciudad_domicilio": "ENVIGADO",
     "macrosector": "COMERCIO", "ingresos_operacionales": "21.000.000.000.000",
     "a_o_de_corte": "2025"},
    {"nit": "900123456", "raz_n_social": "SOFTWARE ANDINO S.A.S.", "ciiu": "6201",
     "departamento_domicilio": "ANTIOQUIA", "ciudad_domicilio": "MEDELLIN",
     "macrosector": "SERVICIOS", "ingresos_operacionales": "85000000000",
     "a_o_de_corte": "2025"},
]  # fmt: skip


def test_columns_and_values() -> None:
    cols = directory.detect_columns(SUPERSOC[0].keys())
    assert cols["tax_id"] == "nit" and cols["name"] == "raz_n_social"
    assert cols["city"] == "ciudad_domicilio" and cols["revenue"] == "ingresos_operacionales"
    denue = directory.detect_columns(["id", "nom_estab", "raz_social", "codigo_act", "nombre_act",
                                      "per_ocu", "www", "entidad", "municipio"])  # fmt: skip
    assert denue["name"] == "raz_social" and denue["employees"] == "per_ocu"
    assert directory.parse_number("$1.234.567") == Decimal("1234567")
    assert directory.parse_number("1,234,567.89") == Decimal("1234567.89")
    assert directory.parse_number("1234567,5") == Decimal("1234567.5")
    assert directory.parse_employees("51 a 100 personas") == 75
    assert directory.parse_employees("251 y más personas") == 251
    assert directory.clean_tax_id("890.900.608-9") == "890900608"
    assert directory.industry_from_code("6412")[1] == "banca_finanzas"
    assert directory.industry_from_code("C1011")[1] == "alimentos_bebidas"
    assert directory.industry_from_code("524110", "naics")[1] == "seguros"
    assert directory.industry_from_code("461110", "naics")[1] == "retail_consumo"


def _portal(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api/catalog" in url:
        return httpx.Response(200, json={"results": [{"resource": {
            "id": "abcd-1234", "columns_field_name": list(SUPERSOC[0])}}]})  # fmt: skip
    if "/resource/abcd-1234.json" in url:
        offset = int(request.url.params.get("$offset", "0"))
        return httpx.Response(200, json=SUPERSOC if offset == 0 else [])
    return httpx.Response(404)


def test_preset_import_search_and_learning(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory.TRANSPORT = httpx.MockTransport(_portal)
    try:
        result = directory.run_preset(session_factory, "co_supersociedades")
    finally:
        directory.TRANSPORT = None
    assert result.saved == 3 and not result.error
    with session_factory() as session:
        banks = directory.search(session, country="CO", industries=["banca_finanzas"],
                                 exclude_industries=[], cities=[], keywords="", sizes=[])  # fmt: skip
        assert [b.name for b in banks] == ["BANCO DE BOGOTA S.A."]
        antioquia = directory.search(session, country="CO", industries=[],
                                     exclude_industries=["retail_consumo"], cities=["Antioquia"],
                                     keywords="", sizes=[])  # fmt: skip
        assert [e.name for e in antioquia] == ["SOFTWARE ANDINO S.A.S."]
        assert "Ingresos reportados" in antioquia[0].description()
        overview = directory.overview(session)
        assert overview["total"] == 3 and overview["presets"][0]["last"]["status"] == "done"

    # Una búsqueda encontró el sitio web: se completa la ficha sin duplicar la empresa.
    found = SimpleNamespace(name="Software Andino SAS", domain="softwareandino.co",
                            website="https://softwareandino.co", linkedin="", industry_code="",
                            industry="", city="", employees=120)  # fmt: skip
    new = SimpleNamespace(name="Lácteos del Valle", domain="lacteosvalle.co", website="",
                          linkedin="", industry_code="alimentos_bebidas", industry="Alimentos",
                          city="Cali", employees=None)  # fmt: skip
    assert directory.learn(session_factory, "CO", [found, new]) == 2
    with session_factory() as session:
        entries = directory.search(session, country="CO", industries=[], exclude_industries=[],
                                   cities=[], keywords="software", sizes=[])  # fmt: skip
        assert entries[0].domain == "softwareandino.co" and entries[0].employees == 120
        assert directory.overview(session)["total"] == 4

    # La búsqueda de Cuentas usa el directorio.
    from orkesta.research.agent import Filters, from_directory

    logs: list[str] = []
    run: Any = SimpleNamespace(factory=session_factory, log=lambda text, **_: logs.append(text))
    filters = Filters.from_payload({"country": "CO", "mode": "accounts",
                                    "industries": ["tecnologia_software"]})  # fmt: skip
    candidates = from_directory(run, filters)
    assert [c.name for c in candidates] == ["SOFTWARE ANDINO S.A.S."]
    assert candidates[0].domain == "softwareandino.co"
    assert candidates[0].sources[0]["label"].startswith("Registro público oficial")


def test_admin_upload_csv(client: TestClient, database: tuple[str, str]) -> None:
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")
    csv_text = (
        "id;nom_estab;raz_social;codigo_act;nombre_act;per_ocu;www;entidad;municipio\n"
        "1;Tienda Sol;COMERCIAL SOL SA DE CV;461110;Comercio al por menor;"
        "11 a 30 personas;www.comercialsol.mx;Jalisco;Guadalajara\n"
        "2;Seguros Uno;SEGUROS UNO SA;524110;Compañías de seguros;251 y más personas;;"
        "Ciudad de México;Cuauhtémoc\n"
    ).encode("latin-1")
    r = client.post("/api/admin/directory/upload", data={"country": "MX", "source": "DENUE"},
                    files={"file": ("denue.csv", csv_text, "text/csv")})  # fmt: skip
    assert r.status_code == 200 and r.json()["started"]
    for _ in range(50):
        live = directory.STATE.get("archivo:DENUE", {})
        if live.get("status") != "running":
            break
        time.sleep(0.1)
    data = client.get("/api/admin/directory").json()
    assert data["total"] == 2
    assert data["uploads"][0]["last"]["saved"] == 2
    assert data["countries"][0] == {"country": "MX", "companies": 2, "with_domain": 1}
