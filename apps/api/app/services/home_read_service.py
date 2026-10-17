"""Home and activity read models (P4.15-A): read-only projections, no tables, no writes.

``list_activity`` merges five keyset-paged sources newest first: decision records, experiment rows
(queued at ``created_at``, finished at ``ended_at``) and specialist/ops agent runs (started at
``created_at``, finished at ``finished_at``; assistant threads and their turns stay private to
``/v1/assistant``, the ``/v1/agent-runs`` rule). Each source is one bounded statement filtered by
workspace (and project), ordered by ``(occurred_at, kind rank, id)`` and cut at ``limit + 1``; the
merge keeps the global top ``limit``. The opaque cursor carries that triple and is bound to the
workspace and filter set (``cursor_codec``).

``project_summaries`` adds goal, champion CV metrics and latest run status to a project list in
four statements whatever the number of projects (no N+1).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    AgentRun,
    Dataset,
    DatasetAsset,
    EvaluationMetric,
    ExecutionRequest,
    Experiment,
    ExperimentCandidate,
    MlRunEvent,
    ModelEvaluation,
    ModelSelectionDecision,
    ModelVersion,
    MonitoringWindow,
    ProblemSpec,
    ProjectDecisionRecord,
    ProjectRef,
    RetrainComparison,
    RetrainRequest,
    ScoringScheduleRun,
    SplitPlan,
    User,
    WorkflowRun,
)
from app.domain.agent_records import AGENT_SUBJECT_COLUMNS
from app.domain.data_plane import DATASET_PURPOSE_TRAINING
from app.domain.decision_records import ACTOR_AGENT, ACTOR_RULE, SUBJECT_COLUMNS
from app.domain.errors import IdentityError, InvalidCursorError
from app.domain.home_reads import (
    ACTIVITY_KIND_RANK,
    ACTIVITY_PAGE_DEFAULT,
    ACTIVITY_PAGE_MAX,
    PROJECT_OBJECTIVE_READ_MAX_CHARS,
    PROJECT_TARGET_READ_MAX_CHARS,
    ActivityActorRead,
    ActivityItemRead,
    ActivityLinkRead,
    ActivityPage,
    ActivityRunResultRead,
    ActivitySubjectRead,
    ChampionSummaryRead,
    LatestRunRead,
    ProjectGoalRead,
    ProjectSummaryRead,
    RunProgressRead,
)
from app.domain.scoring_schedules import OUTCOME_FAILED_TO_QUEUE, OUTCOME_QUEUED
from app.domain.state_graph import node_key
from app.services.authorization_service import can_read_workspace
from app.services.cursor_codec import open_cursor, scope_digest, sign_cursor
from app.services.experiment_service import STATUS_SQL
from app.services.graph_service import untrusted_text, uploaded_file
from app.services.project_service import get_project
from app.services.proposal_review_service import RUN_KINDS_LISTED

# --- activity: one-line summaries from typed vocabulary only ----------------------------------

_DECISION_LABELS: dict[str, str] = {
    "winner_locked": "Winner locked",
    "split_plan_created": "Split plan created",
    "ref_initialized": "Project refs initialized",
    "problem_spec_locked": "Problem spec locked",
    "ref_moved": "Ref move",
    "champion_promoted": "Champion promotion",
    "experiment_accepted": "Experiment acceptance",
    "experiment_rejected": "Experiment rejection",
    "proposal_accepted": "AI proposal accepted",
    "proposal_rejected": "AI proposal rejected",
    "decision_point_resolved": "Decision point resolved",
    "proposal_reverted": "AI proposal reverted",
    "operating_point_chosen": "Operating point chosen",
    "scoring_schedule_changed": "Weekly scoring schedule changed",
    "monitoring_outcomes_linked": "Real outcomes linked for monitoring",
    "target_confirmed": "Target confirmed",
    "test_design_confirmed": "Test design confirmed",
    "goal_metric_confirmed": "Ranking metric set",
    "goal_rule_confirmed": "Business rule set",
    "retrain_policy_changed": "Retrain policy changed",
    "monitoring_thresholds_changed": "Monitoring thresholds changed",
}
# Types whose label already says what happened; the others append the state.
_SELF_DESCRIBING = frozenset(
    {"winner_locked", "split_plan_created", "ref_initialized", "problem_spec_locked", "proposal_accepted",
     "proposal_rejected", "decision_point_resolved", "proposal_reverted", "operating_point_chosen",
     "scoring_schedule_changed", "monitoring_outcomes_linked", "target_confirmed", "test_design_confirmed",
     "goal_metric_confirmed", "goal_rule_confirmed", "retrain_policy_changed", "monitoring_thresholds_changed"}
)
_RUN_FINISHED = {
    "completed": "completed", "failed": "failed", "cancelled": "cancelled", "skipped": "skipped",
}
_RULE_INITIATORS = frozenset({"system", "schedule"})
UNFINISHED_RUN_STATUSES = frozenset({"queued", "running", "cancelling", "needs_input"})
_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


def code_value(value: Any) -> str | None:
    """A code-owned vocabulary value read back from the database (anything else is dropped)."""

    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


# --- shared filters (activity, inbox, flow) ------------------------------------------------------


def latest_window_revision(mw: Any = MonitoringWindow) -> Any:
    """Monitoring windows are append-only revisions; reads use the highest one per week."""

    newer = aliased(MonitoringWindow)
    return ~exists().where(newer.workspace_id == mw.workspace_id, newer.project_id == mw.project_id,
                           newer.model_version_id == mw.model_version_id, newer.window_start == mw.window_start,
                           newer.revision > mw.revision)


def newest_week_of_model_in_use(mw: Any = MonitoringWindow) -> Any:
    """The newest week of the project's model in use (``champion_model`` ref)."""

    later = aliased(MonitoringWindow)
    return and_(
        ~exists().where(later.workspace_id == mw.workspace_id, later.project_id == mw.project_id,
                        later.model_version_id == mw.model_version_id, later.window_start > mw.window_start),
        exists().where(ProjectRef.workspace_id == mw.workspace_id, ProjectRef.project_id == mw.project_id,
                       ProjectRef.ref_kind == "champion_model", ProjectRef.model_version_id == mw.model_version_id),
    )


