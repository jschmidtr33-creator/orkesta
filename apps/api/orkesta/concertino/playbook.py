# ruff: noqa: E501  (textos de venta: líneas largas a propósito)
"""El oficio del vendedor senior: temas, mapa de objeciones, enfoques de respuesta y lenguaje
por nivel del decisor. Todo propio, en español, y sin datos de ninguna empresa: los datos del
producto llegan de la ficha al redactar.

Convenciones de los textos:
- ``[a|b]``: «a» con trato de tú, «b» con trato de usted.
- ``{slot}``: dato de la ficha o de la cuenta. Si falta, la frase que lo usa se omite y
  Concertino avisa qué agregar a la ficha.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOPICS: dict[str, str] = {
    "objecion": "Objeción",
    "respuesta": "Respuesta recibida",
    "perfil": "Perfil del decisor",
    "cuenta": "Cuenta u oportunidad",
    "campana": "Campaña",
    "propuesta": "Propuesta",
    "reunion": "Reunión",
    "estrategia": "Estrategia",
    "producto": "Producto",
    "general": "Consulta general",
    "redactar": "Redacción",
}

# Palabras que indican el tema (sobre el texto sin tildes y en minúsculas).
TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "respuesta": (r"\bme (respondio|contesto|escribio|dijo)\b", r"\b(respondio|contesto)\b",
                  r"\bque le (respondo|contesto|digo)\b", r"\bsu respuesta\b"),
    "perfil": (r"\bcomo le (hablo|escribo|vendo|llego)\b", r"\bperfil\b", r"\bbuyer persona\b",
               r"\b(ceo|cfo|cto|gerente|director|directora|vp|vicepresidente|jefe|jefa)\b",
               r"\bque le importa\b", r"\bdecisor\b"),
    "cuenta": (r"\boportunidad\b", r"\bse enfrio\b", r"\bnegociacion\b", r"\bcuenta\b",
               r"\b(etapa|pipeline|embudo)\b", r"\bcerrar (el|este) (negocio|trato)\b"),
    "campana": (r"\bcampana", r"\bsecuencia\b", r"\btasa de (respuesta|apertura)\b",
                r"\bnadie (responde|contesta|abre)\b", r"\basunto\b", r"\babren\b", r"\bseguimientos?\b"),
    "propuesta": (r"\bpropuesta\b", r"\bcotizacion\b", r"\ben la propuesta\b", r"\bpresent(ar|o|a) (el )?precio\b",
                  r"\b(inversion|tarifa)\b"),
    "reunion": (r"\breunion\b", r"\bllamada\b", r"\bdemo\b", r"\bcita\b", r"\bpresentacion\b",
                r"\bdescubrimiento\b"),
    "estrategia": (r"\bsector(es)?\b", r"\bsegmento\b", r"\ba quien(es)? le apunto\b",
                   r"\bestrategia\b", r"\bnicho\b", r"\bindustria\b", r"\bmercado\b",
                   r"\bmejor (me|nos) (funciona|responde)\b", r"\bcliente(s)? ideal(es)?\b", r"\bicp\b",
                   r"\bbuyer persona\b", r"\bperfil de cliente\b", r"\ba quien(es)? (le )?(deberia|debo|puedo) vender\b"),
    "producto": (r"\bnuestro producto\b", r"\bque vendemos\b", r"\bcomo (explico|presento)\b",
                 r"\bpitch\b", r"\bpropuesta de valor\b", r"\bdiferencia(l|mos)?\b",
                 r"\bque (hace|ofrece) (el|nuestro) producto\b"),
}  # fmt: skip


@dataclass(frozen=True)
class Approach:
    code: str
    name: str
    why: str
    lines: tuple[str, ...]
    levels: tuple[str, ...] = ()  # grupos de nivel con los que mejor funciona


@dataclass(frozen=True)
class Objection:
    code: str
    label: str
    real: str  # lo que suele haber detrás
    confirm: str  # pregunta para confirmar que quedó resuelta
    patterns: tuple[str, ...]
    approaches: tuple[Approach, ...] = field(default_factory=tuple)


OBJECTIONS: tuple[Objection, ...] = (
    Objection(
        "precio",
        "Precio o presupuesto",
        "Casi nunca es solo el número: está comparando contra otra opción, contra un presupuesto fijo o no ve todavía el valor.",
        "[¿Te|¿Le] queda más claro cómo se compara la inversión con lo que [ganas|gana]?",
        (
            r"\bcaro\b",
            r"\bcostoso\b",
            r"\bprecio\b",
            r"\bpresupuesto\b",
            r"\bno (nos |le |les |me )?alcanza\b",
            r"\bno (tenemos|tienen|hay) (plata|dinero|recursos|presupuesto)\b",
            r"\bmuy alto\b",
            r"\bfuera de(l)? (nuestro )?presupuesto\b",
            r"\bexpensive\b",
            r"\bbudget\b",
        ),
        (
            Approach(
                "valor",
                "Llevarlo al valor",
                "Antes de hablar de descuentos, compara la inversión contra lo que cuesta seguir con el problema.",
                (
                    "Gracias por [decírmelo|comentármelo] con franqueza.",
                    "Más que el precio, lo que suele pesar es cuánto cuesta seguir con {problema}.",
                    "Lo que más valoran nuestros clientes: {valor}.",
                    "Por ejemplo: {prueba}.",
                    "[¿Te|¿Le] parece si revisamos en 20 minutos cuánto representa ese costo hoy en [tu|su] operación? Con eso [decides|decide] si tiene sentido.",
                ),
                ("alta", "direccion"),
            ),
            Approach(
                "pregunta",
                "Preguntar contra qué lo compara",
                "Descubre la objeción real (otra opción, un presupuesto fijo o poco valor percibido) antes de responderla.",
                (
                    "Gracias por [decírmelo|comentármelo].",
                    "Para entender bien: ¿lo [ves|ve] caro frente a otra alternativa, frente al presupuesto que [tienes|tiene] asignado o frente al resultado que [esperas|espera]?",
                    "Según eso [te|le] propongo el camino que más sentido tenga.",
                ),
            ),
            Approach(
                "alcance",
                "Ajustar el alcance, no el precio",
                "Si el valor está claro pero el presupuesto no alcanza, ofrece opciones en vez de descuento: protege el precio y la relación.",
                (
                    "Entiendo, el presupuesto manda.",
                    "Como referencia: {precio}.",
                    "Podemos empezar con un alcance más acotado, enfocado en lo que más impacto tiene, y crecer cuando [veas|vea] resultados.",
                    "[¿Te|¿Le] comparto dos opciones de inversión para comparar?",
                ),
                ("gestion", "operativo"),
            ),
        ),
    ),
    Objection(
        "descuento",
        "Pide descuento para cerrar",
        "Ya decidió que te quiere comprar y está negociando: su área de compras o su jefe le pide «sacar algo». Un descuento sin nada a cambio le enseña que tu precio no era real.",
        "¿Con estas condiciones lo dejamos firmado este mes?",
        (
            r"\bdescuentos?\b",
            r"\brebaja\b",
            r"\b\d{1,2} ?(%|por ?ciento)\b",
            r"\bprecio especial\b",
            r"\bmejor(ar|e|es)? (el |su |tu )?precio\b",
            r"\bbaj(ar|o|e|amos|en)( el| su| tu)? precio\b",
            r"\bpara (firmar|cerrar)\b",
            r"\bfirma(ria|rian|mos|n)? (hoy|ya|este mes|esta semana)\b",
            r"\bdiscount\b",
        ),
        (
            Approach(
                "intercambio",
                "Dar algo solo a cambio de algo",
                "Nunca concedas gratis: cada punto de descuento se cambia por algo que te sirva (plazo más largo, pago por adelantado, más usuarios o un caso de éxito). Así el precio sigue siendo creíble.",
                (
                    "Gracias por la confianza: me alegra que quieran avanzar.",
                    "Puedo mejorar las condiciones si lo armamos juntos: con un contrato anual pagado por adelantado, o si nos [autorizas|autoriza] a contar el caso cuando [veas|vea] resultados, revisamos el precio.",
                    "¿Cuál de las dos opciones [te|le] funciona mejor para firmar este mes?",
                ),
                ("alta", "direccion"),
            ),
            Approach(
                "entender",
                "Entender de dónde sale la cifra",
                "Antes de mover el precio, averigua si el número viene de un presupuesto aprobado, de otra cotización o de una meta de compras: cada caso se responde distinto.",
                (
                    "Gracias por decírmelo directo.",
                    "Para buscar la mejor salida: ¿ese porcentaje viene de un presupuesto ya aprobado, de otra propuesta que [estás|está] comparando o es una meta de compras?",
                    "Con eso [te|le] propongo algo que funcione para los dos.",
                ),
            ),
            Approach(
                "alcance",
                "Mantener el precio y ajustar el alcance",
                "Si el presupuesto es fijo, baja lo que incluye y no el precio por unidad. Si cedes algo, que sea poco y una sola vez (5 a 10 %), nunca la cifra completa que piden.",
                (
                    "Entiendo que el presupuesto de este mes es el que hay.",
                    "Antes que bajar el precio, prefiero que empecemos con lo que más impacto tiene: {valor}.",
                    "Así [te|le] queda dentro del presupuesto y [sumas|suma] el resto cuando [veas|vea] los resultados.",
                    "[¿Te|¿Le] envío las dos opciones hoy para que lo [cierres|cierre] este mes?",
                ),
                ("gestion", "operativo"),
            ),
        ),
    ),
    Objection(
        "prioridad",
        "No es prioridad ahora",
        "Tiene otras urgencias o no ve el costo de esperar. Muchas veces es un «no» amable que conviene convertir en una fecha.",
        "¿Dejamos agendada esa fecha?",
        (
            r"\bno es (una )?prioridad\b",
            r"\bno es (el|buen) momento\b",
            r"\bmas adelante\b",
            r"\bel (proximo|otro) (ano|trimestre|mes)\b",
            r"\b(el )?(ano|mes|trimestre) que viene\b",
            r"\b(escriba|escribas|contacte|contactes|llame|llames|busque|busques)( de nuevo)?( en| dentro de)? (\d+|un|una|dos|tres|un par de) (meses|semanas|mes)\b",
            r"\b(esta|estoy|estara|estare|estan) de vacaciones\b",
            r"\b(llame|llames|escriba|escribas|busque|busques|contacte)( de nuevo)? (la )?(otra|proxima) semana\b",
            r"\b(la )?semana que viene\b",
            r"\bhasta (enero|febrero|marzo|abril|el otro ano|el proximo ano)\b",
            r"\bfuera de (la )?oficina\b",
            r"\b(el )?proximo (ano|trimestre|semestre)\b",
            r"\b(en|para) (enero|febrero|marzo|el q1|q1|el primer trimestre|el 2027|2027)\b",
            r"\bdespues de (vacaciones|fin de ano|las fiestas|navidad)\b",
            r"\b(retomemos|hablemos|lo vemos) (mas adelante|el otro ano|en enero|despues)\b",
            r"\bahora no\b",
            r"\bpor ahora no\b",
            r"\b(estamos|estan) (ocupados|enfocados|full)\b",
            r"\bno (tenemos|tienen) tiempo\b",
            r"\bpostergar\b",
            r"\bnot (a priority|now)\b",
            r"\bnext (year|quarter)\b",
        ),
        (
            Approach(
                "costo_esperar",
                "Mostrar el costo de esperar",
                "Respeta su prioridad y deja a la vista lo que se pierde al postergar, sin presionar.",
                (
                    "Entiendo, [tienes|tiene] muchos frentes abiertos.",
                    "Solo una pregunta: ¿{problema} está en los planes de este año o ya lo [resolviste|resolvió] de otra forma?",
                    "Por ejemplo: {prueba}.",
                    "Si no es ahora, [dime|dígame] en qué mes [te|le] escribo y lo dejo agendado.",
                ),
                ("alta", "direccion"),
            ),
            Approach(
                "fecha",
                "Acordar una fecha concreta",
                "Convierte un «no ahora» en un compromiso concreto y demuestra que respetas su tiempo.",
                (
                    "Perfecto, gracias por [avisarme|avisarme].",
                    "Para no insistir de más: ¿en qué mes [te|le] parece bien que retomemos?",
                    "Lo agendo y no [te|le] escribo antes.",
                ),
            ),
            Approach(
                "aporte",
                "Dejar algo de valor",
                "Mantiene viva la relación aportando algo útil en lugar de pedir.",
                (
                    "Entendido, gracias.",
                    "Mientras tanto, [te|le] dejo un dato que puede servir para la planeación: {prueba}.",
                    "Retomo cuando [me digas|me diga].",
                ),
                ("gestion", "operativo"),
            ),
        ),
    ),
    Objection(
        "proveedor",
        "Ya tienen proveedor",
        "No quiere el costo de cambiar lo que funciona. La puerta está en lo que su proveedor actual no cubre bien.",
        "¿Hay algún punto en el que valga la pena comparar?",
        (
            r"\bya (tenemos|tienen|contamos con|cuentan con|trabajamos con|trabajan con|usamos|usan)\b",
            r"\bnuestro proveedor\b",
            r"\bproveedor actual\b",
            r"\b(estamos|estan) contentos con\b",
            r"\bya lo (tenemos|resolvimos|resolvieron|tienen|resuelven|resolvemos)\b",
            r"\b(ya )?(compro|compraron|compramos|adquirieron|contrataron|contratamos|implementaron) (algo|otra|otro|una|un)\b",
            r"\balready (have|work with|use)\b",
        ),
        (
            Approach(
                "complemento",
                "Complementar, no reemplazar",
                "Nadie quiere cambiar lo que funciona: busca el espacio que el proveedor actual no cubre.",
                (
                    "Qué bueno que ya [tienes|tiene] eso cubierto.",
                    "Lo que nos diferencia: {diferencial}.",
                    "Varias empresas nos usan junto a su proveedor actual justo para eso.",
                    "¿Hay algo que hoy [te|le] gustaría que funcionara mejor?",
                ),
            ),
            Approach(
                "comparacion",
                "Ofrecer una comparación sin compromiso",
                "Da una razón concreta para mirar, sin pedir que cambie nada.",
                (
                    "Entiendo, gracias por contarme.",
                    "Frente a {competidor}: {competidor_detalle}.",
                    "Si [quieres|quiere], en 20 minutos [te|le] muestro la diferencia con [tu|su] caso real y [tú decides|usted decide] si vale la pena.",
                ),
                ("direccion", "gestion"),
            ),
            Approach(
                "renovacion",
                "Ubicar la renovación",
                "Encuentra la ventana natural de cambio y llega a tiempo.",
                (
                    "Gracias por contarme.",
                    "¿Cuándo [renuevas|renueva] con [tu|su] proveedor actual?",
                    "Me gustaría [escribirte|escribirle] un mes antes para que [tengas|tenga] un punto de comparación.",
                ),
            ),
        ),
    ),
    Objection(
        "persona",
        "No es la persona indicada",
        "Llegaste a la persona equivocada, o a alguien que influye pero no decide. Es una oportunidad de entrar recomendado.",
        "¿A quién le escribo de [tu|su] parte?",
        (
            r"\bno (soy|es) (la persona|el indicado|la indicada|quien)\b",
            r"\bno es (mi|su) area\b",
            r"\bhabla(r)? con\b",
            r"\bescribele a\b",
            r"\bno manejo (eso|ese tema)\b",
            r"\bel encargado es\b",
            r"\b(a|con) otra persona\b",
            r"\bya no (trabaja|trabajan|esta|estan) (ahi|alli|en la empresa|con ellos)\b",
            r"\b(dejo|salio de|se fue de) la empresa\b",
            r"\b(otra|otro) (area|departamento|equipo|persona)\b",
            r"\b(reenvie|reenviar|envie|mande|pase|derive)(lo|la)? a\b",
            r"\bnot the right person\b",
        ),
        (
            Approach(
                "referido",
                "Pedir el referido",
                "Llegar recomendado por alguien de adentro cambia cómo te reciben.",
                (
                    "Gracias por [responderme|responderme] y por la orientación.",
                    "¿Quién sería la persona indicada para hablar de {problema}?",
                    "Si me [compartes|comparte] su nombre, le escribo mencionando que [tú me orientaste|usted me orientó].",
                ),
            ),
            Approach(
                "presentacion",
                "Pedir que te presente",
                "Una presentación directa es más fuerte que un nombre: le das el texto para que no le cueste.",
                (
                    "Muchas gracias.",
                    "Para no llegar en frío: ¿[te|le] molestaría reenviarle este correo con una línea? Así sabe que viene de [ti|usted].",
                ),
            ),
        ),
    ),
    Objection(
        "info",
        "Pide información",
        "A veces es interés real y a veces una salida amable. Lo que se envía tiene que ser corto y pedir una sola respuesta.",
        "¿Lo revisamos juntos 15 minutos?",
        (
            r"\benviame (informacion|info|una presentacion|el brochure|detalles)\b",
            r"\bmandame (informacion|info|algo)\b",
            r"\b(envieme|enviarme|enviame|mandeme) (mas )?(informacion|info|presentacion)\b",
            r"\bme (envias|mandas|compartes) (informacion|info|una presentacion)\b",
            r"\b(mas|mayor) informacion\b",
            r"\bsend (me )?(more )?info",
            r"\bbrochure\b",
        ),
        (
            Approach(
                "enfocar",
                "Enviar algo corto y enfocado",
                "Una pregunta antes de enviar evita el folleto genérico y mantiene la conversación.",
                (
                    "Con gusto.",
                    "Para no [enviarte|enviarle] algo genérico: ¿lo que más [te|le] interesa revisar es {problema} u otro tema?",
                    "Con eso [te|le] envío una sola página.",
                ),
            ),
            Approach(
                "llamada",
                "Proponer 15 minutos",
                "Ver el producto con su caso es más rápido que leer una presentación.",
                (
                    "Claro, [te|le] la envío hoy.",
                    "Lo que más valoran nuestros clientes: {valor}.",
                    "Muchas veces es más rápido verlo en 15 minutos con [tu|su] caso: ¿[te|le] sirve esta semana?",
                ),
                ("alta", "direccion"),
            ),
        ),
    ),
    Objection(
        "riesgo",
        "Miedo al cambio o a la implementación",
        "Teme el esfuerzo de su equipo, una integración difícil o que la herramienta no se use.",
        "¿Con un plan así [te|le] sentirías más tranquilo?",
        (
            r"\bcomplicad[oa]\b",
            r"\bimplementa(r|cion)\b",
            r"\bintegra(r|cion)\b",
            r"\bmigra(r|cion)\b",
            r"\bcurva de aprendizaje\b",
            r"\bno (lo|la) van a usar\b",
            r"\b(miedo|temor|teme|temen)\b",
            r"\b(muy|demasiado) (pequenos|chicos|nuevos|jovenes)\b",
            r"\bno (los|nos) conoce(n)?\b",
            r"\b(empresa|marca) (nueva|desconocida)\b",
            r"\bno (lo|la) (use|usen|adopten|aprovechen)\b",
            r"\bmucho trabajo\b",
            r"\briesgo\b",
            r"\bcambiar (todo|de sistema)\b",
            r"\bcomplex\b",
        ),
        (
            Approach(
                "plan",
                "Mostrar un plan de bajo riesgo",
                "Baja el miedo con un plan acotado, un responsable y metas claras para decidir si seguir.",
                (
                    "Es una preocupación muy válida.",
                    "Por ejemplo: {prueba}.",
                    "Podemos empezar con una prueba acotada, con un responsable de nuestro lado y metas claras para decidir si seguir.",
                    "¿[Te|Le] comparto cómo se vería ese plan?",
                ),
            ),
            Approach(
                "pregunta",
                "Entender el miedo",
                "Nombrar la preocupación exacta permite responderla en vez de adivinar.",
                (
                    "Entiendo.",
                    "¿Qué es lo que más [te|le] preocupa: el tiempo de [tu|su] equipo, la integración o que no se use?",
                    "Así [te|le] cuento exactamente cómo lo resolvemos.",
                ),
                ("gestion", "operativo"),
            ),
        ),
    ),
    Objection(
        "confianza",
        "Falta de confianza o referencias",
        "No los conoce y no quiere arriesgar su reputación con un proveedor nuevo.",
        "¿Con esto [tienes|tiene] lo que necesitas para avanzar?",
        (
            r"\bno (los|las|te|lo|nos) (conozco|conoce|conocen)\b",
            r"\breferencias\b",
            r"\bcasos de exito\b",
            r"\bquien(es)? (son|mas lo usa)\b",
            r"\bque clientes\b",
            r"\bno (los|las) conocemos\b",
            r"\breferences\b",
        ),
        (
            Approach(
                "prueba",
                "Dar pruebas concretas",
                "La confianza se construye con resultados de empresas parecidas, no con adjetivos.",
                (
                    "Tiene todo el sentido.",
                    "Por ejemplo: {prueba}.",
                    "Si [te|le] sirve, [te|le] comparto el caso completo con los números.",
                ),
            ),
            Approach(
                "prueba_pequena",
                "Proponer un primer paso pequeño",
                "Reduce lo que arriesga: un primer paso corto y medible genera la confianza que falta.",
                (
                    "Entiendo, es lógico querer ir con cuidado.",
                    "¿Qué [te|le] parece empezar con algo pequeño y medible, y decidir con resultados en la mano?",
                ),
            ),
        ),
    ),
    Objection(
        "competencia",
        "Está comparando con la competencia",
        "Ya tiene otras opciones sobre la mesa. Gana quien ayuda a decidir con criterios claros, no quien critica al otro.",
        "¿Con esos criterios [te|le] queda más fácil comparar?",
        (
            r"\bmas barato\b",
            r"\botra (opcion|propuesta|empresa)\b",
            r"\bcotizando\b",
            r"\bcomparando\b",
            r"\bla competencia\b",
            r"\bnos ofrecen\b",
            r"\botro proveedor\b",
            r"\bcompetitor\b",
        ),
        (
            Approach(
                "diferencial",
                "Mostrar la diferencia",
                "Destaca lo que solo tú haces, sin hablar mal de nadie.",
                (
                    "Qué bueno que [estés|esté] comparando: es la forma correcta de decidir.",
                    "Lo que nos diferencia: {diferencial}.",
                    "Frente a {competidor}: {competidor_detalle}.",
                    "¿Qué criterios [vas|va] a usar para decidir? Así [te|le] ayudo a comparar con justicia.",
                ),
            ),
            Approach(
                "criterios",
                "Ayudar a armar los criterios",
                "Quien define los criterios de compra suele ganar la decisión.",
                (
                    "Gracias por contarme.",
                    "Si [te|le] sirve, [te|le] comparto una lista de criterios para comparar opciones como estas (resultado, tiempo de implementación, soporte y costo total), y la [llenas|llena] con todas las propuestas.",
                ),
                ("direccion", "gestion"),
            ),
        ),
    ),
    Objection(
        "comite",
        "Lo tiene que consultar",
        "No decide solo. Necesita argumentos para defender la idea ante su jefe, sus socios o un comité.",
        "¿Qué más necesitaría quien decide para dar el sí?",
        (
            r"\b(lo|la) (tengo|tenemos|tiene|tienen) que (consultar|revisar|hablar)\b",
            r"\bconsultarlo\b",
            r"\bmi (jefe|jefa|socio|socia|gerente)\b",
            r"\b(con|a) (su|mi) (jefe|jefa|socio|socia|gerente|director|directora|junta|area de compras)\b",
            r"\b(hablar|consultar|revisar|validar)(lo|la|ia|ian|a|an|e)? con\b",
            r"\b(lo|la) (tiene|tienen|tengo|tenemos) que aprobar\b",
            r"\bcomite\b",
            r"\bjunta directiva\b",
            r"\blo decide\b",
            r"\bdecidir con\b",
            r"\bto (check|discuss) with\b",
        ),
        (
            Approach(
                "kit",
                "Armar a quien te apoya por dentro",
                "Quien consulta necesita argumentos listos para defender la idea ante otros.",
                (
                    "Perfecto, tiene sentido decidirlo en equipo.",
                    "¿[Te|Le] ayudo con un resumen de una página con el problema, el resultado esperado y la inversión, para que lo [presentes|presente]?",
                    "Puede incluir este ejemplo: {prueba}.",
                ),
            ),
            Approach(
                "sumar",
                "Sumar a quien decide",
                "Hablar directo con quien decide evita el teléfono roto.",
                (
                    "Claro.",
                    "¿[Te|Le] parece si hacemos una llamada corta con quien decide para resolver sus dudas directamente? Así no [tienes|tiene] que repetir todo.",
                ),
                ("gestion", "operativo"),
            ),
            Approach(
                "retomar",
                "Retomar después de la consulta",
                "Si lo iba a hablar con su jefe y no volvió, casi siempre se trabó por dentro: ofrécele el material para vender la idea o una llamada con los dos.",
                (
                    "¿Cómo [te|le] fue al conversarlo con quien decide?",
                    "Si sirve, [te|le] envío un resumen de una página con el problema, el resultado y la inversión para que lo [compartas|comparta], o lo explicamos juntos en 15 minutos.",
                    "¿Qué [te|le] facilita más?",
                ),
            ),
        ),
    ),
    Objection(
        "pago",
        "Pide condiciones de pago",
        "Quiere comprar pero cuida su caja o sigue la política de proveedores. El plazo se negocia igual que el precio: a cambio de algo.",
        "¿Con esas condiciones lo dejamos listo para firmar?",
        (
            r"\bpagar a \d+ dias\b",
            r"\b\d+ dias (de|para) (pago|pagar)\b",
            r"\b(plazo|condiciones|forma|formas) de pago\b",
            r"\b(en|a) cuotas\b",
            r"\b(financiar|financiacion|financiamiento|credito)\b",
            r"\bpago (anticipado|diferido|a credito)\b",
            r"\bpolitica de (pago|proveedores)\b",
        ),
        (
            Approach(
                "intercambio",
                "Plazo a cambio de compromiso",
                "Más plazo se compensa con algo que te dé seguridad: un contrato más largo, un primer pago al firmar o una fecha de inicio cercana.",
                (
                    "Entiendo, y quiero que esto funcione con [tus|sus] tiempos de pago.",
                    "Puedo extender el plazo si lo acompañamos de un primer pago al firmar o de un contrato a 12 meses.",
                    "¿Cuál de las dos [te|le] funciona mejor?",
                ),
            ),
            Approach(
                "entender",
                "Entender la política",
                "Si es una regla de compras (pago a 60 o 90 días a todo proveedor), no es negociable: ajusta tu precio o tu flujo, no tu argumento.",
                (
                    "Gracias por contarme.",
                    "¿Ese plazo es la política general de pago a proveedores o es para este proyecto en particular?",
                    "Así [te|le] propongo la opción que encaje con [tu|su] proceso.",
                ),
            ),
            Approach(
                "hitos",
                "Pagos por etapas",
                "Dividir el pago por hitos (inicio, primer resultado, cierre) reduce el riesgo para el cliente sin que tú financies todo el proyecto.",
                (
                    "Una opción que suele funcionar: dividimos el pago en etapas.",
                    "Una parte al inicio, otra cuando [veas|vea] el primer resultado y el resto al cierre.",
                    "¿[Te|Le] armo la propuesta con ese esquema?",
                ),
            ),
        ),
    ),
    Objection(
        "pensar",
        "Lo va a pensar",
        "«Lo voy a pensar» casi nunca es falta de tiempo: esconde una duda concreta (precio, momento, riesgo o quién decide). Tu trabajo es descubrir cuál, sin presionar.",
        "¿Qué parte [quieres|quiere] revisar con más calma?",
        (
            r"\b(lo|la) (voy|vamos|va|van|iba|ibamos|iban) a pensar\b",
            r"\b(pensarlo|pensarla|madurarlo|meditarlo)\b",
            r"\b(lo|la) (pienso|pensamos|piensa|evaluo|evaluamos|analizo|analizamos) y (te|le|les) (aviso|avisamos|cuento|contamos|digo|decimos)\b",
            r"\b(necesito|necesitamos|necesita|necesitan) (mas )?(tiempo|pensar|unos dias)\b",
            r"\b(pide|pidio|piden|pidieron|me pide) (mas )?tiempo\b",
            r"\b(unos|un par de|algunos|pocos) dias (para|mas)\b",
            r"\b(me )?(da|dan|dio|dieron) largas\b",
            r"\b(lo|la) (voy|vamos|va|van) a (evaluar|analizar)\b",
            r"\bthink (about )?it\b",
        ),
        (
            Approach(
                "aislar",
                "Descubrir la duda real",
                "Una pregunta de escala (del 1 al 10) saca la duda escondida sin incomodar y te dice qué falta para cerrar.",
                (
                    "Claro, es una decisión importante y está bien pensarla.",
                    "Para ayudar[te|le]: del 1 al 10, ¿qué tan cerca está esto de lo que [necesitas|necesita]? ¿Y qué haría falta para que sea un 10?",
                ),
            ),
            Approach(
                "fecha",
                "Acordar cuándo se decide",
                "Sin fecha, «lo pienso» se vuelve silencio. Un siguiente paso concreto mantiene viva la conversación.",
                (
                    "Perfecto, [tómate|tómese] el tiempo que necesite[s|].",
                    "¿[Te|Le] parece si lo retomamos el jueves en una llamada de 15 minutos? Así resuelvo cualquier duda que salga mientras lo [piensas|piensa].",
                ),
            ),
            Approach(
                "esperar",
                "Mostrar el costo de esperar",
                "Recordar el resultado concreto hace visible lo que se pierde cada mes que pasa sin decidir.",
                (
                    "Entiendo.",
                    "Un dato para pensarlo: {valor}.",
                    "Cada mes sin resolverlo es un mes más de {problema}.",
                    "Si [te|le] sirve, [te|le] comparto cómo lo resolvimos con otro cliente: {prueba}.",
                ),
                ("alta", "direccion"),
            ),
        ),
    ),
    Objection(
        "silencio",
        "No responde",
        "El silencio rara vez es un «no»: suele ser falta de tiempo o de una razón clara para contestar.",
        "",
        (
            r"\bno (me )?(responde|contesta)\b",
            r"\bme dejo en visto\b",
            r"\bdesaparecio\b",
            r"\b(me )?(ghostea|ghosteo|ghostearon|ghostean|ghosting)\b",
            r"\bse esfumo\b",
            r"\bno ha (respondido|contestado)\b",
            r"\bghost",
            r"\bsin respuesta\b",
            r"\bno volvio a\b",
        ),
        (
            Approach(
                "cierre_amable",
                "Correo de cierre amable",
                "Darle permiso de decir que no suele provocar una respuesta honesta.",
                (
                    "No quiero llenar [tu|su] bandeja.",
                    "Si esto no es prioridad ahora, lo entiendo perfectamente y no [te|le] escribo más.",
                    "Si sí lo es, con un «sí» me basta para proponer un horario.",
                ),
            ),
            Approach(
                "nuevo_angulo",
                "Cambiar el ángulo",
                "Repetir el mismo mensaje no funciona: entra con un dato o un caso nuevo.",
                (
                    "[Te|Le] escribo con otro ángulo.",
                    "Por ejemplo: {prueba}.",
                    "¿Vale la pena conversarlo 15 minutos?",
                ),
                ("alta", "direccion"),
            ),
        ),
    ),
    Objection(
        "no_interes",
        "No le interesa",
        "Puede ser un «no» real o falta de contexto. Aceptarlo con elegancia deja la puerta abierta.",
        "",
        (
            r"\bno (me|nos|le|les) interesa\b",
            r"\bno gracias\b",
            r"\bno aplica\b",
            r"\bno lo (necesitamos|necesitan)\b",
            r"\bnot interested\b",
        ),
        (
            Approach(
                "aprender",
                "Aceptar y aprender",
                "Una sola pregunta honesta te dice si es «nunca» o «no ahora», y mejora tu próxima campaña.",
                (
                    "Gracias por responder, de verdad.",
                    "Para no volver a [escribirte|escribirle] sin sentido: ¿es porque no es un tema para [ustedes|ustedes] o por el momento?",
                    "Con eso ajusto y no insisto.",
                ),
            ),
            Approach(
                "puerta",
                "Dejar la puerta abierta",
                "Respetar el «no» cuida la marca y deja espacio para volver.",
                (
                    "Entendido, gracias por [decírmelo|decírmelo].",
                    "Si en algún momento {problema} se vuelve prioridad, aquí estoy.",
                ),
            ),
        ),
    ),
    Objection(
        "interno",
        "Lo hacemos internamente",
        "Su equipo ya lo resuelve a su manera. La puerta está en el tiempo y el costo que hoy le dedica su gente y en lo que no alcanza a cubrir.",
        "[¿Te|¿Le] queda claro qué ganaría [tu|su] equipo si deja de hacerlo a mano?",
        (
            r"\blo hacemos (internamente|nosotros|en casa)\b",
            r"\btenemos (un )?equipo (propio|interno)\b",
            r"\b(lo|eso) (manejamos|resolvemos) internamente\b",
            r"\b(resolvieron|resolvemos|resuelven|hacen|manejan) (internamente|en casa|nosotros mismos|ellos mismos)\b",
            r"\binternamente\b",
            r"\b(sobrino|primo|hijo|hija|amigo|conocido|freelance|practicante)\b.*\b(gratis|lo hace|se lo hace|le hace)\b",
            r"\b(desarrollo|solucion|area) (interno|interna|propio|propia)\b",
            r"\bin.?house\b",
        ),
        (
            Approach(
                "tiempo",
                "Poner en números el tiempo de su equipo",
                "Lo interno parece gratis, pero cuesta horas de personas caras. Hazlo visible sin criticar a su equipo.",
                (
                    "Tiene todo el sentido: nadie conoce [tu|su] operación mejor que [tu|su] equipo.",
                    "Justo por eso: ¿cuántas horas a la semana les toma hoy?",
                    "Nuestros clientes con equipo propio lo usan para liberarlo de lo repetitivo: {valor}.",
                    "[¿Te|¿Le] parece si lo comparamos en 20 minutos con [tu|su] forma actual?",
                ),
            ),
            Approach(
                "complemento",
                "Sumar, no reemplazar",
                "No compitas con su equipo: posiciónate como la herramienta que lo hace rendir más.",
                (
                    "Entiendo, y no se trata de reemplazar a [tu|su] equipo.",
                    "Lo que hacemos es que rinda más: {diferencial}.",
                    "¿Qué parte del proceso les quita más tiempo hoy?",
                ),
            ),
        ),
    ),
    Objection(
        "contrato",
        "Tienen un contrato vigente",
        "No puede cambiar ahora, pero sí puede planear. Gana quien llega antes de la renovación con una propuesta lista.",
        "[¿Te|¿Le] parece bien que lo retomemos antes de la renovación?",
        (
            r"\b(tenemos|hay) (un )?contrato\b",
            r"\bcontrato (vigente|firmado|anual)\b",
            r"\bamarrados\b",
            r"\bhasta (que )?(termine|venza|se venza) (el )?contrato\b",
        ),
        (
            Approach(
                "fecha",
                "Convertirlo en una fecha",
                "Pregunta cuándo vence y agenda desde ya: así entras en la evaluación y no después.",
                (
                    "Entiendo, hay que respetar el contrato.",
                    "¿Cuándo vence? Lo ideal es revisarlo con un par de meses de anticipación, para que [tengas|tenga] con qué comparar.",
                    "Mientras tanto [te|le] comparto {prueba}, para que lo [tengas|tenga] a mano.",
                ),
            ),
            Approach(
                "piloto",
                "Proponer una prueba pequeña",
                "Un piloto acotado no rompe el contrato y te deja demostrar resultados antes de la renovación.",
                (
                    "Tiene sentido no cambiar todo de un día para otro.",
                    "Muchos clientes empezaron con una prueba pequeña en paralelo y compararon resultados.",
                    "[¿Te|¿Le] interesaría probarlo con un equipo o una línea durante unas semanas?",
                ),
            ),
        ),
    ),
    Objection(
        "mala_experiencia",
        "Ya probaron algo parecido y no funcionó",
        "Hay una herida previa. Primero escucha qué falló; después muestra en qué eres distinto, con una prueba.",
        "¿Lo que falló la vez pasada queda cubierto con lo que [te|le] propongo?",
        (
            r"\bya (lo )?(probamos|intentamos|usamos)\b",
            r"\bno (nos )?(funciono|sirvio|dio resultado)\b",
            r"\bmala experiencia\b",
            r"\bnos fue mal\b",
        ),
        (
            Approach(
                "escuchar",
                "Preguntar qué falló",
                "Entender la causa real te da el argumento exacto y muestra respeto por su experiencia.",
                (
                    "Gracias por [contármelo|contármelo]; es muy útil saberlo.",
                    "¿Qué fue lo que no funcionó: los resultados, la implementación o el acompañamiento?",
                    "Así [te|le] digo con honestidad si lo nuestro lo resuelve o no.",
                ),
            ),
            Approach(
                "diferencia",
                "Mostrar la diferencia con una prueba",
                "No prometas: muestra un caso parecido al suyo y en qué cambia el enfoque.",
                (
                    "Entiendo la desconfianza.",
                    "Lo que nos diferencia: {diferencial}.",
                    "Un caso parecido: {prueba}.",
                    "[¿Te|¿Le] muestro en 20 minutos cómo lo haríamos distinto en [tu|su] caso?",
                ),
            ),
        ),
    ),
)

OBJECTION_BY_CODE = {o.code: o for o in OBJECTIONS}

# Respuestas que no son objeción: qué hacer según lo que la IA de respuestas entendió.
REPLY_PLAYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "reunion": (
        "Quiere reunión: cierra el horario ya, con dos opciones concretas.",
        (
            "¡Excelente!",
            "¿Qué día y hora [te|le] queda mejor esta semana? Si [prefieres|prefiere], [te|le] propongo dos opciones.",
            "Para aprovechar el tiempo, ¿hay algo puntual que [quieras|quiera] que revisemos?",
        ),
    ),
    "interesado": (
        "Está interesado: lleva el interés a una reunión antes de que se enfríe.",
        (
            "¡Qué bueno!",
            "Lo que más valoran nuestros clientes: {valor}.",
            "¿Lo vemos en 20 minutos esta semana? [Dime|Dígame] qué día [te|le] queda mejor.",
        ),
    ),
    "referido": (
        "Te refirió a otra persona: agradece y pide permiso para mencionarlo.",
        (
            "¡Muchas gracias por la orientación!",
            "¿Me [permites|permite] mencionar que [tú me sugeriste|usted me sugirió] escribirle?",
        ),
    ),
    "fuera_oficina": ("Respuesta automática: no respondas; ORKESTA retoma la secuencia sola.", ()),
    "baja": ("Pidió no recibir más: no respondas. ORKESTA ya lo sacó de las campañas.", ()),
    "otro": (
        "No es una objeción clara: responde corto y con una sola pregunta.",
        (
            "Gracias por [tu|su] respuesta.",
            "Para entender mejor: ¿qué es lo que más [te|le] interesa resolver hoy?",
        ),
    ),
}

# Lenguaje por nivel del decisor.
LEVEL_GROUPS: dict[str, str] = {
    "c_level": "alta",
    "duenio_socio": "alta",
    "vp": "alta",
    "director": "direccion",
    "gerente": "gestion",
    "jefe": "gestion",
    "coordinador": "operativo",
    "especialista": "operativo",
}
LEVEL_TALK: dict[str, tuple[str, str, str]] = {
    # (qué le importa, cómo escribirle, qué evitar)
    "alta": (
        "Impacto en el negocio, riesgo y tiempo: crecimiento, margen y decisiones a tiempo.",
        "Muy corto (3 a 5 líneas), una cifra de resultado y una sola pregunta.",
        "Detalles técnicos, funciones y lenguaje de folleto.",
    ),
    "direccion": (
        "Las metas de su área y cómo se ven ante la dirección general.",
        "Corto, con un resultado de una empresa parecida y un siguiente paso claro.",
        "Pedirle que haga trabajo operativo para evaluarte.",
    ),
    "gestion": (
        "Que su equipo opere mejor: tiempo, errores, carga de trabajo y metas del mes.",
        "Concreto: cómo cambia el día a día de su equipo y cuánto esfuerzo implica.",
        "Hablar solo de visión estratégica sin aterrizarla.",
    ),
    "operativo": (
        "Que la herramienta le facilite el trabajo y no le sume tareas.",
        "Práctico: cómo funciona, cuánto tarda en aprenderse y cómo se integra.",
        "Presionar por una decisión que no es suya: pídele que te presente a quien decide.",
    ),
}

_PAIR = re.compile(r"\[([^\[\]|]*)\|([^\[\]|]*)\]")


def voice(text: str, formal: bool) -> str:
    """Elige tú o usted en los marcadores ``[tú|usted]``."""
    return _PAIR.sub(lambda m: m.group(2) if formal else m.group(1), text)


# Más formas en que los vendedores cuentan cada objeción (se suman a las del mapa).
MORE_PATTERNS: dict[str, tuple[str, ...]] = {
    "precio": (r"\bcar[i]?simo\b", r"\bmucha plata\b", r"\bmuy costos[oa]\b", r"\bse sale del presupuesto\b",
               r"\bno (tienen|hay) (plata|presupuesto)\b"),
    "prioridad": (r"\blo deja(mos)? para (despues|luego|mas adelante)\b", r"\bno es (el|buen) momento\b",
                  r"\bcierre (fiscal|de ano)\b", r"\botras prioridades\b", r"\bel (proximo|otro) ano\b"),
    "persona": (r"\b(le )?escrib(a|e|ir) a (compras|otra area|su jefe)\b", r"\bno es (la persona|quien decide)\b",
                r"\bme derivo\b", r"\beso lo (maneja|ve|lleva) otr[ao]\b"),
    "competencia": (r"\bcotizacion mas barata\b", r"\bevaluando (\d+|dos|tres|varios|otros) proveedores\b",
                    r"\b(otro|la competencia) le (dio|ofrecio)\b", r"\bcomparando con\b"),
    "mala_experiencia": (r"\bquemad[oa]s\b", r"\bno sirve porque ya\b", r"\bles fue mal\b"),
    "silencio": (r"\bno (se|sé) nada\b", r"\bdesde la (demo|reunion|propuesta)\b", r"\bno contesta\b",
                 r"\bme (ignoro|dejo en visto)\b", r"\bno me ha vuelto a responder\b"),
    "no_interes": (r"\bno rotundamente\b", r"\bno le (interesa|vio valor)\b", r"\bno lo necesitan\b",
                   r"\bque no insista\b"),
    "info": (r"\b(mande|envie|manda|envia)(r)? (la |el |una )?(informacion|presentacion|portafolio|cotizacion)\b",),
    "comite": (r"\b(junta|comite|socio|gerente|finanzas) (lo )?(tiene que|debe) (aprobar|validar)\b",
               r"\b(consultarlo|validarlo) con\b"),
}  # fmt: skip

MORE_TOPICS: dict[str, tuple[str, ...]] = {
    "producto": (r"\bque vend(o|emos)\b", r"\bpropuesta de valor\b", r"\b(mi|nuestro) diferencial\b",
                 r"\b(explicame|resumeme) (mi|nuestro) producto\b"),
    "propuesta": (r"\bopciones de precio\b", r"\b(armo|arma|hago|presento) (una |la )?(propuesta|cotizacion)\b",
                  r"\bque (incluyo|pongo) en la (propuesta|cotizacion)\b"),
    "objecion": (r"\b(que le digo|como respondo|como rebato) si\b", r"\bya probaron\b",
                 r"\b(me|le) pusieron una objecion\b"),
}  # fmt: skip
