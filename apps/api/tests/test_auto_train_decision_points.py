"""P6.9-A step A1: hybrid decision points in auto-train (ADR 0008 §1b, §2, §2c, §5, §7, §8).

Fake Jev through the real gateway only (no network). Hook-level tests drive the three
Jev points per level; the golden test runs one upload AI-off and at L0 with a deliberately
wrong fake Jev and asserts identical modeled columns, missing-value plan, search config
and result digest.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from types import SimpleNamespace
from uuid import UUID

import pandas as pd
import pytest
from sqlalchemy import select, text

from app.agents.gateway.contract import Refusal
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers import ProviderError
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.service import GatewayService
from app.agents.governance.decision_points import answer_ceiling
from app.agents.governance.seed import seed_platform_governance
from app.agents.governance.snapshot import PolicySnapshot, take_snapshot
from app.agents.governance.switches import flip_off
from app.agents.semantic import typesafe_jev
from app.agents.semantic.deterministic import DeterministicSemanticPort
from app.agents.semantic.port import Resolution, SemanticOutcome
from app.agents.semantic.releases import ROLES, sync_jev_releases
from app.agents.semantic.typesafe_jev import JevSemanticPort
from app.db.models import (
    AgentProposal,
    AiIncident,
    ClientLabUpload,
    DatasetColumn,
    Experiment,
    LlmInvocation,
    MlRunEvent,
    ProjectDecisionRecord,
    SemanticDecisionAnswer,
)
from app.domain.decision_records import decision_point_rule, is_code_owned_rule
from app.domain.errors import InvalidChangeSetError
from app.domain.experiment_changes import ExperimentChangeSet
from app.engine.search.fingerprint import scientific_candidate_config_payload, scientific_candidate_fingerprint
from app.engine.types import SearchConfig
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.auto_train import decision_points as dp
from app.services.auto_train_service import run_auto_train_job
from app.services.dataset_column_service import publish_dataset_policy_defaults, set_dataset_column_policy
from app.services.decision_point_service import accept_change, resolve, revert_change, write_record
from app.services.pipeline_verifier import verify_pipeline
from test_ai_gateway import gw  # noqa: F401 - fixture
from test_auto_train_event_sequence import _classification_frame

ON = SimpleNamespace(ai_enabled=True, dclab_env="test")
FRAME = pd.DataFrame({"feature": [1, 2, 3] * 10, "target": [0, 1] * 15, SOURCE_ROW_COLUMN: range(30)})
TRAIN_ROWS = list(range(40))  # the run's locked training partition (FRAME's rows are inside it)


def jev(answers) -> FakeProvider:
    """Fake Jev: per purpose, per column name (``*`` = any) -> (value, confidence); or an exception."""

    def handle(call):
        if isinstance(answers, BaseException):
            return answers
        body = json.loads(call.input_json)
        table = answers.get(body["purpose"], {})
        out = []
        for item in body["questions"]:
            key = item["question_key"]["untrusted_text"]
            value, confidence = table.get(key, table.get("*"))
            out.append({"question_key": key, "answer": {"value": value}, "confidence": confidence})
        return {"answers": out}

    return FakeProvider(handler=handle, environment="test")


class Ctx:
    """The slice of ``RunContext`` the decision points use."""

    def __init__(self, ns, snapshot, *, branch=None):
        self.db, self.branch = ns.db, branch
        self.upload = SimpleNamespace(workspace_id=ns.ws_a, dataset_id=ns.dataset_a, experiment_id=ns.alpha.pipeline.id)
        self.decisions = dp.RunDecisionPoints(snapshot)
        self.events: list[dict] = []

    def emit_event(self, stage, event_type, status, payload=None, duration_ms=None):
        assert (stage, event_type, status) == ("decision_points", "decision_point_resolved", "completed")
        self.events.append(dict(payload or {}))


@pytest.fixture
def hk(gw, monkeypatch):  # noqa: F811
    sync_jev_releases(gw.db)
    gw.db.commit()
    level = {"value": 0}

    def effective_level(db, workspace_id, key, kind=None, prompt_release_id=None, model_id=None):
        assert prompt_release_id is not None and model_id == "jev-1.13.0"  # keyed by (release, model)
        return min(level["value"], answer_ceiling(key, kind))

    monkeypatch.setattr("app.agents.governance.snapshot.effective_level", effective_level)

    def ctx(fake, lvl=0, *, service=None):
        level["value"] = lvl
        service = service or gw.service(providers={"openai": gw.fake, "typesafe": fake})
        return Ctx(gw, take_snapshot(gw.db, gw.ws_a, dp.JEV_POINTS, port=JevSemanticPort(service, settings=ON)))

    gw.ctx = ctx
    return gw


def roles(ctx, *, missing="keep", protected=(), legacy_categorical=False):
    """``feature`` is numeric by the rule; ``legacy_categorical``: the legacy writer re-typed it."""

    legacy_num, legacy_cat = ([], ["feature"]) if legacy_categorical else (["feature"], [])
    return dp.resolve_column_points(
        ctx, engineered_train=FRAME, kept_columns=["feature"], rule_numeric=["feature"], rule_categorical=[],
        rule_identifiers=[], legacy_numeric=legacy_num, legacy_categorical=legacy_cat,
        num_cols=list(legacy_num), cat_cols=list(legacy_cat), identifier_cols=[], ignored=[],
        transformed_datetime=set(), protected=set(protected), missing_actions={"feature": missing},
        train_rows=TRAIN_ROWS)


def leakage(ctx):
    risk = SimpleNamespace(column="feature", action="keep", risk="LOW", evidence={}, availability=None)
    return dp.resolve_leakage_point(
        ctx, locked_train=FRAME, target=SimpleNamespace(column="target", task_type="binary"),
        audit=SimpleNamespace(risks=[risk]), development_plan=SimpleNamespace(excluded_features=[], group_column=None))


def answer_rows(ns, purpose=None) -> list[SemanticDecisionAnswer]:
    ns.db.expire_all()
    query = select(SemanticDecisionAnswer).order_by(SemanticDecisionAnswer.created_at)
    if purpose:
        query = query.where(SemanticDecisionAnswer.purpose == purpose)
    return list(ns.db.scalars(query))


def records(db, experiment_id) -> dict[str, ProjectDecisionRecord]:
    db.expire_all()
    rows = db.scalars(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.experiment_id == experiment_id,
        ProjectDecisionRecord.decision_type == "decision_point_resolved"))
    return {row.details["decision_point"]: row for row in rows}


# --- hook level, per level ------------------------------------------------------------------


def test_l0_with_a_wrong_jev_logs_beside_the_rule_and_records_the_point(hk):
    fake = jev({dp.ROLE: {"feature": ("identifier", 0.99)}, dp.IDENTIFIER: {"feature": (0.99, None)}})
    ctx = hk.ctx(fake, 0)
    assert roles(ctx) == (["feature"], [], set())
    assert [(e["decision_point"], e["ai"], e["level"], e["agreement"], e["ai_applied"]) for e in ctx.events] == [
        (dp.IDENTIFIER, "on", 0, "disagree", 0), (dp.ROLE, "on", 0, "disagree", 0)]
    assert {e["policy_digest"] for e in ctx.events} == {ctx.decisions.snapshot.digest}
    sent = [json.loads(call.input_json) for call in fake.calls]
    role_state = next(body["state"] for body in sent if body["purpose"] == dp.ROLE)
    assert role_state == {"c0": {"column": {"untrusted_text": "feature"}, "dtype": "integer", "cardinality": "low",
                                 "value_pattern": "not_text", "nulls": "none"}}  # bands of the given train frame
    rows = answer_rows(hk)
    assert [(r.purpose, r.level, r.policy_outcome, r.evidence_partition) for r in rows] == [
        (dp.IDENTIFIER, 0, "rule", "train"), (dp.ROLE, 0, "rule", "train")]
    found = records(hk.db, hk.alpha.pipeline.id)  # written when each point resolved
    assert set(found) == {dp.IDENTIFIER, dp.ROLE}
    role = found[dp.ROLE]
    assert (role.actor_kind, role.actor_rule, role.state, role.subject_kind) == (
        "rule", "decision_point.column.semantic_role.v1", "accepted", "experiment")
    details = role.details
    assert (details["level"], details["evidence_partition"], details["agreement"], details["used"]["source"]) == (
        0, "train", "disagree", "rule")
    assert details["revert"] == {"kind": "none"} and details["policy_digest"] == ctx.decisions.snapshot.digest
    assert details["columns"] == [{"column": "feature", "rule": "numeric", "ai": "identifier", "used": "numeric",
                                   "source": "rule", "agreement": "disagree", "level": 0, "confidence": 0.99}]
    assert details["ai"]["semantic_answer_ids"] == [str(rows[1].id)] and details["model_id"] == "jev-1.13.0"
    roles(ctx)  # a second resolution finds its record (idempotent per experiment and point)
    assert len(records(hk.db, hk.alpha.pipeline.id)) == 2


@pytest.mark.parametrize(("case", "level", "answer", "missing", "used", "source", "reasons"), [
    ("l0_l2_eligible_disagree_uses_the_rule", 0, ("categorical_code", 0.99), "keep", "numeric", "rule", []),
    ("l1_disagree_is_a_review_item", 1, ("categorical_code", 0.9), "keep", "numeric", "human_pending", []),
    ("l2_validator_accepts", 2, ("categorical_code", 0.9), "keep", "categorical_code", "ai", []),
    ("l2_validator_rejects", 2, ("categorical_code", 0.9), "impute_median", "numeric", "rule",
     ["conflicts_with_missing_value_action"]),
    ("l2_agree", 2, ("numeric", 0.9), "keep", "numeric", "ai", []),
    ("l2_abstain", 2, ("categorical_code", 0.5), "keep", "numeric", "rule", []),
    ("l2_exclusion_stays_l1", 2, ("identifier", 0.95), "keep", "numeric", "human_pending", []),
])
def test_semantic_role_per_level(hk, case, level, answer, missing, used, source, reasons):
    ctx = hk.ctx(jev({dp.ROLE: {"feature": answer}, dp.IDENTIFIER: {"*": (0.05, None)}}), level)
    num, cat, applied = roles(ctx, missing=missing)
    assert (num, cat) == ((["feature"], []) if used == "numeric" else ([], ["feature"]))
    assert applied == ({"feature"} if used != "numeric" else set())
    resolution = ctx.decisions.resolved[dp.ROLE]
    (item,) = resolution.answers
    assert (item.used, item.source, list(item.validator_reasons)) == (used, source, reasons)
    in_band_role_change = answer[0] in ("numeric", "categorical_code") and answer[1] >= 0.8  # §1b kind
    assert item.level == min(level, 2 if in_band_role_change else 1)
    details = records(hk.db, hk.alpha.pipeline.id)[dp.ROLE].details
    column = details["columns"][0]
    if source == "human_pending":
        proposal = hk.db.get(AgentProposal, UUID(details["proposal_id"]))  # one SemanticReviewProposal (0075)
        assert details["proposal_ids"] == [details["proposal_id"]] == [column["proposal_id"]]
        assert (proposal.proposal_type, proposal.status, proposal.level_at_proposal, proposal.run_id,
                proposal.semantic_answer_id, proposal.experiment_id) == (
            "SemanticReviewProposal", "proposed", 1, None, item.answer_id, hk.alpha.pipeline.id)
        assert proposal.payload["evidence"]["cardinality"] == "low" and proposal.payload["ai"] == answer[0]
        assert column["accept_change"]["kind"] == "feature_transform_add"
        assert item.level == 1  # every exclusion and every L1 answer is a review item, never applied
    snapshot_digest = ctx.decisions.snapshot.fingerprint_digest
    if case == "l2_validator_accepts":
        assert details["revert"] == {"kind": "branch_change", "changes": [
            {"kind": "feature_transform_add", "column": "feature", "transform": "impute_median"}]}
        assert details["validator"]["verdict"] == "accepted" and details["answer_ceiling"] == 2
        assert ctx.events[-1]["ai_applied"] == 1
        # the applied values enter the fingerprint digest, not only the policy
        assert snapshot_digest and ctx.decisions.fingerprint_digest not in (None, snapshot_digest)
    else:
        assert details["revert"] == {"kind": "none"}
        assert ctx.decisions.fingerprint_digest == snapshot_digest
    if reasons:
        assert details["validator"] == {"verdict": "rejected", "rejected": 1}
        assert column["validator_reasons"] == reasons


def test_group_entity_and_unique_columns_are_never_retyped(hk):
    ctx = hk.ctx(jev({dp.ROLE: {"feature": ("categorical_code", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 2)
    assert roles(ctx, protected=("feature",))[:2] == (["feature"], [])
    assert "group_or_entity_column" in ctx.decisions.resolved[dp.ROLE].answers[0].validator_reasons
    unique = pd.DataFrame({"feature": range(30)})
    validator = dp.RoleValidator(unique, ["feature"], [], protected=set(), missing_actions={})
    assert "very_high_uniqueness" in validator.check("feature", "categorical_code")
    wide = pd.DataFrame({"feature": list(range(60)) * 2})
    assert dp.RoleValidator(wide, ["feature"], [], protected=set(), missing_actions={}).check(
        "feature", "categorical_code") == ["above_one_hot_cardinality_cap"]
    text_column = pd.DataFrame({"feature": ["a", "b"] * 15})
    assert "not_numeric" in dp.RoleValidator(text_column, [], ["feature"], protected=set(), missing_actions={}).check(
        "feature", "numeric")


def test_identifier_and_leakage_never_change_a_value_even_when_set_to_l2(hk):
    fake = jev({dp.IDENTIFIER: {"feature": (0.99, None)}, dp.ROLE: {"*": ("numeric", 0.9)},
                dp.LEAKAGE: {"feature": (0.97, None)}})
    ctx = hk.ctx(fake, 2)
    assert ctx.decisions.snapshot.max_level(dp.IDENTIFIER) == 1 == ctx.decisions.snapshot.max_level(dp.LEAKAGE)
    roles(ctx)
    leakage(ctx)
    for key in (dp.IDENTIFIER, dp.LEAKAGE):
        (item,) = ctx.decisions.resolved[key].answers
        assert (item.agreement, item.level, item.source, item.used) == ("disagree", 1, "human_pending", item.rule)
    assert ctx.decisions.resolved[dp.LEAKAGE].answers[0].rule == "clear"
    found = records(hk.db, hk.alpha.pipeline.id)
    assert found[dp.IDENTIFIER].details["columns"][0]["accept_change"] == {
        "kind": "feature_transform_add", "column": "feature", "transform": "drop_column"}
    assert found[dp.LEAKAGE].details["columns"][0]["accept_change"] is None  # a review flag only


def test_timeout_and_over_budget_are_unavailable_and_take_the_rule(hk, monkeypatch):
    ctx = hk.ctx(jev(ProviderError("timeout")), 2)
    assert roles(ctx)[:2] == (["feature"], [])
    item = ctx.decisions.resolved[dp.ROLE].answers[0]
    assert (item.agreement, item.refusal, item.used) == ("unavailable", "timeout", "numeric")
    details = records(hk.db, hk.alpha.pipeline.id)[dp.ROLE].details
    assert details["agreement"] == "unavailable" and details["ai"]["refusals"] == ["timeout"]
    fake = jev({dp.ROLE: {"*": ("categorical_code", 0.99)}, dp.IDENTIFIER: {"*": (0.99, None)}})
    service = hk.service(providers={"openai": hk.fake, "typesafe": fake})
    monkeypatch.setattr(service, "reserve", lambda *a, **k: Refusal(code="budget_exhausted"))
    broke = hk.ctx(fake, 2, service=service)
    assert roles(broke)[:2] == (["feature"], []) and fake.calls == []
    assert {a.refusal for r in broke.decisions.resolved.values() for a in r.answers} == {"budget_exhausted"}
    assert {e["agreement"] for e in broke.events} == {"unavailable"}


def test_ai_off_kill_switch_and_branch_runs_ask_nothing_and_write_nothing(hk):
    fake = jev({dp.ROLE: {"*": ("identifier", 0.99)}, dp.IDENTIFIER: {"*": (0.99, None)}})
    service = hk.service(providers={"openai": hk.fake, "typesafe": fake})
    from app.agents.semantic.port import semantic_port

    off = take_snapshot(hk.db, hk.ws_a, dp.JEV_POINTS, port=semantic_port(service, settings=SimpleNamespace(ai_enabled=False)))
    assert isinstance(off.port, DeterministicSemanticPort) and (off.ai, off.digest, off.reason) == ("off", None, "ai_disabled")
    ctx = Ctx(hk, off)
    assert roles(ctx) == (["feature"], [], set())
    leakage(ctx)
    assert [(e["ai"], e["reason"], e["level"], e["agreement"], e["policy_digest"]) for e in ctx.events] == [
        ("off", "ai_disabled", 0, "off", None)] * 3
    assert fake.calls == [] and answer_rows(hk) == [] and records(hk.db, hk.alpha.pipeline.id) == {}
    assert ctx.decisions.evidence() is None and ctx.decisions.fingerprint_digest is None
    flip_off(hk.db, workspace_id=hk.ws_a, switch_key=f"purpose:{dp.ROLE}", reason="test", actor_rule="test.switch.v1")
    hk.db.commit()
    switched = hk.ctx(fake, 2)
    roles(switched)
    by_point = {e["decision_point"]: e for e in switched.events}
    assert (by_point[dp.ROLE]["ai"], by_point[dp.ROLE]["reason"]) == ("off", "kill_switch")
    assert by_point[dp.IDENTIFIER]["ai"] == "on" and {r.purpose for r in answer_rows(hk)} == {dp.IDENTIFIER}
    branch = dp.start_decision_points(SimpleNamespace(branch=object(), db=hk.db, upload=None))
    assert (branch.snapshot.ai, branch.snapshot.reason) == ("off", "branch_run")


def test_a_failed_record_write_at_l2_falls_back_to_the_rule(hk, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(dp, "write_record", broken)
    ctx = hk.ctx(jev({dp.ROLE: {"feature": ("categorical_code", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 2)
    assert roles(ctx) == (["feature"], [], set())  # never trains on an unrecorded AI value
    (item,) = ctx.decisions.resolved[dp.ROLE].answers
    assert (item.ai, item.used, item.source, item.refusal) == ("categorical_code", "numeric", "rule",
                                                               "record_write_failed")
    event = ctx.events[-1]
    assert (event["decision_point"], event["ai"], event["ai_applied"], event["reason"]) == (
        dp.ROLE, "on", 0, "record_write_failed")
    assert records(hk.db, hk.alpha.pipeline.id) == {} and ctx.decisions.fingerprint_digest == (
        ctx.decisions.snapshot.fingerprint_digest)


def test_legacy_column_type_values_are_never_the_rule_answer_or_asked(hk):
    fake = jev({dp.ROLE: {"*": ("numeric", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}})
    ctx = hk.ctx(fake, 2)
    assert roles(ctx, legacy_categorical=True) == ([], ["feature"], set())  # the legacy value stays in use
    (item,) = ctx.decisions.resolved[dp.ROLE].answers
    assert (item.rule, item.used, item.source, item.refusal) == ("numeric", "categorical_code", "legacy",
                                                                 "legacy_override")
    assert {json.loads(c.input_json)["purpose"] for c in fake.calls} == {dp.IDENTIFIER}  # not asked
    details = records(hk.db, hk.alpha.pipeline.id)[dp.ROLE].details
    assert details["revert"] == {"kind": "none"} and details["used"]["legacy_overrides"] == 1
    assert details["columns"][0]["rule"] == "numeric"


def test_no_value_is_applied_without_a_record_and_branches_inherit_recorded_values(hk):
    ctx = hk.ctx(jev({dp.ROLE: {"feature": ("categorical_code", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 2)
    ctx.upload.experiment_id = None  # no project to record on: at most L1
    assert roles(ctx) == (["feature"], [], set())
    assert ctx.decisions.levels(ctx, dp.ROLE) == {"*": 1, "exclusion": 1, "role_numeric_categorical": 1}
    assert ctx.decisions.resolved[dp.ROLE].answers[0].source != "ai"
    applied = hk.ctx(jev({dp.ROLE: {"feature": ("categorical_code", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 2)
    assert roles(applied)[1] == ["feature"]  # recorded on the parent experiment
    branch = SimpleNamespace(parent_id=hk.alpha.pipeline.id, columns={})
    assert dp.inherited_values(hk.db, branch) == {dp.ROLE: {"feature": "categorical_code"}}
    started = dp.start_decision_points(SimpleNamespace(branch=branch, db=hk.db, upload=None))
    assert (started.snapshot.reason, started.inherited) == ("branch_run", {dp.ROLE: {"feature": "categorical_code"}})
    # precedence: the branch's own change (a revert) > the inherited value > rule
    outcome = SemanticOutcome(purpose=dp.ROLE, ai="off", resolutions=(
        _res("feature", "numeric", "numeric", "rule", 0, agreement="off"),
        _res("other", "numeric", "numeric", "rule", 0, agreement="off")))
    inherited = resolve(dp.ROLE, outcome, evidence_partition="train", overrides={"feature": "numeric"},
                        inherited={"feature": "categorical_code", "other": "categorical_code"})
    assert [(a.question_key, a.used, a.source) for a in inherited.answers] == [
        ("feature", "numeric", "human"), ("other", "categorical_code", "ai_inherited")]
    assert inherited.ai == "inherited" and inherited.recorded and [a.question_key for a in inherited.applied()] == [
        "other"]
    truncated = SimpleNamespace(details={**records(hk.db, hk.alpha.pipeline.id)[dp.ROLE].details, "columns": []})
    with pytest.raises(InvalidChangeSetError) as refused:  # applied values no longer named: fail closed
        dp.inherited_values(SimpleNamespace(scalars=lambda _query: [truncated]), branch)
    assert refused.value.reason == "ai_values_not_inherited"


def test_an_inherited_value_that_fails_the_recheck_is_recorded_not_dropped_silently(hk):
    ctx = Ctx(hk, PolicySnapshot.off("branch_run"))
    ctx.decisions = dp.RunDecisionPoints(PolicySnapshot.off("branch_run"),
                                         inherited={dp.ROLE: {"feature": "categorical_code"}})
    assert roles(ctx, protected=("feature",)) == (["feature"], [], set())  # now a protected column
    column = records(hk.db, hk.alpha.pipeline.id)[dp.ROLE].details["columns"][0]
    assert (column["source"], column["used"], column["ai"]) == ("rule", "numeric", None)
    assert column["validator_reasons"] == ["group_or_entity_column"]
    assert ctx.events[-1]["ai"] == "inherited" and ctx.events[-1]["ai_applied"] == 0
    assert ctx.decisions.resolved[dp.ROLE].answers[0].refusal == "recheck_failed"


def test_a_column_name_the_record_guard_refuses_keeps_counts_not_names(hk):
    outcome = SemanticOutcome(purpose=dp.ROLE, ai="on", resolutions=(
        _res("bad\x01name", "numeric", "categorical_code", "ai", 2, "categorical_code"),))
    resolution = resolve(dp.ROLE, outcome, evidence_partition="train", policy_digest="d" * 64)
    row = write_record(hk.db, resolution, workspace_id=hk.ws_a, project_id=hk.project_a,
                       experiment_id=hk.alpha.pipeline.id)
    hk.db.commit()
    assert row.details["columns"] == [] and row.details["revert"] == {
        "kind": "branch_change", "changes_count": 1, "names_omitted": True}


def test_unmapped_columns_are_unavailable_never_asked(hk):
    fake = jev({dp.LEAKAGE: {"*": (0.99, None)}})
    ctx = hk.ctx(fake, 1)
    risk = SimpleNamespace(column="feature", action="keep", risk="LOW", evidence={}, availability=None)
    frame = FRAME.rename(columns={"target": "label"})
    dp.resolve_leakage_point(  # the target name has no source column: nothing travels
        ctx, locked_train=frame, target=SimpleNamespace(column="label", task_type="binary"),
        audit=SimpleNamespace(risks=[risk]), development_plan=SimpleNamespace(excluded_features=[], group_column=None))
    assert fake.calls == [] and ctx.decisions.resolved[dp.LEAKAGE].answers[0].refusal == "column_unmapped"


# --- pure: precedence, change closure, snapshot, fingerprint, rule actor ---------------------


def _res(name, rule, used, outcome, level, ai=None, agreement="disagree"):
    return Resolution(question_key=name, column_id=None, rule_answer=rule, value_used=used, agreement=agreement,
                      level=level, policy_outcome=outcome, ai_answer=ai)


def test_resolve_precedence_is_override_then_inherited_then_ai_then_rule():
    outcome = SemanticOutcome(purpose=dp.ROLE, ai="on", resolutions=(
        _res("a", "numeric", "categorical_code", "ai", 2, "categorical_code"),
        _res("b", "numeric", "categorical_code", "ai", 2, "categorical_code"),
        _res("c", "numeric", "categorical_code", "ai", 2, "categorical_code"),
        _res("d", "numeric", "numeric", "review", 1, "categorical_code"),
        _res("e", "numeric", "identifier", "ai", 2, "identifier"),  # an exclusion is never applied
        _res("f", "numeric", "categorical_code", "ai", 3, "categorical_code"),  # no L3 in auto-train
    ))
    out = resolve(dp.ROLE, outcome, evidence_partition="train", overrides={"a": "numeric"},
                  inherited={"b": "numeric"})
    assert [(a.question_key, a.used, a.source) for a in out.answers] == [
        ("a", "numeric", "human"), ("b", "numeric", "ai_inherited"), ("c", "categorical_code", "ai"),
        ("d", "numeric", "human_pending"), ("e", "numeric", "rule"), ("f", "numeric", "rule")]
    assert [a.question_key for a in out.applied()] == ["c"] and out.agreement() == "disagree"
    with pytest.raises(ValueError):
        resolve(dp.ROLE, outcome, evidence_partition="full_file")


def test_every_revert_and_accept_change_is_an_existing_change_kind():
    changes = [revert_change(dp.ROLE, "x", rule) for rule in ROLES]
    changes += [accept_change(dp.ROLE, "x", rule, ai) for rule in ROLES for ai in ROLES]
    changes += [accept_change(dp.IDENTIFIER, "x", rule, ai) for rule in (True, False) for ai in (True, False)]
    changes += [accept_change(dp.LEAKAGE, "x", rule, True) for rule in ("clear", "exclude", "review_flag")]
    for change in filter(None, changes):
        ExperimentChangeSet.model_validate({"changes": [change]})
    assert revert_change(dp.ROLE, "x", "numeric")["transform"] == "impute_median"
    assert accept_change(dp.IDENTIFIER, "x", True, False)["transform"] == "keep"  # a re-inclusion
    assert is_code_owned_rule(decision_point_rule(dp.LEAKAGE)) and not is_code_owned_rule("decision_point.x")
    assert not is_code_owned_rule("decision_point.not.a.point.v1")  # registry keys only


def test_snapshot_digest_levels_and_fingerprint(hk):
    fake = jev({})
    l0, l2 = hk.ctx(fake, 0).decisions.snapshot, hk.ctx(fake, 2).decisions.snapshot
    assert l0.ai == l2.ai == "on" and len(l0.digest) == 64 and l0.digest != l2.digest
    assert l0.fingerprint_digest is None and l2.fingerprint_digest == l2.digest
    assert l2.levels_for(dp.ROLE) == {"*": 1, "exclusion": 1, "role_numeric_categorical": 2}
    assert PolicySnapshot.off("x").fingerprint_digest is None
    assert "ai_policy_digest" not in SearchConfig().to_dict()
    assert SearchConfig(ai_policy_digest="d" * 64).to_dict()["ai_policy_digest"] == "d" * 64
    base = dict(features=["a"], family="random_forest", seed=1, task_type="binary", target="t")
    assert "ai_policy_digest" not in scientific_candidate_config_payload(**base)
    assert scientific_candidate_fingerprint(**base) == scientific_candidate_fingerprint(**base, ai_policy_digest=None)
    assert scientific_candidate_fingerprint(**base) != scientific_candidate_fingerprint(**base, ai_policy_digest="d" * 64)


def test_verifier_fails_a_record_outside_the_training_partition(hk):
    ctx = hk.ctx(jev({dp.ROLE: {"*": ("numeric", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 0)
    roles(ctx)
    partition = records(hk.db, hk.alpha.pipeline.id)[dp.ROLE].details["partition"]
    assert partition["row_count"] == 30 and partition["subset_of_train"] is True
    report = {"run": {"experiment_id": str(hk.alpha.pipeline.id)},
              "split": {"n_train": len(TRAIN_ROWS), "train_source_rows": TRAIN_ROWS}}

    def check():
        found = [c for c in verify_pipeline(report, db=hk.db)["checks"]
                 if c["check_id"] == "decision_point_evidence_partition"]
        return found[0]["status"] if found else None

    assert check() == "PASS"
    report["split"] = {"n_train": 30, "train_source_rows": list(range(10, 40))}  # another partition's rows
    assert check() == "FAIL"
    report["split"] = {}
    assert check() == "NOT_VERIFIABLE"
    report["split"] = {"n_train": len(TRAIN_ROWS), "train_source_rows": TRAIN_ROWS}
    bad = ctx.decisions.resolved[dp.ROLE]
    from dataclasses import replace

    from app.services.decision_point_service import source_rows_digest

    # A full-size frame must be exactly the training rows, whatever its subset flag says.
    lying = {"row_count": len(TRAIN_ROWS), "source_rows_digest": source_rows_digest(range(1, 41)),
             "train_rows_digest": source_rows_digest(TRAIN_ROWS), "subset_of_train": True}
    write_record(hk.db, replace(bad, key="column.missing_value_action", partition=lying), workspace_id=hk.ws_a,
                 project_id=hk.project_a, experiment_id=hk.alpha.pipeline.id)
    hk.db.commit()
    assert check() == "FAIL"

    row = write_record(hk.db, replace(bad, key=dp.LEAKAGE), workspace_id=hk.ws_a, project_id=hk.project_a,
                       experiment_id=hk.alpha.pipeline.id)
    row.details = {**row.details, "evidence_partition": "full_file"}  # what the check must catch
    hk.db.commit()
    assert check() == "FAIL"


# --- P6.7-A follow-ups ------------------------------------------------------------------------


def test_failed_answer_inserts_are_counted_into_one_reconciliation_incident(hk, monkeypatch):
    monkeypatch.setattr(typesafe_jev, "WRITE_FAILURES", Counter())
    for expected in (1, 2, 3, 1):  # opening the incident restarts the count
        assert typesafe_jev.count_answer_write_failure(hk.db, hk.ws_a, dp.ROLE, lost=2) == expected
    hk.db.expire_all()
    incidents = list(hk.db.scalars(select(AiIncident).where(AiIncident.kind == "reconciliation")))
    assert len(incidents) == 1 and (incidents[0].subject_kind, incidents[0].subject_key, incidents[0].status) == (
        "purpose", dp.ROLE, "open")
    seen = []

    class Broken:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def add_all(self, rows):
            pass

        def commit(self):
            raise RuntimeError("insert failed")

    monkeypatch.setattr(typesafe_jev, "Session", Broken)
    monkeypatch.setattr(typesafe_jev, "count_answer_write_failure", lambda db, ws, purpose, lost: seen.append(purpose))
    ctx = hk.ctx(jev({dp.ROLE: {"*": ("numeric", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 0)
    assert roles(ctx)[:2] == (["feature"], [])  # the decision stands
    assert seen == [dp.IDENTIFIER, dp.ROLE]


def test_jev_ledger_rows_record_whether_user_text_was_present(hk):
    roles(hk.ctx(jev({dp.ROLE: {"*": ("numeric", 0.9)}, dp.IDENTIFIER: {"*": (0.05, None)}}), 0))
    hk.db.expire_all()
    rows = list(hk.db.scalars(select(LlmInvocation).where(LlmInvocation.purpose.in_(dp.JEV_POINTS))))
    assert rows and all(row.redaction_summary.get("user_text_present") is False for row in rows)


# --- the golden: AI off vs L0 with a deliberately wrong Jev ----------------------------------


def _with_visits(frame: pd.DataFrame) -> pd.DataFrame:
    frame.insert(2, "visits", [1, 2, 3, 4] * (len(frame) // 4) + [1] * (len(frame) % 4))
    return frame


def _upload(auth_client, frame):
    response = auth_client.post("/app/labs/uploads", data={"category": "Revenue"},
                                files={"file": ("data.csv", frame.to_csv(index=False).encode(), "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def _label(db, actor, upload_id):
    upload = db.get(ClientLabUpload, upload_id)
    revision = db.execute(text("SELECT count(*) FROM dataset_policy_revisions WHERE dataset_id = :d"),
                          {"d": upload.dataset_id}).scalar()
    publish_dataset_policy_defaults(
        db, actor=actor, workspace_id=upload.workspace_id, dataset_id=upload.dataset_id, expected_revision=revision,
        sensitivity_class="internal", llm_exposure_policy="allow", retention_class="standard",
        residency_class="home_cloud_only", classification_source="manual", classification_confidence=1.0)
    for column in db.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == upload.dataset_id)):
        set_dataset_column_policy(db, workspace_id=upload.workspace_id, column_id=column.id, sensitivity_class="internal",
                                  classification_source="manual", model_use_policy="allow", llm_exposure_policy="allow",
                                  retention_class="standard", residency_class="home_cloud_only",
                                  classification_confidence=1.0)
    db.commit()


def _scientific(db, upload_id) -> dict:
    db.expire_all()
    upload = db.get(ClientLabUpload, upload_id)
    experiment = db.get(Experiment, upload.experiment_id)
    log, result = upload.pipeline_log, experiment.result
    candidates = sorted((c["candidate_id"], c.get("fingerprint"),
                         json.dumps([f.get("metrics") for f in c.get("folds") or []], sort_keys=True, default=str))
                        for c in result["candidates"])
    digest = hashlib.sha256(json.dumps({
        "candidates": candidates, "selected_ids": result.get("selected_ids"), "test_metrics": result.get("test_metrics"),
    }, sort_keys=True, default=str).encode()).hexdigest()
    events = [e.payload for e in db.scalars(select(MlRunEvent).where(
        MlRunEvent.experiment_id == experiment.id, MlRunEvent.event_type == "decision_point_resolved"))]
    checks = {c["check_id"]: c["status"] for c in result["deterministic_verification"]["checks"]}
    return {
        "status": upload.pipeline_status,
        "modeled": (log["column_roles"], log["preprocessing"]["numeric_columns"],
                    log["preprocessing"]["categorical_columns"], result["scientific_evidence"]["modeled_features"]),
        "missing_value_plan": log["missing_value_decisions"]["column_decisions"],
        "search_config": experiment.config,
        "result_digest": digest,
        "experiment": experiment, "result": result, "events": events, "checks": checks,
    }


def test_ai_off_and_l0_with_a_wrong_jev_train_identically(monkeypatch, auth_client, db_session, admin_user):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    seed_platform_governance(db_session, environment="test")
    sync_jev_releases(db_session)
    db_session.commit()
    frame = _with_visits(_classification_frame())  # `visits` is a column RoleValidator would re-type

    off_id = _upload(auth_client, frame)
    run_auto_train_job(db_session, off_id)
    off = _scientific(db_session, off_id)

    # Deliberately wrong at every point: every column an identifier and a leak; `visits` gets an
    # L2-eligible re-type (applied at L2, see the next test), every other column an id role.
    wrong = jev({dp.IDENTIFIER: {"*": (0.99, None)}, dp.ROLE: {"visits": ("categorical_code", 0.99),
                                                                "*": ("identifier", 0.99)},
                 dp.LEAKAGE: {"*": (0.99, None)}})
    service = GatewayService(providers={"typesafe": wrong}, limits=GatewayLimits(), settings=lambda: ON)
    monkeypatch.setattr(dp, "semantic_port", lambda: JevSemanticPort(service, settings=ON))
    l0_id = _upload(auth_client, frame)
    _label(db_session, admin_user, l0_id)
    run_auto_train_job(db_session, l0_id)
    l0 = _scientific(db_session, l0_id)

    assert off["status"] == l0["status"] == "completed"
    for key in ("modeled", "missing_value_plan", "search_config", "result_digest"):
        assert off[key] == l0[key], key
    # AI off: only the new events (ai: off); no record, no Jev row, no run evidence, no new check.
    assert [(e["decision_point"], e["ai"]) for e in off["events"]] == [
        (dp.LEAKAGE, "off"), (dp.IDENTIFIER, "off"), (dp.ROLE, "off")]
    assert records(db_session, off["experiment"].id) == {} and "decision_points" not in off["result"]
    assert "decision_point_evidence_partition" not in off["checks"]
    assert db_session.scalar(select(SemanticDecisionAnswer.id).where(
        SemanticDecisionAnswer.experiment_id == off["experiment"].id).limit(1)) is None
    # L0: Jev was asked and logged beside the rule; one rule-actor record per point.
    assert wrong.calls and {(e["ai"], e["level"]) for e in l0["events"]} == {("on", 0)}
    found = records(db_session, l0["experiment"].id)
    assert set(found) == set(dp.JEV_POINTS)
    assert all(r.actor_kind == "rule" and r.details["evidence_partition"] == "train" and r.details["level"] == 0
               and r.details["used"]["source"] == "rule" for r in found.values())
    assert found[dp.ROLE].details["agreement"] in ("disagree", "partial")
    visits = next(c for c in found[dp.ROLE].details["columns"] if c["column"] == "visits")
    assert (visits["rule"], visits["ai"], visits["used"], visits["source"], visits["level"]) == (
        "numeric", "categorical_code", "numeric", "rule", 0)
    assert "visits" in off["modeled"][1] and "visits" in l0["modeled"][1]  # numeric in both runs
    stored = list(db_session.scalars(select(SemanticDecisionAnswer).where(
        SemanticDecisionAnswer.experiment_id == l0["experiment"].id)))
    assert stored and {(r.level, r.policy_outcome, r.evidence_partition) for r in stored} == {(0, "rule", "train")}
    evidence = l0["result"]["decision_points"]
    assert evidence["fingerprint_digest"] is None and len(evidence["policy_digest"]) == 64
    assert {k: v["record_id"] for k, v in evidence["points"].items()} == {k: str(r.id) for k, r in found.items()}
    assert l0["checks"]["decision_point_evidence_partition"] == "PASS"


def test_l2_role_value_reaches_the_fold_pipeline_with_its_record_and_fingerprint(
        monkeypatch, auth_client, db_session, admin_user):
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    seed_platform_governance(db_session, environment="test")
    sync_jev_releases(db_session)
    db_session.commit()
    monkeypatch.setattr("app.agents.governance.snapshot.effective_level",
                        lambda db, ws, key, kind=None, prompt_release_id=None, model_id=None:
                        min(2, answer_ceiling(key, kind)))
    frame = _with_visits(_classification_frame())
    fake = jev({dp.ROLE: {"visits": ("categorical_code", 0.95), "*": ("numeric", 0.5)},
                dp.IDENTIFIER: {"*": (0.05, None)}, dp.LEAKAGE: {"*": (0.5, None)}})
    service = GatewayService(providers={"typesafe": fake}, limits=GatewayLimits(), settings=lambda: ON)
    monkeypatch.setattr(dp, "semantic_port", lambda: JevSemanticPort(service, settings=ON))
    upload_id = _upload(auth_client, frame)
    _label(db_session, admin_user, upload_id)
    run_auto_train_job(db_session, upload_id)
    run = _scientific(db_session, upload_id)
    assert run["status"] == "completed"
    log = db_session.get(ClientLabUpload, upload_id).pipeline_log
    assert "visits" in log["preprocessing"]["categorical_columns"]
    assert "visits" not in log["preprocessing"]["numeric_columns"]
    assert "visits" in run["result"]["task"]["column_roles"]["categorical"]  # what build_preprocessor receives
    role = next(r for r in log["column_role_evidence"]["columns"] if r["column"] == "visits")
    assert role["source"] == "decision_point:column.semantic_role"
    evidence = run["result"]["decision_points"]
    assert evidence["points"][dp.ROLE]["ai_applied"] == 1
    assert run["search_config"]["ai_policy_digest"] == evidence["fingerprint_digest"] != evidence["policy_digest"]
    details = records(db_session, run["experiment"].id)[dp.ROLE].details
    assert details["used"] == {"source": "ai", "ai_applied": 1, "legacy_overrides": 0, "human_overrides": 0}
    assert details["revert"]["changes"] == [
        {"kind": "feature_transform_add", "column": "visits", "transform": "impute_median"}]
    assert run["checks"]["decision_point_evidence_partition"] == "PASS"
