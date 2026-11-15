# ruff: noqa: E501
"""Concertino crea estrategias comerciales y conoce los dolores de cada cargo y sector.

- Biblioteca de dolores: qué le preocupa a cada sector (además de lo que le preocupa a cada área,
  que vive en ``copywriter.knowledge.PERSONAS``), qué eventos abren la puerta a una venta y quién
  suele comprar. Redactada por ORKESTA a partir del oficio de ventas B2B en Latinoamérica.
- «Créame una estrategia»: a quién apuntar, qué decirle a cada uno, la cadencia por canal y
  cuánta actividad hace falta para la meta de reuniones, con las tasas reales de la empresa si
  ya las tiene y, si no, con referencias del mercado.
"""

from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.concertino import engine
from orkesta.engine.normalize.text import fold

# Sector → (dolores, eventos que abren la puerta, áreas que suelen comprar, palabras que lo delatan).
SECTORS: dict[str, dict[str, Any]] = {
    "banca_finanzas": {
        "pains": ["la presión de las fintech sobre márgenes y clientes jóvenes", "cumplir la regulación (SARLAFT, KYC, protección de datos) sin frenar el negocio", "el costo de adquirir y retener clientes digitales", "sistemas heredados que hacen lento cada cambio"],
        "triggers": ["nueva regulación o auditoría", "lanzamiento de un producto digital", "fusión o cambio de presidente", "resultados trimestrales con caída de margen"],
        "buyers": ["tecnologia", "seguridad", "transformacion_digital", "finanzas", "comercial"],
        "keys": r"\b(banco|bancos|banca|financier[oa]s?|fintech|cooperativas?|fondos? de pensiones|credito)\b"},
    "seguros": {
        "pains": ["la baja digitalización de la experiencia del asegurado", "el fraude en reclamaciones", "la dependencia de intermediarios para vender", "la retención en renovaciones"],
        "triggers": ["temporada de renovaciones", "nuevo canal digital o bancaseguros", "cambio regulatorio"],
        "buyers": ["comercial", "tecnologia", "operaciones", "atencion_cliente"],
        "keys": r"\b(seguros?|asegurador[ae]s?|aseguradoras?|corredores de seguros)\b"},
    "telecomunicaciones": {
        "pains": ["la guerra de precios y la caída del ingreso por usuario", "la cancelación de clientes (churn)", "el costo de desplegar y mantener red", "la experiencia en canales de servicio saturados"],
        "triggers": ["subasta de espectro o 5G", "campañas de portabilidad", "cambio de CEO o de país"],
        "buyers": ["tecnologia", "comercial", "atencion_cliente", "marketing"],
        "keys": r"\b(telecomunicaciones|telco|operador(es)? (movil|de telefonia)|telefonia|internet)\b"},
    "tecnologia_software": {
        "pains": ["crecer ingresos recurrentes con un costo de venta sostenible", "la rotación de talento técnico", "diferenciarse en un mercado saturado", "la cancelación de clientes en los primeros meses"],
        "triggers": ["ronda de inversión", "expansión a otro país", "contratación de un nuevo líder comercial", "lanzamiento de producto"],
        "buyers": ["direccion", "comercial", "marketing", "tecnologia"],
        "keys": r"\b(software|saas|startups?|tecnologic[oa]s?|empresas de tecnologia|desarrollo de software)\b"},
    "farmaceutica_salud": {
        "pains": ["la regulación sanitaria y la trazabilidad", "el acceso a médicos y canales que cada vez reciben menos visitas", "los costos de operación clínica", "la cartera con aseguradoras y el sistema de salud"],
        "triggers": ["registro sanitario o lanzamiento", "auditoría o habilitación", "apertura de sede"],
        "buyers": ["operaciones", "comercial", "compras", "tecnologia"],
        "keys": r"\b(farmaceutic[oa]s?|laboratorios?|clinicas?|hospitales?|salud|ips|eps)\b"},
    "alimentos_bebidas": {
        "pains": ["los quiebres de inventario en el punto de venta", "el alza de materias primas que aprieta el margen", "la ejecución en el canal tradicional (tiendas de barrio)", "la vida útil y la logística de frío"],
        "triggers": ["temporada alta o lanzamiento de producto", "nuevo canal o cadena", "alza de costos"],
        "buyers": ["comercial", "supply_chain", "operaciones", "marketing"],
        "keys": r"\b(alimentos?|bebidas?|lacteos?|consumo masivo|snacks?|panaderias?|restaurantes?)\b"},
    "agroindustria": {
        "pains": ["la volatilidad de precios y del clima", "la trazabilidad que exigen los compradores internacionales", "la logística desde zonas rurales", "el acceso a financiación"],
        "triggers": ["temporada de cosecha", "nueva certificación de exportación", "inversión en planta"],
        "buyers": ["operaciones", "supply_chain", "finanzas", "direccion"],
        "keys": r"\b(agro|agroindustria|agricola|cafe|flores|palma|banano|ganaderia)\b"},
    "mineria_petroleo": {
        "pains": ["la seguridad de las personas en faena", "el costo de mantenimiento y las paradas no programadas", "las licencias y la relación con comunidades", "la eficiencia energética"],
        "triggers": ["nuevo proyecto o expansión", "parada de planta programada", "incidente de seguridad"],
        "buyers": ["operaciones", "compras", "supply_chain", "seguridad"],
        "keys": r"\b(mineri[ao]|mineras?|petroleo|petroler[ao]s?|oil|gas|hidrocarburos)\b"},
    "energia": {
        "pains": ["la transición energética y sus inversiones", "las pérdidas de energía y el fraude", "la atención a usuarios masivos", "la regulación tarifaria"],
        "triggers": ["subasta de energía renovable", "cambio regulatorio", "plan de inversión"],
        "buyers": ["operaciones", "tecnologia", "atencion_cliente", "finanzas"],
        "keys": r"\b(energia|electric[ao]s?|servicios publicos|renovables?|utilities)\b"},
    "automotriz": {
        "pains": ["la rotación de inventario en concesionarios", "la posventa como fuente de margen", "la llegada de marcas chinas y eléctricas", "la financiación al cliente final"],
        "triggers": ["lanzamiento de modelo", "apertura de vitrina", "feria automotriz"],
        "buyers": ["comercial", "marketing", "operaciones"],
        "keys": r"\b(automotriz|concesionari[oa]s?|autos?|vehiculos?|motos?)\b"},
    "transporte_logistica": {
        "pains": ["el costo del combustible y de la última milla", "la visibilidad de los envíos en tiempo real", "el robo y la siniestralidad", "la ocupación de la flota"],
        "triggers": ["temporada alta (fin de año)", "nuevo cliente grande o centro de distribución", "alza de combustible"],
        "buyers": ["operaciones", "supply_chain", "tecnologia", "finanzas"],
        "keys": r"\b(logistic[ao]s?|transporte|carga|courier|ultima milla|operadores logisticos)\b"},
    "construccion_inmobiliario": {
        "pains": ["los sobrecostos y retrasos de obra", "la preventa y la financiación de proyectos", "la gestión de contratistas", "los trámites y licencias"],
        "triggers": ["lanzamiento de proyecto", "inicio de obra", "cambio en tasas de interés"],
        "buyers": ["operaciones", "finanzas", "compras", "comercial"],
        "keys": r"\b(construccion|constructoras?|inmobiliari[ao]s?|vivienda|obras?)\b"},
    "retail_consumo": {
        "pains": ["la rentabilidad por metro cuadrado", "el inventario: quiebres y sobrestock al mismo tiempo", "competir con el comercio electrónico", "la rotación del personal de tienda"],
        "triggers": ["temporadas (regreso a clases, fin de año, Black Friday)", "apertura de tiendas", "lanzamiento de e-commerce"],
        "buyers": ["comercial", "operaciones", "marketing", "supply_chain"],
        "keys": r"\b(retail|supermercados?|cadenas?|almacenes?|tiendas?|comercio)\b"},
    "textil_moda": {
        "pains": ["la rapidez de colección frente al fast fashion", "el inventario de temporada que queda sin vender", "el contrabando y la competencia importada", "la venta digital"],
        "triggers": ["lanzamiento de colección", "feria (Colombiamoda)", "apertura de tiendas"],
        "buyers": ["comercial", "marketing", "operaciones"],
        "keys": r"\b(textil(es)?|moda|confecciones?|ropa|calzado)\b"},
    "quimica_materiales": {
        "pains": ["la seguridad en procesos y el cumplimiento ambiental", "la volatilidad de insumos importados", "la eficiencia de planta"],
        "triggers": ["auditoría ambiental", "ampliación de planta", "cambio de proveedor de insumos"],
        "buyers": ["operaciones", "compras", "supply_chain"],
        "keys": r"\b(quimic[ao]s?|plasticos?|materiales|pinturas?)\b"},
    "manufactura_industrial": {
        "pains": ["las paradas no programadas y el mantenimiento", "la productividad por turno", "el costo de energía e insumos", "la calidad y los reprocesos"],
        "triggers": ["ampliación de planta", "nueva línea de producción", "certificación ISO"],
        "buyers": ["operaciones", "compras", "supply_chain", "finanzas"],
        "keys": r"\b(manufactur\w+|industrial(es)?|plantas?|fabricas?|metalmecanic[ao])\b"},
    "medios_entretenimiento": {
        "pains": ["la caída de la pauta tradicional", "monetizar audiencias digitales", "producir más contenido con menos"],
        "triggers": ["lanzamiento de plataforma", "evento o temporada", "cambio de dueño"],
        "buyers": ["comercial", "marketing", "tecnologia"],
        "keys": r"\b(medios|television|radio|editorial|entretenimiento|eventos?)\b"},
    "turismo_hoteleria": {
        "pains": ["la ocupación en temporada baja", "la dependencia de las agencias en línea (OTA) y sus comisiones", "la experiencia del huésped y las reseñas"],
        "triggers": ["temporada alta", "apertura de hotel", "congresos y eventos en la ciudad"],
        "buyers": ["comercial", "marketing", "operaciones"],
        "keys": r"\b(hotel(es)?|hoteleri[ao]|turismo|agencias de viajes?|aerolineas?)\b"},
    "educacion": {
        "pains": ["la deserción de estudiantes", "captar matrículas con menos demografía", "la acreditación de calidad", "digitalizar la oferta"],
        "triggers": ["periodo de matrículas", "proceso de acreditación", "nuevo programa virtual"],
        "buyers": ["direccion", "marketing", "tecnologia", "finanzas"],
        "keys": r"\b(universidad(es)?|colegios?|educacion|instituciones educativas|edtech)\b"},
    "gobierno": {
        "pains": ["ejecutar el presupuesto a tiempo", "la transparencia y los órganos de control", "la atención al ciudadano"],
        "triggers": ["plan anual de adquisiciones", "inicio de gobierno", "licitación publicada"],
        "buyers": ["compras", "tecnologia", "direccion"],
        "keys": r"\b(gobierno|sector publico|alcaldias?|ministerios?|entidades publicas)\b"},
    "ong": {
        "pains": ["conseguir y retener donantes", "demostrar impacto con datos", "operar con equipos pequeños"],
        "triggers": ["convocatoria de cooperación", "campaña de recaudo", "informe anual"],
        "buyers": ["direccion", "finanzas", "marketing"],
        "keys": r"\b(ong|fundacion(es)?|sin animo de lucro|cooperacion)\b"},
    "servicios_profesionales": {
        "pains": ["vender sin depender de los socios", "la utilización del equipo (horas facturables)", "diferenciarse cuando todos ofrecen lo mismo"],
        "triggers": ["nuevo socio o práctica", "pérdida de un cliente grande", "expansión regional"],
        "buyers": ["direccion", "comercial", "finanzas"],
        "keys": r"\b(consultor(a|as|ia|ias)?|firmas? de abogados|agencias?|servicios profesionales|outsourcing)\b"},
}  # fmt: skip
_SECTOR_RE = {code: re.compile(s["keys"]) for code, s in SECTORS.items()}

