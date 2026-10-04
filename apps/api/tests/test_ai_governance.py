"""AI governance (ADR 0009 §2.5-§2.7, §2.9, §2.11, §3; ADR 0008 §1, §1b, §3; Alembic 0072)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from _alembic_catalog import dump_constraints_triggers
from app.agents.governance import decision_points as dp
from app.agents.governance.platform_default import (
    CAPS,
    PLATFORM_DEFAULT,
    SEED_ACTOR_RULE,
    fake_provider_allowed,
)
from app.agents.governance.policy import (
    GovernanceNotFound,
    GovernanceNotPermitted,
    PolicyCapViolation,
    PolicyConflict,
    PolicyInvalid,
    PolicyUnavailable,
    accept_policy,
    cap_violations,
    document_digest,
    effective_level,
    effective_policy,
    propose_policy,
    accept_level,
    narrow,
    propose_level,
    set_level,
)
from app.agents.governance.seed import seed_platform_governance
from app.agents.governance.switches import (
    SwitchHeldByIncident,
    effective_switches,
    flip_off,
    re_enable,
)
from app.config import Settings
from app.db.models import UserRole, Workspace
from app.services.auth_service import create_user
from app.services.authorization_service import can_approve_ai_policy
from app.services.project_service import create_project
from test_agent_persistence import _insert, _rejects, _sql
from test_historical_alembic_revisions import _assert_head_catalog, _drop_isolated, _isolated_database

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "alembic_head_constraints_triggers.json"
TABLES = ("ai_policies", "decision_point_policies", "workspace_llm_budgets", "ai_incidents", "ai_switches")
FUNCTIONS = (
    "enforce_ai_governance_scope",
    "enforce_decision_point_policy",
    "enforce_ai_incident_update",
    "enforce_agent_proposal_transition",
    "guard_llm_invocation_ledger",
    "enforce_ai_policy_decision",
)


def _policy(**changes) -> dict:
    doc = PLATFORM_DEFAULT.model_dump(mode="json")
    for path, value in changes.items():
        node = doc
        *parents, leaf = path.split("__")
        for part in parents:
            node = node[part]
        node[leaf] = value
    return doc


@pytest.fixture
def gov(db_session):
    db = db_session
    seed_platform_governance(db)
    ns = SimpleNamespace()
    for side in ("a", "b"):
        ws = Workspace(slug=f"gov-{side}-{uuid4().hex[:8]}", name=f"Gov {side}")
        db.add(ws)
        db.flush()
        setattr(ns, f"ws_{side}", ws.id)

    def user(role, ws, tag):
        return create_user(db, email=f"{tag}-{uuid4().hex[:6]}@gov.test", password="pw-12345678",
                           role=role, workspace_id=ws)

    ns.owner = user(UserRole.WORKSPACE_OWNER, ns.ws_a, "owner")
    ns.admin = user(UserRole.WORKSPACE_ADMIN, ns.ws_a, "admin")
    ns.engineer = user(UserRole.ML_ENGINEER, ns.ws_a, "eng")
    ns.owner_b = user(UserRole.WORKSPACE_OWNER, ns.ws_b, "ownerb")
    ns.platform_admin = user(UserRole.DCLAB_ADMIN, None, "staff")
    ns.project = create_project(db, actor=ns.owner, workspace_id=ns.ws_a, name="Gov").id
    ns.release = _insert(db, "prompt_releases", {
        "agent_key": "jev:column.semantic_role", "version": 1, "prompt_digest": "a" * 64,
        "output_schema_digest": "b" * 64, "status": "released", "released_at": datetime.now(UTC),
    })
    db.commit()
    return ns


# --- migration ------------------------------------------------------------------------


def test_create_all_helpers_match_alembic_0072_catalog(test_engine):
    def subset(dump: dict) -> dict:
        return {
            "checks": [i for i in dump["check_constraints"] if i["table"] in TABLES],
            "triggers": [i for i in dump["triggers"] if i["table"] in TABLES],
            "functions": [i for i in dump["functions"] if i["name"] in FUNCTIONS],
        }

    expected = subset(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert len(expected["triggers"]) == 12 and len(expected["functions"]) == len(FUNCTIONS)
    assert subset(dump_constraints_triggers(test_engine)) == expected


def test_0072_upgrade_downgrade_upgrade(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from71"
    )
    try:
        command.upgrade(alembic_config, "0071_agent_runs")
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            with Session(engine) as session:
                assert seed_platform_governance(session) == {"policy_created": True, "switch_created": True}
                session.commit()
            # Seed rows alone do not block the downgrade; any other governance row does.
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO ai_incidents (id, kind, subject_kind, subject_key, action, status) "
                    "VALUES (gen_random_uuid(), 'manual', 'provider', 'openai', 'none', 'open')"
                ))
            with pytest.raises(Exception, match="0072 downgrade refused"):
                command.downgrade(alembic_config, "0071_agent_runs")
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE ai_incidents CASCADE"))
            command.downgrade(alembic_config, "0071_agent_runs")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT to_regclass('ai_policies')")).scalar() is None
                bodies = dict(connection.execute(text(
                    "SELECT proname, prosrc FROM pg_proc WHERE proname IN "
                    "('enforce_agent_proposal_transition', 'guard_llm_invocation_ledger')"
                )).all())
            assert "cannot be extended" not in bodies["enforce_agent_proposal_transition"]
            assert "'currency'" not in bodies["guard_llm_invocation_ledger"]
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


# --- seed, constants, registry ------------------------------------------------------


def test_seed_is_idempotent_and_platform_default_is_within_caps(db_session, gov):
    assert seed_platform_governance(db_session) == {"policy_created": False, "switch_created": False}
    db_session.commit()
    count = _sql(db_session, "SELECT count(*) FROM ai_policies WHERE workspace_id IS NULL").scalar()
    assert count == 1
    assert cap_violations(PLATFORM_DEFAULT, CAPS) == []
    roles = PLATFORM_DEFAULT.models.roles
    assert (roles.lead.default, roles.specialist.per_agent, roles.legacy_decision.default,
            roles.jev.default) == ("gpt-6.1-sol", {"dataset_investigator": "gpt-6-luna"}, "gpt-6-luna",
                                   "jev-1.13.0")
    assert PLATFORM_DEFAULT.budgets.workspace_month_micros == 25_000_000
    assert PLATFORM_DEFAULT.data.max_class == "aggregates" and not PLATFORM_DEFAULT.data.user_text_to_jev
    assert Settings().ai_enabled is False
    assert not fake_provider_allowed("production") and fake_provider_allowed("development")
    # Fail safe: only named development environments count as non-production.
    assert not any(fake_provider_allowed(env) for env in ("prod", "PROD ", "staging", ""))
    assert all(fake_provider_allowed(env) for env in ("dev", "test", "local"))


def test_registry_invariants():
    assert dp.registry_violations() == []
    assert all(p.default_level == 0 and p.outcome_scope in ("none", "cv") for p in dp.REGISTRY.values())
    assert all(p.cap <= 1 for k, p in dp.REGISTRY.items() if k.startswith("lead."))
    assert dp.answer_ceiling("column.semantic_role", "role_numeric_categorical") == 2
    assert dp.answer_ceiling("column.semantic_role", "exclusion") == 1
    assert dp.answer_ceiling("column.semantic_role") == 1
    assert dp.answer_ceiling("training.families_budget", "fixed_hyperparameter") == 1
    assert dp.answer_ceiling("ops.diagnose") == 3
    assert dp.answer_ceiling("no.such_point") == 0


# --- authority ------------------------------------------------------------------------


def test_can_approve_ai_policy(db_session, gov):
    db = db_session
    assert can_approve_ai_policy(db, gov.owner, gov.ws_a)
    assert can_approve_ai_policy(db, gov.admin, gov.ws_a)
    assert not can_approve_ai_policy(db, gov.engineer, gov.ws_a)
    assert not can_approve_ai_policy(db, gov.owner_b, gov.ws_a)
    assert not can_approve_ai_policy(db, gov.platform_admin, gov.ws_a)
    legacy = create_user(db, email=f"legacy-{uuid4().hex[:6]}@gov.test", password="pw-12345678",
                         role=UserRole.CLIENT_USER, workspace_id=gov.ws_a)
    _sql(db, "DELETE FROM workspace_memberships WHERE user_id = :u", u=legacy.id)
    _sql(db, "UPDATE workspace_memberships SET suspended_at = now() WHERE user_id = :u", u=gov.admin.id)
    db.commit()
    assert not can_approve_ai_policy(db, legacy, gov.ws_a)
    assert not can_approve_ai_policy(db, gov.admin, gov.ws_a)


# --- policies ---------------------------------------------------------------------------


def test_effective_policy_merges_and_is_tenant_isolated(db_session, gov):
    db = db_session
    base = effective_policy(db, gov.ws_a)
    assert base.policy == PLATFORM_DEFAULT and base.workspace_version is None
    proposal = propose_policy(db, actor=gov.engineer, workspace_id=gov.ws_a,
                              policy=_policy(limits__specialist__calls=1, budgets__hard_stop=True),
                              rationale="tighter")
    accepted = accept_policy(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=proposal.id)
    db.commit()
    assert not accepted.self_approved and accepted.version == 2 and accepted.base_version is None
    merged = effective_policy(db, gov.ws_a)
    assert merged.policy.limits.specialist.calls == 1 and merged.digest != base.digest
    assert effective_policy(db, gov.ws_b).policy == PLATFORM_DEFAULT
    # A stored document wider than the bound (written around the service) is clamped at read.
    wide = _policy(limits__specialist__calls=99, data__max_class="sample_values")
    _insert(db, "ai_policies", {
        "workspace_id": gov.ws_b, "version": 1, "state": "proposed", "policy": wide,
        "policy_digest": document_digest(wide), "schema_version": 1, "change_kind": "policy",
        "rationale": "raw", "proposed_by_user_id": gov.owner_b.id,
    })
    db.commit()
    proposal_id = _sql(db, "SELECT id FROM ai_policies WHERE workspace_id = :w", w=gov.ws_b).scalar()
    _insert(db, "ai_policies", {
        "workspace_id": gov.ws_b, "version": 2, "state": "accepted", "policy": wide,
        "policy_digest": document_digest(wide), "schema_version": 1, "change_kind": "policy",
        "rationale": "raw", "proposed_by_user_id": gov.owner_b.id, "decided_by_user_id": gov.owner_b.id,
        "supersedes_id": proposal_id, "self_approved": True,
    })
    db.commit()
    clamped = effective_policy(db, gov.ws_b).policy
    assert clamped.limits.specialist.calls == 2 and clamped.data.max_class == "aggregates"


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"data__max_class": "sample_values"}, "data.max_class"),
        ({"models__roles__lead__allowed": ["gpt-6.1-sol", "gpt-9"]}, "models.roles.lead.allowed"),
        ({"limits__assistant_turn__steps": 9}, "limits.assistant_turn.steps"),
        ({"budgets__workspace_month_micros": 26_000_000}, "budgets.workspace_month_micros"),
        ({"autonomy__ops__auto_release": True}, "autonomy.ops.auto_release"),
        ({"incidents__validator_rejections_24h": 10}, "incidents.validator_rejections_24h"),
        ({"incidents__revert_rate_30d": 0.2}, "incidents.revert_rate_30d"),
        ({"budgets__hard_stop": False}, "budgets.hard_stop"),
    ],
)
def test_caps_only_narrow(db_session, gov, changes, path):
    with pytest.raises(PolicyCapViolation) as exc:
        propose_policy(db_session, actor=gov.owner, workspace_id=gov.ws_a, policy=_policy(**changes),
                       rationale="wider")
    assert any(v.startswith(path) for v in exc.value.violations)


def test_invalid_documents_and_authority(db_session, gov):
    with pytest.raises(PolicyInvalid):
        propose_policy(db_session, actor=gov.owner, workspace_id=gov.ws_a,
                       policy=_policy(data__max_class="raw_rows"), rationale="x")
    with pytest.raises(GovernanceNotPermitted):
        propose_policy(db_session, actor=gov.owner_b, workspace_id=gov.ws_a, policy=_policy(), rationale="x")
    proposal = propose_policy(db_session, actor=gov.engineer, workspace_id=gov.ws_a, policy=_policy(),
                              rationale="x")
    with pytest.raises(GovernanceNotPermitted):
        accept_policy(db_session, approver=gov.engineer, workspace_id=gov.ws_a, proposal_id=proposal.id)
    with pytest.raises(GovernanceNotFound):  # another workspace's proposal does not exist for B
        accept_policy(db_session, approver=gov.owner_b, workspace_id=gov.ws_b, proposal_id=proposal.id)


def test_effective_policy_fails_closed(db_session, gov):
    db = db_session
    broken = {"schema_version": 1, "models": {}}
    for policy, digest in ((broken, document_digest(broken)), (_policy(), "0" * 64)):
        ws = Workspace(slug=f"gov-x-{uuid4().hex[:8]}", name="X")
        db.add(ws)
        db.flush()
        pid = _insert(db, "ai_policies", {
            "workspace_id": ws.id, "version": 1, "state": "proposed", "policy": policy,
            "policy_digest": digest, "schema_version": 1, "change_kind": "policy", "rationale": "r",
            "proposed_by_user_id": gov.owner.id,
        })
        _insert(db, "ai_policies", {
            "workspace_id": ws.id, "version": 2, "state": "accepted", "policy": policy,
            "policy_digest": digest, "schema_version": 1, "change_kind": "policy", "rationale": "r",
            "proposed_by_user_id": gov.owner.id, "decided_by_user_id": gov.owner.id, "supersedes_id": pid,
            "self_approved": True,
        })
        db.commit()
        with pytest.raises(PolicyUnavailable):
            effective_policy(db, ws.id)
    _sql(db, "TRUNCATE ai_policies CASCADE")
    db.commit()
    with pytest.raises(PolicyUnavailable):
        effective_policy(db, gov.ws_a)


def test_base_version_conflict_and_single_decision(db_session, gov):
    db = db_session
    first = propose_policy(db, actor=gov.engineer, workspace_id=gov.ws_a,
                           policy=_policy(proposals__ttl_days=5), rationale="a")
    second = propose_policy(db, actor=gov.engineer, workspace_id=gov.ws_a,
                            policy=_policy(proposals__ttl_days=6), rationale="b")
    accept_policy(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=first.id)
    db.commit()
    with pytest.raises(PolicyConflict):
        accept_policy(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=second.id)
    with pytest.raises(PolicyConflict, match="already_decided"):
        accept_policy(db, approver=gov.admin, workspace_id=gov.ws_a, proposal_id=first.id)


def test_self_approval_only_for_a_sole_approver(db_session, gov):
    db = db_session
    own = propose_policy(db, actor=gov.owner, workspace_id=gov.ws_a, policy=_policy(), rationale="mine")
    with pytest.raises(GovernanceNotPermitted, match="self_approval_not_allowed"):
        accept_policy(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=own.id)
    db.rollback()
    _sql(db, "UPDATE workspace_memberships SET suspended_at = now() WHERE user_id = :u", u=gov.admin.id)
    db.commit()
    own = propose_policy(db, actor=gov.owner, workspace_id=gov.ws_a, policy=_policy(), rationale="mine")
    row = accept_policy(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=own.id)
    db.commit()
    assert row.self_approved


def test_policy_chains_are_append_only(db_session, gov):
    for table in ("ai_policies", "ai_switches"):
        _rejects(db_session, "immutable", _sql, db_session, f"UPDATE {table} SET created_at = now()")
        _rejects(db_session, "immutable", _sql, db_session, f"DELETE FROM {table}")
    _rejects(db_session, "ck_ai_policies_seed", _insert, db_session, "ai_policies", {
        "workspace_id": gov.ws_a, "version": 1, "state": "accepted", "policy": _policy(),
        "policy_digest": document_digest(_policy()), "schema_version": 1, "change_kind": "seed",
        "rationale": "r",
    })


# --- decision-point levels ------------------------------------------------------------


def _level_row(db, gov, **values):
    base = {
        "workspace_id": gov.ws_a, "decision_point_key": "column.semantic_role", "level": 0,
        "cap_level": 2, "state": "accepted", "actor_kind": "human", "actor_user_id": gov.owner.id,
        "decided_by_user_id": gov.owner.id, "rationale": "r",
    }
    return _insert(db, "decision_point_policies", {**base, **values})


def _promote(db, gov, key, level, pair):
    proposal = propose_level(db, actor=gov.engineer, workspace_id=gov.ws_a, key=key, level=level,
                             rationale="evidence", **pair)
    row = accept_level(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=proposal.id)
    db.commit()
    return row


def test_levels_rules_only_demote_and_promotions_need_a_human(db_session, gov):
    db = db_session
    pair = {"prompt_release_id": gov.release, "model_id": "jev-1.13.0"}
    with pytest.raises(Exception, match="rule_only_down"):
        set_level(db, workspace_id=gov.ws_a, key="column.semantic_role", level=1, rationale="r",
                  actor_rule="governance.auto_demote.v1", **pair)
    db.rollback()
    top = _promote(db, gov, "column.semantic_role", 2, pair)
    _rejects(db, "rule_only_down", _level_row, db, gov, level=2, actor_kind="rule", actor_user_id=None,
             decided_by_user_id=None, actor_rule="governance.auto_demote.v1", supersedes_id=top.id, **pair)
    _rejects(db, "promotion needs a human decider", _level_row, db, gov, level=2, supersedes_id=None,
             decision_point_key="column.missing_value_action", decided_by_user_id=None, **pair)
    _rejects(db, "ck_dpp_level", _level_row, db, gov, level=3, supersedes_id=top.id, **pair)
    _rejects(db, "ck_dpp_pair", _level_row, db, gov, level=1, supersedes_id=top.id)
    _rejects(db, "uq_dpp_chain_root", _level_row, db, gov, level=0)
    down = set_level(db, workspace_id=gov.ws_a, key="column.semantic_role", level=1, rationale="down",
                     actor_rule="governance.auto_demote.v1", **pair)
    db.commit()
    assert down.supersedes_id == top.id
    with pytest.raises(GovernanceNotPermitted):
        set_level(db, workspace_id=gov.ws_a, key="column.semantic_role", level=0, rationale="x",
                  actor=gov.engineer)
    _rejects(db, "immutable", _sql, db, "UPDATE decision_point_policies SET level = 0")


def test_effective_level_is_the_minimum_and_pair_bound(db_session, gov):
    db = db_session
    pair = {"prompt_release_id": gov.release, "model_id": "jev-1.13.0"}
    key = "column.semantic_role"
    assert effective_level(db, gov.ws_a, key, "role_numeric_categorical", **pair) == 0  # no rows
    # Platform level (P6.8-A writes it from R3; a platform admin is the human decider).
    _level_row(db, gov, workspace_id=None, level=2, actor_user_id=gov.platform_admin.id,
               decided_by_user_id=gov.platform_admin.id, **pair)
    db.commit()
    assert effective_level(db, gov.ws_a, key, "role_numeric_categorical", **pair) == 0  # no ws row
    _promote(db, gov, key, 2, pair)
    assert effective_level(db, gov.ws_a, key, "role_numeric_categorical", **pair) == 2
    assert effective_level(db, gov.ws_a, key, "exclusion", **pair) == 1
    assert effective_level(db, gov.ws_a, key, None, **pair) == 1
    assert effective_level(db, gov.ws_a, key, "role_numeric_categorical",
                           prompt_release_id=gov.release, model_id="gpt-6-luna") == 0
    assert effective_level(db, gov.ws_a, key, "role_numeric_categorical") == 0
    assert effective_level(db, gov.ws_b, key, "role_numeric_categorical", **pair) == 0
    set_level(db, workspace_id=gov.ws_a, key=key, level=0, rationale="off", actor=gov.admin)
    db.commit()
    assert effective_level(db, gov.ws_a, key, "role_numeric_categorical", **pair) == 0


def test_platform_and_workspace_rows_never_reference_each_other(db_session, gov):
    db = db_session
    pair = {"prompt_release_id": gov.release, "model_id": "jev-1.13.0"}
    platform = _level_row(db, gov, workspace_id=None, level=1, actor_user_id=gov.platform_admin.id,
                          decided_by_user_id=gov.platform_admin.id, **pair)
    workspace = _level_row(db, gov, level=1, **pair)
    db.commit()
    _rejects(db, "same workspace scope", _level_row, db, gov, level=0, supersedes_id=platform)
    _rejects(db, "same workspace scope", _level_row, db, gov, workspace_id=None, level=0,
             supersedes_id=workspace)
    _rejects(db, "same workspace scope", _level_row, db, gov, workspace_id=gov.ws_b, level=0,
             supersedes_id=workspace)
    _rejects(db, "keep the same decision_point_key", _level_row, db, gov, level=0,
             decision_point_key="column.missing_value_action", supersedes_id=workspace)
    platform_switch = flip_off(db, workspace_id=None, switch_key="agent:lead", reason="r",
                               actor=gov.platform_admin)
    db.commit()
    switch = {"switch_key": "agent:lead", "state": "on", "changed_by_user_id": gov.owner.id, "reason": "r"}
    _rejects(db, "same workspace scope", _insert, db, "ai_switches",
             {**switch, "workspace_id": gov.ws_a, "supersedes_id": platform_switch.id})
    incident = _insert(db, "ai_incidents", {"workspace_id": gov.ws_a, "kind": "manual",
                                             "subject_kind": "agent", "subject_key": "lead",
                                             "action": "switch_off", "status": "open"})
    db.commit()
    _rejects(db, "same workspace scope", _insert, db, "ai_switches",
             {**switch, "state": "off", "workspace_id": None, "switch_key": "agent:critic",
              "incident_id": incident})
    _rejects(db, "same workspace scope", _sql, db,
             "UPDATE ai_incidents SET action_switch_id = :s WHERE id = :i", s=platform_switch.id, i=incident)
    _rejects(db, "same workspace scope", _sql, db,
             "UPDATE ai_incidents SET action_level_policy_id = :p WHERE id = :i", p=platform, i=incident)


# --- switches ---------------------------------------------------------------------------


def test_switches_off_always_wins_and_precedence(db_session, gov):
    db = db_session
    switches = effective_switches(db, gov.ws_a)
    assert switches.blocking(ai_enabled=False) == "setting:AI_ENABLED"
    assert switches.blocking(ai_enabled=True, agent_key="lead", provider="openai", purpose="assistant.turn") is None
    flip_off(db, workspace_id=gov.ws_a, switch_key="purpose:assistant.turn", reason="noisy", actor=gov.admin)
    flip_off(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="pause", actor=gov.owner)
    flip_off(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="again", actor=gov.owner)  # always ok
    db.commit()
    switches = effective_switches(db, gov.ws_a)
    assert switches.blocking(ai_enabled=True, purpose="assistant.turn") == "workspace:all_ai"
    assert effective_switches(db, gov.ws_b).blocking(ai_enabled=True, purpose="assistant.turn") is None
    re_enable(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="back", actor=gov.admin)
    db.commit()
    assert effective_switches(db, gov.ws_a).blocking(
        ai_enabled=True, purpose="assistant.turn") == "workspace:purpose:assistant.turn"
    with pytest.raises(GovernanceNotPermitted):
        flip_off(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="x", actor=gov.engineer)
    with pytest.raises(GovernanceNotPermitted):
        flip_off(db, workspace_id=None, switch_key="global_ai", reason="x", actor=gov.owner)
    flip_off(db, workspace_id=None, switch_key="global_ai", reason="incident", actor=gov.platform_admin)
    db.commit()
    assert effective_switches(db, gov.ws_b).blocking(ai_enabled=True) == "platform:global_ai"
    _rejects(db, "ck_ai_switches_key", _insert, db, "ai_switches", {
        "workspace_id": gov.ws_a, "switch_key": "global_ai", "state": "off",
        "changed_by_user_id": gov.owner.id, "reason": "r"})


def test_incident_held_switch_stays_off_until_resolved(db_session, gov):
    db = db_session
    incident = _insert(db, "ai_incidents", {"workspace_id": gov.ws_a, "kind": "data_exposure",
                                             "subject_kind": "workspace", "subject_key": "all",
                                             "action": "switch_off", "status": "open"})
    off = flip_off(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="exposure",
                   actor_rule="incidents.auto_switch_off.v1", incident_id=incident)
    _sql(db, "UPDATE ai_incidents SET action_switch_id = :s WHERE id = :i", s=off.id, i=incident)
    db.commit()
    with pytest.raises(SwitchHeldByIncident):
        re_enable(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="nope", actor=gov.owner)
    # Even an `on` row written around the service does not re-enable a held key.
    _insert(db, "ai_switches", {"workspace_id": gov.ws_a, "switch_key": "all_ai", "state": "on",
                                "changed_by_user_id": gov.owner.id, "reason": "raw", "supersedes_id": off.id})
    db.commit()
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True) == "workspace:all_ai"
    _sql(db, "UPDATE ai_incidents SET status = 'resolved', resolved_at = now(), resolved_by_user_id = :u, "
         "resolution = 'fixed' WHERE id = :i", u=gov.owner.id, i=incident)
    db.commit()
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True) is None
    re_enable(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="resolved", actor=gov.owner)
    db.commit()


def test_incident_update_rules(db_session, gov):
    db = db_session
    incident = _insert(db, "ai_incidents", {"workspace_id": gov.ws_a, "kind": "manual",
                                             "subject_kind": "provider", "subject_key": "openai",
                                             "action": "none", "status": "open"})
    db.commit()
    _rejects(db, "only status and resolution", _sql, db,
             "UPDATE ai_incidents SET kind = 'drift' WHERE id = :i", i=incident)
    _rejects(db, "ck_ai_incidents_resolved", _sql, db,
             "UPDATE ai_incidents SET status = 'resolved' WHERE id = :i", i=incident)
    _sql(db, "UPDATE ai_incidents SET status = 'closed', resolved_at = now() WHERE id = :i", i=incident)
    db.commit()
    _rejects(db, "illegal status change", _sql, db,
             "UPDATE ai_incidents SET status = 'resolved' WHERE id = :i", i=incident)
    _rejects(db, "write-once", _sql, db, "UPDATE ai_incidents SET resolved_at = now() WHERE id = :i",
             i=incident)
    _rejects(db, "immutable", _sql, db, "DELETE FROM ai_incidents WHERE id = :i", i=incident)


# --- budgets ----------------------------------------------------------------------------


def test_budget_counters_non_negative_unique_and_identity_frozen(db_session, gov):
    db = db_session
    row = {"workspace_id": gov.ws_a, "scope": "workspace", "period": "month",
           "period_start": date(2026, 10, 1), "limit_micros": 25_000_000}
    budget = _insert(db, "workspace_llm_budgets", row)
    _insert(db, "workspace_llm_budgets", {**row, "scope": "project", "project_id": gov.project})
    _insert(db, "workspace_llm_budgets", {**row, "scope": "run_kind", "run_kind": "assistant_turn",
                                          "period": "run", "limit_micros": 250_000})
    _insert(db, "workspace_llm_budgets", {**row, "workspace_id": gov.ws_b})
    db.commit()
    _rejects(db, "uq_workspace_llm_budgets_scope", _insert, db, "workspace_llm_budgets", row)
    _rejects(db, "ck_workspace_llm_budgets_shape", _insert, db, "workspace_llm_budgets",
             {**row, "scope": "project"})
    _rejects(db, "fk_workspace_llm_budgets_workspace_project", _insert, db, "workspace_llm_budgets",
             {**row, "workspace_id": gov.ws_b, "scope": "project", "project_id": gov.project})
    _sql(db, "UPDATE workspace_llm_budgets SET reserved_micros = 10, spent_micros = 5, calls = 1 "
         "WHERE id = :id", id=budget)
    db.commit()
    for assignment in ("reserved_micros = -1", "spent_micros = -1", "calls = -1"):
        _rejects(db, "ck_workspace_llm_budgets_amounts", _sql, db,
                 f"UPDATE workspace_llm_budgets SET {assignment} WHERE id = :id", id=budget)
    _rejects(db, "is immutable", _sql, db,
             "UPDATE workspace_llm_budgets SET period = 'day' WHERE id = :id", id=budget)


def test_seed_rows_carry_the_seed_actor(db_session, gov):
    actor = _sql(db_session, "SELECT actor_rule, state FROM ai_switches WHERE workspace_id IS NULL "
                 "AND switch_key = 'global_ai'").one()
    assert actor == (SEED_ACTOR_RULE, "on")


# --- review fixes (P6.2-A2) -----------------------------------------------------------


def test_item1_global_ai_absent_in_production_rules_never_switch_on_and_cli(db_session, gov):
    db = db_session
    _sql(db, "TRUNCATE ai_switches CASCADE")
    db.commit()
    assert seed_platform_governance(db, environment="production")["switch_created"] is False
    for env in ("prod", "staging", ""):
        assert seed_platform_governance(db, environment=env)["switch_created"] is False
    db.commit()
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True) == "platform:global_ai"
    _rejects(db, "ck_ai_switches_rule_off", _insert, db, "ai_switches", {
        "switch_key": "global_ai", "state": "on", "actor_rule": "incidents.auto_switch_off.v1",
        "reason": "r"})
    from app.cli.main import main

    assert main(["governance", "switch", "on", "global_ai", "--admin", gov.platform_admin.email,
                 "--reason", "launch"]) == 0
    db.expire_all()
    row = _sql(db, "SELECT state, changed_by_user_id, actor_rule FROM ai_switches "
               "WHERE workspace_id IS NULL AND switch_key = 'global_ai'").one()
    assert row == ("on", gov.platform_admin.id, None)
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True) is None
    with pytest.raises(GovernanceNotPermitted):  # a workspace owner is not a platform admin
        main(["governance", "switch", "off", "global_ai", "--admin", gov.owner.email, "--reason", "x"])


def test_item2_levels_rise_only_through_an_accepted_proposal(db_session, gov):
    db = db_session
    key, pair = "column.semantic_role", {"prompt_release_id": gov.release, "model_id": "jev-1.13.0"}
    with pytest.raises(GovernanceNotPermitted, match="level_raise_needs_approval"):
        set_level(db, workspace_id=gov.ws_a, key=key, level=1, rationale="up", actor=gov.owner, **pair)
    db.rollback()
    with pytest.raises(GovernanceNotPermitted, match="platform_levels_not_supported"):
        propose_level(db, actor=gov.owner, workspace_id=None, key=key, level=1, rationale="x", **pair)
    stale = propose_level(db, actor=gov.engineer, workspace_id=gov.ws_a, key=key, level=1, rationale="a", **pair)
    own = propose_level(db, actor=gov.owner, workspace_id=gov.ws_a, key=key, level=2, rationale="b", **pair)
    db.commit()
    with pytest.raises(GovernanceNotPermitted, match="self_approval_not_allowed"):
        accept_level(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=own.id)
    db.rollback()
    with pytest.raises(GovernanceNotPermitted):
        accept_level(db, approver=gov.engineer, workspace_id=gov.ws_a, proposal_id=stale.id)
    db.rollback()
    accepted = accept_level(db, approver=gov.admin, workspace_id=gov.ws_a, proposal_id=own.id)
    db.commit()
    assert (accepted.level, accepted.actor_user_id, accepted.decided_by_user_id, accepted.self_approved) == (
        2, gov.owner.id, gov.admin.id, False)
    with pytest.raises(PolicyConflict):  # its base is no longer the head
        accept_level(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=stale.id)
    db.rollback()
    # A changed evidence pair above L0 is a raise, even at the same level.
    with pytest.raises(GovernanceNotPermitted, match="level_raise_needs_approval"):
        set_level(db, workspace_id=gov.ws_a, key=key, level=2, rationale="swap", actor=gov.owner,
                  prompt_release_id=gov.release, model_id="gpt-6-luna")
    db.rollback()
    set_level(db, workspace_id=gov.ws_a, key=key, level=1, rationale="down", actor=gov.owner, **pair)
    _sql(db, "UPDATE workspace_memberships SET suspended_at = now() WHERE user_id = :u", u=gov.admin.id)
    db.commit()
    solo = propose_level(db, actor=gov.owner, workspace_id=gov.ws_a, key=key, level=2, rationale="c", **pair)
    assert accept_level(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=solo.id).self_approved
    db.commit()


def test_item3_a_new_evidence_pair_is_a_promotion_in_sql(db_session, gov):
    db = db_session
    pair = {"prompt_release_id": gov.release, "model_id": "jev-1.13.0"}
    top = _promote(db, gov, "column.semantic_role", 2, pair)
    swapped = {"prompt_release_id": gov.release, "model_id": "gpt-6-luna"}
    _rejects(db, "rule_only_down", _level_row, db, gov, level=1, actor_kind="rule", actor_user_id=None,
             decided_by_user_id=None, actor_rule="governance.auto_demote.v1", supersedes_id=top.id, **swapped)
    _rejects(db, "promotion needs a human decider", _level_row, db, gov, level=2, decided_by_user_id=None,
             supersedes_id=top.id, **swapped)
    _level_row(db, gov, level=1, actor_kind="rule", actor_user_id=None, decided_by_user_id=None,
               actor_rule="governance.auto_demote.v1", supersedes_id=top.id, **pair)
    db.commit()


def test_item4_policy_decisions_restate_their_proposal(db_session, gov):
    db = db_session
    proposal = propose_policy(db, actor=gov.engineer, workspace_id=gov.ws_a, policy=_policy(), rationale="p")
    db.commit()
    accepted_row = {
        "workspace_id": gov.ws_a, "version": proposal.version + 1, "state": "accepted",
        "policy": proposal.policy, "policy_digest": proposal.policy_digest, "schema_version": 1,
        "change_kind": "policy", "rationale": "p", "proposed_by_user_id": gov.engineer.id,
        "decided_by_user_id": gov.owner.id, "supersedes_id": proposal.id,
    }
    other = _policy(proposals__ttl_days=3)
    for changes in (
        {"policy": other, "policy_digest": document_digest(other)},
        {"base_version": 1},
        {"change_kind": "budget"},
        {"proposed_by_user_id": gov.owner.id},
    ):
        _rejects(db, "must restate its proposal", _insert, db, "ai_policies", {**accepted_row, **changes})
    _rejects(db, "later version", _insert, db, "ai_policies", {**accepted_row, "version": proposal.version})
    _rejects(db, "must supersede a proposed row", _insert, db, "ai_policies", {
        **accepted_row, "supersedes_id": accept_policy(
            db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=proposal.id).id,
        "version": proposal.version + 5})
    own = propose_policy(db, actor=gov.owner, workspace_id=gov.ws_a, policy=_policy(), rationale="o")
    db.commit()
    _rejects(db, "self_approved", _insert, db, "ai_policies", {
        **accepted_row, "supersedes_id": own.id, "version": own.version + 1, "base_version": own.base_version,
        "proposed_by_user_id": gov.owner.id, "decided_by_user_id": gov.owner.id, "rationale": "o"})


def test_item5_incident_resolution_and_links(db_session, gov):
    db = db_session
    incident = _insert(db, "ai_incidents", {"workspace_id": gov.ws_a, "kind": "manual",
                                             "subject_kind": "workspace", "subject_key": "all",
                                             "action": "switch_off", "status": "open"})
    stray = flip_off(db, workspace_id=gov.ws_a, switch_key="agent:lead", reason="r", actor=gov.owner)
    db.commit()
    _rejects(db, "must cite this incident", _sql, db,
             "UPDATE ai_incidents SET action_switch_id = :s WHERE id = :i", s=stray.id, i=incident)
    level = _level_row(db, gov, level=0)
    _rejects(db, "ck_ai_incidents_action_links", _insert, db, "ai_incidents", {
        "workspace_id": gov.ws_a, "kind": "manual", "subject_kind": "workspace", "subject_key": "all",
        "action": "switch_off", "status": "open", "action_level_policy_id": level})
    _rejects(db, "ck_ai_incidents_switch_off_resolver", _sql, db,
             "UPDATE ai_incidents SET status = 'resolved', resolved_at = now() WHERE id = :i", i=incident)
    _sql(db, "UPDATE ai_incidents SET status = 'resolved', resolved_at = now(), resolved_by_user_id = :u, "
         "resolution = 'fixed' WHERE id = :i", u=gov.owner.id, i=incident)
    db.commit()
    _rejects(db, "write-once", _sql, db,
             "UPDATE ai_incidents SET resolution = 'rewritten' WHERE id = :i", i=incident)


def test_item6_platform_admin_may_switch_a_workspace_off_never_on(db_session, gov):
    db = db_session
    flip_off(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="abuse", actor=gov.platform_admin)
    db.commit()
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True) == "workspace:all_ai"
    with pytest.raises(GovernanceNotPermitted):
        re_enable(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="x", actor=gov.platform_admin)
    db.rollback()
    re_enable(db, workspace_id=gov.ws_a, switch_key="all_ai", reason="ok", actor=gov.owner)
    db.commit()


def test_item7_rollback_retention_and_ttl_only_narrow(db_session, gov):
    wide = PLATFORM_DEFAULT.model_validate(_policy(
        autonomy__ops__rollback_rule={"enabled": True, "metric": "precision", "drop": 0.05, "windows": 2},
        retention__prompts_days=730, proposals__ttl_days=30))
    narrowed = narrow(wide, CAPS)
    assert not narrowed.autonomy.ops.rollback_rule.enabled
    assert (narrowed.retention.prompts_days, narrowed.proposals.ttl_days) == (365, 7)
    assert {"autonomy.ops.rollback_rule.enabled", "retention.prompts_days", "proposals.ttl_days"} <= set(
        cap_violations(wide, CAPS))
