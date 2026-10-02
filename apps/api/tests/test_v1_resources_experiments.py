"""P3.1-B2: /v1 experiments — list, read, root run, branch, compare, cancel.

Real auto-train runs (the worker job runner is called in-test, as the durable
worker would), no engine mocks. Root runs reuse the Labs run envelope keyed by a
ClientLabUpload row; branches reuse P2.4-A; compare refuses different split plans.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.db.models import (
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    IdempotencyKey,
    MlJob,
    ModelSelectionDecision,
    User,
    WorkflowRun,
    WorkspaceRole,
)
from datetime import UTC, datetime, timedelta

from app.db.models import Dataset, ExecutionRequest
from app.engine.experiments import runner
from app.services.lab_service import ingest_dataset
from app.services.ml_job_service import process_next_job, recover_abandoned_jobs
from app.services.workspace_service import add_workspace_member
from test_data_model_lineage import make_lineage_setup
from test_v1_contract_conventions import _assert_envelope, _headers


@pytest.fixture()
def setup(db_session, tmp_path):
    return make_lineage_setup(db_session, tmp_path)


def _csv(n: int = 240, seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    plan = rng.choice(["basic", "plus", "pro"], n)
    tenure = rng.integers(1, 72, n).astype(float)
    visits = rng.integers(0, 4, n)
    logit = -0.6 + 0.9 * (plan == "basic") - 0.02 * tenure + 0.3 * visits
    label = rng.binomial(1, 1 / (1 + np.exp(-logit)))
    frame = pd.DataFrame(
        {
            "tenure": tenure,
            "spend": rng.uniform(20, 120, n),
            "plan": plan,
            "visits": visits,
            "label": np.where(label == 1, "yes", "no"),
        }
    )
    return frame.to_csv(index=False).encode()


def _key() -> str:
    return f"p31b2-{uuid4().hex}"


def _h(setup, user=None, key: str | None = None, workspace=None, **extra) -> dict[str, str]:
    headers = _headers(user or setup["alpha_admin"], (workspace or setup["alpha"]).id, **extra)
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _member(db_session, setup, role: WorkspaceRole) -> User:
    membership = add_workspace_member(
        db_session,
        actor=setup["alpha_admin"],
        workspace_id=setup["alpha"].id,
        email=f"p31b2-{role.value}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=role.value,
    )
    db_session.commit()
    return db_session.get(User, membership.user_id)


def _dataset(client, setup, *, workspace="alpha", seed: int = 7) -> str:
    response = client.post(
        "/v1/datasets",
        headers=_h(setup, setup[f"{workspace}_admin"], key=_key(), workspace=setup[workspace]),
        data={"project_id": str(setup[f"{workspace}_project"].id)},
        files={"file": ("rows.csv", _csv(seed=seed), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _root(client, setup, dataset_id: str, *, key: str | None = None, workspace="alpha", **body):
    payload = {"project_id": str(setup[f"{workspace}_project"].id), "dataset_id": dataset_id,
               "target_column": "label", **body}
    return client.post(
        "/v1/experiments",
        json=payload,
        headers=_h(setup, setup[f"{workspace}_admin"], key=key or _key(), workspace=setup[workspace]),
    )


def _job(db_session, experiment_id) -> MlJob:
    db_session.expire_all()
    return db_session.scalar(select(MlJob).where(MlJob.pipeline_run_id == experiment_id))


def _work(db_session, experiment_id) -> MlJob:
    """What the durable worker does: claim this experiment's job and run it."""

    job = _job(db_session, experiment_id)
    done = process_next_job(db_session, job_id=job.id, claimed_by="p31b2-test")
    assert done is not None
    db_session.expire_all()
    return done


def _count(db_session, model) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model)) or 0


# --- end to end: upload -> root run -> worker -> read -> branch -> compare --------------------


