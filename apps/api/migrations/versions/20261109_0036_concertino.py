"""Concertino: ficha viva del producto y casos de asesoría.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

TENANT = "tenant_id::text = current_setting('app.tenant_id', true)"
KINDS = ("producto", "publico", "problema", "valor", "prueba", "precio", "competidor",
         "diferencial", "objecion", "pregunta")  # fmt: skip


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE orkesta.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE orkesta.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON orkesta.{table} USING ({TENANT}) "
               f"WITH CHECK ({TENANT})")  # fmt: skip


def _common() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]  # fmt: skip


def upgrade() -> None:
    op.create_table(
        "product_facts", *_common(),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="confirmado"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("evidence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(_in("kind", KINDS), name=op.f("ck_product_facts_kind")),
        sa.CheckConstraint(_in("status", ("confirmado", "aprendido", "descartado")),
                           name=op.f("ck_product_facts_status")),
        schema="orkesta",
    )  # fmt: skip
    op.create_index("ix_product_facts_tenant_kind", "product_facts",
                    ["tenant_id", "kind", "status"], schema="orkesta")  # fmt: skip
    _rls("product_facts")

    op.create_table(
        "advice_cases", *_common(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="SET NULL")),
        sa.Column("topic", sa.String(20), nullable=False),
        sa.Column("objection", sa.String(20)),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("shown", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("chosen", sa.String(40)),
        sa.Column("action", sa.String(12), nullable=False, server_default="ninguna"),
        sa.Column("final_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True)),
        sa.Column("outcome", sa.String(20)),
        sa.Column("outcome_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(_in("action", ("ninguna", "usada", "editada", "descartada")),
                           name=op.f("ck_advice_cases_action")),
        schema="orkesta",
    )  # fmt: skip
    op.create_index("ix_advice_cases_tenant_user", "advice_cases",
                    ["tenant_id", "user_id", "created_at"], schema="orkesta")  # fmt: skip
    op.create_index("ix_advice_cases_recipient", "advice_cases", ["recipient_id"],
                    schema="orkesta")  # fmt: skip
    _rls("advice_cases")


def downgrade() -> None:
    op.drop_table("advice_cases", schema="orkesta")
    op.drop_table("product_facts", schema="orkesta")
