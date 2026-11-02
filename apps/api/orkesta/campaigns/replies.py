"""Clasificador propio de respuestas a campañas (sin terceros).

Lee el texto de la respuesta (español, inglés o portugués) y decide qué quiso decir la persona,
para que ORKESTA actúe sola: crear la oportunidad en el CRM, agendar el seguimiento correcto,
dar de baja o ignorar un «fuera de oficina» y seguir con la secuencia.
"""

from __future__ import annotations

import re

from orkesta.engine.normalize.text import fold

CATEGORIES: dict[str, dict[str, str]] = {
    "reunion": {"label": "Quiere reunión", "tone": "ok"},
    "interesado": {"label": "Interesado", "tone": "ok"},
    "mas_info": {"label": "Pide información", "tone": "blue"},
    "referido": {"label": "Refirió a otra persona", "tone": "blue"},
    "no_ahora": {"label": "Más adelante", "tone": "warn"},
    "no_interesado": {"label": "No interesado", "tone": "mist"},
    "baja": {"label": "Pidió no recibir más", "tone": "bad"},
    "fuera_oficina": {"label": "Fuera de oficina", "tone": "mist"},
    "otro": {"label": "Respuesta", "tone": "mist"},
}
POSITIVE = ("reunion", "interesado", "mas_info", "referido")

# (categoría, peso, expresiones). Se comparan sobre el texto sin tildes y en minúsculas.
_RULES: list[tuple[str, int, tuple[str, ...]]] = [
    (
        "fuera_oficina",
        9,
        (
            r"fuera de (la )?oficina",
            r"out of (the )?office",
            r"estoy de vacaciones",
            r"me encuentro (de vacaciones|fuera|ausente)",
            r"respuesta automatica",
            r"auto.?reply",
            r"regreso el",
            r"regresare el",
            r"estare (fuera|ausente)",
            r"i am (currently )?away",
            r"i( m| am) out of",
            r"limited access to (my )?email",
            r"acceso limitado al correo",
            r"ausente hasta",
            r"fora do escritorio",
            r"em ferias",
        ),
    ),
    (
        "baja",
        9,
        (
            r"no me (escribas|contactes|envies|vuelvas a)",
            r"(eliminar|elimina|borra|saca|sacar|"
            r"quitar|quita)(me)? de (la|tu|su|sus) (lista|base)",
            r"darme de baja",
            r"dar(me)? de baja",
            r"\bunsubscribe\b",
            r"remove me",
            r"stop (emailing|contacting|sending)",
            r"no (deseo|quiero) recibir",
            r"no (sigan|siga|sigas) (escribiendo|enviando)",
            r"\bspam\b",
            r"do not (contact|email)",
            r"no enviar mas",
            r"descadastr",
        ),
    ),
    (
        "referido",
        7,
        (
            r"(no soy|no es) (la persona|el indicado|la indicada|quien)",
            r"te (paso|comparto) (el )?"
            r"contacto",
            r"(habla|hablar|hablale|escribe|escribele|escribirle|escribanle|contacta|contactale|"
            r"contactar) (con|a) (mi|nuestro|"
            r"nuestra|el|la)",
            r"(le|lo) (copio|incluyo|reenvio)",
            r"en copia",
            r"\bcc\b",
            r"(the )?right person",
            r"reach out to",
            r"i( ve| have) (cc|copied|looped)",
            r"(encargad[oa]|responsable) (de|del|es)",
            r"quien (ve|lleva|maneja) (eso|esto|ese tema)",
        ),
    ),
    (
        "reunion",
        8,
        (
            r"(agendar|agendemos|agenda|programar|programemos|coordinar|coordinemos|cuadrar|cuadremos)"
            r" (una |la )?(llamada|reunion|cita|videollamada|call|meet)",
            r"(una|la) (llamada|reunion)"
            r" (el|este|la proxima|manana)",
            r"(martes|miercoles|jueves|viernes|lunes) a las",
            r"\bque (dia|horario|hora) (te|le) (queda|sirve|funciona)",
            r"\bmi (calendario|agenda)\b",
            r"calendly",
            r"\bteams\b",
            r"google meet",
            r"\bzoom\b",
            r"(book|schedule|set up) a (call|"
            r"meeting|time)",
            r"let ?s (talk|meet|chat)",
            r"(hablemos|conversemos|charlemos)",
            r"me (puedes|podrias|puede|podria) llamar",
            r"\bllamame\b",
            r"marcar(me|le)",
            r"(estoy|estamos) disponibles? (el|este|la)",
            r"available (on|this|next)",
        ),
    ),
    (
        "mas_info",
        6,
        (
            r"(envia|enviame|enviar|manda|mandame|mandar|compart[ae]|comparteme)(nos|me)? (mas )?"
            r"(informacion|info|detalles|propuesta|cotizacion|brochure|presentacion|precios|tarifas)",
            r"(cuanto|que) (cuesta|vale|valor|precio)",
            r"\bprecios?\b",
            r"\bcotiza",
            r"\btarifas?\b",
            r"(send|share)( me| us)? (more )?(info|information|details|deck|pricing)",
            r"\bpricing\b",
            r"how much",
            r"(como|de que manera) funciona",
            r"\bcasos de exito\b",
            r"me (gustaria|interesaria) (saber|conocer) mas",
            r"(mas|more) (informacion|info|details)",
        ),
    ),
    (
        "interesado",
        5,
        (
            r"\bme interesa\b",
            r"nos interesa",
            r"(estoy|estamos|estaria|estariamos) interesad",
            r"(suena|me parece|parece) (bien|interesante|muy bien|genial)",
            r"\bclaro que si\b",
            r"\bcon gusto\b",
            r"\bperfecto\b",
            r"\bme encantaria\b",
            r"\bsounds (good|great|"
            r"interesting)\b",
            r"\b(i ?m|we ?re|we are|i am) interested\b",
            r"\bde acuerdo\b",
            r"\bsi,? (claro|por favor|me gustaria)\b",
            r"\bsure\b",
            r"\btenho interesse\b",
            r"(adelante|avancemos|sigamos)",
            r"\bvale la pena\b",
        ),
    ),
    (
        "no_ahora",
        6,
        (
            r"(mas|muy) adelante",
            r"(el|al) (proximo|otro) (ano|mes|trimestre|semestre)",
            r"(en|dentro de) (unos|un par de|algunos|\d+) (meses|semanas)",
            r"\bahora no\b",
            r"(no es|no seria) (el|un) (momento|buen momento)",
            r"(por el momento|por ahora) no",
            r"(next|later this) (year|quarter|month)",
            r"not (right )?now",
            r"bad timing",
            r"(reach|check) (back|in) (in|later)",
            r"(escribe|contacta|busca)(me|nos) (en|mas)",
            r"(presupuesto|budget) (cerrado|aprobado|ya esta)",
            r"(ya|aun no) (tenemos|hay) presupuesto",
            r"(retomemos|retomar|volvamos a hablar)",
            r"despues de (enero|febrero|marzo|abril|mayo|"
            r"junio|julio|agosto|septiembre|octubre|noviembre|diciembre)",
        ),
    ),
    (
        "no_interesado",
        7,
        (
            r"no (me|nos) interesa",
            r"no (estoy|estamos) interesad",
            r"\bno,? gracias\b",
            r"no (lo )?(necesito|necesitamos|requerimos|requiero)",
            r"(ya|actualmente) (tenemos|contamos"
            r"|trabajamos) (con|un|una)",
            r"not interested",
            r"no thanks?",
            r"we( re| are) (all )?set",
            r"we already (have|use|work)",
            r"no aplica",
            r"no es (para|de interes)",
            r"no gracias",
            r"nao (tenho|temos) interesse",
        ),
    ),
]
_COMPILED = [(cat, w, [re.compile(p) for p in pats]) for cat, w, pats in _RULES]

