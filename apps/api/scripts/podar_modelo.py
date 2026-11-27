"""Poda el vocabulario del modelo de comprensión de Concertino al español (y algo de inglés).

El modelo multilingüe trae 250.000 piezas de vocabulario de 50+ idiomas; al cargarse, su tabla de
vectores de palabras ocupa ~380 MB de RAM. Concertino solo necesita español (y palabras de ventas
en inglés: pipeline, demo, CRM), así que se quedan las piezas que de verdad se usan: las de las
60.000 palabras más comunes del español, 15.000 del inglés, los textos de Concertino y todas las
piezas cortas en alfabeto latino (para armar cualquier palabra rara). El significado no cambia:
cada pieza conserva exactamente su mismo vector.

    python scripts/podar_modelo.py <carpeta con model.onnx y tokenizer.json> [textos...]
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper
from tokenizers import Tokenizer

SHORT = 3  # piezas latinas de hasta 3 letras: siempre se quedan


def _latin(piece: str) -> bool:
    text = piece.replace("▁", "")
    if not text:
        return True
    for ch in text:
        if ch.isascii():
            continue
        name = unicodedata.name(ch, "")
        if "LATIN" not in name and unicodedata.category(ch)[0] not in "NP":
            return False
    return True


def _words(extra: list[Path]) -> list[str]:
    from wordfreq import top_n_list

    words = top_n_list("es", 150000) + top_n_list("en", 30000)
    words += [w.capitalize() for w in words[:30000]] + [w.upper() for w in words[:2000]]
    # Signos pegados a la palabra, como se escriben las preguntas («¿cómo», «precio?»)
    words += [
        v for w in words[:8000] for v in (f"¿{w}", f"{w}?", f"{w},", f"{w}.", f"¡{w}", f"{w}!")
    ]
    # Cada palabra sola: pegada a una comilla o paréntesis se partiría distinto
    for path in extra:
        files = path.rglob("*.py") if path.is_dir() else [path]
        for f in files:
            words += re.findall(r"\w+|[^\w\s]", f.read_text(encoding="utf-8", errors="ignore"))
    return list(dict.fromkeys(words))


def keep_ids(tok_json: dict, tok: Tokenizer, texts: list[str]) -> list[int]:
    vocab = tok_json["model"]["vocab"]
    keep = {
        i
        for i, (piece, _) in enumerate(vocab)
        if len(piece.replace("▁", "")) <= SHORT and _latin(piece)
    }
    keep |= {t["id"] for t in tok_json.get("added_tokens", [])}
    keep.add(tok_json["model"].get("unk_id") or 0)
    for i in range(0, len(texts), 2000):
        for enc in tok.encode_batch(texts[i : i + 2000], add_special_tokens=False):
            keep.update(enc.ids)
    return sorted(keep)


def prune_tokenizer(tok_json: dict, keep: list[int]) -> dict:
    remap = {old: new for new, old in enumerate(keep)}
    out = json.loads(json.dumps(tok_json))
    vocab = tok_json["model"]["vocab"]
    out["model"]["vocab"] = [vocab[i] for i in keep]
    if out["model"].get("unk_id") is not None:
        out["model"]["unk_id"] = remap[out["model"]["unk_id"]]
    for t in out.get("added_tokens", []):
        t["id"] = remap[t["id"]]

    def fix(node: object) -> None:  # ids de tokens especiales en el post-procesador
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "ids" and isinstance(value, list):
                    node[key] = [remap[v] for v in value]
                elif key in ("sep", "cls") and isinstance(value, list) and len(value) == 2:
                    node[key] = [value[0], remap[value[1]]]
                else:
                    fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)

    fix(out.get("post_processor"))
    return out


def _tensors(model: onnx.ModelProto) -> list[onnx.TensorProto]:
    """Inicializadores y constantes del grafo (la tabla puede venir de cualquiera de los dos)."""
    found = list(model.graph.initializer)
    for node in model.graph.node:
        if node.op_type == "Constant":
            found += [a.t for a in node.attribute if a.name == "value"]
    return found


def prune_onnx(model: onnx.ModelProto, keep: list[int], vocab_size: int) -> str:
    """Recorta las filas de la tabla de vectores de palabras (directa o cuantizada)."""
    tensors = _tensors(model)
    table = [t for t in tensors if len(t.dims) == 2 and vocab_size <= t.dims[0] <= vocab_size + 512]
    if not table:
        big = [
            (t.name, list(t.dims), t.data_type) for t in tensors if t.dims and max(t.dims) > 50000
        ]
        raise SystemExit(f"No encontré la tabla de vectores de palabras. Tensores grandes: {big}")
    rows = np.array(keep, dtype=np.int64)
    done = []
    for t in table:
        n = t.dims[0]
        arr = numpy_helper.to_array(t)
        t.CopyFrom(numpy_helper.from_array(arr[rows], t.name))
        done.append(f"{t.name}: {arr.shape} {arr.dtype} → {(len(keep), arr.shape[1])}")
        # Escala o punto cero por fila (cuantización por eje 0)
        for p in tensors:
            if len(p.dims) == 1 and p.dims[0] == n:
                v = numpy_helper.to_array(p)
                p.CopyFrom(numpy_helper.from_array(v[rows], p.name))
                done.append(f"{p.name}: por fila")
    return "; ".join(done)


def main() -> None:
    folder = Path(sys.argv[1])
    extra = [Path(p) for p in sys.argv[2:]]
    tok_path, model_path = folder / "tokenizer.json", folder / "model.onnx"
    tok_json = json.loads(tok_path.read_text(encoding="utf-8"))
    tok = Tokenizer.from_file(str(tok_path))
    vocab_size = len(tok_json["model"]["vocab"])
    texts = _words(extra)
    keep = keep_ids(tok_json, tok, texts)
    print(f"Vocabulario: {vocab_size} → {len(keep)} piezas")
    model = onnx.load(str(model_path))
    print("Tabla:", prune_onnx(model, keep, vocab_size))
    # Comprobación: las palabras del corpus se parten igual que con el vocabulario completo
    pruned = Tokenizer.from_str(json.dumps(prune_tokenizer(tok_json, keep), ensure_ascii=False))
    sample = texts[:: max(1, len(texts) // 20000)]
    a = [e.tokens for e in tok.encode_batch(sample, add_special_tokens=False)]
    b = [e.tokens for e in pruned.encode_batch(sample, add_special_tokens=False)]
    changed = sum(x != y for x, y in zip(a, b, strict=True))
    print(f"Palabras que cambian al podar: {changed}/{len(sample)}")
    onnx.save(model, str(model_path))
    tok_path.write_text(
        json.dumps(prune_tokenizer(tok_json, keep), ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"model.onnx {model_path.stat().st_size / 1e6:.1f} MB · "
        f"tokenizer.json {tok_path.stat().st_size / 1e6:.1f} MB"
    )


if __name__ == "__main__":
    main()
