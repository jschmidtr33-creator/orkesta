"""Librería propia de aprendizaje automático de ORKESTA (Python puro, sin servicios externos).

- Rasgos de texto: n-gramas de caracteres (aguantan errores de tipeo y variantes «Operacón»,
  «Logistic» / «Logística») más palabras sueltas y pares de palabras, sobre texto plegado.
- Naive Bayes multinomial (rápido, ideal con pocos datos) y regresión logística multiclase con
  descenso de gradiente y regularización (mejor con más datos o rasgos mixtos).
- Evaluación honesta: partición fija por clave (el mismo texto nunca queda en entrenamiento y en
  prueba), exactitud, F1 por clase y «cobertura con confianza»: cuántos casos se atreve a
  resolver el modelo y con qué acierto, para que dude en lugar de equivocarse.
- Todo se guarda como JSON comprimido para versionarlo en la base de datos.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import math
import random
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

Features = dict[str, float]

_NON_WORD = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    """Minúsculas, sin tildes ni signos: «Jefe de Operación» → «jefe de operacion»."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return _NON_WORD.sub(" ", plain).strip()


def text_features(
    text: str, chars: tuple[int, int] = (3, 5), words: bool = True, prefix: str = ""
) -> Features:
    """Rasgos de un texto corto (cargos, asuntos, respuestas). Pesos sublineales (1 + log)."""
    folded = fold(text)
    counts: Counter[str] = Counter()
    tokens = folded.split()
    for token in tokens:
        padded = f" {token} "
        for size in range(chars[0], chars[1] + 1):
            for i in range(max(1, len(padded) - size + 1)):
                gram = padded[i : i + size]
                if gram.strip():
                    counts[f"{prefix}c:{gram}"] += 1
    if words:
        for token in tokens:
            counts[f"{prefix}w:{token}"] += 1
        for a, b in itertools.pairwise(tokens):
            counts[f"{prefix}b:{a}_{b}"] += 1
    return {k: 1.0 + math.log(v) for k, v in counts.items()}


def merge(*parts: Features) -> Features:
    out: Features = {}
    for part in parts:
        for key, value in part.items():
            out[key] = out.get(key, 0.0) + value
    return out


class Model(Protocol):
    labels: list[str]

    def predict_proba(self, x: Features) -> dict[str, float]: ...

    def to_dict(self) -> dict[str, Any]: ...


def predict(model: Model, x: Features) -> tuple[str, float]:
    probs = model.predict_proba(x)
    label = max(probs, key=probs.__getitem__)
    return label, probs[label]


# --- Naive Bayes multinomial ----------------------------------------------------------------


class NaiveBayes:
    kind = "naive_bayes"

    def __init__(self, alpha: float = 0.3, min_count: int = 2) -> None:
        self.alpha, self.min_count = alpha, min_count
        self.labels: list[str] = []
        self.prior: dict[str, float] = {}
        self.loglik: dict[str, dict[str, float]] = {}
        self.unseen: dict[str, float] = {}
        self._vocab: set[str] = set()

    def fit(self, xs: Sequence[Features], ys: Sequence[str]) -> NaiveBayes:
        totals: dict[str, Counter[str]] = defaultdict(Counter)
        df: Counter[str] = Counter()
        for x, y in zip(xs, ys, strict=True):
            totals[y].update(x)
            df.update(x.keys())
        vocab = {f for f, c in df.items() if c >= self.min_count}
        size = max(1, len(vocab))
        self.labels = sorted(totals)
        n = len(ys)
        label_counts = Counter(ys)
        self.prior = {lab: math.log(label_counts[lab] / n) for lab in self.labels}
        self.loglik, self.unseen = {}, {}
        for lab in self.labels:
            mass = sum(v for f, v in totals[lab].items() if f in vocab)
            denom = mass + self.alpha * size
            self.unseen[lab] = math.log(self.alpha / denom)
            self.loglik[lab] = {
                f: math.log((v + self.alpha) / denom) for f, v in totals[lab].items() if f in vocab
            }
        self._vocab = vocab
        return self

    def predict_proba(self, x: Features) -> dict[str, float]:
        scores = {}
        for lab in self.labels:
            table, unseen = self.loglik[lab], self.unseen[lab]
            scores[lab] = self.prior[lab] + sum(
                v * table.get(f, unseen) for f, v in x.items() if f in self._vocab
            )
        return _softmax(scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "alpha": self.alpha, "min_count": self.min_count,
            "labels": self.labels, "prior": self.prior, "unseen": self.unseen,
            "loglik": {lab: {f: round(v, 5) for f, v in t.items()}
                       for lab, t in self.loglik.items()},
        }  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NaiveBayes:
        model = cls(data["alpha"], data["min_count"])
        model.labels, model.prior, model.unseen = data["labels"], data["prior"], data["unseen"]
        model.loglik = data["loglik"]
        model._vocab = set().union(*(t.keys() for t in model.loglik.values()))
        return model


