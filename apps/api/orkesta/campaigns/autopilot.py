# ruff: noqa: E501  (textos para el usuario)
"""Campaña automatizada: de una frase a una campaña lista para aprobar.

El usuario escribe a quién quiere escribirle («gerentes de compras de supermercados en Colombia,
para 10 reuniones») y ORKESTA usa los módulos que ya existen, uno detrás de otro:

1. Buscar personas (prospección: índice propio y web).
2. Completar datos (enriquecimiento, que ya estima y verifica correos).
3. Validar correos (con el cupo del plan).
4. Armar la lista y la audiencia (solo correos confiables por defecto).
5. Concertino escribe la secuencia con la ficha del producto.
6. Revisión anti-spam.

La campaña queda en borrador: nada se envía sin la aprobación del usuario. Cada etapa es un
trabajo aparte en la cola (así ninguno se acerca al tiempo máximo de un trabajo) y es
reentrante: si una etapa se repite, retoma donde iba.
"""

from __future__ import annotations

import copy
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns.models import Campaign
from orkesta.core.db import set_context
from orkesta.core.jobs import enqueue, handler
from orkesta.engine.normalize.text import fold
from orkesta.engine.taxonomy import linkedin as li

AUTO_JOB = "campaign.autopilot"
STEPS = [
    ("buscar", "Buscando personas"),
    ("completar", "Completando datos"),
    ("validar", "Validando correos"),
    ("lista", "Armando la lista"),
    ("escribir", "Concertino escribe la secuencia"),
    ("revisar", "Revisión anti-spam"),
]
BENCH_POSITIVE, BENCH_MEETING = 0.05, 0.6
MAX_TARGET, DEFAULT_TARGET = 500, 100

