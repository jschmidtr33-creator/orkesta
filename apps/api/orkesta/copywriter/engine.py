# ruff: noqa: E501  (textos de redacción: líneas largas a propósito)
"""Redactor de secuencias de correo de ORKESTA (IA propia, determinista y sin costo externo).

Combina el producto, el buyer persona, la industria, el objetivo, el tono y una estructura
probada (PAS, AIDA, BAB, propuesta de valor o pregunta) para escribir una secuencia completa con
seguimientos, variantes de asunto y variables de personalización. Con el buyer persona
«automático», cada contacto recibe el reto de su área y su industria al enviar ({{reto}},
{{industria}}).
"""

from __future__ import annotations

import html
import random
import re
from dataclasses import dataclass, field
from typing import Any

from orkesta.copywriter.knowledge import (
    FRAMEWORKS,
    GOALS,
    INDUSTRY_CONTEXT,
    LANGUAGES,
    PERSONAS,
    TONES,
    WHO,
)

DELAYS = (0, 3, 4, 5, 7, 10)


@dataclass
class Brief:
    product: str
    summary: str = ""
    benefits: list[str] = field(default_factory=list)
    proof: str = ""
    company: str = ""
    area: str = "auto"  # código de área o «auto» (según cada contacto)
    persona_label: str = ""
    pains: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    industry: str = ""
    goal: str = "reunion"
    tone: str = "cercano"
    framework: str = "pas"
    language: str = "es"
    steps: int = 3
    link: str = ""
    variant: int = 0


def _e(text: str) -> str:
    return html.escape(" ".join(text.split()), quote=False)


def _first_sentences(text: str, n: int = 1) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(parts[:n]).strip()


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def _strip_end(text: str) -> str:
    return text.strip().rstrip(".;:")


