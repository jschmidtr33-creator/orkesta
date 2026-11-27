"""Modelo de comprensión de lenguaje propio de Concertino (corre en el servidor de ORKESTA).

Un modelo pequeño de código abierto (paraphrase-multilingual-MiniLM-L12-v2, cuantizado a 8 bits
y con el vocabulario podado al español, ver scripts/podar_modelo.py) convierte cada frase en un
vector que representa su SIGNIFICADO: «me está regateando» y «quiere un descuento» quedan cerca
aunque no compartan palabras. Sin servicios externos, sin costo por uso y sin enviar las
preguntas de nadie a terceros.

Si el modelo no está instalado (desarrollo local, pruebas), Concertino sigue con la similitud por
conceptos de semantic.py: nada se rompe.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_DIR = Path(os.environ.get("CONCERTINO_MODEL_DIR", "/opt/models/concertino"))
MAX_TOKENS = 64  # las preguntas son cortas: menos tokens, menos memoria y más velocidad

_LOCK = threading.Lock()
_MODEL: _Model | None = None
_FAILED = False
_CACHE: dict[str, Any] = {}


class _Model:
    def __init__(self, folder: Path) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1  # no competir con la app por la CPU
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False  # memoria justa, sin reservas grandes
        self.session = ort.InferenceSession(str(folder / "model.onnx"), options,
                                            providers=["CPUExecutionProvider"])  # fmt: skip
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tok = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MAX_TOKENS)
        self.tok.enable_padding(length=None)

    def encode(self, texts: list[str]) -> Any:
        np = self.np
        batch = self.tok.encode_batch(texts)
        ids = np.array([e.ids for e in batch], dtype=np.int64)
        mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feed)[0]  # (n, tokens, 384)
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / np.clip(norms, 1e-9, None)


def model(folder: Path | None = None) -> _Model | None:
    """El modelo cargado (una sola vez por proceso), o None si no está instalado."""
    global _MODEL, _FAILED
    if _MODEL is not None or _FAILED:
        return _MODEL
    with _LOCK:
        if _MODEL is not None or _FAILED:
            return _MODEL
        where = folder or DEFAULT_DIR
        if not (where / "model.onnx").is_file() or not (where / "tokenizer.json").is_file():
            _FAILED = True
            return None
        try:
            _MODEL = _Model(where)
            log.info("Concertino: modelo de comprensión cargado desde %s", where)
        except Exception:  # pragma: no cover - sin modelo, Concertino sigue con reglas
            log.exception("Concertino: no se pudo cargar el modelo de comprensión")
            _FAILED = True
    return _MODEL


def available() -> bool:
    return model() is not None


def vectors(texts: tuple[str, ...]) -> Any:
    """Vectores de un conjunto fijo de frases (el banco), calculados una vez y guardados."""
    m = model()
    if m is None:
        return None
    key = str(hash(texts))
    if key not in _CACHE:
        # Una por una, igual que las preguntas: el modelo cuantizado ajusta su escala a todo el
        # lote, así que agruparlas cambiaría ligeramente cada vector según sus vecinas.
        chunks = [m.encode([t]) for t in texts]
        _CACHE[key] = m.np.vstack(chunks) if chunks else None
    return _CACHE[key]


def nearest(question: str, texts: tuple[str, ...]) -> tuple[int, float] | None:
    """(posición de la frase más parecida en significado, similitud de 0 a 1)."""
    m = model()
    if m is None or not texts:
        return None
    bank = vectors(texts)
    q = m.encode([question])[0]
    sims = bank @ q
    best = int(sims.argmax())
    return best, float(sims[best])


def similarities(question: str, texts: tuple[str, ...]) -> list[float] | None:
    """Similitud de significado de la pregunta contra cada frase del banco."""
    m = model()
    if m is None or not texts:
        return None
    sims = vectors(texts) @ m.encode([question])[0]
    return [float(x) for x in sims]


def reset() -> None:
    """Para pruebas: olvidar el modelo cargado."""
    global _MODEL, _FAILED
    with _LOCK:
        _MODEL, _FAILED = None, False
        _CACHE.clear()