def decision_summary(decision_type: str, state: str) -> str:
    label = _DECISION_LABELS.get(decision_type, "Decision")
    if decision_type in _SELF_DESCRIBING and state == "accepted":
        return label
    return f"{label} {state}" if state in ("proposed", "accepted", "rejected") else label


def run_label(run_number: int | None, branch: bool) -> str:
    noun = "Branch run" if branch else "Run"
    return f"{noun} #{run_number}" if isinstance(run_number, int) and run_number > 0 else noun


def _agent_label(agent_key: str) -> str:
    return f"Agent {agent_key}"


def _subject(kind: str, node_id: UUID | None) -> ActivitySubjectRead:
    return ActivitySubjectRead(kind=kind, id=node_id, key=node_key(kind, node_id) if node_id is not None else None)


# --- activity: cursor ------------------------------------------------------------------------

_Key = tuple[datetime, int, UUID]


def _scope(workspace_id: UUID, project_id: UUID | None) -> str:
    return f"activity:{workspace_id}:{scope_digest({'project_id': project_id})}"


def _open(cursor: str | None, scope: str) -> _Key | None:
    if cursor is None or not cursor.strip():
        return None
    message = "cursor is not an activity cursor for this workspace and filter set"
    try:
        stamp, rank, ident = open_cursor(cursor, scope, message=message)
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None or type(rank) is not int or rank not in ACTIVITY_KIND_RANK.values():
            raise ValueError(message)
        return at, rank, UUID(ident)
    except (InvalidCursorError, ValueError, TypeError) as exc:
        raise InvalidCursorError(message) from exc


def keyset_before(after: _Key | None, kind: str, at_column: Any, id_column: Any,
                  ranks: dict[str, int] = ACTIVITY_KIND_RANK) -> Any:
    """Rows of one source strictly after the cursor in ``(occurred_at, rank, id)`` DESC order
    (also the inbox's merge order, P4.16-A)."""

    if after is None:
        return None
    at, rank, ident = after
    mine = ranks[kind]
    if mine < rank:
        return at_column <= at
    if mine > rank:
        return at_column < at
    return tuple_(at_column, id_column) < tuple_(at, ident)


# --- activity: sources -------------------------------------------------------------------------


