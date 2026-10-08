"""P6.8-A: the R3 runner offline, through the real gateway and the fake Jev provider of the
P6.10-B harness: cases about the seeded workspace's registered columns reach the provider
(no raw rows, one ledger row per call), the run's tenant evidence is aggregates only, and
the AI-off ablation equals the rule baseline. No network, no key, no live provider."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from uuid import UUID, uuid4

from ai_harness import check_ledger, check_no_raw_rows
from ai_harness.scenarios import columns, sentinels
from app.agents.governance.policy import GovernanceNotPermitted
from app.agents.semantic.releases import RELEASES, name_tokens
from app.agents.semantic.typesafe_jev import JevSemanticPort
from app.db.models import Dataset, SemanticDecisionAnswer, UserRole, WorkspaceCapability
from app.services.auth_service import create_user
from app.services import r3_evaluation_service as r3
from test_auto_train_decision_points import ON, jev

pytestmark = pytest.mark.ai_harness

FIELDS = {
    "column.is_identifier": {"dtype": "float", "name_tokens": ["x"], "uniqueness": "from_10_to_50pct", "nulls": "none"},
    "column.semantic_role": {"dtype": "float", "cardinality": "high", "value_pattern": "not_text", "nulls": "none"},
    "feature.leakage_suspect": {"dtype": "float", "availability": "before_prediction"},
}
RULES = {"column.is_identifier": False, "column.semantic_role": "numeric", "feature.leakage_suspect": False}


def _cases(source: str, names: list[str], target: str) -> list[r3.Case]:
    out = []
    for name in names:
        for purpose, fields in FIELDS.items():
            if "name_tokens" in fields:
                fields = {**fields, "name_tokens": name_tokens(name)}  # the gateway binds tokens to the stored name
            context = {"target": target, "task": "binary_classification"} if purpose.startswith("feature") else {}
            out.append(r3.Case(dataset="seeded", variant="plain", partition="sealed", purpose=purpose, column=name,
                               fields=fields, context=context, rule=RULES[purpose], label=RULES[purpose],
                               source=source))
    return out


def _designate(hk) -> SimpleNamespace:
    """The platform marker a live target needs: configured id + the platform-owned capability +
    no unsuspended non-platform member (the seeded tenant admin is suspended for this test)."""

    hk.db.add(WorkspaceCapability(workspace_id=hk.ws_a, capability=r3.EVALUATION_CAPABILITY, enabled=True,
                                  configuration={}))
    hk.db.execute(text("UPDATE workspace_memberships SET suspended_at = now() WHERE workspace_id = :w"),
                  {"w": hk.ws_a})
    hk.db.commit()
    return SimpleNamespace(r3_eval_workspace_id=str(hk.ws_a))


def test_r3_runs_offline_through_the_gateway_fake_provider(hk):
    fake = jev({"column.is_identifier": {"*": (0.97, None)}, "column.semantic_role": {"*": ("categorical_code", 0.9)},
                "feature.leakage_suspect": {"*": (0.95, None)}})  # deliberately wrong everywhere
    port = JevSemanticPort(hk.service(providers={"openai": hk.fake, "typesafe": fake}), settings=ON)
    names = sorted(hk.ids)
    source = hk.db.scalar(select(Dataset.name).where(Dataset.id == hk.dataset_a))
    cases = _cases(source, [n for n in names if n != names[-1]], names[-1])
    configured = SimpleNamespace(r3_eval_workspace_id=str(hk.ws_a))
    with pytest.raises(GovernanceNotPermitted):  # the id alone is no platform marker (no capability yet)
        r3.port_answerer(hk.db, port, hk.ws_a, settings=configured, project_id=hk.project_a)
    designated = _designate(hk)
    with pytest.raises(GovernanceNotPermitted):  # a tenant workspace is never the live target
        r3.port_answerer(hk.db, port, hk.ws_b, settings=designated, project_id=hk.project_b)
    answerer = r3.port_answerer(hk.db, port, hk.ws_a, settings=designated, project_id=hk.project_a)
    assert answerer.live is True
    with pytest.raises(GovernanceNotPermitted, match="r3_live_needs_admin"):  # a live run is stored by an admin only
        r3.run_r3(cases, answerer, candidate="fake-gateway", db=hk.db, settings=designated)
    admin = create_user(hk.db, email=f"staff-{uuid4().hex[:6]}@gov.test", password="pw-12345678",
                        role=UserRole.DCLAB_ADMIN, workspace_id=None)
    report = r3.run_r3(cases, answerer, candidate="fake-gateway", db=hk.db, settings=designated, actor=admin)
    hk.db.commit()
    assert report["live"] is True and report["candidate"] == "live:fake-gateway"  # live comes from the answerer
    assert report["evaluation_workspace"] == str(hk.ws_a)
    assert answerer.unregistered == 0  # every case found its (dataset, column); none was silently dropped
    assert r3.load_run(hk.db, UUID(report["run_id"]))["digest"] == report["digest"]  # stored as it happened
    # ADR 0008 §8 through the real path: AI switched off, same cases, the port's value used is the rule
    off_port = JevSemanticPort(hk.service(providers={"openai": hk.fake, "typesafe": fake}, ai_enabled=False),
                               settings=SimpleNamespace(ai_enabled=False, dclab_env="test"))
    off = r3.port_answerer(hk.db, off_port, hk.ws_a, settings=designated, project_id=hk.project_a)
    calls_before = len(fake.calls)
    answers = [a for key in r3.JEV_POINTS for a in off(RELEASES[key], [c for c in cases if c.purpose == key])]
    assert answers and all(a is None for a in answers) and len(fake.calls) == calls_before
    assert off.resolutions and all(res.agreement == "off" and res.value_used == case.rule
                                   for case, res in off.resolutions)
    assert fake.calls, "the cases reached the provider through the gateway"
    calls = len(fake.calls)
    r3.evaluate(cases, answerer)  # R3 asks bypass the answer cache: the repeat reaches the provider again
    assert len(fake.calls) == 2 * calls and not any(
        hk.db.scalars(select(SemanticDecisionAnswer.cache_hit).where(SemanticDecisionAnswer.workspace_id == hk.ws_a)))
    check_no_raw_rows(fake.calls, columns=columns(hk.db, hk.ws_a), **sentinels(hk.db, hk.ws_a))
    check_ledger(hk.db, fake.calls)
    evidence = report["workspace_evidence"]["evaluation"]
    assert set(report["workspace_evidence"]) == {"evaluation"}  # no tenant workspace, no raw ids
    for key in r3.JEV_POINTS:
        point = report["points"][key]
        assert point["sources"]["sealed"]["disagreement_rate"] == 1.0  # the wrong fake disagrees in band
        assert point["sources"]["sealed"]["ai_policy_accuracy"] < point["sources"]["sealed"]["rule_accuracy"]
        assert evidence[key]["answers"]["total"] == len(cases) // 3 and evidence[key]["ledger"]["calls"] >= 1
        assert point["operations"]["p95_latency_ms"] is not None and point["operations"]["calls"] >= 1
        assert not point["promotion"]["L1"]["allowed"] and point["delta_vs_rule_replay"]["ai_off_equals_rule"]
    assert all(v["equal"] for v in report["ablation"]["points"].values())
    assert not re.search(r"holdout|final_test|sample_values|rows", json.dumps(evidence), re.IGNORECASE)
    assert report["points"]["column.semantic_role"]["current_platform_level"] == 0
    assert r3.evaluate(cases, r3.ai_off)["column.semantic_role"]["all"]["unavailable_rate"] == 1.0
