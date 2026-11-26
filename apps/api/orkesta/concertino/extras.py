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
    r"quemad[oa]|me quiero rendir|tirar la toalla|nadie me (responde|contesta)|no me sale nada|mala racha)\b"
)
_HOW = re.compile(
    r"\b(como|donde|en donde) (uso|usar|hago|hacer|creo|crear|configuro|configurar|conecto|conectar|veo|ver|lanzo|lanzar|envio|enviar|armo|armar|agrego|agregar|prospecto|prospectar)\b"
)

HOWTO: tuple[tuple[str, str, list[str]], ...] = (
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


def wants_win(question: str) -> bool:
    return bool(_WON.search(fold(question)))


def wants_mood(question: str) -> bool:
    return bool(_MOOD.search(fold(question)))


def howto(question: str) -> dict[str, Any] | None:
    folded = fold(question)
    if not _HOW.search(folded) or not re.search(
        r"\b(orkesta|app|aplicacion|plataforma|aqui|prospeccion|campana|propuesta|automatizacion|crm|gmail|buzon|firma)\b",
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
