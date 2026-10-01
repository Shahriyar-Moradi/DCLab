"""Plan and train the five admin Lab use cases from an uploaded dataset."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import Dataset, ExecutionRequest, Experiment, PredictionTask, User
from app.domain.lab_use_cases import LAB_USE_CASES, families_for
from app.engine.data.loaders import load_table
from app.engine.datasets.lab_workbook import make_lab_workbook
from app.engine.lab.column_map import (
    MIN_TRAIN_ROWS,
    build_feature_groups,
    parse_use_case_slug,
    pick_entity_column,
    pick_time_column,
    planned_targets,
)
from app.services.dataset_materialization import materialize_dataset
from app.services.lab_service import ingest_dataset, profile_dataset, seed_dogfood
from app.translation.models import InsightCategory

logger = logging.getLogger(__name__)


def _latest_admin_build(db: Session, dataset: Dataset, slug: str) -> Experiment | None:
    request = db.scalar(
        select(ExecutionRequest)
        .where(
            ExecutionRequest.workspace_id == dataset.workspace_id,
            ExecutionRequest.request_spec["admin_lab_dataset_id"].astext == str(dataset.id),
            ExecutionRequest.request_spec["use_case_slug"].astext == slug,
        )
        .order_by(ExecutionRequest.created_at.desc())
        .limit(1)
    )
    if request is None or request.pipeline_run_id is None:
        return None
    return db.get(Experiment, request.pipeline_run_id)


def plan_dataset_use_cases(db: Session, dataset: Dataset) -> dict:
    with materialize_dataset(dataset, db=db) as source:
        frame = load_table(source)
    columns = [str(name) for name in frame.columns]
    logger.info(
        "lab plan dataset=%s rows=%s columns=%s",
        dataset.name,
        len(frame),
        columns,
    )
    entity = pick_entity_column(columns)
    time_col = pick_time_column(columns)
    targets = planned_targets(columns)
    holdouts = set(targets.values())
    items: list[dict] = []
    for definition in LAB_USE_CASES:
        target = targets.get(definition.slug)
        latest = _latest_admin_build(db, dataset, definition.slug)
        skip_reason = None
        groups: dict[str, list[str]] = {}
        if len(frame) < MIN_TRAIN_ROWS:
            skip_reason = f"Need at least {MIN_TRAIN_ROWS} rows to train (this file has {len(frame)})."
        elif not target:
            skip_reason = (
                "No label column. Add one of: " + ", ".join(definition.target_aliases[:6]) + "."
            )
        else:
            groups = build_feature_groups(
                columns,
                target=target,
                holdouts=holdouts - {target},
                entity=entity,
                time_col=time_col,
                preferred=definition.preferred_groups,
            )
            if not groups:
                skip_reason = "No usable feature columns besides the label."
        trainable = skip_reason is None
        if trainable:
            logger.info(
                "lab plan %s target=%s groups=%s",
                definition.slug,
                target,
                {name: cols for name, cols in groups.items()},
            )
        else:
            logger.info("lab plan %s skipped: %s", definition.slug, skip_reason)
        items.append(
            {
                "slug": definition.slug,
                "name": definition.name,
                "description": definition.description,
                "task_type": definition.task_type,
                "trainable": trainable,
                "target_column": target,
                "skip_reason": skip_reason,
                "feature_groups": groups,
                "model_families": list(families_for(definition.task_type)),
                "latest_experiment_id": str(latest.id) if latest else None,
                "latest_status": latest.status if latest else None,
            }
        )
    return {
        "dataset_id": str(dataset.id),
        "dataset_name": dataset.name,
        "row_count": int(len(frame)),
        "columns": columns,
        "entity_column": entity,
        "time_column": time_col,
        "use_cases": items,
        "trainable_count": sum(1 for item in items if item["trainable"]),
    }


def _admin_build_origin(dataset: Dataset, **extra: str) -> dict[str, str]:
    return {"admin_lab_dataset_id": str(dataset.id), **extra}


def train_dataset_target(
    db: Session,
    dataset: Dataset,
    *,
    actor: User,
    target: str,
    origin: dict[str, str],
    exclude_columns: list[str] | None = None,
) -> Experiment:
    """Queue an open-ingest model build for an existing admin Lab dataset.

    There is exactly one training path: the dataset bytes go through the same
    upload pipeline as a Labs upload (structural validation, ADR 0005
    publication, lineage, ExecutionRequest, ``labs.auto_train`` job), with the
    target passed explicitly. The worker trains it; this returns the queued
    Experiment immediately.
    """
    from app.db.models import ClientLabUpload
    from app.services.client_lab_upload_service import save_upload

    excluded = sorted({c for c in (exclude_columns or []) if c and c != target})
    with materialize_dataset(dataset, db=db) as source:
        path = Path(source)
        if excluded:
            # Labels of the other planned use cases are outcomes, not features:
            # the build never sees them (they would leak post-outcome information).
            frame = load_table(path).drop(columns=excluded, errors="ignore")
            data = frame.to_csv(index=False).encode("utf-8")
            suffix = ".csv"
        else:
            data = path.read_bytes()
            suffix = path.suffix or ".csv"
    if excluded:
        origin = {**origin, "excluded_columns": ",".join(excluded)[:256]}
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset.name or "dataset").strip("._") or "dataset"
    result = save_upload(
        db,
        user=actor,
        category=InsightCategory.CUSTOM.value,
        filename=f"{stem}{suffix}",
        data=data,
        target_column=target,
        workspace_id=dataset.workspace_id,
        project_id=dataset.project_id,
        origin=origin,
    )
    upload = db.get(ClientLabUpload, result.id)
    experiment = db.get(Experiment, upload.experiment_id) if upload else None
    if experiment is None:
        raise ValueError("model build was not created")
    return experiment


def train_dataset_use_case(
    db: Session,
    dataset: Dataset,
    slug: str,
    *,
    actor: User,
) -> Experiment:
    """The use case only supplies an explicit target hint; no alias inference
    happens in the training path itself."""
    plan = plan_dataset_use_cases(db, dataset)
    item = next((row for row in plan["use_cases"] if row["slug"] == slug), None)
    if item is None:
        raise ValueError(f"unknown use case {slug!r}")
    if not item["trainable"]:
        raise ValueError(item["skip_reason"] or "use case is not trainable")
    target = str(item["target_column"])
    sibling_labels = [
        str(row["target_column"])
        for row in plan["use_cases"]
        if row.get("target_column") and str(row["target_column"]) != target
    ]
    return train_dataset_target(
        db,
        dataset,
        actor=actor,
        target=target,
        origin=_admin_build_origin(dataset, use_case_slug=slug),
        exclude_columns=sibling_labels,
    )


def train_dataset_use_cases(
    db: Session,
    dataset: Dataset,
    *,
    actor: User,
    slugs: list[str] | None = None,
) -> list[Experiment]:
    plan = plan_dataset_use_cases(db, dataset)
    wanted = set(slugs) if slugs else None
    runs: list[Experiment] = []
    for item in plan["use_cases"]:
        if not item["trainable"] or (wanted is not None and item["slug"] not in wanted):
            continue
        runs.append(train_dataset_use_case(db, dataset, item["slug"], actor=actor))
    if not runs:
        raise ValueError("no trainable use cases for this dataset")
    return runs


def ingest_sample_workbook(db: Session, *, n: int = 240) -> Dataset:
    env = seed_dogfood(db)
    dest_dir = REPO_ROOT / "data" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"lab_workbook_{uuid4().hex[:8]}.csv"
    frame = make_lab_workbook(n=n)
    frame.to_csv(dest, index=False)
    logger.info("lab sample workbook written %s rows=%s", dest, len(frame))
    dataset = ingest_dataset(
        db,
        environment=env,
        name="lab_workbook",
        location=str(dest),
        source_type="csv",
    )
    profile_dataset(db, dataset)
    return dataset


def experiment_payload(db: Session, experiment: Experiment) -> dict:
    task = db.get(PredictionTask, experiment.task_id) if experiment.task_id is not None else None
    dataset = db.get(Dataset, experiment.dataset_id)
    slug = task.slug if task else None
    use_case = parse_use_case_slug(slug) if slug else None
    task_name = task.name if task else None
    if task is None:
        # Open-ingest builds carry the admin use case as ExecutionRequest provenance.
        request = db.scalar(
            select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == experiment.id)
        )
        use_case = ((request.request_spec or {}).get("use_case_slug") if request else None) or None
        definition = next((row for row in LAB_USE_CASES if row.slug == use_case), None)
        task_name = definition.name if definition else None
    return {
        "id": experiment.id,
        "status": experiment.status,
        "seed": experiment.seed,
        "git_commit": experiment.git_commit,
        "artifact_dir": experiment.artifact_dir,
        "result": experiment.result,
        "config": experiment.config,
        "task_id": experiment.task_id,
        "dataset_id": experiment.dataset_id,
        "task_slug": slug,
        "task_name": task_name,
        "dataset_name": dataset.name if dataset else None,
        "use_case": use_case,
    }
