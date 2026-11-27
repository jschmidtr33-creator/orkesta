# ruff: noqa: E501  (textos para el vendedor: líneas largas a propósito)
"""Respuestas que un buen jefe comercial da y que no son una objeción ni un texto: qué hacer
después de ganar, cómo levantar el ánimo con datos y cómo hacer cada cosa dentro de ORKESTA."""

from __future__ import annotations

import re
from typing import Any

from orkesta.engine.normalize.text import fold

_WON = re.compile(
    r"\b(acepto|aceptaron|firmo|firmaron|cerramos|cerre|gane|ganamos|compro|compraron|me dijo que si|dijeron que si)\b"
    r".*\b(que sigue|y ahora|que hago|siguiente paso|proximo paso|como sigo)\b"
)
_MOOD = re.compile(
    r"\b(desmotivad[oa]|frustrad[oa]|cansad[oa]|agotad[oa]|no puedo mas|me siento mal|estresad[oa]|"
    r"(miedo|nervios|me da pena|verguenza|panico) (de |a )?(llamar|hablar|escribir|vender)|"
    r"quemad[oa]|me quiero rendir|tirar la toalla|nadie me (responde|contesta)|no me sale nada|mala racha)\b"
)
_UPSELL = re.compile(
    r"\b(pido|pedir|pedirle|consigo|conseguir|logro) (un |una )?(aumento|ampliacion|mas presupuesto)|\b(upsell|venta cruzada)\b|\bque (me )?compre(n)? mas\b"
)
_NEW_BOSS = re.compile(
    r"\b(decisor|champion|contacto|gerente|jefe|director|directora) (cambio|se fue|renuncio|salio|es nuevo|es nueva)\b"
    r"|\b(cambio|cambiaron) (el|al|de|la) (decisor|contacto|gerente|director|directora)\b|\bnuevo (decisor|gerente|director|contacto)\b"
)


_PUBLIC = re.compile(
    r"\b(gobierno|sector publico|entidad(es)? public|alcaldia|ministerio|licitacion\w*|secop|compras publicas|compranet|mercado publico)\b"
)


def wants_public(question: str) -> bool:
    return bool(_PUBLIC.search(fold(question)))


