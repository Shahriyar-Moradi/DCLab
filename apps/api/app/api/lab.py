from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import Dataset, DatasetProfile, Environment, Experiment, ExperimentCandidate, PredictionTask
from app.db.session import get_db
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_dataset,
    profile_dataset,
    search_from_mapping,
    seed_dogfood,
    task_from_yaml,
    upsert_task,
)

router = APIRouter(prefix="/lab", tags=["lab"])


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    org_id: str
    name: str


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    source_type: str
    location: str
    version: str
    row_count: int
    column_count: int
    table_schema: dict | None = Field(default=None, validation_alias="schema_json", serialization_alias="schema_json")


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    slug: str
    name: str
    description: str
    task_type: str
    spec: dict


class ExperimentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    seed: int
    git_commit: str | None
    artifact_dir: str | None
    result: dict | None = None
    config: dict
    task_id: UUID
    dataset_id: UUID


class RunRequest(BaseModel):
    dataset_id: UUID | None = None
    task_id: UUID | None = None
    dataset_name: str | None = None
    task_slug: str | None = None
    max_candidates: int | None = None


@router.post("/environments/dogfood", response_model=EnvironmentRead)
def ensure_dogfood(db: Session = Depends(get_db)) -> Environment:
    return seed_dogfood(db)


@router.get("/environments", response_model=list[EnvironmentRead])
def list_environments(db: Session = Depends(get_db)) -> list[Environment]:
    return list(db.query(Environment).order_by(Environment.created_at.desc()).all())


@router.get("/datasets", response_model=list[DatasetRead])
def list_datasets(db: Session = Depends(get_db)) -> list[Dataset]:
    return list(db.query(Dataset).order_by(Dataset.created_at.desc()).all())


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: UUID, db: Session = Depends(get_db)) -> Dataset:
    row = db.get(Dataset, dataset_id)
    if row is None:
        raise HTTPException(404, "dataset not found")
    return row


@router.post("/datasets/upload", response_model=DatasetRead)
async def upload_dataset(
    name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dataset:
    env = seed_dogfood(db)
    dest_dir = REPO_ROOT / "data" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload.csv").suffix or ".csv"
    dest = dest_dir / f"{name}{suffix}"
    dest.write_bytes(await file.read())
    source = "parquet" if suffix in {".parquet", ".pq"} else "csv"
    return ingest_dataset(db, environment=env, name=name, location=str(dest), source_type=source)


@router.post("/datasets/{dataset_id}/profile")
def profile(dataset_id: UUID, db: Session = Depends(get_db)) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "dataset not found")
    profile = profile_dataset(db, dataset)
    return {"id": str(profile.id), "stats": profile.stats}


