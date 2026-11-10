# ruff: noqa: E501  (textos de venta: líneas largas a propósito)
"""Servicio de Concertino: atender una consulta, la ficha del producto y el aprendizaje.

Concertino solo actúa cuando se lo piden: todo lo de este módulo corre por una acción del
usuario (una pregunta, abrir la ficha, marcar cómo terminó un caso). Lo que aprende de las
campañas queda «aprendido» en la ficha hasta que una persona lo confirme.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.concertino import chat, engine, intent, writer
from orkesta.concertino import playbook as pb
from orkesta.concertino.models import FACT_KINDS, AdviceCase, ProductFact
from orkesta.core.db import unscoped
from orkesta.engine.normalize.text import fold
from orkesta.platform.models import User

MAX_FACTS = 300
LEARN_MIN_OBJECTIONS = 3
LEARN_MIN_QUESTIONS = 2

# Qué puede usar cada plan (sin costo por consulta: los planes se distinguen por funciones).
PLAN_TOPICS: dict[str, set[str]] = {
    "inicial": {"objecion", "producto", "general"},
}
UPGRADE = ("Esa consulta es parte del plan Pro. Con tu plan puedes pedirme ayuda con objeciones "
           "y con la ficha del producto.")  # fmt: skip

INTERVIEW: list[dict[str, str]] = [
    {"kind": "producto", "question": "¿Qué vendes, en una frase que entendería un cliente?",
     "example": "Plataforma de prospección B2B que encuentra y contacta a quien decide"},
    {"kind": "publico", "question": "¿A quién se lo vendes? Sector, tamaño de empresa y cargo que decide.",
     "example": "Empresas de 50 a 500 empleados en Latinoamérica; decide la gerencia comercial"},
    {"kind": "problema", "question": "¿Qué problema le resuelves a ese cliente?",
     "example": "el equipo comercial pierde horas buscando contactos y escribiendo correos uno por uno"},
    {"kind": "valor", "question": "¿Qué resultado concreto logra el cliente?",
     "example": "triplicar las reuniones agendadas sin sumar personas al equipo"},
    {"kind": "prueba", "question": "¿Qué caso de éxito puedes contar, con un número?",
     "example": "una empresa de software pasó de 4 a 12 reuniones al mes en 60 días"},
    {"kind": "diferencial", "question": "¿Qué te diferencia de la competencia?",
     "example": "datos de Latinoamérica verificados y una IA propia que aprende de cada respuesta"},
    {"kind": "precio", "question": "¿Cuál es la referencia de precio o el rango de inversión?",
     "example": "desde 99 dólares al mes por usuario"},
    {"kind": "competidor", "question": "¿Contra quién compites y en qué eres distinto? (nombre y diferencia)",
     "example": "Apollo — con mejores datos de Latinoamérica y soporte en español"},
]  # fmt: skip


class ConcertinoError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def allowed(session: Session, tenant_id: uuid.UUID, topic: str) -> bool:
    from orkesta.admin.limits import plan_limits

    found = plan_limits(session, tenant_id)
    if found is None:
        return True
    topics = PLAN_TOPICS.get(found[0])
    return topics is None or topic in topics


def plan_features(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    from orkesta.admin.limits import plan_limits

    found = plan_limits(session, tenant_id)
    topics = PLAN_TOPICS.get(found[0]) if found else None
    return {"full": topics is None, "topics": sorted(topics) if topics else list(pb.TOPICS)}


# --- Consultas --------------------------------------------------------------------------------


def _context(session: Session, *, tenant_id: uuid.UUID, user: User, contact_id: uuid.UUID | None,
             deal_id: uuid.UUID | None, recipient_id: uuid.UUID | None,
             campaign_id: uuid.UUID | None, trato: str) -> engine.Context:  # fmt: skip
    from orkesta.campaigns.models import Campaign, Recipient
    from orkesta.crm.models import Deal
    from orkesta.data.models import Contact

    def mine(model: Any, item_id: uuid.UUID | None) -> Any:
        if item_id is None:
            return None
        row = session.get(model, item_id)  # el filtro por persona sigue activo: solo lo que ve
        if row is None or row.tenant_id != tenant_id:
            raise ConcertinoError("No encuentro eso o no está a tu alcance.", 404)
        return row

    recipient = mine(Recipient, recipient_id)
    deal = mine(Deal, deal_id)
    if recipient is not None and contact_id is None:
        contact_id = recipient.contact_id
    if deal is not None and contact_id is None:
        contact_id = deal.contact_id
    contact = mine(Contact, contact_id)
    campaign = mine(Campaign, campaign_id or (recipient.campaign_id if recipient else None))
    if campaign is not None and campaign.created_by not in (user.id, None):
        raise ConcertinoError("Esa campaña es de otra persona del equipo.", 404)
    level = getattr(contact, "level_code", None)
    area = getattr(contact, "area_code", None)
    if contact is not None and not (level and area) and contact.job_title:
        from orkesta.engine.taxonomy.titles import classify_title

        guess_level, guess_area = classify_title(contact.job_title)
        level, area = level or guess_level, area or guess_area
    group = pb.LEVEL_GROUPS.get(level or "")
    formal = trato == "usted" or (trato == "auto" and group in ("alta", "direccion"))
    return engine.Context(
        tenant_id=tenant_id, user_id=user.id, formal=formal, contact=contact, deal=deal,
        recipient=recipient, campaign=campaign, level=level, area=area,
        industry=getattr(contact, "industry_code", None),
        facts=engine.load_facts(session, tenant_id),
    )  # fmt: skip


def ask(
    session: Session, *, tenant_id: uuid.UUID, user: User, question: str = "",
    mode: str | None = None, contact_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None, recipient_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None, trato: str = "auto",
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:  # fmt: skip
    question = (question or "").strip()[:4000]
    history = (history or [])[-6:]
    ctx = _context(session, tenant_id=tenant_id, user=user, contact_id=contact_id,
                   deal_id=deal_id, recipient_id=recipient_id, campaign_id=campaign_id,
                   trato=trato)  # fmt: skip
    name, turn = chat.user_name(user), len(history)
    # Charla (saludos, gracias, «¿qué puedes hacer?») y «¿qué vendo?»: respuesta directa.
    quick = chat.small_talk(question, ctx, name) if question and mode is None else None
    # Pedidos de redacción («crea la secuencia de 3 correos») y sus ajustes («más corto»).
    if quick is None and question and mode in (None, "redactar"):
        wanted = _writing_intent(question, history)
        if wanted is not None:
            return _write(session, ctx, user=user, tenant_id=tenant_id, question=question,
                          wanted=wanted, history=history, name=name, turn=turn)  # fmt: skip
    if quick is None and question and mode in (None, "producto", "general"):
        quick = chat.catalog(question, ctx)
        if quick is not None:
            quick["summary"] = chat.greet(quick["summary"], name, turn)
    if quick is not None:
        topic_code = "producto" if quick["sections"] else "charla"
        return {"case_id": None, "topic": topic_code,
                "topic_label": pb.TOPICS["producto"] if topic_code == "producto" else "",
                "objection": None, "objection_label": None, "formal": ctx.formal, **quick,
                "sources": ["Ficha del producto"] if quick["sections"] else []}  # fmt: skip
    reply_text = (ctx.recipient.reply_snippet or "") if ctx.recipient is not None else ""
    if not question and not reply_text and mode not in ("cuenta", "campana", "reunion",
                                                         "estrategia", "perfil", "propuesta"):  # fmt: skip
        raise ConcertinoError("Escribe tu pregunta o pega la objeción que recibiste.")
    text = question or reply_text
    objection = engine.classify_objection(session, text) if text else None
    if mode in pb.TOPICS:
        topic = mode
    elif ctx.recipient is not None and not question:
        topic = "respuesta"
    else:
        topic = engine.classify_topic(session, text, objection=objection, has={
            "recipient": ctx.recipient is not None, "deal": ctx.deal is not None,
            "campaign": ctx.campaign is not None, "contact": ctx.contact is not None})  # fmt: skip
    # «¿Tienen descuento para universidades?» es una duda del producto, no una objeción.
    if (topic == "objecion" and question and mode is None and chat.is_question(question)
            and engine.advise_product(session, ctx, question).get("_hits")):  # fmt: skip
        topic, objection = "producto", None
    if topic == "objecion" and objection is None:
        objection = engine.objection_rules(text)[0]
    if not allowed(session, tenant_id, topic if topic != "respuesta" else "objecion"):
        return {"case_id": None, "topic": topic, "topic_label": pb.TOPICS[topic], "objection": None,
                "objection_label": None, **engine._ask_more(UPGRADE), "sources": []}  # fmt: skip
    if topic == "respuesta":
        advice, objection = engine.advise_reply(session, ctx, text)
    elif topic == "objecion":
        if objection is None:
            advice = engine._ask_more(
                "No reconozco la objeción. Pégame lo que te dijo la persona, tal cual, o elige el "
                "tipo de objeción y te propongo cómo responder.")  # fmt: skip
        else:
            advice = engine.advise_objection(session, ctx, objection)
    elif topic == "perfil":
        advice = engine.advise_profile(session, ctx, text)
        sheet = chat.from_sheet(text, ctx) if question else None
        if sheet:
            advice["sections"] = [sheet, *advice["sections"]]
    elif topic == "cuenta":
        advice = engine.advise_account(session, ctx)
    elif topic == "campana":
        advice = engine.advise_campaign(session, ctx)
    elif topic == "propuesta":
        advice = engine.advise_proposal(session, ctx)
    elif topic == "reunion":
        advice = engine.advise_meeting(session, ctx)
    elif topic == "estrategia":
        advice = engine.advise_strategy(session, ctx)
    elif topic in ("producto", "general"):
        # Sigue el hilo: «¿y en Miami?» se entiende con la pregunta anterior.
        previous = chat.follow_up(text, history) if question else ""
        advice = engine.advise_product(session, ctx, text, previous)
        if advice.get("_hits"):
            advice = chat.product_reply(advice, text, ctx)
        elif not advice.get("_pitch") and (topic == "general" or question):
            advice = chat.fallback(text, ctx)
    case = AdviceCase(
        tenant_id=tenant_id, user_id=user.id, topic=topic, objection=objection,
        question=question or reply_text[:2000],
        context={k: str(v) for k, v in (("contact_id", getattr(ctx.contact, "id", None)),
                                        ("deal_id", getattr(ctx.deal, "id", None)),
                                        ("campaign_id", getattr(ctx.campaign, "id", None)),
                                        ("level", ctx.level), ("area", ctx.area),
                                        ("industry", ctx.industry)) if v},
        shown=[o["code"] for o in advice["options"]],
        recipient_id=getattr(ctx.recipient, "id", None),
    )  # fmt: skip
    session.add(case)
    session.flush()
    advice.pop("_hits", None)
    advice.pop("_pitch", None)
    advice["summary"] = chat.greet(advice["summary"], name, turn)
    if not advice.get("suggestions"):
        advice["suggestions"] = chat.suggestions_for(topic, ctx, question or text, [])
    sources = []
    if any(o["code"].startswith("ficha:") for o in advice["options"]) or ctx.facts:
        sources.append("Ficha del producto")
    if ctx.contact is not None or ctx.deal is not None:
        sources.append("Historia de la cuenta")
    if any(o.get("record") for o in advice["options"]) or topic in ("campana", "estrategia"):
        sources.append("Resultados de tu empresa")
    obj = pb.OBJECTION_BY_CODE.get(objection or "")
    return {"case_id": str(case.id), "topic": topic, "topic_label": pb.TOPICS[topic],
            "objection": objection, "objection_label": obj.label if obj else None,
            "formal": ctx.formal, **advice, "sources": sources}  # fmt: skip


def _writing_intent(question: str, history: list[dict[str, Any]]) -> intent.Intent | None:
    """Qué hay que redactar. Un ajuste («más corto», «ahora para un CFO») se suma a lo pedido
    en los mensajes anteriores de la misma redacción."""
    last = history[-1].get("topic") if history else None
    now = intent.understand(question, last_topic=last)
    if now.action is None:
        return None
    if last != "redactar" or (now.action == "redactar" and len(question.split()) > 10):
        return now
    chain: list[str] = []
    for turn in reversed(history):
        if turn.get("topic") != "redactar":
            break
        chain.insert(0, str(turn.get("question") or ""))
    merged = intent.details(chain[0]) if chain else now
    for q in [*chain[1:], question]:
        merged = intent.merge(merged, intent.details(q))
    merged.action = "redactar"
    merged.piece = merged.piece or "correo"
    return merged


def _write(session: Session, ctx: engine.Context, *, user: User, tenant_id: uuid.UUID,
           question: str, wanted: intent.Intent, history: list[dict[str, Any]], name: str,
           turn: int) -> dict[str, Any]:  # fmt: skip
    from orkesta.platform.models import Tenant

    if not allowed(session, tenant_id, "redactar"):
        return {"case_id": None, "topic": "redactar", "topic_label": pb.TOPICS["redactar"],
                "objection": None, "objection_label": None, **engine._ask_more(UPGRADE),
                "sources": [], "suggestions": []}  # fmt: skip
    tenant = session.get(Tenant, tenant_id)
    context_text = " ".join(str(t.get("question") or "") for t in history[-3:]) + " " + question
    advice = writer.write(session, ctx, wanted, context_text, tenant.name if tenant else "")
    case = AdviceCase(tenant_id=tenant_id, user_id=user.id, topic="redactar", objection=None,
                      question=question[:2000],
                      context={k: str(v) for k, v in (("pieza", wanted.piece), ("cargo", wanted.cargo),
                                                       ("industry", wanted.industry)) if v},
                      shown=[o["code"] for o in advice["options"]])  # fmt: skip
    session.add(case)
    session.flush()
    advice["summary"] = chat.greet(advice["summary"], name, turn)
    return {"case_id": str(case.id), "topic": "redactar", "topic_label": pb.TOPICS["redactar"],
            "objection": None, "objection_label": None, "formal": ctx.formal, **advice,
            "sources": ["Ficha del producto"] if ctx.facts else []}  # fmt: skip


def welcome(session: Session, tenant_id: uuid.UUID, user: User) -> dict[str, Any]:
    """Saludo al abrir Concertino, con el nombre de la persona y preguntas para empezar."""
    ctx = engine.Context(tenant_id=tenant_id, user_id=user.id, formal=False,
                         facts=engine.load_facts(session, tenant_id))  # fmt: skip
    name = chat.user_name(user)
    text = (f"Hola, {name}. Soy Concertino, tu asesor de ventas. "
            + ("Conozco tu producto y lo que le funciona a tu empresa. ¿En qué te ayudo hoy?"
               if ctx.facts else "Cuéntame el caso y te ayudo a resolverlo."))  # fmt: skip
    return {"user_name": name, "welcome": text, "starters": chat.starters(ctx)}


def _own_case(session: Session, tenant_id: uuid.UUID, user: User, case_id: uuid.UUID) -> AdviceCase:
    case = session.get(AdviceCase, case_id)
    if case is None or case.tenant_id != tenant_id or case.user_id != user.id:
        raise ConcertinoError("Esa consulta ya no existe.", 404)
    return case


def feedback(
    session: Session, *, tenant_id: uuid.UUID, user: User, case_id: uuid.UUID,
    action: str | None = None, chosen: str | None = None, final_text: str | None = None,
    topic: str | None = None, objection: str | None = None, outcome: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Lo que hizo el vendedor con la sugerencia: así aprende Concertino."""
    from orkesta.brain import registry

    case = _own_case(session, tenant_id, user, case_id)
    if action is not None:
        case.action = action
    if chosen is not None:
        case.chosen = chosen[:40]
    if final_text is not None:
        case.final_text = final_text[:8000]
    if topic is not None and topic in pb.TOPICS and topic != case.topic:
        registry.record_feedback(session, engine.TOPIC_TASK, topic, text=case.question,
                                 predicted=case.topic, tenant_id=tenant_id, user_id=user.id)  # fmt: skip
        case.topic = topic
    if objection is not None and objection in pb.OBJECTION_BY_CODE and objection != case.objection:
        registry.record_feedback(session, engine.OBJECTION_TASK, objection, text=case.question,
                                 predicted=case.objection, tenant_id=tenant_id, user_id=user.id)  # fmt: skip
        case.objection = objection
    if outcome is not None and outcome != "sigue":
        case.outcome = outcome
        case.outcome_at = datetime.now(UTC)
    return {"ok": True}


