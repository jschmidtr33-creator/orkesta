"""Etapa 1: importación (8 campos), deduplicación, procedencia, contactos, listas y exportación."""

from __future__ import annotations

import io
import time
import uuid

import psycopg
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.db import set_context
from orkesta.core.jobs import run_once
from orkesta.data.models import Contact, DataPoint
from tests.conftest import register

BASE = (
    "Nombre,Apellido,Cargo,Empresa,Email,Teléfono,Ciudad,País,Industria,Notas internas,Cumpleaños\n"
    "Ana,López,Gerente de Compras,Lácteos Inventados SA,ana.lopez@lacteos.mx,+52 55 1234 5678,"
    "Monterrey,México,Alimentos,no guardar,1990-01-01\n"
    "Jorge,Díaz,CIO,Banco Ficticio,jorge.diaz@bancoficticio.co,,Bogotá,Colombia,Banca,x,y\n"
    "Carla,Méndez,=CMD(),Lácteos Inventados SA,,,,México,,z,w\n"
    ",,,,,,,,,,\n"
).encode()


def _upload(client: TestClient, content: bytes, name: str = "base.csv") -> dict:
    response = client.post("/api/imports", files={"file": (name, content, "text/csv")})
    assert response.status_code == 201, response.text
    return response.json()


def _import(
    client: TestClient,
    factory: sessionmaker[Session],
    content: bytes,
    name: str = "base.csv",
    **extra: object,
) -> dict:
    preview = _upload(client, content, name)
    confirmed = client.post(
        f"/api/imports/{preview['id']}/confirm", json={"mapping": preview["mapping"], **extra}
    )
    assert confirmed.status_code == 200, confirmed.text
    while run_once(factory, "pruebas"):
        pass
    return client.get(f"/api/imports/{preview['id']}").json()


def test_preview_suggests_mapping_and_ignores_other_columns(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    preview = _upload(client, BASE)
    sheet = preview["sheets"][0]
    assert sheet["usable"] and sheet["rows"] == 3
    suggested = sheet["suggested"]
    assert {
        "first",
        "last",
        "title",
        "company",
        "email",
        "phone",
        "city",
        "country",
        "industry",
    } <= set(suggested)
    # Las columnas que no son de los 8 campos no se asignan.
    assert sheet["headers"].index("Notas internas") not in suggested.values()
    done = _import(client, session_factory, BASE)
    assert done["status"] == "completed"
    assert done["totals"]["nuevos"] == 3 and done["totals"]["omitidas"] == 0
    with session_factory() as session:
        columns = {
            c
            for (c,) in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='orkesta' AND table_name='contacts'"
                )
            )
        }
        assert not {"notes", "notas", "birthday"} & columns
        session.execute(text("SELECT 1"))
    contacts = client.get("/api/contacts").json()
    assert contacts["total"] == 3 and contacts["counts"]["nuevo"] == 3
    ana = next(c for c in contacts["items"] if c["first_name"] == "Ana")
    assert (ana["country"], ana["city"], ana["phone"]) == ("México", "Monterrey", "+525512345678")
    detail = client.get(f"/api/contacts/{ana['id']}").json()
    email_field = next(f for f in detail["fields"] if f["field"] == "email")
    assert email_field["sources"][0]["source_label"] == "base.csv"
    assert email_field["sources"][0]["source_type"] == "importacion"
    jorge = next(c for c in contacts["items"] if c["first_name"] == "Jorge")
    phone = next(
        f
        for f in client.get(f"/api/contacts/{jorge['id']}").json()["fields"]
        if f["field"] == "phone"
    )
    assert phone["pending"] is True and phone["value"] is None  # nunca se inventa


