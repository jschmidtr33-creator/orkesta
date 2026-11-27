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
    ("cómo preparo una reunión de descubrimiento", None, None),
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
    # Enseñar el oficio (no confundir con una cuenta o una objeción)
    ("Enséñame a cerrar", "general", None),
    ("enséñame a negociar", "general", None),
    ("qué es el ciclo de venta", "general", None),
    ("cómo manejo objeciones", "general", None),
    ("cómo vendo por valor y no por precio", "general", None),
    ("cómo construyo confianza con un cliente", "general", None),
    ("cuántos seguimientos debo hacer", "general", None),
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


# Preguntas nuevas usadas para medir la comprensión semántica (4 tandas de 30). Concertino no
# debe quedarse sin respuesta: si no las entiende por reglas, las reescribe a una forma conocida.
FRESH = [
    'Enséñame a cerrar',
    'enséñame a negociar',
    'enséñame a prospectar en LinkedIn',
    'explícame cómo hacer seguimiento',
    'dame tips para llamadas en frío',
    'consejos para mi primera demo',
    'qué es un pipeline',
    'qué es el ciclo de venta',
    'cómo se hace una propuesta',
    'cómo manejo objeciones',
    'enséñame a escribir correos que respondan',
    'cómo se califica a un prospecto',
    'qué hago antes de una reunión',
    'cómo le pido referidos a un cliente',
    'cómo vendo por valor y no por precio',
    'cómo me organizo como vendedor',
    'cómo uso las preguntas SPIN',
    'cuántos seguimientos debo hacer',
    'cómo dejo de sonar como vendedor',
    'cómo construyo confianza con un cliente',
    'qué hago después de una mala reunión',
    'cómo le hago una pregunta difícil al cliente',
    'cómo hago para que me recuerden',
    'cómo manejo el rechazo',
    'cómo vendo a una empresa que no me conoce',
    'enséñame a hacer un pitch',
    'qué es el social selling',
    'cómo escribo un asunto que abran',
    'cómo sé cuándo cerrar',
    'cómo vendo más caro',
    "el cliente dice que no le dan los números",
    "quiere que le haga una rebajita",
    "me dijo que me confirma la próxima semana",
    "el gerente no me ha dado respuesta desde el martes",
    "dice que ya contrataron a otra agencia",
    "necesito escribirle a la directora de recursos humanos de un hospital",
    "mándame un texto para presentarme por linkedin a un gerente de ventas",
    "quiero un correo para alguien que vi en una feria",
    "cómo me fue la semana pasada con los correos",
    "qué negocios están más calientes",
    "tengo cero respuestas en mi campaña",
    "cómo armo mi semana de prospección",
    "qué le pregunto al gerente en la primera reunión",
    "el cliente me dijo que lo va a revisar con su socio",
    "cómo le explico el valor a alguien que solo mira el precio",
    "qué hago si me dicen que mande la propuesta y luego desaparecen",
    "cómo le gano a un competidor más grande",
    "me pidió que le llame en otro momento",
    "quiero algo para publicar el lunes en linkedin",
    "ayúdame a responderle a un cliente que pide más plazo",
    "cuántas propuestas me aceptaron este mes",
    "la gerente me dijo que no tiene tiempo ahora",
    "qué texto le mando a un cliente que cumplió un año con nosotros",
    "cómo cierro si el cliente duda",
    "dime cómo le hablo a un director de operaciones",
    "qué hago con un prospecto que abre mis correos pero no responde",
    "el contacto dice que su empresa está congelando gastos",
    "cómo pido una reunión sin sonar desesperado",
    "necesito ideas para conseguir clientes en México",
    "cómo respondo si me dicen que mande todo por correo",
    "me contestó que por ahora están bien así",
    "la directora me dijo que el costo no lo justifica",
    "me pide que le mantenga el precio del año pasado",
    "dice que lo tiene que ver con finanzas",
    "el prospecto me dejó de contestar después de la llamada",
    "escríbele a una gerente de compras de una farmacéutica",
    "necesito un texto corto para mandar por whatsapp a un cliente nuevo",
    "qué publico en linkedin esta semana",
    "cómo van mis números de la semana",
    "cuál cliente está más cerca de firmar",
    "nadie responde mis secuencias",
    "cómo organizo mi agenda de ventas",
    "qué preguntas hago en una reunión de descubrimiento",
    "me dijo que lo platica con su esposa que es socia",
    "cómo justifico el precio frente a uno más barato",
    "le mandé la propuesta hace una semana y nada",
    "me dijeron que vuelva a escribir en febrero",
    "quiero un post contando un caso de éxito",
    "el cliente pide pagar en 3 cuotas",
    "la gerente me dijo que no tiene presupuesto este año",
    "qué le escribo a un cliente que renovó el contrato",
    "cómo cierro una venta que está estancada",
    "cómo le vendo a un gerente de ti",
    "me abre los correos pero nunca contesta",
    "el cliente dice que tiene que hablarlo con el directorio",
    "cómo me presento en un evento de networking",
    "qué hago si me piden bajar el precio un 15%",
    "el cliente dice que ya lo tienen cubierto",
    "cómo consigo que me presenten con el gerente general",
    "me siento estancada con mis ventas",
    "el cliente me dijo que no ve el beneficio",
    "dice que prefiere esperar a ver cómo cierra el año",
    "me respondió que lo tiene que revisar legal",
    "la persona dejó de responder hace 10 días",
    "me pidió que le baje un 10 por ciento",
    "escríbeme algo para un director de marketing de una universidad",
    "mensaje de linkedin para invitar a conectar a un CEO",
    "dame contenido para mi linkedin sobre productividad",
    "cómo estuvo mi mes",
    "a qué oportunidad le meto más energía",
    "mis correos no los abre nadie",
    "qué hago para tener más citas",
    "qué le pregunto a un cliente para entender su problema",
    "el cliente dijo que lo va a consultar con su gerente",
    "cómo defiendo mi precio",
    "envié la cotización y no hay respuesta",
    "me dijo que lo retomemos después de semana santa",
    "haz un post sobre un cliente que ahorró dinero",
    "el cliente quiere pagar la mitad ahora y la mitad después",
    "el comprador dice que su jefe no aprueba más gastos",
    "correo de gracias a un cliente que nos recomendó",
    "cómo hago que un cliente indeciso decida",
    "cómo le vendo a un gerente de recursos humanos",
    "abrió mi correo 4 veces y no responde",
    "cómo me presento ante un cliente grande",
    "me piden rebajar el precio para cerrar",
    "el cliente dice que ya resolvió eso con otra empresa",
    "cómo llego al dueño de la empresa",
    "me siento sin ganas de vender",
    "qué hago si el cliente me ignora",
    "el cliente dice que nuestro servicio es muy caro comparado con lo que paga hoy",
    "me dijo que lo vemos el próximo semestre",
    "dice que tiene que convencer a su jefe primero",
    "llevo tres semanas sin noticias del cliente",
    "me pidió un 5% menos para firmar ya",
    "redáctame algo para un gerente de logística de una cadena de supermercados",
    "qué le escribo a un CEO por linkedin",
    "quiero publicar sobre un logro de un cliente",
    "cuántas respuestas positivas tuve este mes",
    "cuál es la oportunidad más grande que tengo",
    "mis secuencias no generan reuniones",
    "cómo consigo más demos",
    "qué pregunto para saber si tiene presupuesto",
    "la jefa de compras lo tiene que consultar con gerencia",
    "cómo convenzo a alguien que dice que es caro",
    "mandé la propuesta el lunes y no me dice nada",
    "me dijo que hablemos en marzo",
    "escríbeme un post sobre por qué fallan las entregas",
    "el cliente pide pagar en dos partes",
    "el gerente dice que su jefe congeló el presupuesto",
    "correo para agradecer a un cliente por su confianza",
    "cómo cierro con alguien que tiene dudas",
    "cómo le vendo a una directora de finanzas",
    "lo abre pero no contesta",
    "cómo preparo una reunión con una empresa grande",
    "me pide un descuento por pagar de contado",
    "dice que ya trabaja con otra empresa de logística",
    "cómo llego al gerente general",
    "estoy agotada de no vender",
    "el prospecto me dejó en visto",
]


def test_concertino_semantic_layer_understands_new_phrasings(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    from orkesta.concertino import semantic

    assert (
        semantic.normalize("me ghosteó y no responde el mail")
        == "no me responde y no responde el correo"
    )
    assert (
        semantic.rewrite("quiere que le haga una rebajita", None).canonical
        == "quiere un descuento para firmar"
    )  # type: ignore[union-attr]
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    lost = []
    for question in FRESH:
        r = client.post("/api/concertino/ask", json={"question": question}).json()
        if any(x in str(r.get("summary", "")) for x in _LOST):
            lost.append(question)
    rate = 1 - len(lost) / len(FRESH)
    print(f"\nComprensión de frases nuevas: {rate:.0%} de {len(FRESH)}")
    for q in lost:
        print("  ✗", q)
    assert rate >= 0.9, lost
