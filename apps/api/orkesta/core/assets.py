"""Direcciones de las imágenes subidas (/t/i/<id>.<ext>).

Dentro de ORKESTA se guardan RELATIVAS («/t/i/…»): así no dependen del dominio y siguen
funcionando si el servidor cambia de dirección (por ejemplo, al pasar de Render a AWS). Solo los
correos que salen necesitan la dirección completa, y se arma al enviar con la dirección pública.
"""

from __future__ import annotations

import re

# Una imagen de ORKESTA con o sin dominio delante (http o https, cualquier host).
_ASSET = re.compile(r"(?:https?://[^\s\"'()<>/]+)?(/t/i/[0-9a-fA-F-]{36}\.(?:png|jpg|gif|webp))")
# Solo las relativas (al comienzo, o después de comillas, paréntesis o espacio).
_RELATIVE = re.compile(r"(^|[\"'(\s=])(/t/i/[0-9a-fA-F-]{36}\.(?:png|jpg|gif|webp))")

# Patrón equivalente para PostgreSQL (migración de datos guardados).
SQL_PATTERN = r"https?://[^\s\"'()<>/]+(/t/i/[0-9a-fA-F-]{36}\.(png|jpg|gif|webp))"


def relative(text: str | None) -> str:
    """«https://otro-dominio/t/i/x.png» → «/t/i/x.png»."""
    return _ASSET.sub(lambda m: m.group(1), text or "")


def absolute(text: str | None, base: str) -> str:
    """Para correos: «/t/i/x.png» → «https://dominio-actual/t/i/x.png»."""
    base = (base or "").rstrip("/")
    if not base:
        return text or ""
    return _RELATIVE.sub(lambda m: f"{m.group(1)}{base}{m.group(2)}", relative(text))


# Direcciones antiguas de ORKESTA (Render y la IP temporal de AWS): los enlaces guardados o
# pegados con ellas pasan a la dirección pública actual.
_OLD_HOST = re.compile(r"https?://(?:[a-z0-9-]+\.onrender\.com|[0-9-]+\.sslip\.io)(?=[/\s\"'<>)]|$)",
                       re.IGNORECASE)  # fmt: skip
SQL_OLD_HOST = r"https?://([a-z0-9-]+\.onrender\.com|[0-9-]+\.sslip\.io)"


def current_links(text: str | None, base: str) -> str:
    """«https://orkesta-web.onrender.com/app/p/x» → «https://orkestaapp.com/app/p/x»."""
    base = (base or "").rstrip("/")
    return _OLD_HOST.sub(base, text or "") if base else (text or "")