def public_sector() -> dict[str, Any]:
    return {
        "summary": "Al Estado no se le vende como a una empresa: se compra por procesos formales y con reglas públicas. Gana quien se prepara antes de que salga el proceso.",
        "options": [], "tips": [],
        "sections": [
            {"title": "Antes del proceso", "items": [
                "Regístrate como proveedor en el portal de compras públicas del país (por ejemplo, SECOP en Colombia, CompraNet en México o Mercado Público en Chile).",
                "Conversa con el área técnica que usa la solución: ellos ayudan a definir los requisitos del pliego.",
                "Revisa el plan anual de adquisiciones de la entidad: ahí se ve qué van a comprar y cuándo.",
            ]},
            {"title": "Durante", "items": [
                "Lee el pliego completo y cumple cada requisito al pie de la letra: un documento faltante descalifica.",
                "Haz preguntas dentro de los plazos oficiales; fuera del proceso no se negocia.",
                "Ten listos certificados, experiencia comprobable y estados financieros.",
            ]},
            {"title": "Ten en cuenta", "items": [
                "Los ciclos son largos y los pagos pueden tardar: inclúyelo en tu flujo de caja.",
                "Las compras pequeñas (mínima cuantía) son una buena primera puerta.",
            ]},
        ],
        "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


def wants_upsell(question: str) -> bool:
    return bool(_UPSELL.search(fold(question))) and not re.search(
        r"^(escribe|redacta|hazme|dame un correo)", fold(question)
    )


def wants_new_boss(question: str) -> bool:
    return bool(_NEW_BOSS.search(fold(question)))


def upsell(formal: bool) -> dict[str, Any]:
    return {
        "summary": "Para que un cliente amplíe, primero muestra el resultado que ya tuvo; la propuesta de crecer sale de ahí, no de tu meta.",
        "options": _opts(formal, (
            ("ampliar:resultado", "Desde el resultado", "Abrir con lo que ya ganó hace que ampliar suene a lógica, no a venta.",
             "Hola, {{nombre}}:\n\nRevisando estos meses: [resultado concreto]. Viendo cómo va, creo que llevarlo a [otra área o más volumen] podría repetir ese resultado. ¿Lo revisamos 20 minutos? [Te|Le] llevo los números."),
        )),
        "sections": [{"title": "Cómo hacerlo", "items": [
            "Junta los números de lo que ya logró: ahorro, tiempo o ventas.",
            "Identifica dónde más aplica (otra área, sede o producto) y quién decide ahí.",
            "Pídelo cerca de una renovación o justo después de un buen resultado, no en medio de un problema.",
        ]}],
        "tips": [], "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


def new_boss(formal: bool) -> dict[str, Any]:
    return {
        "summary": "Si cambió quien decide, la venta vuelve a empezar con esa persona: no des nada por acordado. Es también una oportunidad: la persona nueva quiere mostrar resultados rápido.",
        "options": _opts(formal, (
            ("nuevo:presentacion", "Presentarte a la persona nueva", "Breve, con lo que ya se avanzó y una pregunta sobre sus prioridades.",
             "Hola, {{nombre}}:\n\nBienvenida o bienvenido al cargo. Venía trabajando con [nombre anterior] en [el proyecto] y me gustaría poner[te|lo] al día en 20 minutos: qué se logró, qué estaba en curso y cómo encaja con [tus|sus] prioridades. ¿[Te|Le] queda bien esta semana?"),
        )),
        "sections": [{"title": "Qué hacer", "items": [
            "Pídele a tu contacto anterior (si sigue en la empresa) que te presente.",
            "Vuelve a descubrir: sus prioridades pueden ser distintas a las de su antecesor.",
            "Actualiza el contacto y el decisor en la oportunidad del CRM.",
        ]}],
        "tips": [], "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


_HOW = re.compile(
    r"\b(como|donde|en donde) (uso|usar|hago|hacer|creo|crear|configuro|configurar|conecto|conectar|veo|ver|lanzo|lanzar|envio|enviar|armo|armar|agrego|agregar|prospecto|prospectar)\b"
)

HOWTO: tuple[tuple[str, str, list[str]], ...] = (
    (
        r"concertino",
        "Usar Concertino",
        [
            "Pregúntame como le preguntarías a tu jefe comercial: «me dijo que es caro», «escríbeme un correo para un CFO», «¿qué oportunidad priorizo?».",
            "Pídeme textos: correos, secuencias, WhatsApp, mensajes y posts de LinkedIn, guiones de llamada y pedidos de referidos.",
            "Pregúntame por tus números («¿cómo voy este mes?») o por una cuenta («prepárame la reunión con [empresa]»).",
            "Si abres un contacto, una oportunidad o una campaña y me preguntas desde ahí, respondo con sus datos.",
            "Todo lo que escribo sale de la ficha de tu producto: mientras más completa, mejores respuestas.",
        ],
    ),
    (
        r"prospect|buscar (clientes|contactos|empresas)|encontrar clientes",
        "Prospectar en ORKESTA",
        [
            "Ve a Prospección y describe a quién buscas: cargo, sector, país y tamaño de empresa.",
            "Activa el filtro «Con correo» para ver solo a quien puedes escribir.",
            "Revisa «Empresas encontradas» y guarda las personas que encajan; con «Seleccionar los N resultados» guardas todo de una vez.",
            "Los correos se verifican al guardar: solo quedan los que ORKESTA confirmó.",
            "Con los contactos guardados, crea una campaña desde Campañas.",
        ],
    ),
    (
        r"campan|secuencia",
        "Crear y lanzar una campaña",
        [
            "En Campañas, crea una nueva y elige el buzón desde el que sale.",
            "Escribe los pasos (o pídeme «escríbeme una secuencia de 3 correos para…» y la copias).",
            "Elige la audiencia: una lista, un filtro o contactos puntuales.",
            "Revisa el máximo de correos por empresa al día y lanza. ORKESTA respeta el ritmo seguro de tu buzón y frena el seguimiento a quien responde, rebota o bloquea.",
        ],
    ),
    (
        r"propuesta|cotizacion",
        "Hacer y enviar una propuesta",
        [
            "En Propuestas, crea una nueva desde una oportunidad o un contacto: el borrador sale con sus datos.",
            "Agrega productos del catálogo, ajusta textos e imágenes por sección y revisa la vista previa.",
            "Envíala por correo desde la propuesta: sale desde tu Gmail con un botón para abrirla y firmarla.",
            "Si la creaste sin contacto, usa «Ligar a un contacto» junto al estado.",
        ],
    ),
    (
        r"gmail|buzon|correo|firma|outlook",
        "Conectar tu correo y tu firma",
        [
            "Ve a Configuración → Integraciones y conecta tu Gmail.",
            "En el mismo lugar configura tu firma: viaja intacta en todos los correos.",
            "En Campañas revisa la salud de tu dominio (SPF, DKIM y DMARC) para que no te bloqueen.",
        ],
    ),
    (
        r"automatiz|regla|tarea",
        "Automatizar tareas",
        [
            "En Automatización, crea una regla: cuándo pasa (por ejemplo, alguien pide reunión) y qué hace ORKESTA (crear una tarea, mover la oportunidad, avisarte).",
        ],
    ),
    (
        r"crm|oportunidad|pipeline|embudo",
        "Trabajar el CRM",
        [
            "En CRM ves tus oportunidades por etapa y las mueves a medida que avanzan.",
            "Abre una oportunidad para registrar llamadas, notas, reuniones y tareas con fecha.",
            "Pregúntame «¿qué oportunidad priorizo esta semana?» y te las ordeno.",
        ],
    ),
)


_REFS = re.compile(
    r"\b(pidio|pide|piden|pidieron|quiere|quieren|necesita|solicito)( que le (de|pase|mande))? (una |unas |un |algun |algunos )?"
    r"(referencia|referencias|caso|casos|testimonio|testimonios|cliente de referencia|clientes de referencia)\b"
    r"|\breferencias? de (otro|otros|algun|sus) client"
    r"|\b(ver|conocer|tener) (mas |otros )?(casos|ejemplos|clientes)\b"
)
_RATE = re.compile(
    r"\b((contesten|respondan|me respondan|me contesten) (mas|mas rapido|antes|pronto)|"
    r"(mas|mejores) respuestas|(subir|mejorar|aumentar) (la |mi )?tasa de (respuesta|apertura)|"
    r"(que|para que) me (respondan|contesten)|nadie abre mis correos|no abren mis correos|"
    r"mejor (dia|hora|horario|momento) para (enviar|escribir|mandar|llamar))\b"
)


def wants_references(question: str) -> bool:
    return bool(_REFS.search(fold(question)))


def wants_reply_rate(question: str) -> bool:
    return bool(_RATE.search(fold(question)))


def references(prueba: str, product: str, formal: bool) -> dict[str, Any]:
    """El prospecto pide referencias: el caso de la ficha y una llamada con un cliente."""
    from orkesta.concertino import playbook as pb

    case = (f"Con gusto. Un caso reciente: {prueba.rstrip('.')}. Si [te|le] sirve, [te|le] envío el detalle en una página."
            if prueba else f"Con gusto. [Te|Le] preparo un resumen de cómo nuestros clientes usan {product} y los resultados que han tenido.")  # fmt: skip
    call = ("También puedo coordinar una llamada de 10 minutos con uno de nuestros clientes para que [te|le] cuente su "
            "experiencia de primera mano. ¿[Te|Le] sirve esta semana o la próxima?")  # fmt: skip
    options = [
        {"code": "ref:caso", "name": "Compartir el caso de éxito", "why": "Responde de inmediato con un resultado concreto: muestra seguridad y mantiene el ritmo.",
         "text": pb.voice(case, formal), "record": None},
        {"code": "ref:llamada", "name": "Ofrecer una llamada de referencia", "why": "Para decisiones grandes: escuchar a un par convence más que cualquier documento.",
         "text": pb.voice(call, formal), "record": None},
    ]  # fmt: skip
    return {"summary": "Pedir referencias es una señal de compra: está validando antes de decidir. Responde rápido y con algo concreto.",
            "options": options,
            "tips": ["Antes de dar el contacto de un cliente, pídele permiso y cuéntale quién lo va a llamar.",
                     "Elige un cliente parecido (mismo sector o tamaño): la referencia pesa más.",
                     "Después de la llamada, pregunta qué le pareció y propone el siguiente paso con fecha."],
            "sections": [], "next_step": "", "missing": [] if prueba else ["prueba"], "suggestions": []}  # fmt: skip


def reply_rate() -> dict[str, Any]:
    return {
        "summary": "Para que te respondan más (y más rápido), lo que más mueve la aguja:",
        "options": [], "tips": [],
        "sections": [
            {"title": "El correo", "items": [
                "Asunto de 3 a 5 palabras, sin mayúsculas ni signos de venta: parece un correo entre colegas.",
                "Primera línea sobre la persona o su empresa, no sobre ti.",
                "Menos de 90 palabras y una sola pregunta fácil de contestar al final («¿Tiene sentido conversarlo?»).",
            ]},
            {"title": "A quién y cuándo", "items": [
                "Escribe al cargo que sufre el problema, no al más alto: responde más y te lleva a quien decide.",
                "Envía de martes a jueves, entre 7 y 10 a. m. de la hora del cliente.",
                "Haz 3 o 4 seguimientos en 2 a 3 semanas, cada uno con algo nuevo: la mayoría responde después del segundo.",
            ]},
            {"title": "Que llegue a la bandeja", "items": [
                "Revisa en Campañas que tu dominio tenga SPF, DKIM y DMARC: sin eso, muchos correos ni se ven.",
                "No envíes más de lo que tu buzón aguanta: ORKESTA ya cuida el ritmo diario.",
            ]},
        ],
        "next_step": "Pregúntame «¿cómo están mis campañas?» y te digo qué cambiar en la tuya.",
        "missing": [], "suggestions": ["¿Cómo están mis campañas?", "Escríbeme un correo en frío para un gerente comercial"],
    }  # fmt: skip


_VIEWED = re.compile(
    r"\b(abrio|vio|reviso|abrieron|vieron|revisaron|leyo|leyeron) (la |mi |nuestra )?(propuesta|cotizacion)\b.*\b(no (me )?(responde|contesta|respondio|contesto|dice nada)|sin respuesta|nada|silencio)\b"
    r"|\b(propuesta|cotizacion)\b.*\b\d+ veces\b"
    r"|\bseguimiento (a|de) (una|la|mi|las|mis) (propuesta|propuestas|cotizacion)\b"
    r"|\b(mande|envie|mandamos|enviamos|le mande|le envie)( la| mi| una)? (propuesta|cotizacion)\b.*\b(no (me )?(responde|contesta)|nada|sin respuesta|no hay respuesta|silencio)\b"
)
_RESCHEDULE = re.compile(
    r"\b(cancelo|cancelaron|cancela|me cancelo|movio|movieron|aplazo|aplazaron|postergo|postergaron|"
    r"no llego|no se conecto|no aparecio|me dejo plantad[oa]|reprogramar)\b.*\b(reunion|cita|llamada|demo|visita)\b"
    r"|\b(reunion|cita|llamada|demo)\b.*\b(cancelada|cancelo|cancelaron|no llego|no se conecto)\b"
)
_UPSET = re.compile(
    r"\b(enojad[oa]|molest[oa]|furios[oa]|brav[oa]|insatisfech[oa]|disgustad[oa]|"
    r"queja|reclamo|reclamando|se quejo|esta furioso)\b"
)


_TRIAL = re.compile(
    r"\b(prueba|demo|periodo|version) (gratis|gratuita|sin costo)\b|\bgratis (para )?(probar|una prueba)\b|\bfree trial\b"
)
_EARLY_PRICE = re.compile(
    r"\b(pide|pidio|piden|pregunta|pregunto|preguntan)( por)? (el )?precio\b.*\b(primer|primera|antes|de entrada|sin)\b|\bprecio (de entrada|en el primer)\b"
)
_GOAL = re.compile(r"\b(\d+) (reuniones|citas|demos)( al| por| cada)? (mes|semana)\b")


def wants_trial(question: str) -> bool:
    return bool(_TRIAL.search(fold(question)))


def wants_early_price(question: str) -> bool:
    return bool(_EARLY_PRICE.search(fold(question)))


def meeting_goal(question: str) -> tuple[int, str] | None:
    m = _GOAL.search(fold(question))
    if m is None or not re.search(
        r"\b(como|logro|lograr|llego|llegar|consigo|conseguir|meta|cuota|pide|piden|necesito|plan)\b",
        fold(question),
    ):
        return None
    return int(m.group(1)), m.group(4)


def goal_plan(goal: tuple[int, str]) -> dict[str, Any]:
    """La meta de reuniones convertida en actividad diaria, con tasas de referencia en frío."""
    n, period = goal
    monthly = n * 4 if period == "semana" else n
    contacts = -(-monthly * 100 // 2)  # ~2 de cada 100 contactados terminan en reunión
    per_day = -(-contacts // 20)
    return {
        "summary": f"Para {monthly} reuniones al mes, la cuenta es simple: en frío, de cada 100 personas contactadas suelen salir unas 2 reuniones. Necesitas unas {contacts} personas en campaña al mes (unas {per_day} por día hábil).",
        "options": [], "tips": [],
        "sections": [
            {"title": "El plan", "items": [
                f"Carga cada semana unas {-(-contacts // 4)} personas nuevas desde Prospección (filtra «Con correo»).",
                "Campaña de 3 o 4 correos en 2 a 3 semanas, con un problema concreto y una sola pregunta.",
                "Responde el mismo día a cada respuesta positiva con dos horarios concretos.",
                "Suma LinkedIn y una llamada a quienes abren varias veces: duplica la tasa de reunión.",
            ]},
            {"title": "Cómo bajar el esfuerzo", "items": [
                "Si mejoras de 2 a 4 reuniones por cada 100, necesitas la mitad de contactos: afina el cargo y el mensaje antes de subir el volumen.",
                "Pregúntame «¿cómo voy este mes?» para ver tus tasas reales y recalcular.",
            ]},
        ],
        "next_step": "", "missing": [], "suggestions": ["¿Cómo voy este mes?", "¿Cómo consigo más respuestas?"],
    }  # fmt: skip


def trial(formal: bool) -> dict[str, Any]:
    return {
        "summary": "Una prueba gratis sin reglas casi nunca termina en compra: nadie le dedica tiempo a lo que no pagó. Mejor un piloto corto, con criterios de éxito acordados antes.",
        "options": _opts(formal, (
            ("prueba:piloto", "Piloto con criterios de éxito", "Acordar antes qué resultado define el éxito convierte la prueba en una decisión de compra.",
             "Hola, {{nombre}}:\n\nMás que una prueba abierta, [te|le] propongo un piloto de 30 días con un objetivo claro que definamos juntos. Si lo logramos, avanzamos con el plan completo; si no, no [tienes|tiene] compromiso. ¿Lo armamos?"),
            ("prueba:demo", "Demo con sus datos", "Si no puedes regalar tiempo, una demo con su caso real da la misma seguridad en 30 minutos.",
             "Hola, {{nombre}}:\n\n¿Qué [te|le] parece si en vez de una prueba hacemos una sesión de 30 minutos con [tu|su] caso real? Así [ves|ve] el resultado sin invertir tiempo de [tu|su] equipo."),
        )),
        "tips": ["Si das prueba gratis, que sea corta, con un responsable del lado del cliente y una reunión de cierre agendada desde el día 1."],
        "sections": [], "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


def early_price(formal: bool, price: str) -> dict[str, Any]:
    rango = (
        f"Para que [tengas|tenga] una referencia: {price.rstrip('.')}."
        if price
        else "Para que [tengas|tenga] una referencia, nuestros planes van de [mínimo] a [máximo] según el alcance."
    )
    return {
        "summary": "Si pide el precio de entrada, dalo: esquivarlo genera desconfianza. Pero dalo como rango y sigue con una pregunta que te deje entender su caso.",
        "options": _opts(formal, (
            ("precio:rango", "Rango y pregunta", "Responder con transparencia y volver a su necesidad mantiene la conversación viva.",
             f"Hola, {{{{nombre}}}}:\n\nClaro. {rango} El valor exacto depende de [tu|su] volumen y de lo que necesite[s|] resolver. ¿Cuántas personas lo usarían y qué [te|le] gustaría mejorar primero?"),
        )),
        "tips": ["Nunca respondas solo con el número: termina siempre con una pregunta."],
        "sections": [], "next_step": "", "missing": [] if price else ["precio"], "suggestions": [],
    }  # fmt: skip


def wants_viewed(question: str) -> bool:
    return bool(_VIEWED.search(fold(question)))


def wants_reschedule(question: str) -> bool:
    return bool(_RESCHEDULE.search(fold(question)))


def wants_upset(question: str) -> bool:
    return bool(_UPSET.search(fold(question))) and not re.search(
        r"\b(correo|mensaje)\b.*\b(enojad|molest)", fold(question)
    )


def _opts(formal: bool, items: tuple[tuple[str, str, str, str], ...]) -> list[dict[str, Any]]:
    from orkesta.concertino import playbook as pb

    return [
        {"code": c, "name": n, "why": w, "text": pb.voice(t, formal), "record": None}
        for c, n, w, t in items
    ]


def viewed_proposal(formal: bool) -> dict[str, Any]:
    return {
        "summary": "Que la abra varias veces es buena señal: la está mostrando o comparando. No menciones que viste las aperturas; ayúdale a decidir.",
        "options": _opts(formal, (
            ("vista:dudas", "Resolver dudas", "Abre la puerta a la objeción escondida sin presionar.",
             "Hola, {{nombre}}:\n\n¿Pudiste revisar la propuesta con [tu|su] equipo? Si hay algo que ajustar (alcance, plazos o inversión), lo vemos en 15 minutos y [te|le] envío la versión final."),
            ("vista:decision", "Ayudar a decidir", "Dos caminos claros facilitan el sí; preguntar «¿qué opinas?» lo posterga.",
             "Hola, {{nombre}}:\n\nPara avanzar, ¿[prefieres|prefiere] arrancar con la opción completa o empezar con un piloto y sumar el resto después? Con [tu|su] respuesta dejo todo listo esta semana."),
            ("vista:llamada", "Pedir una llamada corta", "Si la abre seguido, quizá la está mostrando a quien decide: ofrécete a explicarla.",
             "Hola, {{nombre}}:\n\nSi [te|le] sirve, puedo explicarle la propuesta en 15 minutos a quien más participe en la decisión. ¿[Te|Le] funciona el jueves?"),
        )),
        "tips": ["Llama además de escribir: después de varias aperturas, una llamada corta suele destrabar.",
                 "Si no responde en 5 días, envía un último correo de cierre amable."],
        "sections": [], "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


def reschedule(formal: bool) -> dict[str, Any]:
    return {
        "summary": "Que cancelen es normal: lo importante es reprogramar en el mismo mensaje y con opciones concretas.",
        "options": _opts(formal, (
            ("reprog:opciones", "Reprogramar con dos horarios", "Dos horarios concretos se responden en segundos; «¿cuándo puedes?» queda sin respuesta.",
             "Hola, {{nombre}}:\n\nSin problema, entiendo que surgen cosas. ¿[Te|Le] queda bien el martes a las 10:00 o el jueves a las 15:00? Son 20 minutos."),
            ("reprog:ausencia", "Si no llegó", "Sin reproches: asume que fue un imprevisto y facilita el siguiente paso.",
             "Hola, {{nombre}}:\n\n[Te|Le] estuve esperando en la llamada de hoy; seguro surgió algo. ¿La movemos para esta semana? [Te|Le] propongo el jueves a la misma hora."),
        )),
        "tips": ["Si cancela dos veces, pregunta con honestidad si sigue siendo prioridad: ahorra tiempo a los dos.",
                 "Envía la invitación de calendario apenas confirme el nuevo horario."],
        "sections": [], "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


def upset(formal: bool) -> dict[str, Any]:
    return {
        "summary": "Con un cliente molesto, primero se escucha y se reconoce; la solución viene después. Defenderse o explicar demasiado lo enoja más.",
        "options": _opts(formal, (
            ("molesto:reconocer", "Reconocer y proponer", "Nombra el problema con sus palabras, asume lo que te toca y da una fecha concreta.",
             "Hola, {{nombre}}:\n\n[Tienes|Tiene] toda la razón: esto no debió pasar. Ya estoy revisándolo con el equipo y [te|le] escribo hoy antes de las 5 p. m. con la solución y los próximos pasos. Gracias por decírmelo directo."),
            ("molesto:llamada", "Pasar a una llamada", "Por escrito todo suena más duro: una llamada corta baja la temperatura.",
             "Hola, {{nombre}}:\n\nLamento mucho lo ocurrido. ¿Podemos hablar 10 minutos hoy? Quiero entender bien qué pasó y resolverlo [contigo|con usted] de inmediato."),
        )),
        "tips": ["No discutas quién tuvo la culpa: enfócate en qué vas a hacer y cuándo.",
                 "Cumple la fecha que diste, aunque sea para avisar que sigues en ello.",
                 "Cuando esté resuelto, escribe para confirmar que todo quedó bien."],
        "sections": [], "next_step": "", "missing": [], "suggestions": [],
    }  # fmt: skip


def wants_win(question: str) -> bool:
    return bool(_WON.search(fold(question)))


def wants_mood(question: str) -> bool:
    return bool(_MOOD.search(fold(question)))


def howto(question: str) -> dict[str, Any] | None:
    folded = fold(question)
    if not _HOW.search(folded) or not re.search(
        r"\b(orkesta|concertino|app|aplicacion|plataforma|aqui|prospeccion|campana|propuesta|automatizacion|crm|gmail|buzon|firma)\b",
        folded,
    ):
        return None
    for pattern, title, steps in HOWTO:
        if re.search(pattern, folded):
            return {"summary": f"{title}, paso a paso:", "options": [], "tips": [],
                    "sections": [{"title": "En ORKESTA", "items": steps}],
                    "next_step": "", "missing": [], "suggestions": []}  # fmt: skip
    return None


def after_win() -> dict[str, Any]:
    return {
        "summary": "¡Felicitaciones! Ganar es la mitad: ahora asegura que empiece bien y que el cliente vuelva a comprar y te recomiende.",
        "options": [], "tips": [],
        "sections": [
            {"title": "Hoy", "items": [
                "Agradece por escrito y confirma lo acordado: alcance, precio, fechas y quién es el contacto de cada lado.",
                "Envía el contrato u orden de compra y pregunta qué necesita su área de compras o legal para firmar.",
                "Marca la oportunidad como ganada en el CRM para que tus números y reportes queden al día.",
            ]},
            {"title": "Esta semana", "items": [
                "Agenda la reunión de inicio con quien va a usar el producto, no solo con quien compró.",
                "Define con el cliente cómo se medirá el éxito y en qué fecha lo revisarán juntos.",
            ]},
            {"title": "Cuando vea el primer resultado", "items": [
                "Pide un testimonio o un referido: pregúntame «qué le escribo a un cliente para pedirle un referido».",
                "Guarda el resultado como caso de éxito en la ficha del producto: lo uso en todos tus textos.",
            ]},
        ],
        "next_step": "Envía hoy el correo de confirmación con los próximos pasos y las fechas.",
        "missing": [], "suggestions": ["¿Qué le escribo para pedirle un referido?"],
    }  # fmt: skip


def mood() -> dict[str, Any]:
    return {
        "summary": "Te entiendo: en prospección hay semanas en que parece que nadie responde, y le pasa a todo el que vende. No es personal: en frío, que respondan entre 1 y 5 de cada 100 es normal. Lo que cambia la racha es el método, no el esfuerzo extra.",
        "options": [], "tips": [],
        "sections": [{"title": "Tres cosas para hoy", "items": [
            "Mira tus números reales (pregúntame «¿cómo voy esta semana?»): suele haber más avance del que se siente.",
            "Escríbele a las personas que ya mostraron interés antes de buscar nuevas: es donde está el sí más cercano.",
            "Cambia una sola cosa de tu correo (el asunto o la primera línea) y compara en una semana.",
        ]}, {"title": "Y para ti", "items": [
            "Celebra lo pequeño: una respuesta, una reunión, una propuesta vista. Cada una cuenta.",
            "Si llevas muchas horas, haz una pausa: una cabeza descansada escribe mejores correos.",
        ]}],
        "next_step": "", "missing": [],
        "suggestions": ["¿Cómo voy esta semana?", "¿Qué oportunidad priorizo esta semana?"],
    }  # fmt: skip