class _Writer:
    def __init__(self, brief: Brief):
        self.b = brief
        self.rng = random.Random(brief.variant * 7919 + 17)  # noqa: S311 (variantes de texto)
        self.lang = brief.language if brief.language in LANGUAGES else "es"
        self.formal = brief.tone == "formal"
        self.short = brief.tone == "directo"
        area = brief.area if brief.area in PERSONAS else ""
        base = PERSONAS.get(area or "direccion", PERSONAS["direccion"])[self.lang]
        self.auto = brief.area == "auto" or not area
        self.pains = [_strip_end(p) for p in brief.pains if p.strip()] or (
            [] if self.auto else list(base["pains"])
        )
        self.goals = [g.strip() for g in brief.goals if g.strip()] or list(base["goals"])
        self.area_label = brief.persona_label or (
            "" if self.auto else WHO.get(area, {}).get(self.lang, base["label"])
        )
        ctx = INDUSTRY_CONTEXT.get(brief.industry, {}).get(self.lang)
        self.industry = ctx[0] if ctx else ""
        self.trend = ctx[1] if ctx else ""
        self.benefits = [_strip_end(b) for b in brief.benefits if b.strip()]
        if not self.benefits and brief.summary:
            self.benefits = [_strip_end(s) for s in re.split(r"(?<=[.!?])\s+", brief.summary) if s]
        self.product = _e(brief.product) or (
            "nuestra solución" if self.lang == "es" else "our solution"
        )

    # --- piezas -----------------------------------------------------------------------------

    def pick(self, *options: str) -> str:
        return self.rng.choice([o for o in options if o])

    def you(self, tu: str, usted: str, en: str) -> str:
        if self.lang == "en":
            return en
        return usted if self.formal else tu

    def pain(self, i: int = 0) -> str:
        if self.pains:
            return _e(self.pains[i % len(self.pains)])
        fallback = "los retos de su área" if self.lang == "es" else "your team's challenges"
        return "{{reto|" + fallback + "}}"

    def where(self) -> str:
        if self.industry:
            return _e(self.industry)
        return "{{industria|" + ("su sector" if self.lang == "es" else "your industry") + "}}"

    def who(self) -> str:
        if self.area_label:
            return _e(_lower_first(self.area_label))
        return (
            "{{cargo|" + ("líderes como tú" if not self.formal else "líderes") + "}}"
            if (self.lang == "es")
            else "{{cargo|leaders}}"
        )

    def benefit(self, i: int) -> str:
        if self.benefits:
            return _e(_lower_first(self.benefits[i % len(self.benefits)]))
        return (
            "ahorrar tiempo y tener más control"
            if self.lang == "es"
            else "save time and gain control"
        )

    def company(self) -> str:
        return "{{empresa|" + self.you("tu equipo", "su equipo", "your team") + "}}"

    def hello(self) -> str:
        if self.lang == "en":
            return self.pick("Hi {{nombre|there}},", "{{nombre|Hi}},")
        if self.formal:
            return self.pick("Hola, {{nombre|}}:", "Buen día, {{nombre|}}:")
        return self.pick("Hola, {{nombre|}}:", "{{nombre|Hola}},", "Hola {{nombre|}},")

    def signature(self) -> str:
        bye = (
            self.pick("Saludos,", "Un saludo,")
            if self.lang == "es"
            else self.pick("Best,", "Thanks,")
        )
        company = f"<br>{_e(self.b.company)}" if self.b.company else ""
        return f"<p>{bye}<br>{{{{remitente}}}}{company}</p>"

    def proof(self) -> str:
        return _e(_strip_end(self.b.proof)) if self.b.proof.strip() else ""

    def cta(self, soft: bool = False) -> str:
        g, link = self.b.goal, _e(self.b.link)
        es_tu = {
            "reunion": self.pick(
                "¿Te parece si lo conversamos 15 minutos esta semana o la próxima?",
                "¿Tienes 15 minutos esta semana para conversarlo?",
            ),
            "demo": f"¿Te muestro en 20 minutos cómo se vería en {self.company()}?",
            "propuesta": "Si te hace sentido, te preparo una propuesta a la medida. ¿Lo vemos?",
            "contenido": "Preparé un recurso corto sobre esto. ¿Te lo comparto?",
            "referido": "Si no eres la persona indicada, ¿me ayudas indicándome con quién hablar?",
            "evento": "Estamos organizando un espacio sobre este tema. ¿Te gustaría acompañarnos?",
        }
        es_usted = {
            "reunion": self.pick(
                "¿Le parece si lo conversamos 15 minutos esta semana o la próxima?",
                "¿Tendría 15 minutos esta semana para conversarlo?",
            ),
            "demo": f"¿Le muestro en 20 minutos cómo se vería en {self.company()}?",
            "propuesta": "Si le hace sentido, le preparo una propuesta a la medida. ¿Lo revisamos?",
            "contenido": "Preparé un recurso breve sobre esto. ¿Se lo comparto?",
            "referido": "Si no es usted la persona indicada, ¿me ayuda indicándome con quién hablar?",
            "evento": "Estamos organizando un espacio sobre este tema. ¿Le gustaría acompañarnos?",
        }
        en = {
            "reunion": self.pick(
                "Open to a 15-minute chat this week or next?",
                "Do you have 15 minutes this week to discuss?",
            ),
            "demo": f"Can I show you in 20 minutes how it would look at {self.company()}?",
            "propuesta": "If it makes sense, I can put together a tailored proposal. Shall we?",
            "contenido": "I put together a short resource on this. Want me to send it?",
            "referido": "If you're not the right person, could you point me to who is?",
            "evento": "We're hosting a session on this topic. Would you like to join?",
        }
        table = en if self.lang == "en" else (es_usted if self.formal else es_tu)
        text = table.get(g, table["reunion"])
        if soft:
            text = {
                "es": self.you(
                    "¿Te hace sentido conversarlo?", "¿Le hace sentido conversarlo?", ""
                ),
                "en": "Worth a quick conversation?",
            }[self.lang] or text
        if link and g in ("reunion", "demo", "evento"):
            extra = self.you(
                "Si prefieres, elige el horario aquí",
                "Si prefiere, elija el horario aquí",
                "If easier, pick a time here",
            )
            text += f" {extra}: {link}"
        elif link and g == "contenido":
            text = (
                self.you("Te dejo el recurso aquí", "Le dejo el recurso aquí", "Here it is")
                + f": {link}. "
                + self.you("¿Qué te parece?", "¿Qué le parece?", "What do you think?")
            )
        return text

    # --- correos ----------------------------------------------------------------------------

    def first(self) -> list[str]:
        fw = self.b.framework if self.b.framework in FRAMEWORKS else "pas"
        p: list[str] = []
        if fw == "pas":
            p.append(
                self.you(
                    f"En {self.where()}, muchos {self.who()} me cuentan que {self.pain(0)} es de lo que más tiempo les quita.",
                    f"En {self.where()}, muchos {self.who()} nos comentan que {self.pain(0)} es uno de sus principales retos.",
                    f"Across {self.where()}, many {self.who()} tell me that {self.pain(0)} is one of their biggest challenges.",
                )
            )
            if not self.short:
                goal = _e(self.goals[0]) if self.goals else ""
                p.append(
                    self.you(
                        f"Cuando no se resuelve, frena metas como {goal or 'el crecimiento'} y consume el tiempo del equipo.",
                        f"Cuando no se resuelve, frena metas como {goal or 'el crecimiento'} y consume el tiempo del equipo.",
                        f"Left unsolved, it shows up in {goal or 'results'} and in the team's time.",
                    )
                )
            p.append(
                self.you(
                    f"Con {self.product} podemos ayudar a {self.company()} a resolverlo: {self.benefit(0)}.",
                    f"Con {self.product} podemos ayudar a {self.company()} a resolverlo: {self.benefit(0)}.",
                    f"With {self.product} we can help {self.company()} solve it: {self.benefit(0)}.",
                )
            )
        elif fw == "aida":
            p.append(
                self.you(
                    f"¿Cómo están abordando {self.pain(0)} en {self.company()}?",
                    f"¿Cómo están abordando {self.pain(0)} en {self.company()}?",
                    f"How is {self.company()} tackling {self.pain(0)} today?",
                )
            )
            summary = _e(_first_sentences(self.b.summary)) if self.b.summary else ""
            if summary and not self.short:
                p.append(f"{self.product}: {summary}")
            items = "".join(
                f"<li>{_upper_first(self.benefit(i))}</li>"
                for i in range(min(3, max(1, len(self.benefits))))
            )
            lead = self.you(
                "Con esto, equipos como el tuyo logran:",
                "Con esto, equipos como el suyo logran:",
                "With it, teams like yours get to:",
            )
            p.append(
                f"{lead}</p><ul>{items}</ul><p>" if not self.short else f"{lead} {self.benefit(0)}."
            )
        elif fw == "bab":
            p.append(
                self.you(
                    f"Hoy, en muchas empresas de {self.where()}, un reto constante es {self.pain(0)}.",
                    f"Hoy, en muchas empresas de {self.where()}, un reto constante es {self.pain(0)}.",
                    f"Today, at many {self.where()} companies, a constant challenge is {self.pain(0)}.",
                )
            )
            p.append(
                self.you(
                    f"Imagina poder {self.benefit(0)} sin sumar carga al equipo.",
                    f"Imagine poder {self.benefit(0)} sin sumar carga al equipo.",
                    f"Imagine being able to {self.benefit(0)} without adding load to the team.",
                )
            )
            if not self.short:
                p.append(
                    self.you(
                        f"{self.product} es el puente para llegar ahí.",
                        f"{self.product} es el puente para llegar ahí.",
                        f"{self.product} is the bridge to get there.",
                    )
                )
        elif fw == "valor":
            p.append(
                self.you(
                    f"{self.product} ayuda a {self.who()} a {self.benefit(0)}.",
                    f"{self.product} ayuda a {self.who()} a {self.benefit(0)}.",
                    f"{self.product} helps {self.who()} {self.benefit(0)}.",
                )
            )
            if self.trend and not self.short:
                p.append(
                    self.you(
                        f"Con {_e(self.trend)}, es justo lo que hoy marca la diferencia.",
                        f"Con {_e(self.trend)}, es justo lo que hoy marca la diferencia.",
                        f"With {_e(self.trend)}, that's what makes the difference today.",
                    )
                )
        else:  # pregunta
            p.append(
                self.you(
                    f"Una pregunta rápida: ¿{self.pain(0)} es prioridad en {self.company()} este trimestre?",
                    f"Una pregunta breve: ¿{self.pain(0)} es prioridad en {self.company()} este trimestre?",
                    f"Quick question: is {self.pain(0)} a priority at {self.company()} this quarter?",
                )
            )
        if self.proof() and fw != "pregunta":
            p.append(self.you(f"{self.proof()}.", f"{self.proof()}.", f"{self.proof()}."))
        if fw == "pregunta":
            p.append(
                self.you(
                    f"Si lo es, tengo una idea con {self.product} que podría servirte.",
                    f"Si lo es, tengo una idea con {self.product} que podría servirle.",
                    f"If so, I have an idea with {self.product} that could help.",
                )
            )
        else:
            p.append(self.cta())
        return p

    def follow_up(self, n: int) -> list[str]:
        kind = ("valor", "angulo", "cierre", "caso", "final")[min(n - 1, 4)]
        if kind == "valor":
            fact = _lower_first(self.proof()) if self.proof() else self.benefit(1)
            return [
                self.you(
                    f"Retomo mi correo anterior con un dato concreto para {self.company()}: {fact}.",
                    f"Retomo mi correo anterior con un dato concreto para {self.company()}: {fact}.",
                    f"Following up with something concrete for {self.company()}: {fact}.",
                ),
                self.cta(soft=True),
            ]
        if kind == "angulo":
            return [
                self.you(
                    f"Otro reto que escucho seguido entre {self.who()}: {self.pain(1)}.",
                    f"Otro reto frecuente entre {self.who()}: {self.pain(1)}.",
                    f"Another challenge I often hear from {self.who()}: {self.pain(1)}.",
                ),
                self.you(
                    f"Ahí {self.product} permite {self.benefit(2)}. ¿Es algo que estén viendo en {self.company()}?",
                    f"Ahí {self.product} permite {self.benefit(2)}. ¿Es algo que estén evaluando en {self.company()}?",
                    f"That's where {self.product} lets you {self.benefit(2)}. Is it on the radar at {self.company()}?",
                ),
            ]
        if kind == "cierre":
            return [
                self.you(
                    "No quiero insistir de más. Si no es el momento, dime y cierro el tema; si conviene hablar con otra persona del equipo, ¿me indicas con quién?",
                    "No quiero insistir de más. Si no es el momento, me lo indica y cierro el tema; si conviene hablar con otra persona, ¿me indica con quién?",
                    "I don't want to be a pest. If now isn't the time, just say so and I'll close the loop; if someone else owns this, who should I talk to?",
                )
            ]
        if kind == "caso":
            return [
                self.you(
                    f"Te comparto cómo lo vemos en {self.where()}: {self.benefit(3)}.",
                    f"Le comparto cómo lo vemos en {self.where()}: {self.benefit(3)}.",
                    f"Here's what we see in {self.where()}: {self.benefit(3)}.",
                ),
                self.cta(soft=True),
            ]
        return [
            self.you(
                "Último mensaje de mi parte por ahora. Si más adelante es prioridad, aquí estaré.",
                "Último mensaje de mi parte por ahora. Si más adelante es prioridad, quedo atento.",
                "Last note from me for now. If it becomes a priority later, I'm here.",
            )
        ]

    def subjects(self) -> list[str]:
        goal = _e(self.goals[0]) if self.goals else ""
        if self.lang == "en":
            options = [
                "{{nombre}}, an idea for {{empresa|your team}}",
                f"{goal} at {{{{empresa|your company}}}}" if goal else "",
                "Quick question, {{nombre}}",
                f"{self.product} + {{{{empresa|you}}}}",
                f"{{{{empresa|Your team}}}} and {self.pain(0)}" if self.pains else "",
            ]
        else:
            options = [
                "{{nombre}}, una idea para {{empresa|tu equipo}}",
                f"{_upper_first(goal)} en {{{{empresa|tu empresa}}}}" if goal else "",
                "Pregunta rápida, {{nombre}}",
                f"{self.product} + {{{{empresa|ustedes}}}}",
                f"¿{_upper_first(self.pain(0))}?" if self.pains and len(self.pains[0]) < 45 else "",
            ]
        out = [o for o in options if o]
        self.rng.shuffle(out)
        return out[:5]

    def body(self, paragraphs: list[str]) -> str:
        inner = "".join(f"<p>{p}</p>" for p in paragraphs)
        return f"<p>{self.hello()}</p>{inner}{self.signature()}".replace("<p></p>", "")


