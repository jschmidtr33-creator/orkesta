#!/usr/bin/env bash
# Instala el verificador de buzones de ORKESTA en un servidor Ubuntu nuevo.
# Uso (como root):  curl -fsSL https://raw.githubusercontent.com/jschmidtr33-creator/orkesta/main/apps/verifier/install.sh | bash
set -euo pipefail
DIR=/opt/orkesta-verifier
mkdir -p "$DIR" && cd "$DIR"
apt-get update -qq && apt-get install -y -qq python3 openssl curl >/dev/null
curl -fsSL https://raw.githubusercontent.com/jschmidtr33-creator/orkesta/main/apps/verifier/verifier.py -o verifier.py
IP=$(hostname -I | awk '{print $1}')
HOST=$(hostname -f 2>/dev/null || echo "$IP")
[ -f key.pem ] || openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 3650 -subj "/CN=$HOST" >/dev/null 2>&1
[ -f token ] || openssl rand -hex 24 > token
chmod 600 key.pem token
cat > /etc/systemd/system/orkesta-verifier.service <<UNIT
[Unit]
Description=Verificador de buzones ORKESTA
After=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=VERIFIER_TOKEN=$(cat token)
Environment=VERIFIER_HELO=$HOST
ExecStart=/usr/bin/python3 $DIR/verifier.py
Restart=always
User=root
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now orkesta-verifier
command -v ufw >/dev/null && ufw allow 8443/tcp >/dev/null || true
FP=$(openssl x509 -in cert.pem -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')
PORT25=$(timeout 6 bash -c 'cat < /dev/null > /dev/tcp/gmail-smtp-in.l.google.com/25' 2>/dev/null && echo "abierto" || echo "BLOQUEADO (pide el desbloqueo a tu proveedor)")
echo
echo "== Verificador ORKESTA instalado =="
echo "Puerto 25 de salida: $PORT25"
echo "Agrega estas variables en Render (servicio orkesta-web):"
echo "  VERIFIER_URL=https://$IP:8443"
echo "  VERIFIER_TOKEN=$(cat token)"
echo "  VERIFIER_FINGERPRINT=$FP"