def test_root_run_branch_compare_end_to_end(client, db_session, setup):
    dataset_id = _dataset(client, setup)
    key = _key()
    created = _root(client, setup, dataset_id, key=key, intent="baseline run")
    assert created.status_code == 202, created.text
    root = created.json()
    assert created.headers["Location"] == f"/v1/experiments/{root['id']}"
    assert root["status"] == "queued" and root["lineage"]["source_dataset_id"] == dataset_id
    assert root["intent"] == "baseline run" and "intent" in root["untrusted_fields"]
    # The root rides the Labs run envelope: one upload row on the same dataset, one job.
    shell = db_session.get(Experiment, root["id"])
    upload = db_session.scalar(select(ClientLabUpload).where(ClientLabUpload.experiment_id == shell.id))
    assert str(upload.dataset_id) == dataset_id and upload.explicit_target_column == "label"
    assert _job(db_session, shell.id).handler_key == "labs.auto_train"

    # Idempotent replay: same experiment, no second run envelope; another body is 409.
    before = (_count(db_session, Experiment), _count(db_session, MlJob))
    replay = _root(client, setup, dataset_id, key=key, intent="baseline run")
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == root["id"]
    assert (_count(db_session, Experiment), _count(db_session, MlJob)) == before
    _assert_envelope(_root(client, setup, dataset_id, key=key, intent="other"), 409, "idempotency_key_conflict")
    stored = db_session.scalar(select(IdempotencyKey).where(IdempotencyKey.idempotency_key == key))
    assert (stored.resource_kind, stored.response_status, str(stored.resource_id)) == ("experiment", 202, root["id"])

    # A queued parent cannot be branched.
    early = client.post(
        f"/v1/experiments/{root['id']}/branches",
        json={"intent": "too early", "changes": [{"kind": "family_exclude", "family": "xgboost"}]},
        headers=_h(setup, key=_key()),
    )
    _assert_envelope(early, 409, "parent_not_completed")

    assert _work(db_session, shell.id).status == "completed"
    read = client.get(f"/v1/experiments/{root['id']}", headers=_h(setup))
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["status"] == "completed" and body["task_type"] == "binary" and body["target_column"] == "label"
    assert body["lineage"]["split_plan_id"] and body["lineage"]["prepared_dataset_id"]
    metrics = body["metrics"]
    assert metrics["cv"] and metrics["holdout"] and metrics["candidate_id"]
    assert metrics["decision_threshold"] is not None and metrics["baseline_comparison"]["metric"]
    assert body["change_set"] is None and body["diff_vs_parent"] is None
    assert client.get(f"/v1/experiments/{root['id']}", headers=_h(setup)).headers["ETag"] == read.headers["ETag"]
    _assert_envelope(client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup)), 409, "not_cancellable")

    # Branch: typed change-set errors are 422 with reason/path, lineage keys need a new root.
    branches = f"/v1/experiments/{root['id']}/branches"
    bad = client.post(
        branches,
        json={"intent": "x", "changes": [{"kind": "family_include", "family": "not_a_family"}]},
        headers=_h(setup, key=_key()),
    )
    details = _assert_envelope(bad, 422, "invalid_change_set")["details"]
    assert details == {"reason": "unknown_family", "path": "changes[0].family"}
    lineage = client.post(
        branches,
        json={"intent": "x", "target_column": "plan", "changes": [{"kind": "family_exclude", "family": "xgboost"}]},
        headers=_h(setup, key=_key()),
    )
    assert _assert_envelope(lineage, 422, "invalid_change_set")["details"]["reason"] == "new_root_required"
    blank = client.post(branches, json={"intent": "  ", "changes": [{"kind": "family_exclude", "family": "xgboost"}]},
                        headers=_h(setup, key=_key()))
    assert _assert_envelope(blank, 422, "invalid_change_set")["details"]["reason"] == "intent_required"

    branch_key = _key()
    change = {"intent": "drop the boosted family", "changes": [{"kind": "family_exclude", "family": "xgboost"}]}
    branched = client.post(branches, json=change, headers=_h(setup, key=branch_key))
    assert branched.status_code == 202, branched.text
    child = branched.json()
    assert child["lineage"]["parent_experiment_id"] == root["id"]
    assert child["lineage"]["split_plan_id"] == body["lineage"]["split_plan_id"]
    assert child["change_set"]["changes"] == change["changes"]
    again = client.post(branches, json=change, headers=_h(setup, key=branch_key))
    assert again.status_code == 202 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == child["id"]
    # The service's own execution-request key is not used by /v1 (one binding only).
    assert db_session.scalar(
        select(func.count()).select_from(Experiment).where(Experiment.parent_pipeline_run_id == root["id"])
    ) == 1

    assert _work(db_session, child["id"]).status == "completed"
    child_read = client.get(f"/v1/experiments/{child['id']}", headers=_h(setup)).json()
    assert child_read["status"] == "completed"
    assert child_read["diff_vs_parent"]["status"] == "compared"
    assert child_read["diff_vs_parent"]["holdout"]

    compared = client.get(f"/v1/experiments/compare?ids={root['id']},{child['id']}", headers=_h(setup))
    assert compared.status_code == 200, compared.text
    comparison = compared.json()
    assert comparison["split_plan_id"] == body["lineage"]["split_plan_id"]
    assert [row["experiment_id"] for row in comparison["experiments"]] == [root["id"], child["id"]]
    assert comparison["authoritative"] is False and comparison["common"]["holdout"]
    assert comparison["experiments"][0]["holdout"] == metrics["holdout"]

    # A run on another split plan (a queued root has none yet) is never compared.
    other = _root(client, setup, _dataset(client, setup, seed=11)).json()
    refused = client.get(f"/v1/experiments/compare?ids={root['id']},{other['id']}", headers=_h(setup))
    _assert_envelope(refused, 409, "split_plan_mismatch")

    # List filters, newest first, opaque cursor bound to the filter set.
    def ids(**params):
        response = client.get("/v1/experiments", params=params, headers=_h(setup))
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()["items"]]

    assert ids() == [other["id"], child["id"], root["id"]]
    assert ids(parent_id=root["id"]) == [child["id"]]
    assert ids(has_change_set="true") == [child["id"]]
    assert ids(status="completed") == [child["id"], root["id"]]
    assert ids(status="queued") == [other["id"]]
    assert ids(split_plan_id=body["lineage"]["split_plan_id"]) == [child["id"], root["id"]]
    assert ids(project_id=str(setup["alpha_project"].id)) == [other["id"], child["id"], root["id"]]
    first = client.get("/v1/experiments", params={"limit": 2}, headers=_h(setup)).json()
    assert first["next_cursor"] and len(first["items"]) == 2
    rest = client.get("/v1/experiments", params={"limit": 2, "cursor": first["next_cursor"]}, headers=_h(setup))
    assert [row["id"] for row in rest.json()["items"]] == [root["id"]] and rest.json()["next_cursor"] is None
    cursor = first["next_cursor"]
    tampered = cursor[:-2] + ("AA" if not cursor.endswith("AA") else "BB")
    _assert_envelope(client.get("/v1/experiments", params={"cursor": tampered}, headers=_h(setup)), 400, "invalid_cursor")
    other_filter = client.get("/v1/experiments", params={"cursor": cursor, "status": "completed"}, headers=_h(setup))
    _assert_envelope(other_filter, 400, "invalid_cursor")


