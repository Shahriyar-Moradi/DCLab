"""Tenant-safe services for DCLab data, workflow, pipeline, and model lineage."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BusinessDomain,
    ClientLabUpload,
    Dataset,
    DatasetAsset,
    Experiment,
    ExperimentCandidate,
    MlWorkflow,
    ModelAsset,
    ModelVersion,
    PredictionTask,
    ProblemSpec,
    Project,
    User,
    WorkflowRun,
    WorkflowRunInput,
    Workspace,
    WorkspaceDomain,
)
from app.engine.types import SearchConfig
from app.services.authorization_service import can_write_workspace
from app.domain.execution_plane import CREATABLE_INITIATED_BY_TYPES

DOMAIN_SEEDS = (
    ("labs", "Labs"),
    ("marketing", "Marketing"),
    ("sales", "Sales"),
    ("revenue", "Revenue"),
    ("customer", "Customer"),
)


class LineageError(ValueError):
    """Raised before persistence when a lineage edge is invalid or cross-tenant."""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:128] or "asset"


_TRIGGER_TO_INITIATOR = {
    "upload": "human",
    "manual": "human",
    "api": "api",
    "schedule": "schedule",
    "system": "system",
}


def _require_project(db: Session, workspace_id: UUID, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise LineageError("project does not belong to this workspace")
    return project


def _require_workspace(db: Session, workspace_id: UUID) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise LineageError("workspace not found")
    return workspace


def _require_actor_write(
    db: Session, actor: User | None, workspace_id: UUID
) -> None:
    if actor is not None and not can_write_workspace(db, actor, workspace_id):
        raise LineageError("actor cannot write this workspace")


def seed_business_domains(db: Session) -> list[BusinessDomain]:
    """Idempotently seed defaults; future domains use the same data operation."""

    rows: list[BusinessDomain] = []
    for slug, name in DOMAIN_SEEDS:
        row = db.scalar(select(BusinessDomain).where(BusinessDomain.slug == slug))
        if row is None:
            row = BusinessDomain(
                slug=slug,
                name=name,
                description=f"{name} workflows",
                default_config={},
            )
            db.add(row)
            db.flush()
        rows.append(row)
    return rows


def enable_workspace_domain(
    db: Session,
    *,
    workspace_id: UUID,
    domain_slug: str,
    config: dict | None = None,
    actor: User | None = None,
) -> WorkspaceDomain:
    _require_workspace(db, workspace_id)
    _require_actor_write(db, actor, workspace_id)
    seed_business_domains(db)
    domain = db.scalar(
        select(BusinessDomain).where(BusinessDomain.slug == domain_slug)
    )
    if domain is None:
        raise LineageError("business domain not found")
    link = db.scalar(
        select(WorkspaceDomain).where(
            WorkspaceDomain.workspace_id == workspace_id,
            WorkspaceDomain.business_domain_id == domain.id,
        )
    )
    if link is None:
        link = WorkspaceDomain(
            workspace_id=workspace_id,
            business_domain_id=domain.id,
            enabled=True,
            config=dict(config or domain.default_config or {}),
        )
        db.add(link)
        db.flush()
        return link
    link.enabled = True
    if config is not None:
        link.config = dict(config)
    db.flush()
    return link


def disable_workspace_domain(
    db: Session,
    *,
    workspace_id: UUID,
    domain_slug: str,
    actor: User | None = None,
) -> WorkspaceDomain:
    _require_workspace(db, workspace_id)
    _require_actor_write(db, actor, workspace_id)
    domain = db.scalar(
        select(BusinessDomain).where(BusinessDomain.slug == domain_slug)
    )
    if domain is None:
        raise LineageError("business domain not found")
    link = db.scalar(
        select(WorkspaceDomain).where(
            WorkspaceDomain.workspace_id == workspace_id,
            WorkspaceDomain.business_domain_id == domain.id,
        )
    )
    if link is None:
        raise LineageError("workspace domain is not enabled")
    link.enabled = False
    db.flush()
    return link


def create_dataset_asset(
    db: Session,
    *,
    workspace_id: UUID,
    name: str,
    slug: str | None = None,
    description: str = "",
    actor: User | None = None,
    project_id: UUID | None = None,
) -> DatasetAsset:
    _require_workspace(db, workspace_id)
    _require_actor_write(db, actor, workspace_id)
    if project_id is not None:
        from app.db.models import Project

        project = db.get(Project, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise LineageError("project does not belong to this workspace")
    row = DatasetAsset(
        workspace_id=workspace_id,
        project_id=project_id,
        name=name,
        slug=slugify(slug or name),
        description=description,
        created_by=actor.id if actor is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def create_workflow(
    db: Session,
    *,
    workspace_id: UUID,
    workspace_domain: WorkspaceDomain,
    name: str,
    slug: str,
    project_id: UUID,
    description: str = "",
    business_objective: str = "",
    status: str = "active",
    config: dict | None = None,
    actor: User | None = None,
) -> MlWorkflow:
    _require_workspace(db, workspace_id)
    _require_actor_write(db, actor, workspace_id)
    if workspace_domain.workspace_id != workspace_id:
        raise LineageError("workspace domain belongs to another workspace")
    _require_project(db, workspace_id, project_id)
    row = MlWorkflow(
        workspace_id=workspace_id,
        project_id=project_id,
        workspace_domain_id=workspace_domain.id,
        name=name,
        slug=slugify(slug),
        description=description,
        business_objective=business_objective,
        status=status,
        config=dict(config or {}),
        created_by=actor.id if actor is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def get_or_create_labs_workflow(
    db: Session,
    *,
    workspace_id: UUID,
    actor: User | None = None,
    project: Project | None = None,
) -> MlWorkflow:
    from app.services.project_service import get_or_create_labs_project

    link = enable_workspace_domain(
        db,
        workspace_id=workspace_id,
        domain_slug="labs",
        actor=actor,
    )
    if project is not None:
        if project.workspace_id != workspace_id:
            raise LineageError("project belongs to another workspace")
    elif actor is None:
        from app.domain.data_plane import LABS_PROJECT_SLUG

        project = db.scalar(
            select(Project).where(
                Project.workspace_id == workspace_id,
                Project.slug == LABS_PROJECT_SLUG,
            )
        )
        if project is None:
            raise LineageError("labs project is required before creating a labs workflow")
    else:
        project = get_or_create_labs_project(
            db, workspace_id=workspace_id, actor=actor
        )
    existing = db.scalar(
        select(MlWorkflow).where(
            MlWorkflow.workspace_id == workspace_id,
            MlWorkflow.project_id == project.id,
            MlWorkflow.slug.in_(
                ("client-lab-analysis", f"client-lab-analysis-{project.slug}")
            ),
        )
    )
    if existing is not None:
        return existing
    canonical = db.scalar(
        select(MlWorkflow).where(
            MlWorkflow.workspace_id == workspace_id,
            MlWorkflow.slug == "client-lab-analysis",
        )
    )
    if canonical is not None:
        if canonical.project_id is None:
            canonical.project_id = project.id
            db.flush()
            return canonical
        slug = f"client-lab-analysis-{project.slug}"
    else:
        slug = "client-lab-analysis"
    return create_workflow(
        db,
        workspace_id=workspace_id,
        project_id=project.id,
        workspace_domain=link,
        name="Client Lab Analysis",
        slug=slug,
        description="Analyze a customer-provided Labs dataset.",
        business_objective="Produce a bounded prediction analysis from an uploaded dataset.",
        config={"pipeline": "deterministic_ml"},
        actor=actor,
    )


def add_workflow_run_input(
    db: Session,
    *,
    workflow_run: WorkflowRun,
    dataset: Dataset,
    input_role: str,
    position: int = 0,
) -> WorkflowRunInput:
    if dataset.workspace_id != workflow_run.workspace_id:
        raise LineageError("dataset belongs to another workspace")
    existing = db.scalar(
        select(WorkflowRunInput).where(
            WorkflowRunInput.workflow_run_id == workflow_run.id,
            WorkflowRunInput.dataset_id == dataset.id,
            WorkflowRunInput.input_role == input_role,
        )
    )
    if existing is not None:
        return existing
    row = WorkflowRunInput(
        workflow_run_id=workflow_run.id,
        dataset_id=dataset.id,
        input_role=input_role,
        position=position,
    )
    db.add(row)
    db.flush()
    return row


def create_workflow_run(
    db: Session,
    *,
    workspace_id: UUID,
    workflow: MlWorkflow,
    requester: User | None,
    trigger_type: str,
    source_type: str,
    source_upload: ClientLabUpload | None = None,
    explicit_target: str | None = None,
    resolved_target: str | None = None,
    task_type: str | None = None,
    status: str = "queued",
    inputs: list[tuple[Dataset, str]] | None = None,
    workflow_version_id: UUID | None = None,
    problem_spec_id: UUID | None = None,
    initiated_by_type: str | None = None,
) -> WorkflowRun:
    from app.services.workflow_execution_service import (
        get_or_create_current_workflow_version,
        get_workflow_version,
    )

    _require_workspace(db, workspace_id)
    _require_actor_write(db, requester, workspace_id)
    if workflow.workspace_id != workspace_id:
        raise LineageError("workflow belongs to another workspace")
    if source_upload is not None and source_upload.workspace_id != workspace_id:
        raise LineageError("source upload belongs to another workspace")
    project_id = workflow.project_id
    if project_id is not None:
        _require_project(db, workspace_id, project_id)
    initiator = initiated_by_type or _TRIGGER_TO_INITIATOR.get(trigger_type)
    if initiator is None:
        initiator = "human" if requester is not None else "system"
    initiator = str(initiator).strip().lower()
    if initiator == "agent":
        raise LineageError("initiated_by_type agent is reserved")
    if initiator not in CREATABLE_INITIATED_BY_TYPES:
        raise LineageError(f"unsupported initiated_by_type: {initiator}")
    version = None
    if workflow_version_id is not None:
        version = get_workflow_version(
            db, workspace_id=workspace_id, workflow_version_id=workflow_version_id
        )
        if version.workflow_id != workflow.id:
            raise LineageError("workflow version belongs to another workflow")
    elif project_id is not None:
        version = get_or_create_current_workflow_version(
            db, workflow=workflow, actor=requester
        )
    if problem_spec_id is not None:
        spec = db.get(ProblemSpec, problem_spec_id)
        if spec is None or spec.workspace_id != workspace_id:
            raise LineageError("problem spec belongs to another workspace")
        if project_id is not None and spec.project_id != project_id:
            raise LineageError("problem spec belongs to another project")
    row = WorkflowRun(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow.id,
        workflow_version_id=version.id if version is not None else None,
        problem_spec_id=problem_spec_id,
        requested_by=requester.id if requester is not None else None,
        initiated_by_type=initiator,
        trigger_type=trigger_type,
        source_type=source_type,
        source_upload_id=source_upload.id if source_upload is not None else None,
        explicit_target=explicit_target,
        resolved_target=resolved_target,
        task_type=task_type,
        status=status,
        started_at=datetime.now(UTC) if status == "running" else None,
    )
    db.add(row)
    db.flush()
    for position, (dataset, input_role) in enumerate(inputs or []):
        add_workflow_run_input(
            db,
            workflow_run=row,
            dataset=dataset,
            input_role=input_role,
            position=position,
        )
    return row


def create_pipeline_run(
    db: Session,
    *,
    workflow_run: WorkflowRun,
    environment,
    dataset: Dataset,
    task: PredictionTask | None,
    pipeline_name: str = "deterministic_ml",
    pipeline_index: int = 0,
    pipeline_purpose: str = "training",
    config: SearchConfig | None = None,
    input_role: str | None = "training",
    commit: bool = True,
) -> Experiment:
    if dataset.workspace_id != workflow_run.workspace_id:
        raise LineageError("pipeline dataset belongs to another workspace")
    if input_role is not None:
        add_workflow_run_input(
            db,
            workflow_run=workflow_run,
            dataset=dataset,
            input_role=input_role,
        )
    from app.services.lab_service import create_experiment
    from app.services.workflow_execution_service import (
        get_or_create_current_pipeline_version,
        get_or_create_pipeline,
        get_workflow_version,
        next_pipeline_run_number,
    )

    pipeline = None
    pipeline_version = None
    run_number = None
    project_id = workflow_run.project_id or dataset.project_id
    workflow = workflow_run.workflow
    workflow_version = workflow_run.workflow_version
    if workflow_version is None and workflow_run.workflow_version_id is not None:
        workflow_version = get_workflow_version(
            db,
            workspace_id=workflow_run.workspace_id,
            workflow_version_id=workflow_run.workflow_version_id,
        )
    if workflow is not None and workflow.project_id is not None and workflow_version is not None:
        pipeline = get_or_create_pipeline(
            db,
            workflow=workflow,
            name=pipeline_name,
            slug=pipeline_name,
            purpose=pipeline_purpose,
            actor=None,
        )
        pipeline_version = get_or_create_current_pipeline_version(
            db,
            pipeline=pipeline,
            workflow_version=workflow_version,
            graph_definition={
                "pipeline_name": pipeline_name,
                "pipeline_purpose": pipeline_purpose,
            },
            config=(config.to_dict() if config is not None else {}),
        )
        run_number = next_pipeline_run_number(db, pipeline.id)

    return create_experiment(
        db,
        environment=environment,
        dataset=dataset,
        task=task,
        workflow_run=workflow_run,
        pipeline_name=pipeline_name,
        pipeline_index=pipeline_index,
        pipeline_purpose=pipeline_purpose,
        config=config,
        commit=commit,
        project_id=project_id,
        pipeline_id=pipeline.id if pipeline is not None else None,
        pipeline_version_id=pipeline_version.id if pipeline_version is not None else None,
        run_number=run_number,
    )


def bind_pipeline_run(
    db: Session,
    *,
    pipeline_run: Experiment,
    workflow_run: WorkflowRun,
    environment,
    dataset: Dataset,
    task: PredictionTask,
    config: SearchConfig,
) -> Experiment:
    """Bind a persisted Labs pipeline shell to its resolved execution inputs."""

    if pipeline_run.workflow_run_id != workflow_run.id:
        raise LineageError("pipeline run belongs to another workflow run")
    if pipeline_run.workspace_id != workflow_run.workspace_id:
        raise LineageError("pipeline run belongs to another workspace")
    if dataset.workspace_id != workflow_run.workspace_id:
        raise LineageError("pipeline dataset belongs to another workspace")
    add_workflow_run_input(
        db,
        workflow_run=workflow_run,
        dataset=dataset,
        input_role="training",
    )
    if pipeline_run.project_id is None:
        pipeline_run.project_id = workflow_run.project_id
    pipeline_run.environment_id = environment.id
    pipeline_run.dataset_id = dataset.id
    pipeline_run.task_id = task.id
    pipeline_run.config = config.to_dict()
    pipeline_run.seed = config.seed
    pipeline_run.failure_reason = None
    db.commit()
    db.refresh(pipeline_run)
    return pipeline_run


def create_model_asset(
    db: Session,
    *,
    workspace_id: UUID,
    workflow: MlWorkflow,
    name: str,
    slug: str,
    description: str = "",
    actor: User | None = None,
) -> ModelAsset:
    _require_actor_write(db, actor, workspace_id)
    if workflow.workspace_id != workspace_id:
        raise LineageError("workflow belongs to another workspace")
    row = ModelAsset(
        workspace_id=workspace_id,
        workflow_id=workflow.id,
        name=name,
        slug=slugify(slug),
        description=description,
        created_by=actor.id if actor is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def _selected_candidate_key(pipeline_run: Experiment) -> str | None:
    result = pipeline_run.result if isinstance(pipeline_run.result, dict) else {}
    selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
    best = result.get("best_single") if isinstance(result.get("best_single"), dict) else {}
    selected = selection.get("selected_candidate_id") or best.get("candidate_id")
    return str(selected) if selected else None


def create_model_version(
    db: Session,
    *,
    model_asset: ModelAsset,
    pipeline_run: Experiment,
    selected_candidate: ExperimentCandidate,
    version: str,
    artifact_uri: str | None = None,
    runtime_environment_id: UUID | None = None,
    code_snapshot_id: UUID | None = None,
    model_artifact_id: UUID | None = None,
    preprocessor_artifact_id: UUID | None = None,
    feature_manifest_artifact_id: UUID | None = None,
    feature_set_version_id: UUID | None = None,
) -> ModelVersion:
    workflow_run = pipeline_run.workflow_run
    if workflow_run is None:
        raise LineageError("pipeline run is not attached to a workflow run")
    if pipeline_run.status != "COMPLETED":
        raise LineageError("pipeline run did not complete successfully")
    if selected_candidate.experiment_id != pipeline_run.id:
        raise LineageError("candidate belongs to another pipeline run")
    if selected_candidate.status.lower() in {"failed", "rejected"}:
        raise LineageError("candidate is not publishable")
    if model_asset.workspace_id != pipeline_run.workspace_id:
        raise LineageError("model asset belongs to another workspace")
    if model_asset.workflow_id != workflow_run.workflow_id:
        raise LineageError("model asset belongs to another workflow")
    selected_key = _selected_candidate_key(pipeline_run)
    if selected_key is None or selected_candidate.candidate_key != selected_key:
        raise LineageError("candidate is not the selected pipeline winner")
    existing_version = db.scalar(
        select(ModelVersion).where(
            (ModelVersion.pipeline_run_id == pipeline_run.id)
            | (ModelVersion.selected_candidate_id == selected_candidate.id)
        )
    )
    if existing_version is not None:
        raise LineageError("pipeline winner already has a model version")
    has_dataset_input = db.scalar(
        select(WorkflowRunInput.id).where(
            WorkflowRunInput.workflow_run_id == workflow_run.id,
            WorkflowRunInput.dataset_id == pipeline_run.dataset_id,
        )
    )
    if has_dataset_input is None:
        raise LineageError("pipeline dataset is missing from workflow run inputs")
    if feature_set_version_id is None:
        from app.services.scientific_lineage_service import (
            latest_pipeline_run_feature_set_version,
        )

        feature_set = latest_pipeline_run_feature_set_version(db, pipeline_run)
        feature_set_version_id = feature_set.id if feature_set is not None else None

    digest_payload = {
        "pipeline_run_id": str(pipeline_run.id),
        "selected_candidate_id": str(selected_candidate.id),
        "candidate_fingerprint": selected_candidate.fingerprint,
        "candidate_payload": selected_candidate.payload,
        "artifact_uri": artifact_uri or pipeline_run.artifact_dir,
    }
    content_digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    metrics = selected_candidate.payload.get("test_metrics") or (
        pipeline_run.result or {}
    ).get("test_metrics") or {}
    row = ModelVersion(
        model_asset_id=model_asset.id,
        version=version,
        workspace_id=pipeline_run.workspace_id,
        project_id=pipeline_run.project_id,
        workflow_id=workflow_run.workflow_id,
        workflow_version_id=workflow_run.workflow_version_id,
        workflow_run_id=workflow_run.id,
        pipeline_id=pipeline_run.pipeline_id,
        pipeline_version_id=pipeline_run.pipeline_version_id,
        pipeline_run_id=pipeline_run.id,
        selected_candidate_id=selected_candidate.id,
        dataset_id=pipeline_run.dataset_id,
        feature_set_version_id=feature_set_version_id,
        runtime_environment_id=runtime_environment_id,
        code_snapshot_id=code_snapshot_id,
        model_artifact_id=model_artifact_id,
        preprocessor_artifact_id=preprocessor_artifact_id,
        feature_manifest_artifact_id=feature_manifest_artifact_id,
        artifact_uri=artifact_uri or pipeline_run.artifact_dir,
        content_digest=content_digest,
        metrics=dict(metrics),
    )
    db.add(row)
    db.flush()
    return row
