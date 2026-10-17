"""P5.4-A2: the improve loop's state machine on one real root, without training the probes.

The loop creates its sealed probes for real (``branch_experiment``); the test then finishes them the
way the platform would without training: cancelled through the experiment cancel (a watchdog-only
path), failed, or -- where a verdict is needed -- with the probe's state and verdict forced (the
verdict is tested in test_improve_loop.py). Covers (19) N and the bound, (20) budgets (each stops
with its reason, cancels the live probe, keeps the best so far), (21) §4 precedence (the start's
findings are injected as a shuffled-label or leaking root would store them), (22) one probe per
step, stale / forged ``labs.improve`` jobs, the watchdog-only path, (23) cancels, the accept-vs-cancel
race and the ``waiting_user`` expiry, (24) idempotent resume after a 429, (25) authority, (27) the
tables' guards (triggers, CHECKs, three-column FKs).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    ExecutionRequest,
    Experiment,
    ImproveIteration,
    ImproveRun,
    MlJob,
    ProjectDecisionRecord,
    WorkspaceMembership,
)
from app.domain.errors import ImproveRunError, RunQuotaExceededError
from app.domain.ml_jobs import HANDLER_LABS_IMPROVE, JOB_TYPE_IMPROVE
from app.services import improve
from app.services.experiment_service import cancel_experiment
from app.services.experiment_verdict_service import _selection
from app.services.improve import loop_handler
from app.services.improve import service as svc
from app.services.improve.comparison import TryVerdict
from app.services.improve.service import (
    accept_improve_run,
    cancel_improve_run,
    experiment_job,
    iterations,
    plan_comparisons,
    start_improve_run,
)
from app.services.ml_job_service import create_ml_job, process_next_job
from conftest import ENGINEER_PASSWORD
from test_experiment_branching import _parent
from test_improve_loop_real import _step

assert improve  # the package


def _live_probe(db, run) -> ImproveIteration:
    live = svc.live_iteration(db, db.get(ImproveRun, run.id))
    assert live is not None and live.status == "running" and live.experiment_id is not None
    return live


def _fail(db, experiment_id) -> None:
    job = experiment_job(db, db.get(Experiment, experiment_id).workspace_id, experiment_id)
    job.status, job.completed_at = "failed", datetime.now(UTC)
    db.get(Experiment, experiment_id).status = "FAILED"
    db.commit()


def _queued_auto_train(db, root_id) -> int:
    """Queued training jobs besides the root's own (the root was trained directly, not via the queue)."""

    return int(db.scalar(select(func.count()).select_from(MlJob).where(
        MlJob.job_type == "auto_train", MlJob.status == "queued", MlJob.pipeline_run_id != root_id)))


