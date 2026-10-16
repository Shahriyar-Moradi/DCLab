"""Dataset profile (training rows of the current SplitPlan) and policy reads (P4.1-C).

Statistics scope (ML correctness): every statistic and the deterministic RULE role are
computed on the TRAINING rows of the dataset's current SplitPlan, selected by the
plan's verified row map (``split_plan_service.load_assignment``); holdout rows are
dropped before anything is computed, so they are never counted or exposed. The
"current" plan is the project's ``split_plan`` ref when it partitions this dataset,
else the dataset's newest plan. No plan, or a map that fails verification: whole-upload
metadata only (name, ordinal, physical type) and no statistic, because a whole-file
statistic would include rows that become (or are) the final holdout.

The training rows' cells get the run's fit-free structural preparation (numeric coercion,
cell hygiene) before the run's role inference; the run decides numeric coercion over the
whole upload, so on a column whose holdout rows alone tip that decision the rule role here
(training rows only, by design) can differ from the run's. ROLE USED, transforms, leakage exclusions and importance come from
one completed experiment on that plan (the champion's when it uses the plan, else the
newest): its column-role evidence, preprocessing record, train-only leakage plan and
CV-validation-fold permutation importance. Nothing here reads holdout metrics.

V7 BE-C: entity count, time range and target rate summarise the same training rows
(``training_summary``); with a time-ordered plan the range ends before the final test period.
"""

from __future__ import annotations

import logging
import threading
import warnings
from collections import OrderedDict
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.gateway.redaction import EXPOSURE_CEILING, min_class
from app.agents.governance.policy import PolicyUnavailable, effective_policy
from app.db.models import (
    ClientLabUpload,
    Dataset,
    DatasetColumn,
    Experiment,
    IngestionPublicationEvent,
    IngestionRun,
    ModelVersion,
    SplitPlan,
    User,
    WorkflowRun,
)
from app.domain.dataset_profile import (
    DatasetPolicyRead,
    DatasetProfileColumnRead,
    DatasetProfileExperimentRead,
    DatasetProfileRead,
    DatasetProfileSplitPlanRead,
    DatasetProfileTimeRangeRead,
    DatasetVersionRead,
)
from app.domain.errors import IdentityError
from app.engine.data.loaders import load_table
from app.engine.features.encode import coerce_binary_target
from app.engine.lab.auto_prepare import (
    clean_feature_cells,
    coerce_numeric_like,
    engineer_features,
    infer_column_roles,
    plan_missing_values,
)
from app.services.authorization_service import can_read_workspace
from app.services.dataset_column_service import effective_dataset_policy
from app.services.dataset_materialization import materialize_dataset
from app.services.ingestion_run_service import INTERNAL_TRAINING_REASONS
from app.services.project_ref_service import current_refs
from app.services.split_plan_service import load_assignment
from app.services.technical_explorer_service import _dataset_list_item

logger = logging.getLogger(__name__)

_CACHE_MAX = 16
_cache: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()  # {"columns": ..., "summary": ...}
_cache_lock = threading.Lock()
_inflight: dict[tuple[str, ...], threading.Lock] = {}
PROFILE_MAX_ROWS = 2_000_000


class DatasetNotFoundError(LookupError):
    """Not in the caller's workspace (or does not exist): the route answers 404."""


def _dataset(db: Session, actor: User, workspace_id: UUID, dataset_id: UUID) -> Dataset:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("workspace read denied", status_code=403)
    row = db.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id))
    if row is None:
        raise DatasetNotFoundError("dataset not found")
    return row


# --- policy -------------------------------------------------------------------------------


def _upload_policy(db: Session, dataset: Dataset) -> tuple[str | None, str | None]:
    if dataset.ingestion_run_id is None:
        return None, None
    run = db.scalar(select(IngestionRun).where(
        IngestionRun.id == dataset.ingestion_run_id, IngestionRun.workspace_id == dataset.workspace_id))
    if run is None:
        return None, None
    published = db.scalar(select(IngestionPublicationEvent.id).where(
        IngestionPublicationEvent.workspace_id == dataset.workspace_id,
        IngestionPublicationEvent.ingestion_run_id == run.id,
        IngestionPublicationEvent.to_state == "published",
        IngestionPublicationEvent.reason_code == INTERNAL_TRAINING_REASONS["published"],
    ).limit(1))
    return ("internal_training" if published is not None else None), run.publication_state


