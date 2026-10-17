"""P5.4-A2 (17): full improve loops on one real small dataset, driven job by job as one worker would.

Real auto-train runs, no engine mocks (one test forces a try's verdict to ``accepted`` so the accept
path runs on real locked evidence; the verdict itself is tested in test_improve_loop.py). Slow: six
trainings (the root, three sealed probes, one pinned final run, plus one final run that is cancelled).

Loop 1 (AI off, the plain goal = the primary metric): every try is a sealed branch compared with the
root on identical folds (gate 1) and its comparison frozen; the plan's k_i equal what the probes
really added; N counts probes in flight; the loop ends without a look (``kept_current`` scores
nothing) and writes no agent row. Loop 2 (constrained goal: max precision s.t. recall >= 0.5):
iteration 0 records the operating point and the loop trains afterwards; an accepted try ->
``waiting_user`` -> a person accepts ->
the final run cancelled before its final test returns to ``waiting_user`` (no look) -> the retry
re-runs the try with its selection pinned, locks the goal's threshold and scores the final test set
once (one look). Loop 3: a person cancels during ``finalizing`` before the look -> ``cancelled``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import (
    AgentRun,
    ExecutionRequest,
    Experiment,
    ImproveIteration,
    ImproveRun,
    LlmInvocation,
    MlJob,
    MlRunEvent,
    ModelEvaluation,
    ExperimentCandidate,
    ProjectDecisionRecord,
)
from app.domain.improve import ImproveGoal
from app.engine.modeling.operating_points import load_curve, solve
from app.services.experiment_verdict_service import locked_run_evidence
from app.services.improve.comparison import point_objective
from app.services.improve import loop_handler
from app.services.improve.comparison import TryVerdict
from app.services.improve.service import (
    accept_improve_run,
    cancel_improve_run,
    experiment_job,
    iterations,
    plan_comparisons,
    plan_look_count,
    start_improve_run,
)
from app.services.ml_job_service import process_next_job
from test_experiment_branching import _parent

GOAL = {"objective": {"maximize": "precision", "constraints": [{"metric": "recall", "op": ">=", "value": 0.5}]}}


def _step(db, run_id) -> ImproveRun:
    """What the worker does when the loop's continuation is due (the hook or the watchdog)."""

    db.expire_all()
    run = db.get(ImproveRun, run_id)
    job = db.get(MlJob, run.continuation_job_id)
    job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    finished = process_next_job(db, job_id=job.id)
    assert finished is not None and finished.status == "completed"
    db.expire_all()
    return db.get(ImproveRun, run_id)


def _run_job(db, experiment_id) -> MlJob:
    job = experiment_job(db, experiment_id=experiment_id, workspace_id=db.get(Experiment, experiment_id).workspace_id)
    finished = process_next_job(db, job_id=job.id)
    db.expire_all()
    return finished


def _trained(db, experiment_id) -> set[str]:
    return set(db.scalars(select(ExperimentCandidate.candidate_key).where(
        ExperimentCandidate.experiment_id == experiment_id, func.lower(ExperimentCandidate.status) == "trained")))


