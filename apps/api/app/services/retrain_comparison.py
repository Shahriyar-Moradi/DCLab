"""D5a (ADR 0012 D3): the model in use on the retrained model's final-test rows, only when leak-free.

Worker only (it loads a stored model). Comparable only when both runs have the same task and the
challenger's final test is the latest chronological slice of its file (a ``temporal_future`` holdout:
"the same kind of test, latest months"). It reads the retrained (challenger) run's source file and
split map: its final-test rows, their time column and target. It reads the model in use's source
file and split map: its TRAINING rows, their time column only. Comparable only when every
challenger final-test row is strictly later than every training row of the model in use
(``later_than_training``; an equal time is not later; an unreadable time anywhere is not
comparable). Then the model in use scores those rows exactly as batch scoring does (its own
package, its own locked threshold) and the figures come from the monitoring outcome function, on
labels both models code the same way (else not comparable). Nothing is fit, nothing is selected:
the result is evidence for a person. The challenger's own figures are its single final test from
``evaluation_metrics``. Data problems return ``not_comparable`` with a closed reason code; they
never raise.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Dataset, Experiment, ModelVersion, RetrainRequest, SplitPlan
from app.domain.retrain import STATUS_COMPARED, STATUS_NOT_COMPARABLE
from app.engine.modeling.holdout_planner import TEMPORAL_FUTURE  # the only "latest months" final test

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MIN_LABELS = 30  # final-test rows with a label (the monitoring floor of 100 is for weekly windows)
MIN_PER_CLASS = 10
_KEPT_METRICS = {
    "binary": ("precision", "recall", "f1", "accuracy", "roc_auc", "pr_auc"),
    "multiclass": ("accuracy", "balanced_accuracy", "macro_f1", "weighted_f1"),
    "regression": ("mae", "rmse", "r2"),
}


class _NotComparable(Exception):
    def __init__(self, code: str, **facts: Any) -> None:
        super().__init__(code)
        self.code = code
        self.facts = facts


def _iso(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _kind(series: pd.Series) -> tuple[str, pd.Series]:
    """(``number`` | ``time``, values): numbers after the engine's numeric coercion, else the raw text."""

    from app.engine.lab.auto_prepare import coerce_numeric_like

    name = str(series.name) if series.name is not None else "t"
    values = coerce_numeric_like(series.to_frame(name), [name])[name]
    if pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values):
        return "number", pd.to_numeric(values, errors="coerce").astype(float)
    return "time", values


def later_than_training(train_times: pd.Series, test_times: pd.Series) -> tuple[str | None, dict[str, Any]]:
    """(``None`` when every final-test time is strictly later than every training time, else the
    reason code; facts). Equal is not later; an unreadable time on either side is not comparable.
    Dates on BOTH sides are read with the one format of the training rows (an ambiguous
    ``03/04/2024`` never reads one way here and another way there); no single format: not comparable."""

    from app.engine.features.encode import infer_datetime_format

    train_kind, train = _kind(train_times)
    test_kind, test = _kind(test_times)
    if train_kind != test_kind:
        return "time_kinds_differ", {}
    if train.empty or test.empty or bool(train.isna().any()) or bool(test.isna().any()):
        return "time_missing", {}
    if train_kind == "time":
        fmt = infer_datetime_format(train) or infer_datetime_format(test)  # the text side's, when one is typed
        if fmt == "mixed":
            return "time_missing", {}
        train, test = (pd.to_datetime(part, errors="coerce", utc=True, format=fmt) for part in (train, test))
        if bool(train.isna().any()) or bool(test.isna().any()):
            return "time_missing", {}
    end, start = train.max(), test.min()
    facts = {"incumbent_training_end": _iso(end), "final_test_start": _iso(start), "final_test_rows": int(len(test))}
    return (None if start > end else "final_test_overlaps_training"), facts


def _scoped(db: Session, model: Any, workspace_id: Any, row_id: Any) -> Any:
    if row_id is None:
        return None
    return db.scalar(select(model).where(model.id == row_id, model.workspace_id == workspace_id))


def _frame_and_map(db: Session, experiment: Experiment) -> tuple[pd.DataFrame, Any]:
    from app.engine.data.loaders import load_table
    from app.services.dataset_materialization import materialize_dataset
    from app.services.split_plan_service import load_assignment

    dataset = _scoped(db, Dataset, experiment.workspace_id, experiment.source_dataset_id)
    plan = _scoped(db, SplitPlan, experiment.workspace_id, experiment.split_plan_id)
    if dataset is None or plan is None or plan.dataset_id != dataset.id:
        raise _NotComparable("data_unavailable")
    try:
        assignment = load_assignment(db, plan)  # bytes verified against the plan's digest
        with materialize_dataset(dataset, db=db) as path:
            frame = load_table(path)
    except Exception as exc:  # noqa: BLE001 - fail closed: never compare on a guessed partition
        raise _NotComparable("data_unavailable") from exc
    frame.columns = [str(name) for name in frame.columns]
    rows = {int(row) for row in assignment.train_folds} | {int(row) for row in assignment.holdout_rows}
    if not rows or not rows <= {int(index) for index in frame.index}:
        raise _NotComparable("data_unavailable")
    return frame, assignment


def _latest_months_column(db: Session, challenger: Experiment) -> str:
    """The challenger's split time column, only when its final test is the latest chronological slice of
    its own file (a ``temporal_future`` holdout); any other final test is mixed in time with its own
    training rows, so beside an out-of-time incumbent it would not be the same kind of test."""

    plan = _scoped(db, SplitPlan, challenger.workspace_id, challenger.split_plan_id)
    if plan is None or plan.holdout_strategy != TEMPORAL_FUTURE or not plan.time_column:
        raise _NotComparable("challenger_test_not_latest",
                             challenger_holdout_strategy=plan.holdout_strategy if plan is not None else None)
    return str(plan.time_column)