# --- cancellation ---------------------------------------------------------------------------


def test_cancel_queued_run_is_immediate_idempotent_and_never_claimed(client, db_session, setup):
    root = _root(client, setup, _dataset(client, setup)).json()
    path = f"/v1/experiments/{root['id']}/cancel"
    stale = client.post(path, headers=_h(setup, **{"If-Match": '"stale"'}))
    _assert_envelope(stale, 412, "precondition_failed")
    assert _job(db_session, root["id"]).status == "queued"
    current = client.get(f"/v1/experiments/{root['id']}", headers=_h(setup)).headers["ETag"]

    cancelled = client.post(path, headers=_h(setup, **{"If-Match": current}))
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["cancel_requested_at"]
    job = _job(db_session, root["id"])
    assert job.status == "cancelled" and job.completed_at is not None
    assert job.cancel_requested_by_user_id == setup["alpha_admin"].id  # audited actor
    experiment = db_session.get(Experiment, root["id"])
    assert experiment.status == "CANCELLED"
    upload = db_session.get(ClientLabUpload, job.upload_id)
    # The coarse client vocabulary has no "cancelled" (CHECK): documented as failed.
    assert (upload.pipeline_status, upload.client_status) == ("cancelled", "failed")
    assert db_session.scalar(select(WorkflowRun.status).where(WorkflowRun.source_upload_id == upload.id)) == "cancelled"
    # The worker never claims it.
    assert process_next_job(db_session, job_id=job.id) is None

    # Twice -> the same result, even with the now stale If-Match.
    again = client.post(path, headers=_h(setup, **{"If-Match": current}))
    assert again.status_code == 200 and again.json() == cancelled.json()
    assert again.headers["ETag"] == cancelled.headers["ETag"]
    listed = client.get("/v1/experiments", params={"status": "cancelled"}, headers=_h(setup)).json()
    assert [row["id"] for row in listed["items"]] == [root["id"]]