# --- Regresión logística multiclase ---------------------------------------------------------


class LogisticRegression:
    kind = "logistic"

    def __init__(
        self, epochs: int = 12, rate: float = 0.3, l2: float = 1e-4, min_count: int = 2,
        seed: int = 7,
    ) -> None:  # fmt: skip
        self.epochs, self.rate, self.l2, self.min_count, self.seed = (
            epochs,
            rate,
            l2,
            min_count,
            seed,
        )
        self.labels: list[str] = []
        self.weights: dict[str, dict[str, float]] = {}
        self.bias: dict[str, float] = {}

    def fit(self, xs: Sequence[Features], ys: Sequence[str]) -> LogisticRegression:
        df: Counter[str] = Counter()
        for x in xs:
            df.update(x.keys())
        vocab = {f for f, c in df.items() if c >= self.min_count}
        self.labels = sorted(set(ys))
        self.weights = {lab: {} for lab in self.labels}
        self.bias = dict.fromkeys(self.labels, 0.0)
        data = [
            ({f: v for f, v in x.items() if f in vocab}, y) for x, y in zip(xs, ys, strict=True)
        ]
        rng = random.Random(self.seed)  # noqa: S311  (barajar datos, no criptografía)
        decay = 1.0 - self.l2
        for epoch in range(self.epochs):
            rng.shuffle(data)
            rate = self.rate / (1 + epoch * 0.5)
            for x, y in data:
                probs = self._probs(x)
                for lab in self.labels:
                    grad = probs[lab] - (1.0 if lab == y else 0.0)
                    if abs(grad) < 1e-4:
                        continue
                    weights = self.weights[lab]
                    for f, v in x.items():
                        weights[f] = weights.get(f, 0.0) * decay - rate * grad * v
                    self.bias[lab] -= rate * grad
        # Poda: pesos casi nulos no aportan y agrandan el modelo.
        self.weights = {
            lab: {f: w for f, w in ws.items() if abs(w) >= 1e-3} for lab, ws in self.weights.items()
        }
        return self

    def _probs(self, x: Features) -> dict[str, float]:
        scores = {
            lab: self.bias[lab] + sum(v * self.weights[lab].get(f, 0.0) for f, v in x.items())
            for lab in self.labels
        }
        return _softmax(scores)

    def predict_proba(self, x: Features) -> dict[str, float]:
        return self._probs(x)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "labels": self.labels, "bias": self.bias,
            "params": [self.epochs, self.rate, self.l2, self.min_count, self.seed],
            "weights": {lab: {f: round(w, 4) for f, w in ws.items()}
                        for lab, ws in self.weights.items()},
        }  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogisticRegression:
        model = cls(*data["params"])
        model.labels, model.bias, model.weights = data["labels"], data["bias"], data["weights"]
        return model


# --- Léxico aprendido (palabras con evidencia, explicable) -----------------------------------

_STOP = frozenset((
    "de", "del", "la", "las", "los", "el", "y", "e", "en", "para", "a", "al", "of", "the", "and",
    "for", "in", "con", "por", "un", "una", "sr", "senior", "jr",
))  # fmt: skip


def word_features(text: str) -> Features:
    """Palabras y pares de palabras (sin conectores ni números) de un texto corto."""
    tokens = [t for t in fold(text).split() if t not in _STOP and not t.isdigit() and len(t) > 1]
    feats = dict.fromkeys(tokens, 1.0)
    feats.update({f"{a}_{b}": 1.0 for a, b in itertools.pairwise(tokens)})
    return feats


