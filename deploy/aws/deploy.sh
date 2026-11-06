#!/usr/bin/env bash
# Descarga la última imagen, aplica migraciones y reinicia sin perder datos.
set -euo pipefail
cd /opt/orkesta
docker compose pull app
docker compose up -d db
docker compose run --rm app python -m orkesta.cli release
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
echo "ORKESTA desplegado: $(grep ^PUBLIC_BASE_URL .env | cut -d= -f2-)"
