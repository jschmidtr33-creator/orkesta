# ruff: noqa: E501  (textos para el vendedor: líneas largas a propósito)
"""LinkedIn con lo que Concertino sabe de ventas y lo que aprendió de la ficha del producto:
posts (resultado primero, retos del público y opinión) y la cadencia de mensajes (invitación,
al aceptar, valor y reunión). Nada inventado: si la ficha no tiene un caso, no hay caso."""

from __future__ import annotations

import re
from typing import Any

from orkesta.concertino import engine
from orkesta.concertino import playbook as pb
from orkesta.copywriter.knowledge import INDUSTRY_CONTEXT, PERSONAS, WHO

INVITE_LIMIT = 300  # LinkedIn corta la nota de invitación en 300 caracteres

_TAGS = {
    "direccion": "Liderazgo", "tecnologia": "Tecnología", "seguridad": "Ciberseguridad",
    "datos": "Datos", "transformacion_digital": "TransformaciónDigital", "finanzas": "Finanzas",
    "compras": "Compras", "supply_chain": "Logística", "comercial": "Ventas",
    "marketing": "Marketing", "rrhh": "TalentoHumano", "legal": "Legal",
    "operaciones": "Operaciones", "atencion_cliente": "ExperienciaDelCliente",
}  # fmt: skip