_FUNCTIONS: list[tuple[str, str]] = [
    ("compras", r"\b(compras|abastecimiento|procurement|adquisiciones|purchasing|suministros)\b"),
    ("finanzas", r"\b(financier[oa]s?|finanzas|cfo|tesorer[oi]a?|tesorer[oa])\b"),
    ("contabilidad", r"\b(contab\w*|contador(es|as)?)\b"),
    ("ti", r"\b(ti|tecnologia|sistemas|cio|cto|informatica|it)\b"),
    ("marketing", r"\b(marketing|mercadeo|cmo|mercadotecnia)\b"),
    ("ventas", r"\b(ventas|comercial(es)?|cso|vendedores)\b"),
    ("recursos_humanos", r"\b(recursos humanos|rrhh|rr hh|talento humano|gestion humana|people)\b"),
    ("operaciones", r"\b(operaciones|coo|logistica|produccion|planta)\b"),
    ("legal", r"\b(legal(es)?|juridic[oa]s?|abogad[oa]s?)\b"),
    ("atencion_cliente", r"\b(servicio al cliente|atencion al cliente|customer)\b"),
    ("calidad", r"\bcalidad\b"),
    ("proyectos", r"\b(proyectos|pmo)\b"),
    (
        "desarrollo_empresarial",
        r"\b(desarrollo de negocios|nuevos negocios|business development)\b",
    ),
]
_SENIORITY: list[tuple[str, str]] = [
    (
        "cxo",
        r"\b(ceo|cfo|cio|cto|coo|cmo|presidente|presidenta|gerente general|director general|c level)\b",
    ),
    ("propietario", r"\b(duen[oa]s?|propietari[oa]s?|socios?|fundador(es|as)?)\b"),
    ("vp", r"\b(vicepresident\w*|vp)\b"),
    ("director", r"\b(director(es|as|a)?|head)\b"),
    (
        "gestion_experiencia",
        r"\b(gerentes?|jef[ea]s?|managers?|lider(es)?|coordinador(es|as|a)?)\b",
    ),
]
# Sector de la biblioteca de dolores → sectores de la búsqueda (por código o por parte del código).
_SECTOR_MAP: dict[str, tuple[str, ...]] = {
    "banca_finanzas": ("banca", "servicios_financieros", "tecnologia_financiera_fintech", "intermediacion_crediticia"),
    "seguros": ("aseguradoras", "seguros"),
    "telecomunicaciones": ("telecomunicaciones", "operadores_de_telecomunicaciones"),
    "tecnologia_software": ("desarrollo_de_software", "software_como_servicio_saas", "servicios_y_consultoria_de_ti", "tecnologia_informacion_e_internet"),
    "farmaceutica_salud": ("fabricacion_de_productos_farmaceuticos", "~hospital", "~sanitari"),
    "alimentos_bebidas": ("alimentacion_y_bebidas", "fabricacion_de_alimentos_y_bebidas", "servicios_de_alimentos_y_bebidas", "lacteos", "fabricacion_de_productos_lacteos", "produccion_alimentaria"),
    "agroindustria": ("agricultura", "ganaderia"),
    "mineria_petroleo": ("mineria", "petroleo_y_gas", "petroleo_gas_y_mineria"),
    "energia": ("servicios_publicos", "generacion_de_energia_electrica", "energia_renovable_y_medio_ambiente"),
    "automotriz": ("~vehiculo", "~automo"),
    "transporte_logistica": ("transporte_de_mercancias_y_paquetes", "~logistic"),
    "construccion_inmobiliario": ("~construccion", "~inmobiliari"),
    "retail_consumo": ("comercio_al_por_menor", "comercio_al_por_menor_de_comestibles"),
    "textil_moda": ("industria_textil_y_moda", "fabricacion_de_productos_textiles", "comercio_al_por_menor_de_prendas_de_vestir_y_moda"),
    "quimica_materiales": ("fabricacion_de_productos_quimicos", "fabricacion_de_productos_de_plastico"),
    "manufactura_industrial": ("industria_manufacturera", "fabricacion_de_maquinaria"),
    "medios_entretenimiento": ("~medios_de_comunicacion", "~radio", "~television"),
    "turismo_hoteleria": ("~hotel", "~turis", "~viaje"),
    "educacion": ("~educa",),
    "gobierno": ("~administracion_publica", "~gobierno"),
    "ong": ("~sin_animo", "~sin_fines", "~benefic"),
    "servicios_profesionales": ("consultoria_y_servicios_a_empresas", "~jurid"),
}  # fmt: skip
_LEVELS = {
    "cxo": "Nivel C (CEO, CFO…)",
    "propietario": "Dueños y socios",
    "vp": "Vicepresidentes",
    "director": "Directores",
    "gestion_experiencia": "Gerentes y jefes",
}
_LATAM = ("CO", "MX", "PE", "CL", "AR", "EC", "PA", "CR", "GT", "DO", "UY", "BO", "PY", "SV", "HN")


def _sector_codes(code: str) -> list[str]:
    out: list[str] = []
    for item in _SECTOR_MAP.get(code, ()):
        if item.startswith("~"):
            out += [k for k in li.SECTORS if item[1:] in k]
        elif item in li.SECTORS:
            out.append(item)
    return list(dict.fromkeys(out))[:12]


def _countries(folded: str) -> list[str]:
    from orkesta.engine.normalize.location import COUNTRIES
    from orkesta.research.wikidata import COUNTRY_QIDS

    if re.search(r"\b(latam|latinoamerica|america latina|latino america)\b", folded):
        return [c for c in _LATAM if c in COUNTRY_QIDS]
    found = []
    for code in COUNTRY_QIDS:
        name, aliases = COUNTRIES.get(code, ("", ()))
        for alias in (fold(name).lower(), *aliases):
            if (len(alias) >= 4 or alias in ("eeuu", "usa")) and re.search(
                rf"\b{re.escape(alias)}\b", folded
            ):
                found.append(code)
                break
    return found


