"""D3a: the founder-decided Data and Goal items 11-14.

11 target rate by month: training rows only; the final-test months are a sealed span (no rate) until the
   model in use had its final test; tokens always sealed; a final-test label never moves a training number.
12 upload log: accepted and rejected attempts on both upload paths, metadata only, append-only, 90-day purge.
13 upload checks: whole-file checks stored at upload and shown on the profile.
14 who decided: reserved decision entries from confirmations and goal versions; "you" never for tokens.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.models import ProjectDecisionRecord, SplitPlan, UploadAttempt
from app.domain.decision_records import DecisionActor
from app.domain.errors import DecisionActorNotPermittedError, InvalidDecisionRecordError
from app.services import dataset_profile_service as profile_service
from app.services import decision_record_service as drs
from app.services.auth_service import create_access_token
from app.services.dataset_profile_service import target_periods
from app.services.goal_decision_service import goal_decisions, record_test_design_confirmed
from app.services.split_plan_service import load_assignment
from app.services.upload_attempt_service import purge_expired_upload_attempts, sanitize_file_name
from app.services.upload_checks import run_upload_checks
from dclab_client import DCLabClient
from test_execution_target_confirmation import _wait_for_target
from test_v1_resources_experiments import _work
from test_v1_service_tokens import _b, _token, st  # noqa: F401  (fixture)

BASE = "http://testserver"
ROW_KEYS = {"what", "suggested_by", "confirmed_by", "when", "why", "why_is_default", "value", "decision_id"}


def _h(user, workspace) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}", "X-Workspace-Id": str(workspace.id)}


# --- 11: target rate by month -------------------------------------------------------------------


def test_target_periods_count_training_rows_only_and_seal_the_final_test():
    frame = pd.DataFrame({
        "month": ["2025-01-05", "2025-01-20", "2025-02-03", "2025-02-17", "2025-03-01", "2025-03-09", "2025-03-30",
                  "2025-04-02", "2025-04-15", "2025-05-01"],
        "label": ["stayed", "left", "stayed", "stayed", "left", "left", "stayed", "left", "left", "left"],
    })
    train, holdout = set(range(7)), {7, 8, 9}
    sealed = target_periods(frame, train, holdout, target="label", time_column="month", after_training=True,
                            disclose=False)
    assert [(b["month"], b["part"], b["rows"]) for b in sealed["buckets"]] == [
        ("2025-01", "training", 2), ("2025-02", "training", 2), ("2025-03", "training", 3)]
    # Two arbitrary labels: the later one ("stayed") is positive, as in the engine.
    assert [b["rate"] for b in sealed["buckets"]] == pytest.approx([0.5, 1.0, 1 / 3])
    assert sealed["training_rate"] == pytest.approx(4 / 7)
    assert sealed["final_test"] == {"start_month": "2025-04", "end_month": "2025-05", "rate": None}
    # No final-test label moves anything while sealed (their target is not even read).
    changed = frame.copy()
    changed.loc[7:9, "label"] = ["stayed", "stayed", "garbage"]
    assert target_periods(changed, train, holdout, target="label", time_column="month", after_training=True,
                          disclose=False) == sealed
    # Disclosed: the training mapping is applied to the final-test rows (all "left" = 0.0, although on their
    # own they are a single label the engine could not map).
    shown = target_periods(frame, train, holdout, target="label", time_column="month", after_training=True,
                           disclose=True)
    assert shown["final_test"]["rate"] == 0.0
    assert [b["part"] for b in shown["buckets"]] == ["training"] * 3 + ["final_test"] * 2
    assert shown["buckets"][:3] == sealed["buckets"]
    # A split that mixes final-test rows into every month gives no month span and no per-month final rate.
    mixed = target_periods(frame, train, holdout, target="label", time_column="month", after_training=False,
                           disclose=True)
    assert (mixed["final_test"]["start_month"], mixed["final_test"]["end_month"]) == (None, None)
    assert {b["part"] for b in mixed["buckets"]} == {"training"}
    # The final-test rate uses the TRAINING 0/1 mapping only: unknown final-test words are no answer, and a
    # target the training rows cannot map (three labels) has no rate at all.
    words = frame.assign(label=["yes", "no", "yes", "yes", "no", "no", "yes", "maybe", "zzz", "zzz"])
    assert target_periods(words, train, holdout, target="label", time_column="month", after_training=True,
                          disclose=True)["final_test"]["rate"] is None
    three = frame.assign(label=["a", "b", "c", "a", "b", "c", "a", "b", "a", "c"])
    out = target_periods(three, train, holdout, target="label", time_column="month", after_training=True,
                         disclose=True)
    assert (out["training_rate"], out["final_test"]["rate"]) == (None, None)
    # Numbers are never read as dates; a map row the file lacks fails closed.
    assert target_periods(frame.assign(month=range(10)), train, holdout, target="label", time_column="month",
                          after_training=True, disclose=False) == {"status": "time_not_dates"}
    with pytest.raises(ValueError):
        target_periods(frame, train | {99}, holdout, target="label", time_column="month", after_training=True,
                       disclose=False)


def _dated_csv(tmp_path, n=240):
    rng = np.random.default_rng(11)
    start = datetime(2023, 1, 1)
    tenure = rng.integers(1, 72, n).astype(float)
    label = rng.binomial(1, 1 / (1 + np.exp(0.4 + 0.03 * tenure)))
    frame = pd.DataFrame({
        "signup": [(start + timedelta(days=31 * (i // 6))).date().isoformat() for i in range(n)],  # 6 rows a month
        "tenure": tenure, "plan": rng.choice(["basic", "pro"], n),
        "label": np.where(label == 1, "yes", "no"),
    })
    path = tmp_path / "dated.csv"
    frame.to_csv(path, index=False)
    return path, frame


def test_target_by_period_end_to_end_sealing_tokens_and_tenants(client, db_session, st, tmp_path,  # noqa: F811
                                                               monkeypatch):
    db = db_session
    human = DCLabClient(BASE, token=create_access_token(st.admin), workspace_id=st.alpha.id, http=client)
    path, frame = _dated_csv(tmp_path)
    dataset = human.datasets.upload(st.project.id, path)
    url = f"/v1/datasets/{dataset.id}/target-by-period"
    before = client.get(url, headers=_h(st.admin, st.alpha))
    assert before.status_code == 200, before.text
    assert set(before.json()) == {"dataset_id", "project_id", "status", "split_plan_id", "target_column",
                                  "time_column", "training_rate", "buckets", "truncated", "final_test"}
    assert (before.json()["status"], before.json()["buckets"], before.json()["final_test"]) == ("no_split_plan", [], None)

    spec = human.projects.create_problem_spec(st.project.id, task_type="binary_classification",
                                              business_objective="reduce churn", target_column="label",
                                              prediction_time_column="signup", status="locked")
    run = human.experiments.create(project_id=st.project.id, dataset_id=dataset.id, problem_spec_id=spec.id,
                                   target_column="label", intent="baseline")
    assert _work(db, run.id).status == "completed"

    body = human.datasets.target_by_period(dataset.id)
    plan = db.get(SplitPlan, body.split_plan_id)
    assignment = load_assignment(db, plan)
    train = frame.iloc[sorted(assignment.train_folds)]
    held = frame.iloc[sorted(assignment.holdout_rows)]
    assert body.status == "computed" and body.time_column == "signup"
    training = [b for b in body.buckets if b.part == "training"]
    assert sum(b.rows for b in training) == len(train)  # every training row, no final-test row
    expected = train.assign(m=train["signup"].str[:7], y=(train["label"] == "yes").astype(float)).groupby("m")["y"]
    assert {b.month: b.rate for b in training} == pytest.approx(expected.mean().to_dict())
    assert body.training_rate == pytest.approx(float((train["label"] == "yes").mean()))
    assert body.training_rate == pytest.approx(human.datasets.profile(dataset.id).target_rate)
    final = body.final_test
    # The run's model is the model in use and had its one final test: the rate is disclosed to people.
    assert (final.sealed, final.disclosed_after_final_test, final.row_count) == (False, True, len(held))
    assert final.rate == pytest.approx(float((held["label"] == "yes").mean()))
    if plan.holdout_strategy == "temporal_future":
        assert final.layout == "after_training" and final.start_month >= training[-1].month
        assert sum(b.rows for b in body.buckets if b.part == "final_test") == len(held)

    # A read-scoped token: the same training months, the final test always sealed.
    token = client.get(url, headers=_b(_token(db, st, scopes=("read",)))).json()
    assert [b for b in token["buckets"] if b["part"] == "training"] == [b.model_dump() for b in training]
    assert not [b for b in token["buckets"] if b["part"] == "final_test"]
    assert (token["final_test"]["sealed"], token["final_test"]["sealed_reason"], token["final_test"]["rate"]) == (
        True, "not_for_service_tokens", None)
    # No model in use (yet) on this plan: sealed for people too.
    real = profile_service.current_refs
    monkeypatch.setattr(profile_service, "current_refs",
                        lambda *a, **k: {key: ref for key, ref in real(*a, **k).items() if key != "champion_model"})
    sealed = client.get(url, headers=_h(st.admin, st.alpha)).json()["final_test"]
    assert (sealed["sealed"], sealed["sealed_reason"], sealed["rate"]) == (True, "final_test_not_run", None)
    # Another workspace: 404 for a person and a token.
    assert client.get(url, headers=_h(st.beta_admin, st.beta)).status_code == 404
    assert client.get(url, headers=_b(_token(db, st, user=st.beta_admin, workspace=st.beta))).status_code == 404

    # 14: the rules' test design (nobody asked) and the goal version's target, set by this person.
    rows = {row["what"]: row for row in client.get(f"/v1/projects/{st.project.id}/goal/decisions",
                                                   headers=_h(st.admin, st.alpha)).json()["items"]}
    assert (rows["test_design"]["suggested_by"], rows["test_design"]["confirmed_by"]) == ("rules", None)
    assert (rows["target"]["suggested_by"], rows["target"]["confirmed_by"], rows["target"]["value"]) == (
        "person", "you", "label")


# --- 12 + 13: upload log and upload checks ------------------------------------------------------


def test_upload_checks_flag_whole_file_issues():
    frame = pd.DataFrame({
        "id": [f"r{i}" for i in range(60)], "flat": ["x"] * 60,
        "when": ["2025-01-0%d" % (1 + i % 9) for i in range(59)] + ["not a date"],
        "spend": [None if i % 10 == 0 else float(i % 4) for i in range(60)],
    })
    frame = pd.concat([frame, frame.iloc[[3]]], ignore_index=True)
    out = run_upload_checks(frame, now=datetime(2026, 10, 16, tzinfo=UTC))
    checks = {c["code"]: c for c in out["checks"]}
    assert set(out) == {"scope", "ran_at", "row_count", "checks"} and out["row_count"] == 61
    assert (checks["duplicate_rows"]["status"], checks["duplicate_rows"]["count"]) == ("warn", 1)
    assert [c["name"] for c in checks["missing_values"]["columns"]] == ["spend"]
    assert [c["name"] for c in checks["date_parsing"]["columns"]] == ["when"]
    assert [c["name"] for c in checks["id_like_columns"]["columns"]] == ["id"]
    assert [c["name"] for c in checks["constant_columns"]["columns"]] == ["flat"]
    assert run_upload_checks(None) is None and run_upload_checks(pd.DataFrame()) is None
    assert sanitize_file_name("../dir\\big\x07  file.csv") == "big file.csv" and sanitize_file_name("") == "upload"
    clean = run_upload_checks(pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "x"]}))
    assert {c["status"] for c in clean["checks"]} == {"ok"}


def _post(client, headers, project_id, name, content, mime="text/csv"):
    return client.post("/v1/datasets", headers={**headers, "Idempotency-Key": f"d3a-{uuid4().hex}"},
                       data={"project_id": str(project_id)}, files={"file": (name, content, mime)})


def test_upload_log_both_paths_reads_and_retention(client, db_session, st, tmp_path, auth_client,  # noqa: F811
                                                   monkeypatch):
    db = db_session
    headers = _h(st.admin, st.alpha)
    path, _frame = _dated_csv(tmp_path, n=40)
    human = DCLabClient(BASE, token=create_access_token(st.admin), workspace_id=st.alpha.id, http=client)
    dataset = human.datasets.upload(st.project.id, path)
    checks = human.datasets.profile(dataset.id).upload_checks
    assert checks["scope"] == "whole_upload" and checks["row_count"] == 40
    assert _post(client, headers, st.project.id, "empty.csv", b"").status_code == 422
    assert _post(client, headers, st.project.id, "old.xls", b"abc", "application/vnd.ms-excel").status_code == 422
    monkeypatch.setattr(get_settings(), "v1_dataset_upload_max_bytes", 10)
    assert _post(client, headers, st.project.id, "big.csv", b"a,b\n1,2\n3,4\n").status_code == 413
    monkeypatch.undo()
    # A token's rejected upload; another workspace's project is never written to.
    writer = _token(db, st, scopes=("datasets:write", "read"))
    assert _post(client, _b(writer), st.project.id, "tool.csv", b"").status_code == 422
    assert _post(client, _b(writer), st.beta_project.id, "x.csv", b"").status_code == 404

    page = client.get(f"/v1/projects/{st.project.id}/upload-attempts", headers=headers)
    assert page.status_code == 200, page.text
    items = page.json()["items"]
    assert set(page.json()) == {"project_id", "retention_days", "items", "next_cursor"}
    assert set(items[0]) == {"id", "created_at", "file_name", "size_bytes", "outcome", "reason_code", "reason",
                             "uploaded_by", "dataset_id", "source"}
    got = [(i["file_name"], i["outcome"], i["reason_code"], i["uploaded_by"]) for i in items]
    assert got == [
        ("tool.csv", "rejected", "empty_file", {"kind": "connected_tool", "is_you": False}),
        ("big.csv", "rejected", "too_large", {"kind": "person", "is_you": True}),
        ("old.xls", "rejected", "unsupported_type", {"kind": "person", "is_you": True}),
        ("empty.csv", "rejected", "empty_file", {"kind": "person", "is_you": True}),
        ("dated.csv", "accepted", None, {"kind": "person", "is_you": True}),
    ]
    assert items[-1]["dataset_id"] == str(dataset.id) and items[1]["reason"] == "The file is larger than the upload limit."
    assert not db.scalars(select(UploadAttempt).where(UploadAttempt.workspace_id == st.beta.id)).all()
    # Tokens: no "you"; paging; another tenant 404.
    as_token = client.get(f"/v1/projects/{st.project.id}/upload-attempts?limit=2", headers=_b(_token(db, st))).json()
    assert len(as_token["items"]) == 2 and as_token["next_cursor"]
    assert not any(i["uploaded_by"]["is_you"] for i in as_token["items"])
    rest = human.projects.upload_attempts(st.project.id, cursor=as_token["next_cursor"], limit=10)
    assert [i.file_name for i in rest.items] == [g[0] for g in got[2:]]
    assert client.get(f"/v1/projects/{st.project.id}/upload-attempts", headers=_h(st.beta_admin, st.beta)).status_code == 404

    # The legacy Labs path logs too.
    assert auth_client.post("/app/labs/uploads", data={"category": "Revenue"},
                            files={"file": ("blank.csv", b"", "text/csv")}).status_code == 422
    assert db.scalar(select(UploadAttempt).where(UploadAttempt.file_name == "blank.csv")).source == "labs"

    # Append-only; the purge deletes only rows past 90 days, idempotently.
    one = db.scalar(select(UploadAttempt.id).where(UploadAttempt.workspace_id == st.alpha.id).limit(1))
    for sql in ("UPDATE upload_attempts SET file_name = 'x' WHERE id = :id", "DELETE FROM upload_attempts WHERE id = :id"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(sql), {"id": one})
        db.rollback()
    db.execute(text("ALTER TABLE upload_attempts DISABLE TRIGGER upload_attempts_created_at_now"))
    db.execute(text("INSERT INTO upload_attempts (id, workspace_id, project_id, actor_kind, actor_user_id, file_name, "
                    "outcome, reason_code, source, created_at) VALUES (:id, :ws, :p, 'person', :u, 'old.csv', "
                    "'rejected', 'empty_file', 'v1_api', now() - interval '91 days')"),
               {"id": uuid4(), "ws": st.alpha.id, "p": st.project.id, "u": st.admin.id})
    db.execute(text("ALTER TABLE upload_attempts ENABLE TRIGGER upload_attempts_created_at_now"))
    db.commit()
    count = len(db.scalars(select(UploadAttempt.id).where(UploadAttempt.workspace_id == st.alpha.id)).all())
    assert purge_expired_upload_attempts(db) == 1 and purge_expired_upload_attempts(db) == 0
    left = db.scalars(select(UploadAttempt.file_name).where(UploadAttempt.workspace_id == st.alpha.id)).all()
    assert len(left) == count - 1 and "old.csv" not in left


# --- 14: who decided ----------------------------------------------------------------------------


def test_goal_decisions_record_confirmations_and_refuse_corrections(client, db_session, st):  # noqa: F811
    db = db_session
    human = DCLabClient(BASE, token=create_access_token(st.admin), workspace_id=st.alpha.id, http=client)
    url = f"/v1/projects/{st.project.id}/goal/decisions"
    empty = client.get(url, headers=_h(st.admin, st.alpha)).json()
    assert [r["what"] for r in empty["items"]] == ["target", "test_design", "metric", "rule"]
    assert all(set(r) == ROW_KEYS and r["confirmed_by"] is None for r in empty["items"])

    draft = dict(task_type="binary_classification", business_objective="churn", target_column="label")
    human.projects.create_problem_spec(st.project.id, **draft, primary_metric="pr_auc", status="draft")
    assert not db.scalars(select(ProjectDecisionRecord).where(  # a draft confirms nothing
        ProjectDecisionRecord.project_id == st.project.id)).all()
    human.projects.create_problem_spec(st.project.id, **draft, primary_metric="roc_auc",
                                       success_criteria={"note": "flag at most 20%"}, status="locked")
    request = SimpleNamespace(id=uuid4(), workspace_id=st.alpha.id, project_id=st.project.id)
    record_test_design_confirmed(db, request=request, user=st.admin, answer="keep_rule_split",
                                 rationale="Months must stay in order.")
    db.commit()
    rows = {r["what"]: r for r in client.get(url, headers=_h(st.admin, st.alpha)).json()["items"]}
    assert (rows["metric"]["suggested_by"], rows["metric"]["confirmed_by"], rows["metric"]["value"]) == (
        "person", "you", "roc_auc")
    assert rows["rule"]["value"] is None and rows["rule"]["why_is_default"] is True
    assert (rows["test_design"]["suggested_by"], rows["test_design"]["why"], rows["test_design"]["why_is_default"]) == (
        "rules", "Months must stay in order.", False)
    # Another person reads "person"; a token never reads "you" and gets the same rows otherwise.
    other = {r["what"]: r for r in client.get(url, headers=_h(st.engineer, st.alpha)).json()["items"]}
    assert other["metric"]["confirmed_by"] == "person"
    tok = client.get(url, headers=_b(_token(db, st))).json()["items"]
    assert {r["confirmed_by"] for r in tok} <= {"person", None}
    assert client.get(url, headers=_h(st.beta_admin, st.beta)).status_code == 404

    # A connected tool's goal version (even locked) confirms nothing: the person's entry stays.
    writer = _token(db, st, scopes=("projects:write", "read"))
    made = client.post(f"/v1/projects/{st.project.id}/problem-specs",
                       headers=_b(writer, **{"Idempotency-Key": f"d3a-{uuid4().hex}"}),
                       json={"task_type": "binary_classification", "business_objective": "churn",
                             "primary_metric": "pr_auc", "status": "locked"})
    assert made.status_code == 201, made.text
    metric = client.get(url, headers=_h(st.admin, st.alpha)).json()["items"][2]
    assert (metric["suggested_by"], metric["confirmed_by"], metric["value"], metric["why_is_default"]) == (
        "person", "you", "roc_auc", True)

    # Reserved types: never recorded or corrected through the generic path.
    entry = db.get(ProjectDecisionRecord, rows["metric"]["decision_id"])
    with pytest.raises((DecisionActorNotPermittedError, InvalidDecisionRecordError)):
        drs.supersede(db, workspace_id=st.alpha.id, project_id=st.project.id, record_id=entry.id,
                      actor=DecisionActor.human(st.admin), rationale="change it")
    with pytest.raises((DecisionActorNotPermittedError, InvalidDecisionRecordError)):
        drs.record(db, workspace_id=st.alpha.id, project_id=st.project.id, actor=DecisionActor.human(st.admin),
                   decision_type="target_confirmed", subject_kind="project", rationale="x", state="accepted")
    db.rollback()
    read = goal_decisions(db, actor=st.admin, workspace_id=st.alpha.id, project_id=st.project.id, viewer_id=None)
    assert {r.confirmed_by for r in read.items} <= {"person", "connected_tool", None}


def test_target_confirmation_writes_the_entry_with_the_persons_reason(auth_client, engineer_client, db_session,
                                                                       monkeypatch):
    _run_id, body = _wait_for_target(auth_client, db_session, monkeypatch)
    request_id = body["target_confirmation"]["execution_request_id"]
    refused = engineer_client.post(f"/v1/execution-requests/{request_id}/target-confirmation",
                                   json={"target_column": "Churn", "rationale": "key dclab_st_" + "a" * 32 + "_" + "b" * 43})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_rationale"
    confirmed = engineer_client.post(f"/v1/execution-requests/{request_id}/target-confirmation",
                                     json={"target_column": "Churn", "rationale": "Churn is what we act on."})
    assert confirmed.status_code == 200, confirmed.text
    project_id = confirmed.json()["project_id"]
    rows = {r["what"]: r for r in engineer_client.get(f"/v1/projects/{project_id}/goal/decisions").json()["items"]}
    assert (rows["target"]["suggested_by"], rows["target"]["confirmed_by"], rows["target"]["value"],
            rows["target"]["why"], rows["target"]["why_is_default"]) == (
        "rules", "you", "Churn", "Churn is what we act on.", False)
    # A replay of the same confirmation writes nothing new.
    again = engineer_client.post(f"/v1/execution-requests/{request_id}/target-confirmation",
                                 json={"target_column": "Churn"})
    assert again.status_code == 200
    entries = db_session.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.decision_type == "target_confirmed")).all()
    assert len(entries) == 1
    # The legacy Labs upload that started this run is in the upload log as accepted.
    assert db_session.scalar(select(UploadAttempt).where(UploadAttempt.file_name == "tie.csv")).outcome == "accepted"
