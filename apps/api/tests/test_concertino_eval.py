"""Evaluación de Concertino con 100 preguntas escritas como las haría un vendedor, ninguna
copiada de las reglas. Mide el porcentaje de aciertos (tema u objeción correctos y nunca «no
entendí» cuando hay respuesta). Si baja del mínimo, algo dejó de entenderse."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register
from tests.test_concertino import _ficha, _product_known  # noqa: F401  (fixture automática)
from tests.test_supervision import _pro

# (pregunta, tema esperado o None = cualquiera útil, objeción esperada o None)
CASES: list[tuple[str, str | None, str | None]] = [
    # Objeciones dichas de mil formas
    ("me dijo que está muy caro", "objecion", "precio"),
    ("dice que no le alcanza la plata", "objecion", "precio"),
    ("el cliente siente que el precio es alto", "objecion", "precio"),
    ("me pidió un 20% de descuento", None, "descuento"),
    ("si le bajo el precio firma hoy", None, "descuento"),
    ("me dijo que lo iba a pensar", "objecion", "pensar"),
    ("quedó en pensarlo y avisarme", "objecion", "pensar"),
    ("me pidió unos días para decidir", "objecion", "pensar"),
    ("me dijeron que retomemos en enero", "objecion", "prioridad"),
    ("no es el momento para ellos", "objecion", "prioridad"),
    ("dicen que el próximo año lo ven", "objecion", "prioridad"),
    ("ya trabajan con otro proveedor", "objecion", "proveedor"),
    ("me contestó que ya tienen una herramienta", "objecion", "proveedor"),
    ("están contentos con lo que tienen", "objecion", "proveedor"),
    ("eso lo resuelven internamente", "objecion", "interno"),
    ("lo tiene que aprobar su jefe", "objecion", "comite"),
    ("lo va a consultar con la junta", "objecion", "comite"),
    ("lo iba a hablar con su gerente y desapareció", "objecion", "comite"),
    ("no me responde hace dos semanas", "objecion", "silencio"),
    ("me dejó en visto", "objecion", "silencio"),
    ("me dijo que le mande información", None, "info"),
    ("no le interesa", "objecion", "no_interes"),
    ("están comparando con Apollo", "objecion", "competencia"),
    ("dice que la competencia es más barata", "objecion", "competencia"),
    ("tiene miedo de que su equipo no lo use", "objecion", "riesgo"),
    # Textos
    ("escríbeme un correo en frío para un director comercial", "redactar", None),
    ("redáctame un correo para un CFO de una minera", "redactar", None),
    ("hazme una secuencia de 4 correos para gerentes de ventas", "redactar", None),
    ("un correo de seguimiento para alguien que no respondió", "redactar", None),
    ("correo para agradecer la reunión de hoy", "redactar", None),
    ("mensaje de whatsapp para un prospecto", "redactar", None),
    ("whatsapp para recordarle la cita de mañana", "redactar", None),
    ("mensajes de linkedin para una gerente de recursos humanos", "redactar", None),
    ("un post de linkedin sobre nuestro caso de éxito", "redactar", None),
    ("publicación para linkedin", "redactar", None),
    ("dame 5 asuntos para un correo en frío", "redactar", None),
    ("guion para llamar a un gerente comercial", "redactar", None),
    ("correo para reactivar a un cliente que se perdió", "redactar", None),
    ("invita a un cliente a nuestro webinar", "redactar", None),
    ("qué le escribo a un cliente para que me recomiende", "redactar", None),
    ("qué le digo a un gerente de compras para que me reciba", "redactar", None),
    ("escríbele a un CEO que no me conoce", "redactar", None),
    # Números y CRM
    ("cuántos correos mandé hoy", "general", None),
    ("cuántas respuestas tuve esta semana", "general", None),
    ("cómo voy este mes", "general", None),
    ("cuánto vendimos este mes", "general", None),
    ("cuántas reuniones tuve la semana pasada", "general", None),
    ("cuál es mi tasa de respuesta", "general", None),
    ("qué oportunidad priorizo esta semana", "cuenta", None),
    ("a quién llamo hoy primero", "cuenta", None),
    ("revisa mis oportunidades", "cuenta", None),
    ("cómo están mis campañas", "campana", None),
    ("cuál es mi mejor campaña", "campana", None),
    # Oficio y estrategia
    ("cómo hago seguimiento sin molestar", None, None),
    ("qué hago si no me contestan después de 3 correos", None, None),
    ("cómo consigo más reuniones", None, None),
    ("dame una estrategia para conseguir clientes nuevos", None, None),
    ("a qué sector le apunto", "estrategia", None),
    ("quién es mi cliente ideal", "estrategia", None),
    ("cómo preparo una reunión de descubrimiento", "reunion", None),
    ("prepárame la reunión de mañana", "reunion", None),
    ("dame una agenda para una demo de 30 minutos", "reunion", None),
    ("qué es SPIN selling", None, None),
    ("explícame el método challenger", None, None),
    ("cómo califico un lead", None, None),
    ("cómo le hablo a un CFO", "perfil", None),
    ("cómo convenzo a la de marketing", "perfil", None),
    ("qué le importa a un gerente de operaciones", None, None),
    ("cómo hago para que me contesten más", None, None),
    ("nadie abre mis correos", None, None),
    ("el cliente aceptó, ¿qué sigue?", None, None),
    ("firmaron el contrato, y ahora qué hago", None, None),
    ("estoy frustrada, no me sale nada", None, None),
    ("me pidieron referencias de otros clientes", None, None),
    ("cómo armo una propuesta que cierre", None, None),
    ("cómo cierro más rápido", None, None),
    ("cómo negocio con compras", None, None),
    # Producto (la ficha)
    ("qué vendemos", "producto", None),
    ("cuánto cuesta", "producto", None),
    ("qué precio tiene", "producto", None),
    ("cuál es el precio por usuario", "producto", None),
    ("preséntame el producto en 30 segundos", None, None),
    ("explícame el producto en una frase", None, None),
    ("en una línea, por qué deberían comprarnos", None, None),
    ("cuál es nuestro diferencial", "producto", None),
    ("en qué somos mejores que Apollo", None, None),
    ("tienen un caso de éxito", None, None),
    ("a quién le vendemos", None, None),
    ("me pregunta si se integra con Salesforce", "producto", None),
    ("¿funciona con HubSpot?", "producto", None),
    # ORKESTA
    ("cómo uso orkesta para prospectar", None, None),
    ("cómo creo una campaña en orkesta", None, None),
    ("cómo envío una propuesta en orkesta", None, None),
    ("cómo conecto mi gmail en orkesta", None, None),
    ("dónde configuro mi firma en orkesta", None, None),
    # Situaciones del día a día (segunda tanda, no vistas al escribir las reglas)
    ("el cliente quiere pagar a 90 días", None, "pago"),
    ("me dijo que su presupuesto ya está asignado", "objecion", "precio"),
    ("me respondió que lo envíe a otra persona", None, "persona"),
    ("dice que ya compró algo parecido", None, "proveedor"),
    ("la propuesta la abrió 5 veces y no responde", None, None),
    ("vio la propuesta y no me contesta", None, None),
    ("me cancelaron la reunión", None, None),
    ("el cliente no se conectó a la llamada", None, None),
    ("cómo manejo a un cliente enojado", None, None),
    ("tengo un cliente molesto por una entrega", None, None),
    ("tienen garantía", "producto", None),
    ("¿se puede facturar en otro país?", None, None),
    ("cómo hago una demo que enamore", None, None),
    ("cómo le hablo a un dueño de empresa pequeña", "perfil", None),
    ("dame ideas para un post", "redactar", None),
    ("hazme un correo corto", "redactar", None),
    # Tercera tanda
    ("el cliente dice que necesita ver más casos", None, None),
    ("me piden una prueba gratis", None, None),
    ("mi jefe me pide 10 reuniones al mes, cómo lo logro", None, None),
    ("el prospecto me dijo que le escriba en dos meses", "objecion", "prioridad"),
    ("cómo le respondo a alguien que me pide el precio en el primer correo", None, None),
    ("cuántos contactos tengo", "general", None),
    ("cómo le hago seguimiento a una propuesta", None, None),
    ("me dijeron que ya no trabajan ahí", "objecion", "persona"),
    ("me dijo que está de vacaciones", "objecion", "prioridad"),
    ("cómo sé si una oportunidad es real", None, None),
    # Cuarta tanda
    ("qué hago si el cliente me ghostea después de la demo", "objecion", "silencio"),
    ("me dijo que su sobrino le hace eso gratis", "objecion", "interno"),
    ("cuál es el mejor día para enviar correos", None, None),
    ("dame un correo para felicitar a un cliente por un premio", "redactar", None),
    ("cómo uso Concertino", None, None),
    ("el decisor cambió, qué hago", None, None),
    ("qué hago con las oportunidades que llevan un mes sin moverse", "cuenta", None),
    ("cómo pido un aumento de presupuesto al cliente", None, None),
    ("redacta un mensaje para un cliente que no ha pagado", "redactar", None),
    ("tengo una reunión con Banco Andino el viernes", None, None),
    # Quinta tanda
    ("el cliente me dijo que lo llame la otra semana", "objecion", "prioridad"),
    ("cómo hago para que el cliente firme hoy", None, None),
    ("qué le respondo si me dice que somos muy pequeños", None, "riesgo"),
    ("escríbeme un correo para un cliente que perdimos por precio", "redactar", None),
    ("cuánto me falta para la meta", "general", None),
    ("me pide un descuento por volumen", None, "descuento"),
    ("cómo le vendo a una empresa del gobierno", None, None),
    ("tengo miedo de llamar", None, None),
    ("qué hago si el cliente me pide exclusividad", None, None),
    # Conversación
    ("hola", None, None),
    ("gracias", None, None),
    ("qué puedes hacer", None, None),
    ("ayuda", None, None),
]

MINIMUM = 0.95
_LOST = ("No tengo eso en la ficha todavía", "No estoy seguro de haber entendido", "No reconozco")


def test_concertino_understands_how_sellers_ask(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    misses = []
    for question, topic, objection in CASES:
        r = client.post("/api/concertino/ask", json={"question": question}).json()
        lost = any(x in str(r.get("summary", "")) for x in _LOST)
        ok = not lost
        if topic is not None and r.get("topic") != topic:
            ok = False
        if objection is not None and r.get("objection") != objection:
            ok = False
        if not ok:
            misses.append(
                (question, r.get("topic"), r.get("objection"), str(r.get("summary", ""))[:90])
            )
    rate = 1 - len(misses) / len(CASES)
    print(f"\nConcertino: {rate:.0%} de {len(CASES)} preguntas")
    for m in misses:
        print("  ✗", m)
    assert rate >= MINIMUM, misses