def test_loop_state_machine(engineer_client, db_session, engineer_user, monkeypatch):
    db = db_session
    parent = _parent(engineer_client, db, engineer_user, monkeypatch)
    ws = parent.workspace_id
    plain = {"plain": {"metric": _selection(db, ws, parent.id).selection_metric}}

    def start(budget=None, **kwargs) -> ImproveRun:
        return start_improve_run(db, actor=engineer_user, workspace_id=ws, start_experiment_id=parent.id,
                                 goal=plain, budget=budget, **kwargs)

    # --- (22) one probe per step; stale and forged jobs do nothing; the watchdog-only path -----
    run = start({"max_iterations": 2, "patience": 2})
    first_job = db.get(MlJob, run.continuation_job_id)
    run = _step(db, run.id)
    probe = _live_probe(db, run)
    probes_before = db.scalar(select(func.count()).select_from(Experiment).where(
        Experiment.parent_pipeline_run_id == parent.id))
    loop_handler.step(db, db.get(MlJob, first_job.id), run.id)  # a stale continuation: not the run's job
    forged = create_ml_job(db, workspace_id=ws, job_type=JOB_TYPE_IMPROVE, handler_key=HANDLER_LABS_IMPROVE,
                           target_id=run.id, payload={"improve_run_id": str(run.id)})
    db.commit()
    assert process_next_job(db, job_id=forged.id).status == "completed"
    # The probe's hook and the watchdog together: one step, which re-arms (the probe is still queued).
    loop_handler.on_job_terminal(db, experiment_job(db, ws, probe.experiment_id))
    run = _step(db, run.id)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Experiment).where(
        Experiment.parent_pipeline_run_id == parent.id)) == probes_before
    assert [it.status for it in iterations(db, run)] == ["running"]
    # Cancelled through the experiment's own cancel (no hook fires): the watchdog carries the loop on.
    assert cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=probe.experiment_id) == "cancelled"
    run = _step(db, run.id)
    its = iterations(db, run)
    assert [(it.status, it.reason) for it in its] == [("cancelled", "probe_cancelled"), ("running", None)]
    # (20) budget_iterations: the second try fails; two tries used of two.
    _fail(db, its[1].experiment_id)
    run = _step(db, run.id)
    assert (run.status, run.outcome, run.stop_reason) == ("completed", "kept_current", "budget_iterations")
    assert run.continuation_job_id is None and iterations(db, run)[1].status == "failed"

    # --- (21) "failed" (rule 2) outranks the spent iteration budget (rule 8) -------------------
    run = start({"max_iterations": 2, "patience": 2})
    run = _step(db, run.id)
    _fail(db, _live_probe(db, run).experiment_id)
    run = _step(db, run.id)
    retry = _live_probe(db, run)
    assert retry.plan_index == iterations(db, run)[0].plan_index  # the same try, once more
    _fail(db, retry.experiment_id)
    run = _step(db, run.id)
    assert (run.status, run.outcome, run.stop_reason) == ("failed", "failed", "failed")

    # --- (20) budget_wall mid-probe cancels the live probe; budget_compute before any try --------
    run = start()
    run = _step(db, run.id)
    live = _live_probe(db, run)
    db.execute(update(ImproveRun).where(ImproveRun.id == run.id).values(
        started_at=datetime.now(UTC) - timedelta(hours=3)))
    db.commit()
    run = _step(db, run.id)
    assert (run.status, run.outcome, run.stop_reason) == ("completed", "kept_current", "budget_wall")
    assert experiment_job(db, ws, live.experiment_id).status == "cancelled"
    assert db.get(Experiment, live.experiment_id).status == "CANCELLED"  # its envelope too (after the commit)
    assert db.get(ImproveIteration, live.id).status == "cancelled"
    run = start({"max_training_seconds_total": 1.0})
    run = _step(db, run.id)
    assert (run.stop_reason, run.outcome) == ("budget_compute", "kept_current") and not iterations(db, run)

    # --- (17) nothing_comparable: a drift against the unchanged start stops at once, never "no change" ----
    drift = TryVerdict("not_comparable", "engine_or_data_drift", {"forced_by_test": True})
    with monkeypatch.context() as patch:
        patch.setattr(loop_handler, "_probe_state", lambda *_args: "completed")
        patch.setattr(loop_handler, "judge", lambda *_args: drift)
        run = start({"max_iterations": 3})
        run = _step(db, run.id)
        drifted = _live_probe(db, run)
        run = _step(db, run.id)
    cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=drifted.experiment_id)
    assert (run.status, run.outcome, run.stop_reason, run.stop_detail) == (
        "completed", "nothing_comparable", "proposer_exhausted", "engine_or_data_drift")
    assert len(iterations(db, run)) == 1

    # --- (20) keeps the best so far; (23) waiting_user expiry; accept-vs-cancel race --------------
    forced = TryVerdict("accepted", None, {"forced_by_test": True})
    with monkeypatch.context() as patch:
        patch.setattr(loop_handler, "_probe_state", lambda *_args: "completed")
        patch.setattr(loop_handler, "judge", lambda *_args: forced)
        run = start()
        run = _step(db, run.id)
        winner = _live_probe(db, run)
        db.execute(update(ImproveRun).where(ImproveRun.id == run.id).values(
            started_at=datetime.now(UTC) - timedelta(hours=3)))
        db.commit()
        run = _step(db, run.id)
    assert (run.status, run.stop_reason, run.incumbent_experiment_id) == (
        "waiting_user", "budget_wall", winner.experiment_id)
    assert run.waiting_user_expires_at > datetime.now(UTC) + timedelta(days=13)
    assert db.get(MlJob, run.continuation_job_id).available_at == run.waiting_user_expires_at
    cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=winner.experiment_id)
    db.execute(update(ImproveRun).where(ImproveRun.id == run.id).values(
        waiting_user_expires_at=datetime.now(UTC) - timedelta(seconds=5)))
    db.commit()
    run = _step(db, run.id)
    assert (run.status, run.outcome, run.stop_detail) == ("completed", "kept_current", "waiting_user_expired")
    def forced_waiting() -> tuple[ImproveRun, ImproveIteration]:
        with monkeypatch.context() as patch:
            patch.setattr(loop_handler, "_probe_state", lambda *_args: "completed")
            patch.setattr(loop_handler, "judge", lambda *_args: forced)
            waiting = start()
            waiting = _step(db, waiting.id)
            probe_it = _live_probe(db, waiting)
            db.execute(update(ImproveRun).where(ImproveRun.id == waiting.id).values(
                started_at=datetime.now(UTC) - timedelta(hours=3)))
            db.commit()
            waiting = _step(db, waiting.id)
        cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=probe_it.experiment_id)
        assert waiting.status == "waiting_user"
        return waiting, probe_it

    # Keep the current model: nothing is scored; the acceptance record names the start and the loop.
    looks = svc.plan_look_count(db, ws, parent.split_plan_id)
    run, _kept = forced_waiting()
    run = accept_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id, keep_current=True)
    assert (run.status, run.outcome, run.incumbent_experiment_id) == ("completed", "kept_current", parent.id)
    record = db.get(ProjectDecisionRecord, run.acceptance_record_id)
    assert (record.subject_kind, record.experiment_id, record.idempotency_key, record.details["improve_run_id"]) == (
        "experiment", parent.id, f"improve:{run.id}:accept", str(run.id))
    assert run.final_experiment_id is None and svc.plan_look_count(db, ws, parent.split_plan_id) == looks
    with monkeypatch.context() as patch:
        patch.setattr(loop_handler, "_probe_state", lambda *_args: "completed")
        patch.setattr(loop_handler, "judge", lambda *_args: forced)
        run = start()
        run = _step(db, run.id)
        stuck = _live_probe(db, run)
        db.execute(update(ImproveRun).where(ImproveRun.id == run.id).values(
            started_at=datetime.now(UTC) - timedelta(hours=3)))
        db.commit()
        run = _step(db, run.id)
    cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=stuck.experiment_id)
    assert run.status == "waiting_user"
    run = cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id)  # the cancel wins the lock
    assert (run.status, run.outcome) == ("cancelled", "cancelled")
    with pytest.raises(ImproveRunError) as late:
        accept_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id, iteration_seq=stuck.seq)
    assert late.value.code == "not_waiting_for_a_person"
    db.rollback()

    # --- (21) precedence on the start's stored findings; shuffled labels stop before any try ----
    unstable = [{"check": "fold_instability", "status": "warning", "evidence": {"worst_fold_at_baseline": True}}]
    leaking = [{"check": "target_leakage", "status": "fail", "evidence": {}}]
    with monkeypatch.context() as patch:
        patch.setattr(svc, "stored_findings", lambda *_args: unstable)
        run = start()
        run = _step(db, run.id)
        assert (run.stop_reason, run.outcome) == ("unstable_evidence", "kept_current") and not iterations(db, run)
        run = start({"max_iterations": 1})
        patch.setattr(svc, "stored_findings", lambda *_args: unstable + leaking)
        run = _step(db, run.id)
        assert (run.stop_reason, run.outcome) == ("blocking_finding", "blocked")  # rule 3 before 6 and 8
        with pytest.raises(ImproveRunError) as blocked:
            start()
        assert (blocked.value.code, blocked.value.status_code) == ("blocking_finding", 409)

    # --- (23) cancel at a step boundary; (24) a 429 keeps the try planned and resumes once -------
    run = start()
    job_id = run.continuation_job_id
    run = cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id)
    assert (run.status, run.stop_detail) == ("cancelled", "cancelled_by_person")
    assert db.get(MlJob, job_id).status == "cancelled"
    assert cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id).status == "cancelled"

    def busy(*_args, **_kwargs):
        raise RunQuotaExceededError(1)

    run = start()
    with monkeypatch.context() as patch:
        patch.setattr("app.services.ml_job_service.ensure_run_capacity", busy)
        run = _step(db, run.id)
    planned = iterations(db, run)[0]
    assert (planned.status, planned.experiment_id) == ("planned", None)
    assert db.get(MlJob, run.continuation_job_id).available_at > datetime.now(UTC) + timedelta(seconds=30)
    run = _step(db, run.id)
    resumed = _live_probe(db, run)
    assert (resumed.id, resumed.idempotency_key) == (planned.id, f"improve:{run.id}:{planned.seq}")
    assert db.scalar(select(func.count()).select_from(ExecutionRequest).where(
        ExecutionRequest.idempotency_key == resumed.idempotency_key)) == 1
    from app.services.experiment_branch_service import branch_experiment
    from app.domain.pipeline_run_branch import ServiceOverrides

    replay = branch_experiment(db, actor=engineer_user, workspace_id=ws, parent_id=parent.id,
                               changes=[resumed.action], intent="replay", idempotency_key=resumed.idempotency_key,
                               service_overrides=ServiceOverrides(evidence_mode="sealed",
                                                                  max_training_seconds=float(
                                                                      db.get(Experiment, resumed.experiment_id)
                                                                      .config["service_overrides"]
                                                                      ["max_training_seconds"])))
    assert not replay.created and replay.experiment.id == resumed.experiment_id

    # --- (25) authority: creator deactivated, role downgraded, token revoked -> stop + cancel ------
    engineer_user.is_active = False
    db.commit()
    run = _step(db, run.id)
    assert (run.status, run.stop_detail) == ("cancelled", "authority_revoked")
    assert experiment_job(db, ws, resumed.experiment_id).status == "cancelled"
    engineer_user.is_active = True
    db.commit()
    membership = db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.user_id == engineer_user.id,
                                                             WorkspaceMembership.workspace_id == ws))
    run = start()
    run = _step(db, run.id)
    downgraded = _live_probe(db, run)
    membership.role = "viewer"
    db.commit()
    run = _step(db, run.id)
    assert (run.status, run.stop_detail) == ("cancelled", "authority_revoked")
    assert experiment_job(db, ws, downgraded.experiment_id).status == "cancelled"
    membership.role = "ml_engineer"
    db.commit()
    from app.services.service_token_service import create_service_token, revoke_service_token

    token, _secret = create_service_token(db, creator=engineer_user, workspace_id=ws, name="loop",
                                          scopes=["read", "experiments:write"], expires_in_days=7,
                                          current_password=ENGINEER_PASSWORD)
    db.commit()
    run = start({"comparison_bound": 60}, service_token_id=token.id)
    run = _step(db, run.id)
    token_probe = _live_probe(db, run)
    request = db.scalar(select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == token_probe.experiment_id))
    assert request.initiated_by_service_token_id == token.id
    revoke_service_token(db, actor=engineer_user, workspace_id=ws, token_id=token.id)
    db.commit()
    run = _step(db, run.id)
    assert (run.status, run.stop_detail) == ("cancelled", "authority_revoked")
    assert experiment_job(db, ws, token_probe.experiment_id).status == "cancelled"
    assert cancel_improve_run(db, actor=engineer_user, workspace_id=ws, run_id=run.id,
                              service_token_id=token.id).status == "cancelled"  # idempotent on a finished loop

    # --- (19) N counts probes, manual branches and k_i; the bound refuses POST and stops a loop -----
    assert _queued_auto_train(db, parent.id) == 0  # every stop cancelled its probe
    n0 = plan_comparisons(db, ws, parent.split_plan_id)  # the root (cancelled / failed tries no longer count)
    run = start({"comparison_bound": n0 + 1})
    run = _step(db, run.id)
    bound_probe = _live_probe(db, run)
    assert bound_probe.comparisons_before == n0  # frozen at enqueue
    assert plan_comparisons(db, ws, parent.split_plan_id) == n0 + 1 + bound_probe.planned_new_candidates
    manual = branch_experiment(db, actor=engineer_user, workspace_id=ws, parent_id=parent.id,
                               changes=[{"kind": "family_exclude", "family": "xgboost"}], intent="manual")
    assert plan_comparisons(db, ws, parent.split_plan_id) == n0 + 2 + bound_probe.planned_new_candidates
    cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=bound_probe.experiment_id)
    # The cancelled probe's experiment no longer counts; its try's k_i does until the loop settles it.
    assert plan_comparisons(db, ws, parent.split_plan_id) == n0 + 1 + bound_probe.planned_new_candidates
    run = _step(db, run.id)
    assert plan_comparisons(db, ws, parent.split_plan_id) == n0 + 1  # the manual branch, still in flight
    assert (run.status, run.stop_reason, run.outcome) == ("completed", "comparison_bound", "kept_current")
    with pytest.raises(ImproveRunError) as exhausted:
        start({"comparison_bound": n0 + 1})
    assert (exhausted.value.code, exhausted.value.status_code) == ("split_plan_exhausted", 409)
    token2, _ = create_service_token(db, creator=engineer_user, workspace_id=ws, name="loop2",
                                     scopes=["read", "experiments:write"], expires_in_days=7,
                                     current_password=ENGINEER_PASSWORD)
    db.commit()
    with pytest.raises(ImproveRunError) as half:  # N + T > bound / 2 for a token
        start({"comparison_bound": 2 * (n0 + 1) + 1}, service_token_id=token2.id)
    assert half.value.code == "split_plan_exhausted"
    start({"comparison_bound": 2 * (n0 + 1) + 1}).id  # a person may (the same bound)
    db.execute(update(ImproveRun).where(ImproveRun.workspace_id == ws, ImproveRun.status == "queued").values(
        status="cancelled", outcome="cancelled", finished_at=datetime.now(UTC)))
    db.commit()
    cancel_experiment(db, actor=engineer_user, workspace_id=ws, experiment_id=manual.experiment.id)

    # --- (27) the tables' guards ------------------------------------------------------------------
    finished = db.get(ImproveRun, run.id)
    for statement, params in (
        ("UPDATE improve_runs SET status = 'running' WHERE id = :id", {"id": finished.id}),  # finished is final
        ("UPDATE improve_runs SET goal = '{}'::jsonb WHERE id = :id", {"id": finished.id}),
    ):
        with pytest.raises(DBAPIError):
            db.execute(text(statement), params)
        db.rollback()
    live_run = start({"comparison_bound": 60})
    for statement in (
        "UPDATE improve_runs SET planned_comparisons = planned_comparisons + 1 WHERE id = :id",  # T frozen
        "UPDATE improve_runs SET status = 'finalizing' WHERE id = :id",  # queued -> finalizing is no transition
        "UPDATE improve_runs SET final_experiment_id = start_experiment_id WHERE id = :id",  # no acceptance: CHECK
    ):
        with pytest.raises(DBAPIError):
            db.execute(text(statement), {"id": live_run.id})
        db.rollback()
    record = db.scalar(select(ProjectDecisionRecord.id).where(
        ProjectDecisionRecord.workspace_id == ws, ProjectDecisionRecord.decision_type == "experiment_accepted"))
    assert record is not None  # the keep-current record written above
    db.execute(update(ImproveRun).where(ImproveRun.id == live_run.id).values(
        accepted_by_user_id=engineer_user.id, accepted_iteration_seq=1))
    db.commit()
    with pytest.raises(DBAPIError):  # write-once
        db.execute(update(ImproveRun).where(ImproveRun.id == live_run.id).values(accepted_iteration_seq=2))
    db.rollback()
    settled = db.scalar(select(ImproveIteration).where(ImproveIteration.workspace_id == ws,
                                                       ImproveIteration.status == "cancelled").limit(1))
    for statement in ("UPDATE improve_iterations SET status = 'running' WHERE id = :id",  # a finished try is final
                      "UPDATE improve_iterations SET action = '{}'::jsonb WHERE id = :id"):
        with pytest.raises(DBAPIError):
            db.execute(text(statement), {"id": settled.id})
        db.rollback()
    other_project = uuid4()
    with pytest.raises(DBAPIError):  # three-column FK: an iteration cannot name another project's run
        db.execute(text(
            "INSERT INTO improve_iterations (id, workspace_id, project_id, improve_run_id, seq, plan_index, kind, "
            "action, status, incumbent_experiment_id, planned_new_candidates, planned_candidate_ids, "
            "idempotency_key) VALUES (:id, :ws, :project, :run, 99, 1, 'family_include', '{}'::jsonb, 'planned', "
            ":inc, 1, '[]'::jsonb, :key)"),
            {"id": uuid4(), "ws": ws, "project": other_project, "run": live_run.id, "inc": parent.id,
             "key": f"x-{uuid4()}"})
    db.rollback()
    with pytest.raises(DBAPIError):  # one live try per loop (partial unique)
        for seq in (50, 51):
            db.execute(text(
                "INSERT INTO improve_iterations (id, workspace_id, project_id, improve_run_id, seq, plan_index, kind, "
                "action, status, incumbent_experiment_id, planned_new_candidates, planned_candidate_ids, "
                "idempotency_key) VALUES (:id, :ws, :project, :run, :seq, 1, 'family_include', '{}'::jsonb, "
                "'planned', :inc, 1, '[]'::jsonb, :key)"),
                {"id": uuid4(), "ws": ws, "project": parent.project_id, "run": live_run.id, "seq": seq,
                 "inc": parent.id, "key": f"y-{seq}"})
    db.rollback()
    with pytest.raises(ImproveRunError) as second:  # one live loop per split plan
        start()
    assert second.value.code == "improve_run_live"
