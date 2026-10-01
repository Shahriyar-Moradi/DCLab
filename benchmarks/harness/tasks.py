"""Pinned benchmark tasks (R1-A).

``sklearn`` tasks ship inside scikit-learn (no download). ``openml`` tasks are
fetched once with ``sklearn.datasets.fetch_openml`` (pinned dataset id + version)
and cached in ``SCIKIT_LEARN_DATA``. Never edit an entry in place: a changed
dataset is a new task id, so results stay comparable with the accepted baseline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    source: str  # "sklearn" | "openml"
    ref: str  # sklearn loader name, or OpenML dataset id
    task_type: str  # "binary" | "multiclass" | "regression"
    version: int | None = None  # OpenML dataset version
    max_rows: int = 5000  # seeded subsample cap, keeps the suite bounded


# Bundled with scikit-learn: the quick suite and the CI smoke run.
QUICK: tuple[BenchmarkTask, ...] = (
    BenchmarkTask("sk-breast-cancer", "sklearn", "load_breast_cancer", "binary"),
    BenchmarkTask("sk-iris", "sklearn", "load_iris", "multiclass"),
    BenchmarkTask("sk-wine", "sklearn", "load_wine", "multiclass"),
    BenchmarkTask("sk-digits", "sklearn", "load_digits", "multiclass"),
    BenchmarkTask("sk-diabetes", "sklearn", "load_diabetes", "regression"),
)

# Small/medium OpenML datasets (classic, public, CC-licensed or public domain).
OPENML: tuple[BenchmarkTask, ...] = (
    BenchmarkTask("oml-credit-g", "openml", "31", "binary", version=1),
    BenchmarkTask("oml-diabetes", "openml", "37", "binary", version=1),
    BenchmarkTask("oml-kr-vs-kp", "openml", "3", "binary", version=1),
    BenchmarkTask("oml-spambase", "openml", "44", "binary", version=1),
    BenchmarkTask("oml-kc1", "openml", "1067", "binary", version=1),
    BenchmarkTask("oml-banknote", "openml", "1462", "binary", version=1),
    BenchmarkTask("oml-blood-transfusion", "openml", "1464", "binary", version=1),
    BenchmarkTask("oml-ilpd", "openml", "1480", "binary", version=1),
    BenchmarkTask("oml-phoneme", "openml", "1489", "binary", version=1),
    BenchmarkTask("oml-wdbc", "openml", "1510", "binary", version=1),
    BenchmarkTask("oml-australian", "openml", "40981", "binary", version=4),
    BenchmarkTask("oml-cmc", "openml", "23", "multiclass", version=1),
    BenchmarkTask("oml-segment", "openml", "36", "multiclass", version=1),
    BenchmarkTask("oml-vehicle", "openml", "54", "multiclass", version=1),
    BenchmarkTask("oml-car", "openml", "40975", "multiclass", version=3),
    BenchmarkTask("oml-abalone", "openml", "183", "regression", version=1),
    BenchmarkTask("oml-kin8nm", "openml", "189", "regression", version=1),
    BenchmarkTask("oml-cpu-small", "openml", "227", "regression", version=1),
)

FULL: tuple[BenchmarkTask, ...] = QUICK + OPENML
SUITES = {"quick": QUICK, "full": FULL}


def find(task_id: str) -> BenchmarkTask:
    for task in FULL:
        if task.id == task_id:
            return task
    raise KeyError(f"unknown benchmark task {task_id!r}")
