"""IA propia, paso 2: cargos → área y nivel con un modelo que aprende.

- Datos: los cargos del índice propio (etiquetados por las reglas), un conjunto curado a mano
  (`data/titles_seed.py`) y las correcciones que hace la administración en el panel.
- En producción manda primero la regla; el modelo solo completa lo que la regla no reconoce y
  solo si está seguro. Nunca aprende de sus propias predicciones (se entrena con las reglas
  puras, no con lo guardado en el índice).
- Al activarse una versión, se re-etiquetan los cargos del índice para que la búsqueda por área
  los encuentre.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Any, cast

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.brain import ml
from orkesta.brain.data import titles_seed
from orkesta.brain.models import BrainFeedback, BrainModel
from orkesta.engine.taxonomy import titles as rules

logger = logging.getLogger(__name__)
NONE = ml.Lexicon.NONE
AREA, LEVEL = "cargos", "cargos_nivel"
DIM = {AREA: 1, LEVEL: 0}
SEED_WEIGHT = 5
THRESHOLD = 0.8  # así acertó el 76 % al completar cargos difíciles reales (medición honesta)
GENERIC = frozenset(
    [
        "director",
        "directora",
        "gerente",
        "gerenta",
        "jefe",
        "jefa",
        "socio",
        "socia",
        "ceo",
        "presidente",
        "presidenta",
        "vice",
        "president",
        "vp",
        "owner",
        "fundador",
        "fundadora",
        "founder",
        "manager",
        "general",
        "coordinador",
        "coordinadora",
        "subgerente",
        "de",
        "la",
        "del",
        "y",
        "executive",
        "ejecutivo",
        "ejecutiva",
        "managing",
        "partner",
        "cofundador",
        "co",
        "dueno",
        "duena",
        "propietario",
        "propietaria",
        "head",
        "chief",
        "officer",
        "senior",
        "sr",
        "asociado",
        "asociada",
        "lider",
        "leader",
        "e",
        "and",
        "the",
        "of",
        "en",
        # Cargos de nivel sin área propia.
        "supervisor",
        "supervisora",
        "ingeniero",
        "ingeniera",
        "analista",
        "consultor",
        "consultora",
        "especialista",
        "asesor",
        "asesora",
        "profesional",
        "tecnico",
        "tecnica",
        "asistente",
        "auxiliar",
        "experto",
        "experta",
        "gestor",
        "gestora",
        "encargado",
        "encargada",
    ]
)


def _rule(title: str, task: str) -> str | None:
    return rules.classify_rules(title)[DIM[task]]


def _index_titles(session: Session) -> Counter[str]:
    from orkesta.research.models import PersonIndex

    titles: Counter[str] = Counter()
    for (title,) in session.execute(select(PersonIndex.title).where(PersonIndex.title != "")):
        titles[title.strip()] += 1
    return titles


def _feedback(session: Session, task: str) -> dict[str, tuple[str, str]]:
    """Correcciones humanas: cargo plegado → (cargo, etiqueta). La más reciente manda."""
    rows = session.execute(
        select(BrainFeedback.text, BrainFeedback.label)
        .where(BrainFeedback.task == task).order_by(BrainFeedback.created_at)
    ).all()  # fmt: skip
    return {ml.fold(text): (text, label) for text, label in rows if text}


def build(session: Session, task: str) -> list[ml.Example]:
    human = _feedback(session, task)
    examples = []
    for title in _index_titles(session):
        key = ml.fold(title)
        if not key or key in human:
            continue
        label = _rule(title, task)
        if label is None and task == AREA and not set(key.split()) - GENERIC:
            label = NONE  # solo palabras genéricas: no tiene área
        if label:
            examples.append(ml.Example(f"w:{key}", ml.word_features(title), label, title))
    curated = [(t, level if task == LEVEL else area) for t, level, area in titles_seed.examples()]
    curated += [(text, label) for text, label in human.values()]
    for i, (title, label) in enumerate(curated):
        prefix = "f" if i >= len(curated) - len(human) else "s"
        x = ml.word_features(title)
        examples += [ml.Example(f"{prefix}:{ml.fold(title)}", x, label, title)] * SEED_WEIGHT
    return examples


def _predictor(task: str) -> Any:
    def predictor(model: Any, threshold: float, ex: ml.Example) -> tuple[str | None, float]:
        """Como decide ORKESTA en producción: regla primero; el modelo, solo si está seguro."""
        rule = _rule(str(ex.raw), task)
        if rule:
            return rule, 1.0
        label, confidence = ml.predict(model, ex.x)
        return (label if confidence >= threshold and label != NONE else NONE), confidence

    return predictor


def _baseline(task: str) -> Any:
    def baseline(session: Session, test: list[ml.Example]) -> ml.Metrics:
        return ml.score([(ex.y, _rule(str(ex.raw), task) or NONE, 1.0) for ex in test])

    return baseline


def _decide(model: Any, threshold: float, title: str) -> tuple[str | None, float]:
    label, confidence = ml.predict(model, ml.word_features(title))
    return (label if confidence >= threshold and label != NONE else None), confidence


def _extras(task: str) -> Any:
    def extras(session: Session, model: Any, threshold: float) -> dict[str, Any]:
        """Cobertura en todo el índice: solo reglas vs. reglas + modelo."""
        titles = _index_titles(session)
        total = sum(titles.values())
        by_rules = by_both = 0
        for title, n in titles.items():
            if _rule(title, task):
                by_rules += n
                by_both += n
            elif _decide(model, threshold, title)[0]:
                by_both += n
        return {
            "titles": total,
            "coverage_rules": round(by_rules / total, 4) if total else 0.0,
            "coverage_model": round(by_both / total, 4) if total else 0.0,
        }

    return extras


def _on_activate(task: str) -> Any:
    def on_activate(session: Session, model: Any, threshold: float) -> None:
        LIVE.set(task, model, threshold)
        relabel(session, task, model, threshold)

    return on_activate


def relabel(session: Session, task: str, model: Any, threshold: float) -> int:
    """Re-etiqueta en el índice los cargos que las reglas no reconocen."""
    from orkesta.research.models import PersonIndex

    column = "area" if task == AREA else "level"
    changes = [
        {"t": title, "v": _decide(model, threshold, title)[0]}
        for title in _index_titles(session)
        if not _rule(title, task)
    ]
    if changes:
        table = cast(Table, PersonIndex.__table__)
        stmt = update(table).where(table.c.title == bindparam("t")).values({column: bindparam("v")})
        session.execute(stmt, changes)
    return len(changes)


# --- Modelos vivos para el clasificador de cargos de toda la plataforma ----------------------


class Live:
    """Modelos activos en memoria; el clasificador de cargos los consulta en cada uso."""

    def __init__(self) -> None:
        self.models: dict[str, tuple[Any, float, int | None]] = {}
        self.factory: sessionmaker[Session] | None = None
        self.checked = 0.0
        self.lock = threading.Lock()

    def set(self, task: str, model: Any, threshold: float, version: int | None = None) -> None:
        self.models[task] = (model, threshold, version)
        rules.classify_title.cache_clear()

    def reset(self) -> None:
        self.models.clear()
        self.checked = 0.0
        rules.classify_title.cache_clear()

    def refresh(self) -> None:
        if self.factory is None or time.monotonic() - self.checked < 300:
            return
        with self.lock:
            if time.monotonic() - self.checked < 300:
                return
            self.checked = time.monotonic()
            try:
                with self.factory() as session:
                    for task in (AREA, LEVEL):
                        row = session.execute(
                            select(BrainModel.version, BrainModel.threshold)
                            .where(BrainModel.task == task, BrainModel.active.is_(True))
                        ).first()  # fmt: skip
                        current = self.models.get(task)
                        if row is None:
                            if current:
                                self.models.pop(task, None)
                                rules.classify_title.cache_clear()
                            continue
                        if current and current[2] == row.version:
                            continue
                        query = select(BrainModel.blob).where(
                            BrainModel.task == task, BrainModel.version == row.version
                        )
                        blob = session.scalar(query)
                        if blob:
                            self.set(task, ml.loads(blob), float(row.threshold), row.version)
            except Exception:
                logger.exception("brain_titles_refresh_failed")

    def __call__(self, title: str) -> tuple[str | None, str | None]:
        self.refresh()
        out: dict[str, str | None] = {AREA: None, LEVEL: None}
        for task, (model, threshold, _) in list(self.models.items()):
            out[task] = _decide(model, threshold, title)[0]
        return out[LEVEL], out[AREA]


LIVE = Live()


def install(factory: sessionmaker[Session]) -> None:
    LIVE.factory = factory
    LIVE.checked = 0.0
    rules.set_learned(LIVE)


# --- Herramientas del panel -------------------------------------------------------------------


def explain(title: str) -> dict[str, Any]:
    """Qué dicen las reglas y el modelo sobre un cargo, con las palabras que lo justifican."""
    level, area = rules.classify_rules(title)
    out: dict[str, Any] = {"title": title, "rules": {"level": level, "area": area}}
    x = ml.word_features(title)
    for task in (AREA, LEVEL):
        entry = LIVE.models.get(task)
        if entry is None:
            out[task] = None
            continue
        model, threshold, version = entry
        label, confidence = ml.predict(model, x)
        out[task] = {
            "label": None if label == NONE else label, "confidence": round(confidence, 3),
            "sure": confidence >= threshold and label != NONE, "version": version,
            "evidence": [e[0] if e[0] == e[1] else f"{e[0]} (≈ {e[1]})"
                         for e in model.explain(x)] if hasattr(model, "explain") else [],
        }  # fmt: skip
    final_level, final_area = rules.classify_title(title)
    out["final"] = {"level": final_level, "area": final_area}
    return out


def _says_none(title: str) -> bool:
    """El modelo ya sabe que este cargo no tiene área («Docente»)."""
    entry = LIVE.models.get(AREA)
    if entry is None:
        return False
    model = entry[0]
    x = ml.word_features(title)
    return bool(model.explain(x)) and ml.predict(model, x)[0] == NONE


def doubts(session: Session, limit: int = 25) -> list[dict[str, Any]]:
    """Los cargos más frecuentes del índice que todavía quedan sin área: los que más conviene
    enseñar (aprendizaje activo)."""
    taught = set(_feedback(session, AREA))
    out = []
    for title, n in _index_titles(session).most_common():
        key = ml.fold(title)
        if key in taught or not set(key.split()) - GENERIC:
            continue
        if rules.classify_title(title)[1] is None and not _says_none(title):
            out.append({"title": title, "count": n})
            if len(out) >= limit:
                break
    return out