def dataset_policy(db: Session, dataset: Dataset) -> DatasetPolicyRead:
    """ADR 0005 labels + the AI data class (exposure ceiling narrowed by the workspace policy)."""

    upload_policy, publication_state = _upload_policy(db, dataset)
    labels = effective_dataset_policy(db, workspace_id=dataset.workspace_id, dataset_id=dataset.id)
    exposure = labels.llm_exposure_policy if labels is not None else "deny"
    try:
        workspace_max = effective_policy(db, dataset.workspace_id).policy.data.max_class
    except PolicyUnavailable:
        workspace_max = None
    ceiling = EXPOSURE_CEILING.get(exposure)
    ai_class = min_class(ceiling, workspace_max) if ceiling is not None and workspace_max is not None else "none"
    return DatasetPolicyRead(
        upload_policy=upload_policy, publication_state=publication_state,
        policy_revision=labels.dataset_policy_revision if labels else None,
        policy_complete=bool(labels and labels.complete),
        sensitivity_class=labels.sensitivity_class if labels else None, llm_exposure_policy=exposure,
        retention_class=labels.retention_class if labels else None,
        residency_class=labels.residency_class if labels else None,
        ai_data_class=ai_class, workspace_ai_max_class=workspace_max,
    )


def dataset_read(db: Session, *, actor: User, workspace_id: UUID, dataset_id: UUID,
                 viewer_id: UUID | None = None) -> DatasetVersionRead:
    dataset = _dataset(db, actor, workspace_id, dataset_id)
    item = _dataset_list_item(dataset, viewer_id=viewer_id)
    return DatasetVersionRead(**item.model_dump(), policy=dataset_policy(db, dataset))


# --- training-row statistics ----------------------------------------------------------------


def _rule_role(name: str, roles: Any, initial: Any, transformed: set[str]) -> str:
    """The run's role vocabulary (``/`` as ``_``): numerical (datetime when converted),
    categorical (boolean when boolean before feature engineering), identifier, ignored_free_text."""

    if name in roles.identifier:
        return "identifier"
    if name in roles.numerical:
        return "datetime" if name in transformed else "numerical"
    if name in roles.categorical or name in roles.boolean:
        return "boolean" if name in initial.boolean else "categorical"
    return "ignored_free_text"


def training_statistics(frame: pd.DataFrame, train_rows: set[int], target: str,
                        holdout_rows: frozenset[int] = frozenset()) -> dict[str, dict[str, Any]]:
    """Per column: missing / unique counts and the deterministic rule role, on ``train_rows`` only.

    ``frame`` is the loaded upload (index = the run's ``source_row``). Holdout rows are
    dropped first; a map row (train or holdout) missing from the frame fails closed
    (``ValueError``). The rule role is the run's train-only rule: role inference after
    feature engineering, with columns the train-only missing-value plan drops as
    ``ignored_free_text``; the leakage plan is not applied (``leakage_excluded`` shows it).
    """

    frame = frame.copy()
    frame.columns = [str(c) for c in frame.columns]
    index = set(int(i) for i in frame.index)
    if not train_rows or not (train_rows | set(holdout_rows)) <= index:
        raise ValueError("split assignment rows do not match the dataset")
    columns = list(frame.columns)
    features = [c for c in columns if c != target]
    # Holdout rows go first: even the fit-free cell preparation (numeric coercion decides a
    # column's type from its values; cell hygiene) sees training rows only.
    train = frame.loc[frame.index.isin(sorted(train_rows))]
    train = coerce_numeric_like(train, columns)
    train, _inf, _strings = clean_feature_cells(train, features)
    train = coerce_numeric_like(train, features)
    initial = infer_column_roles(train, features)
    engineered, actions = engineer_features(train, features)
    transformed = {str(n) for a in actions for n in (a.get("output_columns") or a.get("columns") or [])}
    roles = infer_column_roles(engineered, features)
    dropped = set(plan_missing_values(train, features).dropped_columns)
    total = len(train)
    out: dict[str, dict[str, Any]] = {}
    for name in columns:
        series = train[name]
        missing = int(series.isna().sum())
        unique = int(series.nunique(dropna=True))
        role = ("target" if name == target else "ignored_free_text" if name in dropped
                else _rule_role(name, roles, initial, transformed))
        out[name] = {"rule_role": role, "missing_count": missing, "missing_fraction": missing / total,
                     "unique_count": unique, "unique_fraction": unique / total}
    return out


