"""Campañas de correo en la interfaz: cuentas de envío, campañas, bajas y exclusiones."""

from __future__ import annotations

import html
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from orkesta.audit import audit
from orkesta.campaigns.google_config import (
    google_client,
    google_credentials,
    public_base_url,
    redirect_uri,
    remember_public_base_url,
    save_google_credentials,
)
from orkesta.campaigns.service import (
    CampaignError,
    apply_unsubscribe,
    campaign_stats,
    create_campaign,
    create_smtp_account,
    get_account,
    get_campaign,
    pause_campaign,
    save_gmail_account,
    start_campaign,
    suppress,
)
from orkesta.config import get_settings
from orkesta.db.models import (
    Campaign,
    CampaignRecipient,
    MailAccount,
    Prospect,
    ProspectList,
    ProspectListMember,
    Suppression,
    User,
)
from orkesta.engine.mail import gmail
from orkesta.engine.mail.message import build_mail
from orkesta.engine.mail.smtp import MailTransportError
from orkesta.web.deps import DbSession, check_csrf, current_user, flash, render

router = APIRouter(include_in_schema=False)
CurrentUser = Annotated[User, Depends(current_user)]
CsrfField = Annotated[str | None, Form(alias="csrf_token")]

# Servidores de los proveedores más comunes: con el preajuste basta el correo y la contraseña.
SMTP_PRESETS: dict[str, tuple[str, str, int, str, str, int]] = {
    "microsoft365": (
        "Microsoft 365 / Outlook",
        "smtp.office365.com",
        587,
        "starttls",
        "outlook.office365.com",
        993,
    ),
    "google_workspace": (
        "Google Workspace (contraseña de aplicación)",
        "smtp.gmail.com",
        465,
        "ssl",
        "imap.gmail.com",
        993,
    ),
    "zoho": ("Zoho Mail", "smtp.zoho.com", 465, "ssl", "imap.zoho.com", 993),
}
STATUS_LABELS = {
    "draft": "borrador", "running": "enviando", "paused": "pausada", "completed": "terminada",
    "pending": "pendiente", "active": "en secuencia", "replied": "respondió", "bounced": "rebotó",
    "unsubscribed": "se dio de baja", "skipped": "omitido", "failed": "falló",
}  # fmt: skip
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


# --- Cuentas ---------------------------------------------------------------------------------


@router.get("/cuentas")
def accounts_page(request: Request, db: DbSession, user: CurrentUser) -> Response:
    # Sin PUBLIC_BASE_URL, se recuerda la dirección pública con la que se abrió ORKESTA.
    remember_public_base_url(db, user.organization_id, str(request.base_url))
    accounts = db.scalars(
        select(MailAccount)
        .where(MailAccount.organization_id == user.organization_id)
        .order_by(MailAccount.email)
    ).all()
    settings = get_settings()
    credentials = google_credentials(db, user.organization_id)
    return render(
        request,
        "accounts.html",
        {
            "user": user,
            "accounts": accounts,
            "presets": SMTP_PRESETS,
            "google": credentials is not None and bool(redirect_uri(db)),
            "google_env": credentials is not None and credentials.from_environment,
            "redirect_uri": redirect_uri(db),
            "is_admin": user.role == "admin",
            "default_limit": settings.mail_default_daily_limit,
        },
    )