def _decision_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None, n: int,
                    viewer_id: UUID | None) -> list[ActivityItemRead]:
    pdr = ProjectDecisionRecord
    stmt = (
        select(pdr, AgentRun.agent_key)
        .outerjoin(AgentRun, and_(AgentRun.id == pdr.actor_agent_run_id, AgentRun.workspace_id == pdr.workspace_id,
                                  AgentRun.kind.in_(RUN_KINDS_LISTED)))  # never name an assistant thread
        .where(pdr.workspace_id == ws)
    )
    if project_id is not None:
        stmt = stmt.where(pdr.project_id == project_id)
    cut = keyset_before(after, "decision", pdr.recorded_at, pdr.id)
    if cut is not None:
        stmt = stmt.where(cut)
    items = []
    for row, agent_key in db.execute(stmt.order_by(pdr.recorded_at.desc(), pdr.id.desc()).limit(n)).all():
        column = SUBJECT_COLUMNS.get(row.subject_kind)
        subject_id = getattr(row, column) if column else row.project_id
        if row.actor_kind == ACTOR_RULE:
            actor = ActivityActorRead(kind="rule", rule=row.actor_rule)
        elif row.actor_kind == ACTOR_AGENT:
            actor = ActivityActorRead(kind="agent", agent_key=agent_key,
                                      agent_run_id=row.actor_agent_run_id if agent_key is not None else None)
        else:
            actor = ActivityActorRead(kind="person", is_you=viewer_id is not None and row.actor_user_id == viewer_id)
        items.append(ActivityItemRead(
            id=f"decision:{row.id}", kind="decision", occurred_at=row.recorded_at, project_id=row.project_id,
            actor=actor, subject=_subject(row.subject_kind, subject_id),
            summary=decision_summary(row.decision_type, row.state), status=row.state,
            decision_type=row.decision_type, link=ActivityLinkRead(kind="decision_record", id=row.id),
        ))
    return items


def _run_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None, n: int,
               viewer_id: UUID | None, *, finished: bool) -> list[ActivityItemRead]:
    kind = "run_finished" if finished else "run_queued"
    at_column = Experiment.ended_at if finished else Experiment.created_at
    by_token = exists().where(
        ExecutionRequest.pipeline_run_id == Experiment.id,
        ExecutionRequest.workspace_id == Experiment.workspace_id,
        ExecutionRequest.initiated_by_service_token_id.is_not(None),
    )
    msd, cand = ModelSelectionDecision, ExperimentCandidate
    stmt = (
        select(Experiment.id, Experiment.project_id, at_column, Experiment.run_number,
               Experiment.parent_pipeline_run_id, WorkflowRun.initiated_by_type, WorkflowRun.requested_by,
               by_token.label("by_token"), STATUS_SQL if finished else Experiment.status,
               msd.selection_metric, msd.selected_score, cand.model_family, cand.algorithm)
        .outerjoin(WorkflowRun, and_(WorkflowRun.id == Experiment.workflow_run_id,
                                     WorkflowRun.workspace_id == Experiment.workspace_id))
        # The locked winner (CV selection score only; the final holdout is never read here).
        .outerjoin(msd, and_(finished, msd.pipeline_run_id == Experiment.id, msd.workspace_id == Experiment.workspace_id,
                             Experiment.scientific_evidence_locked_at.is_not(None)))
        .outerjoin(cand, and_(cand.id == msd.selected_candidate_id, cand.workspace_id == msd.workspace_id))
        .where(Experiment.workspace_id == ws)
    )
    if finished:
        stmt = stmt.where(Experiment.ended_at.is_not(None))
    if project_id is not None:
        stmt = stmt.where(Experiment.project_id == project_id)
    cut = keyset_before(after, kind, at_column, Experiment.id)
    if cut is not None:
        stmt = stmt.where(cut)
    items = []
    for exp_id, proj, at, number, parent, initiator, requester, token, status, metric, score, family, algo in db.execute(
        stmt.order_by(at_column.desc(), Experiment.id.desc()).limit(n)
    ).all():
        result = None
        if finished and code_value(metric) and score is not None:
            result = ActivityRunResultRead(model_family=code_value(family), algorithm=code_value(algo),
                                           metric=metric, cv_score=float(score))
        if token or initiator == "agent":
            actor = ActivityActorRead(kind="agent")
        elif initiator in _RULE_INITIATORS:
            actor = ActivityActorRead(kind="rule")
        else:
            actor = ActivityActorRead(kind="person", is_you=viewer_id is not None and requester == viewer_id)
        label = run_label(number, parent is not None)
        public = _RUN_FINISHED.get(str(status), "ended") if finished else None
        items.append(ActivityItemRead(
            id=f"{kind}:{exp_id}", kind=kind, occurred_at=at, project_id=proj, actor=actor,
            subject=_subject("experiment", exp_id),
            summary=f"{label} {public}" if finished else f"{label} queued",
            status=str(status) if finished else None, link=ActivityLinkRead(kind="experiment", id=exp_id),
            run_result=result,
        ))
    return items


