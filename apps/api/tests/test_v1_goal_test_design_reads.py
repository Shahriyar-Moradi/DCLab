"""V7 BE-C: goal versions, the test design and who uploaded a file, as /v1 reads.

``GET /v1/projects/{id}/problem-specs[/{spec_id}]`` and ``GET /v1/split-plans/{id}`` carry no user or
token ids (``creator``: kind + ``is_you``); the split plan shows the final test set as a row count only
(no dates, rates or statistics) and withholds the rule's reason text from service tokens. Dataset
reads show ``uploader`` (person / you / connected tool). Exact key sets, tenant isolation, tokens.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.db.models import Experiment, SplitPlan
from app.services import split_plan_service
from app.services.auth_service import create_access_token
from app.services.split_plan_service import load_assignment
from app.engine.modeling.validation_planner import TIME_SERIES_SPLIT
from app.engine.validation.split_assignment import SplitAssignment, SplitAssignmentMismatchError
from app.services.problem_spec_service import strip_holdout_keys
from dclab_client import DCLabClient
from test_v1_dataset_profile import _csv
from test_v1_resources_experiments import _work
from test_v1_service_tokens import _b, _id, _token, st  # noqa: F401  (fixture)

BASE = "http://testserver"
HOLDOUT = re.compile(r"holdout|final_test", re.IGNORECASE)
CREATOR_KEYS = {"kind", "is_you"}
SPEC_KEYS = {
    "id", "workspace_id", "project_id", "version", "task_type", "target_column", "prediction_unit",
    "prediction_time_column", "prediction_horizon", "primary_metric", "business_objective", "constraints",
    "success_criteria", "status", "content_digest", "is_current", "creator", "created_at", "locked_at",
}
PLAN_KEYS = {
    "id", "workspace_id", "project_id", "dataset_id", "version", "task_type", "target_column",
    "evaluation_strategy", "validation_strategy", "validation_folds", "stratified", "time_column", "group_column",
    "row_count", "train_row_count", "evaluation_row_count", "has_evaluation_set", "folds_status", "folds",
    "warmup_row_count", "reason", "creator", "created_at", "locked_at",
}
FOLD_KEYS = {"fold_number", "train_row_count", "validation_row_count"}


def test_strip_holdout_keys_and_time_ordered_fold_counts():
    raw = {"holdout_auc_min": 0.8, "Final_Test_Window": "Q3", "keep": [{"HOLDOUT": 1, "ok": 2}],
           "nested": {"final_test_rows": 5, "fine": {"x": 1}}}
    assert strip_holdout_keys(raw) == {"keep": [{"ok": 2}], "nested": {"fine": {"x": 1}}}
    # Expanding window: fold k learns on the warm-up rows and every earlier fold, checks on fold k.
    plan = SimpleNamespace(validation_strategy=TIME_SERIES_SPLIT, validation_folds=3)
    assignment = SplitAssignment(train_folds={0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3},
                                 holdout_rows=frozenset({8, 9}))
    folds, warmup = split_plan_service.fold_row_counts(plan, assignment)
    assert [(f.fold_number, f.train_row_count, f.validation_row_count) for f in folds] == [
        (1, 2, 2), (2, 4, 2), (3, 6, 2)]
    assert warmup == 2
    other = SimpleNamespace(validation_strategy="StratifiedKFold", validation_folds=3)
    shuffled = SplitAssignment(train_folds={0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3}, holdout_rows=frozenset({6}))
    folds, warmup = split_plan_service.fold_row_counts(other, shuffled)
    assert [(f.train_row_count, f.validation_row_count) for f in folds] == [(4, 2)] * 3 and warmup is None


def test_split_plan_creator_without_a_run_is_unknown(db_session, st):  # noqa: F811
    plan = SimpleNamespace(id=uuid4(), workspace_id=st.alpha.id, created_by=st.admin.id)
    assert split_plan_service._plan_creator(db_session, plan, st.admin.id).model_dump() == {
        "kind": "unknown", "is_you": False}


def _h(user, workspace) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}", "X-Workspace-Id": str(workspace.id)}


def test_uploader_problem_specs_and_split_plan_reads(client, db_session, st, tmp_path, monkeypatch):  # noqa: F811
    db = db_session
    admin, engineer = _h(st.admin, st.alpha), _h(st.engineer, st.alpha)
    human = DCLabClient(BASE, token=create_access_token(st.admin), workspace_id=st.alpha.id, http=client)
    path, frame = _csv(tmp_path)
    dataset = human.datasets.upload(st.project.id, path)
    reader = _token(db, st, scopes=("read",))

    # B2: who uploaded it, for display; "you" only for the signed-in uploader, never for a token.
    def uploader(headers, dataset_id):
        listed = {row["id"]: row for row in client.get("/v1/datasets", headers=headers).json()}[str(dataset_id)]
        one = client.get(f"/v1/datasets/{dataset_id}", headers=headers).json()
        assert set(listed["uploader"]) == CREATOR_KEYS and listed["uploader"] == one["uploader"]
        return listed["uploader"]

    assert uploader(admin, dataset.id) == {"kind": "person", "is_you": True}
    assert uploader(engineer, dataset.id) == {"kind": "person", "is_you": False}
    assert uploader(_b(reader), dataset.id) == {"kind": "person", "is_you": False}
    writer = _token(db, st, scopes=("read", "datasets:write"))
    tool = client.post("/v1/datasets", headers=_b(writer, **{"Idempotency-Key": "be-c-upload-1"}),
                       data={"project_id": str(st.project.id)},
                       files={"file": ("rows.csv", path.read_bytes(), "text/csv")})
    assert tool.status_code == 201, tool.text
    assert uploader(admin, tool.json()["id"]) == {"kind": "connected_tool", "is_you": False}
    assert str(_id(db, writer)) not in client.get("/v1/datasets", headers=admin).text  # no token id
    # No recorded uploader (a removed user or an old row): `unknown`, never a guessed person.
    from app.db.models import Dataset
    row = db.get(Dataset, dataset.id)
    row.asset.created_by = None
    db.commit()
    assert uploader(admin, dataset.id) == {"kind": "unknown", "is_you": False}

    # B6: goal versions, newest first, no user ids.
    specs_url = f"/v1/projects/{st.project.id}/problem-specs"
    assert client.get(specs_url, headers=admin).json() == []
    constraints = {"metric_constraints": [{"metric": "recall", "op": ">=", "value": 0.5}], "holdout_auc_min": 0.8}
    criteria = {"notes": {"final_test_window": "Q3", "keep": 1}}
    draft = human.projects.create_problem_spec(
        st.project.id, task_type="binary_classification", business_objective="reduce churn, ask ops@corp.example",
        target_column="label", prediction_unit="customer", prediction_horizon="30 days", primary_metric="pr_auc",
        constraints=constraints, success_criteria=criteria)
    locked = human.projects.create_problem_spec(st.project.id, task_type="binary_classification",
                                                business_objective="reduce churn", target_column="label",
                                                status="locked")
    run = human.experiments.create(project_id=st.project.id, dataset_id=dataset.id, problem_spec_id=locked.id,
                                   target_column="label", intent="baseline")
    assert _work(db, run.id).status == "completed"

    listed = client.get(specs_url, headers=admin)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["id"] for row in rows] == [str(locked.id), str(draft.id)]
    assert all(set(row) == SPEC_KEYS and set(row["creator"]) == CREATOR_KEYS for row in rows)
    newest, oldest = rows
    assert (newest["status"], newest["version"], oldest["status"], oldest["version"]) == ("locked", 2, "draft", 1)
    assert (oldest["task_type"], oldest["target_column"], oldest["prediction_unit"], oldest["prediction_horizon"],
            oldest["primary_metric"], oldest["business_objective"]) == (
        "binary_classification", "label", "customer", "30 days", "pr_auc", "reduce churn, ask ops@corp.example")
    assert (oldest["constraints"], oldest["success_criteria"]) == (constraints, criteria)  # people: full text
    assert newest["creator"] == {"kind": "person", "is_you": True}
    ref = client.get(f"/v1/projects/{st.project.id}/refs/problem_spec", headers=admin)
    current = {row["id"] for row in rows if row["is_current"]}
    assert current == ({ref.json()["target"]["id"]} if ref.status_code == 200 else set())
    one = client.get(f"{specs_url}/{draft.id}", headers=admin).json()
    assert one == oldest and human.projects.problem_spec(st.project.id, draft.id).version == 1
    assert [s.version for s in human.projects.problem_specs(st.project.id, limit=1)] == [2]
    assert [row["creator"]["is_you"] for row in client.get(specs_url, headers=engineer).json()] == [False, False]
    as_token = client.get(specs_url, headers=_b(reader))
    assert as_token.status_code == 200 and str(st.admin.id) not in as_token.text
    assert [row["creator"] for row in as_token.json()] == [{"kind": "person", "is_you": False}] * 2
    token_draft = as_token.json()[1]
    assert token_draft["constraints"] == {"metric_constraints": constraints["metric_constraints"]}
    assert token_draft["success_criteria"] == {"notes": {"keep": 1}}
    # The public untrusted-text projection fails closed: a text naming an email is redacted whole.
    assert token_draft["business_objective"] == "[REDACTED]"
    assert as_token.json()[0]["business_objective"] == "reduce churn"  # plain text passes unchanged
    assert not HOLDOUT.search(as_token.text)
    assert client.get(f"{specs_url}/{draft.id}", headers=_b(reader)).json() == token_draft
    # A spec a connected tool wrote is shown as one (no token id).
    projects_writer = _token(db, st, scopes=("read", "projects:write"))
    made = client.post(specs_url, json={"task_type": "binary_classification", "business_objective": "x"},
                       headers=_b(projects_writer, **{"Idempotency-Key": "be-c-spec-1"}))
    assert made.status_code == 201, made.text
    by_tool = client.get(f"{specs_url}/{made.json()['id']}", headers=admin).json()
    assert by_tool["creator"] == {"kind": "connected_tool", "is_you": False}
    assert str(_id(db, projects_writer)) not in json.dumps(by_tool)

    # B7: the test design; the final test set is a row count only.
    plan_id = db.scalar(select(Experiment.split_plan_id).where(Experiment.id == run.id))
    plan = db.get(SplitPlan, plan_id)
    response = client.get(f"/v1/split-plans/{plan_id}", headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == PLAN_KEYS and set(body["creator"]) == CREATOR_KEYS
    assert all(set(fold) == FOLD_KEYS for fold in body["folds"])
    assert (body["row_count"], body["train_row_count"], body["evaluation_row_count"], body["has_evaluation_set"]) == (
        plan.row_count, plan.train_row_count, plan.holdout_row_count, True)
    assert (body["evaluation_strategy"], body["validation_strategy"], body["validation_folds"]) == (
        plan.holdout_strategy, plan.validation_strategy, plan.validation_folds)
    assert body["reason"] == plan.reason and body["creator"] == {"kind": "person", "is_you": True}
    assignment = load_assignment(db, plan)
    expected = {
        number: sum(1 for fold in assignment.train_folds.values() if fold == number)
        for number in range(1, plan.validation_folds + 1)
    }
    assert body["folds_status"] == "computed" and len(body["folds"]) == plan.validation_folds
    assert {fold["fold_number"]: fold["validation_row_count"] for fold in body["folds"]} == expected
    if plan.validation_strategy == TIME_SERIES_SPLIT:
        assert body["warmup_row_count"] == plan.train_row_count - sum(expected.values())
    else:  # all-but-k: each fold learns on every other training row; no warm-up rows
        assert all(fold["train_row_count"] == plan.train_row_count - fold["validation_row_count"]
                   for fold in body["folds"])
        assert body["warmup_row_count"] is None
    assert client.get(f"/v1/split-plans/{plan_id}", headers=engineer).json()["creator"]["is_you"] is False
    token_plan = client.get(f"/v1/split-plans/{plan_id}", headers=_b(reader))
    assert token_plan.status_code == 200 and set(token_plan.json()) == PLAN_KEYS
    assert token_plan.json()["reason"] is None and not HOLDOUT.search(token_plan.text)
    assert {k: v for k, v in token_plan.json().items() if k not in ("reason", "creator")} == {
        k: v for k, v in body.items() if k not in ("reason", "creator")}
    sdk_plan = human.nodes.split_plan(plan_id)
    assert sdk_plan.evaluation_row_count == plan.holdout_row_count and sdk_plan.creator.is_you

    # A plan a connected tool's run created is shown as one (the plan's created_by is the token's creator).
    runner = _token(db, st, scopes=("read", "experiments:write"))
    started = client.post("/v1/experiments", headers=_b(runner, **{"Idempotency-Key": "be-c-run-1"}),
                          json={"project_id": str(st.project.id), "dataset_id": tool.json()["id"],
                                "target_column": "label"})
    assert started.status_code == 202, started.text
    assert _work(db, started.json()["id"]).status == "completed"
    tool_plan = db.scalar(select(Experiment.split_plan_id).where(Experiment.id == started.json()["id"]))
    assert tool_plan is not None and tool_plan != plan_id
    assert client.get(f"/v1/split-plans/{tool_plan}", headers=admin).json()["creator"] == {
        "kind": "connected_tool", "is_you": False}

    # Tenant isolation: another workspace (person or token) gets 404 for every read.
    beta, beta_token = _h(st.beta_admin, st.beta), _b(_token(db, st, user=st.beta_admin, workspace=st.beta))
    for url in (specs_url, f"{specs_url}/{draft.id}", f"/v1/split-plans/{plan_id}"):
        assert client.get(url, headers=beta).status_code == 404
        assert client.get(url, headers=beta_token).status_code == 404

    # A row map that fails verification: no fold counts (never guessed), the rest still reads.
    def tampered(*_args, **_kwargs):
        raise SplitAssignmentMismatchError("stored assignment bytes do not match their digest")

    split_plan_service._fold_cache.clear()
    monkeypatch.setattr(split_plan_service, "load_assignment", tampered)
    withheld = client.get(f"/v1/split-plans/{plan_id}", headers=admin).json()
    assert (withheld["folds_status"], withheld["folds"], withheld["warmup_row_count"]) == ("unavailable", [], None)
    assert withheld["evaluation_row_count"] == plan.holdout_row_count
