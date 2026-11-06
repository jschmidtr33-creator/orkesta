#!/usr/bin/env bash
# Copia TODA la base de datos de Render a la nueva base en AWS.
# Uso: sudo /opt/orkesta/migrate-from-render.sh "postgresql://usuario:clave@host.render.com/orkesta"
# (la "External Database URL" de Render: base orkesta-db → Connect → External).
#
# La base de Render aplica el aislamiento entre empresas (RLS) incluso a su dueño, así que no
# sirve pg_dump: ORKESTA copia empresa por empresa con su propio comando, sin tocar Render.
set -euo pipefail
cd /opt/orkesta
src="${1:-}"
if [ -z "$src" ]; then
  read -r -p "Pega la External Database URL de Render: " src
fi
src="${src/postgresql+psycopg:/postgresql:}"
admin_password=$(grep '^DB_ADMIN_PASSWORD=' .env | cut -d= -f2-)
docker compose up -d db
until docker compose exec -T db pg_isready -U postgres -d orkesta >/dev/null 2>&1; do sleep 2; done
docker compose pull app
echo "==> Preparando las tablas en AWS"
docker compose run --rm -T app python -m orkesta.cli migrate
echo "==> Copiando datos desde Render, empresa por empresa (puede tardar unos minutos)"
docker compose run --rm -T \
  -e ORKESTA_SOURCE_URL="$src" \
  -e ORKESTA_TARGET_URL="postgresql://postgres:${admin_password}@db:5432/orkesta" \
  app python -m orkesta.cli copiar-base
echo "==> Comprobación (como administrador):"
docker compose exec -T db psql -U postgres -d orkesta -c \
  "select 'empresas', count(*) from orkesta.tenants union all select 'usuarios', count(*) from orkesta.users union all select 'contactos', count(*) from orkesta.contacts union all select 'perfiles del índice', count(*) from orkesta.people_index union all select 'propuestas', count(*) from orkesta.proposals;"
