"""Monitoring (P7.4-A): weekly windows of the model in use, drift, and outcomes when linked.

Writers: ``after_scoring_completed`` (the scoring worker's success hook; enqueues one
``monitoring.compute_window`` job per (project, model version, week), debounced, and never fails
the scoring), ``link_outcomes`` (a person, API) and ``compute_window_job`` (worker). The job reads
stored files only, through the existing storage paths: each scoring's input dataset and the
needed columns of its output file, the linked outcome files' key and outcome columns, and the
model's package manifest (its training ``reference_summary``). It never loads a model and never
reads the final holdout. Reads are bounded (newest 20 distinct input files, byte and row caps
with fixed-order sampling). A recompute inserts a new window revision, only when the result
changed and the scorings it saw are still the window's. Every query is workspace-scoped; reads
never carry rows, object keys or holdout figures. Nothing here retrains, switches a model or
calls an agent.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import numpy as np
import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    BatchPrediction,
    Dataset,
    DatasetColumn,
    Experiment,
    MlJob,
    ModelVersion,
    MonitoringOutcomeSet,
    MonitoringThresholdSet,
    MonitoringWindow,
    ProjectDecisionRecord,
    ProjectRef,
    User,
)
from app.domain import monitoring as dm
from app.domain.batch_predictions import PREDICTION_COMPLETED
from app.domain.decision_records import (
    DECISION_MONITORING_OUTCOMES_LINKED,
    DECISION_MONITORING_THRESHOLDS_CHANGED,
    STATE_ACCEPTED,
    DecisionActor,
)
from app.domain.errors import IdentityError, MonitoringError
from app.domain.ml_jobs import (
    HANDLER_MONITORING_COMPUTE_WINDOW,
    HANDLER_VERSION_MONITORING_COMPUTE_WINDOW,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_TYPE_MONITORING,
)
from app.services.authorization_service import can_perform_ml_write

logger = logging.getLogger(__name__)

_REASON_COLUMN = re.compile(r"^reason_\d+$")  # P7.7-A explanations: never inputs, never keys
_SCORE_COLUMNS = frozenset({"probability", "prediction", "label"})
# Threshold-dependent binary metrics: their stored CV mean is not at the locked threshold.
_THRESHOLD_METRICS = frozenset({"f1", "precision", "recall", "accuracy", "balanced_accuracy", "specificity"})
# Server-written History text (the person's file and column names are in the record's facts).
_LINK_RATIONALE = ("Linked a file of real outcomes for monitoring; the recent weeks of the model in use are "
                   "computed again with them. Nothing is retrained or switched.")


def _settings() -> Any:
    from app.config import get_settings

    return get_settings()


def _now() -> datetime:
    return datetime.now(UTC)


def week_start(moment: datetime) -> datetime:
    """Monday 00:00 UTC of the calendar week holding ``moment``."""

    day = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def _scoped(db: Session, model: Any, workspace_id: UUID, row_id: UUID | None) -> Any:
    if row_id is None:
        return None
    return db.scalar(select(model).where(model.id == row_id, model.workspace_id == workspace_id))


def model_in_use(db: Session, workspace_id: UUID, project_id: UUID) -> UUID | None:
    return db.scalar(select(ProjectRef.model_version_id).where(
        ProjectRef.workspace_id == workspace_id, ProjectRef.project_id == project_id,
        ProjectRef.ref_kind == "champion_model"))


# --- enqueue (worker hook and outcome links) ---------------------------------------------------


def enqueue_window_job(
    db: Session, *, workspace_id: UUID, project_id: UUID, model_version_id: UUID, start: datetime, reason: str
) -> MlJob:
    """One queued job per (project, model version, week): an existing queued job is reused (it
    reads the newest data when it runs). Debounced: when a job of this window is running or
    started within ``MONITORING_DEBOUNCE_MINUTES``, the new one waits that long, so a burst of
    scorings or links collapses into one recompute. One attempt (a failure is recorded with its
    code; the next scoring or link queues again). Flushes; the caller commits."""

    from app.services.ml_job_service import create_ml_job

    target = uuid5(NAMESPACE_URL, f"dclab-monitoring:{project_id}:{model_version_id}:{start.isoformat()}")
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"monitoring:{target}", 0))))
    same = (MlJob.workspace_id == workspace_id, MlJob.handler_key == HANDLER_MONITORING_COMPUTE_WINDOW,
            MlJob.target_id == target)
    queued = db.scalar(select(MlJob).where(*same, MlJob.status == JOB_QUEUED).limit(1))
    if queued is not None:
        return queued
    now = datetime.now(UTC)
    debounce = timedelta(minutes=max(0.0, float(_settings().monitoring_debounce_minutes)))
    available_at = None
    if debounce:
        busy = db.scalar(select(func.count()).select_from(MlJob).where(*same, or_(
            MlJob.status == JOB_RUNNING, MlJob.started_at > now - debounce)))
        available_at = now + debounce if busy else None
    return create_ml_job(
        db, workspace_id=workspace_id, project_id=project_id, job_type=JOB_TYPE_MONITORING,
        handler_key=HANDLER_MONITORING_COMPUTE_WINDOW, handler_version=HANDLER_VERSION_MONITORING_COMPUTE_WINDOW,
        target_id=target, available_at=available_at, max_attempts=1,
        payload={"project_id": str(project_id), "model_version_id": str(model_version_id),
                 "window_start": start.isoformat(), "reason": reason},
    )


def after_scoring_completed(db: Session, row: BatchPrediction) -> None:
    """The scoring worker's success hook (after its commit): queue the window of a completed
    scoring of the model in use. Never raises: a failed enqueue is logged, never a scoring failure."""

    prediction_id = None
    try:
        prediction_id = row.id
        if (not _settings().monitoring_enabled or row.status != PREDICTION_COMPLETED or row.completed_at is None
                or row.project_id is None):
            return
        if model_in_use(db, row.workspace_id, row.project_id) != row.model_version_id:
            return
        job = enqueue_window_job(db, workspace_id=row.workspace_id, project_id=row.project_id,
                                 model_version_id=row.model_version_id, start=week_start(row.completed_at),
                                 reason="scoring_completed")
        target_id, job_id = job.target_id, job.id
        db.commit()
        from app.services.job_dispatcher import get_job_dispatcher

        get_job_dispatcher().dispatch(target_id, job_id)  # postgres: a no-op (the worker claims the row)
    except Exception:  # noqa: BLE001 - monitoring never fails a scoring
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - a broken connection must not reach the scoring job either
            pass
        logger.warning("monitoring window not queued after batch prediction %s", prediction_id, exc_info=True)


# --- compute (worker) ------------------------------------------------------------------------


def _read_bytes(db: Session, artifact: Artifact, max_bytes: int, code: str) -> bytes:
    from app.services.artifact_service import ArtifactTooLargeError, read_artifact_bytes
    from app.storage._hashing import sha256_bytes

    try:
        data = read_artifact_bytes(db, workspace_id=artifact.workspace_id, artifact_id=artifact.id, max_bytes=max_bytes)
    except ArtifactTooLargeError as exc:
        raise MonitoringError(code, "a stored file is larger than monitoring reads", status_code=409) from exc
    if sha256_bytes(data) != artifact.content_digest:
        raise MonitoringError(code, "a stored file changed after it was written", status_code=409)
    return data


def _manifest(db: Session, model_version: ModelVersion) -> dict[str, Any] | None:
    from app.engine.serving.model_package import MANIFEST_MAX_BYTES

    artifact = _scoped(db, Artifact, model_version.workspace_id, model_version.feature_manifest_artifact_id)
    if artifact is None:
        return None  # a model saved before the package format: nothing to compare with
    try:
        manifest = json.loads(_read_bytes(db, artifact, MANIFEST_MAX_BYTES, dm.MANIFEST_UNREADABLE).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MonitoringError(dm.MANIFEST_UNREADABLE, "the model's manifest could not be read", status_code=409) from exc
    return manifest if isinstance(manifest, dict) else None


def _spec_input(result: Any) -> dict[str, Any]:
    """Only what the scoring replay plan reads (task, feature actions, missing-value plan,
    threshold, class labels); never evaluation results."""

    result = result if isinstance(result, dict) else {}
    evidence = result.get("scientific_evidence") if isinstance(result.get("scientific_evidence"), dict) else {}
    return {"task": result.get("task"), "decision_threshold": result.get("decision_threshold"),
            "class_labels": result.get("class_labels"),
            "scientific_evidence": {"feature_actions": evidence.get("feature_actions"),
                                    "missing_value_plan": evidence.get("missing_value_plan")}}


def _time_columns(result: Any) -> set[str]:
    """The run's time columns (the task's prediction time, the holdout's and the validation's time
    column): when one is a numeric input, monitoring shows it as a date and never counts it."""

    result = result if isinstance(result, dict) else {}
    names = [((result.get("task") or {}).get("prediction_time_column")),
             ((result.get("holdout_plan") or {}).get("time_column")),
             ((result.get("validation_plan") or {}).get("time_column"))]
    return {str(name) for name in names if isinstance(name, str) and name}


def _window_scorings(db: Session, workspace_id: UUID, project_id: UUID, version_id: UUID, start: datetime
                     ) -> tuple[list[BatchPrediction], int]:
    """(newest ``WINDOW_SCORINGS_MAX`` completed scorings of distinct input files, distinct files
    scored this week). A file scored again counts once: its newest scoring."""

    rows = db.scalars(select(BatchPrediction).where(
        BatchPrediction.workspace_id == workspace_id, BatchPrediction.project_id == project_id,
        BatchPrediction.model_version_id == version_id, BatchPrediction.status == PREDICTION_COMPLETED,
        BatchPrediction.completed_at >= start, BatchPrediction.completed_at < start + timedelta(days=7))
        .order_by(BatchPrediction.completed_at.desc(), BatchPrediction.id.desc())
        .limit(dm.WINDOW_SCORINGS_MAX * 25))
    seen: set[Any] = set()
    distinct = []
    for row in rows:
        if row.input_dataset_id not in seen:
            seen.add(row.input_dataset_id)
            distinct.append(row)
    return distinct[:dm.WINDOW_SCORINGS_MAX], len(distinct)


def _input_rows(db: Session, scorings: list[BatchPrediction], spec: Any, on_heartbeat: Callable[[], None] | None
                ) -> tuple[pd.DataFrame | None, int, int | None]:
    """(prepared pipeline-input rows of the window, unavailable files, sampled row count or None).
    Each file is prepared exactly as scoring prepared it; above ``WINDOW_ROWS_MAX`` rows are
    sampled per scoring with a seed fixed by the scoring id."""

    from app.engine.data.loaders import load_table
    from app.engine.serving.batch_scoring import ScoringError, prepare_features
    from app.services.dataset_materialization import materialize_dataset
    from app.services.experiment_service import ExperimentRequestError, uploaded_dataset

    total = sum(int(row.rows_out or 0) for row in scorings) or 1
    frames, missing, sampled = [], 0, False
    for row in scorings:
        try:
            dataset = uploaded_dataset(db, row.workspace_id, row.project_id, row.input_dataset_id)
            with materialize_dataset(dataset, db=db) as path:
                frame = load_table(path)
        except (ExperimentRequestError, IdentityError, OSError, ValueError):
            missing += 1  # unpublished or removed since it was scored
            continue
        frame.columns = [str(name) for name in frame.columns]
        quota = max(1, dm.WINDOW_ROWS_MAX * int(row.rows_out or len(frame)) // total)
        if len(frame) > quota:
            frame, sampled = frame.sample(n=quota, random_state=row.id.int % (2**32)).sort_index(), True
        try:
            prepared, _facts = prepare_features(frame, spec)
        except (ScoringError, KeyError, TypeError, ValueError) as exc:
            raise MonitoringError(dm.INPUTS_UNREADABLE, "a scored file could not be prepared as scoring prepared it",
                                  status_code=409) from exc
        frames.append(prepared)
        if on_heartbeat is not None:
            on_heartbeat()
    if not frames:
        return None, missing, None
    rows = pd.concat(frames, ignore_index=True)
    return rows, missing, len(rows) if sampled else None


def _key_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and value != value) or value is pd.NA:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def _fixed_sample(frame: pd.DataFrame, quota: int, key: str | None, seed: int) -> pd.DataFrame:
    """At most ``quota`` rows; by key when there is one (the same customers across files and
    recomputes: the smallest key hashes), else a seeded sample. Row order is kept."""

    if len(frame) <= quota:
        return frame
    if key and key in frame.columns:
        hashes = pd.util.hash_pandas_object(frame[key].map(_key_text), index=False).to_numpy()
        keep = np.sort(np.argsort(hashes, kind="stable")[:quota])
        return frame.iloc[keep]
    return frame.sample(n=quota, random_state=seed).sort_index()


def _scored_rows(db: Session, scorings: list[BatchPrediction], entity: str | None
                 ) -> tuple[list[pd.DataFrame], list[BatchPrediction]]:
    """(the needed columns of each scoring's stored output — the key, probability / prediction /
    label columns, never the reason text — newest first; the scorings read). Older files beyond
    ``WINDOW_BYTES_MAX`` are left out; rows beyond ``SCORED_ROWS_MAX`` are sampled by key."""

    wanted = set(_SCORE_COLUMNS) | ({entity} if entity else set())

    def needed(name: Any) -> bool:
        text = str(name)
        return (text in wanted or text.startswith("probability_")) and not _REASON_COLUMN.match(text)

    total = sum(int(row.rows_out or 0) for row in scorings) or 1
    frames, used, budget = [], [], dm.WINDOW_BYTES_MAX
    for row in scorings:
        artifact = _scoped(db, Artifact, row.workspace_id, row.output_artifact_id)
        if artifact is None:
            raise MonitoringError(dm.SCORES_UNREADABLE, "a scored file is missing", status_code=409)
        if used and int(artifact.size_bytes or 0) > budget:
            break  # older files past the byte budget are left out (the summary counts what was read)
        data = _read_bytes(db, artifact, dm.SCORED_FILE_MAX_BYTES, dm.SCORES_UNREADABLE)
        budget -= len(data)
        try:
            if row.output_format == "parquet":
                import pyarrow.parquet as pq

                names = pq.ParquetFile(io.BytesIO(data)).schema_arrow.names
                frame = pd.read_parquet(io.BytesIO(data), columns=[name for name in names if needed(name)])
            else:
                frame = pd.read_csv(io.BytesIO(data), usecols=needed)
        except Exception as exc:  # noqa: BLE001 - parser errors of a stored file: one plain code
            raise MonitoringError(dm.SCORES_UNREADABLE, "a scored file could not be read", status_code=409) from exc
        del data
        quota = max(1, dm.SCORED_ROWS_MAX * int(row.rows_out or len(frame)) // total)
        frames.append(_fixed_sample(frame, quota, entity, row.id.int % (2**32)).reset_index(drop=True))
        used.append(row)
    return frames, used


def _keyed_scores(frames: list[pd.DataFrame], entity: str) -> tuple[pd.DataFrame, pd.Series] | None:
    """One scored row per key across the window's scorings: a key scored again in a newer
    scoring (``frames`` are newest first) keeps only the newest score. None when a key repeats
    inside one scored file (that file cannot be joined)."""

    parts = [frame for frame in frames if entity in frame.columns]
    key_parts = [frame[entity].map(_key_text) for frame in parts]
    if any(keys.dropna().duplicated().any() for keys in key_parts):
        return None
    scored = pd.concat(parts, ignore_index=True)
    keys = pd.concat(key_parts, ignore_index=True)
    keep = (keys.notna() & ~keys.duplicated(keep="first")).to_numpy()
    return scored.loc[keep].reset_index(drop=True), keys.loc[keep].reset_index(drop=True)


def _read_columns(path: Any, columns: list[str], keys: set[str]) -> pd.DataFrame:
    """Only ``columns`` of an outcome file, only rows whose key (first column) is in ``keys``;
    CSV is read in chunks so a large file never sits in memory whole."""

    from app.engine.data.loaders import load_table

    location = Path(path)
    if location.is_dir():
        found = sorted(location.glob("*.csv")) + sorted(location.glob("*.parquet"))
        location = found[0] if found else location
    suffix = location.suffix.lower()

    def matching(frame: pd.DataFrame) -> pd.DataFrame:
        frame.columns = [str(name) for name in frame.columns]
        return frame.loc[frame[columns[0]].map(_key_text).isin(keys), columns]

    if suffix in {".csv", ".tsv", ".tab"}:
        chunks = pd.read_csv(location, sep="\t" if suffix != ".csv" else ",", usecols=lambda c: str(c) in columns,
                             chunksize=200_000)
        parts = [matching(chunk) for chunk in chunks]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=columns)
    if suffix in {".parquet", ".pq"}:
        return matching(pd.read_parquet(location, columns=columns))
    frame = load_table(location)
    return matching(frame.loc[:, [column for column in frame.columns if str(column) in columns]])


def _cv_reference(manifest: dict[str, Any] | None, metric: str | None, task_type: str) -> dict[str, Any] | None:
    """The CV mean of the primary metric, when comparable: threshold-free metrics and regression
    (a binary threshold metric's CV mean is not at the locked threshold, so it is left out)."""

    if not metric or (task_type == "binary" and metric in _THRESHOLD_METRICS):
        return None
    value = (((manifest or {}).get("cv_metrics") or {}).get("mean") or {}).get(metric)
    return {"metric": metric, "value": value} if value is not None else None


def _outcomes(db: Session, *, workspace_id: UUID, project_id: UUID, frames: list[pd.DataFrame],
              entity: str | None, task_type: str, manifest: dict[str, Any] | None, class_labels: Any,
              reference: dict[str, Any] | None) -> dict[str, Any]:
    """Join the newest linked outcome file whose joined rows are measured (D5b: a small newer file
    never replaces a measured older one; without any measured file, the newest file that shares keys
    with this window's scored rows; a file whose joining keys repeat is skipped for the next). Real production outcomes only (never
    the final test set); metrics on joined rows only; ``coverage`` is labelled rows / scored
    customers with a key. Binary outcomes count only in the training target's own spellings."""

    from app.engine.serving.monitoring import binary_outcome_codes, multiclass_outcome_codes, outcome_metrics
    from app.services.dataset_materialization import materialize_dataset
    from app.services.experiment_service import ExperimentRequestError, uploaded_dataset

    sets = list(db.scalars(select(MonitoringOutcomeSet).where(
        MonitoringOutcomeSet.workspace_id == workspace_id, MonitoringOutcomeSet.project_id == project_id)
        .order_by(MonitoringOutcomeSet.created_at.desc(), MonitoringOutcomeSet.id.desc())
        .limit(dm.OUTCOME_SETS_TRIED)))
    n_scored = int(sum(len(frame) for frame in frames))
    if not sets:
        return {"status": "not_linked", "n_scored": n_scored}
    if not entity or _REASON_COLUMN.match(entity) or not any(entity in frame.columns for frame in frames):
        return {"status": "no_key_in_scores", "n_scored": n_scored}
    keyed = _keyed_scores(frames, entity)
    if keyed is None:
        return {"status": "key_not_unique", "side": "scores", "n_scored": n_scored}
    scored, keys = keyed
    n_keyed = int(len(keys))
    key_set = set(keys)
    settings = _settings()
    metric = ((manifest or {}).get("cv_metrics") or {}).get("metric")
    metric = metric if isinstance(metric, str) else None
    threshold = (manifest or {}).get("decision_threshold")
    target = (reference or {}).get("target") if task_type == "binary" else None
    refused: list[dict[str, str]] = []
    fallback: dict[str, Any] | None = None
    for outcome_set in sets:
        columns = [outcome_set.key_column, outcome_set.outcome_column]
        try:
            dataset = uploaded_dataset(db, workspace_id, project_id, outcome_set.dataset_id)
            with materialize_dataset(dataset, db=db) as path:
                labels = _read_columns(path, columns, key_set)
        except (ExperimentRequestError, IdentityError, OSError, ValueError, KeyError):
            continue  # the file or its columns are no longer available
        if labels.empty:
            continue
        label_keys = labels[outcome_set.key_column].map(_key_text)
        if label_keys.duplicated().any():  # a joining key repeats: ambiguous, try the next file
            refused.append({"outcome_set_id": str(outcome_set.id), "reason_code": "key_not_unique"})
            continue
        labels = _fixed_sample(labels.assign(_key=label_keys.to_numpy()), dm.OUTCOME_ROWS_MAX, "_key", 0)
        values, unrecognised = labels[outcome_set.outcome_column], 0
        if task_type == "binary":
            values, unrecognised = binary_outcome_codes(values, target)
        elif task_type == "multiclass":  # counted here; the metrics map the same way
            unrecognised = multiclass_outcome_codes(values, list(class_labels or []))[1]
        outcome_by_key = dict(zip(labels["_key"], values))
        mask = keys.isin(set(labels["_key"])).to_numpy()
        result = outcome_metrics(
            task_type, keys.loc[mask].reset_index(drop=True).map(outcome_by_key),
            scored.loc[mask].reset_index(drop=True), metric=metric,
            threshold=float(threshold) if threshold is not None else None, class_labels=class_labels,
            min_labels=int(settings.monitoring_min_labels), min_per_class=int(settings.monitoring_min_per_class),
        )
        out = {"outcome_set_id": str(outcome_set.id), "n_scored": n_scored, "n_keyed": n_keyed, **result,
               "coverage": round(result["n_labelled"] / n_keyed, 4) if n_keyed else None,
               "cv_reference": _cv_reference(manifest, metric, task_type),
               "unrecognised_outcome_values": int(unrecognised), "refused_sets": refused}
        if task_type == "binary":  # which words counted as outcomes
            out["outcome_spellings"] = ("training_labels" if target and target.get("positive")
                                        and target.get("negative") else "yes_no_tokens")
        if target and target.get("positive_rate") is not None:
            out["positive_rate_reference"] = target["positive_rate"]
        if out.get("status") == "measured":
            return out
        fallback = fallback or out  # the newest non-empty file, used when none is measured
    if fallback is not None:
        return {**fallback, "refused_sets": refused}
    if refused:
        return {"status": "key_not_unique", "side": "outcomes", "n_scored": n_scored, "n_keyed": n_keyed,
                "refused_sets": refused}
    return {"status": "not_enough_labels", "n_labelled": 0, "n_scored": n_scored, "n_keyed": n_keyed,
            "outcome_sets_tried": len(sets)}


def _bounded(content: dict[str, Any]) -> dict[str, Any]:
    """JSON-normalized window content; each jsonb part within ``JSON_MAX_BYTES`` (named code)."""

    normal = json.loads(json.dumps(content, default=str, allow_nan=False))
    for column in ("input_drift", "prediction_drift", "outcomes"):
        if len(json.dumps(normal[column], ensure_ascii=False).encode("utf-8")) > dm.JSON_MAX_BYTES - 1024:
            raise MonitoringError(dm.RESULT_TOO_LARGE, "the window's result is larger than monitoring stores",
                                  status_code=409)
    return normal


def compute_window_job(db: Session, job: MlJob, *, on_heartbeat: Callable[[], None] | None = None) -> None:
    """Worker handler body: compute one window and insert its next revision (only when the
    result changed). A failure raises ``MonitoringError`` with a plain code (anything unexpected
    is ``monitoring_failed``; its cause is logged server-side only) and writes nothing."""

    job_id = job.id
    try:
        _compute_window(db, job, on_heartbeat)
    except MonitoringError:
        raise
    except Exception as exc:
        from app.engine.serving.model_package import failure_chain

        logger.warning("monitoring window job %s failed: %s", job_id, failure_chain(exc))
        raise MonitoringError(dm.MONITORING_FAILED, "the monitoring window could not be computed",
                              status_code=500) from exc


def _compute_window(db: Session, job: MlJob, on_heartbeat: Callable[[], None] | None) -> None:
    from app.engine.serving.batch_scoring import ScoringError, contract_spec
    from app.engine.serving.monitoring import (
        compare_inputs,
        compare_predictions,
        floor_checks,
        floors_below,
        manifest_contract,
        usable_reference,
        window_verdict,
    )

    settings = _settings()
    if not settings.monitoring_enabled:
        return
    payload = job.payload or {}
    workspace_id = job.workspace_id
    project_id, version_id = UUID(str(payload["project_id"])), UUID(str(payload["model_version_id"]))
    start = week_start(datetime.fromisoformat(str(payload["window_start"])))
    end = start + timedelta(days=7)
    model_version = _scoped(db, ModelVersion, workspace_id, version_id)
    if model_version is None or model_version.project_id != project_id:
        return
    chosen, scorings_total = _window_scorings(db, workspace_id, project_id, version_id, start)
    if not chosen:
        return
    experiment = _scoped(db, Experiment, workspace_id, model_version.pipeline_run_id)
    spec_input = _spec_input(experiment.result if experiment is not None else None)
    task = spec_input["task"] if isinstance(spec_input["task"], dict) else {}
    entity = str(task["entity_id"]) if task.get("entity_id") else None
    manifest = _manifest(db, model_version)
    reference = usable_reference(manifest)
    limits = _window_limits(db, workspace_id, project_id, version_id, start, end,
                            newest_scoring=max(row.completed_at for row in chosen), moment=_job_time(job))
    lines = {"psi_watch": limits["psi_watch"], "psi_alert": limits["psi_alert"]}
    frames, scorings = _scored_rows(db, chosen, entity)
    scored = pd.concat(frames, ignore_index=True)
    n_rows = sum(int(row.rows_out or 0) for row in scorings)
    min_rows = int(settings.monitoring_min_rows)
    sampled = None
    if reference is None:
        input_drift: dict[str, Any] = {"available": False, "reason_code": "no_reference"}
        prediction_drift: dict[str, Any] = {"available": False, "reason_code": "no_reference"}
    else:
        try:
            spec = contract_spec(spec_input, manifest_contract(manifest))
        except (ScoringError, KeyError, TypeError, ValueError) as exc:
            raise MonitoringError(dm.INPUTS_UNREADABLE, "the model's preparation cannot be replayed",
                                  status_code=409) from exc
        rows, unavailable, sampled = _input_rows(db, scorings, spec, on_heartbeat)
        dates = sorted(set(spec.date_formats) | set(spec.legacy_date_columns)
                       | _time_columns(experiment.result if experiment is not None else None))
        input_drift = ({"available": False, "reason_code": "inputs_unavailable"} if rows is None
                       else {**compare_inputs(rows, reference, date_columns=dates, **lines),
                             "files_unavailable": unavailable})
        prediction_drift = compare_predictions(scored, reference.get("prediction"), min_rows=min_rows, **lines)
    task_type = str(task.get("task_type") or "")
    outcomes = _outcomes(db, workspace_id=workspace_id, project_id=project_id, frames=frames, entity=entity,
                         task_type=task_type, manifest=manifest,
                         class_labels=spec_input["class_labels"], reference=reference)
    checks = floor_checks(outcomes, task_type, {"precision": limits["precision_floor"],
                                                "recall": limits["recall_floor"]},
                          min_trials=int(settings.monitoring_min_per_class))
    if checks is not None:  # only when a floor is set (D5b): real outcomes, measured and judged
        outcomes = {**outcomes, "floors": checks}
    status, code = window_verdict(min_rows, input_drift, prediction_drift)
    summary = dm.summary_text(code, n_rows=n_rows, min_rows=min_rows, input_drift=input_drift,
                              prediction_drift=prediction_drift, sampled_rows=sampled,
                              scorings_total=max(scorings_total, len(scorings)))
    if floors_below(checks):  # a floor judged below is an alert; it is not drift unless drift alerted too
        summary = f"{summary} {dm.floor_summary(checks)}"[:dm.SUMMARY_MAX_CHARS]
        code = code if status == "alert" else dm.BELOW_FLOOR
        status = "alert"
    release_id = next((row.model_release_id for row in scorings if row.model_release_id is not None), None)
    content = _bounded({"model_release_id": release_id, "window_end": end, "n_rows": n_rows,
                        "n_batch_predictions": len(scorings), "input_drift": input_drift,
                        "prediction_drift": prediction_drift, "outcomes": outcomes, "status": status,
                        "summary_code": code, "summary": summary, "thresholds_used": limits}
                       ) | {"model_release_id": release_id, "window_end": end}
    db.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended(f"monitoring-window:{project_id}:{version_id}:{start.isoformat()}", 0))))
    current, _total = _window_scorings(db, workspace_id, project_id, version_id, start)
    if [row.id for row in current] != [row.id for row in chosen]:
        db.commit()  # scorings landed meanwhile: the job they queued computes the window instead
        logger.info("monitoring window %s/%s: scorings changed during the computation; left to the next job",
                    project_id, start.date())
        return
    latest = db.scalar(select(MonitoringWindow).where(
        MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
        MonitoringWindow.model_version_id == version_id, MonitoringWindow.window_start == start)
        .order_by(MonitoringWindow.revision.desc()).limit(1))
    if latest is not None and all(getattr(latest, key) == value for key, value in content.items()
                                  if key != "thresholds_used") and (
            (latest.thresholds_used or default_limits()) == content["thresholds_used"]):
        db.commit()
        return  # same inputs, same result: no new revision (deterministic rerun)
    db.add(MonitoringWindow(workspace_id=workspace_id, project_id=project_id, model_version_id=version_id,
                            window_start=start, revision=(latest.revision + 1) if latest is not None else 1,
                            **content))
    if status == "alert":  # P8.8-A: once per window (deduped), in this transaction; never raises
        from app.services.notification_service import notify_drift_alert

        notify_drift_alert(db, workspace_id=workspace_id, project_id=project_id, model_version_id=version_id,
                           window_start=start, code=code)
    db.commit()


