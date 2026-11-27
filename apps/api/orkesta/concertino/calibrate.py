# ruff: noqa: E501
"""Calibración del modelo de comprensión de Concertino.

Cada frase del banco se compara contra todas las DEMÁS (sin verse a sí misma): si la más parecida
pertenece a su misma forma canónica, el modelo entendió. Muestra el acierto y la similitud de los
aciertos y de los errores, para fijar los umbrales. También muestra cómo entiende frases nuevas.

    CONCERTINO_MODEL_DIR=/ruta/al/modelo python -m orkesta.concertino.calibrate [frases.txt]
"""

from __future__ import annotations

import sys
import time

from orkesta.concertino import embed, semantic


def _quantiles(values: list[float]) -> str:
    if not values:
        return "—"
    v = sorted(values)
    pick = [v[int(q * (len(v) - 1))] for q in (0.1, 0.25, 0.5, 0.75, 0.9)]
    return " ".join(f"{x:.2f}" for x in pick)


def main() -> None:
    import resource

    pairs = semantic.index().pairs
    texts = tuple(t for t, _ in pairs)
    base_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"Memoria antes del modelo: {base_rss:.0f} MB")
    t0 = time.time()
    m = embed.model()
    if m is None:
        print("Sin modelo: revisa CONCERTINO_MODEL_DIR")
        sys.exit(1)
    bank = embed.vectors(texts)
    print(f"Modelo cargado y banco de {len(texts)} frases en {time.time() - t0:.1f} s")
    sims = bank @ bank.T
    for i in range(len(texts)):
        sims[i, i] = -1.0
    good: list[float] = []
    bad: list[float] = []
    tf_ok = 0
    weights = (0.3, 0.4, 0.5, 0.6, 0.7)
    mix_ok = dict.fromkeys(weights, 0)
    mix_good: dict[float, list[float]] = {w: [] for w in weights}
    mix_bad: dict[float, list[float]] = {w: [] for w in weights}

    for i, (text, canonical) in enumerate(pairs):
        j = int(sims[i].argmax())
        (good if pairs[j][1] == canonical else bad).append(float(sims[i, j]))
        # Referencia: la similitud por conceptos (sin el propio texto en el banco)
        others = semantic.Index([p for k, p in enumerate(pairs) if k != i])
        tm = others.best(text)
        tf_ok += int(tm is not None and tm.canonical == canonical)
        # Mezcla: significado (modelo) + conceptos, frase por frase
        cs = others.scores(text)
        cs.insert(i, -1.0)
        for w in weights:
            mix = [w * float(sims[i, k]) + (1 - w) * cs[k] for k in range(len(pairs))]
            mix[i] = -9.0
            j2 = max(range(len(mix)), key=mix.__getitem__)
            ok = pairs[j2][1] == canonical
            mix_ok[w] += int(ok)
            (mix_good if ok else mix_bad)[w].append(mix[j2])
    total = len(pairs)
    print(f"Modelo:    {len(good) / total:.0%} de aciertos ({len(good)}/{total})")
    print(f"Conceptos: {tf_ok / total:.0%} de aciertos")
    print("Similitud de aciertos (p10 p25 p50 p75 p90):", _quantiles(good))
    print("Similitud de errores  (p10 p25 p50 p75 p90):", _quantiles(bad))
    for w in weights:
        print(
            f"Mezcla {w:.1f}: {mix_ok[w] / total:.0%} | aciertos {_quantiles(mix_good[w])} | errores {_quantiles(mix_bad[w])}"
        )

    if len(sys.argv) > 1:
        print("\nFrases nuevas:")
        with open(sys.argv[1], encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for line in lines:
            q = line.strip()
            if not q:
                continue
            hit = embed.nearest(semantic.normalize(q), texts)
            tm = semantic.index().best(q)
            if hit is not None:
                i, s = hit
                print(
                    f"{s:.2f} {q[:55]:55} → {pairs[i][1][:45]}  | conceptos {tm.score if tm else 0:.2f} → {tm.canonical[:35] if tm else '—'}"
                )
    t1 = time.time()
    for _ in range(20):
        m.encode(["me dijo que lo va a pensar y que me avisa"])
    print(f"\nTiempo por pregunta: {(time.time() - t1) / 20 * 1000:.0f} ms")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"Memoria máxima del proceso: {peak:.0f} MB (el modelo suma {peak - base_rss:.0f} MB)")


if __name__ == "__main__":
    main()