def _sizes(folded: str) -> list[str]:
    bands = [band for band, _ in li.HEADCOUNT]

    def low(band: str) -> int:
        return int(re.sub(r"\D", "", band.split("-")[0].rstrip("+")) or 0)

    if re.search(r"\b(pymes?|pequenas y medianas|medianas empresas)\b", folded):
        return [b for b in bands if 11 <= low(b) <= 51]
    if re.search(r"\b(grandes empresas|corporativos|corporaciones|multinacionales)\b", folded):
        return [b for b in bands if low(b) >= 1001]
    m = re.search(
        r"(?:\+\s?|mas de |desde )(\d[\d.]*)\s*(personas|empleados|trabajadores|colaboradores)",
        folded,
    )
    if m:
        n = int(m.group(1).replace(".", ""))
        return [
            b for b in bands if low(b) >= n or (b.endswith("+") and low(b) >= n) or low(b) + 1 > n
        ]
    return []


def parse(text: str, *, default_country: str = "CO") -> dict[str, Any]:
    """Lo que se entiende de la frase: filtros de búsqueda, meta y cuántas personas buscar."""
    from orkesta.concertino.strategy import sector_of
    from orkesta.engine.normalize.location import COUNTRIES

    folded = fold(text).lower()
    functions = [code for code, rx in _FUNCTIONS if re.search(rx, folded)]
    seniority = [code for code, rx in _SENIORITY if re.search(rx, folded)]
    sector = sector_of(text)
    sectors = _sector_codes(sector) if sector else []
    countries = _countries(folded) or [default_country]
    sizes = _sizes(folded)
    meetings = re.search(r"(\d{1,3})\s*(reuniones|citas|demos|meetings)", folded)
    people = re.search(r"(\d{1,4})\s*(contactos|personas|prospectos|leads)", folded)
    goal = int(meetings.group(1)) if meetings else None
    if goal:
        target = math.ceil(goal / (BENCH_POSITIVE * BENCH_MEETING))
    elif people:
        target = int(people.group(1))
    else:
        target = DEFAULT_TARGET
    target = max(10, min(MAX_TARGET, target))
    chips = [_LEVELS.get(s, li.SENIORITY[s][0]) for s in seniority] + [
        li.FUNCTIONS[f][0] for f in functions
    ]
    if sector:
        from orkesta.concertino.strategy import _label

        label = re.sub(r"^(el|la|los|las) ", "", _label(sector))
        chips.append(label[0].upper() + label[1:])
    names = [COUNTRIES[c][0] for c in countries if c in COUNTRIES]
    chips += names if len(names) <= 4 else [f"{len(names)} países de Latinoamérica"]
    if sizes:
        chips.append(
            f"Empresas de {sizes[0].split('-')[0]}+ personas"
            if len(sizes) > 1
            else f"Empresas de {sizes[0]} personas"
        )
    missing = []
    if not functions and not sectors:
        missing.append(
            "Dime al menos el cargo o el área (compras, finanzas, RR. HH.…) o el sector."
        )
    return {
        "filters": {"mode": "leads", "countries": countries, "functions": functions,
                    "seniority": seniority or list(li.DEFAULT_SENIORITY), "sectors": sectors,
                    "sizes": sizes, "include_base": True},
        "goal": goal, "target": target, "chips": chips, "missing": missing,
        "sector": sector,
    }  # fmt: skip


def _state(steps: list[dict[str, Any]], key: str, state: str, detail: str = "") -> None:
    for s in steps:
        if s["key"] == key:
            s["state"] = state
            if detail:
                s["detail"] = detail


