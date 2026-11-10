# ruff: noqa: E501  (textos de venta: líneas largas a propósito)
"""Motor de Concertino: entiende la pregunta, reúne el contexto, elige la mejor jugada y redacta.

Sin terceros: reglas del oficio (``playbook``), la ficha del producto de la empresa, la cuenta
abierta y lo que la empresa aprendió de sus resultados. Los modelos propios del Cerebro
(tema y tipo de objeción) mandan cuando ya superaron a las reglas.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.brain import ml
from orkesta.concertino import playbook as pb
from orkesta.concertino.models import AdviceCase, ProductFact
from orkesta.engine.normalize.text import fold

WINS = ("reunion", "interesado")
TOPIC_TASK = "concertino_tema"
OBJECTION_TASK = "concertino_objecion"


# --- Entender la pregunta ---------------------------------------------------------------------


def objection_rules(text: str) -> tuple[str | None, int]:
    """(tipo de objeción, coincidencias) según el mapa de objeciones."""
    folded = fold(text)
    best, hits = None, 0
    for obj in pb.OBJECTIONS:
        n = sum(1 for p in obj.patterns if re.search(p, folded))
        if n > hits:
            best, hits = obj.code, n
    return best, hits


def topic_rules(text: str) -> dict[str, float]:
    folded = fold(text)
    out: dict[str, float] = {}
    for topic, pats in pb.TOPIC_RULES.items():
        # Un cargo suelto pesa menos: «reunión con el gerente» es de reunión, no de perfil.
        n = sum(0.5 if "ceo|" in p else 1.0 for p in pats if re.search(p, folded))
        if n:
            out[topic] = n
    return out


_SAID = re.compile(r"\b(me (dice|dijo|respondio|contesto|escribio|puso)|dice que|dijo que|"
                   r"responde que|contesto que|nos dijo)\b")  # fmt: skip


def _model(session: Session | None, task: str, text: str) -> tuple[str | None, bool]:
    if session is None or not text.strip():
        return None, False
    from orkesta.brain import registry

    try:
        found = registry.predict(session, task, ml.word_features(text))
    except Exception:
        return None, False
    return (found.label, found.sure) if found else (None, False)


def classify_objection(session: Session | None, text: str) -> str | None:
    rule, hits = objection_rules(text)
    label, sure = _model(session, OBJECTION_TASK, text)
    if label in pb.OBJECTION_BY_CODE and sure and (rule is None or hits == 1):
        return label
    return rule


def classify_topic(session: Session | None, text: str, *, objection: str | None,
                   has: dict[str, bool]) -> str:  # fmt: skip
    scores = topic_rules(text)
    label, sure = _model(session, TOPIC_TASK, text)
    if label in pb.TOPICS and sure:
        scores[label] = scores.get(label, 0) + 2
    folded = fold(text)
    if objection and (_SAID.search(folded) or '"' in text or "«" in text or not scores
                      or objection_rules(text)[1] > max(scores.values())):  # fmt: skip
        return "objecion"
    if not scores:
        if has.get("recipient"):
            return "respuesta"
        if has.get("deal"):
            return "cuenta"
        if has.get("campaign"):
            return "campana"
        if has.get("contact"):
            return "perfil"
        return "general"
    return max(scores, key=lambda t: (scores[t], -list(pb.TOPICS).index(t)))


# --- Contexto ---------------------------------------------------------------------------------


@dataclass
class Context:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    formal: bool
    contact: Any = None
    deal: Any = None
    recipient: Any = None
    campaign: Any = None
    level: str | None = None
    area: str | None = None
    industry: str | None = None
    facts: dict[str, list[ProductFact]] = field(default_factory=dict)

    @property
    def group(self) -> str | None:
        return pb.LEVEL_GROUPS.get(self.level or "")

    @property
    def first_name(self) -> str:
        return (getattr(self.contact, "first_name", "") or "").strip()


def load_facts(session: Session, tenant_id: uuid.UUID) -> dict[str, list[ProductFact]]:
    out: dict[str, list[ProductFact]] = {}
    rows = session.scalars(select(ProductFact).where(
        ProductFact.tenant_id == tenant_id, ProductFact.status == "confirmado")
        .order_by(ProductFact.evidence.desc(), ProductFact.created_at))  # fmt: skip
    today = datetime.now(UTC).date().isoformat()
    for fact in rows:
        # Un dato con vigencia vencida (una promoción, un precio especial) deja de usarse solo.
        if str((fact.meta or {}).get("vigencia") or "9999") < today:
            continue
        out.setdefault(fact.kind, []).append(fact)
    return out


def _clean(text: str) -> str:
    text = " ".join((text or "").split()).rstrip(" .;:")
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        text = text[0].lower() + text[1:]
    return text


def _best(ctx: Context, kind: str) -> ProductFact | None:
    """El dato de la ficha más pertinente para esta cuenta (mismo sector o área primero)."""
    items = ctx.facts.get(kind) or []
    if not items:
        return None

    def score(f: ProductFact) -> int:
        meta = f.meta or {}
        return 2 * (meta.get("industria") == ctx.industry and bool(ctx.industry)) + (
            meta.get("area") == ctx.area and bool(ctx.area))  # fmt: skip

    return max(items, key=score)


def slots(ctx: Context) -> dict[str, str]:
    from orkesta.copywriter.knowledge import PERSONAS

    out: dict[str, str] = {}
    for kind in ("valor", "prueba", "diferencial", "precio", "producto", "publico"):
        fact = _best(ctx, kind)
        if fact:
            out[kind] = _clean(fact.title)
    problem = _best(ctx, "problema")
    if problem:
        out["problema"] = _clean(problem.title)
    elif ctx.area and ctx.area in PERSONAS:
        out["problema"] = PERSONAS[ctx.area]["es"]["pains"][0]
    rival = _best(ctx, "competidor")
    if rival and rival.detail.strip():
        out["competidor"] = rival.title.strip().rstrip(".")
        out["competidor_detalle"] = _clean(rival.detail)
    return out


MISSING_TIPS = {
    "valor": "una propuesta de valor (qué resultado logra el cliente)",
    "prueba": "un caso de éxito con un resultado concreto",
    "diferencial": "qué los diferencia de la competencia",
    "precio": "una referencia de precio o rango de inversión",
    "competidor": "un competidor y en qué se diferencian",
    "competidor_detalle": "un competidor y en qué se diferencian",
    "problema": "el problema principal que resuelve el producto",
}
_SLOT = re.compile(r"\{([a-z_]+)\}")


def render(lines: tuple[str, ...], values: dict[str, str], ctx: Context) -> tuple[str, list[str]]:
    """Redacta un correo con el trato elegido. Omite frases cuyos datos faltan en la ficha."""
    kept: list[str] = []
    missing: list[str] = []
    for line in lines:
        names = _SLOT.findall(line)
        lack = [n for n in names if not values.get(n)]
        if lack:
            missing += lack
            continue
        kept.append(pb.voice(_SLOT.sub(lambda m: values[m.group(1)], line), ctx.formal))
    if not kept:
        return "", missing
    hello = f"Hola, {ctx.first_name}:" if ctx.first_name else "Hola:"
    if len(kept) <= 2:
        body = "\n\n".join(kept)
    else:
        body = "\n\n".join([kept[0], " ".join(kept[1:-1]), kept[-1]])
    return f"{hello}\n\n{body}", missing


# --- Lo aprendido -----------------------------------------------------------------------------


def approach_stats(
    session: Session, tenant_id: uuid.UUID, objection: str
) -> dict[str, tuple[int, int]]:
    """(usos, éxitos) de cada enfoque para esa objeción en la empresa (solo cifras)."""
    from sqlalchemy import case

    rows = session.execute(
        select(AdviceCase.chosen, func.count(),
               func.sum(case((AdviceCase.outcome.in_(WINS), 1), else_=0)))
        .where(AdviceCase.tenant_id == tenant_id, AdviceCase.objection == objection,
               AdviceCase.chosen.is_not(None), AdviceCase.action.in_(("usada", "editada")),
               AdviceCase.outcome.is_not(None))
        .group_by(AdviceCase.chosen)
    ).all()  # fmt: skip
    return {str(c): (int(n), int(w or 0)) for c, n, w in rows}


def rank(approaches: tuple[pb.Approach, ...], stats: dict[str, tuple[int, int]],
         group: str | None) -> list[tuple[pb.Approach, float, tuple[int, int]]]:  # fmt: skip
    """Ordena por probabilidad de terminar en reunión: lo aprendido manda; sin datos, el
    oficio (qué enfoque le sirve a ese nivel de decisor)."""
    out = []
    for i, ap in enumerate(approaches):
        n, wins = stats.get(ap.code, (0, 0))
        prior = 0.25 + (0.05 if group and group in ap.levels else 0) - 0.01 * i
        score = (wins + 4 * prior) / (n + 4)
        out.append((ap, score, (n, wins)))
    return sorted(out, key=lambda t: t[1], reverse=True)


def own_wins(session: Session, ctx: Context, objection: str) -> list[AdviceCase]:
    """Respuestas del propio vendedor que ya le funcionaron ante esta objeción."""
    return list(session.scalars(
        select(AdviceCase).where(
            AdviceCase.tenant_id == ctx.tenant_id, AdviceCase.user_id == ctx.user_id,
            AdviceCase.objection == objection, AdviceCase.outcome.in_(WINS),
            AdviceCase.final_text != "")
        .order_by(AdviceCase.outcome_at.desc()).limit(2)))  # fmt: skip


# --- Respuestas por tema ----------------------------------------------------------------------


def _level_tips(ctx: Context) -> list[str]:
    from orkesta.engine.taxonomy.titles import LEVEL_LABELS

    group = ctx.group
    if not group:
        return []
    cares, how, avoid = pb.LEVEL_TALK[group]
    who = LEVEL_LABELS.get(ctx.level or "", "este nivel")
    return [f"Con {who}: le importa {cares[0].lower() + cares[1:]}", f"Cómo escribirle: {how}",
            f"Evita: {avoid[0].lower() + avoid[1:]}"]  # fmt: skip


def _missing_text(missing: list[str]) -> list[str]:
    seen = []
    for name in missing:
        tip = MISSING_TIPS.get(name)
        if tip and tip not in seen:
            seen.append(tip)
    return [f"Agrega a la ficha {tip} para que la respuesta sea más fuerte." for tip in seen]


def advise_objection(
    session: Session, ctx: Context, code: str, *, from_reply: bool = False
) -> dict[str, Any]:
    obj = pb.OBJECTION_BY_CODE[code]
    values = slots(ctx)
    options: list[dict[str, Any]] = []
    missing: list[str] = []
    for fact in ctx.facts.get("objecion") or []:
        if (fact.meta or {}).get("tipo") == code and fact.answer.strip():
            text = (
                f"Hola, {ctx.first_name}:\n\n{fact.answer.strip()}"
                if ctx.first_name
                else fact.answer.strip()
            )
            options.append({"code": f"ficha:{fact.id}", "name": "La respuesta de tu ficha",
                            "why": "Tu empresa la definió como la mejor respuesta a esta objeción.",
                            "text": text, "record": None})  # fmt: skip
            break
    for case_ in own_wins(session, ctx, code):
        options.append({"code": f"propia:{case_.id}", "name": "La que ya te funcionó",
                        "why": f"La usaste antes con esta objeción y terminó en {'reunión' if case_.outcome == 'reunion' else 'interés'}.",
                        "text": case_.final_text, "record": None})  # fmt: skip
    for ap, _score, (n, wins) in rank(
        obj.approaches, approach_stats(session, ctx.tenant_id, code), ctx.group
    ):
        text, lack = render(ap.lines, values, ctx)
        missing += lack
        if not text:
            continue
        record = (
            f"En tu empresa: {wins} de {n} veces terminó en reunión o interés." if n >= 3 else None
        )
        options.append(
            {"code": ap.code, "name": ap.name, "why": ap.why, "text": text, "record": record}
        )
    confirm = pb.voice(obj.confirm, ctx.formal)
    return {
        "summary": f"{'Te respondió con una objeción' if from_reply else 'Objeción'}: {obj.label.lower()}. {obj.real}",
        "options": options[:4],
        "tips": _level_tips(ctx),
        "sections": [],
        "next_step": (f"Cuando responda, confirma que quedó resuelta: «{confirm}»" if confirm
                      else "Si no responde en una semana, cierra con elegancia y pasa a otra cuenta."),
        "missing": _missing_text(missing),
    }  # fmt: skip


def advise_reply(session: Session, ctx: Context, text: str) -> tuple[dict[str, Any], str | None]:
    from orkesta.brain import replies
    from orkesta.campaigns.replies import CATEGORIES

    objection = classify_objection(session, text)
    category = replies.decide(session, text).category if text.strip() else "otro"
    if objection and category not in ("reunion", "interesado", "baja", "fuera_oficina"):
        return advise_objection(session, ctx, objection, from_reply=True), objection
    play, lines = pb.REPLY_PLAYS.get(category, pb.REPLY_PLAYS["otro"])
    values = slots(ctx)
    draft, missing = render(lines, values, ctx) if lines else ("", [])
    options = [{"code": f"respuesta:{category}", "name": "Respuesta sugerida",
                "why": play, "text": draft, "record": None}] if draft else []  # fmt: skip
    label = CATEGORIES.get(category, {}).get("label", "Respuesta")
    return {"summary": f"Lo que entendí: {label.lower()}. {play}", "options": options,
            "tips": _level_tips(ctx), "sections": [],
            "next_step": "Responde hoy: la velocidad de respuesta pesa tanto como el contenido." if draft else "",
            "missing": _missing_text(missing)}, None  # fmt: skip


def advise_profile(session: Session, ctx: Context, text: str) -> dict[str, Any]:
    from orkesta.copywriter.knowledge import INDUSTRY_CONTEXT, PERSONAS
    from orkesta.engine.taxonomy.titles import AREA_LABELS, LEVEL_LABELS, classify_title

    if not (ctx.level or ctx.area):
        level, area = classify_title(text)
        ctx.level, ctx.area = ctx.level or level, ctx.area or area
    if not (ctx.level or ctx.area):
        return _ask_more("Cuéntame el cargo de la persona (por ejemplo, «gerente de compras de "
                         "un banco») o abre su contacto y vuelve a preguntarme.")  # fmt: skip
    persona = PERSONAS.get(ctx.area or "", {}).get("es")
    who = " de ".join(x for x in (LEVEL_LABELS.get(ctx.level or "", ""),
                                  AREA_LABELS.get(ctx.area or "", "")) if x) or "esta persona"  # fmt: skip
    sections = []
    cares = []
    if ctx.group:
        cares.append(pb.LEVEL_TALK[ctx.group][0])
    if persona:
        cares += [p[0].upper() + p[1:] for p in persona["pains"]]
    if ctx.industry and ctx.industry in INDUSTRY_CONTEXT:
        name, challenge = INDUSTRY_CONTEXT[ctx.industry]["es"]
        cares.append(f"En {name}, el reto de fondo suele ser {challenge}.")
    sections.append({"title": "Qué le importa", "items": cares})
    if ctx.group:
        _, how, avoid = pb.LEVEL_TALK[ctx.group]
        sections.append({"title": "Cómo escribirle", "items": [
            how, f"Trato sugerido: {'usted' if ctx.formal else 'tú'}."]})  # fmt: skip
        sections.append({"title": "Qué evitar", "items": [avoid]})
    likely = list(persona["objections"]) if persona else []
    if likely:
        sections.append(
            {"title": "Objeciones probables", "items": [o[0].upper() + o[1:] for o in likely]}
        )
    values = slots(ctx)
    opener = ("En empresas como la [tuya|suya], {problema} suele ser de las primeras prioridades.",
              "Lo que más valoran nuestros clientes: {valor}.",
              "Por ejemplo: {prueba}.",
              "¿[Te|Le] hace sentido conversarlo 15 minutos?")  # fmt: skip
    text_, missing = render(opener, values, ctx)
    options = [{"code": "apertura", "name": "Cómo abrir la conversación",
                "why": "Empieza por su problema, no por tu producto, y pide una sola cosa.",
                "text": text_, "record": None}] if text_ else []  # fmt: skip
    return {
        "summary": f"Cómo venderle a {who}.",
        "options": options,
        "tips": [],
        "sections": sections,
        "next_step": "",
        "missing": _missing_text(missing),
    }


def _days(when: datetime | None) -> int | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).days


def advise_account(session: Session, ctx: Context) -> dict[str, Any]:
    from orkesta.crm.models import Activity, Stage

    if ctx.deal is None and ctx.contact is None:
        return _ask_more("Abre la oportunidad o el contacto y pregúntame desde ahí: así leo su "
                         "historia completa.")  # fmt: skip
    conds = []
    if ctx.deal is not None:
        conds.append(Activity.deal_id == ctx.deal.id)
    if ctx.contact is not None:
        conds.append(Activity.contact_id == ctx.contact.id)
    from sqlalchemy import or_

    acts = list(session.scalars(select(Activity).where(Activity.tenant_id == ctx.tenant_id, or_(*conds))
                                .order_by(Activity.created_at.desc()).limit(30)))  # fmt: skip
    human = [a for a in acts if a.type != "system"]
    last = human[0] if human else None
    quiet = (
        _days(last.created_at)
        if last
        else _days(getattr(ctx.deal or ctx.contact, "created_at", None))
    )
    open_tasks = sum(1 for a in acts if a.type == "task" and a.done_at is None)
    meetings = sum(1 for a in acts if a.type == "meeting")
    facts = []
    stage = None
    if ctx.deal is not None:
        stage = session.get(Stage, ctx.deal.stage_id)
        value = f"{ctx.deal.value:,.0f} {ctx.deal.currency}".replace(",", ".")
        facts.append(
            f"Oportunidad «{ctx.deal.title}» en {stage.name if stage else 'su etapa'} ({value})."
        )
    if quiet is not None:
        when = "hoy" if quiet == 0 else f"hace {quiet} día{'s' if quiet != 1 else ''}"
        facts.append(
            f"Último movimiento {when}" + (f" ({_ACT.get(last.type, last.type)})." if last else ".")
        )
    meetings_text = "1 reunión registrada" if meetings == 1 else f"{meetings} reuniones registradas"
    tasks_text = "1 tarea pendiente" if open_tasks == 1 else f"{open_tasks} tareas pendientes"
    facts.append(f"{meetings_text} y {tasks_text}.")
    plan: list[str]
    options: list[dict[str, Any]] = []
    missing: list[str] = []
    if ctx.deal is not None and ctx.deal.status == "lost":
        summary = "Oportunidad perdida: se puede reactivar si cambió algo."
        plan = [f"Motivo registrado: {ctx.deal.lost_reason or 'sin motivo'}.",
                "Espera un cambio real (nuevo presupuesto, nuevo responsable, un resultado tuyo nuevo) y escribe con ese ángulo.",
                "Registra en la ficha el motivo de pérdida si se repite: ayuda a todo el equipo."]  # fmt: skip
    elif ctx.deal is not None and ctx.deal.status == "won":
        summary = "Oportunidad ganada: cuida la relación y pide un referido."
        plan = [
            "Confirma que el cliente está obteniendo el resultado prometido.",
            "Pide una presentación a otra área o empresa parecida.",
            "Si el resultado es bueno, conviértelo en un caso de éxito en la ficha.",
        ]
    elif quiet is not None and quiet >= 14:
        summary = f"La cuenta se está enfriando: {quiet} días sin movimiento."
        plan = [
            "Hoy: escribe con un ángulo nuevo (no «solo para hacer seguimiento»).",
            "En 3 días: llama si tienes su teléfono, o escribe a otra persona de la empresa.",
            "En 7 días sin respuesta: envía el correo de cierre amable y suelta la cuenta.",
        ]
        cold = pb.OBJECTION_BY_CODE["silencio"]
        for ap in cold.approaches:
            text, lack = render(ap.lines, slots(ctx), ctx)
            missing += lack
            if text:
                options.append(
                    {"code": ap.code, "name": ap.name, "why": ap.why, "text": text, "record": None}
                )
    else:
        prob = stage.probability if stage else 0
        if prob >= 60:
            summary = "Cuenta avanzada: el trabajo ahora es asegurar el cierre."
            plan = [
                "Confirma quién firma y qué necesita para decir que sí.",
                "Acuerda una fecha de decisión y los pasos hasta ella.",
                "Anticipa la objeción de precio con opciones, no con descuentos.",
            ]
        elif prob >= 25 or meetings:
            summary = "Cuenta en evaluación: lleva la conversación a una propuesta concreta."
            plan = [
                "Resume por escrito su problema en sus palabras y pide que lo confirme.",
                "Propón una reunión para presentar la propuesta en vivo, no por correo.",
                "Identifica a las otras personas que influyen en la decisión.",
            ]
        else:
            summary = "Cuenta temprana: el siguiente paso es una reunión de descubrimiento."
            plan = [
                "Pide 20 minutos para entender su situación, no para presentar.",
                "Prepara tres preguntas sobre su problema antes de hablar de tu producto.",
                "Deja agendado el siguiente contacto antes de colgar.",
            ]
    return {"summary": summary, "options": options[:2], "tips": _level_tips(ctx),
            "sections": [{"title": "Lo que veo en la cuenta", "items": facts},
                         {"title": "Plan sugerido", "items": plan}],
            "next_step": plan[0] if plan else "", "missing": _missing_text(missing)}  # fmt: skip


_ACT = {"note": "nota", "call": "llamada", "meeting": "reunión", "email": "correo", "task": "tarea",
        "stage": "cambio de etapa"}  # fmt: skip


def _rate(part: int, total: int) -> str:
    return f"{round(100 * part / total)} %" if total else "—"


def advise_campaign(session: Session, ctx: Context) -> dict[str, Any]:
    from orkesta.campaigns.models import Campaign, Recipient
    from orkesta.campaigns.replies import POSITIVE
    from orkesta.copywriter.analyzer import analyze

    query = select(func.count(), func.count(Recipient.opened_at), func.count(Recipient.replied_at),
                   func.count().filter(Recipient.reply_category.in_(POSITIVE)))  # fmt: skip
    query = query.where(Recipient.tenant_id == ctx.tenant_id, Recipient.sent_count > 0)
    campaign = ctx.campaign
    if campaign is None:
        campaign = session.scalars(select(Campaign).where(Campaign.tenant_id == ctx.tenant_id,
                                                          Campaign.created_by == ctx.user_id)
                                   .order_by(Campaign.created_at.desc()).limit(1)).first()  # fmt: skip
    if campaign is None:
        return _ask_more("Todavía no tienes campañas. Cuando envíes la primera, pregúntame y la "
                         "reviso con sus números.")  # fmt: skip
    sent, opened, replied, positive = session.execute(
        query.where(Recipient.campaign_id == campaign.id)
    ).one()
    numbers = [f"Campaña «{campaign.name}»: {sent} personas contactadas.",
               f"Abrieron: {_rate(opened, sent)} · Respondieron: {_rate(replied, sent)} · Respuestas positivas: {_rate(positive, sent)}."]  # fmt: skip
    steps = campaign.steps or []
    first = steps[0] if steps else {"subject": "", "body": ""}
    checks = analyze(first.get("subject", ""), first.get("body", ""), step=0)
    fixes = [
        c["tip"]
        for c in checks.get("checks", [])
        if c.get("status") in ("bad", "warn") and c.get("tip")
    ]
    from orkesta.brain import copywriting

    assessed = copywriting.assess(session, first.get("subject", ""), first.get("body", ""), 0)
    if assessed:
        fixes = list(assessed.get("tips", [])) + fixes
    if sent < 30:
        summary = f"Aún hay pocos datos ({sent} envíos): revisemos el correo antes que los números."
    elif opened / sent < 0.25:
        summary = "Pocas aperturas: el problema está en el asunto o en la entrega, no en el texto."
        fixes = [
            "Prueba un asunto más corto y personal (con su nombre o su empresa), sin palabras de publicidad.",
            "Revisa la salud del dominio de envío en Campañas y no superes el límite diario.",
            *fixes,
        ]
    elif replied / sent < 0.03:
        summary = "Abren pero no responden: el cuerpo no da una razón para contestar."
        fixes = [
            "Abre con el problema de la persona, no con tu empresa.",
            "Cierra con una sola pregunta fácil de responder.",
            *fixes,
        ]
    elif positive < max(1, replied // 3):
        summary = (
            "Responden, pero pocas respuestas son positivas: revisa a quién le estás escribiendo."
        )
        fixes = [
            "Pregúntame «¿a qué sector le apunto?» para ver qué perfiles responden mejor.",
            "Ajusta la audiencia al cargo que decide sobre tu producto.",
            *fixes,
        ]
    else:
        summary = "La campaña va bien: replica lo que funciona en la siguiente."
        fixes = ["Guárdala como plantilla de equipo para que otros la usen.", *fixes]
    return {"summary": summary, "options": [], "tips": [],
            "sections": [{"title": "Números", "items": numbers},
                         {"title": "Cambios concretos", "items": list(dict.fromkeys(fixes))[:5]}],
            "next_step": "", "missing": []}  # fmt: skip


def _common_objections(session: Session, ctx: Context) -> list[str]:
    rows = session.execute(
        select(AdviceCase.objection, func.count()).where(
            AdviceCase.tenant_id == ctx.tenant_id, AdviceCase.objection.is_not(None))
        .group_by(AdviceCase.objection).order_by(func.count().desc()).limit(3)).all()  # fmt: skip
    found = [pb.OBJECTION_BY_CODE[c] for c, _ in rows if c in pb.OBJECTION_BY_CODE]
    if not found:
        found = [pb.OBJECTION_BY_CODE["precio"], pb.OBJECTION_BY_CODE["comite"]]
    return [f"{o.label}: {o.approaches[0].name.lower()}." for o in found]


def advise_proposal(session: Session, ctx: Context) -> dict[str, Any]:
    values = slots(ctx)
    args = [f"Valor: {values['valor']}." if values.get("valor") else "",
            f"Prueba: {values['prueba']}." if values.get("prueba") else "",
            f"Diferencial: {values['diferencial']}." if values.get("diferencial") else "",
            f"Referencia de precio: {values['precio']}." if values.get("precio") else ""]  # fmt: skip
    missing = [k for k in ("valor", "prueba", "diferencial", "precio") if not values.get(k)]
    return {
        "summary": "Una propuesta gana cuando el precio llega después del valor y con opciones.",
        "options": [], "tips": _level_tips(ctx),
        "sections": [
            {"title": "Estructura", "items": [
                "1. Su problema, en sus palabras (que se reconozca).",
                "2. La solución y el alcance, sin funciones de más.",
                "3. El resultado esperado, con una prueba de una empresa parecida.",
                "4. La inversión, en dos o tres opciones.",
                "5. Próximos pasos con fechas."]},
            {"title": "Cómo presentar el precio", "items": [
                "Ancla primero el costo del problema; después el número.",
                "Presenta opciones: quien elige entre opciones deja de decidir entre «sí» y «no».",
                "Repásala en una llamada; no la envíes sola por correo."]},
            {"title": "Argumentos de tu ficha", "items": [a for a in args if a]
             or ["Tu ficha aún no tiene argumentos: agrégalos en la página Producto."]},
            {"title": "Objeciones a anticipar", "items": _common_objections(session, ctx)},
        ],
        "next_step": "Agenda la llamada para presentar la propuesta antes de enviarla.",
        "missing": _missing_text(missing),
    }  # fmt: skip


DISCOVERY = {
    "alta": [
        "¿Qué tendría que pasar este año para que [consideres|considere] que fue un buen año?",
        "¿Qué está frenando hoy ese resultado?",
        "Si no se resuelve, ¿qué impacto tiene en el negocio?",
    ],
    "direccion": [
        "¿Cuáles son las metas de [tu|su] área este año y cómo se miden?",
        "¿Qué [te|le] está costando más alcanzarlas?",
        "¿Quién más participa en una decisión como esta?",
    ],
    "gestion": [
        "¿Cómo resuelve hoy [tu|su] equipo este tema, paso a paso?",
        "¿Dónde se pierde más tiempo o aparecen más errores?",
        "Si se resolviera, ¿qué cambiaría en el día a día?",
    ],
    "operativo": [
        "¿Qué herramientas usan hoy y qué les falta?",
        "¿Qué tan seguido aparece este problema?",
        "¿Quién decide sobre cambios de herramienta en [tu|su] equipo?",
    ],
}


def advise_meeting(session: Session, ctx: Context) -> dict[str, Any]:
    from orkesta.copywriter.knowledge import PERSONAS

    group = ctx.group or "direccion"
    persona = PERSONAS.get(ctx.area or "", {}).get("es")
    who = []
    if ctx.contact is not None:
        who.append(
            f"{ctx.contact.full_name}"
            + (f", {ctx.contact.job_title}" if ctx.contact.job_title else "")
            + (f" en {ctx.contact.company_name}" if ctx.contact.company_name else "")
            + "."
        )
    values = slots(ctx)
    pains = (
        [values["problema"][0].upper() + values["problema"][1:] + "."]
        if values.get("problema")
        else []
    )
    if persona:
        pains += [p[0].upper() + p[1:] + "." for p in persona["pains"][:2]]
    sections = []
    if who:
        sections.append({"title": "Con quién", "items": who})
    sections.append({"title": "Dolor probable", "items": list(dict.fromkeys(pains))
                     or ["Abre el contacto para afinar: no sé aún su cargo ni su área."]})  # fmt: skip
    sections.append({"title": "Preguntas de descubrimiento",
                     "items": [pb.voice(q, ctx.formal) for q in DISCOVERY[group]]})  # fmt: skip
    sections.append({"title": "Objeciones probables", "items": _common_objections(session, ctx)})
    sections.append(
        {
            "title": "Cierre a proponer",
            "items": [
                "Antes de terminar, acuerda el siguiente paso con fecha: propuesta, demo con su equipo o reunión con quien decide."
            ],
        }
    )
    return {"summary": "Así llegas preparado: escucha más de lo que presentas.", "options": [],
            "tips": _level_tips(ctx), "sections": sections,
            "next_step": "Envía hoy la invitación con la agenda en tres líneas.", "missing": []}  # fmt: skip


def advise_strategy(session: Session, ctx: Context) -> dict[str, Any]:
    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.replies import POSITIVE
    from orkesta.copywriter.knowledge import INDUSTRY_CONTEXT
    from orkesta.data.models import Contact
    from orkesta.engine.taxonomy.titles import LEVEL_LABELS

    def by(column: Any) -> list[tuple[str, int, int]]:
        rows = session.execute(
            select(column, func.count(), func.count().filter(Recipient.reply_category.in_(POSITIVE)))
            .join(Contact, Contact.id == Recipient.contact_id)
            .where(Recipient.tenant_id == ctx.tenant_id, Recipient.sent_count > 0, column.is_not(None))
            .group_by(column)).all()  # fmt: skip
        return [(str(k), int(n), int(p)) for k, n, p in rows if n >= 15]

    def label(code: str) -> str:
        return INDUSTRY_CONTEXT.get(code, {}).get("es", (code.replace("_", " "), ""))[0]

    sectors = sorted(by(Contact.industry_code), key=lambda r: r[2] / r[1], reverse=True)
    levels = sorted(by(Contact.level_code), key=lambda r: r[2] / r[1], reverse=True)
    if not sectors and not levels:
        publico = slots(ctx).get("publico")
        return {"summary": "Aún no hay resultados suficientes para comparar sectores (mínimo 15 contactos por grupo).",
                "options": [], "tips": [], "sections": [{"title": "Mientras tanto", "items": [
                    f"Empieza por tu cliente ideal de la ficha: {publico}." if publico else
                    "Define tu cliente ideal en la ficha del producto (sector, tamaño y cargo).",
                    "Lanza campañas pequeñas a dos sectores distintos y compara en dos semanas."]}],
                "next_step": "", "missing": [] if publico else _missing_text([])}  # fmt: skip
    sections = []
    if sectors:
        sections.append(
            {
                "title": "Sectores que mejor responden",
                "items": [
                    f"{label(k)}: {_rate(p, n)} de respuestas positivas ({n} contactados)."
                    for k, n, p in sectors[:3]
                ],
            }
        )
        if len(sectors) > 3:
            k, n, p = sectors[-1]
            sections.append(
                {"title": "Donde menos", "items": [f"{label(k)}: {_rate(p, n)} ({n} contactados)."]}
            )
    if levels:
        sections.append(
            {
                "title": "Niveles que mejor responden",
                "items": [
                    f"{LEVEL_LABELS.get(k, k)}: {_rate(p, n)} de respuestas positivas ({n} contactados)."
                    for k, n, p in levels[:3]
                ],
            }
        )
    best = label(sectors[0][0]) if sectors else None
    return {"summary": f"Con tus resultados, apuntaría primero a {best}." if best else
            "Con tus resultados, estos son los perfiles que mejor responden.",
            "options": [], "tips": [], "sections": sections,
            "next_step": "Busca en Prospección más empresas del sector que mejor responde.", "missing": []}  # fmt: skip


_STOP = frozenset(
    ["como", "cual", "cuales", "cuanto", "cuanta", "cuantos", "cuesta", "cuestan", "nuestro", "nuestra", "nuestros", "nuestras", "producto", "productos", "para", "esta", "este", "estos", "estas", "tiene", "tienen", "puedo", "puede", "sobre", "entre", "donde", "cuando", "quien", "porque", "desde", "hasta", "tambien", "solo", "algo", "alguna", "algun", "hacer", "decir", "dice", "saber", "quiero", "necesito", "ellos", "ustedes", "tengo", "tenemos", "estan", "hay", "que", "los", "las", "una", "uno", "del", "por", "con", "sus", "mas", "les", "le"]
)  # fmt: skip
_PITCH = re.compile(r"\b(presento|presentar|presentacion|pitch|explico|explicar|resumo|resumir)\b")


def _terms(text: str) -> set[str]:
    """Raíces (5 letras) de las palabras con contenido: «entradas» y «entrada» coinciden."""
    return {w[:5] for w in re.findall(r"[a-z0-9$]+", fold(text)) if len(w) > 2 and w not in _STOP}


def _overlap(question: str, fact: ProductFact) -> float:
    words = _terms(question)
    if not words:
        return 0
    title, rest = _terms(fact.title), _terms(f"{fact.detail} {fact.answer}")
    score = 3 * len(words & title) + len(words & (rest - title))
    # Una pregunta frecuente que contiene toda la pregunta va primero.
    if fact.kind == "pregunta" and title:
        cover = len(words & title) / len(words)
        score += 10 if cover == 1 else 2 if cover >= 0.6 else 0
    return score


KIND_LABELS = {"producto": "Qué es", "publico": "Para quién", "problema": "Problema que resuelve",
               "valor": "Propuesta de valor", "prueba": "Caso de éxito", "precio": "Precio",
               "competidor": "Competidor", "diferencial": "Diferencial", "objecion": "Objeción",
               "pregunta": "Pregunta frecuente"}  # fmt: skip


def advise_product(session: Session, ctx: Context, question: str) -> dict[str, Any]:
    all_facts = [f for items in ctx.facts.values() for f in items]
    # «¿Cómo presento el producto?» pide el discurso armado, no datos sueltos.
    pitch_only = (
        bool(_PITCH.search(fold(question))) and len(_terms(_PITCH.sub(" ", fold(question)))) <= 1
    )
    scored = [] if pitch_only else [(f, _overlap(question, f)) for f in all_facts]
    best = max((sc for _, sc in scored), default=0)
    hits = [
        f for f, sc in sorted(scored, key=lambda x: x[1], reverse=True) if sc >= max(2, best / 3)
    ]
    if hits:
        items = [f"{KIND_LABELS.get(f.kind, f.kind)}: {f.title}" + (f" — {f.answer or f.detail}" if (f.answer or f.detail) else "")
                 for f in hits[:5]]  # fmt: skip
        return {
            "summary": "Esto es lo que dice tu ficha:",
            "options": [],
            "tips": [],
            "sections": [{"title": "De la ficha", "items": items}],
            "next_step": "",
            "missing": [],
        }
    values = slots(ctx)
    pitch = ("{producto}.", "Es para {publico}.", "Resuelve {problema}.", "Lo que más valoran nuestros clientes: {valor}.",
             "Por ejemplo: {prueba}.", "Lo que nos diferencia: {diferencial}.")  # fmt: skip
    kept = [pb.voice(_SLOT.sub(lambda m: values[m.group(1)], line), ctx.formal)
            for line in pitch if all(values.get(n) for n in _SLOT.findall(line))]  # fmt: skip
    missing = [n for line in pitch for n in _SLOT.findall(line) if not values.get(n)]
    if not kept:
        return _ask_more("No encuentro eso en la ficha del producto. Agrégalo en la página Producto "
                         "y desde ese momento lo uso en mis respuestas.")  # fmt: skip
    text = " ".join(k[0].upper() + k[1:] for k in kept)
    return {"summary": "No encontré ese dato exacto en la ficha. Así presentaría el producto con lo que sé:",
            "options": [{"code": "pitch", "name": "Presentación en 30 segundos",
                         "why": "Problema, resultado y prueba: en ese orden.", "text": text, "record": None}],
            "tips": [], "sections": [], "next_step": "",
            "missing": _missing_text(missing)}  # fmt: skip


def _ask_more(message: str) -> dict[str, Any]:
    return {
        "summary": message,
        "options": [],
        "tips": [],
        "sections": [],
        "next_step": "",
        "missing": [],
    }


def advise_general(session: Session, ctx: Context, question: str) -> dict[str, Any]:
    found = advise_product(session, ctx, question)
    if found["sections"]:
        return found
    return _ask_more(
        "Puedo ayudarte con una respuesta que recibiste, una objeción, cómo hablarle a un perfil, "
        "una cuenta, una campaña, una propuesta, una reunión o a qué sector apuntar. "
        "Cuéntame el caso con un poco más de detalle."
    )


def frequent(values: list[str], n: int = 3) -> list[tuple[str, int]]:
    return Counter(values).most_common(n)


STALE_DAYS = 21


def stale_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(days=STALE_DAYS)