# --- outcome links (API) ----------------------------------------------------------------------


def link_outcomes(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID,
    request: dm.OutcomeSetCreateRequest,
    before_commit: Callable[[MonitoringOutcomeSet], None] | None = None,
) -> MonitoringOutcomeSet:
    """Link an uploaded file of real outcomes (key column, outcome column) to the project, write
    its accepted ``monitoring_outcomes_linked`` History entry and queue a recompute of the model
    in use's recent weeks (those with a window or a completed scoring). People with ML-write
    only (the route refuses tokens); the same link twice is ``409 already_linked``; at most
    ``LINKS_PER_HOUR`` links per project and hour (``429 rate_limited``). Commits. Raises
    ``IdentityError``, ``ProjectNotFoundError``, ``ExperimentRequestError`` (dataset) and
    ``MonitoringError``."""

    from app.services import decision_record_service as drs
    from app.services.experiment_service import uploaded_dataset
    from app.services.job_dispatcher import get_job_dispatcher
    from app.services.project_service import get_project

    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError("linking outcomes requires an ML-write workspace role", status_code=403)
    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    human = DecisionActor.human(actor)
    drs.authorize_writer(db, human, workspace_id=workspace_id, allow_rule=False)
    if not _settings().monitoring_enabled:
        raise MonitoringError(dm.MONITORING_DISABLED, "monitoring is switched off on this server", status_code=409)
    dataset = uploaded_dataset(db, workspace_id, project_id, request.dataset_id)
    sources = select(Experiment.source_dataset_id).where(
        Experiment.workspace_id == workspace_id, Experiment.project_id == project_id,
        Experiment.source_dataset_id.is_not(None))
    digests = select(Dataset.content_digest).where(Dataset.workspace_id == workspace_id, Dataset.id.in_(sources))
    clash = db.scalar(select(func.count()).select_from(Dataset).where(
        Dataset.id == dataset.id, or_(Dataset.id.in_(sources), Dataset.content_digest.in_(digests))))
    if clash:
        raise MonitoringError(dm.OUTCOMES_FROM_TRAINING_DATA, "this file trained a model of this project; link real "
                              "outcomes of scored rows instead", status_code=409)
    names = set(db.scalars(select(DatasetColumn.name).where(
        DatasetColumn.workspace_id == workspace_id, DatasetColumn.dataset_id == dataset.id)))
    for field, column in (("key_column", request.key_column), ("outcome_column", request.outcome_column)):
        if column not in names:
            raise MonitoringError(f"{field}_unknown", f"the file has no column named in {field}", status_code=422)
    if request.key_column == request.outcome_column:
        raise MonitoringError("columns_must_differ", "the key and outcome columns must differ", status_code=422)
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"monitoring-link:{project_id}", 0))))
    project_sets = (MonitoringOutcomeSet.workspace_id == workspace_id, MonitoringOutcomeSet.project_id == project_id)
    if db.scalar(select(func.count()).select_from(MonitoringOutcomeSet).where(
            *project_sets, MonitoringOutcomeSet.dataset_id == dataset.id,
            MonitoringOutcomeSet.key_column == request.key_column,
            MonitoringOutcomeSet.outcome_column == request.outcome_column)):
        raise MonitoringError(dm.ALREADY_LINKED, "this file is already linked with these columns", status_code=409)
    recent = db.scalar(select(func.count()).select_from(MonitoringOutcomeSet).where(
        *project_sets, MonitoringOutcomeSet.created_at > func.now() - timedelta(hours=1)))
    if int(recent or 0) >= dm.LINKS_PER_HOUR:
        raise MonitoringError("rate_limited", "too many outcome links for this project in the last hour; retry later",
                              status_code=429)
    row = MonitoringOutcomeSet(workspace_id=workspace_id, project_id=project_id, dataset_id=dataset.id,
                               key_column=request.key_column, outcome_column=request.outcome_column,
                               linked_by_user_id=actor.id)
    db.add(row)
    db.flush()
    facts = {"dataset_id": str(dataset.id), "key_column": request.key_column,
             "outcome_column": request.outcome_column, "rationale_default": True}
    drs.insert_record(db, drs.build_record(
        workspace_id=workspace_id, project_id=project_id, actor=human,
        decision_type=DECISION_MONITORING_OUTCOMES_LINKED, state=STATE_ACCEPTED, subject_kind="project",
        subject_id=None, rationale=drs.clean_rationale(_LINK_RATIONALE),
        facts=drs.clean_json_object(facts, label="facts"), evidence_refs=[],
        details=drs.clean_json_object({"monitoring_outcome_set_id": str(row.id)}, label="details"),
    ))
    if before_commit is not None:
        before_commit(row)
    jobs = []
    version_id = model_in_use(db, workspace_id, project_id)
    if version_id is not None:
        jobs = [enqueue_window_job(db, workspace_id=workspace_id, project_id=project_id, model_version_id=version_id,
                                   start=start, reason="outcomes_linked")
                for start in _recent_weeks(db, workspace_id, project_id, version_id)]
    pending = [(job.target_id, job.id) for job in jobs]
    db.commit()
    for target_id, job_id in pending:
        get_job_dispatcher().dispatch(target_id, job_id)
    return row


