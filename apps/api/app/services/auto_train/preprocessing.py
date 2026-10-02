"""Stage: publish the prepared dataset and set up the pipeline-run Experiment."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db.models import ClientLabUpload, Experiment, SplitPlan, WorkflowRun
from app.domain.lab_run_stages import PREPROCESSING
from app.engine.lab.auto_prepare import ColumnRoles, MissingValuePlan
from app.engine.lab.schema_inference import TargetChoice
from app.engine.modeling.leakage_auditor import ModelDevelopmentPlan
from app.engine.modeling.metric_planner import MetricPlan
from app.engine.modeling.validation_planner import ValidationPlan
from app.engine.types import SearchConfig, TaskSpec
from app.services.auto_train.context import RunContext, service_module
from app.services.lab_service import (
    create_experiment,
    ingest_dataset,
    seed_dogfood,
    upsert_task,
)


class PreprocessingSetupInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    workflow_run: WorkflowRun | None
    frame: pd.DataFrame
    target: TargetChoice
    feature_columns: list[str]
    fe_transformations: list[dict[str, Any]]
    missing_plan: MissingValuePlan
    identifier_cols: list[str]
    final_roles: ColumnRoles
    leakage_excluded: list[str]
    development_plan: ModelDevelopmentPlan
    validation_plan: ValidationPlan
    metric_plan: MetricPlan
    entity_column: str | None
    modeled_cols: list[str]
    num_cols: list[str]
    cat_cols: list[str]
    search: SearchConfig
    # ADR 0006 §3: the SplitPlan this run's holdout/folds belong to, if any.
    split_plan_id: UUID | None = None


class PreprocessingSetupOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment: Experiment
    # Re-read after the Experiment is bound; later stages use this one.
    workflow_run: WorkflowRun | None
    feature_report: dict[str, Any]


def run_preprocessing_setup(
    ctx: RunContext, inp: PreprocessingSetupInput
) -> PreprocessingSetupOutput:
    svc = service_module()
    db = ctx.db
    upload = inp.upload
    frame = inp.frame
    target = inp.target
    fe_transformations = inp.fe_transformations
    num_cols = inp.num_cols
    cat_cols = inp.cat_cols
    search = inp.search

    ctx.stage(PREPROCESSING)
    evidence_timer = ctx.evidence_start("preprocessing_setup")
    # P1.3-A: the prepared CSV is written to worker scratch and published to
    # object storage as a ``derived_dataset`` (derived in this job from the
    # already-published upload), so any process can materialize it and the
    # publication gate keeps applying to externally supplied ``dataset`` bytes.
    from app.domain.data_plane import DERIVED_DATASET_TYPE
    from app.engine.serving.artifacts import run_scratch_root
    from app.services.artifact_service import store_artifact

    # Per-job file (re-runs of one upload never share it); serialize once so
    # the stored bytes and the Dataset digest are the same bytes.
    import tempfile

    prepared_root = run_scratch_root() / "prepared"
    prepared_root.mkdir(parents=True, exist_ok=True)
    prepared_dir = Path(tempfile.mkdtemp(prefix=f"{upload.id}-", dir=prepared_root))
    dataset_path = prepared_dir / "prepared.csv"
    prepared_bytes = frame.to_csv(index=False).encode("utf-8")
    dataset_path.write_bytes(prepared_bytes)
    prepared_project_id = svc._resolve_auto_train_project_id(db, upload, inp.workflow_run)
    prepared_artifact = store_artifact(
        db,
        workspace_id=upload.workspace_id,
        project_id=prepared_project_id,
        artifact_type=DERIVED_DATASET_TYPE,
        filename=f"prepared-{upload.id}.csv",
        data=prepared_bytes,
        mime_type="text/csv",
        created_by=upload.requested_by,
        extra_metadata={"derived_from_upload_id": str(upload.id), "role": "prepared_dataset"},
    )

    env = seed_dogfood(db)
    # Prepared CSV is a new Dataset version. Keep the Labs ingest lineage:
    # same IngestionRun/DataSource as the original upload Dataset. Do not
    # invent a second ingest engine or a second training runner.
    dataset = ingest_dataset(
        db,
        environment=env,
        name=f"client-upload-{upload.id}",
        location=str(dataset_path),
        source_type="csv",
        version="v1",
        workspace_id=upload.workspace_id,
        project_id=prepared_project_id,
        ingestion_run_id=upload.ingestion_run_id,
        artifact_id=prepared_artifact.id,
    )
    # The Dataset now materializes from its artifact; drop the scratch copy.
    import shutil

    shutil.rmtree(prepared_dir, ignore_errors=True)

    task_type = target.task_type
    metric = inp.metric_plan.primary_metric
    transformed_features = [
        str(name)
        for action in fe_transformations
        for name in (action.get("columns") or [])
    ]
    removed_features = list(
        dict.fromkeys(
            list(inp.missing_plan.dropped_columns)
            + list(inp.identifier_cols)
            + list(inp.final_roles.ignored_free_text)
            + list(inp.leakage_excluded)
        )
    )
    feature_report = {
        "original_features": list(inp.feature_columns),
        "generated_features": [],
        "transformed_features": transformed_features,
        "removed_features": removed_features,
        "feature_engineering_actions": list(fe_transformations),
    }
    group_column = inp.development_plan.group_column
    time_column = inp.development_plan.time_column
    entity_column = inp.entity_column
    task_spec = TaskSpec(
        id=f"open_ingest_{upload.id.hex[:12]}",
        name=f"Auto-train: {upload.original_filename}",
        description="Automatic training job for a Labs custom-box upload (simple tabular file).",
        task_type=task_type,
        target=target.column,
        entity_id=(
            group_column
            if group_column and group_column in frame.columns
            else (entity_column if entity_column in frame.columns else None)
        ),
        prediction_time_column=time_column if time_column and time_column in frame.columns else None,
        evaluation_metric=metric,
        feature_groups={"features": list(inp.modeled_cols)},
        validation_strategy=inp.validation_plan.strategy,
        column_roles={"numerical": num_cols, "categorical": cat_cols},
        feature_engineering=feature_report,
    )
    task_row = upsert_task(db, env, task_spec)
    workflow_run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.source_upload_id == upload.id)
    )
    if workflow_run is not None:
        from app.services.lineage_service import bind_pipeline_run, create_pipeline_run

        experiment = (
            db.get(Experiment, upload.experiment_id)
            if upload.experiment_id is not None
            else None
        )
        if experiment is not None:
            experiment = bind_pipeline_run(
                db,
                pipeline_run=experiment,
                workflow_run=workflow_run,
                environment=env,
                dataset=dataset,
                task=task_row,
                config=search,
            )
        else:
            experiment = create_pipeline_run(
                db,
                workflow_run=workflow_run,
                environment=env,
                dataset=dataset,
                task=task_row,
                pipeline_name="open_ingest_deterministic_ml",
                pipeline_index=len(workflow_run.pipeline_runs),
                pipeline_purpose="training_and_scoring",
                config=search,
            )
    else:
        experiment = create_experiment(
            db,
            environment=env,
            dataset=dataset,
            task=task_row,
            config=search,
        )
    if inp.split_plan_id is not None:
        from app.services.lineage_service import attach_split_plan

        attach_split_plan(
            db,
            pipeline_run=experiment,
            split_plan=db.get(SplitPlan, inp.split_plan_id),
            source_dataset_id=upload.dataset_id,
        )
        db.commit()
    ctx.evidence_finish(evidence_timer)
    return PreprocessingSetupOutput(
        experiment=experiment, workflow_run=workflow_run, feature_report=feature_report
    )