def test_cancel_running_run_stops_at_the_next_candidate(client, db_session, setup, monkeypatch):
    root = _root(client, setup, _dataset(client, setup)).json()
    seen: list[str] = []
    replies: list = []
    original = runner._emit_event

    def spy(callback, event_type, **payload):
        if event_type in {"candidate_started", "candidate_completed"}:
            seen.append(event_type)
        if event_type == "candidate_started" and not replies:
            # The API request lands while the worker trains (separate session).
            replies.append(client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup)))
            replies.append(client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup)))
        return original(callback, event_type, **payload)

    monkeypatch.setattr(runner, "_emit_event", spy)
    job = _work(db_session, root["id"])

    assert [reply.status_code for reply in replies] == [202, 202]
    assert replies[0].json()["status"] == "cancelling" and replies[1].json() == replies[0].json()
    assert seen == ["candidate_started"], "no candidate trains after the cancellation checkpoint"
    assert job.status == "cancelled" and job.cancel_requested_at is not None and job.attempts == 1
    experiment = db_session.get(Experiment, root["id"])
    assert experiment.status == "CANCELLED" and experiment.scientific_evidence_locked_at is None
    assert db_session.scalar(
        select(func.count()).select_from(ModelSelectionDecision).where(ModelSelectionDecision.pipeline_run_id == experiment.id)
    ) == 0
    assert db_session.scalar(
        select(func.count()).select_from(ExperimentCandidate).where(ExperimentCandidate.experiment_id == experiment.id)
    ) == 0
    read = client.get(f"/v1/experiments/{root['id']}", headers=_h(setup)).json()
    assert read["status"] == "cancelled" and read["metrics"] is None
    after = client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup))
    assert after.status_code == 200 and after.json()["status"] == "cancelled"
    # Not requeued: nothing left for a worker.
    assert process_next_job(db_session, job_id=job.id) is None


# --- validation, authorization, tenant isolation ----------------------------------------------