# Texto citado del correo original («El lun, ... escribió:», «On Mon ... wrote:», «> ...»).
_QUOTE = re.compile(
    r"(\bel (lun|mar|mie|jue|vie|sab|dom)[^\n]{0,80}escribio:|\bon (mon|tue|wed|thu|fri|sat|sun)"
    r"[^\n]{0,80}wrote:|-{2,} ?(mensaje|original|forwarded)|\nde: |\nfrom: |\n>)",
)


def clean(text: str) -> str:
    """Solo lo que escribió la persona: sin el correo citado debajo."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    cut = _QUOTE.search(plain)
    return fold(plain[: cut.start()] if cut else plain).strip()


def classify(text: str) -> tuple[str, int]:
    """(categoría, confianza 0-100) de una respuesta."""
    body = clean(text)
    if not body:
        return "otro", 0
    scores: dict[str, int] = {}
    for cat, weight, patterns in _COMPILED:
        hits = sum(1 for p in patterns if p.search(body))
        if hits:
            scores[cat] = weight * hits
    if not scores:
        return "otro", 30
    # Una negación pesa más que el interés: «no me interesa» contiene «me interesa».
    if "no_interesado" in scores:
        scores.pop("interesado", None)
    if "no_ahora" in scores and "interesado" in scores:
        scores["interesado"] -= 3
    if "baja" in scores:
        return "baja", 95
    if "fuera_oficina" in scores and scores["fuera_oficina"] >= max(scores.values()):
        return "fuera_oficina", 90
    scores.pop("fuera_oficina", None)
    if not scores:
        return "otro", 30
    best = max(scores, key=lambda c: scores[c])
    total = sum(v for v in scores.values() if v > 0) or 1
    return best, max(40, min(95, round(100 * scores[best] / total)))
