"""P6.9-A step A2: the run ``plan`` input, AI-before points, supersede rules, Jev L1 proposals
(0075) and inherited values on branches (ADR 0008 §1b, §2, §2c, §7, § Consequences).

Fake providers only. Root runs go through ``POST /v1/experiments`` and the durable worker.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.service import GatewayService
from app.agents.governance.decision_points import answer_ceiling
from app.agents.governance.seed import seed_platform_governance
from app.agents.semantic.releases import sync_jev_releases
from app.agents.semantic.typesafe_jev import JevSemanticPort
from app.db.models import (
    AgentProposal,
    ClientLabUpload,
    DatasetColumn,
    ExecutionRequest,
    Experiment,
    MlRunEvent,
    ProjectDecisionRecord,
)
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.services.auto_train import decision_points as dp
from app.services.auto_train.plan_points import _missing_reasons, split_reasons
from app.services.dataset_column_service import publish_dataset_policy_defaults, set_dataset_column_policy
from app.services.problem_spec_service import create_problem_spec
from app.services.run_plan_service import plan_for_request, supersede_plans
from test_agent_persistence import agent_run
from test_auto_train_decision_points import ON, jev
from test_v1_resources_experiments import _h, _key, _work, setup  # noqa: F401 - setup is a fixture


def _csv(n: int = 240, seed: int = 7, *, gaps: bool = True) -> bytes:
    """``visits`` is a low-cardinality integer code with gaps (numeric by the rule)."""

    rng = np.random.default_rng(seed)
    plan = rng.choice(["basic", "plus", "pro"], n)
    tenure = rng.integers(1, 72, n).astype(float)
    visits = rng.integers(0, 4, n).astype(float)
    logit = -0.6 + 0.9 * (plan == "basic") - 0.02 * tenure + 0.3 * visits
    label = rng.binomial(1, 1 / (1 + np.exp(-logit)))
    if gaps:
        visits[::13] = np.nan
    return pd.DataFrame({"tenure": tenure, "spend": rng.uniform(20, 120, n), "plan": plan, "visits": visits,
                         "label": np.where(label == 1, "yes", "no")}).to_csv(index=False).encode()


@pytest.fixture
def ai(db_session, setup, monkeypatch):  # noqa: F811
    """AI on with a fake Jev that abstains everywhere (unless a test swaps the answers)."""

    seed_platform_governance(db_session, environment="test")
    sync_jev_releases(db_session)
    db_session.commit()
    ns = SimpleNamespace(db=db_session, setup=setup, ws=setup["alpha"].id, project=setup["alpha_project"].id,
                         answers={dp.ROLE: {"*": ("numeric", 0.5)}, dp.IDENTIFIER: {"*": (0.5, None)},
                                  dp.LEAKAGE: {"*": (0.5, None)}})  # abstains
    ns.fake = jev(ns.answers)
    service = GatewayService(providers={"typesafe": ns.fake}, limits=GatewayLimits(), settings=lambda: ON)
    monkeypatch.setattr(dp, "semantic_port", lambda: JevSemanticPort(service, settings=ON))
    ns.level = {"value": 0}
    monkeypatch.setattr("app.agents.governance.snapshot.effective_level",
                        lambda db, ws, key, kind=None, prompt_release_id=None, model_id=None:
                        min(ns.level["value"], answer_ceiling(key, kind)))
    ns.run_id = agent_run(db_session, SimpleNamespace(ws_a=ns.ws, project_a=ns.project), agent_key="experiment_planner",
                          purpose="training.families_budget")
    db_session.commit()
    return ns


def _plan(ns, payload: dict, *, status: str = "accepted", project=None, proposal_type="ExperimentPlanProposal",
          **subject) -> AgentProposal:
    level = 2 if status == "applied" else 1
    row = AgentProposal(
        workspace_id=ns.ws, project_id=project or ns.project, run_id=ns.run_id,
        decision_point_key="training.families_budget", level_at_proposal=level, answer_ceiling=level,
        proposal_type=proposal_type, schema_version=1, payload=payload,
        payload_digest=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        citations=[], validator_verdict="accepted", validator_reasons=[],
        status="applied" if status == "applied" else "proposed",
        subject_kind=subject.pop("subject_kind", "project"), **subject)
    ns.db.add(row)
    ns.db.commit()
    if status == "accepted":
        ns.db.execute(text("UPDATE agent_proposals SET status = 'accepted', decided_by_user_id = :u, "
                           "decided_at = now() WHERE id = :id"), {"u": ns.setup["alpha_admin"].id, "id": row.id})
        ns.db.commit()
        ns.db.refresh(row)
    return row


def _dataset(client, ns, *, labelled: bool = False, gaps: bool = True) -> str:
    response = client.post("/v1/datasets", headers=_h(ns.setup, key=_key()), data={"project_id": str(ns.project)},
                           files={"file": ("rows.csv", _csv(gaps=gaps), "text/csv")})
    assert response.status_code == 201, response.text
    dataset_id = response.json()["id"]
    if labelled:
        db = ns.db
        revision = db.execute(text("SELECT count(*) FROM dataset_policy_revisions WHERE dataset_id = :d"),
                              {"d": dataset_id}).scalar()
        publish_dataset_policy_defaults(
            db, actor=ns.setup["alpha_admin"], workspace_id=ns.ws, dataset_id=dataset_id, expected_revision=revision,
            sensitivity_class="internal", llm_exposure_policy="allow", retention_class="standard",
            residency_class="home_cloud_only", classification_source="manual", classification_confidence=1.0)
        for column in db.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id)):
            set_dataset_column_policy(db, workspace_id=ns.ws, column_id=column.id, sensitivity_class="internal",
                                      classification_source="manual", model_use_policy="allow",
                                      llm_exposure_policy="allow", retention_class="standard",
                                      residency_class="home_cloud_only", classification_confidence=1.0)
        db.commit()
    return dataset_id


def _root(client, ns, dataset_id: str, **body) -> Experiment:
    response = client.post("/v1/experiments", json={"project_id": str(ns.project), "dataset_id": dataset_id,
                                                     "target_column": "label", **body},
                           headers=_h(ns.setup, key=_key()))
    assert response.status_code == 202, response.text
    return ns.db.get(Experiment, response.json()["id"])


def _request(db, experiment) -> ExecutionRequest:
    db.expire_all()
    return db.scalar(select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == experiment.id))


def _records(db, experiment_id) -> dict[str, ProjectDecisionRecord]:
    db.expire_all()
    return {row.details["decision_point"]: row for row in db.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.experiment_id == experiment_id,
        ProjectDecisionRecord.decision_type == "decision_point_resolved"))}


def _column(record, name):
    return next(c for c in record.details["columns"] if c["column"] == name)


def _upload(db, experiment_id) -> ClientLabUpload:
    db.expire_all()
    return db.scalar(select(ClientLabUpload).where(ClientLabUpload.experiment_id == experiment_id))


# --- request time ------------------------------------------------------------------------------


def test_plan_input_is_checked_bound_once_and_refusals_are_recorded(client, ai):
    plan = _plan(ai, {"families": ["random_forest"]})
    proposed = _plan(ai, {"families": ["random_forest"]}, status="proposed")
    ai.db.execute(text("UPDATE agent_proposals SET status = 'rejected', decided_by_user_id = :u, decided_at = now() "
                       "WHERE id = :id"), {"u": ai.setup["alpha_admin"].id, "id": proposed.id})
    applied_l1 = _plan(ai, {"split": {"strategy": "random"}}, status="applied")
    invalid = _plan(ai, {"families": []})
    ai.db.commit()
    check = lambda plan_id, project=ai.project: plan_for_request(  # noqa: E731
        ai.db, workspace_id=ai.ws, project_id=project, plan_id=plan_id)
    assert check(plan.id) == (plan.id, None)
    assert check(proposed.id)[1]["code"] == "plan_not_accepted"
    assert check(applied_l1.id)[1]["code"] == "applied_plan_has_l1_answers"
    assert check(invalid.id)[1]["code"] == "plan_invalid"
    assert check(uuid4())[1]["code"] == "plan_not_found"
    assert check(plan.id, project=uuid4())[1]["code"] == "plan_not_found"  # another project looks the same
    dataset_id = _dataset(client, ai)
    first = _root(client, ai, dataset_id, plan=str(plan.id))
    assert _request(ai.db, first).plan_proposal_id == plan.id  # consumed (single use)
    second = _root(client, ai, dataset_id, plan=str(plan.id))  # never an error: rule-only, refusal recorded
    request = _request(ai.db, second)
    assert request.plan_proposal_id is None
    assert request.request_spec["plan_refusal"] == {"code": "plan_already_used", "proposal_id": str(plan.id)}
    with pytest.raises(IntegrityError):  # the database keeps it single use
        ai.db.execute(text("UPDATE execution_requests SET plan_proposal_id = :p WHERE id = :id"),
                      {"p": plan.id, "id": request.id})
    ai.db.rollback()


def test_two_runs_racing_for_one_plan_the_loser_runs_rule_only(client, ai, monkeypatch):
    from app.services import run_plan_service

    plan = _plan(ai, {"families": ["random_forest"]})
    dataset_id = _dataset(client, ai)
    first = _root(client, ai, dataset_id, plan=str(plan.id))
    assert _request(ai.db, first).plan_proposal_id == plan.id
    # The race: the check ran before the first run committed, so only the unique index sees it.
    monkeypatch.setattr(run_plan_service, "plan_for_request", lambda db, **kw: (plan.id, None))
    second = _root(client, ai, dataset_id, plan=str(plan.id))  # 202, not a 500
    request = _request(ai.db, second)
    assert request.plan_proposal_id is None
    assert request.request_spec["plan_refusal"] == {"code": "plan_already_used", "proposal_id": str(plan.id)}
    assert _work(ai.db, second.id).status == "completed"
    assert "families" not in ai.db.get(Experiment, second.id).config  # rule-only


def test_the_harness_refuses_a_plan_outside_the_run_project(ai):
    from app.agents.harness.validation import check_proposal_nodes
    from app.agents.tools.catalog import ToolError

    own = _plan(ai, {"families": ["random_forest"]})
    check_proposal_nodes(ai.db, workspace_id=ai.ws, project_id=ai.project, tool="run_experiment",
                         arguments={"plan": str(own.id)})
    for foreign in (uuid4(), own.id):
        with pytest.raises(ToolError) as refused:
            check_proposal_nodes(ai.db, workspace_id=ai.ws, project_id=uuid4() if foreign == own.id else ai.project,
                                 tool="run_experiment", arguments={"plan": str(foreign)})
        assert refused.value.code == "node_not_in_project"


def test_a_problem_spec_takes_the_plan_target_and_metric_with_the_run_checks(client, ai):
    url = f"/v1/projects/{ai.project}/problem-specs"
    base = {"task_type": "binary", "business_objective": "Predict label."}
    plan = _plan(ai, {"target_column": "label", "primary_metric": "f1"})
    created = client.post(url, json={**base, "plan": str(plan.id)}, headers=_h(ai.setup, key=_key()))
    assert created.status_code == 201, created.text
    assert (created.json()["target_column"], created.json()["primary_metric"]) == ("label", "f1")
    again = client.post(url, json={**base, "target_column": "label", "plan": str(plan.id)},
                        headers=_h(ai.setup, key=_key()))
    assert again.status_code == 201  # agreeing request values; a spec does not consume the plan

    def refusal(payload, plan_id):
        response = client.post(url, json={**base, **payload, "plan": str(plan_id)}, headers=_h(ai.setup, key=_key()))
        assert response.status_code == 422, response.text
        return response.json()["error"]["details"]["refusal"]

    assert refusal({"target_column": "tenure"}, plan.id) == "plan_conflicts_with_request"
    assert refusal({}, uuid4()) == "plan_not_found"
    assert refusal({}, _plan(ai, {"target_column": "label"}, status="proposed").id) == "plan_not_accepted"
    assert refusal({}, _plan(ai, {"families": ["random_forest"]}).id) == "plan_has_no_spec_answers"
    assert refusal({"task_type": "regression"}, _plan(ai, {"primary_metric": "f1"}).id) == \
        "plan_metric_invalid_for_task"


def test_proposal_sources_are_exactly_one_and_review_items_are_l1(ai):
    base = dict(workspace_id=ai.ws, project_id=ai.project, decision_point_key="column.semantic_role",
                level_at_proposal=1, answer_ceiling=2, schema_version=1, payload={"x": 1}, payload_digest="a" * 64,
                citations=[], validator_verdict="accepted", validator_reasons=[], status="proposed",
                subject_kind="project")
    for values in ({"run_id": None, "proposal_type": "ExperimentPlanProposal"},  # no source
                   {"run_id": ai.run_id, "proposal_type": "SemanticReviewProposal"}):  # wrong pairing
        ai.db.add(AgentProposal(**base, **values))
        with pytest.raises(IntegrityError):
            ai.db.flush()
        ai.db.rollback()


def test_supersede_rules(ai):
    spec = create_problem_spec(ai.db, actor=ai.setup["alpha_admin"], workspace_id=ai.ws, project_id=ai.project,
                               task_type="binary", business_objective="Predict label.", target_column="label",
                               status="locked")
    ai.db.commit()
    split = _plan(ai, {"split": {"strategy": "random"}, "target_column": "label"}, status="proposed")
    metric = _plan(ai, {"primary_metric": "f1"}, status="proposed", subject_kind="problem_spec",
                   problem_spec_id=spec.id)
    families = _plan(ai, {"families": ["random_forest"]}, status="proposed", subject_kind="problem_spec",
                     problem_spec_id=spec.id)
    target_only = _plan(ai, {"target_column": "label"}, status="proposed", subject_kind="problem_spec",
                        problem_spec_id=spec.id)
    assert supersede_plans(ai.db, workspace_id=ai.ws, project_id=ai.project, reason="plan_exists",
                           target_column="label") == 1
    assert supersede_plans(ai.db, workspace_id=ai.ws, project_id=ai.project, reason="results_exist",
                           problem_spec_id=spec.id) == 2  # objective and portfolio answers
    ai.db.commit()
    ai.db.expire_all()
    assert [(ai.db.get(AgentProposal, row.id).status, ai.db.get(AgentProposal, row.id).supersede_reason)
            for row in (split, metric, families, target_only)] == [
        ("superseded", "plan_exists"), ("superseded", "results_exist"), ("superseded", "results_exist"),
        ("proposed", None)]


def test_split_validator_only_tightens_and_keeps_the_floor():
    rule = HoldoutPlan(strategy="stratified_random", test_size=0.2, random_state=42, stratified=True,
                       group_column=None, time_column=None, reason="rule",
                       evidence={"time_candidates": ["signup_date"]})
    frame = pd.DataFrame({"store": [1, 2, 3, 4] * 25, "customer_id": [f"c{i % 25}" for i in range(100)],
                          "x": range(100), "label": [0, 1] * 50})
    assert split_reasons(rule, {"strategy": "stratified_random", "test_size": 0.3}, frame) == ([], False)
    assert split_reasons(rule, {"strategy": "stratified_random", "test_size": 0.1}, frame)[0] == [
        "below_holdout_fraction_floor"]
    assert split_reasons(rule, {"strategy": "random"}, frame)[0] == ["loosens_split"]
    assert split_reasons(rule, {"strategy": "temporal_future", "time_column": "signup_date"}, frame) == ([], True)
    assert split_reasons(rule, {"strategy": "temporal_future", "time_column": "x"}, frame)[0] == [
        "time_column_not_found_by_rule"]
    group = {"strategy": "group_disjoint"}
    check = lambda column: split_reasons(rule, {**group, "group_column": column}, frame,  # noqa: E731
                                         target="label", task_type="binary")
    assert check("customer_id") == ([], True)  # a repeated-entity candidate of the rule's own profile
    assert check("store")[0] == ["group_column_not_found_by_rule"]  # four codes are a category, not an entity
    assert check("label")[0] == ["group_column_invalid"]  # never the target
    temporal = HoldoutPlan(strategy="temporal_future", test_size=0.2, random_state=42, stratified=False,
                           group_column=None, time_column="t", reason="rule")
    assert split_reasons(temporal, {"strategy": "stratified_random"}, frame)[0] == ["loosens_split"]


# --- job time: AI-before points ----------------------------------------------------------------


def test_accepted_plan_applies_validated_answers_as_human_input(client, ai):
    plan = _plan(ai, {"target_column": "label", "primary_metric": "f1", "families": ["random_forest"],
                      "max_training_seconds": 300, "split": {"strategy": "random"},
                      "missing_values": {"visits": "impute_most_frequent", "tenure": "impute_most_frequent"}})
    experiment = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    assert _work(ai.db, experiment.id).status == "completed"
    experiment = ai.db.get(Experiment, experiment.id)
    result, config = experiment.result, experiment.config
    assert (config["families"], config["max_training_seconds"]) == (["random_forest"], 300.0)
    assert {row["model_family"] for row in result["candidates"]} == {"random_forest", "majority"}  # dummy kept
    assert result["metric_plan"]["primary_metric"] == "f1"
    assert "visits" in result["preprocessing"]["categorical_columns"]  # impute_most_frequent => categorical
    found = _records(ai.db, experiment.id)
    assert set(found) >= {"target.column", "spec.objective", "split.strategy", "training.families_budget",
                          "column.missing_value_action"}
    assert _column(found["target.column"], "target_column")["agreement"] == "agree"
    families = found["training.families_budget"]
    assert (families.actor_kind, families.details["used"]["source"], families.details["evidence_partition"]) == (
        "rule", "human", "metadata")
    assert families.details["ai"]["plan_proposal_id"] == str(plan.id)
    split = _column(found["split.strategy"], "split")
    assert (split["source"], split["validator_reasons"]) == ("rule", ["loosens_split"])  # refused, run goes on
    missing = found["column.missing_value_action"]
    assert (_column(missing, "visits")["used"], _column(missing, "tenure")["source"]) == (
        "impute_most_frequent", "rule")
    assert _column(missing, "tenure")["validator_reasons"] == ["no_missing_values"]
    assert missing.details["revert"] == {"kind": "branch_change", "changes": [
        {"kind": "feature_transform_add", "column": "visits", "transform": "impute_median"}]}
    assert "visits" not in [c["column"] for c in found["column.semantic_role"].details["columns"]]  # not re-asked
    assert result["decision_points"]["plan"] == {"proposal_id": str(plan.id), "status": "accepted", "refusal": None}
    checks = {c["check_id"]: c["status"] for c in result["deterministic_verification"]["checks"]}
    assert checks["decision_point_evidence_partition"] == "PASS"


def test_applied_plan_applies_l2_kinds_only_at_l2_with_the_agent_as_actor(client, ai):
    ai.level["value"] = 2
    plan = _plan(ai, {"families": ["random_forest"], "max_training_seconds": 200}, status="applied")
    experiment = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    assert _work(ai.db, experiment.id).status == "completed"
    experiment = ai.db.get(Experiment, experiment.id)
    assert (experiment.config["families"], experiment.config["max_training_seconds"]) == (["random_forest"], 200.0)
    record = _records(ai.db, experiment.id)["training.families_budget"]
    assert (record.actor_kind, record.actor_agent_run_id, record.rationale_untrusted) == ("agent", ai.run_id, True)
    assert record.details["used"]["source"] == "ai" and record.details["revert"]["kind"] == "branch_change"
    assert experiment.config["ai_policy_digest"]  # L2: the policy and the applied values are in the fingerprint
    ai.level["value"] = 0  # demoted: the same kind of plan is refused at L0
    again = _plan(ai, {"families": ["random_forest"]}, status="applied")
    later = _root(client, ai, _dataset(client, ai), plan=str(again.id))
    assert _work(ai.db, later.id).status == "completed"
    later = ai.db.get(Experiment, later.id)
    assert "families" not in later.config
    column = _column(_records(ai.db, later.id)["training.families_budget"], "families")
    assert (column["source"], column["level"]) == ("rule", 0)


def test_a_valid_split_change_stops_at_needs_input_before_the_holdout_lock(client, ai):
    plan = _plan(ai, {"split": {"strategy": "stratified_random", "test_size": 0.3}})  # a larger holdout
    experiment = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    _work(ai.db, experiment.id)
    upload = _upload(ai.db, experiment.id)
    assert upload.pipeline_status == "needs_input"
    assert upload.pipeline_log["split_confirmation"]["kind"] == "split_strategy_confirmation"
    assert _request(ai.db, experiment).status == "needs_input"
    events = [e.event_type for e in ai.db.scalars(select(MlRunEvent).where(MlRunEvent.experiment_id == experiment.id))]
    assert "holdout_locked" not in events and "split_confirmation_required" in events
    column = _column(_records(ai.db, experiment.id)["split.strategy"], "split")
    assert column["validator_reasons"] == ["needs_confirmation"]


def test_keep_the_rule_split_resumes_the_parked_run_once_without_parking_again(client, ai):
    plan = _plan(ai, {"split": {"strategy": "stratified_random", "test_size": 0.3}})
    experiment = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    _work(ai.db, experiment.id)
    request = _request(ai.db, experiment)
    assert request.status == "needs_input"
    url = f"/v1/execution-requests/{request.id}/split-confirmation"
    target = client.post(f"/v1/execution-requests/{request.id}/target-confirmation", json={"target_column": "tenure"},
                         headers=_h(ai.setup, key=_key()))
    assert target.status_code == 409  # the target is resolved; only the split question is open
    wrong = client.post(url, json={"answer": "use_plan_split"}, headers=_h(ai.setup, key=_key()))
    assert wrong.status_code == 422  # only the rule's split can be kept here
    confirmed = client.post(url, json={"answer": "keep_rule_split"}, headers=_h(ai.setup, key=_key()))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "running"
    replay = client.post(url, json={"answer": "keep_rule_split"}, headers=_h(ai.setup, key=_key()))
    assert replay.status_code == 200  # replay-safe by state: the same answer returns the request
    assert _work(ai.db, experiment.id).status == "completed", _upload(ai.db, experiment.id).pipeline_log.get("reason")
    request = _request(ai.db, experiment)
    assert request.status != "needs_input" and request.request_spec["split_resolution"] == "keep_rule_split"
    assert _upload(ai.db, experiment.id).pipeline_status == "completed"
    rows = list(ai.db.scalars(select(MlRunEvent).where(MlRunEvent.experiment_id == experiment.id)))
    locked = next(e.payload for e in rows if e.event_type == "holdout_locked")
    assert locked["requested_test_size"] == pytest.approx(0.2)  # the rule's fraction, not the plan's 0.3
    events = [e.event_type for e in rows]
    assert events.count("split_confirmation_required") == 1 and "split_confirmed" in events
    confirmed_event = next(e.payload for e in rows if e.event_type == "split_confirmed")
    assert confirmed_event["answer"] == "keep_rule_split" and confirmed_event["confirmed_by"]
    # The parked run's record is reused on resume (records are idempotent per point and the
    # rule value is unchanged); the plan evidence carries the refusal.
    column = _column(_records(ai.db, experiment.id)["split.strategy"], "split")
    assert column["source"] == "rule"
    evidence = ai.db.get(Experiment, experiment.id).result["decision_points"]["plan"]
    assert evidence["refused_fields"] == {"split": "kept_rule_split"}
    late = client.post(url, json={"answer": "keep_rule_split"}, headers=_h(ai.setup, key=_key()))
    assert late.status_code == 200  # still the same answer after completion


def test_split_confirmation_needs_a_split_question(client, ai):
    experiment = _root(client, ai, _dataset(client, ai))
    response = client.post(f"/v1/execution-requests/{_request(ai.db, experiment).id}/split-confirmation",
                           json={"answer": "keep_rule_split"}, headers=_h(ai.setup, key=_key()))
    assert response.status_code == 409


def test_a_families_revert_keeps_the_rule_candidate_order():
    from app.engine.search.generator import open_ingest_families, open_ingest_portfolio

    rule = open_ingest_families("binary")
    last = rule[-1]
    restored = open_ingest_portfolio("binary", {"families_include": rule[:1]}, subset=[last])
    assert restored == [n for n in rule if n in {last, rule[0]}]


# --- branches inherit (reproducibility, ADR 0008 §2c) --------------------------------------------


def _branch(client, ns, parent: Experiment, changes: list[dict]) -> Experiment:
    response = client.post(f"/v1/experiments/{parent.id}/branches", json={"intent": "one change", "changes": changes},
                           headers=_h(ns.setup, key=_key()))
    assert response.status_code == 202, response.text
    child = ns.db.get(Experiment, response.json()["id"])
    assert _work(ns.db, child.id).status == "completed", _upload(ns.db, child.id).pipeline_log.get("reason")
    ns.db.expire_all()
    return ns.db.get(Experiment, child.id)


def _modeled(experiment: Experiment) -> tuple[list[str], list[str]]:
    pre = experiment.result["preprocessing"]
    return sorted(pre["numeric_columns"]), sorted(pre["categorical_columns"])


def test_parent_at_l2_then_demoted_branch_keeps_the_modeled_columns_and_a_revert_flips_back(client, ai):
    ai.answers.update({dp.ROLE: {"visits": ("categorical_code", 0.95), "*": ("numeric", 0.5)},
                       dp.IDENTIFIER: {"*": (0.05, None)}, dp.LEAKAGE: {"*": (0.5, None)}})
    ai.level["value"] = 2
    parent = _root(client, ai, _dataset(client, ai, labelled=True, gaps=False))
    assert _work(ai.db, parent.id).status == "completed"
    parent = ai.db.get(Experiment, parent.id)
    assert "visits" in _modeled(parent)[1]  # re-typed at L2 (validator-accepted)
    ai.level["value"] = 0  # demoted after the parent ran
    child = _branch(client, ai, parent, [{"kind": "family_exclude", "family": "random_forest"}])
    assert _modeled(child) == _modeled(parent)  # inherited, not re-decided
    inherited = _column(_records(ai.db, child.id)[dp.ROLE], "visits")
    assert (inherited["source"], inherited["used"]) == ("ai_inherited", "categorical_code")
    reverted = _branch(client, ai, parent, [{"kind": "feature_transform_add", "column": "visits",
                                             "transform": "impute_median"}])
    assert "visits" in _modeled(reverted)[0]  # the revert restores the recorded rule answer


def test_branches_inherit_plan_values_and_their_own_changes_win(client, ai):
    plan = _plan(ai, {"families": ["random_forest"], "missing_values": {"visits": "impute_most_frequent"}})
    parent = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    assert _work(ai.db, parent.id).status == "completed"
    parent = ai.db.get(Experiment, parent.id)
    assert "visits" in _modeled(parent)[1] and parent.config["families"] == ["random_forest"]
    child = _branch(client, ai, parent, [{"kind": "class_weighting", "mode": "none"}])  # unrelated change
    assert _modeled(child) == _modeled(parent)
    assert {row["model_family"] for row in child.result["candidates"]} == {"random_forest", "majority"}
    found = _records(ai.db, child.id)
    assert _column(found["column.missing_value_action"], "visits")["source"] == "ai_inherited"
    assert _column(found["training.families_budget"], "families")["source"] == "ai_inherited"
    reverted = _branch(client, ai, parent, [
        {"kind": "family_include", "family": "logistic_regression"},  # the recorded revert of the subset
        {"kind": "feature_transform_add", "column": "visits", "transform": "impute_median"}])
    assert "visits" in _modeled(reverted)[0]
    assert {row["model_family"] for row in reverted.result["candidates"]} >= {"logistic_regression", "random_forest"}
    assert _column(_records(ai.db, reverted.id)["column.missing_value_action"], "visits")["source"] == "human"
    empty = client.post(f"/v1/experiments/{parent.id}/branches", headers=_h(ai.setup, key=_key()),
                        json={"intent": "x", "changes": [{"kind": "family_exclude", "family": "random_forest"}]})
    assert empty.status_code == 422 and "portfolio_empty" in empty.text  # the subset's only family


def test_an_agreeing_split_plan_runs_and_missing_value_answers_keep_protected_columns(client, ai, monkeypatch):
    from app.services.auto_train import decisions

    seen = {}
    real = decisions.resolve_missing_point

    def spy(ctx, **kwargs):
        seen.update(kwargs)
        return real(ctx, **kwargs)

    monkeypatch.setattr(decisions, "resolve_missing_point", spy)
    plan = _plan(ai, {"split": {"strategy": "stratified_random"}})  # omits columns and fraction: the rule's
    experiment = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    assert _work(ai.db, experiment.id).status == "completed"  # no spurious needs_input
    column = _column(_records(ai.db, experiment.id)["split.strategy"], "split")
    assert (column["agreement"], column["source"]) == ("agree", "rule")
    development = ai.db.get(Experiment, experiment.id).result["model_development_plan"]
    # target, group, time and the inferred entity column are never re-treated by a plan
    assert {"label", development.get("group_column"), development.get("time_column")} <= seen["protected"]
    frame = pd.DataFrame({"when": [1.0, None, 3.0, 4.0]})
    assert _missing_reasons("when", "impute_median", "impute_most_frequent", frame, leakage_excluded=set(),
                            protected={"when"}) == ["protected_column"]


def test_objective_and_portfolio_answers_made_after_results_are_refused(client, ai):
    early = _plan(ai, {"families": ["random_forest"]})  # made before any result
    dataset_id = _dataset(client, ai)
    first = _root(client, ai, dataset_id)
    assert _work(ai.db, first.id).status == "completed"
    late = _plan(ai, {"primary_metric": "f1", "families": ["random_forest"], "max_training_seconds": 300})
    refused = _root(client, ai, dataset_id, plan=str(late.id))
    assert _work(ai.db, refused.id).status == "completed"
    refused = ai.db.get(Experiment, refused.id)
    assert "families" not in refused.config and refused.config["max_training_seconds"] == 600.0
    assert refused.result["metric_plan"]["primary_metric"] != "f1"
    assert refused.result["decision_points"]["plan"]["refused_fields"] == {
        "primary_metric": "results_exist", "families": "results_exist", "max_training_seconds": "results_exist"}
    found = _records(ai.db, refused.id)
    assert _column(found["training.families_budget"], "families")["validator_reasons"] == ["results_exist"]
    assert _column(found["spec.objective"], "primary_metric")["source"] == "rule"
    usable = _root(client, ai, dataset_id, plan=str(early.id))  # accepted before results: still usable
    assert _work(ai.db, usable.id).status == "completed"
    assert ai.db.get(Experiment, usable.id).config["families"] == ["random_forest"]


def test_a_branch_never_inherits_the_time_budget(client, ai):
    ai.level["value"] = 2
    plan = _plan(ai, {"max_training_seconds": 300}, status="applied")
    parent = _root(client, ai, _dataset(client, ai), plan=str(plan.id))
    assert _work(ai.db, parent.id).status == "completed"
    parent = ai.db.get(Experiment, parent.id)
    assert parent.config["max_training_seconds"] == 300.0
    child = _branch(client, ai, parent, [{"kind": "class_weighting", "mode": "none"}])
    assert child.config["max_training_seconds"] == 600.0 and _modeled(child) == _modeled(parent)
    budget = _column(_records(ai.db, child.id)["training.families_budget"], "max_training_seconds")
    assert (budget["source"], budget["used"], budget["ai"]) == ("rule", 600.0, 300.0)
    assert _records(ai.db, child.id)["training.families_budget"].details["ai"]["refusals"] == ["budget_not_inherited"]


@pytest.mark.parametrize(("jev_role", "kept"), [("numeric", False), ("categorical_code", True)])
def test_a_legacy_l2_switch_applies_by_level_and_a_role_conflict_reverts_the_ledger(client, ai, monkeypatch,
                                                                                     jev_role, kept):
    from app.db.models import DataPreparationDecision, LabDecisionRecord
    from app.engine.lab.decision_validator import ValidationResult
    from app.services import lab_decision_ledger
    from legacy_ai_support import enable_legacy_ai

    low = {"column_type": {"action": "numerical", "evidence_field": "dtype", "rationale": "unsure", "confidence": 0.1},
           "leakage_review": {"availability_status": "unknown", "risk_level": "LOW", "evidence_field": "column",
                              "rationale": "unsure", "confidence": 0.1}}
    enable_legacy_ai(monkeypatch, ai.db, handler=lambda call: {
        "action": "impute_most_frequent", "evidence_field": "missing_fraction", "rationale": "a small integer code",
        "confidence": 0.9} if call.agent_key == "missing_value" else low[call.agent_key])
    # The legacy validator ties most_frequent to non-numeric dtypes, so in practice a legacy answer never
    # reaches this L2 switch; accept it here to drive the decision-point path end to end.
    monkeypatch.setattr(lab_decision_ledger, "validate_decision", lambda *a, **k: ValidationResult("accept", "t"))
    ai.level["value"] = 2
    ai.answers[dp.ROLE] = {"visits": (jev_role, 0.95), "*": ("numeric", 0.5)}
    experiment = _root(client, ai, _dataset(client, ai, labelled=True))
    assert _work(ai.db, experiment.id).status == "completed", _upload(ai.db, experiment.id).pipeline_log.get("reason")
    experiment = ai.db.get(Experiment, experiment.id)
    missing = _column(_records(ai.db, experiment.id)["column.missing_value_action"], "visits")
    upload = _upload(ai.db, experiment.id)
    ledger = ai.db.scalar(select(LabDecisionRecord).where(  # the missing-value row (not the column-type row)
        LabDecisionRecord.upload_id == upload.id, LabDecisionRecord.column == "visits",
        LabDecisionRecord.rule_decision == "impute_median"))
    assert ledger.raw_llm_output["action"] == "impute_most_frequent"
    if kept:  # applied at L2 through the hook; the ledger says the agent's answer was used
        assert (missing["source"], missing["used"], missing["level"]) == ("ai", "impute_most_frequent", 2)
        assert (ledger.source, ledger.final_decision) == ("llm", "impute_most_frequent")
        assert "visits" in _modeled(experiment)[1] and experiment.config["ai_policy_digest"]
    else:  # Jev disagrees: the rule for both, and the ledger and lineage agree with what trained
        assert (missing["source"], missing["used"]) == ("rule", "impute_median")
        assert (ledger.source, ledger.final_decision) == ("rule", "impute_median")
        assert "visits" in _modeled(experiment)[0]
    lineage = next(row for row in ai.db.scalars(select(DataPreparationDecision).where(
        DataPreparationDecision.pipeline_run_id == experiment.id,
        DataPreparationDecision.decision_type == "missing_value")) if row.evidence["column"] == "visits")
    assert lineage.parameter_value["action"] == missing["used"]  # the locked lineage shows what trained


@pytest.mark.parametrize(("jev_role", "kept"), [("numeric", False), ("categorical_code", True)])
def test_an_applied_missing_value_treatment_and_the_role_point_follow_section_1b(client, ai, jev_role, kept):
    ai.level["value"] = 2
    ai.answers[dp.ROLE] = {"visits": (jev_role, 0.95), "*": ("numeric", 0.5)}
    plan = _plan(ai, {"missing_values": {"visits": "impute_most_frequent"}}, status="applied")
    experiment = _root(client, ai, _dataset(client, ai, labelled=True), plan=str(plan.id))
    assert _work(ai.db, experiment.id).status == "completed"
    experiment = ai.db.get(Experiment, experiment.id)
    found = _records(ai.db, experiment.id)
    missing, role = _column(found["column.missing_value_action"], "visits"), _column(found[dp.ROLE], "visits")
    if kept:  # Jev agrees with the treatment: it stands, the role point records it as set upstream
        assert "visits" in _modeled(experiment)[1]
        assert (missing["source"], missing["used"], role["source"]) == ("ai", "impute_most_frequent", "upstream")
    else:  # Jev disagrees: the rule for both (ADR 0008 §1b)
        assert "visits" in _modeled(experiment)[0]
        assert (missing["source"], missing["used"]) == ("rule", "impute_median")
        assert found["column.missing_value_action"].details["ai"]["refusals"] == ["conflicts_with_semantic_role"]
        assert (role["source"], role["used"]) == ("rule", "numeric")
    decision = next(d for d in experiment.result["scientific_evidence"]["missing_value_plan"]["column_decisions"]
                    if d["column"] == "visits")
    assert decision["action"] == ("impute_most_frequent" if kept else "impute_median")