# Referencias del mercado B2B en Latinoamérica para correos en frío bien segmentados.
BENCH_POSITIVE = 0.05  # respuestas positivas sobre contactados
BENCH_MEETING = 0.6  # reuniones sobre respuestas positivas

_STRATEGY = re.compile(
    r"\b((crea|creame|arma|armame|hazme|haz|disena|disename|dame|necesito|quiero) (una |mi |un )?"
    r"(estrategia|plan (de ventas|de prospeccion|comercial))|estrategia (completa|comercial|de ventas|de prospeccion))\b"
)
_PAINS = re.compile(
    r"\b(que le (duele|preocupa|importa|quita el sueno)|dolores? de|que dolores|que problemas tiene|"
    r"biblioteca de dolores|que le interesa a)\b"
)
_GOAL = re.compile(r"\b(\d{1,3}) (reuniones|citas|demos|meetings)\b")


def wants_strategy(question: str) -> bool:
    return bool(_STRATEGY.search(fold(question).lower()))


def wants_pains(question: str) -> bool:
    return bool(_PAINS.search(fold(question).lower()))


def sector_of(text: str) -> str | None:
    folded = fold(text).lower()
    return next((code for code, rx in _SECTOR_RE.items() if rx.search(folded)), None)


def area_of(text: str) -> tuple[str | None, str | None]:
    from orkesta.engine.taxonomy.titles import classify_title

    level, area = classify_title(text)
    return (None if area == "direccion" and level is None else area), level


