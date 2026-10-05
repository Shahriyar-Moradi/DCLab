"""Lab decision-agent wrappers over the AI gateway (ADR 0009 §1, §4, §8).

Fake provider only: these tests never reach a network. The live provider check is
``dclab verify-openai-smoke``.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from sqlalchemy import select

from app.agents.governance.switches import flip_off
from app.agents.legacy import context_for_upload
from app.db.models import LlmInvocation
from app.engine.lab.evidence import ColumnEvidence, MissingnessCooccurrence
from app.engine.lab.llm_client import (
    DecisionAgentUnavailable,
    MissingValueDecisionV3,
    request_decision,
)
from app.engine.lab.prompts.missing_value_v3 import PROMPT_VERSION, SYSTEM_PROMPT
from legacy_ai_support import enable_legacy_ai, forbid_gateway, sent, upload_via_api

SENTINEL = "SENTINEL-RAW-ROW-7731"


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "TotalCharges": [None, None, 12.5, 30.0] * 10,
        "tenure": [0, 0, 5, 9] * 10,
        "Churn": ["No", "Yes"] * 20,
    })


def _evidence() -> ColumnEvidence:
    return ColumnEvidence(
        column="TotalCharges",
        dtype="float64",
        missing_count=3,
        missing_fraction=0.25,
        correlation_with_target=None,
        missingness_cooccurrence=[
            MissingnessCooccurrence(
                other_column="tenure",
                other_value=0,
                missing_and_value_count=3,
                rows_with_value=3,
                fraction_of_missing=1.0,
                fraction_of_value=1.0,
                exact_match=True,
            )
        ],
        sample_rows=[{"TotalCharges": None, "tenure": 0, "Churn": SENTINEL}],
    )


def _valid_payload() -> dict:  # missing_value v3: executed actions only, no fill value
    return {
        "action": "impute_median",
        "evidence_field": "missing_fraction",
        "rationale": "numeric column with a bounded missing fraction",
        "confidence": 0.94,
    }


@pytest.fixture
def run(auth_client, db_session, monkeypatch):
    upload = upload_via_api(auth_client, db_session, monkeypatch, _frame())
    return upload


def _rows(db) -> list[LlmInvocation]:
    db.expire_all()
    return list(db.scalars(select(LlmInvocation).where(LlmInvocation.purpose == "semantic_missing_value")
                           .order_by(LlmInvocation.created_at)))


def test_happy_path_returns_the_validated_decision_through_the_gateway(run, db_session, monkeypatch):
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: _valid_payload())
    context = context_for_upload(db_session, run.id)

    first = request_decision(_evidence(), PROMPT_VERSION, context=context)
    second = request_decision(_evidence(), PROMPT_VERSION, context=context)

    assert first == second == MissingValueDecisionV3(**_valid_payload())
    # legacy rows keep a narrowed summary, so the gateway cache is not used for them
    assert len(ai.fake.calls) == 2
    call = ai.fake.calls[0]
    assert (call.model, call.temperature, call.output_schema) == ("gpt-6-luna", 0.0, MissingValueDecisionV3)
    assert call.instructions.startswith(SYSTEM_PROMPT.rstrip())
    fields = sent(call)
    assert fields["column"] == {"untrusted_text": "TotalCharges"}
    assert fields["missingness_cooccurrence"][0]["exact_match"] is True
    # raw values are sample_values: the accepted policy (0 samples) drops them
    assert {"sample_rows", "cooccurring_values"}.isdisjoint(fields)
    assert "other_value" not in json.dumps(fields) and SENTINEL not in call.input_json
    row = _rows(db_session)[0]
    assert (row.status, row.llm_used, row.provider, row.model) == ("completed", True, "fake", "gpt-6-luna")
    assert (row.agent_role, row.decision_point_key, row.prompt_version) == (
        "legacy_decision", "column.missing_value_action", "missing_value:v3")
    assert (row.data_class, row.outcome_scope, row.experiment_id) == ("aggregates", "none", run.experiment_id)
    assert row.redaction_summary["dropped"]["data_class"] == 2  # above the aggregates ceiling
    assert row.budget_settled and row.cost_micros > 0 and row.input_tokens > 0


def test_unavailable_when_ai_enabled_is_off(run, db_session, monkeypatch):
    enable_legacy_ai(monkeypatch, db_session, ai_enabled=False)
    forbid_gateway(monkeypatch)
    with pytest.raises(DecisionAgentUnavailable, match="disabled"):
        request_decision(_evidence(), PROMPT_VERSION, context=context_for_upload(db_session, run.id))


def test_unavailable_without_an_attributable_run(db_session, monkeypatch):
    enable_legacy_ai(monkeypatch, db_session)
    forbid_gateway(monkeypatch)
    with pytest.raises(DecisionAgentUnavailable, match="attributable"):
        request_decision(_evidence(), PROMPT_VERSION, context=None)


def test_kill_switch_is_a_refusal_row_and_no_provider_call(run, db_session, monkeypatch):
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: _valid_payload())
    flip_off(db_session, workspace_id=run.workspace_id, switch_key="purpose:semantic_missing_value",
             reason="test", actor_rule="test.switch.v1")
    db_session.commit()
    with pytest.raises(DecisionAgentUnavailable, match="kill_switch"):
        request_decision(_evidence(), PROMPT_VERSION, context=context_for_upload(db_session, run.id))
    assert ai.fake.calls == []
    [row] = _rows(db_session)
    assert (row.status, row.refusal_code, row.llm_used, row.provider_kind) == (
        "refused", "kill_switch", False, "deterministic_fallback")


def test_a_denied_column_never_leaves_the_process(auth_client, db_session, monkeypatch):
    upload = upload_via_api(auth_client, db_session, monkeypatch, _frame(), exposure="deny")
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: _valid_payload())
    with pytest.raises(DecisionAgentUnavailable, match="data_class_exceeded"):
        request_decision(_evidence(), PROMPT_VERSION, context=context_for_upload(db_session, upload.id))
    assert ai.fake.calls == []
    assert _rows(db_session)[0].refusal_code == "data_class_exceeded"


def test_unknown_prompt_version_is_unavailable(run, db_session, monkeypatch):
    enable_legacy_ai(monkeypatch, db_session)
    forbid_gateway(monkeypatch)
    with pytest.raises(DecisionAgentUnavailable, match="prompt version"):
        request_decision(_evidence(), "missing_value_v9", context=context_for_upload(db_session, run.id))


def test_production_is_governed_by_switches_and_levels_not_blocked(run, db_session, monkeypatch):
    # P6.9-A lifted the P6.2-B2 block: the writers apply nothing above the decision-point levels.
    ai = enable_legacy_ai(monkeypatch, db_session, handler=lambda _call: _valid_payload())
    ai.settings.dclab_env = "production"
    assert request_decision(_evidence(), PROMPT_VERSION, context=context_for_upload(db_session, run.id)).action == (
        "impute_median")
    assert len(ai.fake.calls) == 1