def test_root_run_validation_and_dataset_rules(client, db_session, setup):
    dataset_id = _dataset(client, setup)
    project = str(setup["alpha_project"].id)
    keyless = client.post("/v1/experiments", json={"project_id": project, "dataset_id": dataset_id}, headers=_h(setup))
    _assert_envelope(keyless, 400, "idempotency_key_required")
    extra = client.post(
        "/v1/experiments", json={"project_id": project, "dataset_id": dataset_id, "seed": 1}, headers=_h(setup, key=_key())
    )
    _assert_envelope(extra, 422, "validation_failed")
    _assert_envelope(_root(client, setup, dataset_id, target_column="nope"), 422, "target_not_in_dataset")
    _assert_envelope(_root(client, setup, str(uuid4())), 404, "dataset_not_found")
    # A legacy dataset outside the project (and without an uploaded artifact) never starts a run.
    legacy = setup["alpha_dataset"]
    assert legacy.project_id != setup["alpha_project"].id and legacy.artifact_id is None
    _assert_envelope(_root(client, setup, str(legacy.id)), 422, "dataset_not_in_project")
    # An in-project dataset that was not uploaded (no dataset artifact) never starts a run.
    tmp = setup["alpha_dataset"].location
    not_uploaded = ingest_dataset(
        db_session, environment=setup["env"], name="Derived", location=tmp,
        workspace_id=setup["alpha"].id, project_id=setup["alpha_project"].id,
    )
    db_session.commit()
    assert not_uploaded.artifact_id is None
    _assert_envelope(_root(client, setup, str(not_uploaded.id)), 409, "dataset_not_uploaded")
    wrong_project = client.post(
        "/v1/experiments",
        json={"project_id": str(uuid4()), "dataset_id": dataset_id},
        headers=_h(setup, key=_key()),
    )
    _assert_envelope(wrong_project, 404, "not_found")
    for query in ("ids=" + str(uuid4()), "ids=" + ",".join(str(uuid4()) for _ in range(11)), "ids=a,b"):
        _assert_envelope(client.get(f"/v1/experiments/compare?{query}", headers=_h(setup)), 422, "validation_failed")
    same = str(uuid4())
    _assert_envelope(client.get(f"/v1/experiments/compare?ids={same},{same}", headers=_h(setup)), 422, "validation_failed")
    _assert_envelope(client.get("/v1/experiments", params={"status": "bogus"}, headers=_h(setup)), 422, "validation_failed")
    assert _count(db_session, Experiment) == 0


def test_viewer_reads_but_cannot_start_branch_or_cancel(client, db_session, setup):
    root = _root(client, setup, _dataset(client, setup)).json()
    viewer = _member(db_session, setup, WorkspaceRole.VIEWER)
    assert client.get(f"/v1/experiments/{root['id']}", headers=_h(setup, viewer)).status_code == 200
    assert client.get("/v1/experiments", headers=_h(setup, viewer)).status_code == 200
    body = {"project_id": str(setup["alpha_project"].id), "dataset_id": root["lineage"]["source_dataset_id"]}
    _assert_envelope(client.post("/v1/experiments", json=body, headers=_h(setup, viewer, key=_key())), 403, "forbidden")
    _assert_envelope(
        client.post(
            f"/v1/experiments/{root['id']}/branches",
            json={"intent": "x", "changes": [{"kind": "family_exclude", "family": "xgboost"}]},
            headers=_h(setup, viewer, key=_key()),
        ),
        403,
        "forbidden",
    )
    _assert_envelope(client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup, viewer)), 403, "forbidden")
    assert _job(db_session, root["id"]).status == "queued"


