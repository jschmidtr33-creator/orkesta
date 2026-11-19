"""Conocimiento verificado de ORKESTA: lo que ya se comprobó en la práctica pasa a ser seguro.

- ``pattern_stats.verified``: buzones con ese formato que el verificador dio por válidos.
- ``people_index.email_checked_at``: cuándo se comprobó el correo por última vez.
- ``mail_domains``: dominios cuyo servidor acepta cualquier dirección (catch-all), para todos.

Con lo que ORKESTA ya sabe hasta hoy:
1. Cada contacto con correo válido (de cualquier empresa cliente) suma su formato al dominio y
   deja su dirección «verificado» en el índice.
2. Cada correo enviado que llegó sin rebotar (envíos ya aprendidos) deja su dirección
   «verificado» en el índice (salvo dominios catch-all, donde que llegue no prueba nada).

Revision ID: 0053
Revises: 0052
"""

from __future__ import annotations

from collections import Counter

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pattern_stats",
        sa.Column("verified", sa.Integer(), nullable=False, server_default="0"),
        schema="orkesta",
    )
    op.add_column(
        "people_index",
        sa.Column("email_checked_at", sa.DateTime(timezone=True), nullable=True),
        schema="orkesta",
    )
    op.create_table(
        "mail_domains",
        sa.Column("domain", sa.String(253), primary_key=True),
        sa.Column("catch_all", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="orkesta",
    )
    from orkesta.research.extract import detect_pattern

    bind = op.get_bind()
    # Las tablas de cada empresa tienen RLS forzado: se recorre empresa por empresa con su
    # contexto (sin alcance ni privacidad, como los procesos internos).
    tenants = [r[0] for r in bind.execute(sa.text("SELECT id FROM orkesta.tenants"))]
    catch_all: set[str] = set()
    valid: dict[str, tuple[str, str, object]] = {}
    delivered: dict[str, object] = {}
    for tenant in tenants:
        bind.execute(
            sa.text(
                "SELECT set_config('app.tenant_id', :t, true), set_config('app.scope', '', true), "
                "set_config('app.private', '', true)"
            ),
            {"t": str(tenant)},
        )
        catch_all |= {
            r[0]
            for r in bind.execute(sa.text(
                "SELECT DISTINCT lower(email_domain) FROM orkesta.contacts "
                "WHERE email_status = 'catch_all' AND email_domain IS NOT NULL"))
        }  # fmt: skip
        for email, first, last, checked in bind.execute(sa.text(
            "SELECT lower(email), first_name, last_name, coalesce(email_checked_at, updated_at) "
            "FROM orkesta.contacts WHERE email_status = 'valido' AND email IS NOT NULL"
        )):  # fmt: skip
            valid.setdefault(email, (first or "", last or "", checked))
        for email, at in bind.execute(sa.text(
            "SELECT lower(email), max(learned_at) FROM orkesta.campaign_recipients "
            "WHERE learned_at IS NOT NULL AND sent_count > 0 "
            "AND status IN ('active', 'completed', 'unsubscribed') GROUP BY lower(email)"
        )):  # fmt: skip
            delivered[email] = at
        _dedup_tasks(bind)  # la limpieza de 0050 no veía filas con RLS: se repite por empresa
    bind.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))

    for domain in catch_all:
        bind.execute(
            sa.text(
                "INSERT INTO orkesta.mail_domains (domain, catch_all) VALUES (:d, true) "
                "ON CONFLICT (domain) DO NOTHING"
            ),
            {"d": domain},
        )
    patterns: Counter[tuple[str, str]] = Counter()
    mark = sa.text(
        "UPDATE orkesta.people_index SET email_status = 'verificado', "
        "email_checked_at = coalesce(email_checked_at, :at) WHERE email = :e "
        "AND coalesce(email_status, '') NOT IN ('confirmado', 'rebotado')"
    )
    for email, (first, last, checked) in valid.items():
        domain = email.rsplit("@", 1)[-1]
        if domain in catch_all:
            continue
        pattern = detect_pattern(first, last, email)
        if pattern:
            patterns[(domain, pattern)] += 1
        bind.execute(mark, {"e": email, "at": checked})
    for (domain, pattern), n in patterns.items():
        bind.execute(
            sa.text(
                "INSERT INTO orkesta.pattern_stats (domain, pattern, verified) "
                "VALUES (:d, :p, :n) ON CONFLICT (domain, pattern) "
                "DO UPDATE SET verified = orkesta.pattern_stats.verified + :n"
            ),
            {"d": domain, "p": pattern, "n": n},
        )
    # Envíos que llegaron sin rebotar (ya aprendidos por la consolidación nocturna).
    for email, at in delivered.items():
        if email.rsplit("@", 1)[-1] not in catch_all:
            bind.execute(mark, {"e": email, "at": at})


def _dedup_tasks(bind: sa.Connection) -> None:
    bind.execute(
        sa.text("""
        DELETE FROM orkesta.activities a
        USING (
            SELECT id, row_number() OVER (
                PARTITION BY tenant_id, contact_id, body ORDER BY created_at, id) AS n
            FROM orkesta.activities
            WHERE type = 'task' AND done_at IS NULL
        ) d
        WHERE a.id = d.id AND d.n > 1
    """)
    )
    bind.execute(
        sa.text("""
        UPDATE orkesta.notifications n SET read_at = now()
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY tenant_id, user_id, kind, title ORDER BY created_at DESC, id) AS k
            FROM orkesta.notifications
            WHERE read_at IS NULL
        ) d
        WHERE n.id = d.id AND d.k > 1
    """)
    )


def downgrade() -> None:
    op.drop_table("mail_domains", schema="orkesta")
    op.drop_column("people_index", "email_checked_at", schema="orkesta")
    op.drop_column("pattern_stats", "verified", schema="orkesta")