def _recent_weeks(db: Session, workspace_id: UUID, project_id: UUID, version_id: UUID) -> list[datetime]:
    """The newest ``WINDOWS_LISTED`` weeks of this version with a window or a completed scoring."""

    windows = db.scalars(select(MonitoringWindow.window_start).where(
        MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
        MonitoringWindow.model_version_id == version_id).distinct()
        .order_by(MonitoringWindow.window_start.desc()).limit(dm.WINDOWS_LISTED))
    completed = db.scalars(select(BatchPrediction.completed_at).where(
        BatchPrediction.workspace_id == workspace_id, BatchPrediction.project_id == project_id,
        BatchPrediction.model_version_id == version_id, BatchPrediction.status == PREDICTION_COMPLETED,
        BatchPrediction.completed_at.is_not(None))
        .order_by(BatchPrediction.completed_at.desc()).limit(dm.WINDOWS_LISTED * dm.WINDOW_SCORINGS_MAX))
    weeks = {week_start(moment) for moment in completed} | {week_start(moment) for moment in windows}
    return sorted(weeks, reverse=True)[:dm.WINDOWS_LISTED]


# --- thresholds (D5b, ADR 0012 D4) ------------------------------------------------------------

_THRESHOLD_VALUES = ("psi_watch", "psi_alert", "precision_floor", "recall_floor")
_THRESHOLDS_NOTES = (
    "A change applies to weeks computed with new scored data after it; past weeks, and a week without new "
    "scorings, keep the values they were computed with (also when outcomes linked later recompute them).",
    "Precision and recall floors apply to yes/no models with linked real outcomes; a week is below a floor only "
    "when its labelled rows say so clearly (a one-sided binomial test), otherwise it is not judged.",
)


