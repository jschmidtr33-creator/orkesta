#!/usr/bin/env bash
# Respaldo diario de PostgreSQL: copia local (2 días, el disco es pequeño) y copia cifrada en S3
# si BACKUP_BUCKET está configurado. Deja el resultado en la base para verlo en Administración.
set -uo pipefail
cd /opt/orkesta
mkdir -p backups
find backups -name 'orkesta-*.sql.gz' -mtime +1 -delete
file="backups/orkesta-$(date -u +%Y%m%d-%H%M).sql.gz"
status() {  # status ok|error s3(true|false) mensaje
  local size=0
  [ -f "$file" ] && size=$(du -m "$file" | cut -f1)
  docker compose exec -T db psql -U postgres -d orkesta -q -c "INSERT INTO orkesta.platform_settings(key, value)
    VALUES ('respaldo', jsonb_build_object('at', now(), 'ok', $1, 's3', $2, 'size_mb', $size, 'note', '$3'))
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();" </dev/null >/dev/null 2>&1 || true
}
if ! docker compose exec -T db pg_dump -U postgres -d orkesta --no-owner </dev/null | gzip > "$file" || ! gzip -t "$file"; then
  status false false "La copia de la base falló"
  echo "$(date -u +%FT%TZ) respaldo FALLÓ"
  exit 1
fi
bucket=$(grep ^BACKUP_BUCKET= .env | cut -d= -f2- || true)
if [ -n "${bucket}" ]; then
  if aws s3 cp "$file" "s3://${bucket}/$(basename "$file")" --sse AES256 --only-show-errors; then
    status true true "Copia local y en S3"
  else
    status true false "Copia local lista; la subida a S3 falló (revisa el rol de IAM del servidor)"
  fi
else
  status true false "Copia local lista; falta conectar S3"
fi
echo "$(date -u +%FT%TZ) respaldo ok: $file"
