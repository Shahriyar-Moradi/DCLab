"""SplitPlan row assignment (ADR 0006 §3): the holdout and outer-fold map of a source dataset.

Canonical CSV, UTF-8, LF, header ``source_row,partition,fold``, one line per
modeling row sorted by ``source_row``. ``source_row`` is the engine's
``__dclab_source_row__``; ``partition`` is ``train`` or ``holdout``; ``fold`` is
the 1-based outer validation fold of a train row (``0`` for a train row that is
never a validation row, i.e. a TimeSeriesSplit warm-up row) and empty for a
holdout row. The digest is the sha256 of the exact bytes.

Pure functions only; persistence and lookup live in ``split_plan_service``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.engine.modeling.validation_planner import FoldSplit, ValidationPlan, folds_from_assignment
from app.engine.validation.splits import (
    SOURCE_ROW_COLUMN,
    SPLIT_ASSIGNMENT_MISMATCH,
    SplitAssignmentMismatchError,
)

__all__ = [
    "SPLIT_ASSIGNMENT_MISMATCH",
    "SplitAssignment",
    "SplitAssignmentMismatchError",
    "build_split_assignment",
    "folds_for_pool",
    "parse_split_assignment",
]

SPLIT_ASSIGNMENT_HEADER = "source_row,partition,fold"
PARTITION_TRAIN = "train"
PARTITION_HOLDOUT = "holdout"


@dataclass(frozen=True)
class SplitAssignment:
    """``train_folds``: source_row -> outer fold (0 = never validated); ``holdout_rows``."""

    train_folds: Mapping[int, int]
    holdout_rows: frozenset[int]

    @property
    def row_count(self) -> int:
        return len(self.train_folds) + len(self.holdout_rows)

    def to_csv_bytes(self) -> bytes:
        lines = [SPLIT_ASSIGNMENT_HEADER]
        rows: list[tuple[int, str, str]] = [
            (int(row), PARTITION_TRAIN, str(int(fold))) for row, fold in self.train_folds.items()
        ]
        rows.extend((int(row), PARTITION_HOLDOUT, "") for row in self.holdout_rows)
        rows.sort(key=lambda item: item[0])
        lines.extend(f"{row},{partition},{fold}" for row, partition, fold in rows)
        return ("\n".join(lines) + "\n").encode("utf-8")

    def digest(self) -> str:
        return assignment_digest(self.to_csv_bytes())


def assignment_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_split_assignment(
    *,
    train_source_rows: Iterable[int],
    holdout_source_rows: Iterable[int],
    train_frame: pd.DataFrame,
    folds: Iterable[FoldSplit],
) -> SplitAssignment:
    """Map the locked holdout and the planned outer folds (indices into ``train_frame``)."""

    if SOURCE_ROW_COLUMN not in train_frame.columns:
        raise SplitAssignmentMismatchError("training frame has no source-row provenance")
    sources = train_frame[SOURCE_ROW_COLUMN].astype(int).to_numpy()
    train_rows = [int(row) for row in train_source_rows]
    holdout = frozenset(int(row) for row in holdout_source_rows)
    if len(set(train_rows)) != len(train_rows) or set(train_rows) & holdout:
        raise SplitAssignmentMismatchError("train/holdout provenance is not disjoint")
    if sorted(train_rows) != sorted(int(row) for row in sources):
        raise SplitAssignmentMismatchError("training frame does not match the locked train rows")
    train_folds = {row: 0 for row in train_rows}
    for fold in folds:
        for index in np.asarray(fold.validation_index):
            row = int(sources[int(index)])
            if train_folds[row] != 0:
                raise SplitAssignmentMismatchError(f"row {row} is validated in two folds")
            train_folds[row] = int(fold.fold_number)
    return SplitAssignment(train_folds=train_folds, holdout_rows=holdout)


def parse_split_assignment(payload: bytes) -> SplitAssignment:
    """Strict parser for the canonical form; anything else is a mismatch."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SplitAssignmentMismatchError("assignment is not UTF-8") from exc
    if not text.endswith("\n"):
        raise SplitAssignmentMismatchError("assignment is not LF-terminated")
    lines = text[:-1].split("\n")
    if not lines or lines[0] != SPLIT_ASSIGNMENT_HEADER:
        raise SplitAssignmentMismatchError("assignment header is not canonical")
    train_folds: dict[int, int] = {}
    holdout: set[int] = set()
    previous = -1
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 3 or not parts[0].isdigit():
            raise SplitAssignmentMismatchError(f"malformed assignment line {line!r}")
        row = int(parts[0])
        if row <= previous:
            raise SplitAssignmentMismatchError("assignment rows are not strictly sorted")
        previous = row
        if parts[1] == PARTITION_HOLDOUT and parts[2] == "":
            holdout.add(row)
        elif parts[1] == PARTITION_TRAIN and parts[2].isdigit():
            train_folds[row] = int(parts[2])
        else:
            raise SplitAssignmentMismatchError(f"malformed assignment line {line!r}")
    parsed = SplitAssignment(train_folds=train_folds, holdout_rows=frozenset(holdout))
    if parsed.to_csv_bytes() != payload:
        raise SplitAssignmentMismatchError("assignment is not in canonical form")
    return parsed


def folds_for_pool(
    plan: ValidationPlan, pool: pd.DataFrame, train_folds: Mapping[int, int]
) -> list[FoldSplit]:
    """Outer folds of the runner's training pool, taken from the stored map."""

    if SOURCE_ROW_COLUMN not in pool.columns:
        raise SplitAssignmentMismatchError("training pool has no source-row provenance")
    sources = [int(value) for value in pool[SOURCE_ROW_COLUMN].astype(int).tolist()]
    if len(set(sources)) != len(sources) or set(sources) != set(int(k) for k in train_folds):
        raise SplitAssignmentMismatchError(
            "training rows differ from the split plan's train partition"
        )
    fold_of = np.asarray([int(train_folds[row]) for row in sources], dtype=int)
    try:
        return folds_from_assignment(plan, pool, fold_of)
    except ValueError as exc:
        raise SplitAssignmentMismatchError(str(exc)) from exc
