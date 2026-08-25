"""Persist datasets, tasks, and experiment runs for the Lab."""

from __future__ import annotations

import subprocess
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import Dataset, DatasetProfile, Environment, Experiment, ExperimentCandidate, PredictionTask
from app.engine.data.loaders import infer_schema, load_table
from app.engine.datasets.synthetic import make_synthetic_customers
from app.engine.experiments.runner import run_experiment
from app.engine.schema.profiler import profile_frame
from app.engine.serving.artifacts import experiment_dir
from app.engine.types import SearchConfig, TaskSpec

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
) -> Dataset:
    frame = load_table(location)
    schema = infer_schema(frame)
    row = Dataset(
        environment_id=environment.id,
        name=name,
        source_type=source_type,
        location=str(location),
        version=version,
        schema_json=schema,
        row_count=int(len(frame)),
        column_count=int(frame.shape[1]),
    )
    db.add(row)
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
    task: PredictionTask,
    config: SearchConfig | None = None,
) -> Experiment:
    cfg = config or SearchConfig()
    row = Experiment(
        environment_id=environment.id,
        dataset_id=dataset.id,
        task_id=task.id,
        status="CREATED",
        config=cfg.to_dict(),
        seed=cfg.seed,
        git_commit=_git_hash(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def execute_experiment(db: Session, experiment: Experiment) -> Experiment:
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
    frame = load_table(dataset.location)
    result = run_experiment(frame, spec, cfg, artifact_dir=artifacts, dataset_version=dataset.version)
    experiment.result = result
    experiment.status = result.get("status") or "COMPLETED"
    experiment.ended_at = datetime.now(timezone.utc)
    db.query(ExperimentCandidate).filter(ExperimentCandidate.experiment_id == experiment.id).delete()
    for row in result.get("candidates") or []:
        db.add(
            ExperimentCandidate(
                experiment_id=experiment.id,
                candidate_key=str(row.get("candidate_id")),
                fingerprint=str(row.get("fingerprint") or ""),
                status=str(row.get("status") or "generated"),
                payload=row,
            )
        )
    db.commit()
    db.refresh(experiment)
    return experiment


def ingest_synthetic(db: Session, environment: Environment, n: int = 2000, leak: bool = False) -> Dataset:
    path = REPO_ROOT / "data" / "synthetic" / ("customers_leak.csv" if leak else "customers.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = make_synthetic_customers(n=n, leak=leak)
    frame.to_csv(path, index=False)
    return ingest_dataset(db, environment=environment, name="synthetic", location=str(path), source_type="csv")