def start(session: Session, *, tenant_id: uuid.UUID, user: Any, text: str,
          mailbox_id: uuid.UUID | None = None) -> Campaign:  # fmt: skip
    """Crea la campaña en borrador y pone en marcha la preparación."""
    from orkesta.campaigns.service import CampaignError, create_campaign
    from orkesta.data.models import Contact

    text = " ".join(text.split())[:400]
    country = session.scalar(
        select(Contact.country_code).where(Contact.tenant_id == tenant_id, Contact.country_code.is_not(None))
        .group_by(Contact.country_code).order_by(func.count().desc()).limit(1)
    )  # fmt: skip
    plan = parse(text, default_country=(country or "CO").upper()[:2])
    if plan["missing"]:
        raise CampaignError(plan["missing"][0])
    active = session.scalar(
        select(func.count()).select_from(Campaign).where(
            Campaign.tenant_id == tenant_id, Campaign.settings["auto"]["state"].astext == "preparing")
    )  # fmt: skip
    if active and active >= 2:
        raise CampaignError(
            "Ya hay 2 campañas automatizadas preparándose. Espera a que termine una.", 429
        )
    campaign = create_campaign(session, tenant_id=tenant_id, user=user,
                               name=f"Automatizada · {text[:120]}")  # fmt: skip
    if mailbox_id:
        campaign.mailbox_id = mailbox_id
    campaign.audience = {"list_id": None, "filters": {}, "contact_ids": [],
                         "include_estimated": False, "quality": "confiables"}  # fmt: skip
    campaign.settings = {**campaign.settings, "auto": {
        "brief": text, "state": "preparing", "plan": plan, "created_by": str(user.id),
        "steps": [{"key": k, "title": t, "detail": "", "state": "wait"} for k, t in STEPS],
        "started_at": datetime.now(UTC).isoformat(),
    }}  # fmt: skip
    session.flush()
    enqueue(session, tenant_id=tenant_id, kind=AUTO_JOB,
            payload={"campaign_id": str(campaign.id), "stage": "buscar"}, max_attempts=1)  # fmt: skip
    return campaign


# --- Etapas -----------------------------------------------------------------------------------


