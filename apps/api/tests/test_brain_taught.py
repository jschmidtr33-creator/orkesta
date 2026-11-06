"""Lo enseñado a mano manda: sumar bases (etiquetadas por reglas) no borra lo aprendido."""

from __future__ import annotations

from orkesta.brain import ml


def test_taught_examples_are_not_diluted_by_automatic_ones() -> None:
    taught = [("Gerente de Experiencia de Cliente", "servicio")] * 5
    automatic = [("Experiencia de Usuario UX", "tecnologia")] * 40 + [
        ("Product Owner Digital", "tecnologia")] * 10  # fmt: skip
    xs = [ml.word_features(t) for t, _ in taught + automatic]
    ys = [y for _, y in taught + automatic]
    flags = [True] * len(taught) + [False] * len(automatic)

    plain = ml.Lexicon(2, 0.66).fit(xs, ys)
    tiered = ml.Lexicon(2, 0.66).fit(xs, ys, flags)
    title = ml.word_features("Directora de Experiencia")
    # Sin prioridad, 40 cargos automáticos tapan lo enseñado sobre «experiencia».
    assert ml.predict(plain, title)[0] != "servicio"
    assert ml.predict(tiered, title)[0] == "servicio"
    # Y lo que nadie enseñó lo siguen aportando las bases.
    assert ml.predict(tiered, ml.word_features("Product Owner"))[0] == "tecnologia"
