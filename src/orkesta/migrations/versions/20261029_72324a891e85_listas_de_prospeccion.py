"""listas de prospeccion

Listas de prospección y sus miembros, con correo principal y dos alternativos (confianza y
origen: conocido, patrón real de la empresa o previo del país).

Revision ID: 72324a891e85
Revises: 41adcd14c4b7
Create Date: 2026-10-29 22:28:00.525838+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "72324a891e85"
down_revision: str | None = "41adcd14c4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prospect_lists",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
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
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_prospect_lists_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_prospect_lists_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prospect_lists")),
        sa.UniqueConstraint(
            "organization_id", "name", name=op.f("uq_prospect_lists_organization_id_name")
        ),
    )
    op.create_index(
        op.f("ix_prospect_lists_organization_id"),
        "prospect_lists",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "prospect_list_members",
        sa.Column("list_id", sa.UUID(), nullable=False),
        sa.Column("prospect_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_confidence", sa.SmallInteger(), nullable=True),
        sa.Column("email_source", sa.String(length=20), nullable=True),
        sa.Column("alt_email_1", sa.String(length=320), nullable=True),
        sa.Column("alt_email_1_confidence", sa.SmallInteger(), nullable=True),
        sa.Column("alt_email_2", sa.String(length=320), nullable=True),
        sa.Column("alt_email_2_confidence", sa.SmallInteger(), nullable=True),
        sa.Column("email_pattern", sa.String(length=40), nullable=True),
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
            "email_source IS NULL OR email_source IN ('known', 'real_pattern', 'country_prior')",
            name=op.f("ck_prospect_list_members_email_source"),
        ),
        sa.CheckConstraint(
            "email_confidence IS NULL OR email_confidence BETWEEN 0 AND 100",
            name=op.f("ck_prospect_list_members_email_confidence"),
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["prospect_lists.id"],
            name=op.f("fk_prospect_list_members_list_id_prospect_lists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_prospect_list_members_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prospect_id"],
            ["prospects.id"],
            name=op.f("fk_prospect_list_members_prospect_id_prospects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prospect_list_members")),
        sa.UniqueConstraint(
            "list_id", "prospect_id", name=op.f("uq_prospect_list_members_list_id_prospect_id")
        ),
    )
    op.create_index(
        op.f("ix_prospect_list_members_list_id"), "prospect_list_members", ["list_id"], unique=False
    )
    op.create_index(
        op.f("ix_prospect_list_members_organization_id"),
        "prospect_list_members",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prospect_list_members_prospect_id"),
        "prospect_list_members",
        ["prospect_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_prospect_list_members_prospect_id"), table_name="prospect_list_members")
    op.drop_index(
        op.f("ix_prospect_list_members_organization_id"), table_name="prospect_list_members"
    )
    op.drop_index(op.f("ix_prospect_list_members_list_id"), table_name="prospect_list_members")
    op.drop_table("prospect_list_members")
    op.drop_index(op.f("ix_prospect_lists_organization_id"), table_name="prospect_lists")
    op.drop_table("prospect_lists")
