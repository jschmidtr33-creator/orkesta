"""Base de prospectos: lectura de bases, patrones reales, búsqueda, worker, web y correos.

Todos los datos son inventados.
"""

from __future__ import annotations

import io
import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.api.main import create_app
from orkesta.auth import create_user
from orkesta.catalog.industries import classify, size_band
from orkesta.catalog.prospects import (
    infer_country,
    learn_patterns,
    map_columns,
    read_sheets,
    records_from_file,
)
from orkesta.catalog.service import (
    ProspectFilters,
    create_upload,
    known_pattern,
    refresh_domain_patterns,
    search_prospects,
    upsert_prospects,
)
from orkesta.db.models import DomainPattern, Organization, Prospect, ProspectUpload
from orkesta.engine.email.decide import decide_from_known, generate_emails
from orkesta.engine.normalize import normalize_person
from orkesta.worker.__main__ import FAILURE_HOOKS, HANDLERS
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import Worker

WIZA_CSV = (
    "list_name,email,first_name,last_name,title,company,country,locality,mobile_phone1,"
    "company_industry,company_description,linkedin\n"
    "demo,ana.lopez@lacteos.com.mx,Ana,López,Gerente de Compras,Lácteos Inventados,Mexico,"
    "Monterrey,+52 81 0000 0001,Food & Beverages,Una descripción larga,https://x\n"
    "demo,jorge.diaz@lacteos.com.mx,Jorge,Díaz,Director de TI,Lácteos Inventados,Mexico,"
    "Monterrey,,Food & Beverages,,\n"
    "demo,mperez@bancoficticio.co,María,Pérez,Jefe de Compras,Banco Ficticio,Colombia,Bogotá,"
    ",Banking,,\n"
).encode()


def _xlsx(rows: list[list[object]], sheets: int = 1) -> bytes:
    workbook = Workbook()
    for index in range(sheets):
        sheet = workbook.active if index == 0 else workbook.create_sheet(f"Hoja{index + 1}")
        assert sheet is not None
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --- Industrias ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BANCA-SEGUROS-FINANZAS-FINTECH", "banca_finanzas"),
        ("Insurance", "seguros"),
        ("Information Technology & Services", "tecnologia_software"),
        ("Hospitality", "turismo_hoteleria"),
        ("Hospital & Health Care", "farmaceutica_salud"),
        ("business services", None),
        ("mediana empresa", None),
        ("GOBIERNO", "gobierno"),
        ("IA", "tecnologia_software"),
        ("OTRO", None),
    ],
)
def test_industry_taxonomy(text: str, expected: str | None) -> None:
    assert classify(text) == expected


def test_size_bands() -> None:
    assert (size_band(4500), size_band(None), size_band(20000)) == ("1001-5000", None, "10001+")


# --- Lectura y registros ---------------------------------------------------------------------


def test_wiza_export_keeps_only_needed_fields() -> None:
    records = records_from_file(WIZA_CSV, "wiza.csv")
    ana = next(r for r in records if r.first_name == "Ana")
    assert (ana.full_name, ana.title, ana.company) == (
        "Ana López",
        "Gerente de Compras",
        "Lácteos Inventados",
    )
    assert (ana.email, ana.email_domain, ana.phone) == (
        "ana.lopez@lacteos.com.mx",
        "lacteos.com.mx",
        "+528100000001",
    )
    assert (ana.city, ana.country_code, ana.industry_code) == (
        "Monterrey",
        "MX",
        "alimentos_bebidas",
    )
    assert not hasattr(ana, "linkedin")


def test_event_form_headers_and_all_sheets() -> None:
    content = _xlsx(
        [
            ["Nombre", "Apellido", "E-mail", "Teléfono", "Cargo", "Empresa  Organización", "País"],
            ["Luis", "Ruiz", "luis.ruiz@retail.cl", 56900000000, "CEO", "Retail Ficticio", "Chile"],
        ],
        sheets=2,
    )
    sheets = read_sheets(content, "registro.xlsx")
    assert len(sheets) == 2
    assert {"first", "last", "email", "phone", "title", "company", "country"} <= set(
        map_columns(sheets[0])
    )
    records = records_from_file(content, "registro.xlsx")
    assert len(records) == 1  # la misma persona en dos hojas se fusiona
    assert records[0].phone == "56900000000"