def _agent_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None, n: int,
                 *, finished: bool) -> list[ActivityItemRead]:
    kind = "agent_run_finished" if finished else "agent_run_started"
    at_column = AgentRun.finished_at if finished else AgentRun.created_at
    stmt = select(AgentRun).where(AgentRun.workspace_id == ws, AgentRun.kind.in_(RUN_KINDS_LISTED))
    if finished:
        stmt = stmt.where(AgentRun.finished_at.is_not(None))
    if project_id is not None:
        stmt = stmt.where(AgentRun.project_id == project_id)
    cut = keyset_before(after, kind, at_column, AgentRun.id)
    if cut is not None:
        stmt = stmt.where(cut)
    items = []
    for run in db.scalars(stmt.order_by(at_column.desc(), AgentRun.id.desc()).limit(n)):
        column = AGENT_SUBJECT_COLUMNS.get(run.subject_kind or "")
        if column:
            subject = _subject(run.subject_kind, getattr(run, column))
        elif run.project_id is not None:
            subject = _subject("project", run.project_id)
        else:
            subject = ActivitySubjectRead(kind=run.subject_kind or "workspace")
        label = _agent_label(run.agent_key)
        items.append(ActivityItemRead(
            id=f"{kind}:{run.id}", kind=kind, occurred_at=run.finished_at if finished else run.created_at,
            project_id=run.project_id,
            actor=ActivityActorRead(kind="agent", agent_key=run.agent_key, agent_run_id=run.id),
            subject=subject,
            summary=f"{label} {run.status.replace('_', ' ')}" if finished else f"{label} started",
            status=run.status if finished else None, link=ActivityLinkRead(kind="agent_run", id=run.id),
        ))
    return items


def _page(db: Session, stmt: Any, kind: str, at_column: Any, id_column: Any, project_column: Any,
          project_id: UUID | None, after: _Key | None, n: int) -> list[Any]:
    if project_id is not None:
        stmt = stmt.where(project_column == project_id)
    cut = keyset_before(after, kind, at_column, id_column)
    if cut is not None:
        stmt = stmt.where(cut)
    return db.execute(stmt.order_by(at_column.desc(), id_column.desc()).limit(n)).all()


def _upload_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None, n: int,
                  viewer_id: UUID | None) -> list[ActivityItemRead]:
    """Uploaded files of a project (never a run's prepared table); no file name (free text)."""

    stmt = (
        select(Dataset.id, Dataset.project_id, Dataset.created_at, Dataset.purpose,
               Dataset.uploaded_by_service_token_id, DatasetAsset.created_by)
        .outerjoin(DatasetAsset, and_(DatasetAsset.id == Dataset.dataset_asset_id,
                                      DatasetAsset.workspace_id == Dataset.workspace_id))
        .where(Dataset.workspace_id == ws, Dataset.project_id.is_not(None), uploaded_file())
    )
    items = []
    for ds_id, proj, at, purpose, token, creator in _page(db, stmt, "dataset_uploaded", Dataset.created_at,
                                                          Dataset.id, Dataset.project_id, project_id, after, n):
        actor = (ActivityActorRead(kind="agent") if token is not None else
                 ActivityActorRead(kind="person", is_you=viewer_id is not None and creator == viewer_id))
        noun = "Training file" if purpose == DATASET_PURPOSE_TRAINING else "Scoring file"
        items.append(ActivityItemRead(
            id=f"dataset_uploaded:{ds_id}", kind="dataset_uploaded", occurred_at=at, project_id=proj, actor=actor,
            subject=_subject("dataset_version", ds_id), summary=f"{noun} uploaded", status=code_value(purpose),
            link=ActivityLinkRead(kind="dataset_version", id=ds_id),
        ))
    return items


