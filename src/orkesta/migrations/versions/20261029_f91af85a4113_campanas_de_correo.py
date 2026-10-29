"""campanas de correo

Cuentas de envío (Gmail OAuth o SMTP/IMAP con credenciales cifradas), campañas de hasta tres
pasos sobre listas, destinatarios, mensajes enviados y lista de exclusión.

Revision ID: f91af85a4113
Revises: 72324a891e85
Create Date: 2026-10-29 22:43:26.424359+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f91af85a4113"
down_revision: str | None = "72324a891e85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=120), nullable=True),
        sa.Column("smtp_host", sa.String(length=253), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_security", sa.String(length=10), nullable=True),
        sa.Column("imap_host", sa.String(length=253), nullable=True),
        sa.Column("imap_port", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=320), nullable=True),
        sa.Column("secret", sa.LargeBinary(), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("provider IN ('gmail', 'smtp')", name=op.f("ck_mail_accounts_provider")),
        sa.CheckConstraint(
            "smtp_security IS NULL OR smtp_security IN ('ssl', 'starttls')",
            name=op.f("ck_mail_accounts_smtp_security"),
        ),
        sa.CheckConstraint(
            "daily_limit BETWEEN 1 AND 2000", name=op.f("ck_mail_accounts_daily_limit")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_mail_accounts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_accounts")),
        sa.UniqueConstraint(
            "organization_id", "email", name=op.f("uq_mail_accounts_organization_id_email")
        ),
    )
    op.create_index(
        op.f("ix_mail_accounts_organization_id"), "mail_accounts", ["organization_id"], unique=False
    )
    op.create_table(
        "suppressions",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason IN ('unsubscribed', 'bounced', 'manual', 'contacted')",
            name=op.f("ck_suppressions_reason"),
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_suppressions_email_lowercase")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_suppressions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppressions")),
        sa.UniqueConstraint(
            "organization_id", "email", name=op.f("uq_suppressions_organization_id_email")
        ),
    )
    op.create_index(
        op.f("ix_suppressions_organization_id"), "suppressions", ["organization_id"], unique=False
    )
    op.create_table(
        "campaigns",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("list_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature_html", sa.Text(), nullable=True),
        sa.Column("signature_text", sa.Text(), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=60), nullable=False),
        sa.Column("window_start_hour", sa.SmallInteger(), nullable=False),
        sa.Column("window_end_hour", sa.SmallInteger(), nullable=False),
        sa.Column("weekdays", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("track_opens", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'running', 'paused', 'completed')",
            name=op.f("ck_campaigns_status"),
        ),
        sa.CheckConstraint("daily_limit BETWEEN 1 AND 2000", name=op.f("ck_campaigns_daily_limit")),
        sa.CheckConstraint(
            "window_end_hour BETWEEN 1 AND 24", name=op.f("ck_campaigns_window_end")
        ),
        sa.CheckConstraint(
            "window_start_hour BETWEEN 0 AND 23", name=op.f("ck_campaigns_window_start")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_campaigns_account_id_mail_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["prospect_lists.id"],
            name=op.f("fk_campaigns_list_id_prospect_lists"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_campaigns_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_campaigns_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(
        op.f("ix_campaigns_organization_id"), "campaigns", ["organization_id"], unique=False
    )
    op.create_table(
        "campaign_recipients",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("prospect_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("next_step", sa.SmallInteger(), nullable=False),
        sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thread_id", sa.String(length=200), nullable=True),
        sa.Column("first_message_id", sa.String(length=300), nullable=True),
        sa.Column("last_message_id", sa.String(length=300), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'replied', 'bounced', 'unsubscribed', 'completed', 'failed', 'skipped')",
            name=op.f("ck_campaign_recipients_status"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_recipients_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_campaign_recipients_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prospect_id"],
            ["prospects.id"],
            name=op.f("fk_campaign_recipients_prospect_id_prospects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_recipients")),
        sa.UniqueConstraint(
            "campaign_id", "email", name=op.f("uq_campaign_recipients_campaign_id_email")
        ),
    )
    op.create_index(
        "ix_campaign_recipients_due",
        "campaign_recipients",
        ["campaign_id", "status", "next_send_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_recipients_organization_id"),
        "campaign_recipients",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "campaign_messages",
        sa.Column("recipient_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("step", sa.SmallInteger(), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("message_id", sa.String(length=300), nullable=False),
        sa.Column("provider_id", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_campaign_messages_account_id_mail_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_campaign_messages_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["campaign_recipients.id"],
            name=op.f("fk_campaign_messages_recipient_id_campaign_recipients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_messages")),
    )
    op.create_index(
        op.f("ix_campaign_messages_organization_id"),
        "campaign_messages",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_messages_recipient_id"),
        "campaign_messages",
        ["recipient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_campaign_messages_recipient_id"), table_name="campaign_messages")
    op.drop_index(op.f("ix_campaign_messages_organization_id"), table_name="campaign_messages")
    op.drop_table("campaign_messages")
    op.drop_index(op.f("ix_campaign_recipients_organization_id"), table_name="campaign_recipients")
    op.drop_index("ix_campaign_recipients_due", table_name="campaign_recipients")
    op.drop_table("campaign_recipients")
    op.drop_index(op.f("ix_campaigns_organization_id"), table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index(op.f("ix_suppressions_organization_id"), table_name="suppressions")
    op.drop_table("suppressions")
    op.drop_index(op.f("ix_mail_accounts_organization_id"), table_name="mail_accounts")
    op.drop_table("mail_accounts")