def test_headerless_file_is_mapped_by_content() -> None:
    rows: list[list[object]] = [
        ["Ana", "López", "ana@lacteos.com.mx", "+525500000001", "Lácteos Inventados", "Mexico",
         "RETAIL-CONSUMO", "Gerente Comercial"],
        ["Luis", "Ruiz", "luis@retail.cl", "+56900000002", "Retail Ficticio", "Chile",
         "TELCOS-TELECOM", "Director de Ventas"],
        ["Eva", "Soto", "eva@otra.com.mx", "+525500000003", "Telecom Ficticia", "Mexico",
         "RETAIL-CONSUMO", "Jefa de Compras"],
    ]  # fmt: skip
    records = records_from_file(_xlsx(rows), "sin_encabezados.xlsx")
    assert {r.full_name for r in records} == {"Ana López", "Luis Ruiz", "Eva Soto"}
    luis = next(r for r in records if r.first_name == "Luis")
    assert (luis.company, luis.title, luis.country_code) == (
        "Retail Ficticio",
        "Director de Ventas",
        "CL",
    )


def test_full_name_column_and_country_inference() -> None:
    content = (
        b"NOMBRE,EMAIL,CARGO,EMPRESA\nSofia Gomez,sofia.gomez@empresa.com.pe,CFO,Empresa Uno\n"
    )
    record = records_from_file(content, "campana.csv")[0]
    assert (record.first_name, record.last_name, record.country_code) == ("Sofia", "Gomez", "PE")
    assert infer_country("+573000000000", None) == "CO"


def test_long_cells_do_not_reject_the_file() -> None:
    long = "x" * 5000
    content = f"first_name,last_name,company,company_description\nAna,López,Uno,{long}\n".encode()
    assert len(records_from_file(content, "wiza.csv")) == 1


def test_learn_patterns_from_known_emails() -> None:
    learned = learn_patterns(records_from_file(WIZA_CSV, "wiza.csv"))
    assert learned["lacteos.com.mx"].pattern == "first.last"
    assert (learned["lacteos.com.mx"].people, learned["lacteos.com.mx"].agreeing) == (2, 2)
    assert learned["bancoficticio.co"].pattern == "flast"


def test_decision_from_known_pattern() -> None:
    decision = decide_from_known(
        "lacteos.com.mx", "first.last", people=3, agreeing=3, country="MX", mx_provider=None
    )
    assert decision.confidence_level == "high" and decision.reasons == ["PATTERN_FROM_BASE"]
    emails = generate_emails(normalize_person("Carla", "Méndez"), decision)
    assert emails[0].address == "carla.mendez@lacteos.com.mx"
    single = decide_from_known(
        "x.com", "flast", people=1, agreeing=1, country=None, mx_provider=None
    )
    assert single.confidence_level == "medium"


# --- Base de datos ---------------------------------------------------------------------------

pytest_db = pytest.mark.db


@pytest.fixture()
def org_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        return org.id


