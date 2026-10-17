"""The improve loop service (ADR 0007 §3, §4, §6.2, §7, §8; P5.4-A2): start, cancel, accept, and the
pieces the ``labs.improve`` step shares (authority, N, budgets, finishing, the continuation job).

Lock order everywhere: ``improve_runs`` row, then the ``ml-runs:<workspace>`` advisory lock, then
``split-plan-comparisons:<plan>``, then ``ml_jobs`` rows. Every query filters by workspace. Nothing here
reads a final-test value: status, outcome and iteration rows are fixed from CV evidence and from
whether a look happened (§6.6). AI off (the default and the only v1 path): the rule proposer alone,
no ``decision_point_resolved``, ``agent_runs`` or ``llm_invocations`` row (ADR 0008 §8).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    DataQualityFinding,
    Experiment,
    ImproveIteration,
    ImproveRun,
    MlJob,
    ServiceToken,
    SplitPlan,
    User,
)
from app.domain.decision_records import STATE_ACCEPTED, DecisionActor
from app.domain.errors import ExperimentNotFoundError, ImproveRunError
from app.domain.findings import INVESTIGATION_SOURCE
from app.domain.idempotency import IMPROVE_FINAL_KEY_PREFIX, IMPROVE_KEY_PREFIX
from app.domain.improve import (
    CAPPED_RUN_STATUSES,
    GOAL_CURVE_METRICS,
    GOAL_NEEDS_CONSTRAINT,
    IT_ACCEPTED,
    IT_CANCELLED,
    IT_RUNNING,
    KIND_FAMILY_INCLUDE,
    LIVE_ITERATION_STATUSES,
    LIVE_RUN_STATUSES,
    MAX_FINAL_ATTEMPTS,
    MAX_LIVE_IMPROVE_RUNS_PER_WORKSPACE,
    MIN_LOOP_FOLDS,
    PLAIN_GOAL_METRICS,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_FINALIZING,
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_WAITING_USER,
    TERMINAL_RUN_STATUSES,
    TOKEN_STARTS_PER_HOUR,
    WAITING_USER_EXPIRY_DAYS,
    IMPROVE_PAGE_DEFAULT,
    IMPROVE_PAGE_MAX,
    ImproveActionsRead,
    ImproveBudget,
    ImproveGoal,
    ImproveIterationRead,
    ImproveRunPage,
    ImproveRunRead,
    clamp_budget_for_agent,
    goal_size_ok,
    sealed_set_label,
)
from app.domain.ml_jobs import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    HANDLER_LABS_IMPROVE,
    HANDLER_VERSION_LABS_IMPROVE,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_TYPE_IMPROVE,
)
from app.domain.pipeline_run_branch import EVIDENCE_MODE_SCORED, ServiceOverrides, strip_service_only
from app.domain.service_tokens import SCOPE_EXPERIMENTS_WRITE
from app.engine.evaluation.comparability import RunEvidence, final_run_budget_seconds, probe_budget_seconds
from app.engine.modeling.validation_planner import TIME_SERIES_SPLIT
from app.engine.models.registry import available_families
from app.engine.search.generator import assemble_candidates
from app.engine.types import TaskSpec
from app.services.authorization_service import (
    can_execute_workspace_ml,
    can_read_workspace,
    explicit_workspace_role,
    role_can_run_ml,
)
from app.services.improve.rule_proposer import Plan, plan_tries
from app.services.improve.action_space import candidate_families

FINAL_INTENT = "Improve loop {run}: the accepted try {seq}, re-run once with its selection pinned; scores the final test set once."


def _now() -> datetime:
    return datetime.now(UTC)


def _heartbeat_seconds() -> float:
    try:
        return float(get_settings().ml_job_heartbeat_timeout_seconds)
    except Exception:  # noqa: BLE001
        return DEFAULT_HEARTBEAT_TIMEOUT_SECONDS


def iteration_key(run_id: UUID, seq: int) -> str:
    return f"{IMPROVE_KEY_PREFIX}{run_id}:{seq}"


def acceptance_key(run_id: UUID) -> str:
    return f"{IMPROVE_KEY_PREFIX}{run_id}:accept"


# --- reads ----------------------------------------------------------------------------------------


def load_run(db: Session, workspace_id: UUID, run_id: UUID, *, for_update: bool = False) -> ImproveRun:
    query = select(ImproveRun).where(ImproveRun.id == run_id, ImproveRun.workspace_id == workspace_id)
    row = db.scalar(query.with_for_update() if for_update else query)
    if row is None:
        raise ImproveRunError("improve_run_not_found", "improve run not found", status_code=404)
    return row


def iterations(db: Session, run: ImproveRun) -> list[ImproveIteration]:
    return list(db.scalars(select(ImproveIteration).where(
        ImproveIteration.improve_run_id == run.id, ImproveIteration.workspace_id == run.workspace_id,
    ).order_by(ImproveIteration.seq)))


def live_iteration(db: Session, run: ImproveRun) -> ImproveIteration | None:
    return db.scalar(select(ImproveIteration).where(
        ImproveIteration.improve_run_id == run.id, ImproveIteration.workspace_id == run.workspace_id,
        ImproveIteration.status.in_(LIVE_ITERATION_STATUSES)))


def experiment_job(db: Session, workspace_id: UUID, experiment_id: UUID) -> MlJob | None:
    return db.scalar(select(MlJob).where(MlJob.workspace_id == workspace_id, MlJob.pipeline_run_id == experiment_id)
                     .order_by(MlJob.queued_at.desc()).limit(1))


def stored_findings(db: Session, workspace_id: UUID, experiment_id: UUID) -> list[dict[str, Any]]:
    """The run's stored trust-check rows as {check, status, evidence} (the proposer filters them)."""

    rows = db.scalars(select(DataQualityFinding.evidence).where(
        DataQualityFinding.workspace_id == workspace_id, DataQualityFinding.pipeline_run_id == experiment_id,
        DataQualityFinding.evidence["source"].astext == INVESTIGATION_SOURCE))
    return [dict(row) for row in rows if isinstance(row, dict)]


