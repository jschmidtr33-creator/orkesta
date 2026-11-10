"""Banco de comprensión de Concertino: frases como las escribe un vendedor (con faltas, sin
tildes, cortas) y lo que se espera entender. Si el puntaje baja de la meta, la prueba falla y el
cambio no se publica."""

from __future__ import annotations

from orkesta.concertino.intent import understand

# (frase, acción esperada, pieza esperada, cantidad esperada o None)
BANK: list[tuple[str, str | None, str | None, int | None]] = [
    # Redactar secuencias
    ("Crea la secuencia de 3 correos", "redactar", "secuencia", 3),
    ("crea la secuensia de 3 correos", "redactar", "secuencia", 3),
    ("Creame una secuencia de 4 correos para un CEO", "redactar", "secuencia", 4),
    ("arma una cadencia de 5 toques para gerentes de TI", "redactar", "secuencia", 5),
    ("hazme 3 correos de seguimiento", "redactar", "secuencia", 3),
    ("necesito una secuencia de correos para banca", "redactar", "secuencia", None),
    ("Secuencia de 3 correos para retail", "redactar", "secuencia", 3),
    ("redactame tres correos para invitar al congreso", "redactar", "secuencia", 3),
    ("genera una campaña de correos para CFOs", "redactar", "secuencia", None),
    ("escribe dos correos para un director comercial", "redactar", "secuencia", 2),
    ("quiero una secuencia corta de 2 mails", "redactar", "secuencia", 2),
    ("prepara una secuencia de seis correos", "redactar", "secuencia", 6),
    ("me armas una secuencia para telcos?", "redactar", "secuencia", None),
    ("dame una secuencia de 3 emails en ingles", "redactar", "secuencia", 3),
    # Redactar un correo
    ("Escribeme un correo para un gerente de TI", "redactar", "correo", None),
    ("redacta un correo para invitar a un CEO al congreso", "redactar", "correo", None),
    ("hazme un mail de seguimiento", "redactar", "correo", None),
    ("necesito un correo formal para el CFO", "redactar", "correo", None),
    ("dame un correo corto para un director de innovacion", "redactar", "correo", None),
    ("crea un email para un prospecto de seguros", "redactar", "correo", None),
    ("ayudame a escribir un correo para una clinica", "redactar", "correo", None),
    ("quiero un correo para el CMO de un banco", "redactar", "correo", None),
    ("propon un correo de primer contacto", "redactar", "correo", None),
    ("escribe un mensaje de seguimiento para alguien que no responde", "redactar", "correo", None),
    # WhatsApp y LinkedIn
    ("dame un whatsapp para un CFO", "redactar", "whatsapp", None),
    ("escribeme un wsp para hacer seguimiento", "redactar", "whatsapp", None),
    ("hazme un mensaje de whatsapp corto", "redactar", "whatsapp", None),
    ("mensaje de WhatsApp para un gerente general", "redactar", "whatsapp", None),
    ("redacta un mensaje de linkedin para conectar", "redactar", "linkedin", None),
    ("dame un inmail para un CTO", "redactar", "linkedin", None),
    ("mensaje de linkedin en ingles", "redactar", "linkedin", None),
    ("crea una invitacion de linkedin", "redactar", "linkedin", None),
    # Asuntos, resumen y guion
    ("hazme 5 asuntos", "redactar", "asuntos", None),
    ("dame asuntos para un correo a CEOs", "redactar", "asuntos", None),
    ("necesito un resumen ejecutivo", "redactar", "resumen", None),
    ("prepara un one pager para el jefe de mi contacto", "redactar", "resumen", None),
    ("arma un resumen ejecutivo de la membresia", "redactar", "resumen", None),
    ("arma un guion de llamada para un director comercial", "redactar", "guion", None),
    ("dame un script para una llamada en frio", "redactar", "guion", None),
    ("que le digo en la llamada al gerente de TI?", "redactar", "guion", None),
    # No son pedidos de redacción (los resuelve el resto de Concertino)
    ("¿Cuántos correos manda la campaña?", None, None, None),
    ("Me dijo que es muy caro", None, None, None),
    ("¿Qué vendo?", None, None, None),
    ("Revisa mi campaña", None, None, None),
    ("¿Cuánto cuesta la entrada?", None, None, None),
    ("¿Cómo le fue a mi campaña de correos?", None, None, None),
    ("¿Qué le ofrezco a un CEO?", None, None, None),
    ("¿Por qué no me responden los correos?", None, None, None),
    ("Hola", None, None, None),
    ("gracias", None, None, None),
    ("No me responde hace dos semanas", None, None, None),
    ("¿Cuándo propongo una delegación?", None, None, None),
    ("ya tienen proveedor", None, None, None),
    ("¿A qué sector apunto?", None, None, None),
]
GOAL = 0.9


def test_comprehension_bank_meets_goal() -> None:
    misses = []
    for text, action, piece, count in BANK:
        it = understand(text)
        ok = it.action == action and (action is None or it.piece == piece)
        ok = ok and (count is None or it.count == count)
        if not ok:
            misses.append(f"{text!r}: {it.action}/{it.piece}/{it.count}")
    score = 1 - len(misses) / len(BANK)
    assert score >= GOAL, f"Comprensión {score:.0%} (< {GOAL:.0%}). Fallan: {misses}"


def test_details_and_tweaks() -> None:
    it = understand("crea la secuensia de 3 correos para un CEO de banca, formal")
    assert (it.piece, it.count, it.cargo, it.industry, it.tone) == (
        "secuencia",
        3,
        "ceo",
        "banca_finanzas",
        "formal",
    )
    invite = understand("Escríbeme un correo para invitar a un gerente de TI al congreso")
    assert invite.cargo == "gerente de ti" and invite.goal == "evento"
    for tweak in ("Hazlo más corto", "Ahora para un CFO", "Más formal", "en inglés"):
        assert understand(tweak, last_topic="redactar").action == "ajustar", tweak
    assert understand("gracias", last_topic="redactar").action is None
