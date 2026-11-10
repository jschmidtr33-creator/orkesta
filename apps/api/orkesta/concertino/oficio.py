# ruff: noqa: E501  (textos de enseñanza: líneas largas a propósito)
"""El oficio de vender B2B: lo que un vendedor senior le enseña a uno nuevo.

Lecciones de método (prospección, correo en frío, llamadas, LinkedIn, descubrimiento,
calificación, demos, propuestas, negociación, cierre, cuentas grandes y la forma de vender en
Latinoamérica). Concertino las usa cuando la pregunta es de «cómo se hace», no de un caso
concreto: para un caso concreto (una respuesta recibida, una cuenta, una objeción) manda el
motor de consejos con los datos de la empresa.

Sin cifras inventadas: cuando hay números, son rangos de orientación y se dicen como tales.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orkesta.engine.normalize.text import fold


@dataclass(frozen=True)
class Lesson:
    code: str
    title: str
    triggers: tuple[str, ...]  # frases o palabras que la delatan (sin tildes, minúsculas)
    summary: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    next_step: str = ""


LESSONS: tuple[Lesson, ...] = (
    Lesson(
        "icp",
        "Cliente ideal (ICP) y a quién apuntar",
        (
            "icp",
            "cliente ideal",
            "perfil de cliente",
            "a quien le vendo",
            "a quien apuntar",
            "segmentar",
            "segmentacion",
            "mercado objetivo",
            "buyer persona",
            "que empresas buscar",
            "nicho",
        ),
        "Antes de prospectar, define a quién NO le vas a vender. Un buen cliente ideal ahorra más tiempo que cualquier herramienta.",
        (
            (
                "Cómo definirlo",
                (
                    "Mira tus mejores 10 clientes (los que compraron rápido, pagan bien y se quedan) y busca qué tienen en común: sector, tamaño, país, tecnología que usan, momento que viven.",
                    "Escribe el ICP en una frase: «Empresas de [sector] con [tamaño] en [país] que [situación o disparador]».",
                    "Define también los descartes: tamaños, sectores o situaciones donde pierdes o el ciclo se vuelve eterno.",
                ),
            ),
            (
                "Del ICP a las personas",
                (
                    "En cada cuenta hay tres papeles: quien sufre el problema (usuario), quien lo paga (decisor económico) y quien puede frenarlo (TI, compras, legal).",
                    "Empieza por quien sufre el dolor con más intensidad; sube al decisor cuando ya tengas una razón concreta para hablarle.",
                    "Arma 2 o 3 buyer personas con: qué le miden, qué le quita el sueño, qué palabras usa y qué le hace perder credibilidad a un vendedor.",
                ),
            ),
            (
                "Señales de buen momento",
                (
                    "Nombramientos recientes (un gerente nuevo quiere mostrar resultados en sus primeros 90 días).",
                    "Expansión: nuevas sedes, rondas de inversión, contratación masiva en un área.",
                    "Cambios regulatorios, fusiones, migraciones de tecnología o una mala noticia pública del sector.",
                ),
            ),
        ),
        "Escribe hoy tu ICP en una frase y busca 20 cuentas que lo cumplan en Prospección.",
    ),
    Lesson(
        "tiers",
        "Priorizar cuentas: niveles 1, 2 y 3",
        (
            "priorizar cuentas",
            "tier",
            "niveles de cuentas",
            "cuentas estrategicas",
            "abm",
            "account based",
            "cuantas cuentas",
            "por cual empiezo",
            "lista de cuentas",
        ),
        "No todas las cuentas merecen el mismo esfuerzo. Reparte tu tiempo según el tamaño del premio y la probabilidad.",
        (
            (
                "Cómo repartir",
                (
                    "Nivel 1 (5 a 15 cuentas): las de mayor valor. Investigación profunda, mensajes uno a uno, varios contactos por cuenta y canales combinados.",
                    "Nivel 2 (30 a 80 cuentas): personalización por segmento o sector, con un detalle propio por empresa.",
                    "Nivel 3 (el resto): secuencias por segmento, bien escritas, con poca personalización individual.",
                ),
            ),
            (
                "Multithreading",
                (
                    "En cuentas grandes, nunca dependas de una sola persona: busca de 3 a 5 contactos en distintas áreas y niveles.",
                    "Mensajes distintos para cada rol: al operativo le hablas de su día a día, al gerente de su meta y al directivo del impacto en el negocio.",
                    "Si una persona sale de la empresa, la oportunidad no se muere contigo.",
                ),
            ),
        ),
        "Marca tus 10 cuentas nivel 1 y busca al menos 3 contactos en cada una.",
    ),
    Lesson(
        "prospeccion",
        "Rutina de prospección que sí funciona",
        (
            "prospectar",
            "prospeccion",
            "conseguir clientes",
            "conseguir leads",
            "generar leads",
            "generar oportunidades",
            "llenar el pipeline",
            "pipeline vacio",
            "nuevos clientes",
            "rutina",
            "outbound",
        ),
        "La prospección es un hábito, no una campaña. Bloques fijos, volumen sostenido y mensajes relevantes ganan a los esfuerzos heroicos de fin de mes.",
        (
            (
                "La rutina",
                (
                    "Bloquea en el calendario de 1,5 a 2 horas diarias solo para prospectar, idealmente en la mañana, sin correo ni reuniones.",
                    "Trabaja por lotes: primero investiga 10 a 20 contactos, luego escribe, luego llama. Cambiar de tarea cada 2 minutos mata el ritmo.",
                    "Cada semana: cuentas nuevas a la lista, secuencias activas y seguimiento de quienes respondieron.",
                ),
            ),
            (
                "Qué medir",
                (
                    "Actividad: contactos nuevos por semana y toques realizados.",
                    "Conversión: tasa de respuesta, tasa de respuestas positivas y reuniones agendadas.",
                    "Si la tasa de respuesta es baja, el problema suele ser la lista (a quién) antes que el mensaje (qué).",
                ),
            ),
            (
                "Errores típicos",
                (
                    "Prospectar solo cuando el pipeline está vacío: el resultado llega 1 a 3 meses tarde.",
                    "Escribir a todos con el mismo mensaje y sorprenderse de que nadie responda.",
                    "Rendirse después de 1 o 2 intentos: la mayoría de respuestas llegan del tercer toque en adelante.",
                ),
            ),
        ),
        "Bloquea mañana 90 minutos de prospección y protégelo como una reunión con un cliente.",
    ),
    Lesson(
        "correo_frio",
        "Cómo escribir un correo en frío que respondan",
        (
            "correo en frio",
            "correo frio",
            "cold email",
            "primer correo",
            "escribir un correo",
            "mail en frio",
            "como escribo",
            "plantilla de correo",
            "que le escribo",
            "email de prospeccion",
            "redactar correo",
        ),
        "Un correo en frío no vende el producto: vende la conversación. Corto, sobre la otra persona y con una pregunta fácil de responder.",
        (
            (
                "Estructura (60 a 120 palabras)",
                (
                    "1. Por qué le escribes a ESA persona hoy: una observación sobre su empresa, su cargo o una señal reciente.",
                    "2. El problema que probablemente vive, dicho con sus palabras, no con las tuyas.",
                    "3. Una prueba breve: qué lograste con alguien parecido (empresa similar, mismo sector o cargo).",
                    "4. Una pregunta de interés, no de agenda: «¿Tiene sentido conversarlo?» o «¿Es una prioridad este semestre?».",
                ),
            ),
            (
                "Reglas de oro",
                (
                    "Nada de «Espero que estés bien» ni presentaciones largas de tu empresa en el primer párrafo.",
                    "Una sola idea y un solo pedido por correo.",
                    "Sin adjuntos ni muchos enlaces en el primer contacto: dañan la entrega y nadie los abre.",
                    "Léelo en voz alta: si suena a folleto, reescríbelo como se lo dirías en un café.",
                ),
            ),
            (
                "El asunto",
                (
                    "De 2 a 5 palabras, en minúscula natural, que parezca escrito por una persona: «idea para [área]», «[empresa] y [tema]».",
                    "Evita mayúsculas, signos de exclamación y palabras de spam («gratis», «oferta», «urgente»).",
                ),
            ),
        ),
        "Pídeme «crea el correo de prospección» y te lo redacto con los datos de tu producto.",
    ),
    Lesson(
        "secuencia",
        "Secuencias y seguimientos: cuántos y cada cuánto",
        (
            "secuencia",
            "cadencia",
            "cuantos seguimientos",
            "seguimiento",
            "follow up",
            "no responde",
            "nadie responde",
            "cada cuanto",
            "cuantos correos",
            "cuantos toques",
            "insistir",
            "no me contestan",
        ),
        "La mayoría de las respuestas llegan después del segundo o tercer toque. El seguimiento no es insistir: es aportar algo nuevo cada vez.",
        (
            (
                "Una cadencia de orientación (2 a 3 semanas)",
                (
                    "Día 1: correo inicial + visita al perfil de LinkedIn.",
                    "Día 3: llamada; si no contesta, mensaje de voz corto o WhatsApp si ya hay relación.",
                    "Día 5: segundo correo con un ángulo nuevo (un caso, un dato del sector, una pregunta distinta).",
                    "Día 8 a 10: conexión o mensaje en LinkedIn comentando algo suyo.",
                    "Día 12 a 15: tercer correo, más corto, con otra prueba o recurso útil.",
                    "Día 18 a 21: correo de cierre amable: «Entiendo que no es prioridad; ¿lo retomo en unos meses?».",
                ),
            ),
            (
                "Cada seguimiento aporta algo",
                (
                    "Nunca «Solo quería saber si viste mi correo»: da una razón nueva para responder.",
                    "Alterna formatos: pregunta, caso de éxito, dato del sector, invitación a un contenido útil.",
                    "Si responde «ahora no», pregunta cuándo sería buen momento y agenda la tarea con esa fecha.",
                ),
            ),
            (
                "Cuándo parar",
                (
                    "Si dice que no, para y agradece: un «no» respetado hoy puede ser un «sí» en 6 meses.",
                    "Si tras 6 a 8 toques no hay señal, pásalo a nutrición (contenido cada 1 o 2 meses) y vuelve con una señal nueva.",
                ),
            ),
        ),
        "En Campañas puedes armar estos pasos con días de espera y ORKESTA se detiene solo cuando la persona responde.",
    ),
    Lesson(
        "llamada_frio",
        "Llamadas en frío: apertura, permiso y cierre",
        (
            "llamada en frio",
            "llamar en frio",
            "cold call",
            "como llamo",
            "guion de llamada",
            "llamada",
            "telefono",
            "que digo cuando llamo",
            "llamadas",
        ),
        "Una llamada en frío dura poco: tu objetivo no es vender, es ganarte 2 minutos más y salir con una reunión agendada.",
        (
            (
                "Los primeros 20 segundos",
                (
                    "Nombre, empresa y la razón de la llamada en una frase. Sin «¿Cómo estás?» robótico.",
                    "Pide permiso honesto: «Te llamo sin aviso, ¿me das 30 segundos para contarte por qué y decides si seguimos?».",
                    "La razón tiene que ser sobre ella o él: «Vi que están abriendo operación en Lima y suele pasar que…».",
                ),
            ),
            (
                "La conversación",
                (
                    "Lanza una hipótesis del problema y pregunta: «¿Les pasa algo así o lo tienen resuelto?».",
                    "Escucha más de lo que hablas. Si da una señal de dolor, profundiza con 1 o 2 preguntas.",
                    "No hagas la demo por teléfono: el objetivo es la reunión.",
                ),
            ),
            (
                "El cierre",
                (
                    "Propón dos horarios concretos: «¿Te sirve el martes a las 10 o el jueves a las 4?».",
                    "Confirma en el momento con una invitación de calendario y una agenda de 3 líneas.",
                    "Si dice que no es buen momento, pregunta cuándo sí y deja la tarea con esa fecha.",
                ),
            ),
        ),
        "Pídeme «guion de llamada» y lo armo con los datos de tu producto.",
    ),
    Lesson(
        "linkedin",
        "LinkedIn y venta social",
        (
            "linkedin",
            "venta social",
            "social selling",
            "perfil de linkedin",
            "conectar en linkedin",
            "mensaje de linkedin",
            "invitacion de linkedin",
            "inmail",
            "publicar en linkedin",
        ),
        "En LinkedIn primero te investigan y después te responden. Tu perfil es tu primera reunión.",
        (
            (
                "Tu perfil",
                (
                    "Titular que diga a quién ayudas y en qué, no solo tu cargo.",
                    "Sección «Acerca de» escrita para tu cliente: su problema, cómo ayudas y una prueba.",
                    "Foto profesional y actividad reciente: un perfil abandonado resta credibilidad.",
                ),
            ),
            (
                "Conectar",
                (
                    "Invitación sin pitch: una razón breve y real para conectar (un tema común, su publicación, su sector).",
                    "Después de que acepte, no vendas de inmediato: comenta o aporta algo útil primero.",
                    "Interactúa con sus publicaciones unos días antes de escribirle: tu nombre ya le sonará.",
                ),
            ),
            (
                "Publicar",
                (
                    "1 a 3 publicaciones por semana sobre problemas de tus clientes, aprendizajes y casos (sin vender en cada una).",
                    "Las historias concretas funcionan mejor que las opiniones genéricas.",
                ),
            ),
        ),
        "Revisa hoy tu titular de LinkedIn: ¿dice a quién ayudas?",
    ),
    Lesson(
        "whatsapp",
        "WhatsApp en ventas B2B",
        ("whatsapp", "mensaje de whatsapp", "escribir por whatsapp", "wsp", "wasap"),
        "En Latinoamérica WhatsApp acelera todo, pero mal usado quema la relación. Úsalo cuando ya hay permiso o confianza.",
        (
            (
                "Cuándo sí",
                (
                    "Después de una primera conversación, para confirmar reuniones o enviar algo prometido.",
                    "Cuando la persona te dio su número o te escribió por ahí.",
                    "Para recordatorios cortos el día de la reunión.",
                ),
            ),
            (
                "Cómo",
                (
                    "Mensajes cortos, en horario laboral y con tu nombre y empresa en el primero.",
                    "Audios solo si la otra persona los usa contigo; si no, texto.",
                    "Nunca reemplaces la propuesta formal por un WhatsApp: envíala por correo y avisa por WhatsApp.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "descubrimiento",
        "Reunión de descubrimiento: qué preguntar",
        (
            "descubrimiento",
            "discovery",
            "primera reunion",
            "que pregunto",
            "que preguntar",
            "preguntas",
            "spin",
            "levantamiento",
            "entender al cliente",
            "diagnostico",
            "necesidades del cliente",
        ),
        "En la primera reunión gana quien mejor entiende el problema, no quien mejor presenta el producto. Habla 30 % y escucha 70 %.",
        (
            (
                "Agenda de la reunión",
                (
                    "Abre acordando tiempo, objetivo y lo que pasará al final: «Si tiene sentido, definimos un siguiente paso; si no, te lo digo».",
                    "Pregunta el contexto: cómo lo hacen hoy, quién participa y qué herramientas usan.",
                    "Busca el dolor y su impacto: qué cuesta no resolverlo (dinero, tiempo, riesgo, reputación).",
                    "Cierra con un siguiente paso con fecha y responsables.",
                ),
            ),
            (
                "Preguntas que abren (método SPIN)",
                (
                    "Situación: «¿Cómo resuelven hoy [el tema], paso a paso?».",
                    "Problema: «¿Dónde se les complica más? ¿Qué pasa cuando falla?».",
                    "Implicación: «Si sigue así 6 meses, ¿qué impacto tiene en tus metas?».",
                    "Necesidad: «Si lo resolvieran, ¿qué cambiaría para ti y tu equipo?».",
                ),
            ),
            (
                "Señales a escuchar",
                (
                    "Urgencia real: una fecha, una meta o una consecuencia concreta.",
                    "Quién más decide y cómo compran (proceso, presupuesto, compras, legal).",
                    "Qué intentaron antes y por qué no funcionó.",
                ),
            ),
        ),
        "Pídeme «prepárame la reunión» con el contacto abierto y te doy preguntas para su nivel y área.",
    ),
    Lesson(
        "calificacion",
        "Calificar oportunidades: BANT y MEDDICC",
        (
            "calificar",
            "calificacion",
            "bant",
            "meddic",
            "meddicc",
            "meddpicc",
            "es una buena oportunidad",
            "oportunidad real",
            "lead calificado",
            "sql",
            "mql",
            "vale la pena",
        ),
        "Calificar es decidir dónde NO invertir tiempo. Una oportunidad sin dolor, sin decisor o sin fecha es una conversación, no un negocio.",
        (
            (
                "Lo mínimo (BANT)",
                (
                    "Presupuesto: ¿existe o se puede conseguir para esto?",
                    "Autoridad: ¿quién firma y quién influye?",
                    "Necesidad: ¿qué problema concreto resuelve y cuánto cuesta no resolverlo?",
                    "Tiempo: ¿hay una fecha o un evento que obligue a decidir?",
                ),
            ),
            (
                "Para cuentas grandes (MEDDICC)",
                (
                    "Métricas: el resultado medible que espera el cliente.",
                    "Decisor económico: quién tiene la última palabra sobre el dinero.",
                    "Criterios de decisión: cómo van a comparar opciones.",
                    "Proceso de decisión: pasos, aprobaciones, compras, legal, fechas.",
                    "Identificar el dolor: el problema y su impacto en el negocio.",
                    "Campeón: alguien adentro que quiere que esto pase y te ayuda a venderlo.",
                    "Competencia: con quién más hablan, incluido «no hacer nada».",
                ),
            ),
            (
                "Regla práctica",
                (
                    "Si después de la segunda reunión no conoces el proceso de decisión ni tienes un campeón, la oportunidad está en riesgo: pregúntalo directo.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "demo",
        "Demos que venden",
        ("demo", "demostracion", "hacer una demo", "dar una demo", "demo que venda"),
        "Una demo no es un recorrido por todas las funciones: es mostrar cómo se resuelve SU problema, con SUS palabras.",
        (
            (
                "Antes",
                (
                    "No hagas demo sin descubrimiento: si no sabes qué le duele, mostrarás lo que no le importa.",
                    "Confirma quién asiste y qué quiere ver cada uno.",
                ),
            ),
            (
                "Durante",
                (
                    "Empieza recordando lo que te contaron: «Me dijeron que hoy les pasa X; les muestro cómo quedaría».",
                    "Muestra 2 o 3 momentos clave, no 20 funciones. Lo que no resuelve su problema, sobra.",
                    "Pregunta cada pocos minutos: «¿Esto cómo lo harían hoy?», «¿Les serviría así?».",
                    "Deja tiempo al final para preguntas y para acordar el siguiente paso.",
                ),
            ),
            (
                "Después",
                (
                    "Envía el mismo día un resumen: lo que escuchaste, lo que mostraste y el siguiente paso con fecha.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "propuesta",
        "Propuestas que se aprueban",
        (
            "propuesta comercial",
            "armar una propuesta",
            "armo una propuesta",
            "enviar propuesta",
            "escribir una propuesta",
            "que incluir en la propuesta",
            "propuesta que se apruebe",
            "cotizacion",
        ),
        "La propuesta confirma lo que ya acordaron en las reuniones; no debería traer sorpresas. Si es la primera vez que ven el precio, llegaste tarde.",
        (
            (
                "Qué incluir",
                (
                    "Resumen de su situación y del problema, con sus palabras.",
                    "El resultado esperado y cómo se medirá.",
                    "La solución: alcance, entregables y plazos.",
                    "Inversión con opciones (por ejemplo, básica, recomendada y completa) para que elijan cómo, no si.",
                    "Próximos pasos y fecha de vigencia.",
                ),
            ),
            (
                "Cómo presentarla",
                (
                    "Preséntala en una reunión, no solo por correo: así resuelves dudas en el momento.",
                    "Antes de enviarla, valida el precio verbalmente: «¿Una inversión de este rango está dentro de lo que imaginaban?».",
                    "Haz seguimiento a los 2 o 3 días con una pregunta concreta, no con «¿La pudiste ver?».",
                ),
            ),
        ),
        "En Propuestas puedes armarla con tu marca y que el cliente la acepte y firme en línea.",
    ),
    Lesson(
        "negociacion",
        "Negociar sin regalar el margen",
        (
            "negociar",
            "negociacion",
            "descuento",
            "rebaja",
            "me piden descuento",
            "bajar el precio",
            "regatear",
            "compras",
            "procurement",
            "contrapropuesta",
        ),
        "En una negociación nunca se da sin recibir. Cada concesión tiene que costar algo del otro lado.",
        (
            (
                "Principios",
                (
                    "Ancla primero: tu primera propuesta marca el rango de toda la conversación.",
                    "Si das un descuento, pide algo a cambio: plazo más largo, pago anticipado, más volumen, caso de éxito o firma antes de una fecha.",
                    "Concesiones cada vez más pequeñas: así el cliente entiende que se acerca el límite.",
                    "Negocia con quien decide: si negocias con alguien sin autoridad, cada «sí» tuyo es un piso para la siguiente ronda.",
                ),
            ),
            (
                "Frases útiles",
                (
                    "«Puedo revisarlo si lo cerramos este mes. ¿Eso es viable de su lado?».",
                    "«Si quitamos [componente], el precio baja. ¿Qué parte es la menos importante para ustedes?».",
                    "«Antes de hablar de precio, ¿estamos de acuerdo en que esta es la solución correcta?».",
                ),
            ),
            (
                "Con el área de compras",
                (
                    "Compras tiene la tarea de bajar el precio: es su trabajo, no algo personal.",
                    "Llega con el valor ya acordado con el área usuaria y con el decisor.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "cierre",
        "Cerrar el negocio",
        (
            "cerrar",
            "cierre",
            "como cierro",
            "cerrar la venta",
            "cerrar el trato",
            "firmar",
            "que firme",
            "plan de accion mutuo",
            "mutual action plan",
        ),
        "Los negocios no se cierran al final: se cierran paso a paso, con acuerdos pequeños en cada reunión.",
        (
            (
                "Plan de acción mutuo",
                (
                    "Escribe con el cliente los pasos que faltan hasta el inicio: aprobaciones, compras, legal, firma, arranque.",
                    "Cada paso con responsable y fecha. Trabajen hacia atrás desde la fecha en que quieren ver resultados.",
                    "Compártelo y revísenlo en cada reunión: si una fecha se mueve, lo ven juntos.",
                ),
            ),
            (
                "Señales de cierre",
                (
                    "Preguntan por implementación, plazos, contrato o quién será su contacto.",
                    "Involucran a nuevas personas (compras, legal, TI).",
                    "Cuando aparecen, pregunta directo: «¿Qué falta para que podamos empezar?».",
                ),
            ),
            (
                "Si se enfría",
                (
                    "Escribe al campeón con una pregunta honesta: «¿Cambió algo de prioridad? Prefiero saberlo».",
                    "Ofrece algo nuevo: un caso, una reunión con un cliente similar o una prueba acotada.",
                    "Si no hay respuesta, cierra la oportunidad como perdida con el motivo y agenda retomarla.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "pipeline",
        "Pipeline y pronóstico ordenados",
        (
            "pipeline",
            "embudo",
            "pronostico",
            "forecast",
            "etapas",
            "crm",
            "cuantas oportunidades",
            "cobertura",
            "meta de ventas",
            "cuota",
        ),
        "Un pipeline sano es la diferencia entre adivinar y saber si vas a llegar a la meta.",
        (
            (
                "Higiene semanal",
                (
                    "Cada oportunidad con siguiente paso y fecha. Sin siguiente paso, no es oportunidad.",
                    "Fechas de cierre realistas: moverlas cada semana es señal de que no está calificada.",
                    "Cierra las perdidas con su motivo: aprenderás más de ellas que de las ganadas.",
                ),
            ),
            (
                "Cobertura",
                (
                    "Como orientación, necesitas en pipeline de 3 a 4 veces tu meta del periodo, según tu tasa de cierre.",
                    "Si tu tasa de cierre es baja, necesitas más cobertura o mejor calificación, no solo más actividad.",
                ),
            ),
        ),
        "En CRM puedes ver tus oportunidades por etapa y las que no tienen actividad reciente.",
    ),
    Lesson(
        "decisores",
        "Hablarle a cada nivel: C-level, directores, gerentes",
        (
            "c level",
            "ceo",
            "gerente general",
            "director",
            "como le hablo",
            "alta direccion",
            "ejecutivos",
            "hablar con el ceo",
            "llegar al decisor",
            "decisor",
        ),
        "Cada nivel compra por razones distintas. Cambia el mensaje, no solo el saludo.",
        (
            (
                "C-level y gerencia general",
                (
                    "Les importa: crecimiento, rentabilidad, riesgo y la estrategia del año.",
                    "Mensajes muy cortos, en lenguaje de negocio, con impacto medible. Nada técnico.",
                    "Su tiempo vale mucho: pide 15 a 20 minutos y llega con una hipótesis clara.",
                ),
            ),
            (
                "Directores y gerentes de área",
                (
                    "Les importa: cumplir las metas de su área y quedar bien con su jefe.",
                    "Muestra cómo los ayudas a llegar a su meta y cómo se verá el resultado ante la dirección.",
                ),
            ),
            (
                "Jefes y equipos operativos",
                (
                    "Les importa: su día a día, menos trabajo manual y menos errores.",
                    "Suelen ser tus mejores campeones: si les facilitas la vida, te llevan al decisor.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "latam",
        "Vender en Latinoamérica",
        (
            "latam",
            "latinoamerica",
            "colombia",
            "mexico",
            "peru",
            "chile",
            "argentina",
            "ecuador",
            "cultura",
            "tu o usted",
            "usted",
            "tutear",
            "formalidad",
        ),
        "En la región la relación pesa tanto como el producto. La confianza se construye antes de la propuesta.",
        (
            (
                "Relación y confianza",
                (
                    "Las referencias y los conocidos en común abren puertas más rápido que cualquier correo frío.",
                    "Las primeras reuniones suelen tener un momento de conversación personal: no lo saltes.",
                    "Cumple lo pequeño (enviar lo prometido a tiempo): así te ganas lo grande.",
                ),
            ),
            (
                "Trato y formalidad",
                (
                    "Con directivos, sectores tradicionales (banca, gobierno, industria) y primeros contactos, empieza con usted; pasa al tú cuando la otra persona lo haga.",
                    "En México y Colombia el usted es común en contextos corporativos; en Argentina y Chile el trato suele volverse cercano antes.",
                    "Ante la duda, sé más formal: nadie se ofende por un usted.",
                ),
            ),
            (
                "Procesos de compra",
                (
                    "Las decisiones suelen subir por la jerarquía: identifica temprano quién aprueba al final.",
                    "Compras, registro de proveedor y pagos a 30, 60 o 90 días son normales en cuentas grandes: pregúntalo antes de prometer fechas.",
                    "Ten en cuenta temporadas lentas: fin de año, Semana Santa y vacaciones de mitad de año según el país.",
                ),
            ),
        ),
        "En ORKESTA puedes fijar el trato (tú o usted) y Concertino ajusta todos los textos.",
    ),
    Lesson(
        "referidos",
        "Referidos y clientes actuales",
        (
            "referido",
            "referidos",
            "recomendacion",
            "pedir referidos",
            "upsell",
            "venta cruzada",
            "cross sell",
            "expansion",
            "clientes actuales",
            "renovacion",
            "retencion",
        ),
        "Tus clientes actuales son tu mejor canal: compran otra vez y te presentan a otros.",
        (
            (
                "Pedir referidos",
                (
                    "El mejor momento es justo después de un resultado visible, no al firmar.",
                    "Pide algo concreto: «¿Conoces a alguien en [sector o cargo] que esté viviendo algo parecido?».",
                    "Facilítale la presentación: escríbele tú el mensaje que puede reenviar.",
                ),
            ),
            (
                "Crecer en la cuenta",
                (
                    "Revisa resultados con el cliente cada trimestre: qué se logró y qué sigue.",
                    "Busca otras áreas o sedes con el mismo problema y pide que te presenten.",
                    "Detecta riesgos antes de la renovación: poco uso, cambio de contacto o quejas sin resolver.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "metricas",
        "Métricas de ventas que importan",
        (
            "metricas",
            "kpi",
            "indicadores",
            "tasa de respuesta",
            "tasa de conversion",
            "cuantas reuniones",
            "productividad",
            "resultados del equipo",
            "medir",
        ),
        "Mide pocas cosas, pero mídelas siempre. La actividad explica el resultado; la conversión explica la calidad.",
        (
            (
                "Embudo de prospección",
                (
                    "Contactos nuevos por semana → tasa de respuesta → respuestas positivas → reuniones agendadas → reuniones realizadas.",
                    "Si cae una etapa, la causa está justo antes: lista, mensaje, seguimiento o forma de agendar.",
                ),
            ),
            (
                "Embudo de cierre",
                (
                    "Oportunidades creadas → propuestas enviadas → negocios ganados, con valor promedio y días de ciclo.",
                    "Mide también por qué se pierden: precio, tiempo, competencia o no decisión.",
                ),
            ),
            (
                "Orientación general",
                (
                    "Las tasas varían mucho por sector, país y calidad de la lista: compárate contigo mismo mes a mes antes que con promedios de internet.",
                ),
            ),
        ),
        "En Reportes ves tu embudo y el de tu equipo.",
    ),
    Lesson(
        "gestion_equipo",
        "Liderar un equipo comercial",
        (
            "liderar",
            "equipo de ventas",
            "coaching",
            "lider comercial",
            "jefe de ventas",
            "motivar al equipo",
            "one on one",
            "1 a 1",
            "gestionar vendedores",
            "onboarding de vendedores",
        ),
        "El líder comercial multiplica: su trabajo es que cada vendedor venda más, no vender por ellos.",
        (
            (
                "Ritmos",
                (
                    "1 a 1 semanal de 30 minutos con cada vendedor: pipeline, bloqueos y una habilidad a mejorar.",
                    "Revisión semanal del pipeline del equipo con las mismas preguntas siempre.",
                    "Escucha llamadas o lee correos reales y da retroalimentación concreta, no general.",
                ),
            ),
            (
                "Coaching",
                (
                    "Una habilidad a la vez: apertura de llamada, descubrimiento, manejo de objeciones o cierre.",
                    "Pregunta antes de decir: «¿Qué harías distinto?» ayuda más que dar la respuesta.",
                    "Reconoce en público y corrige en privado.",
                ),
            ),
            (
                "Nuevos vendedores",
                (
                    "Primeras 2 semanas: producto, cliente ideal, objeciones y herramientas.",
                    "Escuchar llamadas de los mejores antes de hacer las propias.",
                    "Metas de actividad al inicio y de resultado cuando ya tienen pipeline.",
                ),
            ),
        ),
        "",
    ),
    Lesson(
        "rechazo",
        "Manejar el rechazo y la constancia",
        (
            "rechazo",
            "me dicen que no",
            "frustracion",
            "desmotivado",
            "motivacion",
            "no logro vender",
            "no consigo reuniones",
            "mala racha",
        ),
        "En prospección el «no» es parte del proceso: cada no te acerca a un sí y te enseña algo de tu lista o tu mensaje.",
        (
            (
                "Cómo verlo",
                (
                    "Separa la persona del resultado: rechazan una propuesta en un momento, no a ti.",
                    "Lleva la cuenta de actividades, no solo de resultados: la actividad sí la controlas.",
                    "Celebra los pequeños avances: una buena conversación también es un logro.",
                ),
            ),
            (
                "Qué ajustar en una mala racha",
                (
                    "Revisa la lista: ¿le estás escribiendo a quien tiene el problema?",
                    "Revisa el mensaje con alguien de confianza o pídeme que lo mejore.",
                    "Cambia de canal: si el correo no funciona, prueba llamadas o LinkedIn con ese mismo segmento.",
                ),
            ),
        ),
        "",
    ),
)

LESSON_BY_CODE = {lesson.code: lesson for lesson in LESSONS}

_HOW = re.compile(
    r"^(como|que|cual|cuales|cuando|cuanto|cuantos|cuantas|por que|para que|dame|dime|explica|"
    r"explicame|ensename|tips|consejos|mejores practicas|recomendaciones|ayudame a|quiero aprender|"
    r"necesito aprender|que es|que son|es mejor|debo|deberia)\b"
)
_STOP = frozenset(
    [
        "como",
        "que",
        "cual",
        "cuales",
        "cuando",
        "cuanto",
        "para",
        "por",
        "una",
        "uno",
        "unos",
        "unas",
        "los",
        "las",
        "del",
        "con",
        "sin",
        "mas",
        "muy",
        "hay",
        "hace",
        "hacer",
        "tengo",
        "tiene",
        "puedo",
        "debo",
        "seria",
        "ser",
        "esta",
        "este",
        "esto",
        "eso",
        "esa",
        "son",
        "mis",
        "tus",
        "sus",
    ]
)


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]+", fold(text)) if len(w) > 2 and w not in _STOP}


def is_method_question(text: str) -> bool:
    """«¿Cómo escribo un correo en frío?» pide método; «me respondió que no tiene presupuesto»
    es un caso concreto que resuelve el motor de consejos."""
    return bool(_HOW.match(fold(text).strip(" ¿?¡!")))


def score(question: str, lesson: Lesson) -> float:
    folded = f" {fold(question)} "
    phrase = sum(4 + len(t.split()) for t in lesson.triggers if f" {t} " in folded or
                 (len(t) > 5 and t in folded))  # fmt: skip
    words = _words(question)
    title = _words(lesson.title)
    return phrase + 2 * len(words & title)


def match(question: str, minimum: float = 5) -> Lesson | None:
    """La lección que mejor responde la pregunta, o ``None`` si ninguna encaja claramente."""
    if not question.strip():
        return None
    ranked = sorted(((score(question, lesson), lesson) for lesson in LESSONS),
                    key=lambda x: x[0], reverse=True)  # fmt: skip
    best, lesson = ranked[0]
    return lesson if best >= minimum else None


def advice(lesson: Lesson) -> dict[str, object]:
    return {
        "summary": lesson.summary,
        "options": [],
        "tips": [],
        "sections": [{"title": title, "items": list(items)} for title, items in lesson.sections],
        "next_step": lesson.next_step,
        "missing": [],
        "lesson": {"code": lesson.code, "title": lesson.title},
    }
