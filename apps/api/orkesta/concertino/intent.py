# ruff: noqa: E501  (listas de palabras: líneas largas a propósito)
"""Qué le piden a Concertino y con qué datos (IA propia, sin servicios externos).

Separa en cada frase la ACCIÓN (redactar, ajustar lo último que escribió, o dejar que el resto de
Concertino lo resuelva), la PIEZA a escribir (correo, secuencia, WhatsApp, LinkedIn, asuntos,
resumen ejecutivo, guion de llamada) y los DATOS que vienen dentro: cuántos correos, cargo,
industria, idioma, tono, objetivo y el producto de la ficha que se nombra.
Tolera faltas comunes, mayúsculas y la falta de tildes («crea la secuensia de 3 correos»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orkesta.engine.normalize.text import fold

PIECES = {
    "secuencia": "Secuencia de correos",
    "correo": "Correo",
    "whatsapp": "Mensaje de WhatsApp",
    "linkedin": "Mensaje de LinkedIn",
    "asuntos": "Asuntos de correo",
    "resumen": "Resumen ejecutivo",
    "guion": "Guion de llamada",
}

_VERB = (
    r"(crea|creame|crear|crees|redacta|redactame|redactar|escribe|escribeme|escribir|arma|armame|armar|"
    r"genera|generame|generar|hazme|haz|hacer|hagamos|prepara|preparame|preparar|dame|damelo|necesito|quiero|"
    r"ayudame (a|con)|propon|proponme|sugiere|sugiereme|elabora|elaborame|construye|monta|diseña|disena|"
    r"pasame|mandame|envia(me)?|me (das|haces|armas|escribes|redactas|creas|preparas))"
)
_PIECE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "secuencia",
        r"\b(secuencia|secuensia|secuancia|cadencia|seguidilla|serie de correos|varios correos|flujo de correos|campa(n|ñ)a de correos?|\d+ (correos|mails|emails|toques)|(dos|tres|cuatro|cinco|seis) (correos|mails|emails|toques))\b",
    ),
    ("whatsapp", r"\b(whatsapp|whats|wsp|wpp|wasap|guasap|mensaje de texto|sms)\b"),
    ("linkedin", r"\b(linkedin|linked in|inmail|in mail)\b"),
    ("asuntos", r"\b(asuntos?|subject|linea de asunto)\b"),
    (
        "resumen",
        r"\b(resumen ejecutivo|one ?pager|onepager|resumen de una pagina|brief ejecutivo|justificacion de negocio|business case)\b",
    ),
    (
        "guion",
        r"\b(guion|libreto|script|discurso|llamada en frio|cold call|pitch de llamada|que (le )?digo (en|por) (la )?llamada)\b",
    ),
    ("correo", r"\b(correo|correos|mail|mails|email|emails|e-mail|mensaje|carta|seguimiento)\b"),
)
_WORD_NUMBERS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
}
_COUNT = re.compile(
    r"\b(\d|un|uno|una|dos|tres|cuatro|cinco|seis) (correos?|mails?|emails?|toques|pasos|mensajes)\b"
)
_COUNT_OF = re.compile(
    r"\b(secuencia|cadencia|serie|campa(n|ñ)a) de (\d|dos|tres|cuatro|cinco|seis)\b"
)

_CARGO = re.compile(
    r"\b(ceo|cfo|cio|cto|cmo|coo|cdo|ciso|cro|cpo|chro|c-?level|"
    r"(gerente|director|directora|jefe|jefa|vp|vicepresidente|vicepresidenta|head|lider|responsable|encargado|encargada|coordinador|coordinadora|subgerente|presidente|presidenta|dueno|duena|fundador|fundadora|socio|socia)"
    r"( (general|comercial|de [a-z]+( [a-z]+)?|of [a-z]+|financier[oa]|tecnologic[oa]))?)\b"
)
_TAIL = {
    "al",
    "a",
    "del",
    "de",
    "para",
    "en",
    "que",
    "y",
    "con",
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "por",
    "sobre",
}
_INDUSTRY: tuple[tuple[str, str], ...] = (
    (
        "banca_finanzas",
        r"\b(banca|banco|bancos|bancari[oa]s?|financier[oa]s?|finanzas|fintech|cooperativas? de ahorro)\b",
    ),
    ("seguros", r"\b(seguros?|asegurador(a|as)?|aseguradoras)\b"),
    (
        "retail_consumo",
        r"\b(retail|consumo masivo|supermercados?|tiendas?|ecommerce|e-commerce|comercio)\b",
    ),
    (
        "telecomunicaciones",
        r"\b(telco|telcos|telecom|telecomunicaciones|operador(a|es)? (movil|de telefonia))\b",
    ),
    (
        "farmaceutica_salud",
        r"\b(salud|clinicas?|hospital(es)?|farma|farmaceutic[oa]s?|laboratorios?)\b",
    ),
    ("mineria_petroleo", r"\b(mineria|mineras?|petroleo|petroleras?|oil|gas)\b"),
    ("energia", r"\b(energia|electricas?|utilities|servicios publicos)\b"),
    (
        "manufactura_industrial",
        r"\b(manufactura|manufactureras?|industria(l|les)?|fabricas?|plantas? de produccion)\b",
    ),
    ("agroindustria", r"\b(agro|agroindustria|agricol[ao]s?|agricultura)\b"),
    ("transporte_logistica", r"\b(logistica|transporte|aerolineas?|navieras?)\b"),
    ("tecnologia_software", r"\b(software|saas|tecnologicas?|empresas? de tecnologia|startups?)\b"),
    ("alimentos_bebidas", r"\b(alimentos|bebidas|food)\b"),
    ("construccion_inmobiliario", r"\b(construccion|constructoras?|inmobiliari[oa]s?)\b"),
    ("automotriz", r"\b(automotriz|automotoras?|concesionari[oa]s?)\b"),
)
_FORMAL = re.compile(r"\b(formal|de usted|usted|serio)\b")
_CASUAL = re.compile(r"\b(cercano|informal|de tu|tutea|tuteando|amigable)\b")
_SHORT = re.compile(
    r"\b(corto|cortos|corta|cortas|breve|breves|directo|conciso|mas corto|resumido)\b"
)
_ENGLISH = re.compile(r"\b(en ingles|in english|ingles)\b")
_GOALS: tuple[tuple[str, str], ...] = (
    (
        "evento",
        r"\b(invitar|invitacion|invitarlo|invitarla|evento|congreso|conferencia|summit|feria|webinar)\b",
    ),
    ("demo", r"\b(demo|demostracion)\b"),
    ("propuesta", r"\b(propuesta|cotizacion)\b"),
    ("referido", r"\b(referido|persona correcta|quien decide)\b"),
)
# Ajustes sobre lo último que escribió Concertino («más corto», «ahora para un CFO»).
_TWEAK = re.compile(
    r"^(hazlo|hazla|hazlos|ponlo|ponla|que sea|mas|menos|ahora|y (si|para|en)|para (un|una)|cambia(le)?|en ingles|en espanol|otra (version|opcion)|otro)\b"
)


# Para qué es la pieza: no es lo mismo el primer contacto que un seguimiento o una invitación.
_PURPOSES: tuple[tuple[str, str], ...] = (
    (
        "seguimiento",
        r"\b(seguimiento|no (me )?(ha )?(respondio|respondido|contesto|contestado|responde|contesta)|sin respuesta|no respondio|recordatorio|insistir|volver a escribir(le)?)\b",
    ),
    (
        "agradecer",
        r"\b(despues de (la|una) (reunion|llamada|demo)|post ?reunion|gracias por (la|su) (reunion|tiempo|llamada)|agradecer|agradecimiento)\b",
    ),
    (
        "propuesta",
        r"\b(enviar(le)? (la )?propuesta|con la propuesta|adjunt(o|ar) (la )?propuesta|cotizacion)\b",
    ),
    (
        "reactivar",
        r"\b(reactivar|retomar (el )?contacto|cliente (antiguo|anterior|perdido)|hace (mucho )?tiempo|volver a contactar)\b",
    ),
    ("invitacion", r"\b(invitar(lo|la|le)?|invitacion|invitandol[oa]|invitarle)\b"),
)
_PERSON = re.compile(
    r"\b(?:para|a) ([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?: [A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)(?=,| de | del | que | en |$|\s*,)"
)
_COMPANY = re.compile(
    r"\b(?i:ceo|cfo|cio|cto|cmo|coo|cdo|ciso|gerente|director[a]?|jefe|jefa|vp|head|lider|presidente)"
    r"(?: [a-zé]+){0,3} (?:de|en) ([A-ZÁÉÍÓÚÑ][\w&.\-]*(?: [A-ZÁÉÍÓÚÑ][\w&.\-]*){0,3})"
)
_NOT_NAMES = {
    "Hola",
    "Crea",
    "Escribe",
    "Redacta",
    "Dame",
    "Hazme",
    "Necesito",
    "Quiero",
    "Un",
    "Una",
    "Ceo",
    "Cfo",
    "Cio",
    "Cto",
    "Cmo",
    "Gerente",
    "Director",
    "Directora",
    "Miami",
    "Chile",
    "México",
    "Mexico",
    "Colombia",
    "Perú",
    "Peru",
    "Argentina",
    "Bogotá",
    "Lima",
    "Santiago",
}


@dataclass
class Intent:
    action: str | None = None  # «redactar» o «ajustar»; None: lo resuelve el resto de Concertino
    piece: str | None = None
    count: int | None = None
    cargo: str = ""
    industry: str | None = None
    language: str = "es"
    tone: str | None = None  # formal | cercano | directo
    goal: str | None = None
    purpose: str | None = None  # seguimiento | invitacion | propuesta | agradecer | reactivar
    person: str = ""  # «para Juan Pérez»: el correo sale con su nombre
    target_company: str = ""  # «CFO de Bancolombia»
    place: str = ""  # «a Miami», «en Chile»
    extras: list[str] = field(default_factory=list)

    @property
    def piece_label(self) -> str:
        return PIECES.get(self.piece or "", "")


def _count(folded: str) -> int | None:
    m = _COUNT_OF.search(folded)
    raw = m.group(3) if m else None
    if raw is None:
        m2 = _COUNT.search(folded)
        raw = m2.group(1) if m2 else None
    if raw is None:
        return None
    n = int(raw) if raw.isdigit() else _WORD_NUMBERS.get(raw, 0)
    return max(1, min(6, n)) if n else None


def details(text: str) -> Intent:
    """Los datos que vienen en la frase, sin decidir todavía la acción."""
    folded = fold(text)
    it = Intent()
    for piece, pattern in _PIECE_PATTERNS:
        if re.search(pattern, folded):
            it.piece = piece
            break
    it.count = _count(folded)
    if it.piece in (None, "correo") and it.count and it.count > 1:
        it.piece = "secuencia"
    m = _CARGO.search(folded)
    if m:
        words = m.group(0).split()
        while len(words) > 1 and words[-1] in _TAIL:
            words.pop()
        it.cargo = " ".join(words)
    for code, pattern in _INDUSTRY:
        if re.search(pattern, folded):
            it.industry = code
            break
    it.language = "en" if _ENGLISH.search(folded) else "es"
    it.tone = (
        "directo"
        if _SHORT.search(folded)
        else "formal"
        if _FORMAL.search(folded)
        else "cercano"
        if _CASUAL.search(folded)
        else None
    )
    for code, pattern in _GOALS:
        if re.search(pattern, folded):
            it.goal = code
            break
    for code, pattern in _PURPOSES:
        if re.search(pattern, folded):
            it.purpose = code
            break
    m = _PERSON.search(text)
    if m and m.group(1).split()[0] not in _NOT_NAMES and not _CARGO.fullmatch(fold(m.group(1))):
        it.person = m.group(1)
    m = _COMPANY.search(text)
    if m and m.group(1).split()[0] not in _NOT_NAMES:
        it.target_company = m.group(1).strip(" .,")
    m = re.search(
        r"\b(?:a|en|para) (Miami|Chile|M[eé]xico|Colombia|Per[uú]|Argentina|Bogot[aá]|Lima|Santiago|Ciudad de M[eé]xico|Panam[aá]|Costa Rica|Ecuador|Guatemala|Estados Unidos)\b",
        text,
    )
    if m:
        it.place = m.group(1)
    return it


def understand(text: str, *, last_topic: str | None = None) -> Intent:
    """Acción + pieza + datos. «redactar» cuando piden escribir algo; «ajustar» cuando cambian lo
    último que Concertino redactó («más corto», «ahora para un CFO»)."""
    folded = fold(text).strip(" ?¿!¡.")
    it = details(text)
    verb = re.search(
        rf"^(por favor )?{_VERB}\b|\b{_VERB} (un|una|unos|unas|la|el|los|las|me|mi|\d|dos|tres|cuatro|cinco|seis)\b",
        folded,
    )
    asks_to_write = bool(verb) and it.piece is not None
    # «Correo para ofrecer una delegación a…» sin verbo también es un pedido de redacción.
    asks_to_write = asks_to_write or bool(
        re.match(
            r"^(un |una |el |la )?(correo|mail|email|e-mail|mensaje|secuencia|whatsapp|inmail)s? (para|de|a|sobre|ofreciendo|invitando)\b",
            folded,
        )
    )
    # «Secuencia de 3 correos para un CEO» sin verbo también es un pedido de redacción.
    bare = (
        it.piece in ("secuencia", "whatsapp", "linkedin", "asuntos", "resumen", "guion")
        and len(folded.split()) <= 12
        and "?" not in text
    )
    # Una pregunta sobre un correo («¿cuántos correos manda la campaña?») no es redactar.
    question = bool(
        re.match(
            r"^(cuantos|cuantas|cuando|por que|porque|como (va|le fue|van)|que tal|cual(es)? (es|son))\b",
            folded,
        )
    )
    # «¿Qué le digo en la llamada?» pide el guion aunque venga como pregunta.
    bare = bare or (it.piece == "guion" and bool(re.search(r"\bque (le )?digo\b", folded)))
    if (asks_to_write or bare) and not question:
        it.action = "redactar"
    elif (
        last_topic == "redactar"
        and len(folded.split()) <= 10
        and (
            _TWEAK.search(folded)
            or it.cargo
            or it.industry
            or it.tone
            or it.count
            or it.language == "en"
        )
    ):
        it.action = "ajustar"
    return it


def merge(previous: Intent, change: Intent) -> Intent:
    """Lo que pidió antes + el ajuste nuevo (el ajuste manda)."""
    return Intent(
        action="redactar",
        piece=change.piece if change.piece and change.piece != "correo" else previous.piece,
        count=change.count or previous.count,
        cargo=change.cargo or previous.cargo,
        industry=change.industry or previous.industry,
        language=change.language if change.language == "en" else previous.language,
        tone=change.tone or previous.tone,
        goal=change.goal or previous.goal,
        purpose=change.purpose or previous.purpose,
        person=change.person or previous.person,
        target_company=change.target_company or previous.target_company,
        place=change.place or previous.place,
    )