def compose(brief: Brief) -> dict[str, Any]:
    w = _Writer(brief)
    count = max(1, min(6, brief.steps))
    subjects = w.subjects()
    steps = [{"subject": subjects[0], "subjects": subjects, "body": w.body(w.first()),
              "delay_days": 0}]  # fmt: skip
    for n in range(1, count):
        steps.append({"subject": "", "subjects": [], "body": w.body(w.follow_up(n)),
                      "delay_days": DELAYS[n]})  # fmt: skip
    tips = []
    if w.auto:
        tips.append(
            "Buyer persona automático: cada contacto recibe el reto de su área ({{reto}}) "
            "y su industria ({{industria}})."
            if w.lang == "es"
            else "Automatic persona: each contact gets their area's challenge and industry."
        )
    if not brief.benefits and not brief.summary:
        tips.append(
            "Agrega 2 o 3 beneficios concretos (con cifras si las tienes) para un mensaje más fuerte."
            if w.lang == "es"
            else "Add 2-3 concrete benefits (with numbers if possible)."
        )
    if not brief.proof:
        tips.append(
            "Suma una prueba social: un cliente, una cifra o un resultado logrado."
            if w.lang == "es"
            else "Add social proof: a customer, a number or a result."
        )
    return {"steps": steps, "tips": tips}


