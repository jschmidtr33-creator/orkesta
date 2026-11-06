#!/usr/bin/env bash
# Respaldo de PostgreSQL: copia local (7 días) y en S3 si BACKUP_BUCKET está configurado.
set -euo pipefail
cd /opt/orkesta
mkdir -p backups
file="backups/orkesta-$(date -u +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U postgres -d orkesta --no-owner | gzip > "$file"
find backups -name 'orkesta-*.sql.gz' -mtime +7 -delete
bucket=$(grep ^BACKUP_BUCKET= .env | cut -d= -f2- || true)
if [ -n "${bucket}" ]; then
  aws s3 cp "$file" "s3://${bucket}/$(basename "$file")" --only-show-errors
fi
echo "$(date -u +%FT%TZ) respaldo ok: $file"