class Lexicon:
    """Aprende qué palabras indican cada etiqueta y decide solo con evidencia.

    Una palabra cuenta si aparece al menos `min_support` veces y casi siempre con la misma
    etiqueta (`purity`). Las palabras genéricas («gerente», «senior») no cuentan. Una palabra
    nunca vista se compara con las conocidas para tolerar errores de tipeo («Cibersecurity»).
    Puede explicar su decisión: devuelve las palabras que la justificaron.
    """

    kind = "lexicon"
    NONE = "_ninguna"

    def __init__(self, min_support: int = 2, purity: float = 0.66) -> None:
        self.min_support, self.purity = min_support, purity
        self.labels: list[str] = []
        self.table: dict[str, tuple[str, float, int]] = {}
        self.known: set[str] = set()
        self._deletes: dict[str, set[str]] = {}

    def fit(self, xs: Sequence[Features], ys: Sequence[str]) -> Lexicon:
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for x, y in zip(xs, ys, strict=True):
            for token in x:
                counts[token][y] += 1
        self.labels = sorted(set(ys) | {self.NONE})
        self.table = {}
        for token, c in counts.items():
            n = sum(c.values())
            if n < self.min_support:
                continue
            label, top = c.most_common(1)[0]
            p = top / (n + 1)
            if p >= self.purity:
                self.table[token] = (label, round(p, 4), n)
        self.known = {t for t, c in counts.items() if sum(c.values()) >= 2}
        self._index()
        return self

    def _index(self) -> None:
        deletes: dict[str, set[str]] = defaultdict(set)
        for token, (_, _, n) in self.table.items():
            if "_" in token or len(token) < 5 or n < 3:
                continue
            for i in range(len(token)):
                deletes[token[:i] + token[i + 1 :]].add(token)
        self._deletes = dict(deletes)

    def _lookup(self, token: str) -> tuple[str, str, float, int] | None:
        """(palabra que decide, etiqueta, pureza, apoyo) o None."""
        if token in self.table:
            label, p, n = self.table[token]
            return token, label, p, n
        if "_" in token or len(token) < 5 or token in self.known:
            return None
        cands = set(self._deletes.get(token, ()))
        for i in range(len(token)):
            shorter = token[:i] + token[i + 1 :]
            if shorter in self.table:
                cands.add(shorter)
            cands |= self._deletes.get(shorter, set())
        cands = {c for c in cands if c in self.table and self.table[c][2] >= 3}
        if not cands:
            return None
        best = max(cands, key=lambda c: self.table[c][2])
        label, p, n = self.table[best]
        return best, label, p * 0.9, n  # una corrección de tipeo pesa un poco menos

    def explain(self, x: Features) -> list[tuple[str, str, str, float, int]]:
        """(palabra del cargo, palabra conocida, etiqueta, pureza, apoyo) de cada evidencia."""
        found = []
        for token in x:
            hit = self._lookup(token)
            if hit:
                found.append((token, *hit))
        pairs = [e for e in found if "_" in e[0]]
        if pairs:  # si un par de palabras contradice a sus palabras sueltas, manda el par
            covered = {w: e[2] for e in pairs for w in e[0].split("_")}
            found = pairs + [
                e for e in found if "_" not in e[0] and covered.get(e[0], e[2]) == e[2]
            ]
        return found

    def predict_proba(self, x: Features) -> dict[str, float]:
        evidence = self.explain(x)
        if not evidence:
            return {self.NONE: 1.0}
        score: dict[str, float] = defaultdict(float)
        for _, _, label, p, n in evidence:
            score[label] += math.log(p / (1 - p + 1e-9)) * min(1.0, math.log(1 + n) / math.log(20))
        label, top = max(score.items(), key=lambda kv: kv[1])
        share = math.exp(top) / sum(math.exp(v) for v in score.values())
        confidence = share * (1 - 1 / (1 + math.exp(top)))
        if label == self.NONE:
            return {self.NONE: 1.0}
        return {label: confidence, self.NONE: 1 - confidence}

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": [self.min_support, self.purity], "labels": self.labels,
                "table": self.table, "known": sorted(self.known)}  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lexicon:
        model = cls(*data["params"])
        model.labels = data["labels"]
        model.table = {k: (v[0], float(v[1]), int(v[2])) for k, v in data["table"].items()}
        model.known = set(data["known"])
        model._index()
        return model


# --- Ensamble: dos modelos distintos que deben coincidir --------------------------------------


class Ensemble:
    """Promedia varios modelos y sabe cuándo discrepan (señal honesta de duda)."""

    kind = "ensemble"

    def __init__(self, members: list[Any] | None = None) -> None:
        self.members: list[Any] = members or []
        self.labels: list[str] = []

    def fit(self, xs: Sequence[Features], ys: Sequence[str]) -> Ensemble:
        for member in self.members:
            member.fit(xs, ys)
        self.labels = sorted(set(ys))
        return self

    def votes(self, x: Features) -> list[tuple[str, float]]:
        return [predict(m, x) for m in self.members]

    def agree(self, x: Features) -> bool:
        return len({label for label, _ in self.votes(x)}) == 1

    def predict_proba(self, x: Features) -> dict[str, float]:
        total: dict[str, float] = defaultdict(float)
        for member in self.members:
            for label, p in member.predict_proba(x).items():
                total[label] += p / len(self.members)
        return dict(total)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "labels": self.labels,
                "members": [m.to_dict() for m in self.members]}  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ensemble:
        model = cls([KINDS[m["kind"]](m) for m in data["members"]])
        model.labels = data["labels"]
        return model