def _lock_plan(db: Session, workspace_id: UUID, split_plan_id: UUID) -> None:
    """§5 lock order for N: ``ml-runs:<workspace>`` then ``split-plan-comparisons:<plan>``."""

    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"ml-runs:{workspace_id}", 0))))
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"split-plan-comparisons:{split_plan_id}", 0))))


def plan_comparisons(db: Session, workspace_id: UUID, split_plan_id: UUID) -> int:
    """§5 plan-wide N: runs on the plan that locked a winner, queued/running runs on it, plus the
    ``k_i`` of in-flight loop tries (bound and caveat only, never the margin)."""

    from app.services.experiment_verdict_service import plan_looks

    _looks, comparisons = plan_looks(db, workspace_id, split_plan_id)
    in_flight = db.scalar(select(func.coalesce(func.sum(ImproveIteration.planned_new_candidates), 0))
                          .join(ImproveRun, ImproveRun.id == ImproveIteration.improve_run_id)
                          .where(ImproveIteration.workspace_id == workspace_id, ImproveRun.workspace_id == workspace_id,
                                 ImproveRun.split_plan_id == split_plan_id, ImproveIteration.status == IT_RUNNING))
    return int(comparisons) + int(in_flight or 0)


def plan_look_count(db: Session, workspace_id: UUID, split_plan_id: UUID) -> int:
    from app.services.experiment_verdict_service import plan_looks

    return len(plan_looks(db, workspace_id, split_plan_id)[0])


# --- authority (§8) -------------------------------------------------------------------------------


def authority_ok(db: Session, run: ImproveRun, now: datetime | None = None) -> bool:
    """Human-started: the creator is active and still may run ML here. Token-started: also the
    conditions of ``authenticate_service_token`` (kill switch, token active, the creator's explicit
    membership) and the ``experiments:write`` scope."""

    creator = db.get(User, run.created_by_user_id)
    if creator is None or not creator.is_active or not can_execute_workspace_ml(db, creator, run.workspace_id):
        return False
    if run.created_by_service_token_id is None:
        return True
    moment = now or _now()
    token = db.scalar(select(ServiceToken).where(ServiceToken.id == run.created_by_service_token_id,
                                                 ServiceToken.workspace_id == run.workspace_id))
    role = explicit_workspace_role(db, creator, run.workspace_id)
    return bool(
        get_settings().service_tokens_enabled and token is not None and token.revoked_at is None
        and token.expires_at > moment and token.created_by_user_id == creator.id
        and SCOPE_EXPERIMENTS_WRITE in list(token.scopes or []) and role is not None and role_can_run_ml(role)
    )


# --- goal and eligibility ------------------------------------------------------------------------


def parse_goal(raw: Any, *, task_type: str, selection_metric: str) -> ImproveGoal:
    """§1b, 422 on any violation."""

    try:
        goal = raw if isinstance(raw, ImproveGoal) else ImproveGoal.model_validate(raw)
    except ValidationError as exc:
        raise ImproveRunError("invalid_goal", str(exc.errors()[0].get("msg")), status_code=422) from exc
    if not goal_size_ok(goal):
        raise ImproveRunError("goal_too_large", "a goal is at most 4 KB", status_code=422)
    if goal.plain is not None:
        metric = goal.plain.metric
        if metric not in PLAIN_GOAL_METRICS.get(task_type, frozenset()):
            raise ImproveRunError("goal_needs_constraint",
                                  f"{metric!r} depends on a threshold; state it as an objective with a constraint",
                                  status_code=422)
        if metric != selection_metric:
            # Otherwise an old candidate could "win" on the goal with no new model, uncounted in T.
            raise ImproveRunError("goal_not_primary_metric",
                                  f"a plain goal must be the run's primary metric ({selection_metric})", status_code=422)
    else:
        objective = goal.objective
        assert objective is not None
        if task_type != "binary":
            raise ImproveRunError("goal_objective_binary_only", "objectives with constraints are binary only",
                                  status_code=422)
        if objective.maximize not in GOAL_CURVE_METRICS:
            raise ImproveRunError("invalid_goal_metric", f"{objective.maximize!r} cannot be maximised", status_code=422)
        if objective.maximize in GOAL_NEEDS_CONSTRAINT and not objective.constraints:
            raise ImproveRunError("goal_needs_constraint",
                                  f"maximising {objective.maximize} needs a constraint (else it is trivial)",
                                  status_code=422)
        for item in objective.constraints:
            if item.metric not in GOAL_CURVE_METRICS:
                # flagged_share too: the final run's threshold lock takes threshold metrics only (gate 2).
                raise ImproveRunError("goal_constraint_unsupported",
                                      f"{item.metric!r} cannot be a loop constraint", status_code=422)
    if goal.target is not None and goal.target.metric != goal.metric:
        raise ImproveRunError("invalid_goal_target", "the target names the goal's metric", status_code=422)
    return goal