def _load(
    session: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> tuple[Campaign, dict[str, Any]]:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != tenant_id:
        raise LookupError("campaña inexistente")
    # Copia profunda: si se modificara la lista de pasos en su lugar, SQLAlchemy no vería el cambio.
    return campaign, copy.deepcopy(dict(campaign.settings.get("auto") or {}))


def _save(campaign: Campaign, auto: dict[str, Any]) -> None:
    campaign.settings = {**campaign.settings, "auto": auto}


def _next(session: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID, stage: str) -> None:
    enqueue(session, tenant_id=tenant_id, kind=AUTO_JOB,
            payload={"campaign_id": str(campaign_id), "stage": stage}, max_attempts=1)  # fmt: skip


def _stage_search(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, campaign_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from orkesta.research.agent import run_research
    from orkesta.research.models import ResearchItem, ResearchRun
    from orkesta.research.service import _summary, save_people, validate_filters

    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        plan = auto["plan"]
        clean = validate_filters(plan["filters"])
        clean["limit"] = min(1000, max(30, plan["target"] * 2 // len(clean["countries"])))
        run = ResearchRun(tenant_id=tenant_id, created_by=user_id, filters=clean, summary=_summary(clean),
                          progress={"step": "en_cola", "done": 0, "total": 0, "log": []})  # fmt: skip
        session.add(run)
        _state(auto["steps"], "buscar", "run")
        _save(campaign, auto)
        session.flush()
        run_id = run.id
    run_research(factory, tenant_id, {"run_id": str(run_id)})
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        found = session.scalars(
            select(ResearchItem.id).where(ResearchItem.tenant_id == tenant_id, ResearchItem.run_id == run_id,
                                          ResearchItem.kind == "person")
            .order_by(ResearchItem.score.desc(), ResearchItem.created_at)
        ).all()  # fmt: skip
        total = len(found)
        chosen = list(found[: auto["plan"]["target"]])
        if not chosen:
            auto["state"] = "failed"
            auto["error"] = (
                "No encontré personas con esa descripción. Prueba con un cargo o un sector más amplio."
            )
            _state(auto["steps"], "buscar", "failed", "0 personas encontradas")
            _save(campaign, auto)
            _next(session, tenant_id, campaign_id, "revisar")
            return
        saved = save_people(session, tenant_id=tenant_id, user_id=user_id, run_id=run_id,
                            item_ids=list(chosen), new_list=campaign.name.replace("Automatizada · ", "")[:120])  # fmt: skip
        list_id = (saved.get("list") or {}).get("id")
        auto["run_id"], auto["list_id"] = str(run_id), str(list_id) if list_id else None
        _state(auto["steps"], "buscar", "done", f"{total} encontradas · {len(chosen)} elegidas")
        _save(campaign, auto)
        _next(session, tenant_id, campaign_id, "completar")


def _list_ids(session: Session, tenant_id: uuid.UUID, auto: dict[str, Any]) -> list[uuid.UUID]:
    from orkesta.data.models import ListMember

    if not auto.get("list_id"):
        return []
    return list(session.scalars(
        select(ListMember.contact_id).where(ListMember.list_id == uuid.UUID(auto["list_id"]))
    ).all())  # fmt: skip


def _stage_enrich(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, campaign_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from orkesta.data.models import Contact
    from orkesta.enrichment.agent import run_enrichment
    from orkesta.enrichment.models import EnrichmentRun

    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        ids = _list_ids(session, tenant_id, auto)
        run = EnrichmentRun(tenant_id=tenant_id, created_by=user_id,
                            scope={"profiles": True, "ids": [str(i) for i in ids]},
                            summary=f"Campaña automatizada: {len(ids)} contactos",
                            progress={"done": 0, "total": 0, "log": []})  # fmt: skip
        session.add(run)
        _state(auto["steps"], "completar", "run")
        _save(campaign, auto)
        session.flush()
        run_id = run.id
    run_enrichment(factory, tenant_id, {"run_id": str(run_id)})
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        ids = _list_ids(session, tenant_id, auto)
        with_email = session.scalar(select(func.count()).select_from(Contact).where(
            Contact.id.in_(ids), Contact.email.is_not(None))) or 0  # fmt: skip
        _state(auto["steps"], "completar", "done", f"{with_email} de {len(ids)} con correo")
        _save(campaign, auto)
        _next(session, tenant_id, campaign_id, "validar")


def _stage_validate(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, campaign_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from orkesta.data.models import Contact
    from orkesta.validation.agent import run_validation
    from orkesta.validation.models import ValidationRun

    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        ids = list(session.scalars(select(Contact.id).where(
            Contact.id.in_(_list_ids(session, tenant_id, auto)), Contact.email.is_not(None),
            Contact.email_status.notin_(("valido", "invalido")))).all())  # fmt: skip
        _state(auto["steps"], "validar", "run")
        _save(campaign, auto)
        run_id = None
        if ids:
            run = ValidationRun(tenant_id=tenant_id, created_by=user_id,
                                scope={"find_missing": True, "ids": [str(i) for i in ids]},
                                summary="Campaña automatizada", progress={"done": 0, "total": 0, "log": []})  # fmt: skip
            session.add(run)
            session.flush()
            run_id = run.id
    if run_id is not None:
        run_validation(factory, tenant_id, {"run_id": str(run_id)})
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        _state(auto["steps"], "validar", "done")
        _save(campaign, auto)
        _next(session, tenant_id, campaign_id, "lista")


def _stage_list(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, campaign_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from orkesta.campaigns.service import _audience_query, _eligible

    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        campaign.audience = {**campaign.audience, "list_id": auto.get("list_id")}
        contacts = session.scalars(_audience_query(tenant_id, campaign.audience)).all()
        keep, counts = _eligible(session, tenant_id, campaign.audience, contacts)
        companies = len({(c.company_name or "").lower() for c in keep if c.company_name})
        auto["counts"] = {"contacts": len(keep), "companies": companies, "total": counts["total"],
                          "excluded": counts["total"] - len(keep)}  # fmt: skip
        detail = f"{len(keep)} verificados · {counts['total'] - len(keep)} quedan fuera"
        _state(auto["steps"], "lista", "done", detail)
        if not keep:
            auto["state"] = "failed"
            auto["error"] = ("Ningún correo quedó verificado. Puedes elegir «Todos los correos "
                             "válidos» en «Audiencia», con más riesgo de rebote.")  # fmt: skip
        _save(campaign, auto)
        _next(session, tenant_id, campaign_id, "revisar" if not keep else "escribir")


def _stage_write(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, campaign_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from orkesta.campaigns.service import clean_steps
    from orkesta.concertino import intent, writer
    from orkesta.concertino.service import _context
    from orkesta.platform.models import Tenant, User

    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        user = session.get(User, user_id)
        tenant = session.get(Tenant, tenant_id)
        if user is None:
            raise LookupError("usuaria inexistente")
        ctx = _context(session, tenant_id=tenant_id, user=user, contact_id=None, deal_id=None,
                       recipient_id=None, campaign_id=None, trato="")  # fmt: skip
        ask = f"Escribe una secuencia de 3 correos para {auto['brief']}"
        wanted = intent.details(ask)
        wanted.action, wanted.piece, wanted.count = "redactar", "secuencia", 3
        draft = writer.write(session, ctx, wanted, ask, tenant.name if tenant else "")
        action = next((a for a in draft.get("actions") or [] if a.get("kind") == "campana"), None)
        if action and action.get("steps"):
            campaign.steps = clean_steps(list(action["steps"]))
            _state(
                auto["steps"],
                "escribir",
                "done",
                f"{len(campaign.steps)} correos con tu ficha de producto",
            )
        else:
            _state(auto["steps"], "escribir", "failed",
                   "Completa tu producto con Concertino para que escriba la secuencia; dejé una plantilla para editar.")  # fmt: skip
        _save(campaign, auto)
        _next(session, tenant_id, campaign_id, "revisar")


def _stage_review(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, campaign_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from orkesta.campaigns import spamcheck
    from orkesta.notifications.service import notify

    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id, user_id=user_id)
        campaign, auto = _load(session, tenant_id, campaign_id)
        report = spamcheck.check_steps(campaign.steps or [])
        auto["spam"] = {"score": report["score"], "level": report["level"]}
        _state(auto["steps"], "revisar", "done", f"{report['score']}/100")
        if auto.get("state") != "failed":
            auto["state"] = "ready"
        auto["finished_at"] = datetime.now(UTC).isoformat()
        _save(campaign, auto)
        notify(session, tenant_id=tenant_id, user_id=user_id, kind="campana",
               title="Tu campaña automatizada está lista para revisar" if auto["state"] == "ready"
               else "Tu campaña automatizada necesita un ajuste",
               body=campaign.name, link=f"/campanas/{campaign.id}")  # fmt: skip


_STAGES = {"buscar": _stage_search, "completar": _stage_enrich, "validar": _stage_validate,
           "lista": _stage_list, "escribir": _stage_write, "revisar": _stage_review}  # fmt: skip


def _fail(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any], detail: str
) -> None:
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        try:
            campaign, auto = _load(session, tenant_id, uuid.UUID(payload["campaign_id"]))
        except LookupError:
            return
        auto["state"] = "failed"
        auto["error"] = "Algo falló al preparar la campaña. Puedes terminarla a mano o crear otra."
        _state(auto["steps"], str(payload.get("stage")), "failed")
        _save(campaign, auto)


@handler(AUTO_JOB, on_failure=_fail)
def run_stage(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    campaign_id = uuid.UUID(payload["campaign_id"])
    stage = str(payload.get("stage"))
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        try:
            _campaign, auto = _load(session, tenant_id, campaign_id)
        except LookupError:
            return
        if auto.get("state") != "preparing" and stage != "revisar":
            return  # cancelada, fallida o ya lista
        done = {s["key"] for s in auto.get("steps", []) if s["state"] == "done"}
        if stage in done:
            return  # etapa repetida (reintento de la cola): ya se hizo
        user_id = uuid.UUID(auto["created_by"])
    _STAGES[stage](factory, tenant_id, campaign_id, user_id)


# --- Lista viva: los contactos que se suman a la lista entran a la campaña ------------------

SYNC_EVERY = 300  # segundos entre revisiones de la lista de cada campaña
_LAST_SYNC: dict[uuid.UUID, float] = {}


def sync_list(session: Session, campaign: Campaign) -> int:
    """Campaña automatizada en marcha: los contactos nuevos de su lista entran a la secuencia
    (con las mismas reglas de la audiencia: solo correos verificados por defecto). A los que
    llegan sin correo se les busca y a los que llegan sin verificar se les verifica primero. Devuelve cuántos entraron."""
    import time

    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.service import MAX_RECIPIENTS, _audience_query, _eligible
    from orkesta.data.models import Contact
    from orkesta.notifications.service import notify

    auto = dict(campaign.settings.get("auto") or {})
    list_id = (campaign.audience or {}).get("list_id")
    if not auto or not list_id or campaign.status not in ("active", "paused"):
        return 0
    stamp = time.monotonic()
    if stamp - _LAST_SYNC.get(campaign.id, -SYNC_EVERY) < SYNC_EVERY:
        return 0
    _LAST_SYNC[campaign.id] = stamp
    already = select(Recipient.contact_id).where(Recipient.campaign_id == campaign.id)
    total = session.scalar(select(func.count()).select_from(Recipient).where(
        Recipient.campaign_id == campaign.id)) or 0  # fmt: skip
    new = session.scalars(
        _audience_query(campaign.tenant_id, campaign.audience)
        .where(Contact.id.notin_(already)).limit(max(0, MAX_RECIPIENTS - total))
    ).all()  # fmt: skip
    if not new:
        return 0
    keep, _counts = _eligible(session, campaign.tenant_id, campaign.audience, new)
    start = datetime.now(UTC)
    for c in keep:
        session.add(Recipient(tenant_id=campaign.tenant_id, campaign_id=campaign.id,
                              contact_id=c.id, email=c.email, next_send_at=start))  # fmt: skip
    # Los que llegaron sin correo: se buscan y validan una vez (después entran solos).
    asked = set(auto.get("enrich_asked") or [])
    missing = [c.id for c in new if not c.email and str(c.id) not in asked][:500]
    if (
        missing
        and auto.get("created_by")
        and _enrich_later(session, campaign, missing, uuid.UUID(auto["created_by"]))
    ):
        auto["enrich_asked"] = [*list(asked), *(str(i) for i in missing)][-5000:]
    # Los que llegaron con un correo sin verificar: se verifican una vez (después entran solos
    # si resultan válidos; la campaña automatizada solo envía a correos verificados).
    checked = set(auto.get("verify_asked") or [])
    unverified = [c.id for c in new if c.email and c.email_status not in ("valido", "invalido")
                  and str(c.id) not in checked][:500]  # fmt: skip
    if unverified and auto.get("created_by"):
        from orkesta.validation.router import start_for

        if start_for(session, campaign.tenant_id, uuid.UUID(auto["created_by"]), unverified,
                     f"Nuevos de la lista de «{campaign.name[:80]}»"):  # fmt: skip
            auto["verify_asked"] = [*list(checked), *(str(i) for i in unverified)][-5000:]
    if keep:
        auto["added_later"] = int(auto.get("added_later") or 0) + len(keep)
        notify(session, tenant_id=campaign.tenant_id, user_id=campaign.created_by, kind="campana",
               title=f"{len(keep)} contactos nuevos de la lista entraron a «{campaign.name[:80]}»",
               link=f"/campanas/{campaign.id}")  # fmt: skip
    campaign.settings = {**campaign.settings, "auto": auto}
    return len(keep)


def _enrich_later(
    session: Session, campaign: Campaign, ids: list[uuid.UUID], user_id: uuid.UUID
) -> bool:
    from orkesta.enrichment.agent import ENRICH_JOB
    from orkesta.enrichment.models import EnrichmentRun

    busy = session.scalar(select(func.count()).select_from(EnrichmentRun).where(
        EnrichmentRun.tenant_id == campaign.tenant_id,
        EnrichmentRun.status.in_(("queued", "running"))))  # fmt: skip
    if busy:
        return False  # se intenta en la próxima revisión
    run = EnrichmentRun(tenant_id=campaign.tenant_id, created_by=user_id,
                        scope={"profiles": True, "ids": [str(i) for i in ids]},
                        summary=f"Nuevos de la lista de «{campaign.name[:80]}»",
                        progress={"done": 0, "total": 0, "log": []})  # fmt: skip
    session.add(run)
    session.flush()
    enqueue(session, tenant_id=campaign.tenant_id, kind=ENRICH_JOB,
            payload={"run_id": str(run.id)}, max_attempts=1)  # fmt: skip
    return True
