"""Registro de la IA propia: qué tareas aprende ORKESTA, cómo se entrena cada una, qué versión
está activa y cuánto mejora frente al método anterior (reglas fijas).

Regla de oro: un modelo nuevo solo reemplaza al activo si acierta al menos igual en datos que
nunca vio, y nunca peor que las reglas. Así la IA solo puede mejorar con el tiempo; cualquier
versión anterior se puede reactivar desde el panel.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.brain import ml
from orkesta.brain.models import BrainFeedback, BrainModel

logger = logging.getLogger(__name__)
NIGHTLY_FROM, NIGHTLY_UNTIL = 2, 5  # hora de Bogotá
CACHE_SECONDS = 300
TOLERANCE = 0.005


class BrainError(Exception):
    pass


@dataclass
class Task:
    name: str
    label: str
    description: str
    step: int  # paso del plan en que llega su modelo (para mostrarlo en el panel)
    build: Callable[[Session], list[ml.Example]] | None = None
    make: Callable[[], Any] = ml.NaiveBayes
    baseline: Callable[[Session, list[ml.Example]], ml.Metrics] | None = None
    status: Callable[[Session], list[dict[str, Any]]] = field(default=lambda s: [])
    min_examples: int = 200
    target: float = 0.95  # acierto mínimo para que el modelo «se atreva» a decidir solo
    method: str = "Reglas fijas"
    threshold: float | None = None  # confianza fija (si no, se calibra con `target`)
    # Qué ejemplos de la partición de prueba cuentan para medir (p. ej. solo los curados).
    test_filter: Callable[[ml.Example], bool] | None = None
    # Ejemplos enseñados a mano: mandan sobre los automáticos (el modelo debe aceptar «taught»).
    taught: Callable[[ml.Example], bool] | None = None
    # Cómo decide el sistema en producción (p. ej. reglas primero y el modelo después).
    predictor: Callable[[Any, float, ml.Example], tuple[str | None, float]] | None = None
    extras: Callable[[Session, Any, float], dict[str, Any]] | None = None
    on_activate: Callable[[Session, Any, float], None] | None = None


TASKS: dict[str, Task] = {}


def register(task: Task) -> Task:
    TASKS[task.name] = task
    return task


def _fingerprint(examples: list[ml.Example]) -> str:
    counts = Counter(ex.y for ex in examples)
    keys = hashlib.blake2b("|".join(sorted(ex.key for ex in examples)).encode()).hexdigest()
    return hashlib.blake2b(f"{sorted(counts.items())}:{keys}".encode(), digest_size=32).hexdigest()


def active_row(session: Session, task: str) -> BrainModel | None:
    return session.scalar(
        select(BrainModel).where(BrainModel.task == task, BrainModel.active.is_(True)).limit(1)
    )


def _fit(task: Task, examples: list[ml.Example]) -> Any:
    xs, ys = [ex.x for ex in examples], [ex.y for ex in examples]
    if task.taught is None:
        return task.make().fit(xs, ys)
    return task.make().fit(xs, ys, [task.taught(ex) for ex in examples])


def train(session: Session, name: str, trigger: str = "manual") -> BrainModel:
    """Entrena una versión nueva, la mide en datos que no vio y decide si la activa."""
    task = TASKS.get(name)
    if task is None or task.build is None:
        raise BrainError("Esta capacidad todavía no tiene un modelo entrenable.")
    started = time.monotonic()
    examples = task.build(session)
    if len(examples) < task.min_examples:
        raise BrainError(f"Aún no hay suficientes ejemplos para entrenar ({len(examples)} de "
                         f"{task.min_examples}).")  # fmt: skip
    train_set, test_set = ml.split(examples)
    if not test_set or len({ex.y for ex in train_set}) < 2:
        raise BrainError("Los ejemplos no alcanzan para separar entrenamiento y prueba.")
    candidate = _fit(task, train_set)
    measured = [ex for ex in test_set if task.test_filter(ex)] if task.test_filter else test_set
    measured = list({ex.key: ex for ex in measured}.values())  # sin duplicados al medir
    if not measured:
        raise BrainError("No hay ejemplos de prueba para medir este modelo.")
    threshold = task.threshold or ml.best_threshold(candidate, measured, task.target)
    if task.predictor:
        predictor = task.predictor
        pairs = [(ex.y, *predictor(candidate, threshold, ex)) for ex in measured]
        metrics = ml.score(pairs, threshold).as_dict()
    else:
        metrics = ml.evaluate(candidate, measured, threshold).as_dict()
    baseline = task.baseline(session, measured).as_dict() if task.baseline else None
    current = active_row(session, name)
    better_than_rules = baseline is None or metrics["accuracy"] >= baseline["accuracy"]
    not_worse = (
        current is None or metrics["accuracy"] >= current.metrics.get("accuracy", 0) - TOLERANCE
    )
    promote = better_than_rules and not_worse
    # Con la decisión tomada, el modelo de producción aprende de todos los ejemplos.
    final = _fit(task, examples) if promote else candidate
    if task.extras:
        metrics["extra"] = task.extras(session, final, threshold)
    version = int(session.scalar(select(func.max(BrainModel.version)).where(
        BrainModel.task == name)) or 0) + 1  # fmt: skip
    if promote:
        session.execute(update(BrainModel).where(BrainModel.task == name).values(active=False))
    notes = "" if promote else (
        "No se activó: acierta menos que las reglas." if not better_than_rules
        else "No se activó: acierta menos que la versión activa."
    )  # fmt: skip
    row = BrainModel(
        task=name, version=version, algorithm=final.kind, active=promote, trigger=trigger,
        n_train=len(train_set), n_test=len(measured), threshold=threshold, metrics=metrics,
        baseline=baseline, data_hash=_fingerprint(examples),
        duration_ms=int((time.monotonic() - started) * 1000), blob=ml.dumps(final), notes=notes,
    )  # fmt: skip
    session.add(row)
    session.flush()
    _CACHE.pop(name, None)
    if promote and task.on_activate:
        task.on_activate(session, final, threshold)
    logger.info("brain_trained", extra={"task": name, "version": version, "active": promote,
                                        "accuracy": metrics["accuracy"]})  # fmt: skip
    return row


def activate(session: Session, name: str, version: int) -> BrainModel:
    row = session.scalar(select(BrainModel).where(BrainModel.task == name,
                                                  BrainModel.version == version))  # fmt: skip
    if row is None:
        raise BrainError("Esa versión no existe.")
    session.execute(update(BrainModel).where(BrainModel.task == name).values(active=False))
    row.active = True
    session.flush()
    _CACHE.pop(name, None)
    task = TASKS.get(name)
    if task and task.on_activate:
        task.on_activate(session, ml.loads(row.blob), row.threshold)
    return row


# --- Uso en producción ----------------------------------------------------------------------

_CACHE: dict[str, tuple[float, int | None, ml.Model | None, float]] = {}
_CACHE_LOCK = threading.Lock()


def active(session: Session, name: str) -> tuple[ml.Model, float, int] | None:
    """Modelo activo (en memoria, se revisa la base cada 5 minutos)."""
    now = time.monotonic()
    cached = _CACHE.get(name)
    if cached and now - cached[0] < CACHE_SECONDS:
        return (cached[2], cached[3], cached[1]) if cached[2] is not None and cached[1] else None
    with _CACHE_LOCK:
        meta = session.execute(
            select(BrainModel.version, BrainModel.threshold)
            .where(BrainModel.task == name, BrainModel.active.is_(True)).limit(1)
        ).first()  # fmt: skip
        if meta is None:
            _CACHE[name] = (now, None, None, 0.0)
            return None
        if cached and cached[1] == meta.version and cached[2] is not None:
            _CACHE[name] = (now, cached[1], cached[2], cached[3])
            return cached[2], cached[3], meta.version
        blob = session.scalar(
            select(BrainModel.blob).where(
                BrainModel.task == name, BrainModel.version == meta.version
            )
        )
        model = ml.loads(blob) if blob else None
        _CACHE[name] = (now, meta.version, model, float(meta.threshold))
        return (model, float(meta.threshold), meta.version) if model else None


@dataclass
class Prediction:
    label: str
    confidence: float
    sure: bool  # supera el umbral con el que el modelo acierta ≥ la meta
    version: int


def predict(session: Session, name: str, x: ml.Features) -> Prediction | None:
    loaded = active(session, name)
    if loaded is None:
        return None
    model, threshold, version = loaded
    label, confidence = ml.predict(model, x)
    return Prediction(label, confidence, confidence >= threshold, version)


def record_feedback(
    session: Session, task: str, label: str, *, text: str = "", data: dict[str, Any] | None = None,
    predicted: str | None = None, tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> None:  # fmt: skip
    """Guarda una corrección humana: el próximo entrenamiento aprende de ella."""
    feedback = BrainFeedback(task=task, label=label, text=text[:2000], data=data or {},
                             predicted=predicted, tenant_id=tenant_id, user_id=user_id)  # fmt: skip
    session.add(feedback)


# --- Entrenamiento nocturno -----------------------------------------------------------------


def train_due(factory: sessionmaker[Session], force: bool = False) -> list[str]:
    """Una vez por noche (2 a 5 a. m., hora de Bogotá) reentrena lo que tenga datos nuevos."""
    import orkesta.brain.capabilities  # noqa: F401  (registra las capacidades)
    from orkesta.admin.service import _get, _put

    local = datetime.now(ZoneInfo("America/Bogota"))
    if not force and not NIGHTLY_FROM <= local.hour < NIGHTLY_UNTIL:
        return []
    today = local.date().isoformat()
    with factory.begin() as session:
        state = _get(session, "brain", {})
        if not force and state.get("nightly") == today:
            return []
        _put(session, "brain", {**state, "nightly": today})
    try:  # antes de entrenar: toda la data nueva se vuelve evidencia
        from orkesta.brain.consolidate import run as consolidate

        consolidate(factory)
    except Exception:
        logger.exception("consolidate_nightly_failed")
    trained = []
    for name, task in TASKS.items():
        if task.build is None:
            continue
        try:
            with factory.begin() as session:
                current = active_row(session, name)
                examples = task.build(session)
                if current and current.data_hash == _fingerprint(examples):
                    continue  # nada nuevo que aprender
                train(session, name, trigger="nocturno")
                trained.append(name)
        except BrainError as exc:
            logger.info("brain_skip", extra={"task": name, "reason": str(exc)})
        except Exception:
            logger.exception("brain_nightly_failed")
    return trained


# --- Entrenamiento en segundo plano (desde el panel) -----------------------------------------

RUNNING: dict[str, dict[str, Any]] = {}


def train_async(factory: sessionmaker[Session], name: str) -> None:
    if RUNNING.get(name, {}).get("state") == "running":
        raise BrainError("Ya se está entrenando esta capacidad.")
    RUNNING[name] = {"state": "running", "started_at": datetime.now(UTC).isoformat()}

    def work() -> None:
        try:
            with factory.begin() as session:
                row = train(session, name, trigger="manual")
                RUNNING[name] = {"state": "done", "version": row.version, "active": row.active}
        except BrainError as exc:
            RUNNING[name] = {"state": "error", "error": str(exc)}
        except Exception:
            logger.exception("brain_train_failed")
            RUNNING[name] = {"state": "error", "error": "El entrenamiento falló. Intenta de nuevo."}

    threading.Thread(target=work, daemon=True, name=f"orkesta-brain-{name}").start()


# --- Panel «Cerebro» ------------------------------------------------------------------------


def _version_dict(row: BrainModel) -> dict[str, Any]:
    return {
        "version": row.version, "algorithm": row.algorithm, "active": row.active,
        "trigger": row.trigger, "trained_at": row.created_at.isoformat(),
        "accuracy": row.metrics.get("accuracy"), "macro_f1": row.metrics.get("macro_f1"),
        "baseline": (row.baseline or {}).get("accuracy"), "n_train": row.n_train,
        "n_test": row.n_test, "notes": row.notes, "duration_ms": row.duration_ms,
    }  # fmt: skip


def overview(session: Session) -> dict[str, Any]:
    tasks = []
    for name, task in TASKS.items():
        rows = session.scalars(select(BrainModel).where(BrainModel.task == name)
                               .order_by(BrainModel.version.desc()).limit(12)).all()  # fmt: skip
        current = next((r for r in rows if r.active), None)
        feedback = int(session.scalar(select(func.count()).select_from(BrainFeedback)
                                      .where(BrainFeedback.task == name)) or 0)  # fmt: skip
        try:
            status = task.status(session)
        except Exception:
            logger.exception("brain_status_failed")
            status = []
        tasks.append({
            "name": name, "label": task.label, "description": task.description,
            "step": task.step, "trainable": task.build is not None,
            "method": f"Modelo propio v{current.version}" if current else task.method,
            "status": status, "feedback": feedback,
            "active": {
                **_version_dict(current), "threshold": current.threshold,
                "extra": current.metrics.get("extra"),
                "confident": current.metrics.get("confident"),
                "per_label": current.metrics.get("per_label"),
                "confusions": current.metrics.get("confusions"),
                "size_kb": round(len(current.blob) / 1024, 1),
            } if current else None,
            "history": [_version_dict(r) for r in rows],
            "running": RUNNING.get(name),
        })  # fmt: skip
    from orkesta.admin.service import _get

    return {"tasks": tasks, "nightly": _get(session, "brain", {}).get("nightly"),
            "schedule": "todas las noches entre las 2 y las 5 a. m. (hora de Bogotá)"}  # fmt: skip
