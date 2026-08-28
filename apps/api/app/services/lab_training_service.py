"""Plan and train the five admin Lab use cases from an uploaded dataset."""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import Dataset, Experiment, PredictionTask
from app.domain.lab_use_cases import (
    LAB_USE_CASES,
    MODELS_PER_USE_CASE,
    families_for,
    use_case_by_slug,
)
from app.engine.data.loaders import load_table
from app.engine.datasets.lab_workbook import make_lab_workbook
from app.engine.lab.column_map import (
    MIN_TRAIN_ROWS,
    build_feature_groups,
    parse_use_case_slug,
    pick_entity_column,
    pick_time_column,
    planned_targets,
    task_slug_for,
)
from app.engine.types import SearchConfig, TaskSpec
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_dataset,
    profile_dataset,
    seed_dogfood,
    upsert_task,
)

logger = logging.getLogger(__name__)


def _latest_experiment(db: Session, task: PredictionTask | None) -> Experiment | None:
    if task is None:
        return None
    return (
        db.query(Experiment)
        .filter(Experiment.task_id == task.id)
        .order_by(Experiment.created_at.desc())
        .first()
    )


def plan_dataset_use_cases(db: Session, dataset: Dataset) -> dict:
    frame = load_table(dataset.location)
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
        task_slug = task_slug_for(definition.slug, str(dataset.id))
        task = (
            db.query(PredictionTask)
            .filter(PredictionTask.slug == task_slug)
            .first()
        )
        latest = _latest_experiment(db, task)
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


def _search_config(max_models: int) -> SearchConfig:
    cap = max(1, min(max_models, MODELS_PER_USE_CASE))
    return SearchConfig(
        strategy="use_case",
        max_candidates=cap,
        max_feature_group_combinations=8,
        max_ensemble_size=min(5, cap),
        max_training_seconds=120.0,
        n_robustness_folds=2,
        min_metric=0.5,
        retain_min=1,
        retain_max=cap,
        seed=42,
    )


def _spec_for(dataset: Dataset, plan_item: dict, columns: list[str]) -> TaskSpec:
    definition = use_case_by_slug(plan_item["slug"])
    assert definition is not None
    entity = pick_entity_column(columns)
    time_col = pick_time_column(columns)
    return TaskSpec(
        id=task_slug_for(definition.slug, str(dataset.id)),
        name=definition.name,
        description=definition.description,
        task_type=definition.task_type,
        target=str(plan_item["target_column"]),
        entity_id=entity or "entity_id",
        prediction_time_column=time_col,
        evaluation_metric=definition.evaluation_metric,
        feature_groups=plan_item["feature_groups"],
        validation_strategy="time" if time_col else "stratified",
        config_path=None,
    )


def train_dataset_use_case(
    db: Session,
    dataset: Dataset,
    slug: str,
    *,
    max_models: int = MODELS_PER_USE_CASE,
) -> Experiment:
    definition = use_case_by_slug(slug)
    if definition is None:
        raise ValueError(f"Unknown use case {slug!r}")
    plan = plan_dataset_use_cases(db, dataset)
    item = next(row for row in plan["use_cases"] if row["slug"] == slug)
    if not item["trainable"]:
        raise ValueError(item["skip_reason"] or f"{slug} is not trainable on this file")
    env = seed_dogfood(db)
    spec = _spec_for(dataset, item, plan["columns"])
    logger.info(
        "lab train %s dataset=%s target=%s families=%s",
        slug,
        dataset.name,
        spec.target,
        item["model_families"][:max_models],
    )
    task = upsert_task(db, env, spec)
    experiment = create_experiment(
        db,
        environment=env,
        dataset=dataset,
        task=task,
        config=_search_config(max_models),
    )
    return execute_experiment(db, experiment)


def train_dataset_use_cases(
    db: Session,
    dataset: Dataset,
    *,
    slugs: list[str] | None = None,
    max_models: int = MODELS_PER_USE_CASE,
) -> list[Experiment]:
    plan = plan_dataset_use_cases(db, dataset)
    wanted = slugs or [item["slug"] for item in plan["use_cases"] if item["trainable"]]
    logger.info("lab train-all dataset=%s use_cases=%s", dataset.name, wanted)
    runs: list[Experiment] = []
    for slug in wanted:
        runs.append(train_dataset_use_case(db, dataset, slug, max_models=max_models))
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
    task = db.get(PredictionTask, experiment.task_id)
    dataset = db.get(Dataset, experiment.dataset_id)
    slug = task.slug if task else None
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
        "task_name": task.name if task else None,
        "dataset_name": dataset.name if dataset else None,
        "use_case": parse_use_case_slug(slug) if slug else None,
    }
