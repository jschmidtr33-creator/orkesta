# ruff: noqa: E501  (textos de venta: líneas largas a propósito)
"""Concertino redacta: correos y secuencias (con el redactor propio de ORKESTA), mensajes de
WhatsApp y LinkedIn, asuntos, resumen ejecutivo y guion de llamada.

Todo sale de la ficha del producto de la empresa y del oficio de venta: nunca inventa precios,
clientes ni cifras. Nada se envía ni se crea solo: el texto queda para copiar, y la campaña se crea
únicamente si la persona toca «Crear campaña con esta secuencia».
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from orkesta.campaigns.templating import html_to_text
from orkesta.concertino import engine
from orkesta.concertino import playbook as pb
from orkesta.concertino.intent import PIECES, Intent
from orkesta.concertino.models import ProductFact
from orkesta.copywriter import engine as copy
from orkesta.copywriter.knowledge import INDUSTRY_CONTEXT, PERSONAS
from orkesta.engine.normalize.text import fold

_EVENT = re.compile(
    r"\b(congreso|congresos|evento|eventos|conferencia|summit|feria|webinar|foro)\b"
)


def _clean(text: str) -> str:
    return engine._clean(text or "")


def _sentence(text: str) -> str:
    text = " ".join((text or "").split()).strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else f"{text}."


_WRITING_WORDS = frozenset(
    [
        "correo",
        "correos",
        "mail",
        "mails",
        "email",
        "emails",
        "secuencia",
        "secuencias",
        "mensaje",
        "mensajes",
        "whatsapp",
        "linkedin",
        "crea",
        "crear",
        "creame",
        "redacta",
        "redactame",
        "escribe",
        "escribeme",
        "arma",
        "armame",
        "hazme",
        "hazlo",
        "hazla",
        "genera",
        "dame",
        "necesito",
        "quiero",
        "resumen",
        "ejecutivo",
        "guion",
        "llamada",
        "asunto",
        "asuntos",
        "corto",
        "corta",
        "breve",
        "formal",
        "cercano",
        "ahora",
        "para",
        "pasalo",
        "director",
        "directora",
        "gerente",
        "jefe",
        "ceo",
        "cfo",
        "cio",
        "cto",
        "cmo",
        "coo",
        "cdo",
        "ciso",
        "invitar",
        "invitacion",
    ]
)


def _focus(ctx: engine.Context, text: str) -> ProductFact | None:
    """El producto que se nombra («la membresía», «delegación silver») o el principal. Se busca
    solo en el NOMBRE de cada producto, sin las palabras del pedido («correo», «corto»…)."""
    main = engine._best(ctx, "producto")
    words = {w for w in engine._terms(text) if w not in {x[:5] for x in _WRITING_WORDS}}
    if not words:
        return main
    candidates = [f for kind in ("producto", "precio") for f in ctx.facts.get(kind) or []
                  if not (f.meta or {}).get("industria")]  # fmt: skip
    # El nombre pesa más; un producto que lo menciona en su descripción («Expositor (stand)»)
    # gana a una línea de precio con la misma palabra.
    scored = [(len(words & engine._terms(_short(f))) * 2
               + (len(words & engine._terms(f.title)) if f.kind == "producto" else 0)
               + (1 if f.kind == "producto" else 0) * bool(words & engine._terms(f.title)), f)
              for f in candidates]  # fmt: skip
    hits, best = max(scored, key=lambda x: x[0], default=(0, None))
    return best if hits and best is not None else main


def _price_for(ctx: engine.Context, text: str, focus: ProductFact) -> str:
    """El precio de lo que se pidió (la línea de precio que nombra lo mismo), si existe."""
    if focus.kind == "precio":
        return _clean(focus.title) if re.search(r"\d", focus.title) else ""
    words = {
        w
        for w in engine._terms(f"{text} {_short(focus)}")
        if w not in {x[:5] for x in _WRITING_WORDS}
    }
    best = max(((len(words & engine._terms(f.title)), f) for f in ctx.facts.get("precio") or []),
               key=lambda x: x[0], default=(0, None))  # fmt: skip
    return _clean(best[1].title) if best[0] and best[1] is not None else ""


def _short(fact: ProductFact) -> str:
    from orkesta.concertino.chat import short_name

    return short_name(fact)


_PRICE_TAIL = re.compile(
    r"\s*(?:[:\-–—]\s*|\b(?:desde|por|a partir de|en)\s+)?(?:US\$|USD|\$|€)\s?[\d.,]+.*$", re.I
)


def _offer_name(fact: ProductFact) -> str:
    """El nombre de lo que se ofrece, sin el precio («Stands desde US$7.200» → «Stands»)."""
    name = _short(fact)
    clean = _PRICE_TAIL.sub("", name).strip(" :-–—")
    return clean or name


def _offer_kind(fact: ProductFact | None) -> str:
    text = fold(f"{fact.title} {fact.detail}" if fact else "")
    if re.search(r"\b(stands?|patrocin\w*|sponsor\w*|expositor\w*|branding|marca)\b", text):
        return "patrocinio"
    if re.search(r"\b(delegacion\w*|equipo completo|varias personas)\b", text):
        return "delegacion"
    if re.search(r"\b(pase|pases|entrada|entradas|ticket|tickets|full pass|acceso)\b", text):
        return "entrada"
    return "general"


def _who(it: Intent, area: str | None) -> str:
    if it.cargo:
        return f"un {it.cargo.upper()}" if len(it.cargo) <= 5 else f"un {it.cargo}"
    if area and area in PERSONAS:
        return PERSONAS[area]["es"]["label"]
    return "tus contactos"


def _where(industry: str | None) -> str:
    if industry not in INDUSTRY_CONTEXT:
        return ""
    name = INDUSTRY_CONTEXT[industry]["es"][0]
    if name.startswith("el "):
        return f" del {name[3:]}"
    return f" de {name}"


def brief(
    ctx: engine.Context, it: Intent, text: str, company: str
) -> tuple[copy.Brief, dict[str, Any]] | None:
    """El encargo para el redactor, armado con la ficha. None si la ficha está vacía."""
    from orkesta.engine.taxonomy.titles import classify_title

    focus = _focus(ctx, text)
    if focus is None:
        return None
    level, area = classify_title(it.cargo) if it.cargo else (None, None)
    group = pb.LEVEL_GROUPS.get(level or "")
    ctx.industry = it.industry or ctx.industry
    title = focus.title
    _head, _, rest = title.partition(":")
    benefits = [_clean(f.title) for f in (ctx.facts.get("valor") or [])[:2]]
    diff = engine._best(ctx, "diferencial")
    if diff:
        benefits.append(_clean(diff.title))
    proof = engine._best(ctx, "prueba")
    main = engine._best(ctx, "producto")
    eventish = bool(_EVENT.search(fold(f"{main.title if main else ''} {focus.title}")))
    tone = it.tone or ("formal" if group in ("alta", "direccion") else "cercano")
    count = it.count or (3 if it.piece == "secuencia" else 1)
    b = copy.Brief(
        product=_offer_name(focus),
        summary=_sentence(rest.strip() or focus.detail.split(".")[0]),
        benefits=[b for b in benefits if b][:3],
        proof=_clean(proof.title) if proof else "",
        company=company,
        area=area or "auto",
        industry=it.industry or "",
        goal=it.goal or ("evento" if eventish else "reunion"),
        tone=tone,
        framework="pas",
        language=it.language,
        steps=count if it.piece in ("secuencia", "correo") else 1,
    )
    meta = {"who": _who(it, area), "where": _where(it.industry), "product": b.product,
            "formal": tone == "formal" or (tone == "directo" and group in ("alta", "direccion")), "area": area, "focus": focus, "proof": proof,
            "main": _offer_name(main) if main else b.product, "kind": _offer_kind(focus),
            "price": _price_for(ctx, text, focus),
            "detail": _sentence(rest.strip()) if rest.strip() and focus.kind != "precio" else "",
            "purpose": it.purpose or ("invitacion" if it.goal == "evento" else None),
            "person": it.person, "company_name": it.target_company, "place": it.place,
            "eventish": eventish}  # fmt: skip
    return b, meta


def _plain(html: str) -> str:
    return html_to_text(html).strip()


def write(
    session: Session, ctx: engine.Context, it: Intent, text: str, company: str
) -> dict[str, Any]:
    made = brief(ctx, it, text, company)
    if made is None:
        return {"summary": "Para redactar necesito conocer tu producto. Carga la ficha en la página "
                           "Producto y te escribo los correos con ella.",
                "options": [], "tips": [], "sections": [], "next_step": "", "missing": [],
                "suggestions": ["¿Qué puedes hacer?"]}  # fmt: skip
    b, meta = made
    piece = it.piece or "correo"
    if piece in ("secuencia", "correo"):
        advice = _emails(ctx, b, meta, piece)
    elif piece == "asuntos":
        advice = _subjects(b, meta)
    elif piece in ("whatsapp", "linkedin"):
        advice = _short_messages(ctx, b, meta, piece)
    elif piece == "resumen":
        advice = _summary(ctx, meta)
    else:
        advice = _call_script(ctx, b, meta)
    advice["suggestions"] = _tweaks(it, piece)
    advice.setdefault("missing", [])
    advice.setdefault("sections", [])
    advice.setdefault("next_step", "")
    return advice


def _p(*parts: str) -> str:
    """Párrafos de un correo en HTML (los vacíos se omiten)."""
    from html import escape

    return "".join(f"<p>{escape(p, quote=False)}</p>" for p in parts if p and p.strip())


def _sheet_emails(
    ctx: engine.Context, b: copy.Brief, meta: dict[str, Any], count: int
) -> list[dict[str, Any]]:
    """Correos escritos con la ficha tal como está redactada (frases completas), por pasos:
    problema → prueba → diferencial → valor → cierre limpio."""
    v = engine.slots(ctx)
    formal, short = meta["formal"], b.tone == "directo"
    area = meta["area"]
    pain = PERSONAS[area]["es"]["pains"][0] if area in PERSONAS else ""
    problema = v.get("problema") or pain
    product = meta["product"]
    hello = "Buen día, {{nombre|}}:" if formal else "Hola, {{nombre|}}:"
    bye = "Saludos,<br>{{remitente}}" + (f"<br>{b.company}" if b.company else "")
    cta = ("¿[Te|Le] gustaría que [te|le] cuente los detalles en 10 minutos?" if b.goal == "evento"
           else "¿[Te|Le] parece si lo conversamos 15 minutos esta semana?")  # fmt: skip
    bodies: list[list[str]] = []
    bodies.append(_first_body(v, meta, problema, cta, short))
    follow = [
        [f"Retomo mi correo anterior con un dato concreto: {v['prueba']}." if v.get("prueba") else "Retomo mi correo anterior.",
         "" if short else (f"Lo que más valoran: {v['valor']}." if v.get("valor") else ""),
         "¿[Te|Le] hace sentido verlo?"],
        [f"Algo que nos diferencia: {v['diferencial']}." if v.get("diferencial") else f"Sigo pensando que {product} puede servirle a {{{{empresa|[tu|su] equipo}}}}.",
         "¿Es algo que estén evaluando este año?"],
        [f"Para que lo tenga[s|] a mano: {meta['price'] or v['precio']}." if meta["price"] or v.get("precio") else f"Si [te|le] sirve, [te|le] envío un resumen de {product} en una página.",
         "¿Lo vemos esta semana?"],
        ["Una pregunta corta: ¿quién en {{empresa|[tu|su] equipo}} es la persona indicada para conversar esto?",
         "Si no [eres|es] [tú|usted], [te|le] agradezco si me orienta[s|]."],
    ]  # fmt: skip
    breakup = [f"No quiero llenar[te|le] la bandeja. Si {problema or product} está en [tu|su] agenda este año, lo conversamos cuando [te|le] acomode.",
               "Si no es el momento, me lo dice[s|] y cierro el tema."]  # fmt: skip
    for n in range(1, count):
        bodies.append(
            breakup if n == count - 1 and count >= 3 else follow[min(n - 1, len(follow) - 1)]
        )
    subject_brief = copy.Brief(
        **{**b.__dict__, "steps": 1, "tone": "formal" if formal else "cercano"}
    )
    subjects = [s for s in copy.compose(subject_brief)["steps"][0]["subjects"] if s]
    if formal:
        subjects = [
            x.replace("tu equipo", "su equipo").replace("tu empresa", "su empresa")
            for x in subjects
        ]
    if meta["purpose"] == "seguimiento" and subjects:
        subjects = [f"Re: {subjects[0]}", *subjects[1:]]
    steps = []
    for i, parts in enumerate(bodies):
        html = _p(*(pb.voice(x, formal) for x in [*parts])).replace("<p>", "<p>", 1)
        steps.append({"subject": _named(subjects[0], meta) if i == 0 and subjects else "",
                      "subjects": [_named(x, meta) for x in subjects] if i == 0 else [],
                      "body": _named(f"<p>{hello}</p>{html}<p>{bye}</p>", meta),
                      "delay_days": copy.DELAYS[min(i, len(copy.DELAYS) - 1)]})  # fmt: skip
    return steps


def _named(text: str, meta: dict[str, Any]) -> str:
    """Si pidieron el correo para una persona concreta («para Juan Pérez, CFO de Bancolombia»),
    sale con su nombre y su empresa en vez de las variables de campaña."""
    if meta.get("person"):
        first = str(meta["person"]).split()[0]
        text = re.sub(r"\{\{nombre(\|[^}]*)?\}\}", first, text)
    if meta.get("company_name"):
        text = re.sub(r"\{\{empresa(\|[^}]*)?\}\}", str(meta["company_name"]), text)
    return text


def _first_body(v: dict[str, str], meta: dict[str, Any], problema: str, cta: str,
                short: bool) -> list[str]:  # fmt: skip
    """El primer correo según para qué es: primer contacto, seguimiento, invitación, propuesta,
    después de una reunión o para retomar el contacto; y según lo que se ofrece (patrocinio,
    delegación, entradas)."""
    product, main, kind = meta["product"], meta["main"], meta["kind"]
    purpose, where = meta["purpose"], meta["where"]
    place = f" en {meta['place']}" if meta.get("place") else ""
    company = meta.get("company_name") or "{{empresa|[tu|su] equipo}}"
    valor, prueba, publico = v.get("valor", ""), v.get("prueba", ""), v.get("publico", "")
    proof = "" if short or not prueba else f"Un dato: {prueba}."
    detail = meta.get("detail", "")
    if purpose == "seguimiento":
        return [f"[Te|Le] escribo para retomar mi mensaje anterior sobre {product}.",
                f"Sé que la agenda está llena, así que voy al grano: {valor}." if valor else "",
                proof,
                "¿Tiene sentido que lo veamos 15 minutos esta semana, o prefiere[s|] que [te|le] escriba más adelante?"]  # fmt: skip
    if purpose == "agradecer":
        return ["Gracias por [tu|su] tiempo hoy.",
                f"Como conversamos, con {product} {company} logra: {valor}." if valor else f"Como conversamos, [te|le] dejo el resumen de {product}.",
                "Para avanzar, ¿[te|le] envío la propuesta esta semana y la revisamos juntos?"]  # fmt: skip
    if purpose == "propuesta":
        return [f"Como acordamos, [te|le] comparto la propuesta de {product} para {company}.",
                f"En resumen: {valor}." if valor else "",
                f"Inversión: {meta['price']}." if meta.get("price") else "",
                "¿Qué día [te|le] queda bien para revisarla juntos 20 minutos?"]  # fmt: skip
    if purpose == "reactivar":
        return ["Hace un tiempo conversamos y quería retomar el contacto.",
                f"Hoy {product} ofrece: {valor}." if valor else f"Quería contar[te|le] lo nuevo de {product}.",
                f"¿Cómo están viendo este tema hoy en {company}?"]  # fmt: skip
    if kind == "patrocinio":
        return [f"Muchas marcas{where} quieren llegar a quienes deciden en las grandes empresas, y conseguir su atención es cada vez más difícil.",
                f"En {main}{place} se reúnen {publico}." if publico else f"[Te|Le] escribo por {main}{place}.",
                f"Con {product}, {company} está frente a ellos durante el evento" + (f": {detail}" if detail else "."),
                "¿[Te|Le] comparto las opciones de participación y lo vemos 15 minutos?"]  # fmt: skip
    if purpose == "invitacion" or meta.get("eventish"):
        team = (f"Si van varias personas de {company}, {product} tiene condiciones especiales para el equipo."
                if kind == "delegacion" else "")  # fmt: skip
        return [f"[Te|Le] escribo para invitar[te|le] a {main}{place}.",
                f"Se reúnen {publico}." if publico else "",
                f"Lo que se llevan: {valor}." if valor else "",
                team or proof,
                "¿[Te|Le] reservo un lugar o prefiere[s|] que [te|le] envíe la agenda?"]  # fmt: skip
    return [f"Hablo seguido con líderes{where} y hay un tema que se repite: {problema}." if problema else "",
            f"Por eso [te|le] escribo por {product}: {valor}." if valor else f"[Te|Le] escribo por {product}.",
            proof, cta]  # fmt: skip


def _emails(ctx: engine.Context, b: copy.Brief, meta: dict[str, Any], piece: str) -> dict[str, Any]:
    count = max(1, min(6, b.steps))
    if b.language == "en":
        result = copy.compose(b)
        steps, tips = result["steps"], [t for t in result["tips"] if "Buyer persona" not in t][:2]
    else:
        steps, tips = _sheet_emails(ctx, b, meta, count), []
    options = []
    first_subject = steps[0]["subject"]
    for i, step in enumerate(steps):
        subject = step["subject"] or f"Re: {first_subject}"
        when = "se envía primero" if i == 0 else f"{step['delay_days']} días después del anterior"
        options.append({"code": f"redactar:{i}", "name": f"Correo {i + 1} · {when}",
                        "why": "Listo para pegar; {{nombre}} y {{empresa}} se completan con cada contacto.",
                        "text": f"Asunto: {subject}\n\n{_plain(step['body'])}", "record": None})  # fmt: skip
    n = len(steps)
    summary = (f"Te armé una secuencia de {n} correos para {meta['who']}{meta['where']}, sobre {meta['product']}."
               if n > 1 else f"Te escribí un correo para {meta['who']}{meta['where']}, sobre {meta['product']}.")  # fmt: skip
    name = f"{meta['product'][:60]} · {meta['who'][:40]}"
    campaign = {"kind": "campana", "label": "Crear campaña con esta secuencia" if n > 1 else "Crear campaña con este correo",
                "name": name, "steps": [{"subject": s["subject"], "body": s["body"], "delay_days": s["delay_days"]} for s in steps]}  # fmt: skip
    return {"summary": summary, "options": options, "tips": tips, "actions": [campaign]}


def _subjects(b: copy.Brief, meta: dict[str, Any]) -> dict[str, Any]:
    subjects = [s for s in copy.compose(b)["steps"][0]["subjects"] if s][:5]
    return {
        "summary": f"Estos asuntos funcionan para {meta['who']}{meta['where']}: cortos, con su nombre o su empresa y sin palabras de folleto.",
        "options": [
            {
                "code": "asuntos",
                "name": "Asuntos para probar",
                "why": "Prueba dos en un A/B y quédate con el que más se abra.",
                "text": "\n".join(subjects),
                "record": None,
            }
        ],
        "tips": [],
    }


def _short_messages(
    ctx: engine.Context, b: copy.Brief, meta: dict[str, Any], piece: str
) -> dict[str, Any]:
    formal = meta["formal"]
    values = engine.slots(ctx)
    valor = values.get("valor", "")
    prueba = values.get("prueba", "")
    problema = values.get("problema", "")
    product = meta["product"]
    if piece == "whatsapp":
        a = (
            f"Hola {{{{nombre}}}}, [te|le] escribo por {product}. "
            + (f"En pocas palabras: {valor}. " if valor else "")
            + "¿[Te|Le] cuento en 2 minutos por aquí o prefiere[s|] una llamada corta?"
        )
        bmsg = (
            "Hola {{nombre}}, ¿cómo [estás|está]? Una pregunta rápida: "
            + (
                f"¿hoy cómo están resolviendo {problema}? "
                if problema
                else "¿esto está en [tu|su] radar este año? "
            )
            + (f"Lo pregunto porque {prueba}." if prueba else f"Lo pregunto por {product}.")
        )
        names = ("Primer mensaje", "Con una pregunta")
        why = "Corto y humano: una sola pregunta. Máximo 2 mensajes por semana."
    else:
        a = (
            "Hola {{nombre}}, me gustaría conectar con [tu|su] red. Trabajo con "
            + (values.get("publico", "líderes de empresa")[:120])
            + " y comparto ideas sobre "
            + product
            + "."
        )
        bmsg = (
            f"Gracias por aceptar, {{{{nombre}}}}. [Te|Le] escribo por {product}"
            + (f": {valor}" if valor else "")
            + ". ¿[Te|Le] interesa que [te|le] envíe los detalles por correo?"
        )
        names = ("Invitación a conectar", "Mensaje al aceptar")
        why = "Sin enlaces ni precios en la invitación; el detalle va después de que acepte."
    options = [{"code": f"{piece}:{i}", "name": n, "why": why, "text": pb.voice(t, formal), "record": None}
               for i, (n, t) in enumerate(zip(names, (a, bmsg), strict=True))]  # fmt: skip
    return {"summary": f"Te propongo dos mensajes de {PIECES[piece].split(' de ')[-1]} para {meta['who']}{meta['where']}.",
            "options": options, "tips": []}  # fmt: skip


def _summary(ctx: engine.Context, meta: dict[str, Any]) -> dict[str, Any]:
    v = engine.slots(ctx)
    lines = [f"Qué es: {_sentence(meta['focus'].title)}"]
    for label, key in (("Para quién", "publico"), ("Qué logra", "valor"), ("Prueba", "prueba"),
                       ("Diferencial", "diferencial"), ("Inversión", "precio")):  # fmt: skip
        if v.get(key):
            lines.append(f"{label}: {_sentence(v[key])}")
    lines.append("Siguiente paso: una reunión de 20 minutos para revisar si aplica a su equipo.")
    return {"summary": "Este es el resumen ejecutivo de una página, listo para que lo presenten internamente.",
            "options": [{"code": "resumen", "name": "Resumen ejecutivo", "why": "Ayuda a que tu contacto lo defienda ante quien decide.",
                         "text": "\n\n".join(lines), "record": None}], "tips": []}  # fmt: skip


def _call_script(ctx: engine.Context, b: copy.Brief, meta: dict[str, Any]) -> dict[str, Any]:
    v = engine.slots(ctx)
    pains = PERSONAS.get(meta["area"] or "direccion", PERSONAS["direccion"])["es"]["pains"]
    objection = next((f for f in ctx.facts.get("objecion") or [] if f.answer), None)
    parts = [
        "Apertura: «Hola, {{nombre}}, habla [tu nombre] de "
        + (b.company or "[tu empresa]")
        + ". ¿[Tienes|Tiene] dos minutos? [Te|Le] llamo por algo puntual.»",
        "Motivo: «"
        + (
            _sentence(f"Trabajo con {v['publico']}")
            if v.get("publico")
            else "Trabajo con equipos como el [tuyo|suyo]."
        )
        + "»",
        f"Pregunta 1: «¿Cómo están resolviendo hoy {pains[0]}?»",
        f"Pregunta 2: «¿Qué pasaría si {pains[1]} fuera más simple este año?»",
        "Propuesta: «"
        + (_sentence(v["valor"]) if v.get("valor") else _sentence(meta["product"]))
        + (f" {_sentence(v['prueba'])}" if v.get("prueba") else "")
        + "»",
    ]
    if objection:
        parts.append(f"Si dice «{objection.title}»: «{objection.answer}»")
    parts.append("Cierre: «¿[Te|Le] parece si lo vemos 20 minutos el martes o el jueves?»")
    return {"summary": f"Guion de llamada para {meta['who']}{meta['where']}: escucha más de lo que hablas y cierra con dos opciones de horario.",
            "options": [{"code": "guion", "name": "Guion de llamada", "why": "Dos preguntas antes de presentar: así sabes qué ofrecer.",
                         "text": pb.voice("\n\n".join(parts), meta["formal"]), "record": None}], "tips": []}  # fmt: skip


def _tweaks(it: Intent, piece: str) -> list[str]:
    out = []
    if it.tone != "directo":
        out.append("Hazlo más corto")
    out.append("Más cercano" if it.tone == "formal" else "Más formal")
    if not it.cargo:
        out.append("Ahora para un CEO")
    elif "cfo" not in it.cargo:
        out.append("Ahora para un CFO")
    if piece in ("secuencia", "correo"):
        out.append("Pásalo a WhatsApp")
    elif piece == "whatsapp":
        out.append("Ahora como secuencia de 3 correos")
    return out[:4]
