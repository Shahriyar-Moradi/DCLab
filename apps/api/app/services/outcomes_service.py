"""Business view read (P7.6-A): ``GET /v1/projects/{project_id}/outcomes``.

Reads only what other services already own and authorize: the project (``get_project``:
workspace-scoped, other tenants 404), the model in use (the ``champion_model`` ref), its
current approval (``model_release_service.current_release``), the model card of THAT version
(``experiment_service.model_card_read``, final test for people only) and the latest completed
scoring of THAT version (``batch_prediction_service.batch_prediction_read``). Nothing from any
other model version is read into the answer. Sentences come from ``app.domain.outcomes``.

The download path it returns is the existing prediction download, which any workspace reader may
use for any completed prediction of the workspace by id; this read adds no access of its own.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BatchPrediction, ModelVersion, Project, ProjectRef, User
from app.domain.batch_predictions import PREDICTION_COMPLETED
from app.domain.errors import ExperimentRequestError, IdentityError
from app.domain.model_card import ModelCardRead
from app.domain.outcomes import (
    PROJECT_CHOICES_MAX,
    OutcomesList,
    OutcomesProjectChoice,
    OutcomesProjectList,
    OutcomesRead,
    build_outcomes,
    clean,
    list_text,
)
from app.services.authorization_service import can_read_workspace
from app.services.batch_prediction_service import batch_prediction_read
from app.services.experiment_service import model_card_read
from app.services.model_release_service import current_release
from app.services.project_service import get_project


def _model_in_use(db: Session, workspace_id: UUID, project_id: UUID) -> ModelVersion | None:
    version_id = db.scalar(
        select(ProjectRef.model_version_id).where(
            ProjectRef.workspace_id == workspace_id,
            ProjectRef.project_id == project_id,
            ProjectRef.ref_kind == "champion_model",
        )
    )
    if version_id is None:
        return None
    return db.scalar(
        select(ModelVersion).where(
            ModelVersion.id == version_id,
            ModelVersion.workspace_id == workspace_id,
            ModelVersion.project_id == project_id,
        )
    )


def _card(db: Session, actor: User, workspace_id: UUID, model: ModelVersion, include_final: bool) -> ModelCardRead | None:
    try:
        return model_card_read(
            db, actor=actor, workspace_id=workspace_id, model_version_id=model.id,
            include_final_evaluation=include_final,
        )
    except ExperimentRequestError as exc:
        if exc.code == "model_card_unavailable":  # the run has no locked evidence for this version
            return None
        raise


def latest_list(db: Session, actor: User, workspace_id: UUID, model: ModelVersion) -> OutcomesList | None:
    """The newest COMPLETED scoring of the model in use (queued, running and failed ones are skipped)."""

    prediction_id = db.scalar(
        select(BatchPrediction.id)
        .where(
            BatchPrediction.workspace_id == workspace_id,
            BatchPrediction.model_version_id == model.id,
            BatchPrediction.status == PREDICTION_COMPLETED,
        )
        .order_by(BatchPrediction.completed_at.desc(), BatchPrediction.id.desc())
        .limit(1)
    )
    if prediction_id is None:
        return None
    read = batch_prediction_read(db, actor=actor, workspace_id=workspace_id, prediction_id=prediction_id)
    if read is None or read.output is None or read.completed_at is None or read.rows_out is None:
        return None
    has_reasons = bool(read.reasons and read.reasons.available)
    top_k = read.reasons.top_k if read.reasons is not None else 0
    return OutcomesList(
        id=read.id,
        completed_at=read.completed_at,
        rows=read.rows_out,
        has_reasons=has_reasons,
        text=list_text(read.rows_out, has_reasons=has_reasons, top_k=top_k),
        download_path=read.output.download_path,
    )


def project_outcomes(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, include_final_evaluation: bool
) -> OutcomesRead:
    """Raises ``IdentityError`` (403) / ``ProjectNotFoundError`` (404, also for other tenants)."""

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    model = _model_in_use(db, workspace_id, project.id)
    if model is None:
        return build_outcomes(project_name=project.name, saved_at=None, approved_at=None, card=None,
                              weekly_list=None, include_final=include_final_evaluation)
    release = current_release(db, workspace_id=workspace_id, project_id=project.id)
    approved_at = release.created_at if release is not None and release.model_version_id == model.id else None
    return build_outcomes(
        project_name=project.name,
        saved_at=model.created_at,
        approved_at=approved_at,
        card=_card(db, actor, workspace_id, model, include_final_evaluation),
        weekly_list=latest_list(db, actor, workspace_id, model),
        include_final=include_final_evaluation,
    )


def outcome_projects(db: Session, *, actor: User, workspace_id: UUID) -> OutcomesProjectList:
    """Projects of this workspace with a model in use, newest first, id and name only (the page's
    project choice). Raises ``IdentityError`` (403) for a caller who may not read the workspace."""

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    rows = db.execute(
        select(Project.id, Project.name)
        .join(
            ProjectRef,
            (ProjectRef.project_id == Project.id)
            & (ProjectRef.workspace_id == workspace_id)
            & (ProjectRef.ref_kind == "champion_model")
            & ProjectRef.model_version_id.is_not(None),
        )
        .where(Project.workspace_id == workspace_id)
        .order_by(Project.created_at.desc(), Project.id.desc())
        .limit(PROJECT_CHOICES_MAX)
    ).all()
    return OutcomesProjectList(
        items=[OutcomesProjectChoice(id=pid, name=clean(name, 120) or "Project") for pid, name in rows]
    )

