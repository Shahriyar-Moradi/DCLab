"""The legacy LLM writers on the AI gateway (ADR 0009 §1, §4, §8; ADR 0008 §1, §2b).

Fake provider only. AI off leaves today's rows; a refusal maps onto the rule value
with its code in the row's reason; sample values never leave the process; the
leakage reviewer writes a ledger row; prompt files match their output models.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.agents.gateway.contract import Refusal
from app.agents.governance.platform_default import CAPS
from app.agents.governance.switches import flip_off
from app.agents.legacy import context_for_upload
from app.agents.prompt_releases import PROMPTS_ROOT, discover, output_schema_digest, prompt_text
from app.db.models import LabDecisionRecord, LlmInvocation
from app.domain.ml_verification import PipelineAuditReport
from app.engine.lab.auto_prepare import plan_missing_values
from app.engine.lab.evidence import LeakageReviewEvidence
from app.engine.lab.llm_client import (
    CALLS,
    WITHHELD,
    ColumnTypeDecision,
    LeakageReviewDecision,
    MissingValueDecision,
    TargetSelectionDecision,
)
from app.engine.lab.prompts import (
    column_type_v1,
    column_type_v2,
    leakage_review_v1,
    missing_value_v1,
    missing_value_v2,
    target_selection_v1,
)
from app.services import lab_decision_ledger
from legacy_ai_support import enable_legacy_ai, forbid_gateway, sent, upload_via_api

MEDIAN = {"action": "impute_median", "evidence_field": "missing_fraction", "fill_value": None,
          "rationale": "median is robust", "confidence": 0.9}


def _frame(n: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    return pd.DataFrame({
        "monthly": [None] * 10 + list(rng.uniform(20, 120, n - 10)),
        "plan_code": [1 + (i % 3) for i in range(n)],
        "final_score": rng.uniform(0, 1, n),
        "churn": [0, 1] * (n // 2),
    })


def _decide(db, upload) -> list[tuple]:
    frame = _frame()
    lab_decision_ledger.record_missing_value_decisions(
        db, upload.id, frame, plan_missing_values(frame, ["monthly"]), "churn")
    lab_decision_ledger.record_column_type_decisions(db, upload.id, frame, ["monthly", "plan_code"], [])
    db.commit()
    db.expire_all()
    return sorted(
        (row.purpose, row.status, row.reason, row.llm_used, row.provider_kind, row.validator_verdict)
        for row in db.scalars(select(LlmInvocation).where(LlmInvocation.experiment_id == upload.experiment_id,
                                                          LlmInvocation.purpose.like("semantic_%")))
    )


@pytest.fixture
def upload(auth_client, db_session, monkeypatch):
    return upload_via_api(auth_client, db_session, monkeypatch, _frame())


def test_ai_enabled_off_makes_no_gateway_call_and_writes_todays_rows(upload, auth_client, db_session, monkeypatch):
    enable_legacy_ai(monkeypatch, db_session, decision_agent=False)
    forbid_gateway(monkeypatch)
    flag_off = _decide(db_session, upload)
    other = upload_via_api(auth_client, db_session, monkeypatch, _frame())
    enable_legacy_ai(monkeypatch, db_session, ai_enabled=False)  # decision agent on, AI_ENABLED off
    forbid_gateway(monkeypatch)
    assert _decide(db_session, other) == flag_off
    assert {(llm_used, kind) for _p, _s, _r, llm_used, kind, _v in flag_off} == {(False, "deterministic_fallback")}
    assert {reason for _p, _s, reason, *_ in flag_off} == {
        "LLM used: NO — semantic assistance was disabled or unconfigured.",
        "LLM used: NO — deterministic evidence was sufficient.",
    }


def test_flag_on_and_switch_off_falls_back_with_the_refusal_code(upload, db_session, monkeypatch):
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: dict(MEDIAN))
    flip_off(db_session, workspace_id=upload.workspace_id, switch_key="agent:missing_value", reason="test",
             actor_rule="test.switch.v1")
    db_session.commit()
    frame = _frame()
    plan = plan_missing_values(frame, ["monthly"])
    rule_action = plan.column_decisions[0].action
    lab_decision_ledger.record_missing_value_decisions(db_session, upload.id, frame, plan, "churn")
    db_session.commit()
    assert ai.fake.calls == []
    record = db_session.scalar(select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload.id))
    assert (record.source, record.final_decision, record.raw_llm_output) == ("rule", rule_action, None)
    assert record.validator_verdict.startswith("unavailable: kill_switch")
    row = db_session.get(LlmInvocation, record.llm_invocation_id)
    assert (row.status, row.refusal_code, row.llm_used) == ("refused", "kill_switch", False)
    assert row.reason == "LLM used: NO — the AI gateway refused the call (kill_switch); rule retained."
    assert row.final_decision == {"column": "monthly", "rule_decision": rule_action,
                                  "final_decision": rule_action, "source": "rule"}


def test_provider_failure_after_the_call_keeps_the_rule_and_says_so(upload, db_session, monkeypatch):
    from app.agents.gateway.providers import ProviderError

    def fail(_call):
        raise ProviderError("timeout")

    enable_legacy_ai(monkeypatch, db_session, handler=fail)
    frame = _frame()
    plan = plan_missing_values(frame, ["monthly"])
    lab_decision_ledger.record_missing_value_decisions(db_session, upload.id, frame, plan, "churn")
    db_session.commit()
    record = db_session.scalar(select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload.id))
    row = db_session.get(LlmInvocation, record.llm_invocation_id)
    assert (record.source, row.status, row.refusal_code, row.llm_used) == ("rule", "failed", "timeout", True)
    assert row.reason == "LLM used: YES — provider attempt was unavailable (timeout); rule retained."


def test_sample_values_never_reach_the_provider(upload, db_session, monkeypatch):
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda call: dict(MEDIAN) if call.agent_key == "missing_value"
                          else {"action": "categorical", "evidence_field": "cardinality", "rationale": "codes",
                                "confidence": 0.9})
    frame = _frame()
    frame.loc[10, "monthly"] = 424242.5  # a raw value that only a sample could carry
    frame["plan_code"] = frame["plan_code"].replace({2: 777001})
    lab_decision_ledger.record_missing_value_decisions(
        db_session, upload.id, frame, plan_missing_values(frame, ["monthly"]), "churn")
    lab_decision_ledger.record_column_type_decisions(db_session, upload.id, frame, ["plan_code"], [])
    db_session.commit()
    assert {call.agent_key for call in ai.fake.calls} == {"missing_value", "column_type"}
    for call in ai.fake.calls:
        assert "424242" not in call.input_json and "777001" not in call.input_json
        assert not {"sample_rows", "sample_values", "cooccurring_values"} & set(sent(call))
        assert all(item["data_class"] in {"metadata", "aggregates"} for item in json.loads(call.input_json)["context"])


def test_leakage_reviewer_goes_through_the_gateway_and_writes_a_ledger_row(upload, db_session, monkeypatch):
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: {
        "availability_status": "unknown", "risk_level": "MEDIUM", "evidence_field": "suspicious_name_tokens",
        "rationale": "the name suggests a result", "confidence": 0.82,
    })
    evidence = LeakageReviewEvidence(
        column="final_score", target="churn", task="binary", dtype="float64", cardinality=100,
        related_column_names=["churn", "monthly"], exact_target_match_fraction=0.1, single_feature_score=0.6,
        single_feature_score_kind="roc_auc", suspicious_name_tokens=["final"], target_name_similarity=0.0,
        identifier_likelihood=0.0, unique_ratio=1.0, missing_fraction=0.0, availability_reason="name token",
    )
    decision = lab_decision_ledger.leakage_reviewer(db_session, upload.id)(evidence)
    assert isinstance(decision, LeakageReviewDecision) and decision.risk_level == "MEDIUM"
    [call] = ai.fake.calls
    assert sent(call)["exact_target_match_fraction"] == 0.1
    db_session.commit()
    [row] = db_session.scalars(select(LlmInvocation).where(LlmInvocation.purpose == "semantic_leakage")).all()
    assert (row.status, row.llm_used, row.decision_point_key, row.agent_role) == (
        "completed", True, "feature.leakage_suspect", "legacy_decision")
    assert row.final_decision["availability_status"] == "unknown" and row.final_decision["source"] == "llm"
    assert row.final_decision["recommended_risk_level"] == "MEDIUM" and "risk_level" not in row.final_decision
    assert row.safe_output == {"availability_status": "unknown", "risk_level": "MEDIUM",
                               "evidence_field": "suspicious_name_tokens", "confidence": 0.82}


def test_one_denied_related_column_does_not_refuse_the_leakage_review(auth_client, db_session, monkeypatch):
    upload = upload_via_api(auth_client, db_session, monkeypatch, _frame(), exposure=None)
    from legacy_ai_support import label_dataset

    label_dataset(db_session, workspace_id=upload.workspace_id, dataset_id=upload.dataset_id,
                  columns={"monthly": "deny"})
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: {
        "availability_status": "unknown", "risk_level": "LOW", "evidence_field": "column",
        "rationale": "unsure", "confidence": 0.9,
    })
    evidence = LeakageReviewEvidence(column="final_score", target="churn", task="binary", dtype="float64",
                                     cardinality=100, related_column_names=["churn", "monthly"],
                                     suspicious_name_tokens=["final"])
    assert lab_decision_ledger.leakage_reviewer(db_session, upload.id)(evidence) is not None
    [call] = ai.fake.calls
    assert "related_column_names" not in sent(call) and "monthly" not in call.input_json


def test_production_blocks_the_legacy_decision_writers(upload, db_session, monkeypatch):
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: dict(MEDIAN))
    ai.settings.dclab_env = "production"  # flag and AI_ENABLED on, but not a development environment
    forbid_gateway(monkeypatch)
    rows = _decide(db_session, upload)
    assert {(llm_used, reason) for _p, _s, reason, llm_used, *_ in rows} <= {
        (False, "LLM used: NO — semantic assistance was disabled or unconfigured."),
        (False, "LLM used: NO — deterministic evidence was sufficient."),
    }
    assert lab_decision_ledger.leakage_reviewer(db_session, upload.id)(
        LeakageReviewEvidence(column="final_score", target="churn", task="binary", dtype="float64",
                              cardinality=2)) is None


def test_leakage_reviewer_with_the_agent_off_never_calls_a_model(db_session, monkeypatch):
    forbid_gateway(monkeypatch)
    reviewer = lab_decision_ledger.leakage_reviewer(db_session, None)
    assert reviewer(LeakageReviewEvidence(column="a", target="b", task="binary", dtype="int64", cardinality=2)) is None


def test_context_requires_attributable_lineage(db_session):
    assert context_for_upload(db_session, None) is None


def test_prompt_files_match_the_output_models_and_the_legacy_modules():
    models = {"missing_value": MissingValueDecision, "column_type": ColumnTypeDecision,
              "target_selection": TargetSelectionDecision, "leakage_review": LeakageReviewDecision,
              "pipeline_auditor": PipelineAuditReport}
    files = {item.agent_key: item for item in discover(PROMPTS_ROOT)}
    assert set(files) >= set(models)
    for key, model in models.items():
        assert files[key].output_schema_digest == output_schema_digest(model), key
    for module, version in ((missing_value_v1, 1), (column_type_v1, 1), (target_selection_v1, 1),
                            (leakage_review_v1, 1), (missing_value_v2, 2), (column_type_v2, 2)):
        assert module.SYSTEM_PROMPT == prompt_text(module.AGENT_KEY, version)
        assert module.PROMPT_VERSION == f"{module.AGENT_KEY}_v{version}" and module.AGENT_KEY in CALLS
    # calls go to the v2 prompts that no longer steer to withheld evidence; v1 rows stay immutable
    assert {key: call.prompt_version for key, call in CALLS.items()} == {
        "missing_value": 2, "column_type": 2, "target_selection": 1, "leakage_review": 1}
    assert "domain_fill" not in missing_value_v2.SYSTEM_PROMPT and "sample_rows" not in missing_value_v2.SYSTEM_PROMPT
    assert "sample_values" not in column_type_v2.SYSTEM_PROMPT
    auditor = prompt_text("pipeline_auditor", 2)
    assert "no final-holdout values" in auditor and "Never state, estimate or infer a" in auditor
    for key in ("missing_value", "column_type", "pipeline_auditor"):
        assert files_by_version(key, 1).output_schema_digest == files_by_version(key, 2).output_schema_digest
    assert {call.purpose for call in CALLS.values()} == {
        "semantic_missing_value", "semantic_column_type", "semantic_target", "semantic_leakage"}


def files_by_version(agent_key: str, version: int):
    return next(item for item in discover(PROMPTS_ROOT) if (item.agent_key, item.version) == (agent_key, version))


def test_withheld_fields_follow_the_no_sample_values_cap():
    # The validators reject citations of sample fields because the caps never let them through.
    assert CAPS.data.sample_values_per_column == 0 and CAPS.data.max_class == "aggregates"
    assert WITHHELD["missing_value"] >= {"sample_rows", "missingness_cooccurrence.other_value"}
    assert WITHHELD["column_type"] == frozenset({"sample_values"})


def test_a_refusal_before_the_gateway_still_records_a_deterministic_row(upload, db_session, monkeypatch):
    enable_legacy_ai(monkeypatch, db_session)
    monkeypatch.setattr("app.agents.legacy.gateway_service", lambda: _NoBudget())
    frame = _frame()
    lab_decision_ledger.record_missing_value_decisions(
        db_session, upload.id, frame, plan_missing_values(frame, ["monthly"]), "churn")
    db_session.commit()
    record = db_session.scalar(select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload.id))
    row = db_session.get(LlmInvocation, record.llm_invocation_id)
    assert (row.status, row.llm_used, row.provider_kind) == ("refused", False, "deterministic_fallback")
    assert row.reason == "LLM used: NO — the AI gateway refused the call (budget_exhausted); rule retained."


class _NoBudget:
    def reserve(self, *_args, **_kwargs):
        return Refusal(code="budget_exhausted", scope="workspace", message="workspace budget exhausted")


def test_user_seed_syncs_the_code_owned_prompt_releases_idempotently(db_session, capsys):
    from app.cli.main import main as dclab_main
    from app.db.models import PromptRelease

    assert dclab_main(["user", "seed"]) == 0
    assert dclab_main(["user", "seed"]) == 0
    capsys.readouterr()
    db_session.expire_all()
    released = db_session.scalars(select(PromptRelease).where(PromptRelease.status == "released")).all()
    keys = sorted(row.agent_key for row in released)
    from app.agents.semantic.releases import RELEASES

    jev = {release.agent_key for release in RELEASES.values()}  # and one per pinned Jev purpose (P6.7-A)
    assert keys == sorted({*CALLS, "pipeline_auditor", *jev})  # one release per prompt file, never duplicated
