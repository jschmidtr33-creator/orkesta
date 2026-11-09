#!/usr/bin/env bash
# Vigilante: si la app lleva dos revisiones seguidas sin responder, la reinicia y lo anota.
# Lo instala deploy.sh como tarea cada 5 minutos.
set -uo pipefail
cd /opt/orkesta
state=/var/tmp/orkesta-watchdog
status=$(docker inspect --format '{{.State.Health.Status}}' orkesta-app-1 2>/dev/null || echo "ausente")
if [ "$status" = "healthy" ] || [ "$status" = "starting" ]; then
  rm -f "$state"
  exit 0
fi
if [ -f "$state" ]; then
  echo "$(date -u +%FT%TZ) app en estado «$status» por segunda vez: reinicio"
  docker compose up -d app
  rm -f "$state"
else
  echo "$(date -u +%FT%TZ) app en estado «$status»: reviso de nuevo en 5 minutos"
  touch "$state"
fi