def _labels(task_type: str, raw: pd.Series, target: dict[str, Any] | None, class_labels: Any) -> pd.Series:
    """The final-test labels in the incumbent's own coding, refused unless the challenger's training
    coding gives the same value on every row (binary) or every label is a class the incumbent knows."""

    from app.engine.features.encode import coerce_binary_target
    from app.engine.serving.monitoring import binary_outcome_codes, multiclass_outcome_codes

    if task_type == "binary":
        incumbent, unrecognised = binary_outcome_codes(raw, target)
        challenger = pd.to_numeric(coerce_binary_target(raw), errors="coerce")
        same = np.array_equal(incumbent.to_numpy(dtype=float), challenger.to_numpy(dtype=float), equal_nan=True)
        if unrecognised or not same:
            raise _NotComparable("target_coding_differs")
        return incumbent
    if task_type == "multiclass":
        if multiclass_outcome_codes(raw, list(class_labels or []))[1]:
            raise _NotComparable("target_coding_differs")
        return raw
    return pd.to_numeric(raw, errors="coerce")


def compare(db: Session, request: RetrainRequest, challenger: Experiment) -> dict[str, Any]:
    """The stored comparison body: ``status``, ``reason_code`` and aggregate figures (never rows)."""

    from app.services.experiment_branch_service import winner_evidence

    try:
        evidence = winner_evidence(db, challenger)
    except Exception:  # noqa: BLE001 - no locked winner: nothing to show beside the model in use
        evidence = {"holdout": {}}
    metric = evidence.get("selection_metric")
    base: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "time_column": None, "challenger_metric": metric,
                            "challenger_holdout": dict(evidence.get("holdout") or {}),
                            "challenger_threshold": evidence.get("decision_threshold")}
    try:
        return {**base, **_compare(db, request, challenger, base)}
    except _NotComparable as exc:
        return {**base, **exc.facts, "status": STATUS_NOT_COMPARABLE, "reason_code": exc.code}
    except Exception as exc:  # noqa: BLE001 - recorded as not comparable; the cause stays in the worker log
        logger.warning("retrain comparison %s failed: %s", request.id, type(exc).__name__, exc_info=True)
        return {**base, "status": STATUS_NOT_COMPARABLE, "reason_code": "comparison_failed"}


def _compare(db: Session, request: RetrainRequest, challenger: Experiment, base: dict[str, Any]) -> dict[str, Any]:
    from app.engine.serving.batch_scoring import ScoringError, score_frame
    from app.engine.serving.monitoring import outcome_metrics, usable_reference
    from app.services.batch_prediction_service import load_version_for_scoring

    ws = request.workspace_id
    if not base["challenger_holdout"]:
        raise _NotComparable("challenger_not_scored")
    version = _scoped(db, ModelVersion, ws, request.incumbent_model_version_id)
    incumbent = _scoped(db, Experiment, ws, version.pipeline_run_id) if version is not None else None
    if incumbent is None:
        raise _NotComparable("data_unavailable")
    challenger_task = str(((challenger.result or {}).get("task") or {}).get("task_type") or "")
    incumbent_task = str(((incumbent.result or {}).get("task") or {}).get("task_type") or "")
    if not challenger_task or challenger_task != incumbent_task:
        raise _NotComparable("task_differs")
    column = _latest_months_column(db, challenger)
    base["time_column"] = column
    new_frame, new_map = _frame_and_map(db, challenger)
    old_frame, old_map = _frame_and_map(db, incumbent)
    if column not in new_frame.columns or column not in old_frame.columns:
        raise _NotComparable("no_time_column", time_column=column)
    held = new_frame.loc[sorted(int(row) for row in new_map.holdout_rows)]
    reason, facts = later_than_training(old_frame.loc[sorted(int(row) for row in old_map.train_folds), column],
                                        held[column])
    del old_frame
    if reason is not None:
        raise _NotComparable(reason, time_column=column, **facts)
    try:
        loaded, spec = load_version_for_scoring(db, version)
        scored = score_frame(held, spec, loaded.pipeline).predictions
    except ScoringError as exc:
        raise _NotComparable("incumbent_cannot_score", time_column=column, **facts) from exc
    target = str(spec.target_column or (incumbent.result or {}).get("task", {}).get("target") or "")
    if target not in held.columns:
        raise _NotComparable("target_coding_differs", time_column=column, **facts)
    reference = usable_reference(loaded.manifest) or {}
    try:
        labels = _labels(spec.task_type, held[target].reset_index(drop=True), reference.get("target"),
                         spec.class_labels)
    except _NotComparable as exc:
        raise _NotComparable(exc.code, time_column=column, **facts) from exc
    measured = outcome_metrics(spec.task_type, labels, scored, metric=base["challenger_metric"],
                               threshold=spec.decision_threshold, class_labels=spec.class_labels,
                               min_labels=MIN_LABELS, min_per_class=MIN_PER_CLASS)
    if measured.get("status") != "measured":
        raise _NotComparable("not_enough_labels", time_column=column, **facts)
    kept = _KEPT_METRICS.get(spec.task_type, ())
    return {"status": STATUS_COMPARED, "reason_code": None, "time_column": column, **facts,
            "challenger_holdout_strategy": TEMPORAL_FUTURE,
            "incumbent": {"metric": measured.get("metric"), "metric_value": measured.get("metric_value"),
                          "metrics": {key: (measured.get("metrics") or {}).get(key) for key in kept},
                          "n_labelled": measured.get("n_labelled"), "per_class": measured.get("per_class"),
                          "decision_threshold": spec.decision_threshold}}