def _cut(text: str, limit: int) -> str:
    """Una idea completa y corta: hasta la primera pausa natural, sin puntos suspensivos."""
    text = text.strip().rstrip(".")
    if len(text) <= limit:
        return text
    for mark in (". ", "; ", ": ", ", ", " y ", " con ", " para "):
        head = text.split(mark, 1)[0]
        if 15 <= len(head) <= limit:
            return head
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _low(text: str) -> str:
    """Primera letra en minúscula, salvo siglas o nombres («CRM», «Procaps»)."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _audience(ctx: engine.Context, meta: dict[str, Any]) -> tuple[str | None, str]:
    area = meta.get("area") or ctx.area
    peers = WHO[area]["es"] if area in WHO else ""
    if not peers:
        publico = engine.slots(ctx).get("publico", "")
        if meta["where"]:
            peers = "líderes" + meta["where"]
        elif publico and len(publico) <= 70:
            peers = _low(publico.rstrip("."))
        else:
            peers = "líderes de empresa"
    return area, peers


_TOPIC_TAGS = (
    (r"logistic|ultima milla|entregas|transporte|cadena de suministro", "Logística"),
    (r"retail|minorista|tiendas|ecommerce|e-commerce", "Retail"),
    (r"venta|comercial|prospecc|pipeline|crm", "Ventas"),
    (r"marketing|marca|campan", "Marketing"),
    (r"tecnolog|software|saas|nube|cloud|digital", "Tecnología"),
    (r"salud|clinica|hospital", "Salud"),
    (r"banca|banco|fintech|credito", "Banca"),
    (r"seguro", "Seguros"),
    (r"educa|universidad|colegio", "Educación"),
    (r"manufactur|planta|industria", "Manufactura"),
    (r"energ|petrol|mineria", "Energía"),
    (r"talento|rrhh|recursos humanos|nomina", "TalentoHumano"),
    (r"evento|congreso|feria", "Eventos"),
)


def _hashtags(area: str | None, industry: str | None, text: str) -> str:
    from orkesta.engine.normalize.text import fold

    tags: list[str] = []
    if area in _TAGS:
        tags.append(_TAGS[area])
    if industry and industry in INDUSTRY_CONTEXT:
        label = INDUSTRY_CONTEXT[industry]["es"][0]
        word = re.sub(r"^(el|la|los|las) (sector )?", "", label).split(" y ")[0]
        tags.append("".join(w.capitalize() for w in word.split()))
    folded = fold(text)
    tags += [tag for pattern, tag in _TOPIC_TAGS if re.search(pattern, folded)]
    tags.append("B2B")
    seen: list[str] = []
    for t in tags:
        if t and fold(t) not in {fold(x) for x in seen}:
            seen.append(t)
    return " ".join(f"#{t}" for t in seen[:3])


def posts(ctx: engine.Context, b: Any, meta: dict[str, Any]) -> dict[str, Any]:
    v = engine.slots(ctx)
    area, peers = _audience(ctx, meta)
    pains = list(PERSONAS[area]["es"]["pains"]) if area in PERSONAS else []
    problema = _cut(v.get("problema") or (pains[0] if pains else ""), 110)
    valor = _cut(v.get("valor", ""), 120)
    prueba = _cut(v.get("prueba", ""), 140)
    diferencial = _cut(v.get("diferencial", ""), 120)
    product = meta["product"]
    company = b.company or ""
    tags = _hashtags(
        area, ctx.industry, " ".join((product, v.get("publico", ""), v.get("valor", ""), problema))
    )
    options: list[dict[str, Any]] = []

    if prueba:
        lines = [f"{_cap(prueba)}.", "",
                 f"No fue suerte. Fue resolver algo que muchos {peers} tienen pendiente: {_low(problema)}." if problema else "No fue suerte.",
                 "", "Lo que cambió:"]  # fmt: skip
        lines += [f"→ {_cap(x)}" for x in (valor, diferencial) if x]
        lines += ["", "La lección: los resultados llegan cuando se ataca la causa, no el síntoma.",
                  "", "¿Cómo lo están resolviendo en tu equipo? Te leo en los comentarios.", "", tags]  # fmt: skip
        options.append({"name": "Resultado primero",
                        "why": "Abre con el caso de la ficha: un número concreto detiene el scroll mejor que cualquier frase.",
                        "text": "\n".join(lines)})  # fmt: skip

    sheet = [_cut(engine._clean(f.title), 110) for f in (ctx.facts.get("problema") or [])]
    retos: list[str] = []
    for r in [*sheet, problema, *(_cut(p, 110) for p in pains)]:
        if r and r not in retos:
            retos.append(r)
    retos = retos[:3]
    if len(retos) >= 2:
        lines = [f"Los {len(retos)} retos que más escucho de {peers} este año:", ""]
        lines += [f"{i}. {_cap(r)}." for i, r in enumerate(retos, start=1)]
        lines += [
            "",
            "Ninguno se resuelve con más horas de trabajo. Se resuelven con mejor proceso y mejores datos.",
        ]
        if valor:
            pitch = f"Con {product} ayudamos a resolver el primero: {_low(valor)}."
            lines += ["", f"En {company} lo vemos todos los días. {pitch}" if company else pitch]
        lines += ["", "¿Cuál de estos te quita más tiempo hoy?", "", tags]
        options.append({"name": "Retos de tu cliente",
                        "why": "Educa antes de vender: el público se reconoce en la lista y comenta. El producto aparece una sola vez, al final.",
                        "text": "\n".join(lines)})  # fmt: skip

    if problema:
        lines = [f"Opinión impopular: cuando hablamos de {_low(problema)}, lo que falla no es el esfuerzo. Es el método.", "",
                 f"Los {peers} que lo resolvieron no trabajan más horas que el resto."]  # fmt: skip
        if diferencial or valor:
            lines.append(f"Hicieron algo distinto: {_low(diferencial or valor)}.")
        if prueba and len(options) < 1:
            lines += ["", f"Un ejemplo: {prueba}."]
        lines += ["", "Cambiar el método da más miedo que trabajar el doble. Pero es lo único que escala.",
                  "", "¿Estás de acuerdo o lo ves distinto?", "", tags]  # fmt: skip
        options.append({"name": "Opinión que invita a comentar",
                        "why": "Una postura clara genera conversación; termina con una pregunta abierta para que el algoritmo lo muestre a más personas.",
                        "text": "\n".join(lines)})  # fmt: skip

    if not options:
        return {"summary": "Para escribir posts necesito saber qué problema resuelve tu producto o un caso de éxito. Agrégalos en la página Producto.",
                "options": [], "tips": [], "missing": ["problema", "prueba"]}  # fmt: skip
    out = [{"code": f"post:{i}", "record": None, **o} for i, o in enumerate(options)]
    tips = [
        "Las dos primeras líneas deciden si alguien abre «ver más»: empieza con un dato o una postura.",
        "No pongas enlaces en el post (LinkedIn los muestra menos): déjalo en el primer comentario.",
        "Publica de martes a jueves, entre 7 y 9 a. m., y responde cada comentario en la primera hora.",
        "Regla 4 a 1: cuatro posts que enseñan por cada uno que habla del producto.",
    ]
    missing = [k for k in ("prueba", "diferencial") if not v.get(k)]
    return {"summary": f"Te propongo {len(out)} posts de LinkedIn para {peers}, con lo que sé de {product}.",
            "options": out, "tips": tips, "missing": missing}  # fmt: skip


def messages(ctx: engine.Context, b: Any, meta: dict[str, Any]) -> dict[str, Any]:
    v = engine.slots(ctx)
    formal = meta["formal"]
    area, peers = _audience(ctx, meta)
    pains = list(PERSONAS[area]["es"]["pains"]) if area in PERSONAS else []
    problema = _cut(v.get("problema") or (pains[0] if pains else ""), 90)
    valor = _cut(v.get("valor", ""), 140)
    prueba = _cut(v.get("prueba", ""), 160)
    product = meta["product"]

    topic = f" sobre {_low(problema)}" if problema else ""
    invite = f"Hola {{{{nombre}}}}, vi [tu|su] perfil y el trabajo de {{{{empresa|[tu|su] equipo}}}}. Converso seguido con {peers}{topic}. Me gustaría conectar y compartir ideas."
    if len(pb.voice(invite, formal)) > INVITE_LIMIT:
        invite = f"Hola {{{{nombre}}}}, converso seguido con {peers}{topic}. Me gustaría conectar y compartir ideas."
    accepted = (f"Gracias por conectar, {{{{nombre}}}}. Una curiosidad: ¿cómo están resolviendo hoy {_low(problema)} en {{{{empresa|[tu|su] equipo}}}}? "
                f"Lo pregunto porque es el tema que más escucho de {peers}.") if problema else (
                "Gracias por conectar, {{nombre}}. ¿Qué es lo que más [te|le] ocupa este trimestre en {{empresa|[tu|su] equipo}}?")  # fmt: skip
    if prueba:
        value = f"{{{{nombre}}}}, comparto un caso que puede servir[te|le]. {_cap(prueba)}. Si [te|le] interesa, [te|le] cuento en pocas líneas cómo se logró."
    elif valor:
        value = f"{{{{nombre}}}}, comparto algo que puede servir[te|le]: con {product}, {_low(valor)}. Si [te|le] interesa, [te|le] cuento cómo funciona."
    else:
        value = f"{{{{nombre}}}}, [te|le] comparto en dos líneas qué hacemos con {product}, por si es útil para {{{{empresa|[tu|su] equipo}}}}."
    meeting = (f"¿[Te|Le] parece si lo conversamos 15 minutos esta semana? [Te|Le] muestro cómo funcionaría {product} en {{{{empresa|[tu|su] equipo}}}}. "
               "Si no es el momento, [dímelo|dígamelo] y no insisto.")  # fmt: skip
    steps = (
        ("Invitación a conectar", "Sin vender: una razón para conectar en menos de 300 caracteres (el límite de LinkedIn).", invite),
        ("Al aceptar (día 1)", "Una pregunta sobre su realidad, sin producto: abre la conversación y te dice si hay dolor.", accepted),
        ("Aporta valor (día 3 o 4)", "Un resultado concreto de la ficha. Da algo antes de pedir algo.", value),
        ("Propón la reunión (día 7)", "Pedido claro, de bajo compromiso, y una salida elegante que baja la presión.", meeting),
    )  # fmt: skip
    options = [{"code": f"linkedin:{i}", "name": n, "why": w, "text": pb.voice(t, formal), "record": None}
               for i, (n, w, t) in enumerate(steps)]  # fmt: skip
    tips = [
        "Antes de invitar, visita su perfil y reacciona a una publicación suya: la tasa de aceptación sube.",
        "Máximo 20 a 25 invitaciones al día para cuidar la cuenta.",
        "Si responde, responde rápido y lleva la conversación a una llamada; si no, tras el mensaje 4 pásalo a correo.",
    ]
    return {"summary": f"Te propongo la cadencia de LinkedIn para {meta['who']}{meta['where']}: 4 mensajes, del primer contacto a la reunión.",
            "options": options, "tips": tips}  # fmt: skip
