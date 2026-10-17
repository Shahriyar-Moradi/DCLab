"""The ``labs.improve`` step: one step of a loop per job, never waiting on another job (ADR 0007 §8).

A step acts only when its job is the run's ``continuation_job_id`` (a stale or forged job does
nothing). Phase 1 (run locked): re-check authority, settle a finished probe (frozen comparison,
incumbent pointer, spend), record iteration 0 (the operating point) once, apply the §4 stop rules in
order; a probe still running re-arms the watchdog. Phase 2 (run re-locked): N under the lock order,
then ``branch_experiment`` creates the sealed probe and, in the same transaction (``before_commit``),
the iteration turns ``running`` and the next continuation is armed. ``waiting_user`` and
``finalizing`` steps expire the wait or settle the final run. Terminal hooks only pull the
continuation forward (``on_job_terminal``); the watchdog covers every path no hook sees.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Experiment, ImproveIteration, ImproveRun, MlJob, User
from app.domain.errors import (
    ExperimentNotBranchableError,
    ExperimentNotFoundError,
    InvalidChangeSetError,
    QuotaExceededError,
    RunQuotaExceededError,
)
from app.domain.execution_requests import SOURCE_SYSTEM
from app.domain.improve import (
    IT_ACCEPTED,
    IT_CANCELLED,
    IT_FAILED,
    IT_NOT_COMPARABLE,
    IT_PLANNED,
    IT_RECORDED,
    IT_REJECTED,
    IT_RUNNING,
    KIND_FAMILY_INCLUDE,
    KIND_OPERATING_POINT,
    LIVE_RUN_STATUSES,
    MAX_FINAL_ATTEMPTS,
    MAX_STEP_ERRORS,
    QUOTA_BACKOFF_SECONDS,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_FINALIZING,
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_WAITING_USER,
    ImproveGoal,
)
from app.domain.ml_jobs import (
    HANDLER_LABS_AUTO_TRAIN,
    HANDLER_LABS_IMPROVE,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
)
from app.domain.pipeline_run_branch import EVIDENCE_MODE_SEALED, ServiceOverrides
from app.engine.evaluation.comparability import NONDETERMINISTIC_RERUN, probe_budget_seconds, realised_training_seconds
from app.engine.modeling.operating_points import curve_digest, goal_fold_scores, load_curve, solve
from app.services.improve import service as svc
from app.services.improve.comparison import TryVerdict, goal_met, judge_try, point_objective
from app.services.improve.rule_proposer import (
    PROPOSER_VERSION,
    PlannedTry,
    is_blocking,
    is_unstable,
    next_try,
    plan_from_dict,
)

logger = logging.getLogger("dclab.improve")
PROBE_INTENT = "Improve loop try {seq}: add the {family} family (sealed: the final test set is not used)."
_SETTLED = (IT_ACCEPTED, IT_REJECTED)


def _now() -> datetime:
    return datetime.now(UTC)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _experiment(db: Session, workspace_id: UUID, experiment_id: UUID) -> Experiment:
    return db.scalar(select(Experiment).where(Experiment.id == experiment_id, Experiment.workspace_id == workspace_id))


def _run_id(job: MlJob) -> UUID | None:
    try:
        return UUID(str((job.payload or {}).get("improve_run_id")))
    except (TypeError, ValueError):
        return None


def _locked(db: Session, job: MlJob, run_id: UUID) -> ImproveRun | None:
    """The run, locked, when this job may step it (filtered by the job's own workspace)."""

    run = db.scalar(select(ImproveRun).where(ImproveRun.id == run_id, ImproveRun.workspace_id == job.workspace_id)
                    .with_for_update())
    if run is None or run.continuation_job_id != job.id or run.status not in LIVE_RUN_STATUSES:
        return None
    return run


def handle_improve_step(db: Session, job: MlJob, *, on_heartbeat=None) -> None:
    run_id = _run_id(job)
    if run_id is None:
        return
    try:
        step(db, job, run_id)
    except Exception:  # noqa: BLE001 - a step error re-arms the loop; three in a row stop it
        logger.exception("improve step failed run=%s job=%s", run_id, job.id)
        db.rollback()
        _record_step_error(db, job, run_id)


def _record_step_error(db: Session, job: MlJob, run_id: UUID) -> None:
    run = _locked(db, job, run_id)
    if run is None:
        db.rollback()
        return
    run.step_errors += 1
    if run.step_errors >= MAX_STEP_ERRORS:
        svc.finish(db, run, status=RUN_FAILED, outcome="failed", now=_now(), stop_reason="failed",
                   detail="step_error", current_job_id=job.id)
    else:
        svc.arm(db, run, delay_seconds=QUOTA_BACKOFF_SECONDS, current_job_id=job.id)
    svc.commit(db)


def step(db: Session, job: MlJob, run_id: UUID) -> None:
    run = _locked(db, job, run_id)
    if run is None:
        db.rollback()
        return
    now = _now()
    if run.status == RUN_WAITING_USER:
        _waiting(db, run, job, now)
    elif run.status == RUN_FINALIZING:
        _finalizing(db, run, job, now)
    else:
        if run.status == RUN_QUEUED:
            run.status, run.started_at = RUN_RUNNING, now
        proceed = _evaluate(db, run, job, now)
        run.step_errors = 0
        svc.commit(db)
        if proceed:
            _propose(db, job, run_id)
            return
    svc.commit(db)


# --- waiting_user and finalizing ------------------------------------------------------------------


def _waiting(db: Session, run: ImproveRun, job: MlJob, now: datetime) -> None:
    if run.waiting_user_expires_at is not None and now >= run.waiting_user_expires_at:
        svc.finish(db, run, status=RUN_COMPLETED, outcome="kept_current", now=now, detail="waiting_user_expired",
                   current_job_id=job.id)
    else:
        svc.arm(db, run, at=run.waiting_user_expires_at, current_job_id=job.id)


def _finalizing(db: Session, run: ImproveRun, job: MlJob, now: datetime) -> None:
    """§6.2 / §6.4: completed -> ``improved``; failed or cancelled AFTER the final test started -> still a
    look (``improved``, the look recorded); before it -> back to ``waiting_user`` (no look; at most two
    retries), or ``cancelled`` when a person cancelled the loop. Never reads a final-test value."""

    from app.services.experiment_verdict_service import plan_looks

    final = db.scalar(select(Experiment).where(Experiment.id == run.final_experiment_id,
                                               Experiment.workspace_id == run.workspace_id))
    final_job = svc.experiment_job(db, run.workspace_id, run.final_experiment_id)
    if final is None:
        svc.finish(db, run, status=RUN_FAILED, outcome="failed", now=now, detail="final_run_missing",
                   current_job_id=job.id)
        return
    if final_job is not None and final_job.status in (JOB_QUEUED, JOB_RUNNING):
        svc.arm(db, run, current_job_id=job.id)
        return
    looked = final.id in plan_looks(db, run.workspace_id, run.split_plan_id)[0]
    completed = str(final.status or "").upper() == "COMPLETED" and final.scientific_evidence_locked_at is not None
    cancelled_job = final_job is not None and final_job.status == JOB_CANCELLED
    if completed or looked:
        run.sealed_set_consulted = True
        detail = None if completed else ("final_run_cancelled_after_look" if cancelled_job
                                         else "final_run_failed_after_look")
        svc.finish(db, run, status=RUN_COMPLETED, outcome="improved", now=now, detail=detail, current_job_id=job.id)
    elif run.cancel_requested_at is not None:
        svc.finish(db, run, status=RUN_CANCELLED, outcome="cancelled", now=now, stop_reason="cancelled",
                   detail="final_run_cancelled_before_look", current_job_id=job.id)
    else:
        detail = (NONDETERMINISTIC_RERUN if str(final.failure_reason or "").startswith(NONDETERMINISTIC_RERUN)
                  else "final_run_cancelled_before_look" if cancelled_job else "final_run_failed_before_look")
        if run.final_attempts >= MAX_FINAL_ATTEMPTS:
            svc.finish(db, run, status=RUN_FAILED, outcome="failed", now=now, detail=detail, current_job_id=job.id)
        else:
            svc.wait_for_person(db, run, now=now, stop_reason=run.stop_reason, detail=detail, current_job_id=job.id)


# --- phase 1: settle, record, stop ----------------------------------------------------------------


def _probe_state(db: Session, run: ImproveRun, experiment_id: UUID) -> str:
    experiment = db.scalar(select(Experiment).where(Experiment.id == experiment_id,
                                                    Experiment.workspace_id == run.workspace_id))
    job = svc.experiment_job(db, run.workspace_id, experiment_id)
    if job is not None and job.status in (JOB_QUEUED, JOB_RUNNING):
        return "running"
    if experiment is not None and str(experiment.status or "").upper() == "COMPLETED" \
            and experiment.scientific_evidence_locked_at is not None:
        return "completed"
    return "cancelled" if job is not None and job.status == JOB_CANCELLED else "failed"


def judge(db: Session, run: ImproveRun, live: ImproveIteration, goal: ImproveGoal) -> TryVerdict:
    """The §5 verdict of a finished probe against the incumbent it was branched from (locked rows only)."""

    from app.services.experiment_verdict_service import (
        _selection,
        _suspicious,
        locked_fold_scores,
        locked_run_evidence,
    )

    ws, inc_id, probe_id = run.workspace_id, live.incumbent_experiment_id, live.experiment_id
    evidence = [locked_run_evidence(db, ws, inc_id), locked_run_evidence(db, ws, probe_id)]
    selections = [_selection(db, ws, inc_id), _selection(db, ws, probe_id)]
    if None in evidence or None in selections:
        return TryVerdict(IT_NOT_COMPARABLE, "evidence_unreadable", {"reason": "evidence_unreadable"})
    if selections[0].selection_metric != selections[1].selection_metric:
        return TryVerdict(IT_NOT_COMPARABLE, "different_metric", {"reason": "different_metric"})
    metric = selections[1].selection_metric
    scores = [locked_fold_scores(db, ws, item.selected_candidate_id, metric) for item in selections]
    curves = [(_experiment(db, ws, run_id).result or {}).get("operating_curve") for run_id in (inc_id, probe_id)]
    return judge_try(goal=goal, metric=metric, incumbent=evidence[0], probe=evidence[1], incumbent_scores=scores[0],
                     probe_scores=scores[1], incumbent_curve=curves[0], probe_curve=curves[1],
                     planned_candidate_ids=list(live.planned_candidate_ids or []),
                     comparisons=run.planned_comparisons, suspicious=_suspicious(db, ws, probe_id))


def _settle(db: Session, run: ImproveRun, live: ImproveIteration, state: str, goal: ImproveGoal, now: datetime) -> None:
    from app.services.experiment_verdict_service import locked_run_evidence

    if state == "completed":
        verdict = judge(db, run, live, goal)
        evidence = locked_run_evidence(db, run.workspace_id, live.experiment_id)
        seconds = realised_training_seconds(evidence) if evidence is not None else 0.0
        live.status, live.reason, live.training_seconds = verdict.status, verdict.reason, seconds
        live.comparison = _json_safe(verdict.comparison)
        run.training_seconds_spent = float(run.training_seconds_spent or 0.0) + seconds
        if verdict.status == IT_ACCEPTED:
            run.incumbent_experiment_id = live.experiment_id
    else:
        live.status, live.reason = (IT_CANCELLED, "probe_cancelled") if state == "cancelled" else (IT_FAILED,
                                                                                                  "probe_failed")
    live.finished_at = now


def _record_operating_point(db: Session, run: ImproveRun, goal: ImproveGoal, seq: int, now: datetime) -> None:
    """Iteration 0 (§1b): the goal solved on the start's out-of-fold curve, no retrain; the loop continues."""

    from app.services.experiment_verdict_service import _selection, _winner_key

    start = _experiment(db, run.workspace_id, run.start_experiment_id)
    stored = (start.result or {}).get("operating_curve")
    selection = _selection(db, run.workspace_id, start.id)
    winner = _winner_key(db, run.workspace_id, selection.selected_candidate_id) if selection else None
    named = isinstance(stored, dict) and winner is not None and stored.get("candidate_id") == winner
    curve = load_curve(stored) if named else None
    comparison: dict[str, Any] = {"kind": KIND_OPERATING_POINT, "curve_digest": curve_digest(stored),
                                  "proposer": PROPOSER_VERSION, "outcome_scope": "cv"}
    if curve is None:
        comparison.update(status="not_evaluated", reason="no_goal_curve")
    else:
        solved = solve(curve, point_objective(goal))
        index = solved.get("index")
        comparison.update(status=solved["status"], reason=solved.get("reason"),
                          threshold=float(curve.thresholds[index]) if index is not None else None,
                          constraints=solved.get("constraints"),
                          message=("the current model already reaches the goal at this threshold (met on CV)"
                                   if solved["status"] == "optimal" else
                                   "no threshold meets the constraint; the model must change"))
    db.add(ImproveIteration(
        workspace_id=run.workspace_id, project_id=run.project_id, improve_run_id=run.id, seq=seq, plan_index=0,
        kind=KIND_OPERATING_POINT, action={"objective": goal.objective.model_dump(mode="json")}, status=IT_RECORDED,
        experiment_id=start.id, incumbent_experiment_id=start.id, planned_new_candidates=0, planned_candidate_ids=[],
        idempotency_key=svc.iteration_key(run.id, seq), comparison=_json_safe(comparison), finished_at=now,
    ))
    db.flush()


def _incumbent_goal_met(db: Session, run: ImproveRun, goal: ImproveGoal) -> bool:
    from app.services.experiment_verdict_service import _selection, _winner_key, locked_fold_scores

    selection = _selection(db, run.workspace_id, run.incumbent_experiment_id)
    if selection is None:
        return False
    objective = point_objective(goal)
    if objective is None:
        scores = locked_fold_scores(db, run.workspace_id, selection.selected_candidate_id, goal.metric)
        return scores is not None and goal_met(goal, [item.value for _n, item in sorted(scores.items())], feasible=None)
    stored = (_experiment(db, run.workspace_id, run.incumbent_experiment_id).result or {}).get("operating_curve")
    winner = _winner_key(db, run.workspace_id, selection.selected_candidate_id)
    if not isinstance(stored, dict) or stored.get("candidate_id") != winner or (curve := load_curve(stored)) is None:
        return False
    try:
        scores = goal_fold_scores(curve, objective)
    except Exception:  # noqa: BLE001 - GoalScoreRefusal: the goal cannot be read per fold, so it is not met
        return False
    return goal_met(goal, list(scores.values), feasible=scores.feasible)


def _retraining(tries: tuple[PlannedTry, ...]) -> tuple[PlannedTry, ...]:
    return tuple(item for item in tries if item.kind == KIND_FAMILY_INCLUDE)


def stop_reason(db: Session, run: ImproveRun, goal: ImproveGoal, its: list[ImproveIteration], now: datetime) -> str | None:
    """§4 rules 2-9 in their precedence (rule 1, cancel / authority, is checked first by the caller)."""

    from app.services.experiment_verdict_service import locked_run_evidence

    tries = _retraining(plan_from_dict(run.plan))
    trained_its = [it for it in its if it.kind == KIND_FAMILY_INCLUDE]
    nxt = next_try(tries, [(it.plan_index, it.status) for it in trained_its if it.status != IT_PLANNED])
    if nxt == "failed_twice":
        return "failed"
    if is_blocking(svc.stored_findings(db, run.workspace_id, run.incumbent_experiment_id)):
        if run.incumbent_experiment_id != run.start_experiment_id:
            # §1b: revert the pointer to the incumbent the accepted try was compared with; no probe trains.
            accepted = next((it for it in trained_its if it.experiment_id == run.incumbent_experiment_id), None)
            run.incumbent_experiment_id = (accepted.incumbent_experiment_id if accepted is not None
                                           else run.start_experiment_id)
        return "blocking_finding"
    if goal.target is not None and _incumbent_goal_met(db, run, goal):
        return "goal_met"
    budget = dict(run.budget or {})
    trailing = 0
    for it in reversed([it for it in trained_its if it.status in _SETTLED]):
        if it.status != IT_REJECTED:
            break
        trailing += 1
    if trailing >= int(budget.get("patience", 3)):
        return "no_improvement"
    if is_unstable(svc.stored_findings(db, run.workspace_id, run.incumbent_experiment_id)):
        return "unstable_evidence"
    if svc.plan_comparisons(db, run.workspace_id, run.split_plan_id) >= int(budget.get("comparison_bound", 30)):
        return "comparison_bound"
    if any(it.status == IT_NOT_COMPARABLE and it.reason == "engine_or_data_drift"
           and it.incumbent_experiment_id == run.incumbent_experiment_id for it in trained_its):
        # Shared candidates of the unchanged incumbent did not reproduce (e.g. a root trained before the
        # canonical pool order): every later try would drift the same way, so nothing is left to compare.
        run.stop_detail = "engine_or_data_drift"
        return "proposer_exhausted"
    if len([it for it in trained_its if it.status != IT_PLANNED]) >= int(budget.get("max_iterations", 6)):
        return "budget_iterations"
    elapsed = (now - (run.started_at or now)).total_seconds()
    spent, total = float(run.training_seconds_spent or 0.0), float(budget.get("max_training_seconds_total") or 0.0)
    need = 0.0
    if isinstance(nxt, PlannedTry):
        evidence = locked_run_evidence(db, run.workspace_id, run.incumbent_experiment_id)
        need = probe_budget_seconds(evidence, nxt.k) if evidence is not None else None  # type: ignore[assignment]
        if need is None:
            return "budget_wall"  # the try cannot fit the per-probe budget
    if elapsed + need > float(budget.get("max_wall_seconds", 7200)):
        return "budget_wall"  # a probe is never started unless its full budget fits the remainder
    if total and spent + need > total:
        return "budget_compute"
    if nxt is None:
        return "proposer_exhausted"
    return None


def _stop(db: Session, run: ImproveRun, reason: str, its: list[ImproveIteration], now: datetime, job: MlJob) -> None:
    if reason == "failed":
        svc.finish(db, run, status=RUN_FAILED, outcome="failed", now=now, stop_reason=reason, current_job_id=job.id)
    elif reason == "blocking_finding":
        svc.finish(db, run, status=RUN_COMPLETED, outcome="blocked", now=now, stop_reason=reason,
                   current_job_id=job.id)
    elif run.incumbent_experiment_id != run.start_experiment_id:
        svc.wait_for_person(db, run, now=now, stop_reason=reason, detail=None, current_job_id=job.id)
    elif reason == "goal_met":
        svc.finish(db, run, status=RUN_COMPLETED, outcome="goal_met", now=now, stop_reason=reason,
                   current_job_id=job.id)
    else:
        # §4 rule 9 / §12: when no try could be compared fairly, never "no change beyond noise".
        verdicts = [it.status for it in its if it.kind == KIND_FAMILY_INCLUDE]
        nothing = IT_NOT_COMPARABLE in verdicts and not any(status in _SETTLED for status in verdicts)
        svc.finish(db, run, status=RUN_COMPLETED, outcome="nothing_comparable" if nothing else "kept_current",
                   now=now, stop_reason=reason, current_job_id=job.id)


def _evaluate(db: Session, run: ImproveRun, job: MlJob, now: datetime) -> bool:
    if not svc.authority_ok(db, run, now):
        svc.finish(db, run, status=RUN_CANCELLED, outcome="cancelled", now=now, stop_reason="cancelled",
                   detail="authority_revoked", current_job_id=job.id)
        return False
    goal = ImproveGoal.model_validate(run.goal)
    live = svc.live_iteration(db, run)
    if live is not None and live.status == IT_RUNNING:
        state = _probe_state(db, run, live.experiment_id)
        if state == "running":
            if (now - (run.started_at or now)).total_seconds() >= float(run.budget.get("max_wall_seconds", 7200)):
                _stop(db, run, "budget_wall", svc.iterations(db, run), now, job)
            else:
                svc.arm(db, run, current_job_id=job.id)
            return False
        _settle(db, run, live, state, goal, now)
        db.flush()
    its = svc.iterations(db, run)
    tries = plan_from_dict(run.plan)
    if tries and tries[0].kind == KIND_OPERATING_POINT and not any(it.kind == KIND_OPERATING_POINT for it in its):
        _record_operating_point(db, run, goal, max((it.seq for it in its), default=-1) + 1, now)
        its = svc.iterations(db, run)
    reason = stop_reason(db, run, goal, its, now)
    if reason is not None:
        _stop(db, run, reason, its, now, job)
        return False
    return True


# --- phase 2: enqueue the sealed probe ------------------------------------------------------------


def _propose(db: Session, job: MlJob, run_id: UUID) -> None:
    from app.services.experiment_branch_service import branch_experiment
    from app.services.experiment_verdict_service import locked_run_evidence

    run = _locked(db, job, run_id)
    if run is None or run.status != RUN_RUNNING:
        db.rollback()
        return
    now = _now()
    if not svc.authority_ok(db, run, now):
        svc.finish(db, run, status=RUN_CANCELLED, outcome="cancelled", now=now, stop_reason="cancelled",
                   detail="authority_revoked", current_job_id=job.id)
        svc.commit(db)
        return
    live = svc.live_iteration(db, run)
    if live is None:
        its = svc.iterations(db, run)
        nxt = next_try(_retraining(plan_from_dict(run.plan)),
                       [(it.plan_index, it.status) for it in its if it.kind == KIND_FAMILY_INCLUDE])
        if not isinstance(nxt, PlannedTry):  # phase 1 just checked; a concurrent change -> next step decides
            svc.arm(db, run, delay_seconds=0, current_job_id=job.id)
            db.commit()
            return
        seq = max((it.seq for it in its), default=-1) + 1
        live = ImproveIteration(
            workspace_id=run.workspace_id, project_id=run.project_id, improve_run_id=run.id, seq=seq,
            plan_index=nxt.index, kind=KIND_FAMILY_INCLUDE, action=nxt.change, status=IT_PLANNED,
            incumbent_experiment_id=run.incumbent_experiment_id, planned_new_candidates=nxt.k,
            planned_candidate_ids=list(nxt.new_candidate_ids), idempotency_key=svc.iteration_key(run.id, seq),
        )
        db.add(live)
        db.flush()
    svc._lock_plan(db, run.workspace_id, run.split_plan_id)
    n_before = svc.plan_comparisons(db, run.workspace_id, run.split_plan_id)
    evidence = locked_run_evidence(db, run.workspace_id, live.incumbent_experiment_id)
    budget = probe_budget_seconds(evidence, live.planned_new_candidates) if evidence is not None else None
    creator = db.get(User, run.created_by_user_id)

    def bind(shell: Experiment) -> None:
        live.experiment_id, live.status, live.comparisons_before = shell.id, IT_RUNNING, n_before
        svc.arm(db, run, current_job_id=job.id)

    try:
        if budget is None or creator is None:
            raise InvalidChangeSetError("probe_budget_unavailable", "the probe's time budget cannot be set")
        result = branch_experiment(
            db, actor=creator, workspace_id=run.workspace_id, parent_id=live.incumbent_experiment_id,
            changes=[dict(live.action)], intent=PROBE_INTENT.format(seq=live.seq, family=live.action.get("family")),
            idempotency_key=live.idempotency_key, source_surface=SOURCE_SYSTEM, before_commit=bind,
            initiated_by_service_token_id=run.created_by_service_token_id,
            service_overrides=ServiceOverrides(evidence_mode=EVIDENCE_MODE_SEALED, max_training_seconds=budget),
        )
        if not result.created:  # a replay of this try's own reserved key: bind the existing probe
            bind(result.experiment)
            svc.commit(db)
    except (RunQuotaExceededError, QuotaExceededError):
        # §3: the try stays planned and is retried with backoff (the wall budget still applies).
        svc.arm(db, run, delay_seconds=QUOTA_BACKOFF_SECONDS, current_job_id=job.id)
        db.commit()
    except (InvalidChangeSetError, ExperimentNotBranchableError, ExperimentNotFoundError) as exc:
        code = getattr(exc, "reason", None) or getattr(exc, "code", None) or "branch_refused"
        live.status, live.reason, live.finished_at = IT_FAILED, str(code)[:64], now
        svc.arm(db, run, delay_seconds=0, current_job_id=job.id)
        db.commit()


# --- terminal hooks (after the job's terminal commit, own transaction, best-effort) ---------------


def on_job_terminal(db: Session, job: MlJob) -> None:
    """A loop's probe or final run ended: pull the loop's watchdog forward (never touches ``job``). A
    ``labs.improve`` job that failed for good (worker lost three times): the loop stops ``failed``."""

    workspace_id = job.workspace_id
    if job.handler_key == HANDLER_LABS_AUTO_TRAIN and job.pipeline_run_id is not None:
        run_id = db.scalar(select(ImproveIteration.improve_run_id).where(
            ImproveIteration.workspace_id == workspace_id, ImproveIteration.experiment_id == job.pipeline_run_id,
            ImproveIteration.kind == KIND_FAMILY_INCLUDE).limit(1)) or db.scalar(select(ImproveRun.id).where(
                ImproveRun.workspace_id == workspace_id, ImproveRun.final_experiment_id == job.pipeline_run_id))
        if run_id is None:
            return
        db.execute(text(
            "UPDATE ml_jobs SET available_at = now() WHERE workspace_id = :ws AND status = 'queued' AND id = "
            "(SELECT continuation_job_id FROM improve_runs WHERE id = :run AND workspace_id = :ws)"),
            {"ws": workspace_id, "run": run_id})
        db.commit()
    elif job.handler_key == HANDLER_LABS_IMPROVE and job.status == JOB_FAILED:
        # The step job itself failed for good (its worker was lost on every attempt): re-arm so the
        # next step decides (waiting_user / finalizing settle as usual); repeated failures stop a loop
        # that is still proposing.
        run = db.scalar(select(ImproveRun).where(ImproveRun.workspace_id == workspace_id,
                                                 ImproveRun.continuation_job_id == job.id,
                                                 ImproveRun.status.in_(LIVE_RUN_STATUSES)).with_for_update())
        if run is not None:
            run.step_errors += 1
            if run.step_errors >= MAX_STEP_ERRORS and run.status in (RUN_QUEUED, RUN_RUNNING):
                svc.finish(db, run, status=RUN_FAILED, outcome="failed", now=_now(), stop_reason="failed",
                           detail="step_job_failed")
            else:
                svc.arm(db, run, delay_seconds=QUOTA_BACKOFF_SECONDS)
        svc.commit(db)