def _label(sector: str) -> str:
    from orkesta.copywriter.knowledge import INDUSTRY_CONTEXT

    return INDUSTRY_CONTEXT.get(sector, {}).get("es", (sector.replace("_", " "), ""))[0]


def _reply(ctx: engine.Context, summary: str, label: str, **extra: Any) -> dict[str, Any]:
    return {"case_id": None, "topic": "estrategia", "topic_label": label, "objection": None,
            "objection_label": None, "formal": ctx.formal, "summary": summary, "options": [],
            "tips": [], "sections": [], "next_step": "", "missing": [], "sources": [],
            "suggestions": [], **extra}  # fmt: skip


def pains(ctx: engine.Context, question: str) -> dict[str, Any]:
    """Qué le duele a un cargo en un sector, con una frase para abrir la conversación."""
    from orkesta.copywriter.knowledge import PERSONAS

    area, _level = area_of(question)
    sector = sector_of(question)
    if area is None and sector is None:
        return _reply(ctx, "Dime el cargo y el sector, por ejemplo: «¿Qué le duele a un gerente de compras de un banco?» o «dolores de una directora de RR. HH. en retail».",
                      "Dolores por cargo y sector",
                      suggestions=["¿Qué le duele a un gerente de compras de un banco?",
                                   "¿Qué le preocupa a un director de operaciones en manufactura?",
                                   "¿Qué le importa a un CFO en retail?"])  # fmt: skip
    sections: list[dict[str, Any]] = []
    persona = PERSONAS.get(area or "", {}).get("es") if area else None
    if persona:
        sections.append(
            {"title": f"Lo que le duele a {persona['label']}", "items": list(persona["pains"])[:4]}
        )
        sections.append({"title": "Lo que quiere lograr", "items": list(persona["goals"])[:3]})
    if sector:
        info = SECTORS[sector]
        sections.append({"title": f"Lo que preocupa en {_label(sector)}", "items": info["pains"]})
        sections.append(
            {"title": "Momentos para escribirle (abren la puerta)", "items": info["triggers"]}
        )
    who = persona["label"].lower() if persona else "quien decide"
    where = f" en {_label(sector)}" if sector else ""
    first = persona["pains"][0] if persona else ""
    pain = SECTORS[sector]["pains"][0] if sector else first
    valor = engine.slots(ctx).get("valor")
    opener = (f"«Hablando con {who}{where}, lo que más escucho es {pain}. "
              + (f"Ayudamos a lograr {valor}. " if valor else "")
              + "¿Les está pasando algo parecido?»")  # fmt: skip
    sections.append({"title": "Una frase para abrir la conversación", "items": [opener]})
    if persona and persona.get("objections"):
        sections.append(
            {"title": "Lo que te va a objetar", "items": list(persona["objections"])[:3]}
        )
    subject = f"{who}{where}"
    return _reply(ctx, f"Esto es lo que le preocupa a {subject}. Úsalo para personalizar el primer mensaje y la reunión.",
                  "Dolores por cargo y sector", sections=sections,
                  suggestions=[f"Crea una secuencia de 3 correos para {subject}", "Créame una estrategia",
                               "Entréname con una objeción"])  # fmt: skip


