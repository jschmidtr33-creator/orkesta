"""La poda del vocabulario conserva exactamente el significado de las frases en español."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("wordfreq")
ort = pytest.importorskip("onnxruntime")
from onnx import TensorProto, helper, numpy_helper  # noqa: E402
from tokenizers import Tokenizer, decoders, models, pre_tokenizers, processors  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "podar_modelo.py"


def _tiny(folder: Path) -> None:
    vocab = [("<s>", 0.0), ("<pad>", 0.0), ("</s>", 0.0), ("<unk>", 0.0)]
    pieces = ["▁", "▁me", "▁dijo", "▁que", "▁es", "▁muy", "▁caro", "a", "c", "o", "s", "▁北京"]
    pieces += [f"▁zzz{i}" for i in range(40)]
    vocab += [(p, -float(i + 1)) for i, p in enumerate(pieces)]
    tok = Tokenizer(models.Unigram(vocab, unk_id=3))
    tok.pre_tokenizer = pre_tokenizers.Metaspace()
    tok.decoder = decoders.Metaspace()
    tok.post_processor = processors.RobertaProcessing(("</s>", 2), ("<s>", 0))
    tok.add_special_tokens(["<s>", "<pad>", "</s>", "<unk>"])
    tok.save(str(folder / "tokenizer.json"))
    w = np.random.RandomState(0).randint(0, 255, (len(vocab), 8)).astype(np.uint8)
    graph = helper.make_graph(
        [
            helper.make_node("DequantizeLinear", ["W", "sc", "zp"], ["Wf"]),
            helper.make_node("Gather", ["Wf", "input_ids"], ["out"]),
        ],
        "g",
        [helper.make_tensor_value_info("input_ids", TensorProto.INT64, [None, None])],
        [helper.make_tensor_value_info("out", TensorProto.FLOAT, [None, None, 8])],
        [
            numpy_helper.from_array(w, "W"),
            numpy_helper.from_array(np.array(0.01, dtype=np.float32), "sc"),
            numpy_helper.from_array(np.array(128, dtype=np.uint8), "zp"),
        ],
    )
    onnx.save(
        helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)]),
        str(folder / "model.onnx"),
    )


def _run(folder: Path, text: str) -> tuple[list[str], np.ndarray]:
    tok = Tokenizer.from_file(str(folder / "tokenizer.json"))
    enc = tok.encode(text)
    out = ort.InferenceSession(str(folder / "model.onnx")).run(
        None, {"input_ids": np.array([enc.ids])}
    )[0]
    return enc.tokens, out


def test_poda_conserva_frases_y_achica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tiny(tmp_path)
    before = _run(tmp_path, "me dijo que es muy caro")
    spec = importlib.util.spec_from_file_location("podar_modelo", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("sys.argv", ["podar", str(tmp_path)])
    mod.main()
    after = _run(tmp_path, "me dijo que es muy caro")
    assert before[0] == after[0]
    assert np.allclose(before[1], after[1])
    vocab = json.loads((tmp_path / "tokenizer.json").read_text())["model"]["vocab"]
    assert len(vocab) < 30 and not any(p.startswith("▁zzz") for p, _ in vocab)


def test_mezcla_modelo_y_conceptos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con el modelo instalado, el significado suma a los conceptos para elegir la forma."""
    from orkesta.concertino import embed, semantic

    pairs = semantic.index().pairs
    target = next(i for i, (_, c) in enumerate(pairs) if c == "quiere un descuento para firmar")
    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(
        embed,
        "similarities",
        lambda q, texts: [0.95 if i == target else 0.1 for i in range(len(texts))],
    )
    found = semantic.rewrite("me está regateando feo", None)
    assert found is not None and found.canonical == "quiere un descuento para firmar"