def _final_test_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None,
                      n: int) -> list[ActivityItemRead]:
    """When a run's selected model met its final test (first completed evaluation): no values."""

    ev, earlier, msd = ModelEvaluation, aliased(ModelEvaluation), ModelSelectionDecision
    stmt = (
        select(Experiment.id, Experiment.project_id, ev.created_at, Experiment.run_number,
               Experiment.parent_pipeline_run_id)
        .select_from(ev)
        .join(msd, and_(msd.selected_candidate_id == ev.candidate_id, msd.workspace_id == ev.workspace_id))
        .join(Experiment, and_(Experiment.id == msd.pipeline_run_id, Experiment.workspace_id == msd.workspace_id))
        .where(ev.workspace_id == ws, ev.evaluation_scope == "final_holdout", ev.status == "completed",
               ~exists().where(earlier.workspace_id == ev.workspace_id, earlier.candidate_id == ev.candidate_id,
                               earlier.evaluation_scope == "final_holdout", earlier.status == "completed",
                               tuple_(earlier.created_at, earlier.id) < tuple_(ev.created_at, ev.id)))
    )
    return [
        ActivityItemRead(
            id=f"final_test_run:{exp_id}", kind="final_test_run", occurred_at=at, project_id=proj,
            actor=ActivityActorRead(kind="rule"), subject=_subject("experiment", exp_id),
            summary=f"Final test run once for {run_label(number, parent is not None)}",
            link=ActivityLinkRead(kind="experiment", id=exp_id),
        )
        for exp_id, proj, at, number, parent in _page(db, stmt, "final_test_run", ev.created_at, Experiment.id,
                                                      Experiment.project_id, project_id, after, n)
    ]


def _drift_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None,
                 n: int) -> list[ActivityItemRead]:
    mw = MonitoringWindow
    stmt = select(mw.id, mw.project_id, mw.computed_at, mw.model_version_id).where(
        mw.workspace_id == ws, mw.status == "alert", latest_window_revision())
    return [
        ActivityItemRead(
            id=f"drift_alert:{window_id}", kind="drift_alert", occurred_at=at, project_id=proj,
            actor=ActivityActorRead(kind="rule"), subject=_subject("model_version", version_id),
            summary="Weekly monitoring found drift (alert)", status="alert",
            link=ActivityLinkRead(kind="monitoring_window", id=window_id),
        )
        for window_id, proj, at, version_id in _page(db, stmt, "drift_alert", mw.computed_at, mw.id, mw.project_id,
                                                     project_id, after, n)
    ]


def _scheduled_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None,
                     n: int) -> list[ActivityItemRead]:
    run = ScoringScheduleRun
    stmt = select(run.id, run.project_id, run.created_at, run.outcome, run.batch_prediction_id).where(
        run.workspace_id == ws)
    items = []
    for run_id, proj, at, outcome, prediction in _page(db, stmt, "scheduled_scoring", run.created_at, run.id,
                                                       run.project_id, project_id, after, n):
        verb = ("started" if outcome == OUTCOME_QUEUED else
                "could not start" if outcome == OUTCOME_FAILED_TO_QUEUE else "skipped")
        items.append(ActivityItemRead(
            id=f"scheduled_scoring:{run_id}", kind="scheduled_scoring", occurred_at=at, project_id=proj,
            actor=ActivityActorRead(kind="rule"),
            subject=_subject("batch_prediction", prediction) if prediction else _subject("project", proj),
            summary=f"Weekly scoring {verb}", status=code_value(outcome),
            link=ActivityLinkRead(kind="scoring_schedule_run", id=run_id),
        ))
    return items


_RETRAIN_REQUESTED = {"person": "Retrain on newest data started", "auto": "Retrain on newest data started "
                      "automatically", "ask": "Retrain on newest data suggested"}


