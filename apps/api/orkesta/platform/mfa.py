"""Doble verificación (TOTP, RFC 6238) con motor propio: compatible con Google Authenticator,
Microsoft Authenticator, Authy y similares. Códigos de respaldo de un solo uso."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

ISSUER = "ORKESTA"
STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def verify(
    secret: str, code: str, last_step: int | None = None, now: float | None = None
) -> int | None:
    """Paso válido (para impedir reutilizar el mismo código) o ``None``. Tolera ±30 s."""
    code = "".join(c for c in code if c.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in (current, current - 1, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, email: str) -> str:
    label = quote(f"{ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&digits={DIGITS}&period={STEP}"


def qr_data_uri(uri: str) -> str:
    import segno

    return str(segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#0B1220"))


def recovery_codes(n: int = 8) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
            for _ in range(n)]  # fmt: skip


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()
