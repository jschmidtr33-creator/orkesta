"""Interfaz de campañas: cuentas, campaña desde el formulario, inicio, bajas y exclusiones."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.api.main import create_app
from orkesta.auth import create_user
from orkesta.campaigns import service as campaign_service
from orkesta.campaigns.service import unsubscribe_token
from orkesta.catalog.lists import add_to_list, create_list
from orkesta.catalog.prospects import records_from_file
from orkesta.catalog.service import upsert_prospects
from orkesta.db.models import (
    Campaign,
    CampaignRecipient,
    MailAccount,
    Organization,
    Prospect,
    ProspectList,
    Suppression,
)

pytestmark = pytest.mark.db
BASE = (
    "email,first_name,last_name,title,company,country\n"
    "ana.lopez@lacteos.com.mx,Ana,López,Directora de Compras,Lácteos Inventados,Mexico\n"
    "jorge.diaz@lacteos.com.mx,Jorge,Díaz,CIO,Lácteos Inventados,Mexico\n"
).encode()


@pytest.fixture()
def org_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        create_user(
            session,
            email="jenn@ejemplo.com",
            password="clave-de-prueba-1",
            organization_id=org.id,
            role="admin",
        )
        upsert_prospects(session, org.id, records_from_file(BASE, "b.csv"), source="base")
        target = create_list(session, organization_id=org.id, user_id=None, name="Lácteos")
        ids = list(session.scalars(select(Prospect.id).where(Prospect.organization_id == org.id)))
        add_to_list(session, organization_id=org.id, list_id=target.id, prospect_ids=ids)
        return org.id


@pytest.fixture()
def client(
    engine: Engine, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    checked: list[str] = []
    monkeypatch.setattr(campaign_service, "check_smtp", lambda s: checked.append(s.host))
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
    assert checked in ([], ["smtp.office365.com"])


def _token(client: TestClient, path: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match
    return match.group(1)


def test_full_campaign_flow(
    client: TestClient, session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    # 1) Cuenta corporativa con preajuste de Microsoft 365 (verificación simulada).
    response = client.post(
        "/cuentas/smtp",
        data={
            "csrf_token": _token(client, "/cuentas"),
            "correo": "Jenn@AmericaDigital.com",
            "contrasena": "app-pass",
            "nombre": "Jenn",
            "preajuste": "microsoft365",
            "limite": "40",
        },
    )
    assert response.status_code == 303
    with session_factory() as session:
        account = session.scalar(select(MailAccount).where(MailAccount.organization_id == org_id))
        assert account is not None
        assert (account.smtp_host, account.smtp_port, account.imap_host) == (
            "smtp.office365.com",
            587,
            "outlook.office365.com",
        )
        assert account.secret and b"app-pass" not in account.secret  # cifrada
        list_id = session.scalar(
            select(ProspectList.id).where(ProspectList.organization_id == org_id)
        )
    assert "jenn@americadigital.com" in client.get("/cuentas").text

    # 2) Campaña desde el formulario, con variables desconocidas rechazadas.
    bad = client.post(
        "/campanas",
        data={
            "csrf_token": _token(client, "/campanas"),
            "nombre": "Mala",
            "lista": str(list_id),
            "cuenta": str(account.id),
            "asunto_1": "Hola",
            "cuerpo_1": "Tu {{telefono}}",
        },
    )
    assert bad.status_code == 303 and "telefono" in client.get("/campanas").text
    response = client.post(
        "/campanas",
        data={
            "csrf_token": _token(client, "/campanas"),
            "nombre": "Congreso 2027",
            "lista": str(list_id),
            "cuenta": str(account.id),
            "asunto_1": "{{nombre}}, invitación para {{empresa}}",
            "cuerpo_1": "Hola {{nombre}},\n\nTe invito al Congreso.",
            "cuerpo_2": "¿Lo viste?",
            "dias_2": "4",
            "firma_texto": "Jenn\nAmerica Digital",
            "dias": ["0", "1", "2", "3", "4"],
        },
    )
    assert response.status_code == 303
    detail_url = response.headers["location"]
    detail = client.get(detail_url).text
    assert "invitación para Lácteos Inventados" in detail  # vista previa con datos reales
    assert "Re: " in detail and "4 días después" in detail

    # 3) Iniciar: destinatarios congelados; pausar.
    client.post(f"{detail_url}/iniciar", data={"csrf_token": _token(client, detail_url)})
    with session_factory() as session:
        campaign = session.scalar(select(Campaign).where(Campaign.name == "Congreso 2027"))
        assert campaign is not None and campaign.status == "running"
        emails = set(
            session.scalars(
                select(CampaignRecipient.email).where(CampaignRecipient.campaign_id == campaign.id)
            )
        )
        assert emails == {"ana.lopez@lacteos.com.mx", "jorge.diaz@lacteos.com.mx"}
    client.post(f"{detail_url}/pausar", data={"csrf_token": _token(client, detail_url)})
    assert "pausada" in client.get(detail_url).text


def test_suppressions_and_public_unsubscribe(
    client: TestClient, engine: Engine, session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    client.post(
        "/exclusiones",
        data={
            "csrf_token": _token(client, "/exclusiones"),
            "correos": "A@x.com, b@y.com\nno-es-correo",
        },
    )
    page = client.get("/exclusiones").text
    assert "a@x.com" in page and "b@y.com" in page and "no-es-correo" not in page
    # Baja pública: sin sesión y sin CSRF (los proveedores hacen POST de un clic).
    token = unsubscribe_token(org_id, "jorge.diaz@lacteos.com.mx")
    with TestClient(
        create_app(engine_provider=lambda: engine), follow_redirects=False
    ) as anonymous:
        assert "Darme de baja" in anonymous.get(f"/baja/{token}").text
        assert anonymous.post(f"/baja/{token}").status_code == 200
        assert anonymous.post("/baja/falso").status_code == 400
        evil = anonymous.get('/baja/x"><img src=y>').text
        assert '"><img' not in evil
    with session_factory() as session:
        reasons = dict(session.execute(select(Suppression.email, Suppression.reason)).all())
        assert reasons["jorge.diaz@lacteos.com.mx"] == "unsubscribed"


def test_gmail_requires_configuration_and_valid_state(client: TestClient) -> None:
    response = client.get("/cuentas/google/conectar")
    assert response.status_code == 303 and response.headers["location"] == "/cuentas"
    assert "todavía no está configurado" in client.get("/cuentas").text
    response = client.get("/cuentas/google/retorno", params={"code": "x", "state": "y"})
    assert response.status_code == 303
    assert "No se completó la conexión" in client.get("/cuentas").text


def test_admin_configures_google_from_the_page(
    engine: Engine, session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    from orkesta.campaigns.google_config import google_credentials, public_base_url

    with TestClient(
        create_app(engine_provider=lambda: engine),
        base_url="https://orkesta.ejemplo.com",
        follow_redirects=False,
    ) as c:
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
        page = c.get("/cuentas").text
        # La dirección pública se recuerda sola y el URI de retorno queda listo para copiar.
        assert "https://orkesta.ejemplo.com/cuentas/google/retorno" in page
        assert "Configurar Google" in page
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
        assert csrf
        bad = c.post(
            "/cuentas/google/configurar",
            data={
                "csrf_token": csrf.group(1),
                "client_id": "abc",
                "client_secret": "secreto-largo-123",
            },
        )
        assert bad.status_code == 303 and "googleusercontent" in c.get("/cuentas").text
        ok = c.post(
            "/cuentas/google/configurar",
            data={
                "csrf_token": csrf.group(1),
                "client_id": "123-abc.apps.googleusercontent.com",
                "client_secret": "GOCSPX-secreto-de-prueba",
            },
        )
        assert ok.status_code == 303
        assert "Conectar con Google" in c.get("/cuentas").text
        connect = c.get("/cuentas/google/conectar")
        assert connect.status_code == 303
        location = connect.headers["location"]
        assert location.startswith("https://accounts.google.com/")
        assert "client_id=123-abc.apps.googleusercontent.com" in location
        assert (
            "redirect_uri=https%3A%2F%2Forkesta.ejemplo.com%2Fcuentas%2Fgoogle%2Fretorno"
            in location
        )
    with session_factory() as session:
        credentials = google_credentials(session, org_id)
        assert credentials is not None and credentials.client_secret == "GOCSPX-secreto-de-prueba"
        assert public_base_url(session) == "https://orkesta.ejemplo.com"
