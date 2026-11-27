# ruff: noqa: E501  (frases de ejemplo: líneas largas a propósito)
"""Comprensión semántica propia de Concertino (sin modelos externos ni servicios de terceros).

Concertino responde con reglas precisas. Esta capa hace que entienda la MISMA idea dicha de otra
forma, como lo haría un modelo de lenguaje:

1. Normaliza el habla de ventas de Latinoamérica («lana», «mail», «me ghosteó», «la reu») a
   palabras que las reglas conocen.
2. Compara la pregunta con un banco de cientos de formas reales de preguntar (n-gramas de
   caracteres + palabras, TF-IDF y similitud coseno: aguanta errores de tipeo y orden distinto).
3. Si la pregunta no se entendió, la reescribe a la forma canónica más parecida que SÍ sabe
   responder, conservando el cargo, la empresa y el número que traía la original.
4. Aprende: cada vez que alguien elige una sugerencia o corrige el tema, esa frase entra al banco
   de su empresa.

Todo corre en el servidor de ORKESTA, en memoria, en milisegundos.
"""

from __future__ import annotations

import itertools
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass

from orkesta.engine.normalize.text import fold

# --- 1. Habla de ventas --------------------------------------------------------------------------

_SLANG: tuple[tuple[str, str], ...] = (
    (r"\b(lana|plata|billete|guita|feria|pasta)\b", "dinero"),
    (r"\b(e-?mails?|mails?|correitos?)\b", "correo"),
    (r"\b(wsp|wpp|whats|wasap|guasap|whatsap)\b", "whatsapp"),
    (r"\b(linkedn|linkdin|linkeding|linked in)\b", "linkedin"),
    (r"\b(me )?(ghostea|ghoste[oó]|ghostearon|ghostean|ghostear)\b", "no me responde"),
    (r"\bme (clav[oó]|dej[oó]|dejaron) (el|en) visto\b", "no me responde"),
    (r"\b(reu|meet|meeting|junta de trabajo)\b", "reunion"),
    (r"\b(call|llamadita)\b", "llamada"),
    (r"\b(leads?|clientes? potenciales?)\b", "prospecto"),
    (r"\bdeals?\b", "oportunidad"),
    (r"\b(car[ií]simo|carito|car[ií]sima|re caro|muy costoso)\b", "muy caro"),
    (r"\b(boss|manager|jefa)\b", "jefe"),
    (r"\b(chamba|curro|pega)\b", "trabajo"),
    (r"\b(porfa|plis|please|xfa|x favor)\b", ""),
    (r"\b(q|k)\b", "que"),
    (r"\bxq\b", "porque"),
    (r"\bpq\b", "porque"),
    (r"\btb\b", "tambien"),
    (r"\bmsj(s)?\b", "mensaje"),
    (r"(\d)\s?%", r"\1 por ciento"),
)


def normalize(text: str) -> str:
    """El habla del vendedor a palabras que Concertino conoce (en minúsculas, sin perder tildes)."""
    out = text
    for pattern, repl in _SLANG:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


# --- 2. Banco de formas de preguntar --------------------------------------------------------------
# (forma canónica que Concertino responde bien, [otras formas de decir lo mismo])