def options(language: str = "es") -> dict[str, Any]:
    from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS

    lang = language if language in LANGUAGES else "es"
    return {
        "industries": [{"value": k, "label": v} for k, v in INDUSTRY_LABELS.items()],
        "goals": [{"value": k, "label": v[lang]} for k, v in GOALS.items()],
        "tones": [{"value": k, "label": v[lang]} for k, v in TONES.items()],
        "frameworks": [{"value": k, "label": v[lang]} for k, v in FRAMEWORKS.items()],
        "languages": [{"value": k, "label": v} for k, v in LANGUAGES.items()],
        "personas": [
            {
                "value": "auto",
                "label": "Automático según cada contacto",
                "pains": [],
                "goals": [],
                "objections": [],
            },
            *[{"value": k, **v[lang]} for k, v in PERSONAS.items()],
        ],
    }


def contact_reto(area_code: str | None, language: str = "es") -> str:
    """Reto típico del área de un contacto (para la variable {{reto}})."""
    persona = PERSONAS.get(area_code or "", PERSONAS["direccion"])
    return persona.get(language, persona["es"])["pains"][0]


def contact_industry(industry_code: str | None, raw: str | None, language: str = "es") -> str:
    ctx = INDUSTRY_CONTEXT.get(industry_code or "", {}).get(language)
    return ctx[0] if ctx else (raw or "")