_BINARY_TASKS = frozenset({"binary", "binary_classification"})


def _only(names: list[str]) -> str | None:
    return names[0] if len(names) == 1 else None


def training_summary(frame: pd.DataFrame, train_rows: set[int], stats: dict[str, dict[str, Any]], *,
                     target: str, task_type: str, group_column: str | None,
                     time_column: str | None) -> dict[str, Any]:
    """Entity count, time range and target rate of the TRAINING rows only (V7 BE-C).

    ``frame`` and ``train_rows`` are what ``training_statistics`` validated; holdout rows are
    never selected, so none of the three moves with a final-test cell. The entity column is the
    plan's group column, else the single rule ``identifier``; the time column is the plan's,
    else the single rule ``datetime``. Ambiguous or unparsable columns give null, never a guess.
    """

    frame = frame.copy()
    frame.columns = [str(c) for c in frame.columns]
    train = frame.loc[frame.index.isin(sorted(train_rows))]
    roles = {name: item.get("rule_role") for name, item in stats.items()}
    entity = group_column if group_column in train.columns else _only(
        [n for n, r in roles.items() if r == "identifier" and n in train.columns])
    when = time_column if time_column in train.columns else _only(
        [n for n, r in roles.items() if r == "datetime" and n in train.columns])
    # The training rows' own numeric-coercion decision (as in ``training_statistics``): a column's type
    # must never come from the whole file, where one final-test cell could flip it.
    train = coerce_numeric_like(train, [c for c in dict.fromkeys((entity, when, target)) if c in train.columns])
    out: dict[str, Any] = {"entity_column": entity, "entity_count": None, "time_range": None, "target_rate": None}
    if entity is not None:
        out["entity_count"] = int(train[entity].nunique(dropna=True))
    if when is not None and not pd.api.types.is_numeric_dtype(train[when]):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            moments = pd.to_datetime(train[when], errors="coerce", utc=True).dropna()
        if len(moments):
            out["time_range"] = {"column": when, "start": moments.min().to_pydatetime(),
                                 "end": moments.max().to_pydatetime()}
    if task_type in _BINARY_TASKS and target in train.columns:
        labels = coerce_binary_target(train[target]).dropna()
        if len(labels):
            out["target_rate"] = float(labels.mean())
    return out


def _cached(key: tuple[str, ...]) -> dict[str, Any] | None:
    with _cache_lock:
        if key not in _cache:
            return None
        _cache.move_to_end(key)
        hit = _cache[key]
        return {"columns": {name: dict(item) for name, item in hit["columns"].items()},
                "summary": dict(hit["summary"])}


def _load_statistics(db: Session, dataset: Dataset, plan: SplitPlan) -> dict[str, Any] | None:
    """Training-row statistics, computed once per (dataset, plan) at a time (single flight)."""

    if dataset.row_count > PROFILE_MAX_ROWS:
        return None  # bounded work in the API process; larger files: statistics unavailable
    key = (str(dataset.id), str(dataset.content_digest), str(plan.id), plan.assignment_digest)
    if (hit := _cached(key)) is not None:
        return hit
    with _cache_lock:
        flight = _inflight.setdefault(key, threading.Lock())
    with flight:
        try:
            return _cached(key) or _compute_statistics(db, dataset, plan, key)
        finally:
            with _cache_lock:
                _inflight.pop(key, None)