def default_limits() -> dict[str, Any]:
    """Version 0: the engine's PSI lines and no floors (what every window used before D5b)."""

    from app.engine.serving.monitoring import PSI_ALERT, PSI_WATCH

    return {"version": 0, "psi_watch": PSI_WATCH, "psi_alert": PSI_ALERT, "precision_floor": None,
            "recall_floor": None}


def _threshold_row(db: Session, workspace_id: UUID, project_id: UUID) -> MonitoringThresholdSet | None:
    return db.scalar(select(MonitoringThresholdSet).where(
        MonitoringThresholdSet.workspace_id == workspace_id, MonitoringThresholdSet.project_id == project_id)
        .order_by(MonitoringThresholdSet.version.desc()).limit(1))


def _limits(row: MonitoringThresholdSet | None) -> dict[str, Any]:
    if row is None:
        return default_limits()
    return {"version": row.version, **{key: getattr(row, key) for key in _THRESHOLD_VALUES}}


def _job_time(job: MlJob) -> datetime:
    return job.created_at or _now()


def _window_limits(db: Session, workspace_id: UUID, project_id: UUID, version_id: UUID, start: datetime,
                   end: datetime, *, newest_scoring: datetime, moment: datetime) -> dict[str, Any]:
    """The values a computation of this window uses. A window that already has a revision keeps the
    values stored on its newest one (the defaults when computed before they were stored) when the
    week had ended when the job was queued (``moment``) or no scoring of it completed since that
    revision: a later change never alters a past week, nor an open week's result without new scored
    data (also when linked outcomes recompute it). Otherwise (new scored data, or a first
    computation) the project's values in force now."""

    stored = db.execute(select(MonitoringWindow.thresholds_used, MonitoringWindow.computed_at).where(
        MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
        MonitoringWindow.model_version_id == version_id, MonitoringWindow.window_start == start)
        .order_by(MonitoringWindow.revision.desc()).limit(1)).first()
    if stored is not None and (end <= moment or newest_scoring <= stored.computed_at):
        used = _parsed_used(stored.thresholds_used)  # only the five known keys, validated
        return used.model_dump() if used is not None else default_limits()
    return _limits(_threshold_row(db, workspace_id, project_id))