@router.get("/datasets/{dataset_id}/profile")
def latest_profile(dataset_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = (
        db.query(DatasetProfile)
        .filter(DatasetProfile.dataset_id == dataset_id)
        .order_by(DatasetProfile.created_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(404, "profile not found")
    return {"id": str(row.id), "stats": row.stats}


@router.get("/tasks", response_model=list[TaskRead])
def list_tasks(db: Session = Depends(get_db)) -> list[PredictionTask]:
    return list(db.query(PredictionTask).order_by(PredictionTask.created_at.desc()).all())


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: UUID, db: Session = Depends(get_db)) -> PredictionTask:
    row = db.get(PredictionTask, task_id)
    if row is None:
        raise HTTPException(404, "task not found")
    return row


@router.post("/tasks/from-config", response_model=TaskRead)
def create_task_from_config(path: str = Query(...), db: Session = Depends(get_db)) -> PredictionTask:
    env = seed_dogfood(db)
    spec = task_from_yaml(Path(path))
    return upsert_task(db, env, spec)


@router.get("/experiments", response_model=list[ExperimentRead])
def list_experiments(db: Session = Depends(get_db)) -> list[Experiment]:
    return list(db.query(Experiment).order_by(Experiment.created_at.desc()).limit(50).all())


@router.get("/experiments/{experiment_id}", response_model=ExperimentRead)
def get_experiment(experiment_id: UUID, db: Session = Depends(get_db)) -> Experiment:
    row = db.get(Experiment, experiment_id)
    if row is None:
        raise HTTPException(404, "experiment not found")
    return row


@router.get("/experiments/{experiment_id}/candidates")
def get_candidates(experiment_id: UUID, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(ExperimentCandidate).filter(ExperimentCandidate.experiment_id == experiment_id).all()
    return [row.payload for row in rows]


@router.get("/experiments/{experiment_id}/metrics")
def get_metrics(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment metrics not found")
    return {
        "test": row.result.get("test_metrics"),
        "blend": row.result.get("validation_blend_metrics"),
        "best_single": (row.result.get("best_single") or {}).get("metrics"),
        "funnel": row.result.get("funnel"),
    }


@router.get("/experiments/{experiment_id}/models")
def get_models(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    return {"selected_ids": row.result.get("selected_ids"), "best_single": row.result.get("best_single")}


@router.get("/experiments/{experiment_id}/ensemble")
def get_ensemble(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    return {
        "fusion": row.result.get("fusion"),
        "weights": row.result.get("weights"),
        "test_metrics": row.result.get("test_metrics"),
    }


@router.get("/experiments/{experiment_id}/report")
def get_report(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None:
        raise HTTPException(404, "experiment not found")
    report = None
    if row.artifact_dir:
        path = Path(row.artifact_dir) / "report.md"
        if path.exists():
            report = path.read_text()
    return {"markdown": report, "result": row.result}


@router.get("/experiments/{experiment_id}/feature-importance")
def feature_importance(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    return {"feature_group_scores": row.result.get("feature_group_scores")}


@router.get("/experiments/{experiment_id}/feature-groups")
def feature_groups(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    return {
        "groups": (row.result.get("task") or {}).get("feature_groups"),
        "combination_table": row.result.get("combination_table"),
    }


@router.get("/experiments/{experiment_id}/predictions")
def predictions(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    return {"test_metrics": row.result.get("test_metrics"), "split": row.result.get("split")}


@router.get("/experiments/{experiment_id}/errors")
def errors(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    failed = [c for c in (row.result.get("candidates") or []) if c.get("status") == "FAILED"]
    return {"failed": failed, "leakage": row.result.get("leakage"), "quality": row.result.get("quality")}


@router.get("/experiments/{experiment_id}/comparison")
def get_comparison(experiment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Experiment, experiment_id)
    if row is None or not row.result:
        raise HTTPException(404, "experiment not found")
    return {
        "baselines": row.result.get("baselines"),
        "best_single": row.result.get("best_single"),
        "fusion": row.result.get("fusion"),
        "weights": row.result.get("weights"),
        "validation_blend_metrics": row.result.get("validation_blend_metrics"),
        "test_metrics": row.result.get("test_metrics"),
    }


@router.post("/experiments", response_model=ExperimentRead)
def create_and_optionally_run(
    payload: RunRequest,
    db: Session = Depends(get_db),
) -> Experiment:
    env = seed_dogfood(db)
    dataset = None
    task = None
    if payload.dataset_id:
        dataset = db.get(Dataset, payload.dataset_id)
    elif payload.dataset_name:
        dataset = db.query(Dataset).filter(Dataset.name == payload.dataset_name).order_by(Dataset.created_at.desc()).first()
    if payload.task_id:
        task = db.get(PredictionTask, payload.task_id)
    elif payload.task_slug:
        task = db.query(PredictionTask).filter(PredictionTask.slug == payload.task_slug).first()
    if dataset is None or task is None:
        raise HTTPException(400, "dataset and task are required")
    yaml_search = {}
    if task.config_path and Path(task.config_path).exists():
        import yaml

        yaml_search = (yaml.safe_load(Path(task.config_path).read_text()) or {}).get("search") or {}
    cfg = search_from_mapping(
        yaml_search,
        overrides={"max_candidates": payload.max_candidates} if payload.max_candidates else None,
    )
    return create_experiment(db, environment=env, dataset=dataset, task=task, config=cfg)


@router.post("/experiments/{experiment_id}/run", response_model=ExperimentRead)
def run_existing(experiment_id: UUID, db: Session = Depends(get_db)) -> Experiment:
    row = db.get(Experiment, experiment_id)
    if row is None:
        raise HTTPException(404, "experiment not found")
    return execute_experiment(db, row)