def _retrain_items(db: Session, ws: UUID, project_id: UUID | None, after: _Key | None, n: int,
                   viewer_id: UUID | None) -> list[ActivityItemRead]:
    """D5a: retrains asked, started (by a person or the rule) and compared (no figures)."""

    rr, rc = RetrainRequest, RetrainComparison
    items = []
    for row_id, proj, at, origin, run, version, who in _page(
            db, select(rr.id, rr.project_id, rr.created_at, rr.origin, rr.experiment_id, rr.incumbent_model_version_id,
                       rr.requested_by_user_id).where(rr.workspace_id == ws),
            "retrain_requested", rr.created_at, rr.id, rr.project_id, project_id, after, n):
        actor = (ActivityActorRead(kind="person", is_you=viewer_id is not None and who == viewer_id)
                 if origin == "person" else ActivityActorRead(kind="rule"))
        items.append(ActivityItemRead(
            id=f"retrain_requested:{row_id}", kind="retrain_requested", occurred_at=at, project_id=proj, actor=actor,
            subject=_subject("experiment", run) if run else _subject("model_version", version),
            summary=_RETRAIN_REQUESTED.get(str(origin), "Retrain on newest data"), status=code_value(origin),
            link=ActivityLinkRead(kind="retrain_request", id=row_id)))
    for row_id, proj, at, status, run in _page(
            db, select(rc.id, rc.project_id, rc.created_at, rc.status, rc.experiment_id).where(rc.workspace_id == ws),
            "retrain_compared", rc.created_at, rc.id, rc.project_id, project_id, after, n):
        items.append(ActivityItemRead(
            id=f"retrain_compared:{row_id}", kind="retrain_compared", occurred_at=at, project_id=proj,
            actor=ActivityActorRead(kind="rule"), subject=_subject("experiment", run),
            summary=("Retrained model compared with the model in use" if status == "compared"
                     else "Retrained model is not comparable with the model in use"), status=code_value(status),
            link=ActivityLinkRead(kind="retrain_comparison", id=row_id)))
    return items


def _key(item: ActivityItemRead) -> _Key:
    return item.occurred_at, ACTIVITY_KIND_RANK[item.kind], item.link.id


def list_activity(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = ACTIVITY_PAGE_DEFAULT,
    viewer_is_agent: bool = False,
) -> ActivityPage:
    """Workspace (or one project's) activity, newest first. ``ProjectNotFoundError`` for a project of
    another workspace (indistinguishable from an unknown id); ``InvalidCursorError`` for a foreign cursor."""

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    if project_id is not None:
        get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    limit = max(1, min(int(limit), ACTIVITY_PAGE_MAX))
    scope = _scope(workspace_id, project_id)
    after = _open(cursor, scope)
    viewer_id = None if viewer_is_agent else actor.id
    n = limit + 1
    candidates = [
        *_decision_items(db, workspace_id, project_id, after, n, viewer_id),
        *_run_items(db, workspace_id, project_id, after, n, viewer_id, finished=False),
        *_run_items(db, workspace_id, project_id, after, n, viewer_id, finished=True),
        *_agent_items(db, workspace_id, project_id, after, n, finished=False),
        *_agent_items(db, workspace_id, project_id, after, n, finished=True),
        *_upload_items(db, workspace_id, project_id, after, n, viewer_id),
        *_final_test_items(db, workspace_id, project_id, after, n),
        *_drift_items(db, workspace_id, project_id, after, n),
        *_scheduled_items(db, workspace_id, project_id, after, n),
        *_retrain_items(db, workspace_id, project_id, after, n, viewer_id),
    ]
    candidates.sort(key=_key, reverse=True)
    page = candidates[:limit]
    next_cursor = None
    if len(candidates) > limit:
        at, rank, ident = _key(page[-1])
        next_cursor = sign_cursor(scope, [at.isoformat(), rank, str(ident)])
    return ActivityPage(items=page, next_cursor=next_cursor, limit=limit)


# --- project list summaries ----------------------------------------------------------------------