def can_edit_thresholds(db: Session, user: User, workspace_id: UUID) -> bool:
    """A workspace owner or administrator, never platform staff (the retrain policy's rule)."""

    from app.services.retrain_service import can_set_policy

    return can_set_policy(db, user, workspace_id)


def thresholds_read(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID,
                    agent: bool) -> dm.MonitoringThresholdsRead:
    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    row = _threshold_row(db, workspace_id, project_id)
    limits, defaults = _limits(row), default_limits()
    return dm.MonitoringThresholdsRead(
        project_id=project_id, version=limits["version"], etag=f'"{limits["version"]}"',
        **{key: limits[key] for key in _THRESHOLD_VALUES},
        defaults=dm.MonitoringThresholdValues(**{key: defaults[key] for key in _THRESHOLD_VALUES}),
        bounds=dm.MonitoringThresholdBounds(),
        changed_at=row.created_at if row is not None else None,
        changed_by_user_id=None if agent or row is None else row.created_by_user_id,
        can_edit=not agent and can_edit_thresholds(db, actor, workspace_id), notes=list(_THRESHOLDS_NOTES))


def set_thresholds(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, request: dm.MonitoringThresholdsWrite,
    precondition: Callable[[int], None],
) -> ProjectDecisionRecord:
    """A workspace owner or administrator saves the next version with a reason
    (``precondition(version)`` checks If-Match under the project lock) and its accepted
    ``monitoring_thresholds_changed`` record. Weeks computed after it use it; nothing is recomputed.
    The same values again are ``409 thresholds_unchanged``. Does not commit."""

    from app.services import decision_record_service as drs

    human = DecisionActor.human(actor)
    drs.authorize_writer(db, human, workspace_id=workspace_id, allow_rule=False)
    if not can_edit_thresholds(db, actor, workspace_id):
        raise IdentityError("only a workspace owner or administrator can change the monitoring thresholds",
                            status_code=403)
    drs.load_project(db, workspace_id=workspace_id, project_id=project_id)
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"monitoring-thresholds:{project_id}", 0))))
    current = _limits(_threshold_row(db, workspace_id, project_id))
    precondition(current["version"])
    values = {key: getattr(request, key) for key in _THRESHOLD_VALUES}
    previous = {key: current[key] for key in _THRESHOLD_VALUES}
    if values == previous:
        raise MonitoringError(dm.THRESHOLDS_UNCHANGED, "these are already the thresholds in force", status_code=409)
    row_id, version = uuid4(), current["version"] + 1
    facts = {"version": version, "values": values, "previous": previous}
    record = drs.insert_record(db, drs.build_record(
        workspace_id=workspace_id, project_id=project_id, actor=human,
        decision_type=DECISION_MONITORING_THRESHOLDS_CHANGED, state=STATE_ACCEPTED, subject_kind="project",
        subject_id=None, rationale=drs.clean_rationale(request.rationale),
        facts=drs.clean_json_object(facts, label="facts"), evidence_refs=[],
        details=drs.clean_json_object({"monitoring_thresholds_id": str(row_id)}, label="details")))
    db.add(MonitoringThresholdSet(id=row_id, workspace_id=workspace_id, project_id=project_id, version=version,
                                  decision_record_id=record.id, created_by_user_id=actor.id, **values))
    db.flush()
    return record


