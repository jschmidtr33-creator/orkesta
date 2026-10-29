"""De correos publicados a observaciones de patrón.

Cada correo encontrado se clasifica:

- **genérico** (ventas@, contacto@, rh@…): no dice nada del patrón personal;
- **con nombre**: el texto que lo rodea trae un nombre de persona y algún patrón, con alguna
  variante de ese nombre, produce exactamente la parte local (evidencia fuerte);
- **estructural**: no hay nombre cerca, pero la forma de la parte local y el diccionario de
  nombres de pila sugieren el patrón (``maria.lopez`` → nombre.apellido; evidencia débil).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orkesta.engine.email.harvest import FoundEmail
from orkesta.engine.email.patterns import matching_patterns
from orkesta.engine.normalize.first_names import FIRST_NAMES
from orkesta.engine.normalize.names import normalize_person, split_full_name
from orkesta.engine.normalize.text import fold

ROLE_WORDS = frozenset(
    """info informacion informes contacto contact contactenos contactanos hola hello hi
    ventas venta sales comercial negocios business cotizaciones cotizacion pedidos orders
    admin administracion administrador webmaster web sistemas it soporte support ayuda help
    servicio servicios atencion clientes cliente customer customercare service cs
    rh rrhh recursoshumanos humanos talento talentohumano empleo empleos jobs careers trabajo
    trabajaconnosotros reclutamiento seleccion vacantes cv curriculum
    prensa press comunicacion comunicaciones comunicacionsocial media medios marketing
    mercadeo publicidad eventos social redes
    facturacion factura facturas billing cobranza cobros pagos tesoreria contabilidad
    finanzas compras proveedores proveedor suppliers procurement logistica
    legal juridico privacidad privacy datospersonales datos arco habeasdata cumplimiento
    compliance etica denuncias transparencia auditoria calidad
    inversionistas investors ir relacioninversionistas accionistas
    noreply no-reply donotreply mailer postmaster abuse office oficina recepcion
    direccion gerencia secretaria mesa mesadeayuda servicioalcliente sac pqr pqrs pqrsd
    notificaciones notificacionesjudiciales boletin newsletter suscripciones""".split()
)

_SEPARATORS = re.compile(r"[._-]")
_WORD = r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü'’]+"
# Palabras capitalizadas seguidas (con partículas de apellido), sin importar la puntuación que
# rodea al nombre: "Mariana Torres, Gerente", "Ing. José de la Cruz:".
_NAME_RUN = re.compile(rf"{_WORD}(?:\s+(?:(?:de|del|la|las|los|y)\s+)*{_WORD})+")
_PARTICLES = frozenset({"de", "del", "la", "las", "los", "y"})


@dataclass(frozen=True)
class Observation:
    pattern: str
    kind: str  # "named" | "structural"
    email_domain: str

    @property
    def weight(self) -> float:
        return 1.0 if self.kind == "named" else 0.35


def is_role_address(local: str) -> bool:
    tokens = [t for t in re.split(r"[\s._-]+", fold(local)) if t]
    folded = "".join(tokens)
    if folded in ROLE_WORDS:
        return True
    if any(t in ROLE_WORDS or t.rstrip("0123456789") in ROLE_WORDS for t in tokens):
        return True
    # Muchos dígitos: buzones numerados (ventas01, mx123…), no personas.
    return sum(c.isdigit() for c in folded) >= 3


def named_patterns(item: FoundEmail) -> list[str]:
    """Patrones confirmados por un nombre de persona escrito cerca del correo."""
    for match in _NAME_RUN.finditer(item.context):
        for candidate in _spans(match.group(0).split()):
            split = split_full_name(candidate)
            if not split.first or not split.last:
                continue
            person = normalize_person(split.first, split.last)
            if not person.is_valid:
                continue
            found = matching_patterns(item.local, person)
            if found:
                return found
    return []


def _spans(words: list[str]) -> list[str]:
    """Tramos de 2 a 4 palabras ("Ventas Mariana Torres" → "Mariana Torres"), los largos primero.

    Un tramo no empieza ni termina en partícula.
    """
    spans: list[str] = []
    for size in range(min(4, len(words)), 1, -1):
        for start in range(len(words) - size + 1):
            part = words[start : start + size]
            if part[0].lower() in _PARTICLES or part[-1].lower() in _PARTICLES:
                continue
            spans.append(" ".join(part))
    return spans


def structural_pattern(local: str) -> str | None:
    """Patrón probable por la forma de la parte local, sin conocer a la persona."""
    tokens = [t for t in _SEPARATORS.split(local.lower()) if t]
    separator = next((c for c in local if c in "._-"), "")
    if any(not t.isalpha() for t in tokens):
        return None
    if len(tokens) == 3 and tokens[0] in FIRST_NAMES and len(tokens[1]) >= 2:
        return "first.last.last2" if separator == "." else None
    if len(tokens) == 2:
        a, b = tokens
        if a in FIRST_NAMES and len(b) >= 2 and b not in FIRST_NAMES:
            return {".": "first.last", "_": "first_last", "-": "first-last"}.get(separator)
        if a in FIRST_NAMES and len(b) == 1:
            return "first.l" if separator == "." else None
        if len(a) == 1 and len(b) >= 3:
            return "f.last" if separator == "." else None
        if b in FIRST_NAMES and a not in FIRST_NAMES and len(a) >= 3:
            return "last.first" if separator == "." else None
        return None
    if len(tokens) == 1:
        token = tokens[0]
        if token in FIRST_NAMES:
            return "first"
        for size in range(min(len(token) - 2, 12), 2, -1):
            head, tail = token[:size], token[size:]
            if head in FIRST_NAMES:
                if len(tail) >= 3:
                    return "firstlast"
                if len(tail) == 1:
                    return "firstl"
        if len(token) >= 5:
            # Una inicial seguida de un apellido (jperez) es el patrón de una palabra más común;
            # sin nombre cerca no se distingue de un apellido solo: evidencia débil.
            return "flast"
    return None


def observe(emails: list[FoundEmail]) -> list[Observation]:
    observations: list[Observation] = []
    for item in emails:
        if is_role_address(item.local):
            continue
        named = named_patterns(item)
        if named:
            # Si varias variantes coinciden, cuenta la primera (la más frecuente del catálogo).
            observations.append(Observation(named[0], "named", item.domain))
            continue
        pattern = structural_pattern(item.local)
        if pattern:
            observations.append(Observation(pattern, "structural", item.domain))
    return observations