def _own_rate(session: Session, ctx: engine.Context) -> tuple[float, int] | None:
    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.replies import POSITIVE

    sent, positive = session.execute(
        select(func.count(), func.count().filter(Recipient.reply_category.in_(POSITIVE)))
        .where(Recipient.tenant_id == ctx.tenant_id, Recipient.sent_count > 0)
    ).one()  # fmt: skip
    if int(sent) < 100:
        return None
    return max(int(positive) / int(sent), 0.005), int(sent)


def build(session: Session, ctx: engine.Context, question: str) -> dict[str, Any]:
    """Estrategia completa: a quién, qué decir, cadencia por canal y actividad para la meta."""
    from orkesta.copywriter.knowledge import PERSONAS

    values = engine.slots(ctx)
    publico = values.get("publico", "")
    folded = fold(question).lower()
    goal_match = _GOAL.search(folded)
    goal = int(goal_match.group(1)) if goal_match else 10
    sector = sector_of(question) or sector_of(publico)
    area, _ = area_of(question)
    if area is None and publico:
        area, _ = area_of(publico)
    sections: list[dict[str, Any]] = []
    # 1. A quién apuntar.
    target = []
    if publico:
        target.append(f"Tu cliente ideal según tu ficha: {publico}.")
    results = engine.advise_strategy(session, ctx)
    for s in results.get("sections", []):
        if s["title"] in ("Sectores que mejor responden", "Niveles que mejor responden"):
            target += [f"{s['title']}: {s['items'][0]}"] if s["items"] else []
    if sector:
        buyers = [
            PERSONAS[a]["es"]["label"] for a in SECTORS[sector]["buyers"][:3] if a in PERSONAS
        ]
        target.append(f"En {_label(sector)} suelen decidir o influir: {', '.join(buyers)}.")
    target.append(
        "Prioriza en tres niveles: 1) 20 cuentas soñadas con mensaje uno a uno, 2) 100 cuentas parecidas a tus mejores clientes, 3) el resto con secuencia automática."
    )
    sections.append({"title": "1. A quién apuntar", "items": target})
    # 2. Qué decirle.
    say = []
    persona = PERSONAS.get(area or "", {}).get("es") if area else None
    if persona:
        say.append(
            f"A {persona['label']} le duele {persona['pains'][0]}; háblale de eso, no de tu producto."
        )
    if sector:
        say.append(f"En {_label(sector)} el tema del momento es {SECTORS[sector]['pains'][0]}.")
        say.append(f"Escríbele cuando pase algo: {', '.join(SECTORS[sector]['triggers'][:2])}.")
    if values.get("valor"):
        say.append(f"Tu promesa en una frase: {values['valor']}.")
    if values.get("prueba"):
        say.append(f"Tu prueba para el segundo contacto: {values['prueba']}.")
    if values.get("diferencial"):
        say.append(f"Tu diferencial frente a otras opciones: {values['diferencial']}.")
    sections.append(
        {
            "title": "2. Qué decirle",
            "items": say
            or [
                "Completa en tu ficha el problema, el resultado y un caso de éxito para personalizar el mensaje."
            ],
        }
    )
    # 3. Cadencia multicanal (3 semanas).
    sections.append({"title": "3. Cadencia por canal (3 semanas)", "items": [
        "Día 1 · Correo: el problema del cargo y una pregunta corta.",
        "Día 2 · LinkedIn: solicitud de conexión sin nota de venta.",
        "Día 4 · Correo: caso de éxito con un número.",
        "Día 7 · Llamada: 30 segundos para pedir permiso y una reunión de 20 minutos.",
        "Día 10 · LinkedIn: comenta algo suyo o comparte un dato útil.",
        "Día 14 · Correo: una idea nueva para su negocio (estilo Challenger).",
        "Día 21 · Correo de despedida: «¿Lo dejamos para más adelante?».",
    ]})  # fmt: skip
    # 4. Cuánta actividad hace falta.
    own = _own_rate(session, ctx)
    rate, basis = (own[0], f"tu tasa real de respuestas positivas ({own[0]:.1%} en {own[1]} envíos)") if own else (
        BENCH_POSITIVE, "una referencia del mercado (5 % de respuestas positivas en correos en frío bien segmentados)")  # fmt: skip
    contacts = math.ceil(goal / (rate * BENCH_MEETING))
    per_day = math.ceil(contacts / 20)
    sections.append({"title": "4. Cuánta actividad necesitas", "items": [
        f"Para {goal} reuniones al mes necesitas escribirle a unas {contacts} personas nuevas al mes: unas {per_day} por día hábil.",
        f"Cálculo con {basis} y 6 de cada 10 respuestas positivas convertidas en reunión.",
        "Si subes las respuestas positivas personalizando mejor, necesitas menos contactos: mide cada dos semanas.",
    ]})  # fmt: skip
    who = (persona["label"] if persona else "tu cliente ideal") + (
        f" en {_label(sector)}" if sector else ""
    )
    missing = (
        []
        if values.get("valor") and values.get("problema")
        else ["completa en tu ficha el problema y el resultado que logra tu cliente"]
    )
    return _reply(
        ctx, f"Esta es tu estrategia para conseguir {goal} reuniones al mes con {who}. Cuando quieras, creo la secuencia de correos y la dejamos como campaña en borrador.",
        "Estrategia comercial", sections=sections, missing=missing,
        next_step="Busca en Prospección las primeras 20 cuentas soñadas y escríbeles esta semana.",
        suggestions=[f"Crea una secuencia de 3 correos para {who}", f"¿Qué le duele a {who}?",
                     "Entréname con una objeción"],
    )  # fmt: skip