def _refuse(code: str, message: str, status_code: int = 409) -> ImproveRunError:
    return ImproveRunError(code, message, status_code=status_code)


def generator_ids(start: Experiment, ctx: Any, change: dict[str, Any] | None) -> list[str] | None:
    """Candidate ids the pure generator plans for ``start`` (``change`` None) or ``start ⊕ change``,
    exactly as ``branch_experiment`` materializes the run (None: the change is refused)."""

    from app.domain.errors import InvalidChangeSetError
    from app.services.experiment_branch_service import materialize_branch, parse_change_set
    from app.services.lab_service import search_from_mapping

    if change is None:
        overrides, objective = ctx.overrides, ctx.objective
    else:
        try:
            overrides, objective = materialize_branch(parse_change_set([change]), ctx)
        except InvalidChangeSetError:
            return None
    config = search_from_mapping({**strip_service_only(start.config), "objective": objective,
                                  "branch_overrides": overrides or None})
    task = TaskSpec(id="improve-plan", name="improve-plan", task_type=ctx.task_type,
                    feature_groups={"features": ["feature"]}, validation_strategy="kfold")
    development = (start.result or {}).get("model_development_plan")
    return [item.candidate_id for item in assemble_candidates(task, config, development_plan=development)]


def _start_evidence(db: Session, workspace_id: UUID, start: Experiment) -> tuple[RunEvidence, Any]:
    from app.services.experiment_verdict_service import _selection, locked_run_evidence

    evidence = locked_run_evidence(db, workspace_id, start.id)
    selection = _selection(db, workspace_id, start.id)
    if evidence is None or selection is None or evidence.winner is None:
        raise _refuse("start_not_locked", "the start has no locked winner with readable CV evidence")
    return evidence, selection


def _eligible_start(db: Session, workspace_id: UUID, start: Experiment) -> tuple[RunEvidence, Any, SplitPlan]:
    """§4 eligibility at POST (409 with the reason)."""

    if str(start.status or "").upper() != "COMPLETED" or start.scientific_evidence_locked_at is None:
        raise _refuse("start_not_locked", "the start must be a completed run with locked evidence")
    if start.evidence_mode != EVIDENCE_MODE_SCORED:
        raise _refuse("start_sealed", "a sealed improve try cannot start a loop (fresh splits are deferred)")
    plan = db.scalar(select(SplitPlan).where(SplitPlan.id == start.split_plan_id, SplitPlan.workspace_id == workspace_id))
    if plan is None or start.project_id is None:
        raise _refuse("start_without_split_plan", "the start has no split plan")
    if plan.validation_strategy == TIME_SERIES_SPLIT:
        raise _refuse("time_ordered_folds", "loops need exchangeable K-fold folds")
    if (start.config or {}).get("strategy") != "open_ingest":
        raise _refuse("start_not_open_ingest", "loops plan their tries over open-ingest runs only")
    evidence, selection = _start_evidence(db, workspace_id, start)
    folds = len(evidence.trained[evidence.winner].folds)
    if folds < MIN_LOOP_FOLDS:
        raise _refuse("too_few_folds", f"the winner has {folds} folds; a loop needs at least {MIN_LOOP_FOLDS}")
    if evidence.skipped or any(item.truncated for item in evidence.trained.values()):
        raise _refuse("start_truncated", "the start skipped a candidate for time or truncated its tuning")
    return evidence, selection, plan


def _token_starts_last_hour(db: Session, workspace_id: UUID, service_token_id: UUID) -> int:
    """§3: token starts are counted in the DB (5 per hour per token), under the workspace lock."""

    return int(db.scalar(select(func.count()).select_from(ImproveRun).where(
        ImproveRun.workspace_id == workspace_id, ImproveRun.created_by_service_token_id == service_token_id,
        ImproveRun.created_at > func.now() - timedelta(hours=1))) or 0)