BANK: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Objeciones
    (
        "me dijo que es muy caro",
        (
            "le pareció costoso",
            "dice que se le sale del presupuesto",
            "el precio le asustó",
            "piensa que cobramos mucho",
            "no le cuadran los números",
            "dice que es mucha inversión",
            "le parece excesivo el valor",
            "no tienen con qué pagarlo",
        ),
    ),
    (
        "quiere un descuento para firmar",
        (
            "me está regateando",
            "quiere que le rebaje",
            "pide un mejor precio",
            "me pide una rebaja",
            "quiere precio especial",
            "me pidió bajar la tarifa",
            "pide un porcentaje menos",
        ),
    ),
    (
        "el cliente quiere pagar a 90 días",
        (
            "quiere pagar después",
            "me pide crédito",
            "quiere pagar en partes",
            "pide plazo para pagar",
            "quiere pagar a fin de año",
            "necesitan financiamiento",
        ),
    ),
    (
        "me dijo que lo va a pensar",
        (
            "quedó en darme respuesta",
            "dijo que me avisa",
            "está analizándolo",
            "me dijo déjame ver",
            "lo está evaluando",
            "me dijo que lo consulta con la almohada",
            "me pidió tiempo",
        ),
    ),
    (
        "no es prioridad ahora",
        (
            "dijo que ahora no",
            "me dijo que más adelante",
            "están con otras cosas",
            "no es buen momento para ellos",
            "tienen otras prioridades",
            "me dijo que después de fin de año",
            "lo dejan para el otro trimestre",
        ),
    ),
    (
        "ya tienen proveedor",
        (
            "ya trabajan con alguien",
            "tienen contrato con otra empresa",
            "están felices con su proveedor",
            "usan otra herramienta",
            "ya compraron otra solución",
            "tienen a otro",
        ),
    ),
    (
        "eso lo resuelven internamente",
        (
            "lo hacen ellos mismos",
            "tienen un equipo que lo hace",
            "lo arman en casa",
            "su área de sistemas lo construye",
            "lo hace un empleado",
        ),
    ),
    (
        "lo tiene que aprobar su jefe",
        (
            "lo debe validar gerencia",
            "decide el dueño",
            "tiene que pasar por comité",
            "lo revisa la junta directiva",
            "no puede decidir solo",
            "necesita el visto bueno de arriba",
        ),
    ),
    (
        "no me responde",
        (
            "lleva días sin contestar",
            "no me ha vuelto a escribir",
            "desapareció después de la propuesta",
            "no da señales de vida",
            "no contesta mis correos",
            "le escribí 3 veces y nada",
            "no me devuelve las llamadas",
        ),
    ),
    (
        "no es la persona indicada",
        (
            "no es quien decide",
            "me dijo que no es su área",
            "me mandó con otra persona",
            "me pasó a otro departamento",
            "dice que eso lo ve otro",
        ),
    ),
    (
        "me dijo que le mande información",
        (
            "me pidió una presentación",
            "quiere que le envíe el portafolio",
            "me pidió que le mande algo por correo",
            "pidió más detalles por escrito",
            "quiere un brochure",
        ),
    ),
    (
        "no le interesa",
        (
            "me dijo que no gracias",
            "me rechazó",
            "dijo que no le sirve",
            "no lo necesitan",
            "me cortó en seco",
        ),
    ),
    (
        "están comparando con la competencia",
        (
            "están viendo otras opciones",
            "tienen otra cotización",
            "están evaluando varios proveedores",
            "me comparan con otro",
            "la competencia les ofreció algo",
        ),
    ),
    (
        "tiene miedo de que su equipo no lo use",
        (
            "le preocupa la implementación",
            "teme que sea complicado",
            "cree que va a ser difícil de usar",
            "le asusta el cambio",
            "no quiere arriesgarse",
        ),
    ),
    (
        "me dijo que le parece interesante",
        (
            "le gustó la propuesta",
            "se mostró interesado",
            "me respondió que suena bien",
            "quedó entusiasmado",
        ),
    ),
    # Textos
    (
        "escríbeme un correo en frío para {cargo}",
        (
            "redáctame un primer correo para {cargo}",
            "quiero contactar por primera vez a {cargo}",
            "necesito un mail para {cargo}",
            "arma un correo de prospección para {cargo}",
            "dame un texto para escribirle a {cargo}",
            "cómo le escribo a {cargo}",
            "qué le pongo en el correo a {cargo}",
        ),
    ),
    (
        "escríbeme una secuencia de 3 correos para {cargo}",
        (
            "necesito varios correos seguidos para {cargo}",
            "arma una cadencia de correos para {cargo}",
            "quiero una campaña de correos para {cargo}",
            "dame los correos de seguimiento para {cargo}",
        ),
    ),
    (
        "escríbeme un correo de seguimiento",
        (
            "redáctame un correo para insistir",
            "un correo para recordarle",
            "cómo le escribo a alguien que no respondió",
            "texto para volver a escribirle",
            "mensaje para retomar la conversación",
        ),
    ),
    (
        "escríbeme un correo de seguimiento después de una demo",
        (
            "correo para después de la presentación",
            "qué le escribo después de la reunión",
            "resumen para mandar después de la demo",
            "correo post reunión",
        ),
    ),
    (
        "escríbeme un whatsapp para un prospecto",
        (
            "mensaje corto para whatsapp",
            "qué le mando por whatsapp",
            "texto para whatsapp a un cliente",
            "un whatsapp para presentarme",
        ),
    ),
    (
        "escríbeme un whatsapp para confirmar la reunión de mañana",
        (
            "mensaje para recordarle la cita",
            "whatsapp para confirmar la llamada",
            "recordatorio de la reunión por whatsapp",
        ),
    ),
    (
        "escríbeme mensajes de linkedin para {cargo}",
        (
            "qué le escribo por linkedin a {cargo}",
            "invitación de linkedin para {cargo}",
            "cómo contacto a {cargo} por linkedin",
            "nota para conectar en linkedin con {cargo}",
        ),
    ),
    (
        "hazme un post de linkedin",
        (
            "quiero publicar algo en linkedin",
            "ideas de contenido para linkedin",
            "escríbeme una publicación",
            "qué publico esta semana",
            "contenido para mi perfil",
        ),
    ),
    (
        "qué le escribo a un cliente para pedirle un referido",
        (
            "cómo pido que me recomienden",
            "quiero que un cliente me presente a otros",
            "cómo consigo referidos",
            "pedir testimonio a un cliente",
        ),
    ),
    (
        "dame un guion de llamada en frío",
        (
            "qué digo cuando llamo",
            "script para llamar",
            "cómo empiezo una llamada en frío",
            "guion para el teléfono",
            "qué le digo cuando me contesta",
        ),
    ),
    (
        "escríbeme un correo para reactivar un cliente perdido",
        (
            "cómo recupero un cliente que se fue",
            "correo para un cliente que dejó de comprar",
            "quiero volver a hablar con un excliente",
        ),
    ),
    (
        "escríbeme un correo para invitar a un cliente a nuestro webinar",
        (
            "invitación a un evento",
            "correo para invitar a un desayuno",
            "invitar clientes a una charla",
        ),
    ),
    (
        "dame 5 asuntos para un correo en frío",
        (
            "ideas de asunto",
            "cómo titulo el correo",
            "asuntos que se abran",
            "qué pongo en el asunto",
        ),
    ),
    (
        "escríbeme un correo para felicitar a un cliente",
        (
            "qué le mando a un cliente que cumple un año con nosotros",
            "mensaje de aniversario para un cliente",
            "felicitar a un cliente por su logro",
            "texto para celebrar con un cliente",
        ),
    ),
    (
        "escríbeme un correo para pedir una reunión",
        (
            "cómo pido una reunión",
            "correo para agendar una cita",
            "cómo le pido 15 minutos",
            "pedir una llamada sin sonar insistente",
            "mensaje para conseguir una cita",
        ),
    ),
    (
        "escríbeme un correo para alguien que conocí en un evento",
        (
            "correo para alguien que vi en una feria",
            "escribirle a un contacto del congreso",
            "seguimiento a alguien que conocí en un evento",
        ),
    ),
    # Números y CRM
    (
        "cómo voy este mes",
        (
            "cómo me fue este mes",
            "cuál es mi avance",
            "resumen de mis resultados",
            "cómo están mis números",
            "dame mis métricas",
            "qué tal voy",
            "muéstrame mi desempeño",
        ),
    ),
    (
        "cuántos correos envié esta semana",
        (
            "cuántos mails mandé",
            "cuánto he enviado",
            "cuántas personas contacté",
            "volumen de envíos de la semana",
        ),
    ),
    (
        "cuánto vendí este mes",
        (
            "cuántas ventas cerré",
            "cuánto he facturado",
            "cuántos negocios gané",
            "qué tanto he vendido",
        ),
    ),
    (
        "qué oportunidad priorizo esta semana",
        (
            "a quién le dedico tiempo primero",
            "qué negocios muevo primero",
            "en qué cuentas me enfoco",
            "ordéname el pipeline",
            "cuáles están más cerca de cerrar",
            "qué hago primero hoy",
        ),
    ),
    (
        "cómo están mis campañas",
        (
            "cómo van mis envíos",
            "qué tal están funcionando mis campañas",
            "resultados de mis campañas",
            "mis campañas sirven",
        ),
    ),
    # Oficio y estrategia
    (
        "cómo consigo más respuestas",
        (
            "nadie me responde los correos",
            "tengo muy pocas respuestas",
            "cómo hago que contesten",
            "por qué no me responden",
            "cómo subo la tasa de respuesta",
            "mis correos no funcionan",
        ),
    ),
    (
        "dame una estrategia para conseguir reuniones",
        (
            "cómo lleno mi agenda",
            "cómo consigo más citas",
            "necesito más reuniones",
            "plan para agendar demos",
            "cómo genero oportunidades",
        ),
    ),
    (
        "¿Quién es mi cliente ideal?",
        (
            "a quién debería venderle",
            "cuál es mi público",
            "qué tipo de empresa me conviene",
            "a qué empresas les apunto",
            "cuál es mi nicho",
        ),
    ),
    (
        "prepárame la reunión de mañana",
        (
            "tengo una reunión y no sé qué preguntar",
            "cómo me preparo para la cita",
            "qué llevo a la reunión",
            "ayúdame con la reunión",
            "cómo preparo la demo",
        ),
    ),
    (
        "cómo armo mi rutina de prospección",
        (
            "cómo organizo mi semana de prospección",
            "cuánto tiempo dedico a prospectar",
            "rutina diaria de un vendedor",
            "cómo planifico mi día de ventas",
        ),
    ),
    (
        "cómo hago seguimiento sin molestar",
        (
            "cada cuánto le escribo",
            "cuántas veces insisto",
            "no quiero ser intenso",
            "cuándo dejo de insistir",
        ),
    ),
    (
        "cómo le hablo a un CFO",
        (
            "qué le importa al de finanzas",
            "cómo le vendo al gerente financiero",
            "cómo convenzo a finanzas",
            "argumentos para el área financiera",
        ),
    ),
    (
        "cómo califico un lead",
        (
            "cómo sé si vale la pena",
            "este prospecto es bueno",
            "cómo descarto prospectos",
            "qué es BANT",
        ),
    ),
    (
        "cómo cierro una venta",
        (
            "cómo logro que firme",
            "cómo hago para cerrar",
            "técnicas de cierre",
            "cómo lo llevo al sí",
        ),
    ),
    (
        "cómo negocio con compras",
        (
            "cómo manejo al área de compras",
            "compras me está apretando",
            "negociación con procurement",
        ),
    ),
    (
        "el cliente aceptó, ¿qué sigue?",
        (
            "ya me dijeron que sí",
            "ganamos el negocio y ahora qué",
            "firmaron, qué hago",
            "cerré la venta qué sigue",
        ),
    ),
    (
        "estoy desmotivada, nadie me responde",
        (
            "estoy cansada de prospectar",
            "me siento frustrado",
            "no me sale ninguna venta",
            "tengo una mala semana",
            "quiero tirar la toalla",
        ),
    ),
    (
        "me pidieron referencias de otros clientes",
        (
            "quieren hablar con un cliente nuestro",
            "me piden casos de éxito",
            "quiere saber quién más lo usa",
        ),
    ),
    (
        "me piden una prueba gratis",
        ("quieren probarlo sin pagar", "me piden un demo gratuito", "quieren un periodo de prueba"),
    ),
    (
        "me cancelaron la reunión",
        (
            "me movieron la cita",
            "no llegó a la llamada",
            "me dejaron plantada",
            "aplazaron la demo",
        ),
    ),
    (
        "cómo manejo a un cliente enojado",
        (
            "tengo un cliente molesto",
            "un cliente se quejó",
            "el cliente está furioso",
            "me reclamó un cliente",
        ),
    ),
    (
        "el decisor cambió, qué hago",
        ("cambió mi contacto", "se fue la persona con la que hablaba", "llegó un gerente nuevo"),
    ),
    (
        "cómo uso orkesta para prospectar",
        (
            "dónde busco clientes en orkesta",
            "cómo encuentro contactos aquí",
            "cómo uso prospección",
        ),
    ),
    (
        "cómo creo una campaña en orkesta",
        ("dónde hago una campaña", "cómo lanzo correos masivos aquí", "cómo programo envíos"),
    ),
    (
        "cómo uso Concertino",
        ("qué puedes hacer", "para qué sirves", "en qué me ayudas", "qué te puedo preguntar"),
    ),
    # Producto
    (
        "preséntame el producto en 30 segundos",
        (
            "cómo explico lo que vendemos",
            "dame el pitch",
            "cómo describo el producto",
            "qué digo cuando me preguntan qué hacemos",
        ),
    ),
    (
        "en una línea, por qué deberían comprarnos",
        (
            "por qué nosotros",
            "por qué elegirnos",
            "cuál es nuestra propuesta de valor",
            "qué nos hace distintos",
        ),
    ),
    (
        "qué precio tiene",
        (
            "cuánto vale",
            "cuánto cobramos",
            "cuál es la tarifa",
            "cuánto sale el servicio",
            "precios",
        ),
    ),
)

