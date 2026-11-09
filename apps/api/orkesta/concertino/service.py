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

from orkesta.concertino import engine
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
) -> dict[str, Any]:  # fmt: skip
    question = (question or "").strip()[:4000]
    ctx = _context(session, tenant_id=tenant_id, user=user, contact_id=contact_id,
                   deal_id=deal_id, recipient_id=recipient_id, campaign_id=campaign_id,
                   trato=trato)  # fmt: skip
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
    elif topic == "producto":
        advice = engine.advise_product(session, ctx, text)
    else:
        advice = engine.advise_general(session, ctx, text)
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


def learn_outcomes(session: Session, tenant_id: uuid.UUID, user: User) -> int:
    """Cierra los casos usados del vendedor con lo que pasó después en la cuenta: reunión,
    oportunidad, respuesta positiva o silencio (a los 21 días)."""
    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.replies import POSITIVE
    from orkesta.crm.models import Activity, Deal

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
        count = len(
            list(session.scalars(select(ProductFact.id).where(ProductFact.tenant_id == tenant_id)))
        )
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
        elif re.search(r"\d+ ?(%|x\b|veces|dias|semanas|meses|horas)|paso de \d|de \d+ a \d+", f"{f} {s.lower()}"):
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
