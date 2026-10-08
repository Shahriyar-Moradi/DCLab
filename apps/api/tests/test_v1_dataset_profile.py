"""P4.1-C: ``GET /v1/datasets/{id}/profile`` and the dataset policy read.

Statistics come from the training rows of the current SplitPlan only: a per-row marker
column proves no holdout row is counted (its unique count equals the plan's training
rows, not the file's rows) and a column whose missing cells are spread over every row
proves the missing count is the training-row count. Without a SplitPlan the profile is
names and types only. Cross-workspace reads are 404; read-scoped tokens, the SDK and the
catalog tool get the same holdout-free shape.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from app.agents.tools.catalog import ToolContext, catalog
from app.agents.tools.render import to_mcp
from app.db.models import SplitPlan
from app.services.auth_service import create_access_token
from app.engine.validation.split_assignment import SplitAssignmentMismatchError
from app.services import dataset_profile_service as profile_service
from app.services.dataset_profile_service import training_statistics
from app.services.split_plan_service import load_assignment
from dclab_client import DCLabClient
from test_v1_resources_experiments import _work
from test_v1_service_tokens import _b, _token, st  # noqa: F401  (fixture)

BASE = "http://testserver"
HOLDOUT = re.compile(r"holdout|final_test", re.IGNORECASE)


def test_training_statistics_never_count_holdout_rows():
    frame = pd.DataFrame({
        "plan": ["a", "b", "a", "b", "a", "b", "a", "ONLY_IN_HOLDOUT", "ONLY_IN_HOLDOUT", "c"],
        "spend": [1.0, 2.0, None, 4.0, 5.0, 6.0, 7.0, None, None, None],
        "label": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
    })
    stats = training_statistics(frame, set(range(7)), "label")  # rows 7-9 are holdout
    assert stats["plan"]["unique_count"] == 2 and stats["plan"]["missing_count"] == 0
    assert stats["spend"]["missing_count"] == 1 and stats["spend"]["missing_fraction"] == pytest.approx(1 / 7)
    assert stats["label"]["rule_role"] == "target" and stats["spend"]["rule_role"] == "numerical"
    assert stats["plan"]["rule_role"] == "categorical"
    with pytest.raises(ValueError):
        training_statistics(frame, {0, 1, 99}, "label")  # a map row the file does not have: fail closed
    with pytest.raises(ValueError):
        training_statistics(frame, set(range(7)), "label", frozenset({7, 8, 99}))  # holdout rows checked too
    # Holdout cells never move a statistic or a rule role, even the numeric-coercion decision.
    changed = frame.copy()
    changed.loc[7:9, "plan"] = ["x", "y", "z"]
    changed["spend"] = changed["spend"].astype(object)
    changed.loc[7:9, "spend"] = ["n/a", "text", "more text"]
    assert training_statistics(changed, set(range(7)), "label") == stats
    # A column the train-only missing-value plan drops is ignored by the rule, not numerical.
    sparse = frame.assign(sparse=[1.0, None, None, None, None, None, 2.0, 3.0, 4.0, 5.0])
    assert training_statistics(sparse, set(range(7)), "label")["sparse"]["rule_role"] == "ignored_free_text"


def _csv(tmp_path, n=240):
    rng = np.random.default_rng(7)
    tenure = rng.integers(1, 72, n).astype(float)
    label = rng.binomial(1, 1 / (1 + np.exp(0.6 + 0.02 * tenure)))
    frame = pd.DataFrame({
        "tenure": tenure, "plan": rng.choice(["basic", "pro"], n),
        "gap": [None if i % 3 == 0 else float(i) for i in range(n)],  # missing cells in every partition
        "marker": [f"m{i:04d}" for i in range(n)],  # one value per row: unique count = rows counted
        "label": np.where(label == 1, "yes", "no"),
    })
    # Rows the run drops before the split (exact duplicates, blank labels) shift nothing:
    # the map's source rows are positions in the loaded file.
    frame = pd.concat([frame, frame.iloc[[3, 5]]], ignore_index=True)
    frame.loc[[10, 20], "label"] = None
    path = tmp_path / "rows.csv"
    frame.to_csv(path, index=False)
    return path, frame


def test_profile_training_rows_only_tenant_isolation_tokens_sdk_and_catalog(client, db_session, st, tmp_path,  # noqa: F811
                                                                             monkeypatch):
    db = db_session
    human = DCLabClient(BASE, token=create_access_token(st.admin), workspace_id=st.alpha.id, http=client)
    path, frame = _csv(tmp_path)
    dataset = human.datasets.upload(st.project.id, path)
    headers = {"Authorization": f"Bearer {create_access_token(st.admin)}", "X-Workspace-Id": str(st.alpha.id)}

    # Before any run: no SplitPlan, so names and types only (no whole-file statistics).
    before = client.get(f"/v1/datasets/{dataset.id}/profile", headers=headers)
    assert before.status_code == 200, before.text
    body = before.json()
    assert (body["scope"], body["statistics_status"], body["split_plan"], body["experiment"]) == (
        "upload", "no_split_plan", None, None)
    assert [c["name"] for c in body["columns"]] == list(frame.columns)
    assert all(c["missing_count"] is None and c["unique_count"] is None and c["rule_role"] is None
               for c in body["columns"])

    # Policy on the dataset read (ADR 0005 defaults: exposure deny, so nothing reaches an AI call).
    policy = client.get(f"/v1/datasets/{dataset.id}", headers=headers).json()["policy"]
    assert policy["upload_policy"] == "internal_training" and policy["publication_state"] == "published"
    assert (policy["llm_exposure_policy"], policy["ai_data_class"]) == ("deny", "none")

    spec = human.projects.create_problem_spec(st.project.id, task_type="binary_classification",
                                              business_objective="reduce churn", target_column="label", status="locked")
    run = human.experiments.create(project_id=st.project.id, dataset_id=dataset.id, problem_spec_id=spec.id,
                                   target_column="label", intent="baseline")
    assert _work(db, run.id).status == "completed"

    profile = human.datasets.profile(dataset.id)
    plan = db.get(SplitPlan, profile.split_plan.id)
    train_rows = sorted(load_assignment(db, plan).train_folds)
    assert plan.holdout_row_count > 0 and len(train_rows) == plan.train_row_count < len(frame)
    assert (profile.scope, profile.statistics_status, profile.split_plan.training_row_count) == (
        "training_rows", "computed", len(train_rows))
    assert profile.split_plan.source == "project_ref" and profile.experiment.id == run.id
    by_name = {c.name: c for c in profile.columns}
    train = frame.iloc[train_rows]
    assert by_name["marker"].unique_count == train["marker"].nunique() == len(train_rows)  # holdout never counted
    assert not {10, 20, len(frame) - 1, len(frame) - 2} & set(train_rows)  # blank labels and duplicates dropped
    assert by_name["gap"].missing_count == int(train["gap"].isna().sum()) != int(frame["gap"].isna().sum())
    assert by_name["plan"].unique_count == train["plan"].nunique()
    assert by_name["label"].rule_role == by_name["label"].role_used == "target"
    assert by_name["tenure"].role_used == "numerical" and by_name["tenure"].transforms
    assert by_name["marker"].leakage_excluded or by_name["marker"].role_used in {"identifier", "ignored_free_text"}

    # Tenant isolation: another workspace (human or token) gets 404 for both reads.
    beta = {"Authorization": f"Bearer {create_access_token(st.beta_admin)}", "X-Workspace-Id": str(st.beta.id)}
    for suffix in ("", "/profile"):
        assert client.get(f"/v1/datasets/{dataset.id}{suffix}", headers=beta).status_code == 404
        assert client.get(f"/v1/datasets/{dataset.id}{suffix}",
                          headers=_b(_token(db, st, user=st.beta_admin, workspace=st.beta))).status_code == 404

    # A read-scoped token sees the same holdout-free body as the human.
    token = _token(db, st, scopes=("read",))
    as_token = client.get(f"/v1/datasets/{dataset.id}/profile", headers=_b(token))
    assert as_token.status_code == 200 and as_token.json() == client.get(
        f"/v1/datasets/{dataset.id}/profile", headers=headers).json()

    # The catalog tool (MCP shape): the same profile, column names as untrusted text, no holdout keys.
    ctx = ToolContext(db=db, actor=st.admin, workspace_id=st.alpha.id)
    out = to_mcp(catalog()["inspect_dataset"].read(ctx, {"dataset_id": str(dataset.id)}))[0]
    assert out["profile"]["scope"] == "training_rows" and out["policy"]["ai_data_class"] == "none"
    marker = next(c for c in out["profile"]["columns"] if c["name"] == {"untrusted_text": "marker"})
    assert marker["unique_count"] == len(train_rows)
    assert not HOLDOUT.search(json.dumps(out))

    # A row map that fails verification: statistics withheld, never a whole-file fallback.
    def tampered(*_args, **_kwargs):
        raise SplitAssignmentMismatchError("stored assignment bytes do not match their digest")

    profile_service._cache.clear()
    monkeypatch.setattr(profile_service, "load_assignment", tampered)
    withheld = client.get(f"/v1/datasets/{dataset.id}/profile", headers=headers).json()
    assert (withheld["scope"], withheld["statistics_status"]) == ("upload", "unavailable")
    assert all(c["missing_count"] is None and c["rule_role"] is None for c in withheld["columns"])
