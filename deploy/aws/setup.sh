#!/usr/bin/env bash
# Prepara un servidor Ubuntu 24.04 nuevo (AWS EC2) para ORKESTA. Ejecutar una sola vez:
#   curl -fsSL https://raw.githubusercontent.com/jschmidtr33-creator/orkesta/main/deploy/aws/setup.sh | sudo bash
set -euo pipefail

echo "==> Actualizando el sistema e instalando Docker"
apt-get update -y
apt-get install -y ca-certificates curl unzip cron
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker cron

echo "==> Memoria de intercambio de 2 GB (evita reinicios por falta de memoria)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/99-orkesta.conf
fi

echo "==> AWS CLI (para respaldos en S3)"
if ! command -v aws >/dev/null; then
  arch=$(uname -m)
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip" -o /tmp/awscli.zip
  unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscli.zip
fi

echo "==> Archivos de ORKESTA en /opt/orkesta"
mkdir -p /opt/orkesta
cd /opt/orkesta
base=https://raw.githubusercontent.com/jschmidtr33-creator/orkesta/main/deploy/aws
mkdir -p initdb
for f in docker-compose.yml Caddyfile deploy.sh backup.sh migrate-from-render.sh initdb/01-orkesta.sh; do
  if [ -f "/tmp/orkesta-deploy/$f" ]; then
    cp "/tmp/orkesta-deploy/$f" "$f"  # enviado por GitHub Actions (sin caché)
  else
    curl -fsSL "$base/$f" -o "$f"
  fi
done
chmod +x deploy.sh backup.sh migrate-from-render.sh initdb/01-orkesta.sh
if [ ! -f .env ]; then
  curl -fsSL "$base/env.example" -o .env
  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$(openssl rand -hex 24)|" .env
  sed -i "s|^DB_ADMIN_PASSWORD=.*|DB_ADMIN_PASSWORD=$(openssl rand -hex 24)|" .env
  chmod 600 .env
fi

echo "==> Respaldo diario a las 3:30 a. m. (hora del servidor, UTC)"
# En un servidor nuevo no hay tareas programadas: «crontab -l» falla y no debe detener todo.
{ crontab -l 2>/dev/null | grep -v orkesta-backup || true
  echo "30 8 * * * /opt/orkesta/backup.sh >> /var/log/orkesta-backup.log 2>&1 # orkesta-backup"
} | crontab -

usermod -aG docker ubuntu 2>/dev/null || true
echo
echo "Listo. Ahora edita /opt/orkesta/.env (sudo nano /opt/orkesta/.env) y luego ejecuta:"
echo "  sudo /opt/orkesta/migrate-from-render.sh   # copia los datos desde Render"
echo "  sudo /opt/orkesta/deploy.sh                # arranca ORKESTA"