def _compute_statistics(db: Session, dataset: Dataset, plan: SplitPlan, key: tuple[str, ...]
                        ) -> dict[str, Any] | None:
    try:
        assignment = load_assignment(db, plan)  # bytes verified against the plan's digest
        with materialize_dataset(dataset, db=db) as path:
            frame = load_table(path)
        train_rows = {int(r) for r in assignment.train_folds}
        stats = training_statistics(frame, train_rows, plan.target_column,
                                    frozenset(int(r) for r in assignment.holdout_rows))
    except Exception as exc:  # noqa: BLE001 - fail closed: no statistics, never a whole-file fallback
        logger.warning("dataset profile statistics unavailable: %s", type(exc).__name__)
        return None
    try:
        summary = training_summary(frame, train_rows, stats, target=plan.target_column, task_type=plan.task_type,
                                   group_column=plan.group_column, time_column=plan.time_column)
    except Exception as exc:  # noqa: BLE001 - the summary is optional; the column statistics still stand
        logger.warning("dataset profile summary unavailable: %s", type(exc).__name__)
        summary = {}
    with _cache_lock:
        _cache[key] = {"columns": stats, "summary": summary}
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return {"columns": {name: dict(item) for name, item in stats.items()}, "summary": dict(summary)}


# --- plan and experiment selection ----------------------------------------------------------


def _current_plan(db: Session, dataset: Dataset) -> tuple[SplitPlan | None, str | None]:
    if dataset.project_id is None:
        return None, None
    ref = current_refs(db, workspace_id=dataset.workspace_id, project_id=dataset.project_id).get("split_plan")
    if ref is not None and ref.split_plan_id is not None:
        plan = db.scalar(select(SplitPlan).where(
            SplitPlan.id == ref.split_plan_id, SplitPlan.workspace_id == dataset.workspace_id,
            SplitPlan.dataset_id == dataset.id))
        if plan is not None:
            return plan, "project_ref"
    plan = db.scalar(select(SplitPlan).where(
        SplitPlan.workspace_id == dataset.workspace_id, SplitPlan.dataset_id == dataset.id,
    ).order_by(SplitPlan.version.desc()).limit(1))
    return plan, ("latest_for_dataset" if plan is not None else None)


def _profiled_experiment(db: Session, dataset: Dataset, plan: SplitPlan) -> tuple[Experiment | None, str]:
    on_plan = (Experiment.workspace_id == dataset.workspace_id, Experiment.split_plan_id == plan.id,
               Experiment.source_dataset_id == dataset.id, Experiment.status == "COMPLETED")
    champion = current_refs(db, workspace_id=dataset.workspace_id, project_id=plan.project_id).get("champion_model")
    if champion is not None and champion.model_version_id is not None:
        run_id = db.scalar(select(ModelVersion.pipeline_run_id).where(
            ModelVersion.id == champion.model_version_id, ModelVersion.workspace_id == dataset.workspace_id))
        row = db.scalar(select(Experiment).where(Experiment.id == run_id, *on_plan)) if run_id else None
        if row is not None:
            return row, "champion"
    row = db.scalar(select(Experiment).where(*on_plan).order_by(Experiment.created_at.desc()).limit(1))
    return row, "latest_completed"


def _pipeline_log(db: Session, experiment: Experiment) -> dict[str, Any]:
    if experiment.workflow_run_id is None:
        return {}
    upload_id = db.scalar(select(WorkflowRun.source_upload_id).where(
        WorkflowRun.id == experiment.workflow_run_id, WorkflowRun.workspace_id == experiment.workspace_id))
    upload = db.scalar(select(ClientLabUpload).where(
        ClientLabUpload.id == upload_id, ClientLabUpload.workspace_id == experiment.workspace_id)) if upload_id else None
    return dict(upload.pipeline_log or {}) if upload is not None else {}


def _code(value: Any) -> str | None:
    """Evidence vocabulary as code keys (``ignored/free_text`` -> ``ignored_free_text``, lower case)."""

    return None if value is None else str(value).replace("/", "_").lower()


