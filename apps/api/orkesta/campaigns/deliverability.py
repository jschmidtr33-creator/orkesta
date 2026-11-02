# ruff: noqa: E501  (consejos largos para el usuario)
"""Salud del dominio que envía: SPF, DKIM y DMARC (lo primero que revisan los filtros de
bancos y grandes empresas). Consulta DNS pública, sin terceros."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TxtResolver = Callable[[str], list[str] | None]  # None = no se pudo consultar


def _txt(name: str) -> list[str] | None:
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    try:
        return [b"".join(r.strings).decode(errors="replace") for r in resolver.resolve(name, "TXT")]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException:
        return None


RESOLVER: TxtResolver | None = None  # pruebas

GOOGLE_SPF = ("_spf.google.com", "spf.google.com")


def check(domain: str, provider: str = "google") -> dict[str, Any]:
    """Revisa SPF, DKIM y DMARC del dominio. Cada punto: ok | warn | bad | unknown + consejo."""
    txt = RESOLVER or _txt
    items: list[dict[str, str]] = []

    root = txt(domain)
    spf = [r for r in (root or []) if r.lower().startswith("v=spf1")]
    if root is None:
        items.append(
            {"id": "spf", "status": "unknown", "label": "SPF", "tip": "No se pudo consultar."}
        )
    elif not spf:
        items.append({"id": "spf", "status": "bad", "label": "SPF ausente",
                      "tip": "Agrega en tu DNS un registro TXT: v=spf1 include:_spf.google.com ~all"})  # fmt: skip
    elif len(spf) > 1:
        items.append({"id": "spf", "status": "bad", "label": "Hay más de un registro SPF",
                      "tip": "Deja uno solo que incluya a todos tus remitentes."})  # fmt: skip
    elif provider == "google" and not any(g in spf[0].lower() for g in GOOGLE_SPF):
        items.append({"id": "spf", "status": "bad", "label": "El SPF no autoriza a Google",
                      "tip": "Agrega include:_spf.google.com a tu registro SPF."})  # fmt: skip
    else:
        items.append({"id": "spf", "status": "ok", "label": "SPF correcto", "tip": spf[0][:160]})

    dkim = txt(f"google._domainkey.{domain}") if provider == "google" else None
    if dkim is None:
        items.append({"id": "dkim", "status": "unknown", "label": "DKIM",
                      "tip": "No se pudo consultar el selector de Google."})  # fmt: skip
    elif not any("v=dkim1" in r.lower() or "p=" in r.lower() for r in dkim):
        items.append({"id": "dkim", "status": "bad", "label": "DKIM sin activar",
                      "tip": "En la consola de Google Workspace: Aplicaciones › Gmail › Autenticar correo; genera la clave, publícala en tu DNS y pulsa «Iniciar autenticación»."})  # fmt: skip
    else:
        items.append(
            {
                "id": "dkim",
                "status": "ok",
                "label": "DKIM activo",
                "tip": "Firma de Google publicada.",
            }
        )

    dmarc = txt(f"_dmarc.{domain}")
    record = next((r for r in (dmarc or []) if r.lower().startswith("v=dmarc1")), "")
    if dmarc is None:
        items.append(
            {"id": "dmarc", "status": "unknown", "label": "DMARC", "tip": "No se pudo consultar."}
        )
    elif not record:
        items.append({"id": "dmarc", "status": "bad", "label": "DMARC ausente",
                      "tip": f"Agrega un TXT en _dmarc.{domain}: v=DMARC1; p=none; rua=mailto:dmarc@{domain}"})  # fmt: skip
    else:
        policy = next((p.split("=", 1)[1].strip() for p in record.split(";")
                       if p.strip().lower().startswith("p=")), "none")  # fmt: skip
        items.append({"id": "dmarc", "status": "ok", "label": f"DMARC presente (política {policy})",
                      "tip": record[:160]})  # fmt: skip

    statuses = {i["status"] for i in items}
    overall = "bad" if "bad" in statuses else "unknown" if "unknown" in statuses else "ok"
    return {"domain": domain, "overall": overall, "items": items}