def history(
    session: Session, tenant_id: uuid.UUID, user: User, limit: int = 30
) -> list[dict[str, Any]]:
    rows = session.scalars(select(AdviceCase).where(
        AdviceCase.tenant_id == tenant_id, AdviceCase.user_id == user.id)
        .order_by(AdviceCase.created_at.desc()).limit(limit))  # fmt: skip
    return [{"id": str(c.id), "topic": c.topic, "topic_label": pb.TOPICS.get(c.topic, c.topic),
             "question": c.question[:200], "action": c.action, "outcome": c.outcome,
             "created_at": c.created_at.isoformat()} for c in rows]  # fmt: skip


# --- Lo que aprende de los resultados ---------------------------------------------------------


LEARN_EVERY = 600  # segundos entre revisiones de resultados por persona
_LEARNED_AT: dict[tuple[Any, Any], float] = {}


def learn_outcomes(session: Session, tenant_id: uuid.UUID, user: User) -> int:
    """Cierra los casos usados del vendedor con lo que pasó después en la cuenta: reunión,
    oportunidad, respuesta positiva o silencio (a los 21 días)."""
    import time

    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.replies import POSITIVE
    from orkesta.crm.models import Activity, Deal

    key = (tenant_id, user.id)
    if time.monotonic() - _LEARNED_AT.get(key, -1e9) < LEARN_EVERY:
        return 0  # revisado hace poco: no repetir consultas en cada pregunta
    _LEARNED_AT[key] = time.monotonic()
    cases = list(session.scalars(select(AdviceCase).where(
        AdviceCase.tenant_id == tenant_id, AdviceCase.user_id == user.id,
        AdviceCase.action.in_(("usada", "editada")), AdviceCase.outcome.is_(None)).limit(200)))  # fmt: skip
    closed = 0
    for case in cases:
        contact_id = (case.context or {}).get("contact_id")
        outcome = None
        if contact_id:
            cid = uuid.UUID(contact_id)
            meeting = session.scalar(select(Activity.id).where(
                Activity.tenant_id == tenant_id, Activity.contact_id == cid,
                Activity.type == "meeting", Activity.created_at > case.created_at).limit(1))  # fmt: skip
            deal = session.scalar(select(Deal.id).where(
                Deal.tenant_id == tenant_id, Deal.contact_id == cid,
                Deal.created_at > case.created_at).limit(1))  # fmt: skip
            reply = session.scalar(select(Recipient.reply_category).where(
                Recipient.tenant_id == tenant_id, Recipient.contact_id == cid,
                Recipient.replied_at > case.created_at, Recipient.reply_category.in_(POSITIVE)).limit(1))  # fmt: skip
            outcome = (
                "reunion"
                if meeting or reply == "reunion"
                else "interesado"
                if (deal or reply)
                else None
            )
        created = case.created_at if case.created_at.tzinfo else case.created_at.replace(tzinfo=UTC)
        if outcome is None and created < engine.stale_cutoff():
            outcome = "sin_respuesta"
        if outcome:
            case.outcome, case.outcome_at = outcome, datetime.now(UTC)
            closed += 1
    return closed


