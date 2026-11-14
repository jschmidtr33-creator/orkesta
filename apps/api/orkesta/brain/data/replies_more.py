# ruff: noqa: E501
"""Más respuestas etiquetadas a mano (redactadas por ORKESTA, no salen de ningún buzón) para que
el clasificador propio vea más formas de decir lo mismo: tuteo y usted, frases cortas, firmas,
respuestas desde el celular, español de distintos países, inglés y portugués."""

from __future__ import annotations

MORE: dict[str, list[str]] = {
    "reunion": [
        "Buenas tardes, con gusto. ¿Le sirve el martes 9 am?", "Ok, el jueves a las 4 estoy libre.",
        "Agendemos. ¿Mañana a primera hora?", "Va, mándame el link de Meet para el viernes.",
        "Estimado, podemos reunirnos la próxima semana. Coordine con mi asistente.",
        "Sí, con gusto lo vemos en una videollamada corta.", "Listo, te espero el lunes a las 11.",
        "Me interesa, separemos 30 minutos esta semana.", "Organicemos una presentación para el comité el día 20.",
        "Claro, ¿qué horarios tienes disponibles?", "Let's meet on Tuesday, send me a few options.",
        "Works for me, book a slot on my calendar.", "Podemos conversar amanhã às 10h?",
        "Sí, quiero ver la demo. ¿Cuándo puedes?", "Pásame tu agenda y escojo un espacio.",
    ],
    "interesado": [
        "Suena interesante, cuéntame un poco más.", "Me llama la atención, ¿cómo funciona exactamente?",
        "Nos podría servir. ¿Qué incluye el plan?", "Interesante propuesta, la estamos evaluando.",
        "Justo estábamos buscando algo así.", "Me gusta la idea, ¿con qué empresas trabajan?",
        "Podría encajar con lo que necesitamos este año.", "Sí me interesa, ¿cómo seguimos?",
        "Gracias por escribir, es un tema que nos preocupa.", "This could be relevant for us, tell me more.",
        "Interesting, how does pricing work?", "Parece útil, qual é o próximo passo?",
        "Hola, sí. Tenemos esa necesidad en el área comercial.", "Me interesa para mi equipo de ventas.",
    ],
    "mas_info": [
        "¿Me puedes enviar una presentación?", "Envíame la información por correo y la reviso.",
        "¿Tienes un PDF con los precios?", "Mándame el brochure, por favor.", "Comparte una ficha técnica y lo evalúo.",
        "Antes de reunirnos, ¿me envías más detalles?", "Quisiera conocer los planes y precios.",
        "Por favor envíenos una cotización formal.", "¿Tienen un video o una demo grabada?",
        "Can you send me a one-pager?", "Please share some pricing details first.",
        "Pode me mandar uma apresentação?", "Envíame casos de éxito de empresas como la nuestra.",
        "¿Qué necesitan de nosotros para cotizar?",
    ],
    "referido": [
        "Esto lo ve mi compañera Laura, te copio.", "Te sugiero escribirle al gerente de compras.",
        "No es mi área, habla con Pedro de tecnología.", "Le reenvío su correo al responsable.",
        "La persona indicada es nuestra jefa de mercadeo, te paso su correo.",
        "Contacta a nuestro director comercial, él decide eso.", "Copio a quien maneja ese tema en la empresa.",
        "That would be my colleague in procurement, cc'ing her.", "Please reach out to our CTO instead.",
        "Quem cuida disso é o time de compras, vou te passar o contato.",
        "Escríbele a operaciones@, ellos lo manejan.", "Te comparto el contacto del área de innovación.",
    ],
    "no_ahora": [
        "Ahora no es buen momento, escríbeme en marzo.", "Estamos en cierre de año, retomemos en enero.",
        "Por el momento no, quizás el próximo semestre.", "Ya tenemos el presupuesto asignado, hablemos el otro año.",
        "Estamos en medio de una reestructuración, más adelante.", "Contáctame en unos tres meses.",
        "No este trimestre, pero guárdame para el siguiente.", "Not right now, check back in Q2.",
        "Maybe later this year.", "Agora não, me procura no ano que vem.",
        "Estamos implementando otro proyecto, escríbeme después de julio.", "En este momento no podemos, más adelante sí.",
    ],
    "no_interesado": [
        "No nos interesa, gracias.", "No aplica para nuestra empresa.", "Gracias, pero no lo necesitamos.",
        "Ya tenemos proveedor y estamos contentos.", "No estamos buscando nada así.",
        "Le agradezco, pero no es para nosotros.", "No, gracias. Saludos.", "No vemos necesidad de esto.",
        "Not interested, thanks.", "We're all set, thank you.", "Não temos interesse, obrigado.",
        "Preferimos seguir con nuestra solución actual.", "Declinamos su propuesta.",
    ],
    "baja": [
        "Por favor sáquenme de su lista.", "No me vuelvan a escribir.", "Elimine mi correo de su base de datos.",
        "Dejen de enviarme estos correos.", "Quiero darme de baja.", "No autorizo el uso de mis datos.",
        "Unsubscribe me please.", "Remove me from your mailing list.", "Stop emailing me.",
        "Por favor me retirem da lista.", "Esto es spam, bórrenme.", "No deseo recibir más comunicaciones.",
    ],
    "fuera_oficina": [
        "Estoy fuera de la oficina hasta el lunes 15. Para temas urgentes escribir a soporte@.",
        "Respuesta automática: me encuentro de vacaciones y regreso el 3 de febrero.",
        "Gracias por su mensaje. Estaré sin acceso al correo hasta el próximo martes.",
        "Me encuentro en licencia. Responderé a mi regreso.", "Actualmente estoy de viaje con acceso limitado al correo.",
        "Out of office until Monday. I'll reply when I'm back.", "I'm on vacation with no access to email.",
        "Estou fora do escritório até dia 10.", "Este buzón ya no está activo. Por favor escriba a contacto@.",
        "Ya no trabajo en la empresa. Por favor contacte a recepción.",
    ],
    "otro": [
        "¿De dónde sacaron mi correo?", "Recibido.", "Gracias.", "¿Quién eres?", "Ok.",
        "¿Esto es una venta?", "Ya les había respondido antes.", "Who is this?", "Thanks.",
        "¿Me puedes recordar de qué empresa eres?", "Te respondo luego.", "Vale.",
    ],
}  # fmt: skip
