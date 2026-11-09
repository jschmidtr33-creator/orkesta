"""Redacción que aprende entre todos: estilos abstractos, probabilidad de respuesta con
sugerencias, y rankings que no dejan deducir los resultados de una sola empresa."""

from __future__ import annotations

from typing import Any

import pytest

from orkesta.brain import copywriting as cw
from orkesta.brain import ml, registry

ASK = (
    "Hola, {{nombre|}}:\n\nVi que {{empresa}} está creciendo. "
    "¿Te sirve una llamada de 15 minutos el jueves?\n\nAna"
)
STATE = "Estimado equipo:\n\nAdjunto nuestro portafolio completo de servicios.\n\nSaludos,\nAna"


def test_style_is_abstract() -> None:
    st = cw.style("Una idea para {{empresa}}", ASK, 0)
    assert st["apertura"] == "nombre" and st["cierre"] == "reunion" and st["preguntas"] == "1"
    assert st["variables"] == "2+" and st["paso"] == "1"
    other = cw.style("Portafolio", STATE, 2)
    assert other["apertura"] == "saludo" and other["cierre"] == "afirmacion"
    assert other["preguntas"] == "0" and other["paso"] == "3"
    # Solo categorías: ninguna palabra del correo sobrevive.
    assert not any("portafolio" in v for v in other.values())


def _examples(n_tenants: int) -> list[ml.Example]:
    out = []
    for t in range(n_tenants):
        for i in range(40):
            closing = "reunion" if i % 2 else "afirmacion"
            st = {"apertura": "nombre", "cierre": closing, "largo": "corto", "preguntas": "1",
                  "variables": "1", "asunto": "corto", "asunto_pregunta": "no", "paso": "1"}  # fmt: skip
            ok = closing == "reunion" and i % 4 == 1
            out.append(ml.Example(f"r:{t}-{i}", cw.features(st), cw.YES if ok else cw.NO, f"t{t}"))
    return out


def test_assess_suggests_the_style_that_replies_more(monkeypatch: pytest.MonkeyPatch) -> None:
    examples = _examples(3)
    model = cw.make().fit([e.x for e in examples], [e.y for e in examples])
    monkeypatch.setattr(registry, "active", lambda _s, _n: (model, 0.5, 1))
    flat = cw.assess(None, "Portafolio", STATE)  # type: ignore[arg-type]
    meeting = cw.assess(None, "Idea", ASK)  # type: ignore[arg-type]
    assert flat and meeting and meeting["probability"] > flat["probability"]
    assert any("reunión" in tip for tip in flat["tips"])


def test_ranking_hides_styles_of_few_companies(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(n: int) -> Any:
        return lambda _s: _examples(n)

    monkeypatch.setattr(cw, "build", fake(2))
    assert cw.ranking(None) == []  # type: ignore[arg-type]
    monkeypatch.setattr(cw, "build", fake(3))
    groups = {g["group"]: g for g in cw.ranking(None)}  # type: ignore[arg-type]
    assert groups["cierre"]["items"][0]["value"] == "reunion"
