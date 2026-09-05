"""Persist datasets, tasks, and experiment runs for the Lab."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Dataset,
    DatasetAsset,
    DatasetProfile,
    Environment,
    Experiment,
    ExperimentTestPrediction,
    PredictionTask,
    WorkflowRun,
)
from app.engine.data.loaders import infer_schema, load_table
from app.engine.datasets.synthetic import make_synthetic_customers
from app.engine.experiments.runner import run_experiment
from app.engine.schema.profiler import profile_frame
from app.engine.serving.artifacts import experiment_dir
from app.engine.types import SearchConfig, TaskSpec

logger = logging.getLogger(__name__)

DOGFOOD_ORG = "dclab"
DOGFOOD_NAME = "DCLab Internal Dogfood"


def _git_hash() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def seed_dogfood(db: Session) -> Environment:
    existing = db.scalars(
        select(Environment).where(Environment.org_id == DOGFOOD_ORG, Environment.name == DOGFOOD_NAME)
    ).first()
    if existing:
        return existing
    env = Environment(org_id=DOGFOOD_ORG, name=DOGFOOD_NAME)
    db.add(env)
    db.commit()
    db.refresh(env)
    return env


def ingest_dataset(
    db: Session,
    *,
    environment: Environment,
    name: str,
    location: str,
    source_type: str = "csv",
    version: str = "v1",
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    dataset_asset: DatasetAsset | None = None,
    created_by: UUID | None = None,
    project_id: UUID | None = None,
    ingestion_run_id: UUID | None = None,
    artifact_id: UUID | None = None,
) -> Dataset:
    frame = load_table(location)
    schema = infer_schema(frame)
    if dataset_asset is not None and dataset_asset.workspace_id != workspace_id:
        raise ValueError("dataset asset belongs to another workspace")
    if dataset_asset is None:
        from app.services.lineage_service import slugify

        dataset_asset = DatasetAsset(
            workspace_id=workspace_id,
            project_id=project_id,
            name=name,
            slug=f"{slugify(name)}-{uuid4().hex[:8]}",
            created_by=created_by,
        )
        db.add(dataset_asset)
        db.flush()
    digest = hashlib.sha256(Path(location).read_bytes()).hexdigest()
    size_bytes = Path(location).stat().st_size
    from app.services.dataset_column_service import persist_dataset_columns, schema_digest_from_columns

    schema_digest = schema_digest_from_columns(list((schema or {}).get("columns") or []))
    row = Dataset(
        workspace_id=workspace_id,
        dataset_asset_id=dataset_asset.id,
        environment_id=environment.id,
        project_id=project_id if project_id is not None else dataset_asset.project_id,
        ingestion_run_id=ingestion_run_id,
        artifact_id=artifact_id,
        name=name,
        source_type=source_type,
        location=str(location),
        version=version,
        content_digest=digest,
        schema_digest=schema_digest,
        size_bytes=size_bytes,
        schema_json=schema,
        row_count=int(len(frame)),
        column_count=int(frame.shape[1]),
    )
    db.add(row)
    db.flush()
    persist_dataset_columns(
        db,
        workspace_id=workspace_id,
        dataset_id=row.id,
        schema=schema,
        frame=frame,
    )
    db.commit()
    db.refresh(row)
    return row


def profile_dataset(db: Session, dataset: Dataset) -> DatasetProfile:
    frame = load_table(dataset.location)
    stats = profile_frame(frame)
    profile = DatasetProfile(dataset_id=dataset.id, stats=stats)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def search_from_mapping(raw: dict | None, *, overrides: dict | None = None) -> SearchConfig:
    payload = dict(raw or {})
    payload.update({key: value for key, value in (overrides or {}).items() if value is not None})
    allowed = {item.name for item in fields(SearchConfig)}
    return SearchConfig(**{key: value for key, value in payload.items() if key in allowed})


def search_from_yaml(path: Path, *, overrides: dict | None = None) -> SearchConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    return search_from_mapping(raw.get("search"), overrides=overrides)


def task_from_yaml(path: Path) -> TaskSpec:
    raw = yaml.safe_load(path.read_text())
    return TaskSpec(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        description=str(raw.get("description") or ""),
        task_type=str(raw.get("task_type") or "binary"),
        target=str(raw["target"]),
        entity_id=str(raw.get("entity_id") or "entity_id"),
        prediction_time_column=raw.get("prediction_time_column"),
        prediction_horizon_days=raw.get("prediction_horizon_days"),
        evaluation_metric=str(raw.get("evaluation_metric") or "pr_auc"),
        feature_groups=dict(raw.get("feature_groups") or {}),
        validation_strategy=str(raw.get("validation_strategy") or "time"),
        event_time_column=raw.get("event_time_column"),
        event_value_column=raw.get("event_value_column"),
        config_path=str(path),
    )


def upsert_task(db: Session, environment: Environment, spec: TaskSpec) -> PredictionTask:
    existing = db.scalars(
        select(PredictionTask).where(
            PredictionTask.environment_id == environment.id, PredictionTask.slug == spec.id
        )
    ).first()
    if existing:
        existing.spec = spec.to_dict()
        existing.name = spec.name
        existing.description = spec.description
        existing.task_type = spec.task_type
        existing.config_path = spec.config_path
        db.commit()
        db.refresh(existing)
        return existing
    row = PredictionTask(
        environment_id=environment.id,
        slug=spec.id,
        name=spec.name,
        description=spec.description,
        task_type=spec.task_type,
        spec=spec.to_dict(),
        config_path=spec.config_path,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_experiment(
    db: Session,
    *,
    environment: Environment,
    dataset: Dataset,
    task: PredictionTask | None,
    config: SearchConfig | None = None,
    workflow_run: WorkflowRun | None = None,
    pipeline_name: str = "deterministic_ml",
    pipeline_index: int = 0,
    pipeline_purpose: str = "training",
    commit: bool = True,
    project_id: UUID | None = None,
    pipeline_id: UUID | None = None,
    pipeline_version_id: UUID | None = None,
    run_number: int | None = None,
) -> Experiment:
    if workflow_run is not None and workflow_run.workspace_id != dataset.workspace_id:
        raise ValueError("workflow run and dataset belong to different workspaces")
    cfg = config or SearchConfig()
    row = Experiment(
        workspace_id=dataset.workspace_id,
        project_id=project_id if project_id is not None else (
            workflow_run.project_id if workflow_run is not None else dataset.project_id
        ),
        workflow_run_id=workflow_run.id if workflow_run is not None else None,
        pipeline_id=pipeline_id,
        pipeline_version_id=pipeline_version_id,
        run_number=run_number,
        pipeline_name=pipeline_name,
        pipeline_index=pipeline_index,
        pipeline_purpose=pipeline_purpose,
        environment_id=environment.id,
        dataset_id=dataset.id,
        task_id=task.id if task is not None else None,
        status="CREATED",
        config=cfg.to_dict(),
        seed=cfg.seed,
        git_commit=_git_hash(),
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def _persist_pipeline_stage_runs(db: Session, experiment: Experiment, result: dict) -> None:
    timings = list(result.get("stage_timings") or result.get("execution_stage_timings") or [])
    if not timings:
        return
    from app.services.workflow_execution_service import reconcile_pipeline_stage_runs

    reconcile_pipeline_stage_runs(db, experiment, timings)


def _persist_experiment_test_predictions(db: Session, experiment_id, result: dict) -> None:
    """Replace holdout rows for this experiment. Opportunity `predictions` are untouched."""
    db.query(ExperimentTestPrediction).filter(
        ExperimentTestPrediction.experiment_id == experiment_id
    ).delete(synchronize_session=False)
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    classifier = str(task.get("task_type") or "") == "binary"
    for row in result.get("test_predictions") or []:
        if not isinstance(row, dict):
            continue
        probability = None
        if classifier:
            raw = row.get("probability", row.get("score"))
            if raw is not None:
                probability = float(raw)
        row_index = int(row.get("row_index", 0))
        record_id = row.get("record_id")
        if record_id is None:
            record_id = row_index
        db.add(
            ExperimentTestPrediction(
                experiment_id=experiment_id,
                row_index=row_index,
                source_row_index=(
                    int(row["source_row_index"])
                    if row.get("source_row_index") is not None
                    else None
                ),
                record_id=str(record_id),
                predicted_value=row.get("y_pred"),
                probability=probability,
                y_true=row.get("y_true"),
            )
        )


def execute_experiment(
    db: Session,
    experiment: Experiment,
    *,
    on_stage: Callable[[str], None] | None = None,
    on_event: Callable[[str, dict], None] | None = None,
    persist_scientific: bool = True,
) -> Experiment:
    dataset = db.get(Dataset, experiment.dataset_id)
    task_row = db.get(PredictionTask, experiment.task_id)
    if dataset is None or task_row is None:
        raise ValueError("experiment is missing dataset or task")
    spec = TaskSpec(**{k: v for k, v in task_row.spec.items() if k in {f.name for f in fields(TaskSpec)}})
    yaml_search = {}
    if task_row.config_path and Path(task_row.config_path).exists():
        yaml_search = (yaml.safe_load(Path(task_row.config_path).read_text()) or {}).get("search") or {}
    cfg = search_from_mapping(yaml_search, overrides=experiment.config)
    experiment.status = "QUEUED"
    experiment.started_at = datetime.now(timezone.utc)
    db.commit()
    artifacts = experiment_dir(str(experiment.id))
    experiment.artifact_dir = str(artifacts)
    db.commit()
    logger.info(
        "lab experiment %s starting task=%s dataset=%s",
        experiment.id,
        task_row.slug,
        dataset.name,
    )
    try:
        frame = load_table(dataset.location)

        def _persist_checkpoint(payload: dict) -> None:
            # Selection is committed before the final holdout evaluator runs.
            # This makes winner locking an auditable database fact rather than
            # only an in-memory ordering convention.
            current = dict(experiment.result or {})
            current.update(payload)
            experiment.result = current
            db.commit()

        result = run_experiment(
            frame,
            spec,
            cfg,
            artifact_dir=artifacts,
            dataset_version=dataset.version,
            dataset_content_digest=dataset.content_digest,
            on_stage=on_stage,
            on_checkpoint=_persist_checkpoint,
            on_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("lab experiment %s failed before completion", experiment.id)
        experiment.status = "FAILED"
        experiment.result = {"status": "FAILED", "error": str(exc)}
        experiment.ended_at = datetime.now(timezone.utc)
        experiment.failure_reason = str(exc)[:2048]
        from app.services.workflow_execution_service import fail_open_pipeline_stage_runs

        fail_open_pipeline_stage_runs(
            db,
            experiment,
            reason=str(exc),
            failure_code="process_failure",
        )
        db.commit()
        db.refresh(experiment)
        return experiment
    experiment.result = result
    experiment.status = result.get("status") or "COMPLETED"
    experiment.ended_at = datetime.now(timezone.utc)
    logger.info(
        "lab experiment %s finished status=%s trained=%s",
        experiment.id,
        experiment.status,
        (result.get("funnel") or {}).get("trained"),
    )
    _persist_experiment_test_predictions(db, experiment.id, result)
    _persist_pipeline_stage_runs(db, experiment, result)
    if persist_scientific:
        from app.services.scientific_lineage_service import persist_scientific_lineage_from_result

        persist_scientific_lineage_from_result(db, experiment, result)
    from app.services.candidate_modeling_service import persist_candidate_modeling

    persist_candidate_modeling(db, experiment, result)
    db.commit()
    db.refresh(experiment)
    return experiment


def ingest_synthetic(db: Session, environment: Environment, n: int = 2000, leak: bool = False) -> Dataset:
    path = REPO_ROOT / "data" / "synthetic" / ("customers_leak.csv" if leak else "customers.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = make_synthetic_customers(n=n, leak=leak)
    frame.to_csv(path, index=False)
    return ingest_dataset(db, environment=environment, name="synthetic", location=str(path), source_type="csv")