# Más formas, agregadas al probar con preguntas reales (se suman a las del banco).
MORE: dict[str, tuple[str, ...]] = {
    "no es prioridad ahora": (
        "por ahora están bien así",
        "por el momento no",
        "están bien como están",
        "me dijo que no tiene tiempo",
        "ahora están a full",
    ),
    "me dijo que es muy caro": (
        "el costo no lo justifica",
        "no le ve el retorno",
        "no le ve el valor",
        "dice que no vale lo que cobramos",
        "le parece mucha plata",
    ),
    "lo tiene que aprobar su jefe": (
        "lo tiene que ver con finanzas",
        "lo habla con su esposa que es socia",
        "lo platica con su socio",
        "lo lleva al directorio",
        "lo consulta con el dueño",
    ),
    "no me responde": (
        "me dejó de contestar después de la llamada",
        "le mandé la propuesta y nada",
        "no ha vuelto a escribir",
        "ya no me contesta",
        "dejó de responder",
    ),
    "preséntame el producto en 30 segundos": (
        "cómo me presento en un evento",
        "qué digo cuando me preguntan a qué me dedico",
        "cómo me presento en networking",
        "mi presentación de ascensor",
    ),
    "estoy desmotivada, nadie me responde": (
        "me siento estancada",
        "estoy bloqueado con las ventas",
        "no avanzo",
        "siento que no vendo nada",
    ),
    "qué oportunidad priorizo esta semana": (
        "qué cliente está más cerca de firmar",
        "cuál está por cerrar",
        "quién va a firmar primero",
        "qué negocio cierro primero",
    ),
    "escríbeme un correo para felicitar a un cliente": (
        "qué le escribo a un cliente que renovó",
        "agradecer a un cliente que renovó el contrato",
        "correo de agradecimiento a un cliente fiel",
    ),
    "no es la persona indicada": (
        "cómo consigo que me presenten con el gerente general",
        "cómo llego a quien decide",
        "cómo me presentan con el jefe de mi contacto",
    ),
    "quiere un descuento para firmar": (
        "me piden bajar el precio",
        "quiere el precio del año pasado",
        "me pide congelar el precio",
    ),
    "hazme un post de linkedin": (
        "qué publico en linkedin",
        "contenido para publicar",
        "publicación sobre un caso de éxito",
    ),
}

