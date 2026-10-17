"""Project flow read (V7 BE-A; ``GET /v1/projects/{id}/flow``): typed facts for the eight steps of
the flow bar (Data, Goal & test design, Experiments, Improve, Model, Predictions, Monitoring,
History). Read-only, no tables, a fixed number of workspace-filtered statements.

Facts only: the web writes the sentences. Scores are CV (the locked winner's selection score);
the final holdout is never read. The file name is user data (redacted, labelled untrusted).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BatchPrediction,
    Dataset,
    Experiment,
    ExperimentCandidate,
    ModelSelectionDecision,
    ModelVersion,
    MonitoringWindow,
    ProjectDecisionRecord,
    ProjectRef,
    ScoringSchedule,
    SplitPlan,
    User,
)
from app.domain.batch_predictions import PREDICTION_COMPLETED, PREDICTION_QUEUED, PREDICTION_RUNNING
from app.domain.data_plane import DATASET_PURPOSE_TRAINING
from app.domain.home_reads import (
    FlowDataStep,
    FlowExperimentsStep,
    FlowGoalStep,
    FlowHistoryStep,
    FlowImproveStep,
    FlowModelStep,
    FlowMonitoringStep,
    FlowPredictionsStep,
    ProjectFlowRead,
)
from app.domain.state_graph import GRAPH_EXPERIMENT_WINDOW
from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.services.experiment_service import STATUS_SQL
from app.services.graph_service import newer_dataset, untrusted_text, uploaded_file
from app.services.home_read_service import (
    UNFINISHED_RUN_STATUSES,
    code_value,
    latest_window_revision,
    newest_week_of_model_in_use,
    project_summaries,
)
from app.services.project_service import get_project

FILE_NAME_MAX_CHARS = 128


def _data(db: Session, ws: UUID, project, refs: dict[str, ProjectRef]) -> FlowDataStep:
    in_use = refs["dataset"].dataset_id if "dataset" in refs else None
    order = [Dataset.created_at.desc(), Dataset.id.desc()]
    if in_use is not None:
        order.insert(0, (Dataset.id == in_use).desc())
    row = db.execute(
        select(Dataset.id, Dataset.name, Dataset.row_count, Dataset.created_at, func.count().over())
        .where(Dataset.workspace_id == ws, Dataset.project_id == project.id,
               Dataset.purpose == DATASET_PURPOSE_TRAINING, uploaded_file())
        .order_by(*order).limit(1)
    ).first()
    if row is None:
        return FlowDataStep(state="todo")
    newer = newer_dataset(db, project, list(refs.values()))
    return FlowDataStep(
        state="done", dataset_id=row[0], in_use=row[0] == in_use, file_name=untrusted_text(row[1], FILE_NAME_MAX_CHARS),
        row_count=row[2], uploaded_at=row[3], training_files=int(row[4]), newer_file=newer is not None,
    )


def _experiments(db: Session, ws: UUID, pid: UUID, split_plan_id: UUID | None) -> tuple[FlowExperimentsStep,
                                                                                       FlowImproveStep]:
    total, running, branches = db.execute(
        select(func.count(), func.count().filter(STATUS_SQL.in_(sorted(UNFINISHED_RUN_STATUSES))),
               func.count().filter(Experiment.parent_pipeline_run_id.is_not(None)))
        .where(Experiment.workspace_id == ws, Experiment.project_id == pid)
    ).one()
    msd = ModelSelectionDecision
    locked = db.execute(
        select(Experiment.id, Experiment.run_number, Experiment.split_plan_id, msd.selection_metric, msd.selected_score)
        .join(msd, (msd.pipeline_run_id == Experiment.id) & (msd.workspace_id == Experiment.workspace_id))
        .where(Experiment.workspace_id == ws, Experiment.project_id == pid,
               Experiment.scientific_evidence_locked_at.is_not(None))
        .order_by(msd.locked_at.desc(), Experiment.id.desc()).limit(GRAPH_EXPERIMENT_WINDOW)
    ).all()
    # Comparable runs only: one split plan (the ref, else the newest locked run's) and one metric.
    plan = split_plan_id or (locked[0][2] if locked else None)
    same_plan = [row for row in locked if row[2] == plan]
    metric = same_plan[0][3] if same_plan else None
    pool = [row for row in same_plan if row[3] == metric and row[4] is not None]
    best = None
    if pool:
        sign = -1.0 if metric in LOWER_IS_BETTER else 1.0
        best = max(reversed(pool), key=lambda row: sign * float(row[4]))  # ties: the earliest locked run
    experiments = FlowExperimentsStep(
        state="in_progress" if running else "done" if total else "todo", run_count=int(total),
        running_count=int(running), best_experiment_id=best[0] if best else None,
        best_run_number=best[1] if best else None, selection_metric=code_value(metric) if best else None,
        best_cv_score=float(best[4]) if best else None,
    )
    return experiments, FlowImproveStep(state="done" if branches else "todo", branch_run_count=int(branches))


def _model(db: Session, ws: UUID, model_version_id: UUID | None) -> FlowModelStep:
    if model_version_id is None:
        return FlowModelStep(state="todo")
    cand = ExperimentCandidate
    locked = Experiment.scientific_evidence_locked_at.is_not(None)
    row = db.execute(
        select(ModelVersion.version, Experiment.run_number, cand.model_family, cand.algorithm,
               # The locked threshold only (chosen on CV out-of-fold rows); nothing else of `result` is read.
               Experiment.result[("decision_threshold", "value")].as_float(), locked)
        .select_from(ModelVersion)
        .outerjoin(Experiment, (Experiment.id == ModelVersion.pipeline_run_id)
                   & (Experiment.workspace_id == ModelVersion.workspace_id))
        .outerjoin(cand, (cand.id == ModelVersion.selected_candidate_id) & (cand.workspace_id == ModelVersion.workspace_id))
        .where(ModelVersion.workspace_id == ws, ModelVersion.id == model_version_id)
    ).first()
    if row is None:
        return FlowModelStep(state="done", model_version_id=model_version_id)
    version, number, family, algorithm, threshold, is_locked = row
    return FlowModelStep(
        state="done", model_version_id=model_version_id, version=version, run_number=number,
        model_family=code_value(family), algorithm=code_value(algorithm),
        decision_threshold=float(threshold) if is_locked and threshold is not None else None,
    )


def _predictions(db: Session, ws: UUID, pid: UUID) -> FlowPredictionsStep:
    bp = BatchPrediction
    completed, active, last_at = db.execute(
        select(func.count().filter(bp.status == PREDICTION_COMPLETED),
               func.count().filter(bp.status.in_((PREDICTION_QUEUED, PREDICTION_RUNNING))),
               func.max(bp.completed_at).filter(bp.status == PREDICTION_COMPLETED))
        .where(bp.workspace_id == ws, bp.project_id == pid)
    ).one()
    last_rows = db.scalar(
        select(bp.rows_out).where(bp.workspace_id == ws, bp.project_id == pid, bp.status == PREDICTION_COMPLETED)
        .order_by(bp.completed_at.desc().nulls_last(), bp.id.desc()).limit(1)
    ) if completed else None
    schedule = db.execute(
        select(ScoringSchedule.enabled, ScoringSchedule.next_planned_for)
        .where(ScoringSchedule.workspace_id == ws, ScoringSchedule.project_id == pid)
    ).first()
    return FlowPredictionsStep(
        state="done" if completed else "in_progress" if active else "todo", completed_scorings=int(completed),
        last_scored_at=last_at, last_rows_scored=last_rows,
        schedule_enabled=bool(schedule[0]) if schedule else None,
        next_planned_for=schedule[1] if schedule and schedule[0] else None,
    )


def _monitoring(db: Session, ws: UUID, pid: UUID) -> FlowMonitoringStep:
    mw = MonitoringWindow
    row = db.execute(
        select(mw.status, mw.window_start, mw.input_drift["max_psi"].as_float())
        .where(mw.workspace_id == ws, mw.project_id == pid, latest_window_revision(), newest_week_of_model_in_use())
        .order_by(mw.window_start.desc(), mw.revision.desc()).limit(1)
    ).first()
    if row is None:
        return FlowMonitoringStep(state="todo")
    return FlowMonitoringStep(state="alert" if row[0] == "alert" else "done", status=code_value(row[0]),
                              window_start=row[1], max_psi=row[2])


def project_flow(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID,
                 viewer_is_agent: bool = False) -> ProjectFlowRead:
    """``ProjectNotFoundError`` for a project of another workspace (indistinguishable from unknown)."""

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    ws, pid = workspace_id, project.id
    summary = project_summaries(db, workspace_id=ws, project_ids=[pid], actor=actor,
                                viewer_is_agent=viewer_is_agent)[pid]
    refs = {ref.ref_kind: ref for ref in db.scalars(
        select(ProjectRef).where(ProjectRef.workspace_id == ws, ProjectRef.project_id == pid))}
    split_plan_id = refs["split_plan"].split_plan_id if "split_plan" in refs else None
    folds = db.scalar(select(SplitPlan.validation_folds).where(
        SplitPlan.workspace_id == ws, SplitPlan.id == split_plan_id)) if split_plan_id else None
    goal_state = "done" if summary.goal and split_plan_id else "in_progress" if summary.goal else "todo"
    experiments, improve = _experiments(db, ws, pid, split_plan_id)
    count, last_at = db.execute(
        select(func.count(), func.max(ProjectDecisionRecord.recorded_at))
        .where(ProjectDecisionRecord.workspace_id == ws, ProjectDecisionRecord.project_id == pid)
    ).one()
    return ProjectFlowRead(
        project_id=pid, stage=summary.stage, waiting_decisions=summary.waiting_decisions,
        data=_data(db, ws, project, refs),
        goal=FlowGoalStep(state=goal_state, goal=summary.goal, split_plan_id=split_plan_id, validation_folds=folds),
        experiments=experiments, improve=improve,
        model=_model(db, ws, summary.champion.model_version_id if summary.champion else None),
        predictions=_predictions(db, ws, pid), monitoring=_monitoring(db, ws, pid),
        history=FlowHistoryStep(state="done" if count else "todo", decision_count=int(count), last_decision_at=last_at),
    )