@router.post("/cuentas/google/configurar")
def configure_google(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    if user.role != "admin":
        raise HTTPException(status_code=403)
    try:
        save_google_credentials(db, user.organization_id, client_id, client_secret)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/cuentas#configurar-google", status_code=303)
    audit(
        db, organization_id=user.organization_id, actor_user_id=user.id,
        action="google_oauth.configured", entity_type="app_setting",
    )  # fmt: skip
    flash(request, "Google quedó configurado. Ya puedes conectar Gmail con un clic.", "success")
    return RedirectResponse("/cuentas", status_code=303)


@router.post("/cuentas/smtp")
def add_smtp_account(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    correo: Annotated[str, Form()],
    contrasena: Annotated[str, Form()],
    nombre: Annotated[str, Form()] = "",
    preajuste: Annotated[str, Form()] = "",
    smtp_host: Annotated[str, Form()] = "",
    smtp_port: Annotated[int, Form()] = 587,
    seguridad: Annotated[str, Form()] = "starttls",
    imap_host: Annotated[str, Form()] = "",
    imap_port: Annotated[int, Form()] = 993,
    usuario: Annotated[str, Form()] = "",
    limite: Annotated[int, Form()] = 50,
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    if preajuste in SMTP_PRESETS:
        _, smtp_host, smtp_port, seguridad, imap_host, imap_port = SMTP_PRESETS[preajuste]
    try:
        account = create_smtp_account(
            db, organization_id=user.organization_id, email=correo, from_name=nombre,
            smtp_host=smtp_host, smtp_port=smtp_port, smtp_security=seguridad,
            username=usuario or correo, password=contrasena, imap_host=imap_host,
            imap_port=imap_port, daily_limit=limite,
        )  # fmt: skip
    except (CampaignError, MailTransportError) as exc:
        db.rollback()
        message = f"No se pudo conectar la cuenta: {exc}"
        if preajuste == "google_workspace":
            message += (
                " · Google pide una contraseña de aplicación (16 letras), no tu contraseña "
                "normal, y la verificación en dos pasos activa."
            )
        flash(request, message, "error")
        return RedirectResponse("/cuentas", status_code=303)
    audit(
        db, organization_id=user.organization_id, actor_user_id=user.id,
        action="mail_account.connected", entity_type="mail_account", entity_id=account.id,
        provider="smtp",
    )  # fmt: skip
    flash(request, f"Cuenta {account.email} conectada y verificada.", "success")
    return RedirectResponse("/cuentas", status_code=303)


@router.get("/cuentas/google/conectar")
def google_connect(request: Request, db: DbSession, user: CurrentUser) -> Response:
    try:
        client = google_client(db, user.organization_id)
    except MailTransportError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/cuentas", status_code=303)
    state = secrets.token_urlsafe(24)
    request.session["google_oauth_state"] = state
    return RedirectResponse(gmail.authorization_url(client, state), status_code=303)


@router.get("/cuentas/google/retorno")
def google_callback(
    request: Request, db: DbSession, user: CurrentUser, code: str = "", state: str = "",
    error: str = "",
) -> Response:  # fmt: skip
    expected = request.session.pop("google_oauth_state", None)
    if error or not code or not expected or not secrets.compare_digest(state, expected):
        flash(request, "No se completó la conexión con Google.", "error")
        return RedirectResponse("/cuentas", status_code=303)
    try:
        refresh_token, email = gmail.exchange_code(google_client(db, user.organization_id), code)
    except MailTransportError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/cuentas", status_code=303)
    account = save_gmail_account(
        db, organization_id=user.organization_id, email=email, refresh_token=refresh_token
    )
    audit(
        db, organization_id=user.organization_id, actor_user_id=user.id,
        action="mail_account.connected", entity_type="mail_account", entity_id=account.id,
        provider="gmail",
    )  # fmt: skip
    flash(request, f"Gmail {email} conectado.", "success")
    return RedirectResponse("/cuentas", status_code=303)


@router.post("/cuentas/{account_id}/limite")
def update_limit(
    request: Request, account_id: uuid.UUID, db: DbSession, user: CurrentUser,
    limite: Annotated[int, Form()], csrf: CsrfField = None,
) -> Response:  # fmt: skip
    check_csrf(request, csrf)
    try:
        account = get_account(db, user.organization_id, account_id)
    except CampaignError as exc:
        raise HTTPException(status_code=404) from exc
    account.daily_limit = max(1, min(limite, 2000))
    flash(request, f"Límite diario de {account.email}: {account.daily_limit} correos.", "success")
    return RedirectResponse("/cuentas", status_code=303)


@router.post("/cuentas/{account_id}/estado")
def toggle_account(
    request: Request, account_id: uuid.UUID, db: DbSession, user: CurrentUser,
    csrf: CsrfField = None,
) -> Response:  # fmt: skip
    check_csrf(request, csrf)
    try:
        account = get_account(db, user.organization_id, account_id)
    except CampaignError as exc:
        raise HTTPException(status_code=404) from exc
    account.active = not account.active
    flash(request, f"{account.email} {'activada' if account.active else 'desactivada'}.", "success")
    return RedirectResponse("/cuentas", status_code=303)


# --- Campañas --------------------------------------------------------------------------------


def _org_lists(db: DbSession, user: User) -> list[ProspectList]:
    return list(
        db.scalars(
            select(ProspectList)
            .where(ProspectList.organization_id == user.organization_id)
            .order_by(ProspectList.name)
        )
    )


def _org_accounts(db: DbSession, user: User) -> list[MailAccount]:
    return list(
        db.scalars(
            select(MailAccount).where(
                MailAccount.organization_id == user.organization_id, MailAccount.active.is_(True)
            )
        )
    )


@router.get("/campanas")
def campaigns_page(request: Request, db: DbSession, user: CurrentUser) -> Response:
    campaigns = db.scalars(
        select(Campaign)
        .where(Campaign.organization_id == user.organization_id)
        .order_by(Campaign.created_at.desc())
    ).all()
    return render(
        request,
        "campaigns.html",
        {
            "user": user,
            "campaigns": campaigns,
            "stats": {c.id: campaign_stats(db, c.id) for c in campaigns},
            "labels": STATUS_LABELS,
            "lists": _org_lists(db, user),
            "accounts": _org_accounts(db, user),
            "weekdays": WEEKDAYS,
        },
    )


@router.post("/campanas")
def new_campaign(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    nombre: Annotated[str, Form()],
    lista: Annotated[uuid.UUID, Form()],
    cuenta: Annotated[uuid.UUID, Form()],
    asunto_1: Annotated[str, Form()] = "",
    cuerpo_1: Annotated[str, Form()] = "",
    asunto_2: Annotated[str, Form()] = "",
    cuerpo_2: Annotated[str, Form()] = "",
    dias_2: Annotated[int, Form()] = 3,
    asunto_3: Annotated[str, Form()] = "",
    cuerpo_3: Annotated[str, Form()] = "",
    dias_3: Annotated[int, Form()] = 5,
    firma_texto: Annotated[str, Form()] = "",
    firma_html: Annotated[str, Form()] = "",
    limite: Annotated[int, Form()] = 50,
    desde: Annotated[int, Form()] = 8,
    hasta: Annotated[int, Form()] = 18,
    dias: Annotated[list[int] | None, Form()] = None,
    zona: Annotated[str, Form()] = "America/Bogota",
    excluir_dias: Annotated[int, Form()] = 0,
    excluir_respondieron: Annotated[str, Form()] = "",
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    try:
        campaign = create_campaign(
            db, organization_id=user.organization_id, user_id=user.id, name=nombre,
            list_id=lista, account_id=cuenta,
            steps=[
                {"subject": asunto_1, "body": cuerpo_1, "delay_days": 0},
                {"subject": asunto_2, "body": cuerpo_2, "delay_days": dias_2},
                {"subject": asunto_3, "body": cuerpo_3, "delay_days": dias_3},
            ],
            signature_text=firma_texto, signature_html=firma_html, daily_limit=limite,
            timezone=zona, window_start_hour=desde, window_end_hour=hasta,
            weekdays=dias or [0, 1, 2, 3, 4],
            exclude_recent_days=excluir_dias,
            exclude_replied=excluir_respondieron == "1",
        )  # fmt: skip
    except CampaignError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/campanas", status_code=303)
    flash(request, "Campaña creada en borrador. Revisa la vista previa y iníciala.", "success")
    return RedirectResponse(f"/campanas/{campaign.id}", status_code=303)


@router.get("/campanas/{campaign_id}")
def campaign_detail(
    request: Request, campaign_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    try:
        campaign = get_campaign(db, user.organization_id, campaign_id)
    except CampaignError as exc:
        raise HTTPException(status_code=404) from exc
    account = db.get(MailAccount, campaign.account_id)
    sample = db.execute(
        select(ProspectListMember, Prospect)
        .join(Prospect, Prospect.id == ProspectListMember.prospect_id)
        .where(
            ProspectListMember.list_id == campaign.list_id, ProspectListMember.email.is_not(None)
        )
        .limit(1)
    ).first()
    variables = (
        {"nombre": sample[1].first_name.split()[0].title(), "apellido": sample[1].last_name,
         "empresa": sample[1].company, "cargo": sample[1].title or ""}
        if sample else {"nombre": "Ana", "apellido": "López", "empresa": "Empresa", "cargo": "CEO"}
    )  # fmt: skip
    previews = [
        build_mail(
            to=(sample[0].email or "ana@empresa.com") if sample else "ana@empresa.com",
            subject_template=step["subject"] or campaign.steps[0]["subject"],
            body_template=step["body"], variables=variables,
            signature_text=campaign.signature_text, signature_html=campaign.signature_html,
            sender_email=account.email if account else "tu@empresa.com",
            in_reply_to="<previo>" if index else None,
        )
        for index, step in enumerate(campaign.steps)
    ]  # fmt: skip
    recipients = db.scalars(
        select(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.status, CampaignRecipient.email)
        .limit(200)
    ).all()
    return render(
        request,
        "campaign_detail.html",
        {
            "user": user,
            "campaign": campaign,
            "account": account,
            "previews": previews,
            "stats": campaign_stats(db, campaign.id),
            "recipients": recipients,
            "labels": STATUS_LABELS,
            "weekdays": WEEKDAYS,
            "public_url_missing": get_settings().is_deployed and not public_base_url(db),
        },
    )


@router.post("/campanas/{campaign_id}/iniciar")
def start(
    request: Request,
    campaign_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    try:
        campaign = get_campaign(db, user.organization_id, campaign_id)
        summary = start_campaign(db, campaign)
    except CampaignError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(f"/campanas/{campaign_id}", status_code=303)
    audit(
        db, organization_id=user.organization_id, actor_user_id=user.id,
        action="campaign.started", entity_type="campaign", entity_id=campaign.id,
        recipients=summary["destinatarios"],
    )  # fmt: skip
    message = f"Campaña en marcha: {summary['destinatarios']} destinatarios"
    if summary["excluidos"]:
        message += f", {summary['excluidos']} excluidos (bajas, rebotes o repetidos)"
    if summary["por_historial"]:
        message += f", {summary['por_historial']} omitidos por contacto reciente o respuesta previa"
    if summary["sin_correo"]:
        message += f", {summary['sin_correo']} sin correo"
    flash(request, message + ".", "success")
    return RedirectResponse(f"/campanas/{campaign.id}", status_code=303)


@router.post("/campanas/{campaign_id}/pausar")
def pause(
    request: Request,
    campaign_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    csrf: CsrfField = None,
) -> Response:
    check_csrf(request, csrf)
    try:
        campaign = get_campaign(db, user.organization_id, campaign_id)
    except CampaignError as exc:
        raise HTTPException(status_code=404) from exc
    pause_campaign(db, campaign)
    flash(request, "Campaña pausada. Puedes reanudarla cuando quieras.", "success")
    return RedirectResponse(f"/campanas/{campaign.id}", status_code=303)


# --- Exclusiones -----------------------------------------------------------------------------


@router.get("/exclusiones")
def suppressions_page(request: Request, db: DbSession, user: CurrentUser) -> Response:
    rows = db.scalars(
        select(Suppression)
        .where(Suppression.organization_id == user.organization_id)
        .order_by(Suppression.created_at.desc())
        .limit(500)
    ).all()
    return render(request, "suppressions.html", {"user": user, "rows": rows})


@router.post("/exclusiones")
def add_suppressions(
    request: Request, db: DbSession, user: CurrentUser, correos: Annotated[str, Form()] = "",
    csrf: CsrfField = None,
) -> Response:  # fmt: skip
    check_csrf(request, csrf)
    added = 0
    for raw in correos.replace(",", "\n").replace(";", "\n").splitlines():
        email = raw.strip().lower()
        if "@" in email and len(email) <= 320:
            suppress(db, user.organization_id, email, "manual")
            added += 1
    flash(request, f"{added} correos agregados a la lista de exclusión.", "success")
    return RedirectResponse("/exclusiones", status_code=303)


# --- Baja de un clic (pública: sin sesión ni CSRF, la firma del enlace protege) ---------------

_UNSUB_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Baja</title>
<link rel="stylesheet" href="/static/app.css"></head>
<body><main class="page"><section class="card narrow">{body}</section></main></body></html>"""


@router.get("/baja/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str) -> HTMLResponse:
    body = (
        "<h1>¿Dejar de recibir estos correos?</h1>"
        f'<form method="post" action="/baja/{html.escape(token, quote=True)}">'
        '<button type="submit" class="button">'
        "Darme de baja</button></form>"
    )
    return HTMLResponse(_UNSUB_PAGE.format(body=body))


@router.post("/baja/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: DbSession) -> HTMLResponse:
    try:
        apply_unsubscribe(db, token)
    except CampaignError:
        return HTMLResponse(_UNSUB_PAGE.format(body="<h1>Enlace no válido.</h1>"), status_code=400)
    return HTMLResponse(
        _UNSUB_PAGE.format(body="<h1>Listo.</h1><p>No volverás a recibir estos correos.</p>")
    )