def test_improve_loops_on_a_real_dataset(engineer_client, db_session, engineer_user, monkeypatch):
    db = db_session
    parent = _parent(engineer_client, db, engineer_user, monkeypatch)
    ws = parent.workspace_id
    looks0 = plan_look_count(db, ws, parent.split_plan_id)
    assert looks0 == 1  # the root's own final-test look

    # --- loop 1: AI off, sealed tries, no look ----------------------------------------------------
    plain = {"plain": {"metric": parent.result["selection"]["selection_metric"]}}
    run = start_improve_run(db, actor=engineer_user, workspace_id=ws, start_experiment_id=parent.id, goal=plain,
                            budget={"max_iterations": 2, "patience": 2})
    assert run.status == "queued" and run.sealed_set_label == "reported_before" and run.looks_before == 1
    assert [(t["kind"], t["family"]) for t in run.plan] == [
        ("family_include", "extra_trees"), ("family_include", "gradient_boosting")]
    assert run.planned_comparisons == sum(t["k"] for t in run.plan) >= 2
    n0 = plan_comparisons(db, ws, parent.split_plan_id)
    run = _step(db, run.id)
    its = iterations(db, run)
    assert [(it.seq, it.kind, it.status, it.agent) for it in its] == [(0, "family_include", "running", "off")]
    probe = db.get(Experiment, its[0].experiment_id)
    assert probe.evidence_mode == "sealed" and probe.parent_pipeline_run_id == parent.id
    assert its[0].comparisons_before == n0 and its[0].idempotency_key == f"improve:{run.id}:0"
    assert plan_comparisons(db, ws, parent.split_plan_id) == n0 + 1 + its[0].planned_new_candidates  # (19)
    before = db.get(MlJob, run.continuation_job_id).available_at
    assert _run_job(db, probe.id).status == "completed"
    # The probe's terminal hook pulled the watchdog forward (never touched the probe's own job).
    assert db.get(MlJob, run.continuation_job_id).available_at < before
    run = _step(db, run.id)
    its = iterations(db, run)
    settled = its[0]
    assert settled.status in ("accepted", "rejected"), settled.comparison  # gate 1: root vs branch comparable
    # (12) k_i: what the probe really added is exactly what the plan priced in T.
    assert _trained(db, probe.id) - _trained(db, parent.id) == set(settled.planned_candidate_ids)
    assert settled.comparison["planned_comparisons"] == run.planned_comparisons
    assert settled.comparison["basis"] == "locked_cv_fold_evaluations" and len(settled.comparison["fold_scores"]) == 5
    second = its[1]
    assert second.status == "running" and second.action == {"kind": "family_include", "family": "gradient_boosting"}
    assert second.incumbent_experiment_id == run.incumbent_experiment_id
    _run_job(db, second.experiment_id)
    run = _step(db, run.id)
    # Two rejected tries reach patience 2 (rule 5) before the spent iteration budget (rule 8).
    assert run.stop_reason in ("no_improvement", "budget_iterations") and run.status in ("completed", "waiting_user")
    if run.status == "completed":  # nothing beat the noise: no person needed, nothing scored
        assert run.outcome == "kept_current" and run.continuation_job_id is None and not run.sealed_set_consulted
    # No probe touched the final test set; no look was spent; AI off wrote no agent row of any kind.
    probes = [it.experiment_id for it in iterations(db, run) if it.kind == "family_include"]
    assert not db.scalar(select(func.count()).select_from(ModelEvaluation).join(
        ExperimentCandidate, ExperimentCandidate.id == ModelEvaluation.candidate_id).where(
        ExperimentCandidate.experiment_id.in_(probes), ModelEvaluation.evaluation_scope == "final_holdout"))
    assert not db.scalars(select(MlRunEvent.event_type).where(MlRunEvent.experiment_id.in_(probes),
                                                             MlRunEvent.event_type.like("final_%"))).all()
    assert plan_look_count(db, ws, parent.split_plan_id) == looks0
    assert not db.scalar(select(func.count()).select_from(ProjectDecisionRecord).where(
        ProjectDecisionRecord.decision_type == "decision_point_resolved"))
    assert not db.scalar(select(func.count()).select_from(AgentRun))
    # The runs' own pipelines may log their (disabled) advisory calls; the loop itself logs none.
    for experiment_id, agent_run_id, purpose in db.execute(
            select(LlmInvocation.experiment_id, LlmInvocation.agent_run_id, LlmInvocation.purpose)):
        assert experiment_id in {parent.id, *probes} and agent_run_id is None and "improve" not in purpose
    if run.status == "waiting_user":
        run = accept_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id, keep_current=True)
    assert run.status == "completed" and plan_look_count(db, ws, parent.split_plan_id) == looks0

    # --- loop 2: an accepted try, a person accepts, one look --------------------------------------
    real_judge = loop_handler.judge

    def accept_it(db_, run_, live, goal):
        verdict = real_judge(db_, run_, live, goal)
        return TryVerdict("accepted", None, {**verdict.comparison, "status": "accepted", "forced_by_test": True})

    monkeypatch.setattr(loop_handler, "judge", accept_it)
    run = start_improve_run(db, actor=engineer_user, workspace_id=ws, start_experiment_id=parent.id, goal=GOAL,
                            budget={"max_iterations": 1, "patience": 1})
    assert [t["kind"] for t in run.plan] == ["operating_point", "family_include"]
    run = _step(db, run.id)
    op, probe_it = iterations(db, run)
    # (16) "max precision s.t. recall >= 0.5": iteration 0 records the operating point, then it trains.
    assert (op.kind, op.status, op.experiment_id, probe_it.status) == ("operating_point", "recorded", parent.id,
                                                                       "running")
    assert op.comparison["status"] in ("optimal", "infeasible") and op.comparison["threshold"] is not None
    _run_job(db, probe_it.experiment_id)
    run = _step(db, run.id)
    monkeypatch.setattr(loop_handler, "judge", real_judge)
    assert (run.status, run.stop_reason, run.incumbent_experiment_id) == (
        "waiting_user", "budget_iterations", probe_it.experiment_id)
    assert run.waiting_user_expires_at is not None and run.outcome is None
    seq = probe_it.seq
    run = accept_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id, iteration_seq=seq)
    assert run.status == "finalizing" and run.final_attempts == 1 and run.accepted_iteration_seq == seq
    first_final = db.get(Experiment, run.final_experiment_id)
    assert first_final.evidence_mode == "scored" and first_final.parent_pipeline_run_id == parent.id
    request = db.scalar(select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == first_final.id))
    assert request.requested_by_user_id == engineer_user.id and request.idempotency_key == f"improve-final:{run.id}:1"
    record = db.get(ProjectDecisionRecord, run.acceptance_record_id)
    assert record.decision_type == "experiment_accepted" and record.actor_kind == "human"
    assert record.idempotency_key == f"improve:{run.id}:accept" and record.details["improve_run_id"] == str(run.id)
    # The final run is stopped before its final test: no look, back to the person (retry allowed).
    final_job = experiment_job(db, ws, first_final.id)
    final_job.status, final_job.completed_at = "cancelled", datetime.now(UTC)
    db.commit()
    run = _step(db, run.id)
    assert (run.status, run.stop_detail) == ("waiting_user", "final_run_cancelled_before_look")
    assert plan_look_count(db, ws, parent.split_plan_id) == looks0
    run = accept_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id, iteration_seq=seq)
    final = db.get(Experiment, run.final_experiment_id)
    assert run.final_attempts == 2 and final.id != first_final.id
    assert _run_job(db, final.id).status == "completed"
    run = _step(db, run.id)
    assert (run.status, run.outcome, run.sealed_set_consulted) == ("completed", "improved", True)
    assert plan_look_count(db, ws, parent.split_plan_id) == looks0 + 1  # exactly one look for the loop
    final = db.get(Experiment, final.id)
    assert final.result["pinned_selection"]["reproduced"] is True
    assert locked_run_evidence(db, ws, final.id).winner == locked_run_evidence(db, ws, probe_it.experiment_id).winner
    # Gate 2: the final run locked the goal's own operating point (maximise precision s.t. recall >= 0.5).
    lock = final.result["decision_threshold"]
    assert (lock["source"], lock["goal_metric"]) == ("improve_goal", "precision")
    probe_curve = load_curve(db.get(Experiment, probe_it.experiment_id).result["operating_curve"])
    goal_point = solve(probe_curve, point_objective(ImproveGoal.model_validate(GOAL)))
    assert lock["value"] == float(probe_curve.thresholds[goal_point["index"]])  # the threshold the try was judged at
    assert final.result["objective"]["constraints"] == [{"metric": "recall", "op": ">=", "value": 0.5}]

    # --- loop 3: cancelled by a person during finalizing, before the look ------------------------
    monkeypatch.setattr(loop_handler, "judge", accept_it)
    run = start_improve_run(db, actor=engineer_user, workspace_id=ws, start_experiment_id=parent.id, goal=GOAL,
                            budget={"max_iterations": 1, "patience": 1})
    run = _step(db, run.id)
    _run_job(db, iterations(db, run)[-1].experiment_id)
    run = _step(db, run.id)
    monkeypatch.setattr(loop_handler, "judge", real_judge)
    run = accept_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id,
                             iteration_seq=iterations(db, run)[-1].seq)
    run = cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id)
    assert run.status == "finalizing" and run.cancel_requested_at is not None
    assert experiment_job(db, ws, run.final_experiment_id).status == "cancelled"
    run = _step(db, run.id)
    assert (run.status, run.outcome, run.stop_detail) == ("cancelled", "cancelled", "final_run_cancelled_before_look")
    assert plan_look_count(db, ws, parent.split_plan_id) == looks0 + 1
    assert db.scalar(select(func.count()).select_from(ImproveIteration).where(
        ImproveIteration.workspace_id == ws, ImproveIteration.status.in_(("planned", "running")))) == 0
