# ruff: noqa: E501  (textos para el vendedor: líneas largas a propósito)
"""«¿Cuántos correos envié esta semana?», «¿cómo voy este mes?»: Concertino responde con los
números reales de la persona en ORKESTA (correos, respuestas, reuniones, propuestas y
oportunidades), los compara con el periodo anterior y dice qué mejorar primero."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.engine.normalize.text import fold

_WANTS = re.compile(
    r"\b(cuant[oa]s? (correos|mails|emails|respuestas|reuniones|propuestas|oportunidades|ventas|negocios|cierres|aperturas|contactos|personas|tareas)|"
    r"como (voy|vamos|me fue|me va|nos fue|nos va|va mi semana|va mi mes|van mis numeros)|"
    r"mis (numeros|metricas|resultados|indicadores|kpis|estadisticas)|"
    r"(resumen|balance|reporte) de (mi|mis|la|esta|este) (semana|mes|dia|actividad|resultados)|"
    r"(mi|la) tasa de (respuesta|apertura|cierre)|"
    r"(cuanto|cuantos|cuantas|que) (vendi|vendimos|facture|facturamos|cerre|cerramos|gane|ganamos)|"
    r"(mis|las|nuestras) ventas (de|del) (hoy|este|esta|el|la)|como (van|estan) (mis|las) ventas|"
    r"(cuanto|que tanto) me falta|(mi|la) meta)\b"
)


def wants(question: str) -> bool:
    return bool(_WANTS.search(fold(question)))


def _period(question: str, tz: ZoneInfo) -> tuple[str, datetime, datetime, datetime]:
    """(nombre, desde, hasta, desde del periodo anterior) en la hora local de la persona."""
    folded = fold(question)
    now = datetime.now(tz)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\bhoy\b", folded):
        return "hoy", today, now, today - timedelta(days=1)
    if re.search(r"\bayer\b", folded):
        return "ayer", today - timedelta(days=1), today, today - timedelta(days=2)
    if re.search(r"\bsemana pasada\b", folded):
        start = today - timedelta(days=today.weekday() + 7)
        return "la semana pasada", start, start + timedelta(days=7), start - timedelta(days=7)
    if re.search(r"\bmes pasado\b", folded):
        first = today.replace(day=1)
        prev = (first - timedelta(days=1)).replace(day=1)
        before = (prev - timedelta(days=1)).replace(day=1)
        return "el mes pasado", prev, first, before
    if re.search(r"\b(mes|30 dias)\b", folded):
        first = today.replace(day=1)
        return "este mes", first, now, (first - timedelta(days=1)).replace(day=1)
    start = today - timedelta(days=today.weekday())
    return "esta semana", start, now, start - timedelta(days=7)


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f} %".replace(".", ",") if whole else "—"


def _trend(now: int, before: int) -> str:
    if before == 0:
        return ""
    diff = (now - before) / before * 100
    if abs(diff) < 5:
        return " (igual que el periodo anterior)"
    return f" ({'+' if diff > 0 else '−'}{abs(diff):.0f} % vs. el periodo anterior)"


def build(session: Session, tenant_id: uuid.UUID, user: Any, question: str) -> dict[str, Any]:
    from orkesta.campaigns.models import Campaign, Mailbox, Message, Recipient
    from orkesta.crm.models import Activity, Deal
    from orkesta.proposals.models import Proposal

    try:
        tz = ZoneInfo(getattr(user, "timezone", None) or "America/Bogota")
    except Exception:
        tz = ZoneInfo("America/Bogota")
    label, start, end, prev_start = _period(question, tz)
    s, e, ps = start.astimezone(UTC), end.astimezone(UTC), prev_start.astimezone(UTC)
    prev_end = s

    def sent(a: datetime, b: datetime) -> tuple[int, int]:
        row = session.execute(
            select(func.count(), func.count(func.distinct(Message.recipient_id)))
            .select_from(Message).join(Mailbox, Mailbox.id == Message.mailbox_id)
            .where(Message.tenant_id == tenant_id, Mailbox.user_id == user.id,
                   Message.sent_at >= a, Message.sent_at < b)
        ).one()  # fmt: skip
        return int(row[0]), int(row[1])

    def replies(a: datetime, b: datetime) -> dict[str, int]:
        rows = session.execute(
            select(Recipient.reply_category, func.count()).join(Campaign, Campaign.id == Recipient.campaign_id)
            .where(Recipient.tenant_id == tenant_id, Campaign.created_by == user.id,
                   Recipient.replied_at >= a, Recipient.replied_at < b)
            .group_by(Recipient.reply_category)
        ).all()  # fmt: skip
        return {str(r[0] or "otra"): int(r[1]) for r in rows}

    emails, people = sent(s, e)
    emails_before, _ = sent(ps, prev_end)
    rep = replies(s, e)
    total_replies = sum(rep.values())
    asked_meeting = rep.get("reunion", 0)
    positive = sum(rep.get(k, 0) for k in ("reunion", "interesado", "mas_info", "referido"))
    meetings = int(session.scalar(
        select(func.count()).select_from(Activity).where(
            Activity.tenant_id == tenant_id, Activity.user_id == user.id, Activity.type == "meeting",
            Activity.created_at >= s, Activity.created_at < e)
    ) or 0)  # fmt: skip
    prop = session.execute(
        select(
            func.count().filter(Proposal.sent_at >= s, Proposal.sent_at < e),
            func.count().filter(Proposal.first_viewed_at >= s, Proposal.first_viewed_at < e),
            func.count().filter(Proposal.status == "accepted", Proposal.decided_at >= s, Proposal.decided_at < e),
        ).where(Proposal.tenant_id == tenant_id, Proposal.created_by == user.id)
    ).one()  # fmt: skip
    p_sent, p_viewed, p_won = (int(x or 0) for x in prop)
    deals_new = int(session.scalar(
        select(func.count()).select_from(Deal).where(
            Deal.tenant_id == tenant_id, Deal.owner_user_id == user.id,
            Deal.created_at >= s, Deal.created_at < e)
    ) or 0)  # fmt: skip
    won = session.execute(
        select(func.count(), func.coalesce(func.sum(Deal.value), 0), func.min(Deal.currency)).where(
            Deal.tenant_id == tenant_id, Deal.owner_user_id == user.id, Deal.status == "won",
            Deal.closed_at >= s, Deal.closed_at < e)
    ).one()  # fmt: skip
    overdue = int(session.scalar(
        select(func.count()).select_from(Activity).where(
            Activity.tenant_id == tenant_id, Activity.user_id == user.id, Activity.type == "task",
            Activity.done_at.is_(None), Activity.due_at < datetime.now(UTC))
    ) or 0)  # fmt: skip

    prospecting = [
        f"Correos enviados: {emails:,}".replace(",", ".")
        + f" a {people:,} personas".replace(",", ".")
        + _trend(emails, emails_before)
        + ".",
        f"Respuestas: {total_replies} ({_pct(total_replies, people)} de las personas contactadas), {positive} con interés.",
        f"Pidieron reunión: {asked_meeting}. Reuniones registradas en el CRM: {meetings}.",
    ]
    selling = [
        f"Propuestas enviadas: {p_sent} · vistas por el cliente: {p_viewed} · aceptadas: {p_won}.",
        f"Oportunidades nuevas: {deals_new} · ganadas: {int(won[0])}"
        + (
            f" por {(won[2] + ' ') if won[2] else ''}{int(won[1]):,}".replace(",", ".")
            if int(won[0]) and won[1]
            else ""
        )
        + ".",
    ]
    if overdue:
        selling.append(f"Tareas vencidas sin hacer: {overdue}.")

    # Lo primero que conviene mejorar, según dónde se corta el embudo.
    rate = total_replies / people if people else 0
    if emails == 0:
        focus = "Esta vez no salieron correos desde tu buzón: lanza o reactiva una campaña para que el embudo tenga de dónde crecer."
    elif people >= 50 and rate < 0.01:
        focus = "Tu tasa de respuesta está bajo el 1 %: prueba asuntos más cortos, una primera línea personalizada y una sola pregunta al final. Revisa también que tu dominio tenga DKIM."
    elif total_replies and positive == 0:
        focus = "Te responden, pero sin interés: ajusta a quién le escribes (cargo y tipo de empresa) y abre con el problema, no con el producto."
    elif positive and meetings == 0 and asked_meeting == 0:
        focus = "Hay interés pero no reuniones: responde rápido y propone dos horarios concretos en cada respuesta positiva."
    elif p_viewed and not p_won:
        focus = "Tus clientes están viendo las propuestas: llama hoy a quien la abrió y pregunta qué falta para decidir."
    elif overdue:
        focus = (
            f"Tienes {overdue} tareas vencidas: ponte al día antes de abrir nuevas conversaciones."
        )
    else:
        focus = "Vas bien: mantén el ritmo de envíos y no dejes ninguna respuesta positiva sin contestar el mismo día."
    folded = fold(question)
    asked = {
        "correo": f"{label.capitalize()} enviaste {_n(emails, 'correo', 'correos')} a {_n(people, 'persona', 'personas')}.",
        "respuesta": f"{label.capitalize()} recibiste {_n(total_replies, 'respuesta', 'respuestas')}, {positive} con interés.",
        "reunion": f"{label.capitalize()} {_n(asked_meeting, 'persona pidió', 'personas pidieron')} reunión y registraste {_n(meetings, 'reunión', 'reuniones')} en el CRM.",
        "propuesta": f"{label.capitalize()} enviaste {_n(p_sent, 'propuesta', 'propuestas')}; {p_viewed} vistas y {p_won} aceptadas.",
        "oportunidad": f"{label.capitalize()} creaste {_n(deals_new, 'oportunidad', 'oportunidades')} y ganaste {int(won[0])}.",
        "venta": f"{label.capitalize()} ganaste {_n(int(won[0]), 'oportunidad', 'oportunidades')}"
        + (
            f" por {(won[2] + ' ') if won[2] else ''}{int(won[1]):,}".replace(",", ".")
            if int(won[0]) and won[1]
            else ""
        )
        + f" y enviaste {_n(p_sent, 'propuesta', 'propuestas')}.",
    }
    if re.search(r"\b(me falta|meta)\b", folded):
        lead = ("Aún no tengo tu meta guardada. Dímela así: «tengo que lograr 10 reuniones al mes, ¿cómo lo "
                f"logro?» y te armo el plan. Esto llevas {label}:")  # fmt: skip
    elif re.search(r"\bcontactos\b", folded):
        from orkesta.data.models import Contact

        base = select(func.count()).select_from(Contact).where(Contact.tenant_id == tenant_id)
        total = int(session.scalar(base) or 0)
        with_email = int(
            session.scalar(base.where(Contact.email.is_not(None), Contact.email != "")) or 0
        )
        new_ones = int(
            session.scalar(base.where(Contact.created_at >= s, Contact.created_at < e)) or 0
        )
        lead = (f"Tienes {_n(total, 'contacto', 'contactos')} en ORKESTA, {with_email} con correo; "
                f"{label} sumaste {new_ones}.")  # fmt: skip
    elif re.search(
        r"\b(vendi|vendimos|facture|facturamos|cerre|cerramos|gane|ganamos|ventas)\b", folded
    ):
        lead = asked["venta"]
    else:
        lead = next((line for key, line in asked.items() if re.search(rf"\b{key}", folded)), "")
    return {
        "summary": (lead if lead.endswith(":") else lead + " El detalle:") if lead else f"Tus números de {label} en ORKESTA:",
        "options": [], "tips": [],
        "sections": [{"title": "Prospección", "items": prospecting},
                     {"title": "Ventas", "items": selling},
                     {"title": "Lo primero que mejoraría", "items": [focus]}],
        "next_step": "Pregúntame «¿qué oportunidad priorizo esta semana?» para el siguiente paso.",
        "missing": [], "suggestions": ["¿Qué oportunidad priorizo esta semana?", "¿Cómo voy este mes?"],
    }  # fmt: skip