def test_foreign_experiments_are_not_found_everywhere(client, db_session, setup):
    alpha = _root(client, setup, _dataset(client, setup)).json()
    beta_dataset = _dataset(client, setup, workspace="beta")
    beta = _root(client, setup, beta_dataset, workspace="beta").json()
    for path in (f"/v1/experiments/{beta['id']}", f"/v1/experiments/compare?ids={alpha['id']},{beta['id']}"):
        _assert_envelope(client.get(path, headers=_h(setup)), 404, "not_found")
    _assert_envelope(client.post(f"/v1/experiments/{beta['id']}/cancel", headers=_h(setup)), 404, "not_found")
    branch = client.post(
        f"/v1/experiments/{beta['id']}/branches",
        json={"intent": "x", "changes": [{"kind": "family_exclude", "family": "xgboost"}]},
        headers=_h(setup, key=_key()),
    )
    _assert_envelope(branch, 404, "not_found")
    # Another tenant's dataset or project is indistinguishable from a missing one.
    _assert_envelope(_root(client, setup, beta_dataset), 404, "dataset_not_found")
    foreign_project = client.post(
        "/v1/experiments",
        json={"project_id": str(setup["beta_project"].id), "dataset_id": beta_dataset},
        headers=_h(setup, key=_key()),
    )
    _assert_envelope(foreign_project, 404, "not_found")
    listed = client.get("/v1/experiments", headers=_h(setup)).json()
    assert [row["id"] for row in listed["items"]] == [alpha["id"]]
    _assert_envelope(
        client.get("/v1/experiments", params={"project_id": str(setup["beta_project"].id)}, headers=_h(setup)),
        404,
        "not_found",
    )
    # Selecting the other workspace without membership is 403, not data.
    _assert_envelope(client.get(f"/v1/experiments/{beta['id']}", headers=_h(setup, workspace=setup["beta"])), 403)
    assert _job(db_session, beta["id"]).status == "queued"


def _assert_run_cancelled(db_session, experiment_id) -> MlJob:
    job = _job(db_session, experiment_id)
    assert job.status == "cancelled" and job.cancel_requested_at is not None
    upload = db_session.get(ClientLabUpload, job.upload_id)
    assert upload.pipeline_status == "cancelled"
    assert db_session.get(Experiment, experiment_id).status == "CANCELLED"
    assert db_session.scalar(select(WorkflowRun.status).where(WorkflowRun.source_upload_id == upload.id)) == "cancelled"
    return job


def test_cancel_request_wins_over_needs_input_requeue_and_failure_retry(client, db_session, setup):
    """A run that stops for input (explicit target -> would requeue) or fails after a
    cancel request ends cancelled: never requeued, never retried."""

    for outcome in ("needs_input", "failed"):
        root = _root(client, setup, _dataset(client, setup)).json()

        def fake_run(db, upload_id, outcome=outcome, experiment_id=root["id"]):
            # The cancel request lands while the run works; the run ends before a checkpoint.
            assert client.post(f"/v1/experiments/{experiment_id}/cancel", headers=_h(setup)).status_code == 202
            upload = db.get(ClientLabUpload, upload_id)
            upload.pipeline_status = outcome
            db.commit()
            if outcome == "failed":
                raise RuntimeError("boom")

        job = _job(db_session, root["id"])
        done = process_next_job(db_session, job_id=job.id, runner=fake_run)
        assert done.status == "cancelled" and done.attempts == 1
        _assert_run_cancelled(db_session, root["id"])
        assert process_next_job(db_session, job_id=job.id) is None  # nothing requeued
        read = client.get(f"/v1/experiments/{root['id']}", headers=_h(setup)).json()
        assert read["status"] == "cancelled"


def test_abandoned_job_with_cancel_request_is_recovered_as_cancelled(client, db_session, setup):
    root = _root(client, setup, _dataset(client, setup)).json()
    job = _job(db_session, root["id"])
    past = datetime.now(UTC) - timedelta(hours=1)
    job.status, job.attempts, job.started_at, job.heartbeat_at = "running", 1, past, past
    job.lease_expires_at, job.cancel_requested_at = past, past
    upload = db_session.get(ClientLabUpload, job.upload_id)
    upload.pipeline_status = "training"
    db_session.commit()
    recovered = recover_abandoned_jobs(db_session)
    db_session.commit()
    assert [row.id for row in recovered] == [job.id]
    _assert_run_cancelled(db_session, root["id"])


def test_target_confirmation_cannot_reopen_a_cancelled_run(client, db_session, setup):
    root = _root(client, setup, _dataset(client, setup)).json()
    assert client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup)).status_code == 200
    request = db_session.scalar(select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == root["id"]))
    request.status = "needs_input"  # a run that was waiting for its target when cancelled
    db_session.commit()
    confirm = client.post(
        f"/v1/execution-requests/{request.id}/target-confirmation",
        json={"target_column": "label"},
        headers=_h(setup, key=_key()),
    )
    _assert_envelope(confirm, 409, "execution_not_waiting")
    _assert_run_cancelled(db_session, root["id"])