def project_summaries(db: Session, *, workspace_id: UUID, project_ids: list[UUID], actor: User | None = None,
                      viewer_is_agent: bool = False) -> dict[UUID, ProjectSummaryRead]:
    """Goal, champion CV metrics, latest run (+ progress while unfinished), stage and the caller's
    waiting decisions of each project, in a fixed number of statements (the caller has already
    authorized workspace read and loaded ``project_ids`` from this workspace)."""

    if not project_ids:
        return {}
    ws = workspace_id
    refs = db.execute(
        select(ProjectRef.project_id, ProjectRef.ref_kind, ProjectRef.problem_spec_id, ProjectRef.model_version_id)
        .where(ProjectRef.workspace_id == ws, ProjectRef.project_id.in_(project_ids),
               ProjectRef.ref_kind.in_(("problem_spec", "champion_model")))
    ).all()
    spec_ref = {p: s for p, kind, s, _m in refs if kind == "problem_spec" and s is not None}
    champion_ref = {p: m for p, kind, _s, m in refs if kind == "champion_model" and m is not None}

    latest_spec = (
        select(ProblemSpec.id)
        .where(ProblemSpec.workspace_id == ws, ProblemSpec.project_id.in_(project_ids))
        .ext(distinct_on(ProblemSpec.project_id))
        .order_by(ProblemSpec.project_id, ProblemSpec.version.desc())
    )
    spec_filter = ProblemSpec.id.in_(latest_spec)
    if spec_ref:
        spec_filter = or_(spec_filter, ProblemSpec.id.in_(list(spec_ref.values())))
    goals: dict[UUID, ProjectGoalRead] = {}
    for spec in db.execute(
        select(ProblemSpec.id, ProblemSpec.project_id, ProblemSpec.version, ProblemSpec.status,
               ProblemSpec.task_type, ProblemSpec.target_column, ProblemSpec.primary_metric,
               ProblemSpec.business_objective)
        .where(ProblemSpec.workspace_id == ws, spec_filter)
    ).all():
        is_ref = spec_ref.get(spec.project_id) == spec.id
        if spec.project_id in spec_ref and not is_ref:
            continue  # the ref wins over a newer draft
        goals[spec.project_id] = ProjectGoalRead(
            problem_spec_id=spec.id, version=spec.version, status=spec.status, is_ref=is_ref,
            task_type=untrusted_text(spec.task_type, 64) or "", primary_metric=untrusted_text(spec.primary_metric, 128),
            target_column=untrusted_text(spec.target_column, PROJECT_TARGET_READ_MAX_CHARS),
            objective=untrusted_text(spec.business_objective, PROJECT_OBJECTIVE_READ_MAX_CHARS),
        )

    champions: dict[UUID, ChampionSummaryRead] = {}
    if champion_ref:
        msd, ev, metric = ModelSelectionDecision, ModelEvaluation, EvaluationMetric
        rows = db.execute(
            select(ModelVersion.id, ModelVersion.version, ModelVersion.pipeline_run_id, msd.selection_metric,
                   metric.metric_name, metric.metric_value)
            .outerjoin(Experiment, and_(Experiment.id == ModelVersion.pipeline_run_id,
                                        Experiment.workspace_id == ModelVersion.workspace_id))
            # Same gate as GET /v1/model-versions/{id}: the run's locked winner, evidence locked.
            .outerjoin(msd, and_(msd.pipeline_run_id == ModelVersion.pipeline_run_id,
                                 msd.workspace_id == ModelVersion.workspace_id,
                                 msd.selected_candidate_id == ModelVersion.selected_candidate_id,
                                 Experiment.scientific_evidence_locked_at.is_not(None)))
            # CV aggregate only: the final holdout is never read here (non-negotiable #3).
            .outerjoin(ev, and_(ev.candidate_id == msd.selected_candidate_id, ev.workspace_id == ws,
                                ev.evaluation_scope == "cv_aggregate", ev.status == "completed"))
            .outerjoin(metric, metric.model_evaluation_id == ev.id)
            .where(ModelVersion.workspace_id == ws, ModelVersion.id.in_(list(champion_ref.values())))
        ).all()
        cv: dict[UUID, dict[str, float]] = defaultdict(dict)
        heads: dict[UUID, tuple[str, UUID, str | None]] = {}
        for mv_id, version, run_id, selection_metric, name, value in rows:
            heads[mv_id] = (version, run_id, selection_metric)
            if name is not None and value is not None:
                cv[mv_id][str(name)] = float(value)
        for project, mv_id in champion_ref.items():
            if mv_id in heads:
                version, run_id, selection_metric = heads[mv_id]
                champions[project] = ChampionSummaryRead(
                    model_version_id=mv_id, version=version, experiment_id=run_id,
                    selection_metric=selection_metric, cv_metrics=dict(cv.get(mv_id, {})),
                )

    latest = (
        select(Experiment.id)
        .where(Experiment.workspace_id == ws, Experiment.project_id.in_(project_ids))
        .ext(distinct_on(Experiment.project_id))
        .order_by(Experiment.project_id, Experiment.created_at.desc(), Experiment.id.desc())
    )
    runs = {
        project: LatestRunRead(experiment_id=exp_id, run_number=number, status=str(status), created_at=created,
                               ended_at=ended)
        for exp_id, project, number, status, created, ended in db.execute(
            select(Experiment.id, Experiment.project_id, Experiment.run_number, STATUS_SQL, Experiment.created_at,
                   Experiment.ended_at)
            .where(Experiment.workspace_id == ws, Experiment.id.in_(latest))
        ).all()
    }
    running = dict(db.execute(
        select(Experiment.project_id, func.count())
        .where(Experiment.workspace_id == ws, Experiment.project_id.in_(project_ids),
               STATUS_SQL.in_(sorted(UNFINISHED_RUN_STATUSES)))
        .group_by(Experiment.project_id)
    ).all())
    unfinished = [run.experiment_id for run in runs.values() if run.status in UNFINISHED_RUN_STATUSES]
    progress = run_progress(db, ws, unfinished) if unfinished else {}
    for run in runs.values():
        run.progress = progress.get(run.experiment_id)
    with_data = set(db.scalars(
        select(Dataset.project_id).distinct()
        .where(Dataset.workspace_id == ws, Dataset.project_id.in_(project_ids),
               Dataset.purpose == DATASET_PURPOSE_TRAINING, uploaded_file())
    ))
    waiting: dict[UUID, int] = {}
    if actor is not None:
        from app.services.inbox_read_service import needs_decision_by_project  # the inbox imports this module

        waiting = needs_decision_by_project(db, actor=actor, workspace_id=ws, project_ids=project_ids,
                                            viewer_is_agent=viewer_is_agent)

    def stage(project: UUID) -> str:
        run = runs.get(project)
        if project in champion_ref:
            return "model_in_use"
        if run is not None:
            return "running" if run.status in UNFINISHED_RUN_STATUSES else "runs_finished"
        return "goal_set" if project in goals else "data_uploaded" if project in with_data else "no_data"

    return {
        project: ProjectSummaryRead(goal=goals.get(project), champion=champions.get(project),
                                    latest_run=runs.get(project), stage=stage(project),
                                    waiting_decisions=waiting.get(project, 0),
                                    running_count=int(running.get(project, 0)))
        for project in project_ids
    }


