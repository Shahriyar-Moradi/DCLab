"""Step 6/7 — Admin Model Registry.

Workspace-owned models this system has trained, from every place it trains one: Lab
experiments (created against client/uploaded data), the simulation pack (the
eight bundled use cases), and client-triggered Labs trials (Step 7 — audited
in full via `ClientLabRunAudit` even though the matching client-facing row
only ever exposes translated insights). Raw detail is restricted to platform
members for the selected workspace; the translation layer never touches it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ClientLabRun, ClientLabRunAudit, Experiment, ExperimentCandidate, PredictionTask, SimulationRun
from app.domain.admin_model_registry import ClientTrialAuditDetail, RegisteredModel


def _experiment_models(db: Session, workspace_id: UUID) -> list[RegisteredModel]:
    experiments = db.scalars(
        select(Experiment)
        .where(Experiment.workspace_id == workspace_id)
        .order_by(Experiment.created_at.desc())
    ).all()
    if not experiments:
        return []
    experiment_ids = [exp.id for exp in experiments]
    task_ids = {exp.task_id for exp in experiments if exp.task_id is not None}
    task_names = {
        task.id: task.name
        for task in db.scalars(select(PredictionTask).where(PredictionTask.id.in_(task_ids)))
    }
    counts = dict(
        db.execute(
            select(ExperimentCandidate.experiment_id, func.count(ExperimentCandidate.id))
            .where(ExperimentCandidate.experiment_id.in_(experiment_ids))
            .group_by(ExperimentCandidate.experiment_id)
        ).all()
    )
    models = []
    for exp in experiments:
        result = exp.result or {}
        best_single = result.get("best_single") or {}
        models.append(
            RegisteredModel(
                id=exp.id,
                source="experiment",
                name=task_names.get(exp.task_id, str(exp.task_id)),
                status=exp.status,
                model_family=best_single.get("model_family"),
                fusion=result.get("fusion"),
                metrics=result.get("test_metrics") or {},
                candidate_count=counts.get(exp.id),
                created_at=exp.created_at,
            )
        )
    return models


def _simulation_models(db: Session, workspace_id: UUID) -> list[RegisteredModel]:
    runs = db.scalars(
        select(SimulationRun)
        .where(SimulationRun.workspace_id == workspace_id)
        .order_by(SimulationRun.created_at.desc())
    ).all()
    return [
        RegisteredModel(
            id=run.id,
            source="simulation",
            name=run.use_case,
            status="COMPLETED",
            model_family=None,
            fusion=run.fusion,
            metrics=(run.payload or {}).get("metrics") or {},
            candidate_count=(run.payload or {}).get("n_candidates_evaluated"),
            created_at=run.created_at,
        )
        for run in runs
    ]


def _client_trial_models(db: Session, workspace_id: UUID) -> list[RegisteredModel]:
    audits = db.scalars(
        select(ClientLabRunAudit)
        .join(ClientLabRun, ClientLabRunAudit.client_lab_run_id == ClientLabRun.id)
        .where(ClientLabRun.workspace_id == workspace_id)
        .order_by(ClientLabRunAudit.created_at.desc())
    ).all()
    return [
        RegisteredModel(
            id=audit.id,
            source="client_trial",
            name=audit.use_case,
            status="COMPLETED",
            model_family=None,
            fusion=(audit.payload or {}).get("fusion"),
            metrics=(audit.payload or {}).get("metrics") or {},
            candidate_count=(audit.payload or {}).get("n_candidates_evaluated"),
            created_at=audit.created_at,
            client_lab_run_id=audit.client_lab_run_id,
        )
        for audit in audits
    ]


def list_registered_models(db: Session, workspace_id: UUID) -> list[RegisteredModel]:
    models = (
        _experiment_models(db, workspace_id)
        + _simulation_models(db, workspace_id)
        + _client_trial_models(db, workspace_id)
    )
    models.sort(key=lambda model: model.created_at, reverse=True)
    return models


def get_client_trial_audit(
    db: Session, audit_id: UUID, workspace_id: UUID
) -> ClientTrialAuditDetail | None:
    audit = db.scalar(
        select(ClientLabRunAudit)
        .join(ClientLabRun, ClientLabRunAudit.client_lab_run_id == ClientLabRun.id)
        .where(ClientLabRunAudit.id == audit_id, ClientLabRun.workspace_id == workspace_id)
    )
    if audit is None:
        return None
    return ClientTrialAuditDetail(
        id=audit.id,
        client_lab_run_id=audit.client_lab_run_id,
        use_case=audit.use_case,
        payload=audit.payload or {},
        created_at=audit.created_at,
    )