def start_improve_run(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    start_experiment_id: UUID,
    goal: Any,
    budget: Any = None,
    service_token_id: UUID | None = None,
    fresh_split: bool = False,
    agent_proposed: bool = False,
    before_commit: Callable[[ImproveRun], None] | None = None,
) -> ImproveRun:
    """Plan and queue one loop. Commits. 404 (no access), 422 (goal/budget), 409 (eligibility, a live
    loop on the plan, the comparison bound, nothing to try), 429 (the workspace's live loops, a
    token's starts per hour). A token's budget, and that of an agent proposal a person accepted
    (``agent_proposed``), is clamped to the defaults; ``fresh_split`` is a person's request (403 for
    tokens) and is deferred (409 ``fresh_split_unavailable``, ADR 0007 Revision 5)."""

    from app.services.experiment_branch_service import load_parent_context
    from app.services.improve.rule_proposer import is_blocking
    from app.services.ml_job_service import create_ml_job

    if not can_read_workspace(db, actor, workspace_id) or not can_execute_workspace_ml(db, actor, workspace_id):
        raise ExperimentNotFoundError("experiment not found")
    start = db.scalar(select(Experiment).where(Experiment.id == start_experiment_id,
                                               Experiment.workspace_id == workspace_id))
    if start is None:
        raise ExperimentNotFoundError("experiment not found")
    if fresh_split:
        if service_token_id is not None:
            raise ImproveRunError("fresh_split_human_only", "only a person can ask for a fresh split", status_code=403)
        raise _refuse("fresh_split_unavailable", "a loop on a fresh split plan is not available yet; start from "
                                                 "this run's own split plan")
    evidence, selection, split_plan = _eligible_start(db, workspace_id, start)
    task_type = str(((start.result or {}).get("task") or {}).get("task_type") or "")
    parsed = parse_goal(goal, task_type=task_type, selection_metric=selection.selection_metric)
    try:
        limits = budget if isinstance(budget, ImproveBudget) else ImproveBudget.model_validate(budget or {})
    except ValidationError as exc:
        raise ImproveRunError("invalid_budget", str(exc.errors()[0].get("msg")), status_code=422) from exc
    clamped = service_token_id is not None or agent_proposed
    if clamped:
        limits = clamp_budget_for_agent(limits)
    findings = stored_findings(db, workspace_id, start.id)
    if is_blocking(findings):
        raise _refuse("blocking_finding", "the start failed a target-leakage or implausible-score check")
    ctx = load_parent_context(db, actor=actor, workspace_id=workspace_id, parent_id=start.id)
    families = candidate_families(task_type, ctx.portfolio, available_families(task_type))
    plan: Plan = plan_tries(
        task_type=task_type, constrained=parsed.constrained, families=families,
        start_trained_ids=evidence.trained, max_iterations=limits.max_iterations, findings=findings,
        probe_ids=lambda family: generator_ids(start, ctx, {"kind": KIND_FAMILY_INCLUDE, "family": family}),
    )
    tries = plan.retraining_tries
    if not tries:
        raise _refuse("nothing_to_try", "every model family is already in this run: nothing to try")
    budgets = [probe_budget_seconds(evidence, item.k) for item in tries]
    if any(value is None for value in budgets):
        raise _refuse("start_too_slow", "a try would not fit the per-probe time budget")
    default_total = min(28_800.0, limits.max_iterations * max(budgets))
    total = limits.max_training_seconds_total or default_total
    if clamped:
        total = min(total, default_total)
    _lock_plan(db, workspace_id, split_plan.id)
    if service_token_id is not None and _token_starts_last_hour(db, workspace_id, service_token_id) \
            >= TOKEN_STARTS_PER_HOUR:
        raise _refuse("improve_rate_limited", f"a token may start {TOKEN_STARTS_PER_HOUR} loops per hour",
                      status_code=429)
    n = plan_comparisons(db, workspace_id, split_plan.id)
    t_planned = plan.planned_comparisons
    if n >= limits.comparison_bound:
        raise _refuse("split_plan_exhausted", f"{n} comparisons on this split plan already reach the bound "
                                              f"{limits.comparison_bound}")
    if service_token_id is not None and n + t_planned > limits.comparison_bound / 2:
        raise _refuse("split_plan_exhausted", "a token may not take this split plan past half its comparison bound")
    if db.scalar(select(ImproveRun.id).where(ImproveRun.workspace_id == workspace_id,
                                             ImproveRun.split_plan_id == split_plan.id,
                                             ImproveRun.status.in_(LIVE_RUN_STATUSES))) is not None:
        raise _refuse("improve_run_live", "this split plan already has a live improve loop")
    live = db.scalar(select(func.count()).select_from(ImproveRun).where(
        ImproveRun.workspace_id == workspace_id, ImproveRun.status.in_(CAPPED_RUN_STATUSES)))
    if int(live or 0) >= MAX_LIVE_IMPROVE_RUNS_PER_WORKSPACE:
        raise _refuse("improve_run_quota", f"this workspace already runs {live} improve loops", status_code=429)
    looks = plan_look_count(db, workspace_id, split_plan.id)
    run = ImproveRun(
        workspace_id=workspace_id, project_id=start.project_id, split_plan_id=split_plan.id,
        start_experiment_id=start.id, incumbent_experiment_id=start.id, status=RUN_QUEUED,
        goal=parsed.model_dump(mode="json", exclude_none=True),
        budget={**limits.model_dump(mode="json"), "max_training_seconds_total": float(total)},
        plan=[item.to_dict() for item in plan.tries], planned_comparisons=t_planned,
        validation_folds=len(evidence.trained[evidence.winner].folds), looks_before=looks,
        sealed_set_label=sealed_set_label(looks), created_by_user_id=actor.id,
        created_by_service_token_id=service_token_id,
    )
    db.add(run)
    db.flush()
    if service_token_id is not None and not authority_ok(db, run):
        raise ImproveRunError("token_not_authorized", "this service token may not start a loop here", status_code=403)
    job = create_ml_job(db, workspace_id=workspace_id, project_id=start.project_id, job_type=JOB_TYPE_IMPROVE,
                        handler_key=HANDLER_LABS_IMPROVE, handler_version=HANDLER_VERSION_LABS_IMPROVE,
                        target_id=run.id, payload={"improve_run_id": str(run.id)})
    run.continuation_job_id = job.id
    if before_commit is not None:
        before_commit(run)
    db.commit()
    return run


