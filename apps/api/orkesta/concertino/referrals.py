# ruff: noqa: E501  (textos para el vendedor: líneas largas a propósito)
"""Pedir referidos, testimonios y llamadas de referencia a clientes que ya compraron, con la
ficha del producto. Lo que dice cualquier vendedor senior: pedir en el mejor momento (después
de un resultado), ser específico y dejarle el texto listo al cliente."""

from __future__ import annotations

from typing import Any

from orkesta.concertino import engine
from orkesta.concertino import playbook as pb
from orkesta.concertino.linkedin import _audience, _cut, _low


def referrals(ctx: engine.Context, b: Any, meta: dict[str, Any]) -> dict[str, Any]:
    v = engine.slots(ctx)
    formal = meta["formal"]
    _area, peers = _audience(ctx, meta)
    generic = peers == "líderes de empresa"
    who = "personas en empresas parecidas a la [tuya|suya]" if generic else peers
    problema = _cut(v.get("problema", ""), 100)
    valor = _cut(v.get("valor", ""), 120)
    product = meta["product"]
    company = b.company or "nuestro equipo"
    need = (
        f" que estén enfrentando {_low(problema)}"
        if problema
        else " a quienes les pueda servir lo mismo"
    )
    steps = (
        ("Pedir un referido", "Específico y fácil: un perfil claro (cargo y problema) y tú haces el resto. Pedir «a alguien que conozcas» no funciona.",
         f"Hola, {{{{nombre}}}}:\n\nMe alegra mucho ver cómo va {{{{empresa|[tu|su] equipo}}}} con {product}.\n\n"
         f"Una pregunta: ¿[conoces|conoce] a 1 o 2 {who}{need}? Si [me presentas|me presenta] por correo, les escribo con cuidado y menciono que vengo de [tu|su] parte.\n\n¡Gracias por la confianza!"),
        ("Pedir un testimonio", "Ofrecer un borrador multiplica las respuestas: el cliente solo ajusta y aprueba.",
         "Hola, {{nombre}}:\n\n¿Me [regalarías|regalaría] dos líneas sobre qué cambió desde que [trabajas|trabaja] con nosotros? Lo usaríamos, con [tu|su] permiso, como referencia para otros clientes.\n\n"
         + (f"Si [te|le] sirve, aquí va un borrador para que solo lo [ajustes|ajuste]: «Con {product} logramos {_low(valor)}." + ("»" if generic else f" Lo recomiendo a {peers}.»") if valor
            else "Si [prefieres|prefiere], [te|le] envío un borrador y solo lo [ajustas|ajusta].")),
        ("Llamada de referencia", "Para cierres grandes: un cliente contento convence más que cualquier propuesta. Pide 10 minutos y avisa antes de dar su contacto.",
         f"Hola, {{{{nombre}}}}:\n\nEstoy conversando con una empresa parecida a {{{{empresa|la [tuya|suya]}}}} que quiere escuchar a alguien que ya [usa|use] {product}.\n\n"
         "¿[Aceptarías|Aceptaría] una llamada de 10 minutos con ellos esta semana o la próxima? Yo coordino todo y [te|le] envío los detalles antes."),
        ("Texto para que te presente", "Déjale el correo de presentación listo para reenviar: así el referido llega con contexto y sin esfuerzo para tu cliente.",
         f"Hola, [nombre del referido]:\n\nTe presento a [tu nombre], de {company}. Trabajamos con ellos en {product}"
         + (f" y logramos {_low(valor)}" if valor else "") + ".\n\nCreo que les puede servir conversar. Los dejo en contacto."),
    )  # fmt: skip
    options = [{"code": f"referido:{i}", "name": n, "why": w, "text": pb.voice(t, formal), "record": None}
               for i, (n, w, t) in enumerate(steps)]  # fmt: skip
    tips = [
        "Pide justo después de un resultado o de un buen comentario del cliente: es cuando más ganas tiene de ayudar.",
        "Sé específico: cargo, tipo de empresa y problema. Un perfil claro le trae a alguien a la mente.",
        "Cuando el referido avance, cuéntale a tu cliente cómo terminó y agradécele: así vuelve a recomendarte.",
    ]
    return {"summary": "Para pedir referidos sin incomodar: 4 textos, del pedido directo al correo de presentación listo para reenviar.",
            "options": options, "tips": tips}  # fmt: skip
