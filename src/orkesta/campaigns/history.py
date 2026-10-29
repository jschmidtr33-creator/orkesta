"""Historial de contacto: envíos anteriores importados y campañas de ORKESTA.

Las planillas del script de mailing anterior traen columnas como SENT, OPENED, REPLIED,
FECHAENVIO (o Sent_At) y ETAPA. Cada fila enviada se registra como contacto previo para no
volver a escribirle antes de tiempo a la misma persona.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.catalog.prospects import Sheet
from orkesta.db.models import ContactHistory
from orkesta.engine.normalize.text import fold

_EMAIL = re.compile(r"^[a-z0-9][a-z0-9._%+'-]{0,63}@[a-z0-9-]+(\.[a-z0-9-]+)+$")
_TRUE = frozenset({"x", "si", "sí", "yes", "true", "1", "verdadero", "enviado"})
SENT_COLUMNS = ("sent", "enviado", "followupsent")
DATE_COLUMNS = ("fechaenvio", "sentat", "fechadeenvio", "fechafollowup", "followupsentat")
REPLIED_COLUMNS = ("replied", "respondio", "followupreplied")
OPENED_COLUMNS = ("opened", "abierto", "followupopened")
STAGE_COLUMNS = ("etapaetapa", "etapa", "estado")
EMAIL_COLUMNS = ("email", "correo", "correoelectronico", "e-mail")


@dataclass(frozen=True)
class HistoryEntry:
    email: str
    contacted_at: datetime | None
    opened: bool
    replied: bool
    stage: str | None


def _canonical(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(header))


def _date(value: str) -> datetime | None:
    value = value.strip()[:19]
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def history_from_sheets(sheets: Iterable[Sheet]) -> list[HistoryEntry]:
    """Filas con evidencia de envío (marca SENT o fecha de envío) y correo válido."""
    found: dict[str, HistoryEntry] = {}
    for sheet in sheets:
        headers = [_canonical(h) for h in sheet.headers]

        def cols(names: tuple[str, ...], headers: list[str] = headers) -> list[int]:
            return [i for i, h in enumerate(headers) if h in {_canonical(n) for n in names}]

        email_cols, sent_cols, date_cols = (
            cols(EMAIL_COLUMNS),
            cols(SENT_COLUMNS),
            cols(DATE_COLUMNS),
        )
        if not email_cols or not (sent_cols or date_cols):
            continue
        replied_cols, opened_cols, stage_cols = (
            cols(REPLIED_COLUMNS), cols(OPENED_COLUMNS), cols(STAGE_COLUMNS),
        )  # fmt: skip
        for row in sheet.rows:
            email = row[email_cols[0]].strip().lower()
            if not _EMAIL.match(email):
                continue
            dates = [d for d in (_date(row[i]) for i in date_cols) if d]
            sent = any(fold(row[i]) in _TRUE for i in sent_cols) or bool(dates)
            if not sent:
                continue
            entry = HistoryEntry(
                email=email,
                contacted_at=max(dates) if dates else None,
                opened=any(fold(row[i]) in _TRUE for i in opened_cols),
                replied=any(fold(row[i]) in _TRUE for i in replied_cols)
                or any("respond" in fold(row[i]) for i in stage_cols),
                stage=next((row[i].strip()[:60] for i in stage_cols if row[i].strip()), None),
            )
            previous = found.get(email)
            found[email] = merge_entries(previous, entry) if previous else entry
    return list(found.values())


def merge_entries(a: HistoryEntry, b: HistoryEntry) -> HistoryEntry:
    dates = [d for d in (a.contacted_at, b.contacted_at) if d]
    return HistoryEntry(
        a.email, max(dates) if dates else None, a.opened or b.opened, a.replied or b.replied,
        b.stage or a.stage,
    )  # fmt: skip


def upsert_history(
    session: Session, organization_id: uuid.UUID, entries: Iterable[HistoryEntry], source: str
) -> int:
    items = list(entries)
    for start in range(0, len(items), 1000):
        values = [
            {
                "id": uuid.uuid4(),
                "organization_id": organization_id,
                "email": e.email,
                # Sin fecha conocida se toma la de hoy: mejor no repetir que repetir de más.
                "last_contacted_at": e.contacted_at or datetime.now(UTC),
                "opened": e.opened,
                "replied": e.replied,
                "stage": e.stage,
                "source": source[:200],
            }
            for e in items[start : start + 1000]
        ]
        statement = pg_insert(ContactHistory).values(values)
        excluded = statement.excluded
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_contact_history_organization_id_email",
                set_={
                    "last_contacted_at": func.greatest(
                        ContactHistory.last_contacted_at, excluded.last_contacted_at
                    ),
                    "opened": ContactHistory.opened | excluded.opened,
                    "replied": ContactHistory.replied | excluded.replied,
                    "stage": func.coalesce(excluded.stage, ContactHistory.stage),
                    "source": excluded.source,
                    "updated_at": func.now(),
                },
            )
        )
    return len(items)


def record_contact(
    session: Session,
    organization_id: uuid.UUID,
    email: str,
    when: datetime,
    *,
    replied: bool = False,
    source: str = "campaña ORKESTA",
) -> None:
    upsert_history(
        session,
        organization_id,
        [
            HistoryEntry(
                email.lower(), when, False, replied, "Respondió" if replied else "Contactado"
            )
        ],
        source,
    )


def excluded_by_history(
    session: Session, organization_id: uuid.UUID, *, recent_days: int, exclude_replied: bool
) -> set[str]:
    conditions = []
    if recent_days > 0:
        since = datetime.combine(date.today(), datetime.min.time(), UTC) - timedelta(
            days=recent_days
        )
        conditions.append(ContactHistory.last_contacted_at >= since)
    if exclude_replied:
        conditions.append(ContactHistory.replied.is_(True))
    if not conditions:
        return set()
    return set(
        session.scalars(
            select(ContactHistory.email).where(
                ContactHistory.organization_id == organization_id, or_(*conditions)
            )
        )
    )