# --- shared by the step, cancel and accept --------------------------------------------------------


def arm(db: Session, run: ImproveRun, *, delay_seconds: float | None = None, at: datetime | None = None,
        current_job_id: UUID | None = None) -> MlJob:
    """The run's one continuation (watchdog) job; a queued earlier one is cancelled (lock order:
    the run is already locked, then the job row)."""

    from app.services.ml_job_service import create_ml_job

    moment = at or (_now() + timedelta(seconds=_heartbeat_seconds() if delay_seconds is None else delay_seconds))
    _drop_continuation(db, run, keep=current_job_id)
    job = create_ml_job(db, workspace_id=run.workspace_id, project_id=run.project_id, job_type=JOB_TYPE_IMPROVE,
                        handler_key=HANDLER_LABS_IMPROVE, handler_version=HANDLER_VERSION_LABS_IMPROVE,
                        target_id=run.id, available_at=moment, payload={"improve_run_id": str(run.id)})
    run.continuation_job_id = job.id
    return job


def _drop_continuation(db: Session, run: ImproveRun, *, keep: UUID | None = None) -> None:
    from app.services.ml_job_service import request_job_cancellation

    old = run.continuation_job_id
    run.continuation_job_id = None
    if old is None or old == keep:
        return
    job = db.scalar(select(MlJob).where(MlJob.id == old, MlJob.workspace_id == run.workspace_id).with_for_update())
    if job is not None and job.status == JOB_QUEUED:
        request_job_cancellation(db, job)


_CANCELLED_UPLOADS = "improve_cancelled_uploads"


def cancel_experiment_job(db: Session, workspace_id: UUID, experiment_id: UUID | None,
                          actor_user_id: UUID | None = None) -> None:
    """Every stop cancels the in-flight probe (§3): a queued job at once (its run envelope is marked
    cancelled right after this transaction commits, see ``commit``), a running one cooperatively."""

    from app.domain.ml_jobs import JOB_CANCELLED
    from app.services.ml_job_service import request_job_cancellation

    if experiment_id is None:
        return
    job = db.scalar(select(MlJob).where(MlJob.workspace_id == workspace_id, MlJob.pipeline_run_id == experiment_id,
                                        MlJob.status.in_((JOB_QUEUED, JOB_RUNNING))).with_for_update())
    if job is not None and request_job_cancellation(db, job, actor_user_id=actor_user_id) == JOB_CANCELLED \
            and job.upload_id is not None:
        db.info.setdefault(_CANCELLED_UPLOADS, []).append((job.upload_id, actor_user_id))


def commit(db: Session) -> None:
    """Commit, then mark the run envelopes of probes cancelled while queued (``record_run_cancelled``
    commits on its own, so it runs after the loop's transaction, never inside it)."""

    from app.services.auto_train_service import record_run_cancelled

    db.commit()
    for upload_id, actor_user_id in db.info.pop(_CANCELLED_UPLOADS, []):
        record_run_cancelled(db, upload_id, cancelled_by=actor_user_id)


def cancel_live_iteration(db: Session, run: ImproveRun, *, now: datetime, actor_user_id: UUID | None = None) -> None:
    live = live_iteration(db, run)
    if live is None:
        return
    cancel_experiment_job(db, run.workspace_id, live.experiment_id, actor_user_id)
    live.status, live.reason, live.finished_at = IT_CANCELLED, "loop_stopped", now


def finish(db: Session, run: ImproveRun, *, status: str, outcome: str, now: datetime, stop_reason: str | None = None,
           detail: str | None = None, current_job_id: UUID | None = None) -> None:
    cancel_live_iteration(db, run, now=now)
    _drop_continuation(db, run, keep=current_job_id)
    run.status, run.outcome, run.finished_at = status, outcome, now
    run.stop_reason = stop_reason or run.stop_reason
    run.stop_detail = detail or run.stop_detail


def wait_for_person(db: Session, run: ImproveRun, *, now: datetime, stop_reason: str | None, detail: str | None,
                    current_job_id: UUID | None) -> None:
    """An accepted try exists: a person accepts it (one final run, one look) or keeps the current model;
    the loop expires to ``kept_current`` after 14 days (the continuation waits until then)."""

    cancel_live_iteration(db, run, now=now)
    run.status = RUN_WAITING_USER
    run.stop_reason = stop_reason or run.stop_reason
    run.stop_detail = detail
    run.waiting_user_expires_at = now + timedelta(days=WAITING_USER_EXPIRY_DAYS)
    arm(db, run, at=run.waiting_user_expires_at, current_job_id=current_job_id)


# --- cancel (§8) -----------------------------------------------------------------------------------


def _live_token(db: Session, workspace_id: UUID, service_token_id: UUID, actor: User) -> None:
    """A token principal must be a live token of this workspace created by ``actor`` (defence in
    depth for in-process callers; ``/v1`` authenticated it already)."""

    token = db.scalar(select(ServiceToken).where(ServiceToken.id == service_token_id,
                                                 ServiceToken.workspace_id == workspace_id))
    if token is None or token.revoked_at is not None or token.expires_at <= _now() \
            or token.created_by_user_id != actor.id:
        raise ImproveRunError("token_not_authorized", "this service token may not act here", status_code=403)