SLOTTED = re.compile(r"\{cargo\}")
# Formas del producto: si la ficha ya respondió con un dato, no se cambia por estas.
PRODUCT = {
    "preséntame el producto en 30 segundos",
    "en una línea, por qué deberían comprarnos",
    "qué precio tiene",
}


@dataclass
class Match:
    canonical: str
    example: str
    score: float


# --- 3. Similitud (conceptos + raíces + n-gramas, TF-IDF en memoria) ------------------------------

_STOP = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "de",
    "del",
    "que",
    "y",
    "a",
    "en",
    "por",
    "para",
    "me",
    "mi",
    "le",
    "lo",
    "se",
    "con",
    "es",
    "al",
    "su",
    "como",
    "mas",
    "muy",
    "ya",
    "si",
    "no",
    "nos",
    "te",
    "tu",
    "yo",
    "este",
    "esta",
    "eso",
    "esto",
}

# Conceptos de ventas: palabras distintas que significan lo mismo pesan como una sola idea.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "precio": (
        "precio",
        "caro",
        "costos",
        "costoso",
        "cobra",
        "tarifa",
        "valor",
        "inversion",
        "presupuesto",
        "dinero",
        "numeros",
        "gastos",
        "pagarlo",
        "alcanza",
        "cuesta",
        "vale",
    ),
    "descuento": (
        "descuento",
        "rebaj",
        "regate",
        "porcentaje",
        "precio especial",
        "mejor precio",
        "bajar",
    ),
    "pago": ("plazo", "credito", "cuotas", "financ", "pagar", "pago", "partes", "dias para pagar"),
    "pensar": (
        "pensar",
        "pensarlo",
        "analiz",
        "evalu",
        "revisar",
        "revisa",
        "consult",
        "almohada",
        "avisa",
        "confirma",
        "decidir",
        "dudas",
        "duda",
    ),
    "decisor": (
        "jefe",
        "socio",
        "junta",
        "comite",
        "gerencia",
        "directorio",
        "dueno",
        "aprobar",
        "aprueba",
        "visto bueno",
        "arriba",
        "esposa",
        "socia",
        "platica",
        "hablarlo",
        "ver con",
    ),
    "momento": (
        "momento",
        "ahora no",
        "despues",
        "luego",
        "mas adelante",
        "proximo",
        "proxima",
        "semana",
        "mes",
        "trimestre",
        "enero",
        "otro momento",
        "tiempo",
    ),
    "proveedor": (
        "proveedor",
        "agencia",
        "contrat",
        "otra empresa",
        "otra herramienta",
        "otra solucion",
        "ya tienen",
        "ya trabajan",
        "ya usan",
    ),
    "competencia": (
        "competencia",
        "competidor",
        "comparan",
        "comparando",
        "otra cotizacion",
        "opciones",
        "grande",
        "grandes",
    ),
    "silencio": (
        "no responde",
        "no contesta",
        "desaparec",
        "silencio",
        "sin respuesta",
        "no da senales",
        "visto",
        "no me ha",
    ),
    "interes": ("interes", "gusto", "suena bien", "entusias", "le gusto"),
    "rechazo": ("no le interesa", "no gracias", "rechaz", "no lo necesitan", "no sirve"),
    "informacion": (
        "informacion",
        "presentacion",
        "portafolio",
        "brochure",
        "por correo",
        "por escrito",
        "mande todo",
        "envie todo",
    ),
    "redactar": (
        "escrib",
        "redact",
        "texto",
        "mensaje",
        "correo",
        "mandame",
        "mando",
        "envio",
        "arma",
        "plantilla",
        "invitacion",
    ),
    "linkedin": ("linkedin", "publicar", "publicacion", "post", "perfil", "contenido"),
    "whatsapp": ("whatsapp", "chat"),
    "llamada": ("llamada", "llamar", "llamo", "telefono", "guion", "script", "cuelguen"),
    "reunion": (
        "reunion",
        "cita",
        "demo",
        "agenda",
        "primera reunion",
        "presentacion del producto",
    ),
    "numeros": (
        "cuantos",
        "cuantas",
        "cuanto",
        "numeros",
        "metricas",
        "resultados",
        "desempeno",
        "avance",
        "como voy",
        "como me fue",
    ),
    "ventas_cerradas": (
        "vendi",
        "vendimos",
        "facture",
        "cerre",
        "cerramos",
        "gane",
        "ganamos",
        "acept",
    ),
    "pipeline": (
        "priori",
        "calientes",
        "cerca de cerrar",
        "enfoco",
        "primero",
        "pipeline",
        "embudo",
        "oportunidades",
    ),
    "campana": ("campana", "envios", "secuencia", "cadencia"),
    "respuestas": ("respuestas", "contesten", "respondan", "tasa de respuesta", "abren"),
    "cliente_ideal": (
        "cliente ideal",
        "publico",
        "nicho",
        "a quien",
        "que empresas",
        "sector",
        "apunto",
        "conseguir clientes",
        "nuevos clientes",
    ),
    "cierre": ("cierro", "cerrar", "firme", "firmar", "cierre", "si definitivo"),
    "referido": (
        "referido",
        "recomiend",
        "referencia",
        "testimonio",
        "casos de exito",
        "presente a otros",
    ),
    "molesto": ("enojad", "molest", "furios", "queja", "reclam"),
    "cancelo": ("cancel", "plantad", "aplaz", "movieron", "no llego"),
    "animo": (
        "desmotiv",
        "frustr",
        "cansad",
        "toalla",
        "mala racha",
        "mala semana",
        "miedo",
        "desesperado",
    ),
    "producto": (
        "producto",
        "vendemos",
        "pitch",
        "explico",
        "propuesta de valor",
        "por que nosotros",
        "distintos",
    ),
    "aniversario": ("ano con nosotros", "aniversario", "cumplio", "felicit"),
}