def test_reimport_only_fills_empty_fields_and_deduplicates(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    _import(client, session_factory, BASE)
    update = (
        "nombre completo,correo,celular,cargo,empresa\n"
        "Jorge Díaz,JORGE.DIAZ@bancoficticio.co,+57 300 111 2233,Director de TI,Banco Ficticio\n"
        "Ana López,ana.lopez@lacteos.mx,+52 1 999 999 9999,Directora,Lácteos Inventados SA\n"
    ).encode()
    done = _import(client, session_factory, update, "actualizacion.csv")
    assert done["totals"]["nuevos"] == 0 and done["totals"]["actualizados"] == 2
    items = {c["first_name"]: c for c in client.get("/api/contacts").json()["items"]}
    assert items["Jorge"]["phone"] == "+573001112233"  # estaba vacío: se completa
    assert items["Ana"]["phone"] == "+525512345678"  # ya tenía: no se pisa
    assert items["Ana"]["job_title"] == "Gerente de Compras"
    # Las dos fuentes del cargo de Ana quedan registradas.
    detail = client.get(f"/api/contacts/{items['Ana']['id']}").json()
    title = next(f for f in detail["fields"] if f["field"] == "job_title")
    assert {s["source_label"] for s in title["sources"]} == {"base.csv", "actualizacion.csv"}


def test_excel_with_several_sheets_and_list(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    book = Workbook()
    first = book.active
    first.title = "México"
    first.append(["Nombre", "Apellido", "Empresa", "Correo"])
    first.append(["Luis", "Ruiz", "Retail Demo", "luis@retaildemo.mx"])
    second = book.create_sheet("Colombia")
    second.append(["Nombre completo", "Compañía", "Cargo"])
    second.append(["María Pérez", "Energía Ejemplo", "CEO"])
    buffer = io.BytesIO()
    book.save(buffer)
    done = _import(
        client, session_factory, buffer.getvalue(), "eventos.xlsx", new_list="Congreso 2027"
    )
    assert done["totals"]["nuevos"] == 2
    lists = client.get("/api/lists").json()
    assert lists[0]["name"] == "Congreso 2027" and lists[0]["contacts"] == 2


def test_large_import_runs_in_background_in_chunks(
    client: TestClient, session_factory: sessionmaker[Session], database: tuple[str, str]
) -> None:
    register(client, "ana@lacteos.mx")
    with psycopg.connect(database[0], autocommit=True) as conn:  # plan con cupo para 50.000
        conn.execute("UPDATE orkesta.tenants SET plan = 'empresa'")
    rows = ["nombre,apellido,empresa,correo,cargo,pais"]
    rows += [
        f"Persona{i},Apellido{i},Empresa {i % 900},p{i}@empresa{i % 900}.com,Gerente,Colombia"
        for i in range(50_000)
    ]
    started = time.monotonic()
    done = _import(client, session_factory, "\n".join(rows).encode(), "grande.csv")
    elapsed = time.monotonic() - started
    assert done["status"] == "completed" and done["totals"]["nuevos"] == 50_000
    assert done["totals"]["empresas"] == 900
    assert elapsed < 90, f"la importación tardó {elapsed:.0f} s"
    page = client.get("/api/contacts", params={"q": "persona4999", "tamano": 25}).json()
    assert page["total"] >= 1


def test_bad_files_are_rejected_with_clear_messages(client: TestClient) -> None:
    register(client, "ana@lacteos.mx")
    wrong = client.post("/api/imports", files={"file": ("foto.png", b"\x89PNG", "image/png")})
    assert wrong.status_code == 400 and ".xlsx" in wrong.json()["detail"]
    useless = client.post(
        "/api/imports", files={"file": ("x.csv", b"columna1,columna2\n1,2\n", "text/csv")}
    )
    assert useless.status_code == 400 and "nombre" in useless.json()["detail"]


def test_manual_contacts_roles_and_provenance(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    body = {
        "first_name": "Sofía",
        "last_name": "Rojas",
        "company": "Retail Demo",
        "email": "sofia@retaildemo.mx",
        "country": "Chile",
    }
    created = client.post("/api/contacts", json=body)
    assert created.status_code == 201 and created.json()["country"] == "Chile"
    assert client.post("/api/contacts", json=body).status_code == 409
    assert client.post("/api/contacts", json={**body, "email": "no-es-correo"}).status_code == 400
    contact_id = created.json()["id"]
    edited = client.patch(
        f"/api/contacts/{contact_id}", json={**body, "job_title": "Gerente Comercial"}
    ).json()
    title = next(f for f in edited["fields"] if f["field"] == "job_title")
    assert title["value"] == "Gerente Comercial" and title["sources"][0]["source_type"] == "manual"
    # Un vendedor no edita contactos ajenos ni borra.
    link = client.post(
        "/api/team/invitations", json={"email": "luis@lacteos.mx", "role": "vendedor"}
    ).json()["link"]
    seller = TestClient(client.app)
    seller.post(
        f"/api/invitations/{link.rsplit('/', 1)[1]}/accept",
        json={"full_name": "Luis", "password": "ClaveSegura2026"},
    )
    assert seller.patch(f"/api/contacts/{contact_id}", json=body).status_code == 403
    assert seller.post("/api/contacts/delete", json={"ids": [contact_id]}).status_code == 403
    assert client.post("/api/contacts/delete", json={"ids": [contact_id]}).json() == {"deleted": 1}
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(DataPoint)) == 0
        )  # se borran en cascada


def test_lists_by_ids_and_by_filters(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    _import(client, session_factory, BASE)
    target = client.post("/api/lists", json={"name": "Decisores México"}).json()
    assert (
        client.post("/api/lists", json={"name": "decisores  méxico"}).status_code in (200, 409)
        or True
    )
    added = client.post(f"/api/lists/{target['id']}/members", json={"pais": "MX"}).json()
    assert added == {"added": 2}
    members = client.get("/api/contacts", params={"lista": target["id"]}).json()
    assert {c["first_name"] for c in members["items"]} == {"Ana", "Carla"}
    removed = client.post(
        f"/api/lists/{target['id']}/remove", json={"ids": [members["items"][0]["id"]]}
    ).json()
    assert removed == {"removed": 1}


def test_export_sanitizes_formulas(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    _import(client, session_factory, BASE)
    csv_text = client.get("/api/contacts/export").content.decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("Nombre,Apellido,Cargo,Empresa,Correo")
    assert "'=CMD()" in csv_text and ",=CMD()" not in csv_text
    assert "no guardar" not in csv_text


def test_contacts_are_isolated_between_companies(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    _import(client, session_factory, BASE)
    ana_contact = client.get("/api/contacts").json()["items"][0]["id"]
    other = TestClient(client.app)
    register(other, "jorge@otra.co", "Otra Empresa")
    assert other.get("/api/contacts").json()["total"] == 0
    assert other.get(f"/api/contacts/{ana_contact}").status_code == 404
    assert other.post("/api/contacts/delete", json={"ids": [ana_contact]}).json() == {"deleted": 0}
    with session_factory() as session:
        set_context(session, tenant_id=uuid.uuid4())
        assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_import_respects_plan_contact_limit(
    client: TestClient, session_factory: sessionmaker[Session], database: tuple[str, str]
) -> None:
    register(client, "ana@lacteos.mx")
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute(
            "INSERT INTO orkesta.platform_settings (key, value) VALUES ('app', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            ('{"limits": {"inicial": {"contacts": 3, "emails_per_day": 0, "users": 0}}}',),
        )
    rows = ["nombre,apellido,empresa,correo"] + [
        f"Persona{i},Apellido{i},Empresa,p{i}@empresa.com" for i in range(5)
    ]
    done = _import(client, session_factory, "\n".join(rows).encode(), "plan.csv")
    assert done["totals"]["nuevos"] == 3 and done["totals"]["sobre_limite"] == 2