def token_cancellable(run: ImproveRun, service_token_id: UUID | None) -> bool:
    """§8: a token cancels only a ``queued`` / ``running`` loop it started itself."""

    return (service_token_id is not None and run.created_by_service_token_id == service_token_id
            and run.status in (RUN_QUEUED, RUN_RUNNING))


def token_may_cancel_experiment(db: Session, workspace_id: UUID, experiment_id: UUID, service_token_id: UUID) -> bool:
    """§8: the generic experiment cancel refuses a token for a loop's probe or final run unless the
    loop itself is cancellable by that token (a final run's loop never is). A lock-free read: the
    experiment cancel locks the job, not the loop; a loop that leaves ``running`` meanwhile only makes
    the cancelled probe a cancelled try, which the loop's next step settles (never a look)."""

    final = db.scalar(select(ImproveRun.id).where(ImproveRun.workspace_id == workspace_id,
                                                  ImproveRun.final_experiment_id == experiment_id))
    if final is not None:
        return False
    loop = db.scalar(select(ImproveRun).join(
        ImproveIteration, ImproveIteration.improve_run_id == ImproveRun.id).where(
        ImproveIteration.workspace_id == workspace_id, ImproveRun.workspace_id == workspace_id,
        ImproveIteration.experiment_id == experiment_id, ImproveIteration.kind == KIND_FAMILY_INCLUDE).limit(1))
    return loop is None or token_cancellable(loop, service_token_id)


def cancel_improve_run(db: Session, *, actor: User, workspace_id: UUID, run_id: UUID,
                       service_token_id: UUID | None = None,
                       precondition: Callable[[ImproveRun], None] | None = None) -> ImproveRun:
    """Humans may cancel any live state; tokens only ``queued`` / ``running``. During ``finalizing``
    the final run is asked to stop: before its final test the loop ends ``cancelled``; after it the
    look counts and the loop still completes (§6.4). Commits; idempotent on a finished loop (no
    precondition then). ``precondition(run)`` runs under the row lock (the route's If-Match)."""

    if not can_execute_workspace_ml(db, actor, workspace_id):
        raise ImproveRunError("improve_run_not_found", "improve run not found", status_code=404)
    run = load_run(db, workspace_id, run_id, for_update=True)
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    if service_token_id is not None:
        _live_token(db, workspace_id, service_token_id, actor)
    if service_token_id is not None and not token_cancellable(run, service_token_id):
        raise ImproveRunError("token_cannot_cancel", "a token may cancel only a queued or running loop it started",
                              status_code=403)
    if precondition is not None:
        precondition(run)
    now = _now()
    if run.status == RUN_FINALIZING:
        run.cancel_requested_at = run.cancel_requested_at or now
        cancel_experiment_job(db, workspace_id, run.final_experiment_id, actor.id)
        if run.continuation_job_id is not None:  # let the step settle the final run at once
            db.execute(update(MlJob).where(MlJob.id == run.continuation_job_id, MlJob.workspace_id == workspace_id,
                                           MlJob.status == JOB_QUEUED).values(available_at=func.now()))
    else:
        run.cancel_requested_at = now
        finish(db, run, status=RUN_CANCELLED, outcome="cancelled", now=now, stop_reason="cancelled",
               detail="cancelled_by_person" if service_token_id is None else "cancelled_by_token")
    commit(db)
    return run


# --- accept (§6.2, §7) -----------------------------------------------------------------------------


def _acceptance_record(db: Session, run: ImproveRun, actor: User, *, subject_id: UUID, seq: int | None,
                       key: str | None = None, reason: str | None = None) -> UUID:
    """The person's ``experiment_accepted`` record (reserved key ``improve:<run>:accept``; a keep after a
    final run that stopped before its look gets its own ``improve:<run>:keep`` record)."""

    from app.services.decision_record_service import build_record, evidence_ref, insert_record, node_digest

    subject = db.scalar(select(Experiment).where(Experiment.id == subject_id, Experiment.workspace_id == run.workspace_id))
    row = build_record(
        workspace_id=run.workspace_id, project_id=run.project_id, actor=DecisionActor.human(actor),
        decision_type="experiment_accepted", state=STATE_ACCEPTED, subject_kind="experiment",
        subject_id=subject_id, subject_digest=node_digest(subject),
        rationale=("Accepted improve try {seq} on cross-validation; its final run scores the final test set once."
                   .format(seq=seq) if seq is not None else "Kept the current model; nothing beyond the noise "
                   "was accepted, so the final test set is not used.") + (f" Reason: {reason}" if reason else ""),
        facts={"decided_on": "cross_validation", "keep_current": seq is None},
        evidence_refs=[evidence_ref("experiment", subject_id)],
        details={"improve_run_id": str(run.id), "iteration_seq": seq, "keep_current": seq is None},
        idempotency_key=key or acceptance_key(run.id),
    )
    return insert_record(db, row).id