_CONCEPT_RE = [
    (name, re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")"))
    for name, words in CONCEPTS.items()
]


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def _features(text: str) -> Counter[str]:
    folded = fold(normalize(text))
    words = [w for w in re.findall(r"[a-z0-9]+", folded) if w not in _STOP]
    stems = [_stem(w) for w in words]
    feats: Counter[str] = Counter()
    for name, rx in _CONCEPT_RE:
        if rx.search(folded):
            feats[f"k:{name}"] += 3  # una idea pesa más que una palabra
    feats.update(f"s:{w}" for w in stems)
    feats.update(f"b:{a}_{b}" for a, b in itertools.pairwise(stems))
    # ¿Quién habla? «me dijo / dice que» = el cliente (objeción); «cómo / qué hago» = consejo.
    if re.search(
        r"\b(me dijo|dice que|dijo que|me pidio|pide|piden|quiere|quieren|me contesto|me respondio)\b",
        folded,
    ):
        feats["k:dijo"] += 2
    if re.search(r"^(como|que hago|que le|dime|ayudame|necesito|quiero)\b", folded):
        feats["k:consejo"] += 1
    return feats


class Index:
    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self.pairs = pairs
        docs = [_features(text) for text, _ in pairs]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(d.keys())
        n = len(docs) or 1
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in docs]

    def _vec(self, feats: Counter[str]) -> dict[str, float]:
        v = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in feats.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def best(self, text: str) -> Match | None:
        q = self._vec(_features(text))
        top, score = -1, 0.0
        for i, v in enumerate(self.vecs):
            s = sum(w * v.get(t, 0.0) for t, w in q.items())
            if s > score:
                top, score = i, s
        if top < 0:
            return None
        example, canonical = self.pairs[top]
        return Match(canonical=canonical, example=example, score=score)