# --- reads (API) ------------------------------------------------------------------------------


def thresholds(row: MonitoringThresholdSet | None = None, *, agent: bool = True) -> dm.MonitoringThresholds:
    """The values in force (``row``: the project's newest version; None = the defaults) and the
    code-owned rest. ``agent=True`` drops who changed them."""

    from app.engine.serving.monitoring import FLAGGED_ABS_WATCH, FLAGGED_SHIFT_WATCH, SIGNIFICANCE

    settings = _settings()
    limits = _limits(row)
    return dm.MonitoringThresholds(
        psi_watch=limits["psi_watch"], psi_alert=limits["psi_alert"], precision_floor=limits["precision_floor"],
        recall_floor=limits["recall_floor"], version=limits["version"],
        changed_at=row.created_at if row is not None else None,
        changed_by_user_id=None if agent or row is None else row.created_by_user_id,
        significance_level=SIGNIFICANCE,
        flagged_share_relative_watch=FLAGGED_SHIFT_WATCH, flagged_share_absolute_watch=FLAGGED_ABS_WATCH,
        min_rows=int(settings.monitoring_min_rows), min_labels=int(settings.monitoring_min_labels),
        min_per_class=int(settings.monitoring_min_per_class))


def _parsed_used(value: Any) -> dm.MonitoringThresholdsUsed | None:
    stored = value if isinstance(value, dict) else None
    try:
        return dm.MonitoringThresholdsUsed(**stored) if stored else None
    except ValueError:  # never fail on a stored value
        return None