def accept_improve_run(db: Session, *, actor: User, workspace_id: UUID, run_id: UUID, iteration_seq: int | None = None,
                       keep_current: bool = False, source_surface: str = "api", rationale: str | None = None,
                       precondition: Callable[[ImproveRun], None] | None = None,
                       before_commit: Callable[[ImproveRun], None] | None = None) -> ImproveRun:
    """A person (never a token or an agent: the caller passes a session user) accepts one ``accepted``
    try of this loop -- one ordinary branch of the probe's parent with the probe's own change set,
    its selection, budget and goal pinned (§6.2) -- or keeps the current model (no look). A retry after
    a final run that stopped before its final test names the same try (write-once), at most twice."""

    from app.services.experiment_branch_service import branch_experiment
    from app.services.experiment_verdict_service import locked_run_evidence
    from app.services.improve.comparison import point_objective

    if not can_execute_workspace_ml(db, actor, workspace_id):
        raise ImproveRunError("improve_run_not_found", "improve run not found", status_code=404)
    run = load_run(db, workspace_id, run_id, for_update=True)
    if precondition is not None:
        precondition(run)
    if keep_current == (iteration_seq is not None):
        raise ImproveRunError("invalid_acceptance", "accept exactly one try, or keep the current model", status_code=422)
    if run.status != RUN_WAITING_USER:
        raise ImproveRunError("not_waiting_for_a_person", f"the loop is {run.status}")
    now = _now()
    if keep_current:  # also after a final run that stopped before its look: nothing was scored
        if run.acceptance_record_id is None:
            run.acceptance_record_id = _acceptance_record(db, run, actor, subject_id=run.start_experiment_id, seq=None,
                                                          reason=rationale)
        else:  # the try's own acceptance record stays; the keep is a record of its own
            _acceptance_record(db, run, actor, subject_id=run.start_experiment_id, seq=None,
                               key=f"{IMPROVE_KEY_PREFIX}{run.id}:keep", reason=rationale)
        run.accepted_by_user_id = run.accepted_by_user_id or actor.id
        run.incumbent_experiment_id = run.start_experiment_id
        finish(db, run, status=RUN_COMPLETED, outcome="kept_current", now=now, detail="kept_by_person")
        if before_commit is not None:
            before_commit(run)
        commit(db)
        return run
    # waiting_user -> finalizing re-enters the workspace cap (§3): re-checked under the lock order
    # improve_runs -> ml-runs:<workspace>.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"ml-runs:{workspace_id}", 0))))
    live = db.scalar(select(func.count()).select_from(ImproveRun).where(
        ImproveRun.workspace_id == workspace_id, ImproveRun.status.in_(CAPPED_RUN_STATUSES), ImproveRun.id != run.id))
    if int(live or 0) >= MAX_LIVE_IMPROVE_RUNS_PER_WORKSPACE:
        raise ImproveRunError("improve_run_quota", f"this workspace already runs {live} improve loops; accept "
                              "again when one finishes", status_code=429)
    if run.accepted_iteration_seq is not None and run.accepted_iteration_seq != iteration_seq:
        raise ImproveRunError("acceptance_is_final", "this loop's accepted try cannot change")
    if run.final_attempts >= MAX_FINAL_ATTEMPTS:
        raise ImproveRunError("final_attempts_exhausted", "the final run was tried three times")
    chosen = db.scalar(select(ImproveIteration).where(
        ImproveIteration.improve_run_id == run.id, ImproveIteration.workspace_id == workspace_id,
        ImproveIteration.seq == iteration_seq))
    if chosen is None or chosen.status != IT_ACCEPTED or chosen.experiment_id is None:
        raise ImproveRunError("iteration_not_accepted", "only a try that beat the noise can be accepted", status_code=422)
    probe = db.scalar(select(Experiment).where(Experiment.id == chosen.experiment_id,
                                               Experiment.workspace_id == workspace_id))
    pinned = locked_run_evidence(db, workspace_id, probe.id) if probe is not None else None
    if pinned is None or probe.parent_pipeline_run_id is None or probe.change_set is None:
        raise ImproveRunError("probe_unreadable", "the accepted try's locked evidence cannot be read")
    goal = ImproveGoal.model_validate(run.goal)
    objective = point_objective(goal)
    overrides = ServiceOverrides(
        evidence_mode=EVIDENCE_MODE_SCORED, expected_selection=pinned,
        max_training_seconds=min(final_run_budget_seconds(pinned), 43_200.0),
        objective_constraints=None if objective is None else tuple(
            (item.metric, item.op, float(item.value)) for item in objective.constraints),
        objective_goal=None if objective is None else objective.goal,
    )
    attempt = run.final_attempts + 1

    def bind(shell: Experiment) -> None:
        if run.acceptance_record_id is None:
            run.acceptance_record_id = _acceptance_record(db, run, actor, subject_id=probe.id, seq=iteration_seq,
                                                          reason=rationale)
        run.accepted_iteration_seq = iteration_seq
        run.accepted_by_user_id = run.accepted_by_user_id or actor.id
        run.final_experiment_id, run.final_attempts = shell.id, attempt
        run.status, run.stop_detail, run.waiting_user_expires_at = RUN_FINALIZING, None, None
        run.incumbent_experiment_id = probe.id
        arm(db, run)
        if before_commit is not None:
            before_commit(run)

    result = branch_experiment(
        db, actor=actor, workspace_id=workspace_id, parent_id=probe.parent_pipeline_run_id,
        changes=dict(probe.change_set), intent=FINAL_INTENT.format(run=run.id, seq=iteration_seq),
        idempotency_key=f"{IMPROVE_FINAL_KEY_PREFIX}{run.id}:{attempt}", source_surface=source_surface,
        before_commit=bind, service_overrides=overrides,
    )
    if not result.created:  # a replay of this loop's own reserved key: bind the existing final run
        run = load_run(db, workspace_id, run_id, for_update=True)
        if run.status == RUN_WAITING_USER:
            bind(result.experiment)
        elif before_commit is not None:
            before_commit(run)
        commit(db)
    db.refresh(run)
    return run


