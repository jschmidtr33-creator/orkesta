"""historial de contacto

Último contacto por correo (campañas de ORKESTA o envíos anteriores importados) y exclusión
por contacto reciente o respuesta previa en las campañas.

Revision ID: 92b07a601736
Revises: f91af85a4113
Create Date: 2026-10-29 22:52:16.996294+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "92b07a601736"
down_revision: str | None = "f91af85a4113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_history",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened", sa.Boolean(), nullable=False),
        sa.Column("replied", sa.Boolean(), nullable=False),
        sa.Column("stage", sa.String(length=60), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
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
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_contact_history_email_lowercase")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_contact_history_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_history")),
        sa.UniqueConstraint(
            "organization_id", "email", name=op.f("uq_contact_history_organization_id_email")
        ),
    )
    op.create_index(
        op.f("ix_contact_history_organization_id"),
        "contact_history",
        ["organization_id"],
        unique=False,
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "exclude_recent_days", sa.SmallInteger(), server_default=sa.text("90"), nullable=False
        ),
    )
    op.add_column(
        "campaigns",
        sa.Column("exclude_replied", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "exclude_replied")
    op.drop_column("campaigns", "exclude_recent_days")
    op.drop_index(op.f("ix_contact_history_organization_id"), table_name="contact_history")
    op.drop_table("contact_history")
