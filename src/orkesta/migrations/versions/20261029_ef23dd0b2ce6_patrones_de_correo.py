"""patrones de correo

Decisión de patrón de correo en el dominio seleccionado: dominio que recibe el correo, patrón,
ranking de alternativas, confianza, razones y versión del modelo. Índice para reutilizar el
patrón de un dominio ya investigado por la organización.

Revision ID: ef23dd0b2ce6
Revises: bfd04a46a216
Create Date: 2026-10-29 20:51:38.628104+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ef23dd0b2ce6"
down_revision: str | None = "bfd04a46a216"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_LIST = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("domains", sa.Column("email_domain", sa.String(length=253), nullable=True))
    op.add_column("domains", sa.Column("email_pattern", sa.String(length=40), nullable=True))
    op.add_column(
        "domains",
        sa.Column(
            "pattern_ranked",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_JSON_LIST,
            nullable=False,
        ),
    )
    op.add_column(
        "domains", sa.Column("pattern_confidence_score", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        "domains", sa.Column("pattern_confidence_level", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "domains",
        sa.Column(
            "pattern_reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_JSON_LIST,
            nullable=False,
        ),
    )
    op.add_column(
        "domains", sa.Column("pattern_model_version", sa.String(length=40), nullable=True)
    )
    op.add_column(
        "domains", sa.Column("pattern_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_domains_pattern_confidence_level"),
        "domains",
        "pattern_confidence_level IS NULL OR pattern_confidence_level IN ('high', 'medium', 'low')",
    )
    op.create_check_constraint(
        op.f("ck_domains_pattern_confidence_score"),
        "domains",
        "pattern_confidence_score IS NULL OR pattern_confidence_score BETWEEN 0 AND 100",
    )
    op.create_index("ix_domains_org_domain", "domains", ["organization_id", "domain"])


def downgrade() -> None:
    op.drop_index("ix_domains_org_domain", table_name="domains")
    op.drop_constraint(op.f("ck_domains_pattern_confidence_score"), "domains", type_="check")
    op.drop_constraint(op.f("ck_domains_pattern_confidence_level"), "domains", type_="check")
    for column in (
        "pattern_checked_at",
        "pattern_model_version",
        "pattern_reason_codes",
        "pattern_confidence_level",
        "pattern_confidence_score",
        "pattern_ranked",
        "email_pattern",
        "email_domain",
    ):
        op.drop_column("domains", column)