@pytest_db
def test_upsert_merges_without_erasing(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    records = records_from_file(WIZA_CSV, "wiza.csv")
    with session_factory.begin() as session:
        first = upsert_prospects(session, org_id, records, source="base", source_name="a.csv")
    assert first == {"personas": 3, "nuevas": 3, "actualizadas": 0}
    # Segunda carga de Ana sin teléfono ni ciudad y con cargo nuevo: completa, no borra.
    again = (
        b"email,first_name,last_name,title,company\n"
        b"ana.lopez@lacteos.com.mx,Ana,Lopez,Directora de Compras,Lacteos Inventados\n"
    )
    with session_factory.begin() as session:
        second = upsert_prospects(session, org_id, records_from_file(again, "b.csv"), source="base")
        ana = session.scalar(select(Prospect).where(Prospect.email == "ana.lopez@lacteos.com.mx"))
        assert ana is not None
    assert second == {"personas": 1, "nuevas": 0, "actualizadas": 1}
    assert (ana.title, ana.phone, ana.city) == (
        "Directora de Compras",
        "+528100000001",
        "Monterrey",
    )


@pytest_db
def test_search_filters(session_factory: sessionmaker[Session], org_id: uuid.UUID) -> None:
    with session_factory.begin() as session:
        upsert_prospects(session, org_id, records_from_file(WIZA_CSV, "w.csv"), source="base")
    with session_factory() as session:

        def names(**kwargs: object) -> set[str]:
            rows, total = search_prospects(session, org_id, ProspectFilters(**kwargs))  # type: ignore[arg-type]
            assert total == len(rows)
            return {r.full_name for r in rows}

        assert names(title="compras") == {"Ana López", "María Pérez"}
        assert names(title="gerente compras") == {"Ana López"}
        assert names(countries=["CO"]) == {"María Pérez"}
        assert names(industries=["banca_finanzas"]) == {"María Pérez"}
        assert names(company="lacteos", title="TI") == {"Jorge Díaz"}
        assert names(city="monterrey") == {"Ana López", "Jorge Díaz"}
        assert names(title="100%") == set()  # comodines escapados
        assert names(title="ti") == {"Jorge Díaz"}  # inicio de palabra, no "estratégico"
        assert names(title="ras") == set()  # "compras" no empieza por "ras"


@pytest_db
def test_domain_patterns_and_brand_lookup(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    with session_factory.begin() as session:
        upsert_prospects(session, org_id, records_from_file(WIZA_CSV, "w.csv"), source="base")
        assert refresh_domain_patterns(session, org_id, ["lacteos.com.mx", "bancoficticio.co"]) == 2
    with session_factory() as session:
        exact = known_pattern(session, org_id, "lacteos.com.mx")
        brand = known_pattern(session, org_id, "lacteos.com")  # misma marca, otra extensión
        assert exact is not None and brand is not None
        assert exact.pattern == brand.pattern == "first.last"
        assert known_pattern(session, org_id, "otra.com") is None


@pytest_db
def test_upload_is_processed_by_worker_and_file_discarded(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    with session_factory.begin() as session:
        upload_id = create_upload(
            session, organization_id=org_id, user_id=None, filename="wiza.csv", content=WIZA_CSV
        ).id
    worker = Worker(
        session_factory, HANDLERS, retry_policy=RetryPolicy(1, 1), poll_interval_seconds=0.01,
        lock_timeout_seconds=600, failure_hooks=FAILURE_HOOKS,
    )  # fmt: skip
    while worker.run_once():
        pass
    with session_factory() as session:
        upload = session.get(ProspectUpload, upload_id)
        assert upload is not None
        assert upload.status == "completed" and upload.content is None
        assert upload.summary["personas"] == 3 and upload.summary["dominios_con_patron"] == 2
        assert session.scalar(select(DomainPattern).where(DomainPattern.organization_id == org_id))


@pytest_db
def test_bad_upload_fails_cleanly(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    with session_factory.begin() as session:
        upload_id = create_upload(
            session, organization_id=org_id, user_id=None, filename="x.csv", content=b"a,b\n1,2\n"
        ).id
    worker = Worker(
        session_factory, HANDLERS, retry_policy=RetryPolicy(1, 1), poll_interval_seconds=0.01,
        lock_timeout_seconds=600, failure_hooks=FAILURE_HOOKS,
    )  # fmt: skip
    while worker.run_once():
        pass
    with session_factory() as session:
        upload = session.get(ProspectUpload, upload_id)
        assert upload is not None
        assert upload.status == "failed" and upload.content is None
        assert "nombre y empresa" in (upload.error or "")


@pytest.fixture()
def client(engine: Engine, session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        create_user(
            session, email="jenn@ejemplo.com", password="clave-de-prueba-1", organization_id=org.id
        )
        upsert_prospects(session, org.id, records_from_file(WIZA_CSV, "w.csv"), source="base")
    with TestClient(create_app(engine_provider=lambda: engine), follow_redirects=False) as c:
        token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text)
        assert token
        c.post(
            "/login",
            data={
                "email": "jenn@ejemplo.com",
                "password": "clave-de-prueba-1",
                "csrf_token": token.group(1),
            },
        )
        yield c


@pytest_db
def test_prospects_page_search_and_export(client: TestClient) -> None:
    page = client.get("/prospectos", params={"cargo": "compras", "pais": "MX"}).text
    assert "Ana López" in page and "María Pérez" not in page
    assert "Personas en tu base" in page
    response = client.get("/prospectos/exportar", params={"industria": "banca_finanzas"})
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert (
        text.splitlines()[0]
        == "Nombre completo,Cargo,Empresa,Correo,Teléfono,Ciudad,País,Industria"
    )
    assert "María Pérez" in text and "Ana López" not in text


@pytest_db
def test_upload_from_page_requires_csrf_and_queues(client: TestClient) -> None:
    files = {"archivo": ("base.csv", WIZA_CSV, "text/csv")}
    assert client.post("/prospectos/cargar", files=files).status_code in (400, 403)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/prospectos").text)
    assert token
    response = client.post("/prospectos/cargar", files=files, data={"csrf_token": token.group(1)})
    assert response.status_code == 303
    assert "procesando" in client.get("/prospectos").text


# --- Cargos: nivel y área --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Gerente de Compras", ("gerente", "compras")),
        ("Purchasing Manager", ("gerente", "compras")),
        ("Jefe de Abastecimiento", ("jefe", "compras")),
        ("CIO", ("c_level", "tecnologia")),
        ("IT Director", ("director", "tecnologia")),
        ("Gerente General", ("c_level", "direccion")),
        ("Founder & CTO", ("c_level", "tecnologia")),
        ("Vice President of Sales", ("vp", "comercial")),
        ("Jefe de Operaciones TI", ("jefe", "tecnologia")),
        ("HR Business Partner", (None, "rrhh")),
        ("Head of Data", ("director", "datos")),
        ("CISO", ("c_level", "seguridad")),
        ("Gerente de Tienda", ("gerente", None)),
        ("Auditor de TI", ("especialista", "tecnologia")),
        ("", (None, None)),
    ],
)
def test_title_level_and_area(title: str, expected: tuple[str | None, str | None]) -> None:
    from orkesta.catalog.titles import classify_title

    assert classify_title(title) == expected


@pytest_db
def test_search_by_level_and_area(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    with session_factory.begin() as session:
        upsert_prospects(session, org_id, records_from_file(WIZA_CSV, "w.csv"), source="base")
    with session_factory() as session:
        rows, _ = search_prospects(session, org_id, ProspectFilters(areas=["compras"]))
        assert {r.full_name for r in rows} == {"Ana López", "María Pérez"}
        rows, _ = search_prospects(
            session, org_id, ProspectFilters(areas=["compras"], levels=["gerente"])
        )
        assert {r.full_name for r in rows} == {"Ana López"}
        rows, _ = search_prospects(session, org_id, ProspectFilters(areas=["tecnologia"]))
        assert {r.full_name for r in rows} == {"Jorge Díaz"}


def test_decision_makers() -> None:
    from orkesta.catalog.titles import is_decision_maker

    assert all(
        is_decision_maker(t)
        for t in ("CEO", "Cofundador", "Dueño", "VP Sales", "Directora de Compras", "Jefe de TI")
    )
    assert not any(is_decision_maker(t) for t in ("Gerente de Compras", "Analista", "", None))


@pytest_db
def test_first_visit_shows_only_decision_makers(client: TestClient) -> None:
    first = client.get("/prospectos").text
    # Ana (gerente) no es decisora; Jorge (director) y María (jefe) sí.
    assert "Jorge Díaz" in first and "María Pérez" in first and "Ana López" not in first
    everyone = client.get("/prospectos", params={"cargo": ""}).text
    assert "Ana López" in everyone
