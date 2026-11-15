# ruff: noqa: E501
"""Concertino como entrenador de ventas: juego de roles con objeciones y escuela de ventas.

Entrenamiento: Concertino hace de cliente (con el cargo al que vende la empresa) y pone una
objeción real. La persona responde y Concertino la califica en cinco puntos que usan los mejores
vendedores (escuchar, preguntar, conectar con el dolor, aportar valor y proponer un siguiente
paso), le explica qué mejorar y le muestra cómo lo diría un vendedor top con su propio producto.
Cada práctica se guarda (privada de la persona) para mostrar cómo sube su puntaje.

Escuela de ventas: un menú de todos los temas y las metodologías SPIN y Challenger
aplicadas al producto de la empresa con los datos de su ficha. El resto de los temas (calificar,
seguimiento, cierre, negociación…) los responde el oficio de ventas de Concertino.
"""

from __future__ import annotations

import random
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.concertino import engine
from orkesta.concertino import playbook as pb
from orkesta.concertino.models import AdviceCase
from orkesta.engine.normalize.text import fold

TOPIC = "entreno"

_TRAIN = re.compile(
    r"\b(entrename|entrenarme|entrenamiento|practicar|practiquemos|practica conmigo|role ?play|"
    r"juego de roles|simula(r|me)? (un|una|ser) (cliente|prospecto)|hazte pasar|otra objecion|"
    r"ponme a prueba|evaluame)\b"
)
_SCHOOL = re.compile(
    r"\b(escuela de ventas|metodologias? de ventas?|spin selling|\bspin\b|challenger|"
    r"ensename a vender|que metodologia)\b"
)
_EXIT = re.compile(r"^(salir|terminar|basta|listo|ya no|fin)\b.{0,30}(entren|practic)?")

# Lo que dice un cliente de verdad con cada objeción (Concertino lo dice en primera persona).
CLIENT_LINES: dict[str, tuple[str, ...]] = {
    "precio": ("Me parece interesante, pero está muy caro para nosotros.",
               "La verdad, no tenemos presupuesto para esto este año."),
    "prioridad": ("Ahora mismo no es prioridad, escríbeme en unos meses.",
                  "Estamos con el cierre del año, esto lo vemos después."),
    "proveedor": ("Ya tenemos un proveedor para eso y estamos contentos.",
                  "Eso ya lo resolvimos con otra empresa."),
    "persona": ("Yo no soy quien decide eso, habla con otra área.",
                "Eso no lo manejo yo."),
    "info": ("Mándame información por correo y la reviso.",
             "Envíame una presentación y te aviso."),
    "riesgo": ("Me da miedo cambiar algo que funciona; implementar esto es un riesgo.",
               "¿Y si no funciona? No quiero meter a mi equipo en un proyecto que falle."),
    "confianza": ("No los conozco. ¿Quién más trabaja con ustedes?",
                  "Nunca había oído de su empresa."),
    "competencia": ("Estoy evaluando a otros dos proveedores que son más baratos.",
                    "La competencia me ofreció algo parecido por menos."),
    "comite": ("Esto lo tiene que aprobar el comité, no depende de mí.",
               "Lo tengo que validar con mi jefe y con finanzas."),
    "silencio": ("(El cliente no responde desde hace dos semanas después de tu propuesta.)",),
    "no_interes": ("No me interesa, gracias.",
                   "No lo necesitamos."),
    "interno": ("Eso lo hacemos internamente con nuestro equipo.",
                "Tenemos gente que ya se encarga de eso."),
    "contrato": ("Tenemos contrato con otro proveedor hasta el próximo año.",),
    "mala_experiencia": ("Ya probamos algo así y nos fue mal.",
                         "Quedamos quemados con otra empresa que prometía lo mismo."),
}  # fmt: skip

