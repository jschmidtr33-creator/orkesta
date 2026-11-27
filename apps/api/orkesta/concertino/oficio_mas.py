# ruff: noqa: E501  (textos de enseñanza: líneas largas a propósito)
"""Más oficio de ventas B2B: lo que un buen jefe comercial enseña además de las etapas.

Ciclo de venta, manejo de objeciones como método, vender por valor (y más caro), confianza,
sonar menos a vendedor, preguntas difíciles, organizarse, recordación, cuántos seguimientos,
venderle a quien no te conoce y qué hacer después de una mala reunión. Sin cifras inventadas:
los números son rangos de orientación.
"""

from __future__ import annotations

from orkesta.concertino.oficio import Lesson

MORE_LESSONS: tuple[Lesson, ...] = (
    Lesson(
        "ciclo_venta",
        "El ciclo de venta B2B",
        (
            "ciclo de venta",
            "ciclo de ventas",
            "etapas de la venta",
            "proceso de venta",
            "proceso comercial",
            "embudo de ventas",
            "etapas del embudo",
        ),
        "El ciclo de venta es el camino que recorre un cliente desde que no te conoce hasta que compra (y vuelve a comprar). Conocerlo te dice qué hacer en cada momento y dónde se te caen los negocios.",
        (
            (
                "Las etapas",
                (
                    "Prospección: encontrar empresas y personas que encajan con tu cliente ideal.",
                    "Primer contacto: lograr una conversación (correo, llamada, LinkedIn).",
                    "Descubrimiento: entender el problema, su costo, quién decide y cuándo.",
                    "Propuesta o demo: mostrar cómo se resuelve SU problema, con opciones.",
                    "Negociación: ajustar condiciones sin regalar valor.",
                    "Cierre y arranque: firmar, empezar bien y medir el primer resultado.",
                ),
            ),
            (
                "Cómo usarlo",
                (
                    "Cada etapa termina con un acuerdo concreto (una reunión, un dato, una fecha): si no hay acuerdo, no avanzó.",
                    "Mide en qué etapa se te pierden más negocios: ahí está tu mejora más rentable.",
                    "En ORKESTA cada etapa del CRM tiene una probabilidad: úsala para pronosticar.",
                ),
            ),
        ),
        "Pregúntame «¿qué oportunidad priorizo esta semana?» para ver tu ciclo con datos reales.",
    ),
    Lesson(
        "objeciones",
        "Cómo manejar cualquier objeción",
        (
            "manejo objeciones",
            "manejar objeciones",
            "manejo de objeciones",
            "responder objeciones",
            "rebatir objeciones",
            "objeciones en general",
            "cualquier objecion",
        ),
        "Una objeción no es un «no»: es una duda que el cliente se atrevió a decir. El método sirve para todas: escuchar, reconocer, preguntar y recién después responder.",
        (
            (
                "El método en 4 pasos",
                (
                    "Escucha completo, sin interrumpir ni defenderte.",
                    "Reconoce: «Tiene sentido que lo pregunte», sin darle la razón ni discutir.",
                    "Pregunta para entender qué hay detrás: «¿Comparado con qué lo ve caro?», «¿Qué tendría que pasar para que sea prioridad?».",
                    "Responde a la duda real con un dato, un caso o una opción, y confirma: «¿Eso resuelve su duda?».",
                ),
            ),
            (
                "Para anticiparte",
                (
                    "Anota las 5 objeciones que más escuchas y prepara una respuesta con un caso para cada una.",
                    "Menciona tú la objeción más común antes de que la diga el cliente: le quita fuerza.",
                ),
            ),
        ),
        "Cuéntame la objeción exacta («me dijo que es caro») y te doy las respuestas listas.",
    ),
    Lesson(
        "valor",
        "Vender por valor (y no por precio)",
        (
            "vender por valor",
            "vendo por valor",
            "venta por valor",
            "por valor y no por precio",
            "vender mas caro",
            "vendo mas caro",
            "subir precios",
            "justificar el precio",
            "que no me comparen por precio",
            "dejar de competir por precio",
        ),
        "Si el cliente solo ve el precio, es porque no ve la diferencia. Vender por valor es hacer que compare el costo de su problema con tu precio, no tu precio con el de otro.",
        (
            (
                "Cómo hacerlo",
                (
                    "En el descubrimiento, cuantifica el problema con el cliente: «¿Cuánto les cuesta hoy, en tiempo o dinero?». Que el número lo diga él.",
                    "Presenta el precio después del valor y como inversión contra ese costo («Recuperas la inversión en X meses»).",
                    "Ofrece 2 o 3 opciones: la del medio suele ganar y el cliente elige cómo, no si compra.",
                    "Usa un caso parecido con un resultado concreto: vale más que cualquier argumento.",
                ),
            ),
            (
                "Para vender más caro",
                (
                    "Especialízate: un experto en un sector cobra más que un generalista.",
                    "Vende el resultado (menos fallas, más ventas), no las horas ni las funciones.",
                    "Si bajas el precio, baja también el alcance: nunca regales valor.",
                ),
            ),
        ),
    ),
    Lesson(
        "confianza",
        "Construir confianza con un cliente",
        (
            "confianza",
            "generar confianza",
            "construir confianza",
            "construyo confianza",
            "rapport",
            "relacion con el cliente",
            "credibilidad",
        ),
        "La confianza en B2B se gana con tres cosas: que entiendes su negocio, que cumples lo que dices y que te importa su resultado más que tu venta.",
        (
            (
                "En la conversación",
                (
                    "Prepárate: llega sabiendo de su empresa, su sector y su cargo.",
                    "Escucha más de lo que hablas y repite con tus palabras lo que entendiste.",
                    "Di «no sé, lo confirmo hoy» cuando no sepas: inventar destruye la confianza.",
                    "Si tu producto no es para ellos, dilo: te recomendarán por honesto.",
                ),
            ),
            (
                "En el tiempo",
                (
                    "Cumple cada pequeña promesa (enviar algo a la hora que dijiste): así se prueba la grande.",
                    "Comparte algo útil sin pedir nada a cambio (un dato, un contacto, una idea).",
                ),
            ),
        ),
    ),
    Lesson(
        "consultivo",
        "Dejar de sonar como vendedor",
        (
            "sonar como vendedor",
            "no sonar vendedor",
            "sonar menos vendedor",
            "venta consultiva",
            "consultivo",
            "no parecer vendedor",
            "sonar insistente",
            "sin sonar desesperado",
        ),
        "Suenas a vendedor cuando hablas de ti y de tu producto. Suenas a experto cuando hablas del problema del cliente y haces buenas preguntas.",
        (
            (
                "Cambia esto",
                (
                    "De «Te presento nuestra solución» a «¿Cómo lo están resolviendo hoy?».",
                    "De adjetivos («innovador», «líder») a hechos («en un cliente de [sector] bajamos [indicador] de X a Y»).",
                    "De perseguir («¿Pudiste ver mi correo?») a aportar («Vi esto y pensé en ustedes»).",
                ),
            ),
            (
                "Y esto",
                (
                    "Habla como habla tu cliente, sin jerga comercial.",
                    "Está dispuesto a perder la venta si no encaja: esa calma se nota y genera confianza.",
                ),
            ),
        ),
    ),
    Lesson(
        "preguntas_dificiles",
        "Hacer preguntas difíciles sin incomodar",
        (
            "pregunta dificil",
            "preguntas dificiles",
            "preguntar por el presupuesto",
            "preguntar quien decide",
            "preguntar el presupuesto",
            "preguntas incomodas",
            "como pregunto",
        ),
        "Las preguntas difíciles (presupuesto, quién decide, qué pasa si no hacen nada) son las que más información dan. Se hacen con permiso y con contexto.",
        (
            (
                "Cómo hacerlas",
                (
                    "Pide permiso: «¿Le puedo hacer una pregunta directa?».",
                    "Da contexto: «Para no hacerle perder el tiempo con una propuesta fuera de rango…».",
                    "Pregunta y calla: el silencio después de la pregunta es tu mejor aliado.",
                ),
            ),
            (
                "Ejemplos",
                (
                    "Presupuesto: «¿Tienen un rango pensado para esto o lo definimos según la propuesta?».",
                    "Decisión: «Además de usted, ¿quién más participa en la decisión?».",
                    "Urgencia: «¿Qué pasa si esto sigue igual 6 meses más?».",
                ),
            ),
        ),
    ),
    Lesson(
        "organizacion",
        "Organizarte como vendedor",
        (
            "me organizo",
            "organizarme",
            "organizar mi semana",
            "organizo mi semana",
            "organizo mi dia",
            "productividad",
            "gestion del tiempo",
            "agenda de ventas",
            "mi rutina",
            "como planifico",
        ),
        "Los vendedores que más venden no trabajan más horas: protegen bloques de tiempo para lo que genera ventas y dejan lo demás para después.",
        (
            (
                "Tu semana",
                (
                    "Bloques fijos de prospección (por ejemplo, 2 horas en la mañana de martes a jueves): sin correo ni reuniones en ese tiempo.",
                    "Reuniones agrupadas en ciertos días para no partir tu día en pedazos.",
                    "Cada viernes, 30 minutos para revisar tu embudo y planear la semana.",
                ),
            ),
            (
                "Tu día",
                (
                    "Empieza por lo que mueve dinero: respuestas positivas, propuestas vistas, tareas vencidas.",
                    "Usa las tareas con fecha del CRM: si no está agendado, no existe.",
                    "Pregúntame cada lunes «¿qué oportunidad priorizo esta semana?».",
                ),
            ),
        ),
    ),
    Lesson(
        "recordacion",
        "Que te recuerden (marca personal)",
        (
            "que me recuerden",
            "me recuerden",
            "recordacion",
            "marca personal",
            "posicionarme",
            "que me conozcan",
            "ser referente",
            "top of mind",
        ),
        "Te recuerdan cuando apareces seguido con algo útil y siempre con el mismo tema. La constancia pesa más que el talento.",
        (
            (
                "Cómo lograrlo",
                (
                    "Elige un tema y repítelo: «la persona que sabe de [tu tema]».",
                    "Publica en LinkedIn una o dos veces por semana sobre problemas de tus clientes (pídeme un post).",
                    "Comenta con criterio en publicaciones de tus prospectos: te ven antes de que les escribas.",
                    "Envía a tus contactos algo útil cada tanto (un dato, un caso) sin pedir nada.",
                ),
            ),
        ),
    ),
    Lesson(
        "cuantos_seguimientos",
        "Cuántos seguimientos hacer",
        (
            "cuantos seguimientos",
            "cuantas veces insisto",
            "cuantas veces escribo",
            "cuantos correos de seguimiento",
            "cuando dejo de insistir",
            "cuantos toques",
        ),
        "En frío, la mayoría de las respuestas llegan entre el segundo y el cuarto contacto. Después de 4 o 5 sin respuesta, rinde más cerrar con amabilidad y volver en unos meses.",
        (
            (
                "Una cadencia que funciona",
                (
                    "Día 1: primer correo, corto y sobre su problema.",
                    "Día 3 o 4: seguimiento con un dato o caso nuevo.",
                    "Día 7 a 10: otro ángulo (otro beneficio, otra pregunta) y, si puedes, LinkedIn o llamada.",
                    "Día 14 a 21: correo de cierre amable («¿Lo dejo para más adelante?»): suele ser el que más respuestas trae.",
                ),
            ),
            (
                "Reglas",
                (
                    "Cada seguimiento aporta algo nuevo: nunca «solo quería saber si viste mi correo».",
                    "Si responde, la cadencia se detiene: ORKESTA ya lo hace solo.",
                ),
            ),
        ),
    ),
    Lesson(
        "desconocido",
        "Venderle a una empresa que no te conoce",
        (
            "no me conoce",
            "no nos conoce",
            "no me conocen",
            "no nos conocen",
            "empresa nueva",
            "marca desconocida",
            "sin referencias",
            "somos nuevos",
            "somos pequenos",
        ),
        "Cuando no te conocen, el riesgo que perciben es lo que frena. Tu trabajo es bajar ese riesgo: prueba social, empezar en pequeño y que alguien hable por ti.",
        (
            (
                "Cómo bajar el riesgo",
                (
                    "Muestra casos de empresas parecidas con resultados concretos (aunque sean pocos).",
                    "Propón un piloto corto con criterios de éxito acordados.",
                    "Pide a un cliente que dé una referencia o te presente.",
                    "Cuida tu perfil de LinkedIn y tu sitio: te van a buscar antes de responder.",
                ),
            ),
        ),
    ),
    Lesson(
        "mala_reunion",
        "Qué hacer después de una mala reunión",
        (
            "mala reunion",
            "reunion salio mal",
            "me fue mal en la reunion",
            "reunion fue un desastre",
            "la cague en la reunion",
            "reunion no salio bien",
        ),
        "Una mala reunión casi nunca mata un negocio; lo que lo mata es no hacer nada después. Reconoce, aporta y propone un siguiente paso.",
        (
            (
                "Hoy mismo",
                (
                    "Anota qué falló (no entendiste algo, faltó un dato, no estaba quien decide).",
                    "Envía un correo de agradecimiento con un resumen y lo que faltó: «Me quedé con su pregunta sobre X: aquí va la respuesta».",
                    "Propón un paso corto: una llamada de 15 minutos o una demo con su caso.",
                ),
            ),
            (
                "Para la próxima",
                (
                    "Prepara 3 preguntas de descubrimiento y el caso más parecido antes de cada reunión (pídeme «prepárame la reunión»).",
                ),
            ),
        ),
    ),
)
