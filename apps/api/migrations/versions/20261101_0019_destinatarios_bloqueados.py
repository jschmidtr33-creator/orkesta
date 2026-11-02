"""Destinatarios «bloqueados» por el filtro del destinatario (distinto de dirección inexistente).

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

NEW = ("pending", "active", "replied", "bounced", "blocked", "unsubscribed", "completed", "failed",
       "skipped")  # fmt: skip
OLD = tuple(s for s in NEW if s != "blocked")


def _set(values: tuple[str, ...]) -> None:
    op.drop_constraint("ck_campaign_recipients_status", "campaign_recipients", schema="orkesta")
    allowed = ", ".join(repr(v) for v in values)
    op.create_check_constraint(
        "ck_campaign_recipients_status",
        "campaign_recipients",
        f"status IN ({allowed})",
        schema="orkesta",
    )


def upgrade() -> None:
    _set(NEW)


def downgrade() -> None:
    op.execute("UPDATE orkesta.campaign_recipients SET status = 'bounced' WHERE status = 'blocked'")
    _set(OLD)