# Los cinco puntos que se califican: (código, nombre, expresión que lo cumple, consejo si falta).
_CRITERIA: list[tuple[str, str, str, str]] = [
    ("escucha", "Escuchaste y validaste",
     r"\b(entiendo|comprendo|tiene (todo el )?sentido|es (muy )?normal|es (muy )?valid[oa]|gracias por|valoro|"
     r"te entiendo|le entiendo|tienes razon|tiene razon|me imagino|claro que|es logico|muchos (clientes|de nuestros))\b",
     "Empieza reconociendo lo que dijo («Tiene sentido», «Gracias por decírmelo») antes de responder: baja la defensa."),
    ("pregunta", "Hiciste una pregunta para entender",
     r"\?|\b(que|como|cuanto|cual|cuales|por que|quien|cuando) (te|le|les|es|seria|harian|hacen|pesa|preocupa|significa|cuesta)\b",
     "Pregunta antes de convencer: «¿Qué es lo que más te pesa: el monto o el momento?». Una objeción casi nunca es lo que parece."),
    ("dolor", "Conectaste con su problema",
     r"\b(cuesta|costo|pierde|pierden|perdiendo|tiempo|riesgo|problema|dolor|seguir (con|asi)|hoy (les|te|le)|"
     r"mientras tanto|cada mes|oportunidades|frustra|dificil)\b",
     "Llévalo al costo de no hacer nada: cuánto le cuesta seguir con el problema cada mes."),
    ("valor", "Aportaste valor con un dato o caso",
     r"\d|\b(resultado|clientes?|caso|ejemplo|logr|ahorr|aument|reduc|triplic|duplic|empresas como|sector|retorno|roi)\w*",
     "Respalda con algo concreto: un resultado con número o un cliente parecido que ya lo logró."),
    ("paso", "Propusiste un siguiente paso concreto",
     r"\b(reunion|llamada|demo|agend\w*|\d+ minutos|lunes|martes|miercoles|jueves|viernes|esta semana|proxima semana|"
     r"te parece (si|bien)|le parece (si|bien)|revisemos|conversemos|veamos|propongo|mañana|manana)\b",
     "Cierra siempre con un paso pequeño y concreto: «¿Te parece si lo revisamos 20 minutos el jueves?»."),
]  # fmt: skip
_COMPILED = [(c, n, re.compile(p), t) for c, n, p, t in _CRITERIA]
_DISCOUNT = re.compile(r"\b(descuento|rebaja|bajar el precio|precio especial)\b")


def wants_training(question: str) -> bool:
    return bool(_TRAIN.search(fold(question).lower()))


def wants_school(question: str) -> bool:
    return bool(_SCHOOL.search(fold(question).lower()))


def wants_exit(question: str) -> bool:
    return bool(_EXIT.search(fold(question).lower().strip()))


def _persona(ctx: engine.Context) -> str:
    publico = engine.slots(ctx).get("publico", "")
    m = re.search(r"\b(gerente|director|directora|jefe|jefa|vp|vicepresidente|ceo|cfo|cio|cto|coo|due[nñ]o)[^,.;]{0,40}",
                  publico, re.I)  # fmt: skip
    role = m.group(0).strip() if m else "gerente de una empresa mediana"
    return role[0].lower() + role[1:]


def _base(ctx: engine.Context, summary: str, **extra: Any) -> dict[str, Any]:
    return {"case_id": None, "topic": TOPIC, "topic_label": "Entrenamiento", "objection": None,
            "objection_label": None, "formal": ctx.formal, "summary": summary, "options": [],
            "tips": [], "sections": [], "next_step": "", "missing": [], "sources": [],
            "suggestions": [], **extra}  # fmt: skip


