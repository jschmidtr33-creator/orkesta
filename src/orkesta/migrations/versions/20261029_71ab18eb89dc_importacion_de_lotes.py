"""importacion de lotes

Añade a los lotes el formato, los encabezados y el resumen de importación; a las filas, su
estado de validación, incidencias y valores normalizados; y hace único al contacto por empresa.

Revision ID: 71ab18eb89dc
Revises: 12aad826b06d
Create Date: 2026-10-29 16:25:01.540076+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "71ab18eb89dc"
down_revision: str | None = "12aad826b06d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_BATCH_STATUSES = "'draft', 'queued', 'running', 'completed', 'failed', 'cancelled'"
NEW_BATCH_STATUSES = (
    "'draft', 'importing', 'imported', 'queued', 'running', 'completed', 'failed', 'cancelled'"
)


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_contacts_company_person"),
        "contacts",
        ["company_id", "first_name_normalized", "last_name_normalized"],
    )

    op.add_column(
        "enrichment_batches",
        sa.Column("source_format", sa.String(length=10), server_default="csv", nullable=False),
    )
    op.add_column(
        "enrichment_batches",
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "enrichment_batches",
        sa.Column(
            "import_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("enrichment_batches", sa.Column("error_message", sa.Text(), nullable=True))
    op.drop_constraint(op.f("ck_enrichment_batches_status"), "enrichment_batches", type_="check")
    op.create_check_constraint(
        op.f("ck_enrichment_batches_status"),
        "enrichment_batches",
        f"status IN ({NEW_BATCH_STATUSES})",
    )

    op.add_column(
        "import_rows",
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
    )
    op.add_column(
        "import_rows",
        sa.Column(
            "issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "import_rows",
        sa.Column("normalized", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("import_rows", sa.Column("duplicate_of_row", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_import_rows_status"),
        "import_rows",
        "status IN ('pending', 'valid', 'invalid', 'duplicate')",
    )
    op.create_index(
        "ix_import_rows_batch_status", "import_rows", ["batch_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_import_rows_batch_status", table_name="import_rows")
    op.drop_constraint(op.f("ck_import_rows_status"), "import_rows", type_="check")
    op.drop_column("import_rows", "duplicate_of_row")
    op.drop_column("import_rows", "normalized")
    op.drop_column("import_rows", "issues")
    op.drop_column("import_rows", "status")

    # Los lotes en estados nuevos pasan a 'draft' para que la restricción anterior sea válida.
    op.execute(
        "UPDATE enrichment_batches SET status = 'draft' WHERE status IN ('importing', 'imported')"
    )
    op.drop_constraint(op.f("ck_enrichment_batches_status"), "enrichment_batches", type_="check")
    op.create_check_constraint(
        op.f("ck_enrichment_batches_status"),
        "enrichment_batches",
        f"status IN ({OLD_BATCH_STATUSES})",
    )
    op.drop_column("enrichment_batches", "error_message")
    op.drop_column("enrichment_batches", "import_summary")
    op.drop_column("enrichment_batches", "headers")
    op.drop_column("enrichment_batches", "source_format")

    op.drop_constraint(op.f("uq_contacts_company_person"), "contacts", type_="unique")