def test_cancel_after_the_holdout_is_touched_is_not_honoured(client, db_session, setup, monkeypatch):
    """Once the final holdout is being scored the run must finish and lock that
    single evaluation; a late cancel cannot discard (hide) it."""

    root = _root(client, setup, _dataset(client, setup)).json()
    replies: list = []
    original = runner._emit_event

    def spy(callback, event_type, **payload):
        if event_type == "final_test_started" and not replies:
            replies.append(client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup)))
        return original(callback, event_type, **payload)

    monkeypatch.setattr(runner, "_emit_event", spy)
    job = _work(db_session, root["id"])
    assert replies[0].status_code == 202 and replies[0].json()["status"] == "cancelling"
    assert job.status == "completed" and job.cancel_requested_at is not None
    experiment = db_session.get(Experiment, root["id"])
    assert experiment.status == "COMPLETED" and experiment.scientific_evidence_locked_at is not None
    assert db_session.scalar(
        select(func.count()).select_from(ModelSelectionDecision).where(ModelSelectionDecision.pipeline_run_id == experiment.id)
    ) == 1
    read = client.get(f"/v1/experiments/{root['id']}", headers=_h(setup)).json()
    assert read["status"] == "completed" and read["metrics"]["holdout"]
    _assert_envelope(client.post(f"/v1/experiments/{root['id']}/cancel", headers=_h(setup)), 409, "not_cancellable")


def test_active_run_quota_is_429_and_replays_still_answer(client, db_session, setup, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ml_max_active_runs_per_workspace", 1)
    dataset_id = _dataset(client, setup)
    key = _key()
    first = _root(client, setup, dataset_id, key=key)
    assert first.status_code == 202
    over = _root(client, setup, dataset_id)
    error = _assert_envelope(over, 429, "run_quota_exceeded")
    assert error["retryable"] is True and error["details"]["limit"] == 1
    assert _root(client, setup, dataset_id, key=key).headers["Idempotent-Replayed"] == "true"
    # Cancelling frees the slot.
    assert client.post(f"/v1/experiments/{first.json()['id']}/cancel", headers=_h(setup)).status_code == 200
    assert _root(client, setup, dataset_id).status_code == 202


def test_keys_cursors_and_untrusted_text_are_scoped_and_redacted(client, db_session, setup):
    dataset_id = _dataset(client, setup)
    key = _key()
    secretish = "see /Users/alice/private/notes and email bob@example.com"
    mine = _root(client, setup, dataset_id, key=key, intent=secretish).json()
    assert mine["intent"] == "[REDACTED]"
    listed = client.get("/v1/experiments", headers=_h(setup)).json()["items"]
    assert listed[0]["intent"] == "[REDACTED]" and listed[0]["untrusted_fields"] == ["intent"]
    # Another ML-write principal's identical key is an independent key: no replay.
    engineer = _member(db_session, setup, WorkspaceRole.ML_ENGINEER)
    theirs = client.post(
        "/v1/experiments",
        json={"project_id": str(setup["alpha_project"].id), "dataset_id": dataset_id,
              "target_column": "label", "intent": secretish},
        headers=_h(setup, engineer, key=key),
    )
    assert theirs.status_code == 202 and "Idempotent-Replayed" not in theirs.headers
    assert theirs.json()["id"] != mine["id"]
    # A cursor minted in one workspace is invalid in another.
    page = client.get("/v1/experiments", params={"limit": 1}, headers=_h(setup)).json()
    assert page["next_cursor"]
    beta = client.get(
        "/v1/experiments",
        params={"limit": 1, "cursor": page["next_cursor"]},
        headers=_h(setup, setup["beta_admin"], workspace=setup["beta"]),
    )
    _assert_envelope(beta, 400, "invalid_cursor")
