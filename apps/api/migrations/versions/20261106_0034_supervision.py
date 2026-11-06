"""Supervisión: cada persona ve solo su trabajo; las jefaturas ven además el de su equipo.

La separación vive en la base (políticas restrictivas de RLS), no en cada pantalla, para que
ninguna consulta pueda filtrar el trabajo de otra persona. Cuando la sesión no fija
``app.scope`` (administradores, procesos internos) no hay restricción adicional.

- ``app.scope``: personas cuyo trabajo puede verse («uuid,uuid»).
- ``app.user_id``: quien puede modificar; nadie modifica el trabajo de otra persona salvo un
  administrador.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

TENANT = "tenant_id::text = current_setting('app.tenant_id', true)"

FUNCTIONS = """
CREATE OR REPLACE FUNCTION orkesta.scope_open() RETURNS boolean
LANGUAGE sql STABLE AS $$ SELECT coalesce(current_setting('app.scope', true), '') = '' $$;

CREATE OR REPLACE FUNCTION orkesta.can_see(owner uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT owner IS NULL OR orkesta.scope_open()
      OR owner::text = ANY (string_to_array(current_setting('app.scope', true), ','))
$$;

CREATE OR REPLACE FUNCTION orkesta.can_edit(owner uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT owner IS NULL OR orkesta.scope_open()
      OR owner::text = current_setting('app.user_id', true)
$$;
"""

# Tablas con dueño directo: (tabla, columna del dueño)
OWNED = [
    ("contacts", "owner_user_id"),
    ("contact_lists", "created_by"),
    ("imports", "created_by"),
    ("deals", "owner_user_id"),
    ("mailboxes", "user_id"),
    ("campaigns", "created_by"),
    ("proposals", "created_by"),
    ("research_runs", "created_by"),
    ("enrichment_runs", "created_by"),
    ("validation_runs", "created_by"),
    ("audit_logs", "actor_user_id"),
]
# Tablas que dependen de otra: (tabla, columna, tabla madre, dueño de la madre)
CHILDREN = [
    ("list_members", "list_id", "contact_lists", "created_by"),
    ("campaign_recipients", "campaign_id", "campaigns", "created_by"),
    ("campaign_messages", "campaign_id", "campaigns", "created_by"),
    ("proposal_links", "proposal_id", "proposals", "created_by"),
    ("research_items", "run_id", "research_runs", "created_by"),
]


def _policies(table: str, see: str, edit: str) -> list[str]:
    t = f"orkesta.{table}"
    return [
        f"CREATE POLICY scope_select ON {t} AS RESTRICTIVE FOR SELECT USING ({see})",
        f"CREATE POLICY scope_update ON {t} AS RESTRICTIVE FOR UPDATE USING ({edit}) "
        f"WITH CHECK ({edit})",
        f"CREATE POLICY scope_delete ON {t} AS RESTRICTIVE FOR DELETE USING ({edit})",
    ]


def upgrade() -> None:
    op.create_table(
        "team_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("manager_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("tenant_id", "manager_id", "member_id",
                            name=op.f("uq_team_assignments_pair")),
        sa.CheckConstraint("manager_id <> member_id", name=op.f("ck_team_assignments_distinct")),
        schema="orkesta",
    )  # fmt: skip
    op.create_index("ix_team_assignments_manager", "team_assignments",
                    ["tenant_id", "manager_id"], schema="orkesta")  # fmt: skip
    op.execute("ALTER TABLE orkesta.team_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE orkesta.team_assignments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON orkesta.team_assignments USING ({TENANT}) "
        f"WITH CHECK ({TENANT})"
    )

    # Cada persona nombra sus listas: dos personas pueden tener «Decisores México».
    op.drop_constraint("uq_contact_lists_tenant_id_name", "contact_lists", schema="orkesta")
    op.create_unique_constraint("uq_contact_lists_tenant_id_created_by_name", "contact_lists",
                                ["tenant_id", "created_by", "name"], schema="orkesta")  # fmt: skip

    op.execute(FUNCTIONS)
    for table, owner in OWNED:
        for stmt in _policies(table, f"orkesta.can_see({owner})", f"orkesta.can_edit({owner})"):
            op.execute(stmt)
    for table, column, parent, owner in CHILDREN:
        see = f"orkesta.scope_open() OR EXISTS (SELECT 1 FROM orkesta.{parent} p WHERE p.id = {column})"
        edit = (
            f"orkesta.scope_open() OR EXISTS (SELECT 1 FROM orkesta.{parent} p "
            f"WHERE p.id = {column} AND orkesta.can_edit(p.{owner}))"
        )
        for stmt in _policies(table, see, edit):
            op.execute(stmt)
    # Notas y tareas: las propias, y las automáticas solo si se ve el contacto u oportunidad.
    see = (
        "orkesta.scope_open() OR (orkesta.can_see(user_id) AND (user_id IS NOT NULL OR ("
        "(deal_id IS NULL OR EXISTS (SELECT 1 FROM orkesta.deals d WHERE d.id = deal_id)) AND "
        "(contact_id IS NULL OR EXISTS (SELECT 1 FROM orkesta.contacts c WHERE c.id = contact_id))"
        ")))"
    )
    edit = (
        "orkesta.scope_open() OR (orkesta.can_edit(user_id) AND (user_id IS NOT NULL OR "
        "deal_id IS NULL OR EXISTS (SELECT 1 FROM orkesta.deals d WHERE d.id = deal_id "
        "AND orkesta.can_edit(d.owner_user_id))))"
    )
    for stmt in _policies("activities", see, edit):
        op.execute(stmt)


def downgrade() -> None:
    tables = [t for t, _ in OWNED] + [t for t, *_ in CHILDREN] + ["activities"]
    for table in tables:
        for name in ("scope_select", "scope_update", "scope_delete"):
            op.execute(f"DROP POLICY IF EXISTS {name} ON orkesta.{table}")
    op.execute("DROP FUNCTION IF EXISTS orkesta.can_edit(uuid)")
    op.execute("DROP FUNCTION IF EXISTS orkesta.can_see(uuid)")
    op.execute("DROP FUNCTION IF EXISTS orkesta.scope_open()")
    op.drop_table("team_assignments", schema="orkesta")
    op.drop_constraint("uq_contact_lists_tenant_id_created_by_name", "contact_lists",
                       schema="orkesta")  # fmt: skip
    op.create_unique_constraint("uq_contact_lists_tenant_id_name", "contact_lists",
                                ["tenant_id", "name"], schema="orkesta")  # fmt: skip