_LOCK = threading.Lock()
_BASE: Index | None = None


# Tercera tanda de formas reales (probadas con preguntas nuevas).
MORE_3: dict[str, tuple[str, ...]] = {
    "me dijo que es muy caro": (
        "no ve el beneficio",
        "no le encuentra valor",
        "dice que no le sirve tanto como para pagarlo",
    ),
    "no es prioridad ahora": (
        "prefiere esperar a ver cómo cierra el año",
        "quiere esperar",
        "lo deja para cuando mejore la situación",
    ),
    "qué oportunidad priorizo esta semana": (
        "cuál es la oportunidad más grande que tengo",
        "mis negocios más grandes",
        "a qué oportunidad le meto más energía",
        "en cuál me concentro",
        "qué cuenta trabajo hoy",
    ),
    "lo tiene que aprobar su jefe": (
        "su jefe no aprueba más gastos",
        "gerencia no autoriza",
        "no le firman el gasto",
    ),
    "cómo cierro una venta": (
        "cómo hago que un cliente indeciso decida",
        "el cliente no se decide",
        "cómo ayudo a decidir",
    ),
    "prepárame la reunión de mañana": (
        "cómo me presento ante un cliente grande",
        "tengo reunión con una empresa grande",
        "qué hago en mi primera reunión",
    ),
    "estoy desmotivada, nadie me responde": (
        "sin ganas de vender",
        "sin motivación",
        "no tengo energía para prospectar",
    ),
    "no me responde": (
        "llevo semanas sin noticias del cliente",
        "no sé nada de él hace días",
        "me ignora",
        "me ignoran",
        "envié la cotización y no hay respuesta",
        "mandé la propuesta y no contesta",
    ),
    "no es la persona indicada": (
        "cómo llego al dueño de la empresa",
        "cómo hablo con el que decide",
    ),
    "qué preguntas hago en una reunión de descubrimiento": (
        "qué le pregunto a un cliente para entender su problema",
        "preguntas para entender al cliente",
        "cómo descubro qué necesita",
        "qué le pregunto en la primera reunión",
        "cómo hago una reunión de descubrimiento",
    ),
}


