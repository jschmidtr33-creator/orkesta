#!/usr/bin/env python3
"""Verificador de buzones de ORKESTA (motor propio, solo biblioteca estándar de Python).

Corre en un servidor pequeño con el puerto 25 de salida abierto. Recibe por HTTPS (certificado
propio, fijado por huella en ORKESTA) pedidos firmados con un token y conversa con el servidor de
correo del destinatario sin enviar ningún mensaje: EHLO, MAIL FROM, RCPT TO, QUIT.

Variables: VERIFIER_TOKEN (obligatoria), VERIFIER_HELO (nombre del servidor),
VERIFIER_SENDER (remitente de prueba), VERIFIER_PORT (8443), VERIFIER_CERT / VERIFIER_KEY.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import smtplib
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("VERIFIER_TOKEN", "")
HELO = os.environ.get("VERIFIER_HELO", "verificador.local")
SENDER = os.environ.get("VERIFIER_SENDER", f"verificacion@{HELO}")
PORT = int(os.environ.get("VERIFIER_PORT", "8443"))
MX_RE = re.compile(r"^[A-Za-z0-9.-]{3,253}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{3,253}$")
MAX_ADDRESSES = 25
RATE = {"window": 60.0, "limit": 120}
_hits: list[float] = []
_lock = threading.Lock()


def probe(mx: str, addresses: list[str]) -> dict[str, list]:
    results: dict[str, list] = {}
    try:
        server = smtplib.SMTP(timeout=15, local_hostname=HELO)
        server.connect(mx, 25)
    except (OSError, smtplib.SMTPException) as exc:
        return {"__error__": [0, f"sin conexión: {exc}"[:200]]}
    try:
        server.ehlo_or_helo_if_needed()
        if server.has_extn("starttls"):
            try:
                server.starttls()
                server.ehlo()
            except (smtplib.SMTPException, OSError):
                pass
        code, _ = server.mail(SENDER)
        if code >= 400:
            return {a: [code, "remitente rechazado"] for a in addresses}
        for address in addresses:
            code, message = server.rcpt(address)
            results[address] = [code, message.decode(errors="replace")[:200]]
    except (smtplib.SMTPException, OSError) as exc:
        for address in addresses:
            results.setdefault(address, [0, f"conexión interrumpida: {exc}"[:200]])
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            server.close()
    return results


class Handler(BaseHTTPRequestHandler):
    server_version = "orkesta-verifier"

    def _send(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # sin correos en los registros
        return

    def do_GET(self) -> None:
        self._send(200, {"ok": True, "helo": HELO}) if self.path == "/health" else self._send(404, {})

    def do_POST(self) -> None:
        if self.path != "/probe":
            return self._send(404, {})
        auth = self.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(auth, f"Bearer {TOKEN}"):
            return self._send(401, {"error": "token"})
        with _lock:
            now = time.monotonic()
            _hits[:] = [t for t in _hits if now - t < RATE["window"]]
            if len(_hits) >= RATE["limit"]:
                return self._send(429, {"error": "ritmo"})
            _hits.append(now)
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 64_000)
            data = json.loads(self.rfile.read(length) or b"{}")
            mx = str(data.get("mx", ""))
            addresses = [str(a) for a in data.get("addresses", [])][:MAX_ADDRESSES]
        except (ValueError, TypeError):
            return self._send(400, {"error": "formato"})
        if not MX_RE.match(mx) or not addresses or not all(EMAIL_RE.match(a) for a in addresses):
            return self._send(400, {"error": "datos"})
        self._send(200, {"results": probe(mx, addresses)})


def main() -> None:
    if len(TOKEN) < 24:
        raise SystemExit("Define VERIFIER_TOKEN (al menos 24 caracteres).")
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ.get("VERIFIER_CERT", "cert.pem"), os.environ.get("VERIFIER_KEY", "key.pem"))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print(f"Verificador ORKESTA escuchando en el puerto {PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