_QUESTION = re.compile(r"[^.!?¿\n]*\?")
_PII = re.compile(r"\S+@\S+|\+?\d[\d\s().-]{6,}\d")
_STOP = {"para", "como", "este", "esta", "esto", "tienen", "tiene", "pueden", "puede", "cual",
         "cuales", "cuanto", "cuando", "donde", "ustedes", "nosotros", "sobre", "hacer", "seria",
         "porque", "tambien", "quien", "nuestro", "nuestra", "estan", "algun", "alguna"}  # fmt: skip


def _words(text: str) -> set[str]:
    return {w for w in fold(text).split() if len(w) >= 3 and w not in _STOP}


def _same(a: set[str], b: set[str]) -> bool:
    """Dos preguntas son la misma si casi todas las palabras de la más corta están en la otra."""
    small = min(len(a), len(b))
    return small >= 2 and len(a & b) / small >= 0.75


def learn_from_campaigns(session: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """Propone a la ficha las objeciones y preguntas que se repiten en las respuestas de las
    campañas de la empresa. Quedan como «aprendido» hasta que alguien las confirme."""
    from orkesta.campaigns.models import Recipient

    with unscoped(session):  # la ficha es de la empresa: aprende de todas sus campañas
        snippets = [s for (s,) in session.execute(select(Recipient.reply_snippet).where(
            Recipient.tenant_id == tenant_id, Recipient.reply_snippet.is_not(None),
            Recipient.reply_category.not_in(("fuera_oficina", "baja")))) if s]  # fmt: skip
    objections: dict[str, int] = defaultdict(int)
    groups: list[tuple[set[str], list[str]]] = []
    for snippet in snippets:
        code, _ = engine.objection_rules(snippet)
        if code and code not in ("silencio",):
            objections[code] += 1
        for q in _QUESTION.findall(snippet):
            q = " ".join(_PII.sub("", q).split()).strip(" ¿?")
            words = _words(q)
            if len(words) < 2 or not 10 <= len(q) <= 200:
                continue
            for seen, found in groups:
                if _same(seen, words):
                    found.append(q)
                    break
            else:
                groups.append((words, [q]))
    existing = list(session.scalars(select(ProductFact).where(
        ProductFact.tenant_id == tenant_id, ProductFact.kind.in_(("objecion", "pregunta")))))  # fmt: skip
    by_type = {(f.meta or {}).get("tipo"): f for f in existing if f.kind == "objecion"}
    asked = [(_words(f.title), f) for f in existing if f.kind == "pregunta"]
    added = updated = 0
    for code, count in objections.items():
        if count < LEARN_MIN_OBJECTIONS:
            continue
        fact = by_type.get(code)
        if fact is None:
            obj = pb.OBJECTION_BY_CODE[code]
            session.add(ProductFact(tenant_id=tenant_id, kind="objecion", title=obj.label,
                                    detail=obj.real, status="aprendido", source="campanas",
                                    evidence=count, meta={"tipo": code}))  # fmt: skip
            added += 1
        elif fact.status != "descartado" and fact.evidence != count:
            fact.evidence = count
            updated += 1
    for words, found in groups:
        if len(found) < LEARN_MIN_QUESTIONS:
            continue
        fact = next((f for w, f in asked if _same(w, words)), None)
        if fact is None:
            session.add(ProductFact(tenant_id=tenant_id, kind="pregunta",
                                    title=found[0][0].upper() + found[0][1:] + "?",
                                    status="aprendido", source="campanas", evidence=len(found)))  # fmt: skip
            added += 1
        elif fact.status != "descartado" and fact.evidence != len(found):
            fact.evidence = len(found)
            updated += 1
    session.flush()
    return {"added": added, "updated": updated}


# --- Ficha del producto -----------------------------------------------------------------------


def fact_dict(f: ProductFact) -> dict[str, Any]:
    return {"id": str(f.id), "kind": f.kind, "kind_label": engine.KIND_LABELS.get(f.kind, f.kind),
            "title": f.title, "detail": f.detail, "answer": f.answer, "status": f.status,
            "source": f.source, "evidence": f.evidence, "meta": f.meta or {},
            "updated_at": f.updated_at.isoformat() if f.updated_at else None}  # fmt: skip


def can_curate(role: str | None) -> bool:
    return role in ("admin", "supervisor")


def facts(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    rows = list(session.scalars(select(ProductFact).where(ProductFact.tenant_id == tenant_id)
                                .order_by(ProductFact.kind, ProductFact.evidence.desc(),
                                          ProductFact.created_at)))  # fmt: skip
    confirmed = {f.kind for f in rows if f.status == "confirmado"}
    return {
        "items": [fact_dict(f) for f in rows if f.status != "descartado"],
        "kinds": [{"kind": k, "label": engine.KIND_LABELS[k]} for k in FACT_KINDS],
        "objection_types": [{"code": o.code, "label": o.label} for o in pb.OBJECTIONS],
        "interview": [q for q in INTERVIEW if q["kind"] not in confirmed],
        "complete": sum(1 for q in INTERVIEW if q["kind"] in confirmed),
        "total": len(INTERVIEW),
    }


def _clean(value: str, size: int) -> str:
    return " ".join((value or "").split())[:size] if size <= 300 else (value or "").strip()[:size]


def save_fact(
    session: Session, *, tenant_id: uuid.UUID, user: User, role: str | None,
    fact_id: uuid.UUID | None = None, kind: str | None = None, title: str | None = None,
    detail: str | None = None, answer: str | None = None, status: str | None = None,
    meta: dict[str, Any] | None = None,
) -> ProductFact:  # fmt: skip
    if fact_id is None:
        if kind not in FACT_KINDS:
            raise ConcertinoError("Tipo de dato no válido.")
        from sqlalchemy import func

        count = int(session.scalar(select(func.count()).select_from(ProductFact)
                                   .where(ProductFact.tenant_id == tenant_id)) or 0)  # fmt: skip
        if count >= MAX_FACTS:
            raise ConcertinoError(f"La ficha admite hasta {MAX_FACTS} datos.")
        if not (title or "").strip():
            raise ConcertinoError("Escribe el dato.")
        # Quien no administra propone: queda pendiente hasta que la administración lo confirme.
        fact = ProductFact(tenant_id=tenant_id, created_by=user.id, kind=kind, title="",
                           status="confirmado" if can_curate(role) else "aprendido",
                           source="manual")  # fmt: skip
        session.add(fact)
    else:
        found = session.get(ProductFact, fact_id)
        if found is None or found.tenant_id != tenant_id:
            raise ConcertinoError("Ese dato ya no existe.", 404)
        if not can_curate(role) and found.created_by != user.id:
            raise ConcertinoError(
                "Solo la administración o la jefatura pueden cambiar la ficha.", 403
            )
        fact = found
    if title is not None:
        fact.title = _clean(title, 300)
    if detail is not None:
        fact.detail = _clean(detail, 4000)
    if answer is not None:
        fact.answer = _clean(answer, 4000)
    if status is not None:
        if status not in ("confirmado", "aprendido", "descartado"):
            raise ConcertinoError("Estado no válido.")
        if status != fact.status and not can_curate(role):
            raise ConcertinoError("Solo la administración o la jefatura confirman la ficha.", 403)
        fact.status = status
    if meta is not None:
        clean = {
            k: str(v)[:60] for k, v in meta.items() if k in ("tipo", "industria", "area") and v
        }
        fact.meta = {**(fact.meta or {}), **clean}
    if fact.kind == "objecion" and not (fact.meta or {}).get("tipo"):
        code, _ = engine.objection_rules(f"{fact.title} {fact.detail}")
        if code:
            fact.meta = {**(fact.meta or {}), "tipo": code}
    session.flush()
    return fact


def delete_fact(
    session: Session, *, tenant_id: uuid.UUID, role: str | None, fact_id: uuid.UUID
) -> None:
    fact = session.get(ProductFact, fact_id)
    if fact is None or fact.tenant_id != tenant_id:
        raise ConcertinoError("Ese dato ya no existe.", 404)
    if not can_curate(role):
        raise ConcertinoError("Solo la administración o la jefatura pueden cambiar la ficha.", 403)
    session.delete(fact)


def import_text(session: Session, *, tenant_id: uuid.UUID, user: User, role: str | None,
                text: str) -> int:  # fmt: skip
    """Lee una propuesta o presentación pegada y propone datos para la ficha (quedan
    pendientes de confirmar). Reconoce frases con cifras como pruebas, precios por la moneda,
    y preguntas como preguntas frecuentes."""
    sentences = [
        " ".join(s.split()) for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()
    ]
    added = 0
    for s in sentences[:200]:
        if not 15 <= len(s) <= 300:
            continue
        f = fold(s)
        if re.search(
            r"(\$|usd|dolares|pesos|cop|mxn|al mes|mensual|anual|por usuario|precio|inversion)", f
        ) and re.search(r"\d", s):
            kind = "precio"
        elif re.search(
            r"\d+ ?(%|x\b|veces|dias|semanas|meses|horas)|paso de \d|de \d+ a \d+",
            f"{f} {s.lower()}",
        ):
            kind = "prueba"
        elif s.rstrip().endswith("?"):
            kind = "pregunta"
        elif re.search(
            r"\b(a diferencia|unic[oa]s?|el unico|la unica|solo nosotros|diferencia)\b", f
        ):
            kind = "diferencial"
        elif re.search(r"\b(ayudamos|logra|permite|reduce|aumenta|ahorra|mejora|acelera)\b", f):
            kind = "valor"
        else:
            continue
        session.add(ProductFact(tenant_id=tenant_id, created_by=user.id, kind=kind,
                                title=s.rstrip("."), status="aprendido", source="documento"))  # fmt: skip
        added += 1
    session.flush()
    return added


META_KEYS = ("tipo", "industria", "area", "vigencia")
MAX_BULK_TEMPLATES = 40


def _bulk_meta(raw: Any) -> dict[str, str]:
    meta: dict[str, str] = {}
    for key, value in (raw or {}).items() if isinstance(raw, dict) else ():
        if key in META_KEYS and isinstance(value, str) and value.strip():
            meta[key] = value.strip()[:40]
    if "vigencia" in meta and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["vigencia"]):
        raise ConcertinoError("La vigencia debe tener el formato AAAA-MM-DD.")
    if "tipo" in meta and meta["tipo"] not in pb.OBJECTION_BY_CODE:
        raise ConcertinoError(f"Tipo de objeción desconocido: {meta['tipo']}.")
    return meta


def bulk_import(session: Session, *, tenant_id: uuid.UUID, user: User, role: str | None,
                data: dict[str, Any]) -> dict[str, int]:  # fmt: skip
    """Carga un paquete de conocimiento ya revisado (JSON) en la ficha de ESTA empresa y,
    si trae plantillas, las deja compartidas con su equipo. Solo administración o jefatura.
    Lo que ya existe (mismo tipo y título, o plantilla del mismo nombre) no se duplica."""
    from orkesta.library.models import TeamTemplate

    if not can_curate(role):
        raise ConcertinoError(
            "Solo la administración o la jefatura pueden cargar conocimiento.", 403
        )
    facts = data.get("facts") or []
    templates = data.get("plantillas") or []
    if not isinstance(facts, list) or not isinstance(templates, list):
        raise ConcertinoError("El archivo no tiene el formato esperado.")
    existing = session.execute(select(ProductFact.kind, ProductFact.title, ProductFact.meta)
                               .where(ProductFact.tenant_id == tenant_id)).all()  # fmt: skip
    # Un mismo dato puede repetirse para distintas industrias o áreas.
    seen = {(k, fold(t), (m or {}).get("industria"), (m or {}).get("area")) for k, t, m in existing}
    room = MAX_FACTS - len(existing)
    added = skipped = 0
    for raw in facts:
        if not isinstance(raw, dict):
            raise ConcertinoError("El archivo no tiene el formato esperado.")
        kind = raw.get("kind")
        title = _clean(str(raw.get("title") or ""), 300)
        if kind not in FACT_KINDS or not title:
            raise ConcertinoError(f"Dato no válido: {title or 'sin título'}.")
        meta = _bulk_meta(raw.get("meta"))
        key = (kind, fold(title), meta.get("industria"), meta.get("area"))
        if key in seen:
            skipped += 1
            continue
        if added >= room:
            raise ConcertinoError(f"La ficha admite hasta {MAX_FACTS} datos.")
        seen.add(key)
        session.add(ProductFact(
            tenant_id=tenant_id, created_by=user.id, kind=kind, title=title,
            detail=_clean(str(raw.get("detail") or ""), 4000),
            answer=_clean(str(raw.get("answer") or ""), 4000),
            status="confirmado", source="importado",
            evidence=max(1, min(int(raw.get("prioridad") or 1), 5)),
            meta=meta,
        ))  # fmt: skip
        added += 1
    names = {fold(n) for n in session.scalars(select(TeamTemplate.name).where(
        TeamTemplate.tenant_id == tenant_id, TeamTemplate.kind == "campaign",
        TeamTemplate.shared.is_(True)))}  # fmt: skip
    created = 0
    for raw in templates[:MAX_BULK_TEMPLATES]:
        name = " ".join(str((raw or {}).get("name") or "").split())[:120]
        steps = []
        for step in (raw or {}).get("steps") or []:
            subject = " ".join(str(step.get("subject") or "").split())[:200]
            body = str(step.get("body") or "").strip()[:8000]
            if subject and body:
                steps.append({"subject": subject, "body": body,
                              "delay_days": max(0, min(int(step.get("delay_days") or 0), 60))})  # fmt: skip
        if not name or not steps:
            raise ConcertinoError(f"Plantilla no válida: {name or 'sin nombre'}.")
        if fold(name) in names:
            skipped += 1
            continue
        names.add(fold(name))
        session.add(TeamTemplate(tenant_id=tenant_id, created_by=user.id, kind="campaign", name=name,
                                 description=" ".join(str(raw.get("description") or "").split())[:300],
                                 payload={"steps": steps}, shared=True))  # fmt: skip
        created += 1
    session.flush()
    return {"added": added, "templates": created, "skipped": skipped}


def stats(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Cifras para el panel Cerebro y para las jefaturas (nunca el contenido)."""
    from sqlalchemy import func

    with unscoped(session):
        rows = session.execute(select(AdviceCase.action, AdviceCase.outcome, func.count())
                               .where(AdviceCase.tenant_id == tenant_id)
                               .group_by(AdviceCase.action, AdviceCase.outcome)).all()  # fmt: skip
    total = sum(n for _, _, n in rows)
    used = sum(n for a, _, n in rows if a in ("usada", "editada"))
    as_is = sum(n for a, _, n in rows if a == "usada")
    closed = sum(n for a, o, n in rows if a in ("usada", "editada") and o)
    wins = sum(n for a, o, n in rows if a in ("usada", "editada") and o in engine.WINS)
    return {"questions": total, "used": used, "used_as_is": as_is, "closed": closed, "wins": wins}