def stats(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[float, int, float | None]:
    """(promedio de las últimas 10 prácticas, total de prácticas, promedio de las 10 anteriores)."""
    scores = [int((c or {}).get("score", 0)) for c in session.scalars(
        select(AdviceCase.context).where(AdviceCase.tenant_id == tenant_id, AdviceCase.user_id == user_id,
                                         AdviceCase.topic == TOPIC).order_by(AdviceCase.created_at.desc()).limit(20)
    )]  # fmt: skip
    if not scores:
        return 0.0, 0, None
    total = session.query(AdviceCase).filter(AdviceCase.tenant_id == tenant_id, AdviceCase.user_id == user_id,
                                             AdviceCase.topic == TOPIC).count()  # fmt: skip
    recent = scores[:10]
    before = scores[10:20]
    return (round(sum(recent) / len(recent), 1), total,
            round(sum(before) / len(before), 1) if before else None)  # fmt: skip


def start(
    session: Session,
    ctx: engine.Context,
    name: str,
    *,
    avoid: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """Concertino hace de cliente y pone una objeción para practicar."""
    rng = random.Random()  # noqa: S311  (variedad de práctica, no criptografía)
    if code not in CLIENT_LINES:
        codes = [c for c in CLIENT_LINES if c != avoid]
        # Primero las objeciones que la empresa dice escuchar más.
        own = [str((f.meta or {}).get("tipo")) for f in ctx.facts.get("objecion") or []]
        own = [c for c in own if c in codes]
        code = rng.choice(own) if own and rng.random() < 0.6 else rng.choice(codes)
    code = str(code)
    line = rng.choice(CLIENT_LINES[code])
    avg, n, _ = stats(session, ctx.tenant_id, ctx.user_id)
    progress = f" Llevas {n} prácticas y tu promedio reciente es {avg}/10." if n else ""
    who = _persona(ctx)
    summary = (f"Practiquemos, {name}. Yo soy {who} y tú me estás vendiendo.{progress}\n\n"
               f"«{line}»\n\n¿Qué me respondes? Escríbelo como se lo dirías al cliente.")  # fmt: skip
    obj = pb.OBJECTION_BY_CODE[code]
    return _base(ctx, summary, objection=code, objection_label=obj.label,
                 practice={"code": code, "label": obj.label, "line": line},
                 suggestions=["Dame una pista", "Otra objeción", "Salir del entrenamiento"])  # fmt: skip


def hint(ctx: engine.Context, code: str) -> dict[str, Any]:
    obj = pb.OBJECTION_BY_CODE[code]
    summary = (f"Pista: {obj.real}\n\nUna buena respuesta tiene cinco partes: valida lo que dijo, "
               "pregunta para entender, conéctalo con su problema, aporta un dato o caso y propone "
               "un paso pequeño. Inténtalo, te califico.")  # fmt: skip
    return _base(ctx, summary, objection=code, objection_label=obj.label,
                 practice={"code": code, "label": obj.label, "line": ""},
                 suggestions=["Otra objeción", "Salir del entrenamiento"])  # fmt: skip


def grade(
    session: Session, ctx: engine.Context, code: str, answer: str, name: str
) -> dict[str, Any]:
    """Califica la respuesta en cinco puntos (0 a 10) y muestra la versión de un vendedor top."""
    obj = pb.OBJECTION_BY_CODE[code]
    text = fold(answer).lower()
    words = len(text.split())
    values = engine.slots(ctx)
    facts_terms = {w for k in ("problema", "valor", "prueba", "diferencial") for w in
                   fold(values.get(k, "")).lower().split() if len(w) > 4}  # fmt: skip
    criteria: list[dict[str, Any]] = []
    for c, label, pattern, tip in _COMPILED:
        hit = bool(pattern.search(text))
        if c in ("dolor", "valor") and not hit and facts_terms & set(text.split()):
            hit = True
        points = 2 if hit else 0
        if hit and words < 12 and c in ("dolor", "valor"):
            points = 1  # mencionado, pero sin desarrollar
        criteria.append({"code": c, "name": label, "points": points, "max": 2,
                         "tip": "" if points == 2 else tip})  # fmt: skip
    total = sum(c["points"] for c in criteria)
    warnings = []
    if code == "precio" and _DISCOUNT.search(text):
        total = max(0, total - 2)
        warnings.append("Ofreciste descuento de entrada: eso le enseña al cliente que el precio era inflado. "
                        "Primero lleva la conversación al valor; el descuento, si llega, al final y a cambio de algo.")  # fmt: skip
    if words < 8:
        total = min(total, 4)
        warnings.append(
            "Tu respuesta es muy corta: el cliente necesita sentir que lo escuchaste y que tienes un plan."
        )
    if words > 140:
        warnings.append("Es larga para un correo o una llamada: apunta a 4 o 5 frases.")
    model = engine.advise_objection(session, ctx, code)
    top = model["options"][0]["text"] if model["options"] else ""
    top = re.sub(r"^Hola[^\n]{0,40}:\s*", "", top)  # en la práctica se responde en la conversación
    session.add(AdviceCase(tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic=TOPIC, objection=code,
                           question=answer[:4000], context={"score": total,
                           "criteria": {c["code"]: c["points"] for c in criteria}}))  # fmt: skip
    session.flush()
    avg, n, before = stats(session, ctx.tenant_id, ctx.user_id)
    verdict = ("¡Excelente! Así responde un vendedor top." if total >= 9 else
               "Muy bien, vas por buen camino." if total >= 7 else
               "Buen intento: con dos ajustes queda muy sólida." if total >= 4 else
               "Vamos a armarla mejor juntos.")  # fmt: skip
    trend = ""
    if before is not None:
        diff = round(avg - before, 1)
        trend = f" Subiste {diff} puntos frente a tus prácticas anteriores." if diff > 0 else ""
    good = [str(c["name"]) for c in criteria if c["points"] == 2]
    improve = [f"{c['name']}: {c['tip']}" for c in criteria if c["points"] < 2]
    sections = []
    if good:
        sections.append({"title": "Lo que hiciste bien", "items": good})
    if improve or warnings:
        sections.append({"title": "Para subir tu puntaje", "items": [*improve, *warnings]})
    sections.append({"title": "Detrás de esta objeción", "items": [obj.real]})
    options = ([{"code": "top", "name": "Así lo diría un vendedor top",
                 "why": "Valida, pregunta, conecta con el problema, aporta un dato y propone un paso.",
                 "text": top, "record": None}] if top else [])  # fmt: skip
    return _base(
        ctx, f"{verdict} Tu puntaje: {total}/10.{trend}", objection=code, objection_label=obj.label,
        sections=sections, options=options, missing=model.get("missing", []),
        score={"total": total, "criteria": criteria, "average": avg, "count": n},
        practice={"code": code, "label": obj.label, "line": "", "done": True},
        suggestions=["Otra objeción", "Repetir esta objeción", "Escuela de ventas", "Salir del entrenamiento"],
    )  # fmt: skip


# --- Escuela de ventas -----------------------------------------------------------------------

LESSONS: dict[str, dict[str, Any]] = {
    "spin": {
        "name": "SPIN: preguntas que descubren la necesidad",
        "when": "En la primera reunión, antes de presentar tu producto.",
        "idea": "El cliente se convence solo cuando él mismo dice en voz alta cuánto le cuesta su problema. Tú solo haces las preguntas en orden.",
        "steps": [
            "Situación: entiende cómo trabajan hoy (pocas preguntas, investígalas antes).",
            "Problema: descubre qué les cuesta trabajo o no funciona.",
            "Implicación: haz que dimensione el impacto: tiempo, dinero, riesgo, personas.",
            "Necesidad-beneficio: que diga él qué ganaría si se resolviera.",
        ],
        "applied": [
            "Situación: «¿Cómo manejan hoy {publico_corto}?»",
            "Problema: «¿Qué tan seguido les pasa que {problema}?»",
            "Implicación: «¿Cuánto les cuesta al mes seguir con eso, en tiempo o en ventas?»",
            "Necesidad-beneficio: «Si lograran {valor}, ¿qué cambiaría para tu equipo?»",
        ],
    },
    "challenger": {
        "name": "Challenger: enseñar algo que el cliente no sabía",
        "when": "Con clientes que dicen «estamos bien así» o con decisores de alto nivel.",
        "idea": "Los vendedores que más venden no preguntan qué necesita el cliente: le enseñan una idea nueva sobre su negocio, la adaptan a su caso y toman el control de la conversación con respeto.",
        "steps": [
            "Enseñar: comparte un dato o tendencia que cambie cómo ve su problema.",
            "Adaptar: conéctalo con lo que le importa a ese cargo y a ese sector.",
            "Tomar el control: propone el siguiente paso con claridad, sin miedo a hablar de dinero.",
        ],
        "applied": [
            "Enseñar: «Muchas empresas creen que el problema es X; en realidad lo que más pesa es {problema}».",
            "Adaptar: «Para tu área esto significa {valor}».",
            "Tomar el control: «Te propongo revisar 20 minutos cómo se vería en tu caso; ¿martes o jueves?»",
        ],
    },
}  # fmt: skip
_LESSON_KEYS = {"spin": r"\bspin\b", "challenger": r"challenger"}


def school(ctx: engine.Context, question: str) -> dict[str, Any]:
    out = _school(ctx, question)
    out["topic_label"] = "Escuela de ventas"
    return out


def _school(ctx: engine.Context, question: str) -> dict[str, Any]:
    text = fold(question).lower()
    key = next((k for k, p in _LESSON_KEYS.items() if re.search(p, text)), None)
    if key is None:
        from orkesta.concertino import oficio

        items = [f"{lesson['name']} — {lesson['when']}" for lesson in LESSONS.values()]
        items += [lesson.title for lesson in oficio.LESSONS]
        return _base(ctx, "Bienvenida a la escuela de ventas de Concertino. Elige un tema y te lo explico "
                     "aplicado a tu producto; después lo practicamos con un cliente simulado.",
                     sections=[{"title": "Temas", "items": items}],
                     suggestions=["Enséñame SPIN", "Enséñame Challenger", "¿Cómo califico una oportunidad?",
                                  "¿Cómo escribo un correo en frío?", "¿Cómo cierro el negocio?",
                                  "Entréname con una objeción"])  # fmt: skip
    lesson = LESSONS[key]
    values = engine.slots(ctx)
    values.setdefault("publico_corto", "este proceso")
    applied = []
    for line in lesson["applied"]:
        line = str(line)
        names = re.findall(r"\{([a-z_]+)\}", line)
        if all(values.get(n) for n in names):
            applied.append(
                pb.voice(
                    re.sub(r"\{([a-z_]+)\}", lambda m: values[str(m.group(1))], line), ctx.formal
                )
            )
    sections = [{"title": "Cómo se hace", "items": lesson["steps"]}]
    if applied:
        sections.append({"title": "Con tu producto", "items": applied})
    missing = (
        [] if applied else ["completa tu producto (problema y resultado) para aplicarla a tu caso"]
    )
    others = ["Enséñame Challenger" if key == "spin" else "Enséñame SPIN", "Escuela de ventas"]
    return _base(ctx, f"{lesson['name']}. {lesson['idea']}\n\nCuándo usarla: {lesson['when']}",
                 sections=sections, missing=missing,
                 suggestions=["Entréname con una objeción", *others])  # fmt: skip


_ANOTHER = re.compile(r"\b(otra objecion|otra|siguiente|dame otra)\b")
_REPEAT = re.compile(r"\b(repetir|repite|de nuevo|otra vez|intentar de nuevo)\b")
_HINT = re.compile(r"\b(pista|ayuda|no se que (decir|responder)|como respondo)\b")


def handle(
    session: Session, ctx: engine.Context, practicing: str, question: str, name: str
) -> dict[str, Any] | None:
    """Respuesta dentro del entrenamiento. ``practicing`` es la objeción en juego; con «fin:»
    adelante la práctica ya se calificó y solo se atienden los botones (otra, repetir, salir)."""
    done = practicing.startswith("fin:")
    code = practicing.removeprefix("fin:")
    if code not in CLIENT_LINES:
        return None
    text = fold(question).lower().strip()
    if wants_exit(text):
        avg, n, _ = stats(session, ctx.tenant_id, ctx.user_id)
        summary = (f"Listo, cerramos el entrenamiento. Llevas {n} prácticas y tu promedio reciente es "
                   f"{avg}/10. Cuando quieras seguir, dime «Entréname»." if n else
                   "Listo, cerramos el entrenamiento. Cuando quieras practicar, dime «Entréname».")  # fmt: skip
        return _base(
            ctx, summary, suggestions=["Escuela de ventas", "Crea una secuencia de 3 correos"]
        )
    if _REPEAT.search(text) and len(text.split()) <= 5:
        return start(session, ctx, name, code=code)
    if _ANOTHER.search(text) and len(text.split()) <= 5:
        return start(session, ctx, name, avoid=code)
    if done:
        return None
    if _HINT.search(text) and len(text.split()) <= 6:
        return hint(ctx, code)
    return grade(session, ctx, code, question, name)