def _used(row: MonitoringWindow) -> dm.MonitoringThresholdsUsed | None:
    return _parsed_used(row.thresholds_used)


def _summary(row: MonitoringWindow) -> dm.MonitoringWindowSummary:
    outcomes = row.outcomes or {}
    precision, recall = dm.outcome_rates(outcomes)  # V7 B7: stored at compute time, read here
    return dm.MonitoringWindowSummary(
        thresholds_used=_used(row), floor_status=dm.floor_status(outcomes),
        window_start=row.window_start, window_end=row.window_end, revision=row.revision,
        model_version_id=row.model_version_id, model_release_id=row.model_release_id, status=row.status,
        summary_code=row.summary_code, summary=row.summary, n_rows=row.n_rows,
        n_batch_predictions=row.n_batch_predictions, max_psi=(row.input_drift or {}).get("max_psi"),
        prediction_psi=(row.prediction_drift or {}).get("psi"), outcomes_status=str(outcomes.get("status") or ""),
        n_labelled=outcomes.get("n_labelled"), coverage=outcomes.get("coverage"),
        outcome_metric=outcomes.get("metric"), outcome_metric_value=outcomes.get("metric_value"),
        outcome_precision=precision, outcome_recall=recall, computed_at=row.computed_at)


def _version(db: Session, actor: User, workspace_id: UUID, project_id: UUID,
             model_version_id: UUID | None) -> ModelVersion | None:
    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    version_id = model_version_id or model_in_use(db, workspace_id, project_id)
    model = _scoped(db, ModelVersion, workspace_id, version_id)
    if model_version_id is not None and (model is None or model.project_id != project_id):
        raise MonitoringError("model_version_not_found", "model version not found", status_code=404)
    return model