def _base_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for canonical, others in BANK:
        for text in (canonical, *others, *MORE.get(canonical, ()), *MORE_3.get(canonical, ())):
            pairs.append((SLOTTED.sub("un gerente", text), canonical))
    known = {c for c, _ in BANK}
    for canonical, others in MORE_3.items():
        if canonical not in known:
            pairs.extend((text, canonical) for text in (canonical, *others))
    return pairs


def index(extra: list[tuple[str, str]] | None = None) -> Index:
    """El índice base (se arma una vez) más las frases aprendidas de la empresa."""
    global _BASE
    with _LOCK:
        if _BASE is None:
            _BASE = Index(_base_pairs())
    if not extra:
        return _BASE
    return Index(_BASE.pairs + extra)


# --- 4. Reescribir conservando lo concreto --------------------------------------------------------

STRONG = 0.6  # casi la misma frase: se usa aunque las reglas hayan respondido algo
ENOUGH = 0.4
WEAK = 0.45  # respondió con un dato suelto de la ficha: basta una idea parecida  # parecida: se usa solo si la pregunta no se entendió


def rewrite(question: str, cargo: str | None, extra: list[tuple[str, str]] | None = None,
            minimum: float = ENOUGH) -> Match | None:  # fmt: skip
    """La forma canónica más parecida (con el cargo de la pregunta original), o None."""
    m = index(extra).best(question)
    if m is None or m.score < minimum:
        return None
    who = f"un {cargo}" if cargo else "un gerente comercial"
    return Match(canonical=SLOTTED.sub(who, m.canonical), example=m.example, score=m.score)
