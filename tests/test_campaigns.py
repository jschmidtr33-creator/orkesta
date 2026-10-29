"""Campañas de correo: mensajes, respuestas/rebotes, motor de envío y bajas (sin red)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from email import message_from_string
from email.message import EmailMessage

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns import jobs as campaign_jobs
from orkesta.campaigns.service import (
    CampaignError,
    _within_window,
    apply_unsubscribe,
    campaign_stats,
    create_campaign,
    create_smtp_account,
    run_tick,
    start_campaign,
    unsubscribe_token,
    validate_steps,
)
from orkesta.campaigns.transports import Events, SentRef, Watched
from orkesta.catalog.lists import add_to_list, create_list
from orkesta.catalog.prospects import records_from_file
from orkesta.catalog.service import refresh_domain_patterns, upsert_prospects
from orkesta.db.models import (
    Campaign,
    CampaignMessage,
    CampaignRecipient,
    EnrichmentJob,
    Organization,
    Prospect,
    Suppression,
)
from orkesta.engine.mail.message import build_mail, render_text, to_mime
from orkesta.engine.mail.smtp import MailTransportError, parse_inbox
from orkesta.secrets_box import decrypt

# --- Mensajes --------------------------------------------------------------------------------


def test_render_and_escape_variables() -> None:
    assert render_text(
        "Hola {{ nombre }}, ¿{{empresa}}? {{otra}}", {"nombre": "Ana", "empresa": "Lácteos"}
    ) == ("Hola Ana, ¿Lácteos? ")
    mail = build_mail(
        to="ana@lacteos.com.mx",
        subject_template="Propuesta para {{empresa}}",
        body_template="Hola {{nombre}},\n\nTe escribo por {{empresa}}.",
        variables={"nombre": "<script>Ana</script>", "empresa": "Lácteos"},
        signature_text="Jenn\nAmerica Digital",
        signature_html=None,
        sender_email="jenn@americadigital.com",
        unsubscribe_url="https://orkesta.example/baja/x",
    )
    assert mail.subject == "Propuesta para Lácteos"
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html
    assert "Jenn\nAmerica Digital" in mail.text and "baja/x" in mail.text
    mime = to_mime(mail, "jenn@americadigital.com", "Jenn")
    assert mime["List-Unsubscribe"] == "<https://orkesta.example/baja/x>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mime["Message-ID"].endswith("@americadigital.com>")


def test_followup_goes_in_thread() -> None:
    mail = build_mail(
        to="a@b.com",
        subject_template="Propuesta",
        body_template="Seguimiento",
        variables={},
        signature_text=None,
        signature_html=None,
        sender_email="j@x.com",
        in_reply_to="<primero@x.com>",
    )
    mime = to_mime(mail, "j@x.com", None)
    assert mail.subject == "Re: Propuesta"
    assert mime["In-Reply-To"] == mime["References"] == "<primero@x.com>"


def test_validate_steps() -> None:
    steps = validate_steps(
        [
            {"subject": "Hola {{nombre}}", "body": "Texto"},
            {"subject": "", "body": "Seguimiento", "delay_days": 4},
            {"subject": "", "body": ""},
        ]
    )
    assert [s["delay_days"] for s in steps] == [0, 4]
    with pytest.raises(CampaignError, match="asunto"):
        validate_steps([{"subject": "", "body": "x"}])
    with pytest.raises(CampaignError, match="telefono"):
        validate_steps([{"subject": "Hola", "body": "Llama al {{telefono}}"}])


def test_parse_inbox_replies_and_bounces() -> None:
    reply = message_from_string(
        "From: Ana <ana@lacteos.com.mx>\nIn-Reply-To: <m1@x.com>\nSubject: Re: Hola\n\nGracias"
    )
    bounce = message_from_string(
        "From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\nSubject: Undelivered\n\n"
        "Your message to luis@noexiste.com could not be delivered."
    )
    other = message_from_string("From: x@y.com\nSubject: Otro\n\nNada")
    events = parse_inbox(
        [reply, bounce, other], {"<m1@x.com>", "<m2@x.com>"}, {"luis@noexiste.com"}
    )
    assert events.replied == {"<m1@x.com>"} and events.bounced == {"luis@noexiste.com"}


def test_window() -> None:
    campaign = Campaign(
        timezone="America/Bogota", window_start_hour=8, window_end_hour=18, weekdays=[0, 1, 2, 3, 4]
    )
    tuesday_10 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)  # 10:00 en Bogotá
    assert _within_window(campaign, tuesday_10)[0] is True
    saturday = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
    open_now, next_start = _within_window(campaign, saturday)
    assert open_now is False and next_start == datetime(2026, 11, 9, 13, 0, tzinfo=UTC)  # lun 8:00


# --- Motor de envío --------------------------------------------------------------------------

pytest_db = pytest.mark.db
BASE = (
    "email,first_name,last_name,title,company,country\n"
    "ana.lopez@lacteos.com.mx,Ana,López,Directora de Compras,Lácteos Inventados,Mexico\n"
    "jorge.diaz@lacteos.com.mx,Jorge,Díaz,CIO,Lácteos Inventados,Mexico\n"
    "luis.ruiz@lacteos.com.mx,Luis,Ruiz,CFO,Lácteos Inventados,Mexico\n"
).encode()


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.replied: set[str] = set()  # correos que "responden"
        self.bounced: set[str] = set()
        self.fail = False

    def send(self, message: EmailMessage, thread_id: str | None) -> SentRef:
        if self.fail:
            raise MailTransportError("servidor caído")
        self.sent.append(message)
        return SentRef(f"id{len(self.sent)}", thread_id or f"hilo-{message['To']}")

    def events(self, watched: list[Watched]) -> Events:
        return Events(
            {w.recipient_key for w in watched if w.email in self.replied},
            {w.recipient_key for w in watched if w.email in self.bounced},
        )


@pytest.fixture()
def setup(session_factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        upsert_prospects(session, org.id, records_from_file(BASE, "b.csv"), source="base")
        refresh_domain_patterns(session, org.id, ["lacteos.com.mx"])
        target = create_list(session, organization_id=org.id, user_id=None, name="Lácteos")
        ids = list(session.scalars(select(Prospect.id).where(Prospect.organization_id == org.id)))
        add_to_list(session, organization_id=org.id, list_id=target.id, prospect_ids=ids)
        account = create_smtp_account(
            session,
            organization_id=org.id,
            email="Jenn@AmericaDigital.com",
            from_name="Jenn",
            smtp_host="smtp.ejemplo.com",
            smtp_port=587,
            smtp_security="starttls",
            username="jenn@americadigital.com",
            password="contraseña-app",
            imap_host="imap.ejemplo.com",
            imap_port=993,
            daily_limit=2,
            verify=False,
        )
        campaign = create_campaign(
            session,
            organization_id=org.id,
            user_id=None,
            name="Congreso",
            list_id=target.id,
            account_id=account.id,
            weekdays=[0, 1, 2, 3, 4, 5, 6],
            window_start_hour=0,
            window_end_hour=24,
            steps=[
                {
                    "subject": "Hola {{nombre}}, invitación para {{empresa}}",
                    "body": "Hola {{nombre}}",
                },
                {"subject": "", "body": "Seguimiento para {{nombre}}", "delay_days": 3},
            ],
        )
        assert decrypt(account.secret or b"") == "contraseña-app"
        assert account.email == "jenn@americadigital.com"
        return org.id, campaign.id


def _tick(factory: sessionmaker[Session], campaign_id: uuid.UUID, transport: FakeTransport) -> str:
    with factory.begin() as session:
        return run_tick(session, campaign_id, lambda _account: transport)


def _age_messages(factory: sessionmaker[Session], days: float) -> None:
    """Simula el paso del tiempo moviendo envíos y vencimientos al pasado."""
    with factory.begin() as session:
        delta = timedelta(days=days)
        session.execute(update(CampaignMessage).values(sent_at=CampaignMessage.sent_at - delta))
        session.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.next_send_at.is_not(None))
            .values(next_send_at=CampaignRecipient.next_send_at - delta)
        )


@pytest_db
def test_campaign_sends_followups_stops_on_reply_and_respects_limits(
    session_factory: sessionmaker[Session], setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_id, campaign_id = setup
    with session_factory.begin() as session:
        session.add(
            Suppression(organization_id=org_id, email="luis.ruiz@lacteos.com.mx", reason="manual")
        )
    with session_factory.begin() as session:
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        summary = start_campaign(session, campaign)
    assert summary == {"destinatarios": 2, "excluidos": 1, "por_historial": 0, "sin_correo": 0}

    transport = FakeTransport()
    assert _tick(session_factory, campaign_id, transport) == "enviado"
    assert _tick(session_factory, campaign_id, transport) == "enviado"
    # Límite diario de la cuenta (2) alcanzado.
    assert _tick(session_factory, campaign_id, transport) == "límite de la cuenta"
    first = transport.sent[0]
    assert first["Subject"].startswith("Hola Ana") or first["Subject"].startswith("Hola Jorge")
    assert "List-Unsubscribe" not in first  # sin PUBLIC_BASE_URL (pruebas) no hay enlace

    # Ana responde; pasan 3 días: solo Jorge recibe el seguimiento, en el mismo hilo.
    transport.replied.add("ana.lopez@lacteos.com.mx")
    _age_messages(session_factory, 3.1)
    assert _tick(session_factory, campaign_id, transport) == "enviado"
    followup = transport.sent[2]
    assert followup["To"] == "jorge.diaz@lacteos.com.mx"
    assert followup["Subject"].startswith("Re: ")
    assert (
        followup["In-Reply-To"]
        == transport.sent[[m["To"] for m in transport.sent].index("jorge.diaz@lacteos.com.mx")][
            "Message-ID"
        ]
    )
    _age_messages(session_factory, 1.1)
    assert _tick(session_factory, campaign_id, transport) == "terminada"
    with session_factory() as session:
        stats = campaign_stats(session, campaign_id)
        assert stats["replied"] == 1 and stats["completed"] == 1 and stats["enviados"] == 3


@pytest_db
def test_bounce_suppresses_and_send_errors_retry(
    session_factory: sessionmaker[Session], setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, campaign_id = setup
    with session_factory.begin() as session:
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        start_campaign(session, campaign)
    transport = FakeTransport()
    transport.fail = True
    assert _tick(session_factory, campaign_id, transport) == "error de envío"
    with session_factory() as session:
        failed = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.last_error.is_not(None))
        )
        assert failed is not None and "servidor caído" in (failed.last_error or "")
    transport.fail = False
    assert _tick(session_factory, campaign_id, transport) == "enviado"
    bounced_to = transport.sent[0]["To"]
    transport.bounced.add(bounced_to)
    _tick(session_factory, campaign_id, transport)
    with session_factory() as session:
        recipient = session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.email == bounced_to)
        )
        assert recipient is not None and recipient.status == "bounced"
        reasons = dict(session.execute(select(Suppression.email, Suppression.reason)).all())
        assert reasons[bounced_to] == "bounced"


@pytest_db
def test_unsubscribe_token_stops_sending(
    session_factory: sessionmaker[Session], setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_id, campaign_id = setup
    with session_factory.begin() as session:
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        start_campaign(session, campaign)
        assert apply_unsubscribe(
            session, unsubscribe_token(org_id, "ANA.LOPEZ@lacteos.com.mx")
        ) == ("ana.lopez@lacteos.com.mx")
        with pytest.raises(CampaignError):
            apply_unsubscribe(session, "token-falso")
    transport = FakeTransport()
    while _tick(session_factory, campaign_id, transport) == "enviado":
        pass
    assert all(m["To"] != "ana.lopez@lacteos.com.mx" for m in transport.sent)


@pytest_db
def test_worker_runs_ticks(
    session_factory: sessionmaker[Session], setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from orkesta.worker.__main__ import FAILURE_HOOKS, HANDLERS
    from orkesta.worker.queue import RetryPolicy
    from orkesta.worker.runner import Worker

    _, campaign_id = setup
    with session_factory.begin() as session:
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        start_campaign(session, campaign)
    transport = FakeTransport()
    previous = campaign_jobs.transport_factory
    campaign_jobs.transport_factory = lambda _account: transport
    try:
        worker = Worker(
            session_factory,
            HANDLERS,
            retry_policy=RetryPolicy(1, 1),
            poll_interval_seconds=0.01,
            lock_timeout_seconds=600,
            failure_hooks=FAILURE_HOOKS,
        )
        assert worker.run_once()  # primer tick: envía y se reprograma con pausa
    finally:
        campaign_jobs.transport_factory = previous
    assert len(transport.sent) == 1
    with session_factory() as session:
        pending = session.scalar(
            select(EnrichmentJob).where(
                EnrichmentJob.kind == "campaign_tick", EnrichmentJob.status == "pending"
            )
        )
        assert pending is not None and pending.run_after > datetime.now(UTC) + timedelta(seconds=50)


# --- Historial de contacto ---------------------------------------------------------------------


def test_history_from_previous_mailing_sheets() -> None:
    from orkesta.campaigns.history import history_from_sheets
    from orkesta.catalog.prospects import Sheet

    sheet = Sheet(
        "CHILE",
        [
            "NOMBRE",
            "EMAIL",
            "CARGO",
            "EMPRESA",
            "SENT",
            "OPENED",
            "REPLIED",
            "FECHAENVIO",
            "ETAPAETAPA",
        ],
        [
            [
                "Ana López",
                "Ana.Lopez@x.cl",
                "CFO",
                "X",
                "X",
                "X",
                "",
                "2026-07-22",
                "Seguimiento enviado",
            ],
            ["Luis Ruiz", "luis@y.cl", "CEO", "Y", "X", "", "", "2026-07-21", "Respondió"],
            ["Sin envío", "nadie@z.cl", "CEO", "Z", "", "", "", "", "Contactado"],
            ["Mal correo", "no-es-correo", "CEO", "Z", "X", "", "", "2026-07-21", ""],
        ],
    )
    v3 = Sheet(
        "BASE44",
        ["Nombre", "Email", "Sent", "Replied", "Sent_At"],
        [["Ana", "ana.lopez@x.cl", "X", "X", "22/07/2026"]],
    )
    entries = {e.email: e for e in history_from_sheets([sheet, v3])}
    assert set(entries) == {"ana.lopez@x.cl", "luis@y.cl"}
    ana = entries["ana.lopez@x.cl"]
    assert ana.opened and ana.replied  # respondió en la segunda planilla
    assert ana.contacted_at == datetime(2026, 7, 22, tzinfo=UTC)
    assert entries["luis@y.cl"].replied  # la etapa "Respondió" cuenta como respuesta


@pytest_db
def test_campaign_excludes_recent_and_replied_and_records_its_own_sends(
    session_factory: sessionmaker[Session], setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from orkesta.campaigns.history import HistoryEntry, upsert_history
    from orkesta.db.models import ContactHistory

    org_id, campaign_id = setup
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        upsert_history(
            session,
            org_id,
            [
                HistoryEntry(
                    "ana.lopez@lacteos.com.mx", now - timedelta(days=10), False, False, None
                ),
                HistoryEntry(
                    "jorge.diaz@lacteos.com.mx", now - timedelta(days=400), True, True, None
                ),
                HistoryEntry(
                    "luis.ruiz@lacteos.com.mx", now - timedelta(days=200), False, False, None
                ),
            ],
            source="planilla anterior",
        )
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        # Por defecto el historial no excluye a nadie: cada usuario lo decide por campaña.
        assert (campaign.exclude_recent_days, campaign.exclude_replied) == (0, False)
        campaign.exclude_recent_days, campaign.exclude_replied = 90, True
        summary = start_campaign(session, campaign)
    # Ana: contactada hace 10 días; Jorge: ya respondió. Solo Luis recibe la campaña.
    assert (summary["destinatarios"], summary["por_historial"]) == (1, 2)
    transport = FakeTransport()
    assert _tick(session_factory, campaign_id, transport) == "enviado"
    with session_factory() as session:
        luis = session.scalar(
            select(ContactHistory).where(ContactHistory.email == "luis.ruiz@lacteos.com.mx")
        )
        assert luis is not None and luis.last_contacted_at > now - timedelta(minutes=5)
        assert luis.source == "campaña ORKESTA"


@pytest_db
def test_upload_with_mailing_columns_loads_history(session_factory: sessionmaker[Session]) -> None:
    from orkesta.catalog.service import create_upload, process_upload
    from orkesta.db.models import ContactHistory

    content = (
        b"NOMBRE,EMAIL,CARGO,EMPRESA,SENT,OPENED,REPLIED,FECHAENVIO\n"
        b"Ana Lopez,ana@x.cl,CFO,Empresa X,X,X,,2026-07-22\n"
        b"Luis Ruiz,luis@y.cl,CEO,Empresa Y,,,,\n"
    )
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        upload = create_upload(
            session, organization_id=org.id, user_id=None, filename="campaña.csv", content=content
        )
        summary = process_upload(session, upload.id)
        assert summary["personas"] == 2 and summary["historial"] == 1
        assert (
            session.scalar(
                select(ContactHistory.email).where(ContactHistory.organization_id == org.id)
            )
            == "ana@x.cl"
        )