def run_progress(db: Session, ws: UUID, experiment_ids: list[UUID]) -> dict[UUID, RunProgressRead]:
    """Latest recorded event of each unfinished run, in one statement (``ml_run_events`` are append-only)."""

    ev, done = MlRunEvent, aliased(MlRunEvent)
    finished = (select(func.count()).where(done.experiment_id == ev.experiment_id, done.workspace_id == ev.workspace_id,
                                           done.event_type == "candidate_completed")
                .correlate(ev).scalar_subquery())
    rows = db.execute(
        select(ev.experiment_id, ev.stage, ev.event_type, ev.timestamp, ev.payload["model_family"].astext,
               ev.payload["fold_number"].astext, finished.label("finished"), SplitPlan.validation_folds)
        .join(Experiment, and_(Experiment.id == ev.experiment_id, Experiment.workspace_id == ev.workspace_id))
        .outerjoin(SplitPlan, and_(SplitPlan.id == Experiment.split_plan_id, SplitPlan.workspace_id == ws))
        .where(ev.workspace_id == ws, ev.experiment_id.in_(experiment_ids))
        .ext(distinct_on(ev.experiment_id))
        .order_by(ev.experiment_id, ev.sequence.desc())
    ).all()
    result: dict[UUID, RunProgressRead] = {}
    for exp_id, stage, event_type, at, family, fold, count, folds in rows:
        fold_number = int(fold) if str(event_type).startswith("cv_fold_") and str(fold or "").isdigit() else None
        result[exp_id] = RunProgressRead(stage=code_value(stage), model_family=code_value(family),
                                         fold_number=fold_number, folds_planned=folds,
                                         candidates_finished=int(count or 0), updated_at=at,
                                         fold_completed=fold_number is not None and event_type == "cv_fold_completed")
    return result