def _softmax(scores: dict[str, float]) -> dict[str, float]:
    top = max(scores.values())
    exps = {k: math.exp(v - top) for k, v in scores.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


KINDS: dict[str, Callable[[dict[str, Any]], Model]] = {
    NaiveBayes.kind: NaiveBayes.from_dict,
    LogisticRegression.kind: LogisticRegression.from_dict,
    Lexicon.kind: Lexicon.from_dict,
    Ensemble.kind: Ensemble.from_dict,
}


def dumps(model: Model) -> bytes:
    return gzip.compress(json.dumps(model.to_dict(), separators=(",", ":")).encode(), 6)


def loads(blob: bytes) -> Model:
    data = json.loads(gzip.decompress(blob))
    return KINDS[data["kind"]](data)


# --- Datos y evaluación ---------------------------------------------------------------------


@dataclass
class Example:
    key: str  # agrupa duplicados: el mismo texto siempre cae del mismo lado de la partición
    x: Features
    y: str
    raw: Any = None


@dataclass
class Metrics:
    n: int
    accuracy: float
    macro_f1: float
    per_label: dict[str, dict[str, float]]
    confident: dict[str, float] = field(default_factory=dict)
    confusions: list[tuple[str, str, int]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n, "accuracy": round(self.accuracy, 4), "macro_f1": round(self.macro_f1, 4),
            "per_label": self.per_label, "confident": self.confident,
            "confusions": [list(c) for c in self.confusions],
        }  # fmt: skip


def in_test(key: str, ratio: float = 0.2, salt: str = "orkesta") -> bool:
    digest = hashlib.blake2b(f"{salt}:{key}".encode(), digest_size=8).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < ratio


def split(examples: Iterable[Example], ratio: float = 0.2) -> tuple[list[Example], list[Example]]:
    train: list[Example] = []
    test: list[Example] = []
    for ex in examples:
        (test if in_test(ex.key, ratio) else train).append(ex)
    return train, test


def score(pairs: Sequence[tuple[str, str | None, float]], threshold: float = 0.6) -> Metrics:
    """pairs = (real, predicho o None si no responde, confianza). None cuenta como error."""
    n = len(pairs)
    labels = sorted({p[0] for p in pairs} | {p[1] for p in pairs if p[1]})
    hits = sum(1 for real, pred, _ in pairs if real == pred)
    per_label: dict[str, dict[str, float]] = {}
    f1s = []
    for lab in labels:
        tp = sum(1 for r, p, _ in pairs if r == lab and p == lab)
        fp = sum(1 for r, p, _ in pairs if r != lab and p == lab)
        fn = sum(1 for r, p, _ in pairs if r == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        support = tp + fn
        if support:
            f1s.append(f1)
        per_label[lab] = {"precision": round(prec, 3), "recall": round(rec, 3),
                          "f1": round(f1, 3), "support": support}  # fmt: skip
    sure = [(r, p) for r, p, conf in pairs if p is not None and conf >= threshold]
    confident = {
        "threshold": threshold,
        "coverage": round(len(sure) / n, 4) if n else 0.0,
        "accuracy": round(sum(1 for r, p in sure if r == p) / len(sure), 4) if sure else 0.0,
    }
    confusions = Counter((r, p) for r, p, _ in pairs if p and r != p).most_common(8)
    return Metrics(
        n=n, accuracy=hits / n if n else 0.0, macro_f1=sum(f1s) / len(f1s) if f1s else 0.0,
        per_label=per_label, confident=confident,
        confusions=[(r, p, c) for (r, p), c in confusions],
    )  # fmt: skip


def evaluate(model: Model, test: Sequence[Example], threshold: float = 0.6) -> Metrics:
    return score([(ex.y, *predict(model, ex.x)) for ex in test], threshold)


def best_threshold(model: Model, test: Sequence[Example], target: float = 0.95) -> float:
    """Confianza mínima con la que el modelo acierta al menos `target` de las veces."""
    preds = sorted(((predict(model, ex.x), ex.y) for ex in test), key=lambda p: -p[0][1])
    best, hits = 0.99, 0
    for i, ((label, conf), real) in enumerate(preds, 1):
        hits += label == real
        if hits / i >= target:
            best = conf
    return round(min(max(best, 0.3), 0.99), 3)
