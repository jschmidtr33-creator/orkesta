#!/usr/bin/env bash
# Respaldo de PostgreSQL: copia local (2 días, el disco es pequeño) y en S3 si BACKUP_BUCKET está configurado.
set -euo pipefail
cd /opt/orkesta
mkdir -p backups
# Primero se borran las copias viejas: así el disco nunca tiene más de dos más la nueva.
find backups -name 'orkesta-*.sql.gz' -mtime +1 -delete
file="backups/orkesta-$(date -u +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U postgres -d orkesta --no-owner | gzip > "$file"
bucket=$(grep ^BACKUP_BUCKET= .env | cut -d= -f2- || true)
if [ -n "${bucket}" ]; then
  aws s3 cp "$file" "s3://${bucket}/$(basename "$file")" --only-show-errors
fi
echo "$(date -u +%FT%TZ) respaldo ok: $file"