def _windows(db: Session, workspace_id: UUID, project_id: UUID, version_id: UUID, *,
             start: datetime | None = None) -> list[MonitoringWindow]:
    """Highest revision per window, newest window first (last ``WINDOWS_LISTED``)."""

    query = select(MonitoringWindow).where(
        MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
        MonitoringWindow.model_version_id == version_id)
    if start is not None:
        query = query.where(MonitoringWindow.window_start == start)
    return list(db.scalars(query.ext(distinct_on(MonitoringWindow.window_start))
                           .order_by(MonitoringWindow.window_start.desc(), MonitoringWindow.revision.desc())
                           .limit(dm.WINDOWS_LISTED)))


def monitoring_overview(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID,
                        model_version_id: UUID | None = None, agent: bool = True) -> dm.MonitoringOverviewRead:
    from app.services.model_release_service import current_release

    model = _version(db, actor, workspace_id, project_id, model_version_id)
    rows = _windows(db, workspace_id, project_id, model.id) if model is not None else []
    latest = rows[0] if rows else None
    release = current_release(db, workspace_id=workspace_id, project_id=project_id)
    finding = (dm.finding_for(latest.status, latest.summary, latest.input_drift, latest.prediction_drift,
                              latest.outcomes) if latest is not None else None)
    status = latest.status if latest is not None else "no_windows" if model is not None else "no_model_in_use"
    return dm.MonitoringOverviewRead(
        project_id=project_id, enabled=bool(_settings().monitoring_enabled),
        model_version_id=model.id if model is not None else None,
        model_version=model.version if model is not None else None,
        model_release_id=release.id if release is not None and model is not None
        and release.model_version_id == model.id else None,
        status=status, latest=_summary(latest) if latest is not None else None,
        windows=[_summary(row) for row in rows],
        feature_psi_series=dm.feature_psi_series([(row.window_start, row.input_drift or {}) for row in rows]),
        finding=finding, thresholds=thresholds(_threshold_row(db, workspace_id, project_id), agent=agent),
        notes=list(dm.NOTES))


def parse_window_start(value: str) -> datetime | None:
    """A window's Monday (``YYYY-MM-DD`` or an ISO timestamp at Monday 00:00 UTC), else None."""

    try:
        moment = datetime.fromisoformat(value.strip()[:40])
    except ValueError:
        return None
    moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    start = week_start(moment)
    return start if start == moment.astimezone(UTC) else None


def monitoring_window(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, window_start: str,
                      model_version_id: UUID | None = None, agent: bool = True) -> dm.MonitoringWindowRead | None:
    from app.engine.serving.monitoring import category_label

    model = _version(db, actor, workspace_id, project_id, model_version_id)
    start = parse_window_start(window_start)
    rows = _windows(db, workspace_id, project_id, model.id, start=start) if model is not None and start else []
    if not rows:
        return None
    row = rows[0]
    features = list((row.input_drift or {}).get("features") or [])
    outcomes = copy.deepcopy(row.outcomes or {})  # never edit the loaded row's value
    if (outcomes.get("cv_reference") or {}).get("metric"):
        outcomes["cv_reference"]["metric"] = category_label(outcomes["cv_reference"]["metric"])
    limits = thresholds(_threshold_row(db, workspace_id, project_id), agent=agent)  # in force now
    used = _used(row)  # this week's own values: its sentences use them
    watch = used.psi_watch if used is not None else default_limits()["psi_watch"]
    return dm.MonitoringWindowRead(
        **_summary(row).model_dump(), input_drift=row.input_drift, prediction_drift=row.prediction_drift,
        outcomes=outcomes, feature_sentences=[dm.feature_sentence(feature, watch) for feature in features],
        outcome_sentences=dm.outcome_sentences(outcomes, limits),
        finding=dm.finding_for(row.status, row.summary, row.input_drift, row.prediction_drift, row.outcomes),
        thresholds=limits, notes=list(dm.NOTES))


def list_outcome_sets(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID,
                      agent: bool) -> dm.OutcomeSetList:
    """Linked outcome files, newest first. ``agent=True`` (service tokens) drops who linked them."""

    from app.services.graph_service import untrusted_text
    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    rows = db.execute(
        select(MonitoringOutcomeSet, Dataset.name)
        .join(Dataset, (Dataset.id == MonitoringOutcomeSet.dataset_id) & (Dataset.workspace_id == workspace_id))
        .where(MonitoringOutcomeSet.workspace_id == workspace_id, MonitoringOutcomeSet.project_id == project_id)
        .order_by(MonitoringOutcomeSet.created_at.desc(), MonitoringOutcomeSet.id.desc())
        .limit(dm.OUTCOME_SETS_LISTED)).all()
    return dm.OutcomeSetList(project_id=project_id, notes=list(dm.NOTES[1:]), items=[
        outcome_set_read(row, untrusted_text(name, 128), agent=agent) for row, name in rows])


def outcome_set_read(row: MonitoringOutcomeSet, dataset_name: str | None, *, agent: bool) -> dm.OutcomeSetRead:
    return dm.OutcomeSetRead(
        id=row.id, project_id=row.project_id, dataset_id=row.dataset_id, dataset_name=dataset_name,
        key_column=row.key_column, outcome_column=row.outcome_column, linked_at=row.created_at,
        linked_by_user_id=None if agent else row.linked_by_user_id)
