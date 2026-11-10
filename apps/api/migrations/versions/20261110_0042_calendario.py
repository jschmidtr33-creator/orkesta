"""Calendario: Google Calendar de cada persona sincronizado en ORKESTA (en ambos sentidos).

- mailboxes: si el buzón de Gmail tiene permiso de calendario y el estado de la sincronización.
- calendar_events: los eventos de cada persona. Son privados: solo los ve su dueño (igual que
  los contactos), ni la administración.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

TENANT = "tenant_id::text = current_setting('app.tenant_id', true)"
PRIVATE = ("coalesce(current_setting('app.private', true), '') = '' "
           "OR user_id::text = current_setting('app.private', true)")  # fmt: skip


def upgrade() -> None:
    for column in (
        sa.Column("calendar_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calendar_sync_token", sa.Text()),
        sa.Column("calendar_synced_at", sa.DateTime(timezone=True)),
        sa.Column("calendar_error", sa.String(300)),
    ):  # fmt: skip
        op.add_column("mailboxes", column, schema="orkesta")
    op.create_table(
        "calendar_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.mailboxes.id", ondelete="SET NULL")),
        sa.Column("google_id", sa.String(1024)),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("location", sa.String(500), nullable=False, server_default=""),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attendees", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("meet_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("html_link", sa.String(1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="confirmed"),
        sa.Column("source", sa.String(10), nullable=False, server_default="google"),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.contacts.id", ondelete="SET NULL")),
        sa.Column("deal_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.deals.id", ondelete="SET NULL")),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.campaigns.id", ondelete="SET NULL")),
        sa.CheckConstraint("status IN ('confirmed', 'tentative', 'cancelled')",
                           name="ck_calendar_events_status"),
        sa.CheckConstraint("source IN ('google', 'orkesta')", name="ck_calendar_events_source"),
        sa.UniqueConstraint("user_id", "google_id", name="uq_calendar_events_user_google"),
        schema="orkesta",
    )  # fmt: skip
    op.create_index("ix_calendar_events_user_start", "calendar_events",
                    ["tenant_id", "user_id", "start_at"], schema="orkesta")  # fmt: skip
    t = "orkesta.calendar_events"
    op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({TENANT}) WITH CHECK ({TENANT})")
    op.execute(f"CREATE POLICY private_select ON {t} AS RESTRICTIVE FOR SELECT USING ({PRIVATE})")
    op.execute(f"CREATE POLICY private_update ON {t} AS RESTRICTIVE FOR UPDATE "
               f"USING ({PRIVATE}) WITH CHECK (true)")  # fmt: skip
    op.execute(f"CREATE POLICY private_delete ON {t} AS RESTRICTIVE FOR DELETE USING ({PRIVATE})")


def downgrade() -> None:
    op.drop_table("calendar_events", schema="orkesta")
    for name in ("calendar_enabled", "calendar_sync_token", "calendar_synced_at",
                 "calendar_error"):  # fmt: skip
        op.drop_column("mailboxes", name, schema="orkesta")