def _run_facts(db: Session, experiment: Experiment) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Per column: role used, source, reason, transforms, leakage, importance (train/CV evidence only)."""

    log = _pipeline_log(db, experiment)
    facts: dict[str, dict[str, Any]] = {}

    def fact(name: Any) -> dict[str, Any]:
        return facts.setdefault(str(name), {"transforms": []})

    for row in (log.get("column_role_evidence") or {}).get("columns") or []:
        if isinstance(row, dict) and row.get("column") is not None:
            fact(row["column"]).update(role_used=_code(row.get("final_role")), role_source=_code(row.get("source")),
                                       role_reason=row.get("reason"))
    for action in (log.get("feature_engineering") or {}).get("transformations") or []:
        step = (action.get("transformation") or action.get("step")) if isinstance(action, dict) else None
        for name in (action.get("input_columns") or action.get("columns") or []) if step else []:
            fact(name)["transforms"].append(_code(step))
    prep = log.get("preprocessing") or {}
    for group, steps in (("numerical_cols", prep.get("numerical")), ("categorical_cols", prep.get("categorical"))):
        for name in log.get(group) or []:
            fact(name)["transforms"].extend(_code(step) for step in steps or [])
    for item in (log.get("model_development_plan") or {}).get("excluded_features") or []:
        if isinstance(item, dict) and item.get("column") is not None:
            fact(item["column"]).update(leakage_excluded=True, leakage_risk=_code(item.get("risk")),
                                        leakage_reason=item.get("reason"))
    importance = (experiment.result or {}).get("feature_importance")
    if not isinstance(importance, dict) or importance.get("status") != "computed":
        return facts, None
    for row in importance.get("features") or []:
        if isinstance(row, dict) and row.get("column") is not None and row.get("importance_mean") is not None:
            fact(row["column"])["importance"] = float(row["importance_mean"])
    return facts, importance.get("method")


def dataset_profile(db: Session, *, actor: User, workspace_id: UUID, dataset_id: UUID) -> DatasetProfileRead:
    dataset = _dataset(db, actor, workspace_id, dataset_id)
    columns = db.scalars(select(DatasetColumn).where(
        DatasetColumn.workspace_id == workspace_id, DatasetColumn.dataset_id == dataset.id,
    ).order_by(DatasetColumn.ordinal_position)).all()
    plan, plan_source = _current_plan(db, dataset)
    loaded = _load_statistics(db, dataset, plan) if plan is not None else None
    stats = loaded["columns"] if loaded is not None else None
    summary = loaded["summary"] if loaded is not None else {}
    time_range = summary.get("time_range")
    experiment, selection = _profiled_experiment(db, dataset, plan) if plan is not None else (None, "")
    facts, method = _run_facts(db, experiment) if experiment is not None else ({}, None)
    out = []
    for column in columns:
        s = (stats or {}).get(column.name, {})
        f = facts.get(column.name, {})
        out.append(DatasetProfileColumnRead(
            name=column.name, ordinal_position=column.ordinal_position, physical_dtype=column.physical_dtype,
            rule_role=s.get("rule_role"), role_used=f.get("role_used"), role_source=f.get("role_source"),
            role_reason=f.get("role_reason"), missing_count=s.get("missing_count"),
            missing_fraction=s.get("missing_fraction"), unique_count=s.get("unique_count"),
            unique_fraction=s.get("unique_fraction"), transforms=list(dict.fromkeys(f.get("transforms") or [])),
            importance=f.get("importance"), leakage_excluded=bool(f.get("leakage_excluded")),
            leakage_risk=f.get("leakage_risk"), leakage_reason=f.get("leakage_reason"),
        ))
    return DatasetProfileRead(
        dataset_id=dataset.id, project_id=dataset.project_id,
        scope="training_rows" if stats is not None else "upload",
        statistics_status="computed" if stats is not None else ("unavailable" if plan is not None else "no_split_plan"),
        split_plan=DatasetProfileSplitPlanRead(
            id=plan.id, version=plan.version, source=plan_source, target_column=plan.target_column,
            training_row_count=plan.train_row_count,
        ) if plan is not None else None,
        experiment=DatasetProfileExperimentRead(
            id=experiment.id, selection=selection, created_at=experiment.created_at,
        ) if experiment is not None else None,
        importance_method=method,
        columns=out,
        entity_column=summary.get("entity_column"),
        entity_count=summary.get("entity_count"),
        time_range=DatasetProfileTimeRangeRead(**time_range) if time_range else None,
        target_rate=summary.get("target_rate"),
    )
