#!/usr/bin/env bash
# Descarga la última imagen, aplica migraciones y reinicia sin perder datos.
set -euo pipefail
cd /opt/orkesta
docker compose pull app
docker compose up -d db
docker compose run --rm app python -m orkesta.cli release
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
# Tareas programadas: respaldo diario y vigilante cada 5 minutos (idempotente).
{ crontab -l 2>/dev/null | grep -v -e orkesta-backup -e orkesta-watchdog || true
  echo "30 8 * * * /opt/orkesta/backup.sh >> /var/log/orkesta-backup.log 2>&1 # orkesta-backup"
  echo "*/5 * * * * /opt/orkesta/watchdog.sh >> /var/log/orkesta-watchdog.log 2>&1 # orkesta-watchdog"
} | crontab -
echo "ORKESTA desplegado: $(grep ^PUBLIC_BASE_URL .env | cut -d= -f2-)"