# --- read models (§10; P5.4-A3) -------------------------------------------------------------------


def _iteration_read(row: ImproveIteration) -> ImproveIterationRead:
    return ImproveIterationRead(
        seq=row.seq, kind=row.kind, status=row.status, reason=row.reason, action=dict(row.action or {}),
        experiment_id=row.experiment_id, incumbent_experiment_id=row.incumbent_experiment_id,
        planned_new_candidates=row.planned_new_candidates, planned_candidate_ids=list(row.planned_candidate_ids or []),
        comparison=row.comparison, agent=row.agent, training_seconds=row.training_seconds, created_at=row.created_at,
        finished_at=row.finished_at,
    )


def run_read(run: ImproveRun, its: list[ImproveIteration], *, agent: bool, can_act: bool,
             token_id: UUID | None = None) -> ImproveRunRead:
    """The loop as this reader may see it. Built from the loop's own rows only (CV evidence and
    whether a look happened); a token/agent reader gets no person ids, every key naming the holdout
    stripped and only the cancel action of a queued / running loop that token started."""

    waiting = run.status == RUN_WAITING_USER and can_act and not agent
    acceptable = any(it.status == IT_ACCEPTED for it in its) and run.final_attempts < MAX_FINAL_ATTEMPTS
    actions = ImproveActionsRead(
        accept=waiting and acceptable, keep_current=waiting,
        cancel=can_act and (token_cancellable(run, token_id) if agent else run.status in LIVE_RUN_STATUSES),
    )
    body = ImproveRunRead(
        id=run.id, project_id=run.project_id, split_plan_id=run.split_plan_id,
        start_experiment_id=run.start_experiment_id, incumbent_experiment_id=run.incumbent_experiment_id,
        final_experiment_id=run.final_experiment_id, status=run.status, outcome=run.outcome,
        stop_reason=run.stop_reason, stop_detail=run.stop_detail, goal=dict(run.goal or {}),
        budget=dict(run.budget or {}), plan=list(run.plan or []), planned_comparisons=run.planned_comparisons,
        validation_folds=run.validation_folds, looks_before=run.looks_before, sealed_set_label=run.sealed_set_label,
        sealed_set_consulted=bool(run.sealed_set_consulted), final_attempts=run.final_attempts,
        accepted_iteration_seq=run.accepted_iteration_seq,
        accepted_by_user_id=None if agent else run.accepted_by_user_id,
        created_by_user_id=None if agent else run.created_by_user_id,
        started_by="service_token" if run.created_by_service_token_id is not None else "person",
        training_seconds_spent=float(run.training_seconds_spent or 0.0), llm_micros_spent=int(run.llm_micros_spent or 0),
        cancel_requested_at=run.cancel_requested_at, waiting_user_expires_at=run.waiting_user_expires_at,
        created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
        iterations=[_iteration_read(it) for it in its], actions=actions,
    )
    if agent:
        from app.agents.tools.shaping import strip_holdout

        body = ImproveRunRead.model_validate(strip_holdout(body))
    return body


def improve_run_read(db: Session, *, actor: User, workspace_id: UUID, run_id: UUID, agent: bool,
                     token_id: UUID | None = None) -> ImproveRunRead:
    """404 for another workspace's loop or a reader without workspace access (no oracle)."""

    if not can_read_workspace(db, actor, workspace_id):
        raise ImproveRunError("improve_run_not_found", "improve run not found", status_code=404)
    run = load_run(db, workspace_id, run_id)
    return run_read(run, iterations(db, run), agent=agent, can_act=can_execute_workspace_ml(db, actor, workspace_id),
                    token_id=token_id)


def list_improve_runs(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, agent: bool,
                      limit: int = IMPROVE_PAGE_DEFAULT, token_id: UUID | None = None) -> ImproveRunPage:
    """A project's loops, newest first (``limit`` ≤ 100)."""

    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)  # 404 for another tenant
    runs = list(db.scalars(select(ImproveRun).where(ImproveRun.workspace_id == workspace_id,
                                                    ImproveRun.project_id == project_id)
                           .order_by(ImproveRun.created_at.desc(), ImproveRun.id.desc())
                           .limit(max(1, min(limit, IMPROVE_PAGE_MAX)))))
    by_run: dict[UUID, list[ImproveIteration]] = {run.id: [] for run in runs}
    if runs:
        for row in db.scalars(select(ImproveIteration).where(
                ImproveIteration.workspace_id == workspace_id, ImproveIteration.improve_run_id.in_(list(by_run)))
                .order_by(ImproveIteration.seq)):
            by_run[row.improve_run_id].append(row)
    can_act = can_execute_workspace_ml(db, actor, workspace_id)
    return ImproveRunPage(project_id=project_id, items=[
        run_read(run, by_run[run.id], agent=agent, can_act=can_act, token_id=token_id) for run in runs])
