#!/usr/bin/env bash
# Copia TODA la base de datos de Render a la nueva base en AWS (una sola vez).
# Uso: sudo /opt/orkesta/migrate-from-render.sh "postgresql://usuario:clave@host.render.com/orkesta"
# (la "External Database URL" de Render, en el panel de la base orkesta-db → Connect).
set -euo pipefail
cd /opt/orkesta
src="${1:-}"
if [ -z "$src" ]; then
  read -r -p "Pega la External Database URL de Render: " src
fi
src="${src/postgresql+psycopg:/postgresql:}"
docker compose up -d db
until docker compose exec -T db pg_isready -U postgres -d orkesta >/dev/null 2>&1; do sleep 2; done
echo "==> Copiando datos desde Render (puede tardar unos minutos)"
docker run --rm postgres:16-alpine pg_dump "$src" --no-owner --no-privileges --format=custom \
  > /tmp/render.dump
docker compose exec -T db pg_restore -U orkesta -d orkesta --no-owner --no-privileges \
  --clean --if-exists < /tmp/render.dump || true
rm -f /tmp/render.dump
echo "==> Listo. Filas copiadas por tabla principal:"
# Lectura de comprobación como administrador (el usuario de la app solo ve su empresa).
docker compose exec -T db psql -U postgres -d orkesta -c \
  "select 'empresas', count(*) from orkesta.tenants union all select 'contactos', count(*) from orkesta.contacts union all select 'usuarios', count(*) from orkesta.users union all select 'propuestas', count(*) from orkesta.proposals;"
