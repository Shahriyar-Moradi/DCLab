"""Agent persistence (ADR 0009 §2.1-§2.4, §2.8, §2.10; Alembic 0071) on the live schema."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from _alembic_catalog import dump_constraints_triggers
from app.db.retention_guard import allow_retention, allow_workspace_deletion
from app.domain import decision_records as vocab
from app.services.observability_service import finalize_llm_invocation
from test_data_model_lineage import make_lineage_setup
from test_historical_alembic_revisions import (
    _assert_head_catalog,
    _drop_isolated,
    _isolated_database,
)
from test_tenant_backfill_schema import _run

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "alembic_head_constraints_triggers.json"
NEW_TABLES = (
    "prompt_releases",
    "agent_runs",
    "agent_events",
    "agent_proposals",
    "semantic_decision_answers",
)
NEW_FUNCTIONS = (
    "prevent_mutation_except_retention",
    "enforce_agent_proposal_transition",
    "guard_llm_invocation_ledger",
    "prevent_llm_invocation_agent_run_clear",
    "force_created_at_now",
)
LEGACY_PURPOSES = (
    "semantic_target",
    "semantic_missing_value",
    "semantic_column_type",
    "semantic_leakage",
    "pipeline_audit_routine",
    "pipeline_audit_deep",
)
JSON_COLUMNS = {
    "limits", "usage", "page_context", "payload", "rule_answer", "citations",
    "validator_reasons", "tool_arguments", "answer", "probabilities", "value_used",
    "ground_truth", "facts", "evidence_refs", "details", "redaction_summary", "scopes",
}


def _insert(db, table: str, values: dict) -> UUID:
    values = {"id": uuid4(), **values}
    columns = ", ".join(values)
    params = ", ".join(
        f"CAST(:{name} AS jsonb)" if name in JSON_COLUMNS else f":{name}" for name in values
    )
    bound = {k: json.dumps(v) if k in JSON_COLUMNS else v for k, v in values.items()}
    db.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), bound)
    return values["id"]


def _rejects(db, match: str, fn, *args, **kwargs) -> None:
    with pytest.raises(DBAPIError, match=match):
        fn(*args, **kwargs)
        db.commit()
    db.rollback()


def _sql(db, sql: str, **params):
    return db.execute(text(sql), params)


def _guc(db, name: str, value: str) -> None:
    _sql(db, "SELECT set_config(:name, :value, true)", name=name, value=value)


@pytest.fixture
def agents(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    alpha, beta = _run(db_session, setup, "alpha"), _run(db_session, setup, "beta")
    ns = SimpleNamespace(
        setup=setup,
        alpha=alpha,
        beta=beta,
        ws_a=setup["alpha"].id,
        ws_b=setup["beta"].id,
        project_a=alpha.pipeline.project_id,
        project_b=beta.pipeline.project_id,
        user_a=setup["alpha_admin"].id,
    )
    assert ns.project_a is not None and ns.project_b is not None
    ns.release = _insert(db_session, "prompt_releases", {
        "agent_key": "experiment_critic", "version": 1, "prompt_digest": "a" * 64,
        "output_schema_digest": "b" * 64, "status": "released", "released_at": datetime.now(UTC),
    })
    ns.run_a = agent_run(db_session, ns)
    ns.run_b = agent_run(db_session, ns, workspace_id=ns.ws_b, project_id=ns.project_b)
    db_session.commit()
    return ns


def agent_run(db, ns, **overrides) -> UUID:
    values = {
        "workspace_id": ns.ws_a, "project_id": ns.project_a, "kind": "specialist",
        "agent_key": "experiment_critic", "agent_version": "1", "runtime": "fake",
        "runtime_version": "fake==1", "purpose": "experiment.review", "subject_kind": "project",
        "policy_digest": "c" * 64, "tool_catalog_digest": "d" * 64, "data_class": "aggregates",
        "outcome_scope": "cv", "status": "completed", "limits": {"steps": 4},
    }
    return _insert(db, "agent_runs", {**values, **overrides})


def insert_agent_run(db, *, workspace_id, project_id) -> UUID:
    """A minimal completed specialist run (for tests that need a real actor_agent_run_id)."""

    return agent_run(db, SimpleNamespace(ws_a=workspace_id, project_a=project_id))


def agent_event(db, ns, seq: int, **overrides) -> UUID:
    values = {
        "workspace_id": ns.ws_a, "run_id": ns.run_a, "seq": seq, "type": "run_started",
        "payload": {"hooks": []}, "payload_digest": "e" * 64,
    }
    return _insert(db, "agent_events", {**values, **overrides})


def proposal(db, ns, **overrides) -> UUID:
    values = {
        "workspace_id": ns.ws_a, "project_id": ns.project_a, "run_id": ns.run_a,
        "decision_point_key": "experiment.review", "level_at_proposal": 1, "answer_ceiling": 2,
        "proposal_type": "ExperimentReviewProposal", "schema_version": 1,
        "payload": {"verdict": "keep"}, "payload_digest": "f" * 64,
        "validator_verdict": "accepted", "status": "proposed", "subject_kind": "experiment",
        "experiment_id": ns.alpha.pipeline.id,
    }
    return _insert(db, "agent_proposals", {**values, **overrides})


def invocation_base(ns) -> dict:
    return {
        "workspace_id": ns.ws_a, "experiment_id": ns.alpha.pipeline.id, "mode": "routine",
        "prompt_version": "v1", "schema_version": "1", "input_evidence_digest": "a" * 64,
        "redaction_summary": {}, "llm_used": False, "reason": "r", "status": "not_used",
        "validator_verdict": "not_run", "started_at": datetime.now(UTC),
    }


def agent_invocation(ns, run_id) -> dict:
    return {
        **invocation_base(ns), "purpose": "experiment.review", "agent_run_id": run_id,
        "data_class": "aggregates", "outcome_scope": "cv",
    }


def semantic_answer(db, ns, **overrides) -> UUID:
    values = {
        "workspace_id": ns.ws_a, "project_id": ns.project_a,
        "llm_invocation_id": ns.alpha.invocation.id, "decision_point_key": "column.semantic_role",
        "purpose": "column.semantic_role", "release_version": "3", "model_id": "jev-1.13.0",
        "question_key": "amount", "question_digest": "1" * 64, "data_class": "metadata",
        "evidence_partition": "train", "primitive": "choice", "answer": {"value": "numeric"},
        "in_acting_band": True, "agreement": "agree", "level": 1, "policy_outcome": "rule",
    }
    return _insert(db, "semantic_decision_answers", {**values, **overrides})


# --- migration -------------------------------------------------------------------


def test_create_all_helpers_match_alembic_0071_catalog(test_engine):
    """integrity.py/models.py (create_all) and the 0071 literal SQL produce identical objects."""

    def subset(dump: dict) -> dict:
        return {
            "checks": [
                item for item in dump["check_constraints"]
                if item["table"] in NEW_TABLES
                or item["name"].startswith("ck_llm_invocations_")
            ],
            "triggers": [
                item for item in dump["triggers"]
                if item["table"] in NEW_TABLES or item["table"] == "llm_invocations"
            ],
            "functions": [item for item in dump["functions"] if item["name"] in NEW_FUNCTIONS],
        }

    expected = subset(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert len(expected["triggers"]) == 22
    assert len(expected["functions"]) == len(NEW_FUNCTIONS)
    assert subset(dump_constraints_triggers(test_engine)) == expected


def test_0071_upgrade_downgrade_upgrade(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from70"
    )
    try:
        command.upgrade(alembic_config, "0070_investigation_findings")
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            ws = uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO workspaces (id, slug, name) VALUES (:id, 'w71', 'W')"),
                    {"id": ws},
                )
                connection.execute(text(
                    "INSERT INTO prompt_releases (id, agent_key, version, prompt_digest, "
                    "output_schema_digest, status) VALUES (gen_random_uuid(), 'lead', 1, "
                    "repeat('a', 64), repeat('b', 64), 'draft')"
                ))
            with pytest.raises(Exception, match="0071 downgrade refused"):
                command.downgrade(alembic_config, "0070_investigation_findings")
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE prompt_releases CASCADE"))
                # A gateway ledger value on a legacy-purpose row also has no 0070 form.
                connection.execute(
                    text(
                        "INSERT INTO projects (id, workspace_id, name, slug, description, provenance) "
                        "VALUES (gen_random_uuid(), :ws, 'P', 'p71', '', 'system_legacy_import')"
                    ),
                    {"ws": ws},
                )
                connection.execute(
                    text(
                        "INSERT INTO llm_invocations (id, workspace_id, project_id, purpose, mode, "
                        "prompt_version, schema_version, input_evidence_digest, redaction_summary, "
                        "llm_used, reason, status, validator_verdict, started_at, cost_micros) "
                        "SELECT gen_random_uuid(), :ws, id, 'semantic_target', 'semantic_decision', "
                        "'v1', '1', repeat('a', 64), '{}'::jsonb, false, 'r', 'not_used', "
                        "'not_run', now(), 1234 FROM projects WHERE slug = 'p71'"
                    ),
                    {"ws": ws},
                )
            with pytest.raises(Exception, match="0071 downgrade refused"):
                command.downgrade(alembic_config, "0070_investigation_findings")
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE llm_invocations CASCADE"))
            command.downgrade(alembic_config, "0070_investigation_findings")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT to_regclass('agent_runs')")).scalar() is None
                assert connection.execute(
                    text("SELECT count(*) FROM pg_proc WHERE proname = ANY(:names)"),
                    {"names": list(NEW_FUNCTIONS)},
                ).scalar() == 0
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


# --- append-only + retention (items 3, 5, 11) ------------------------------------------


def _backdated(db, table: str, insert, days: int = 400) -> UUID:
    """Insert past ``force_created_at_now`` (test-only, like _force_lock in other suites)."""

    db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_created_at_now"))
    row_id = insert(created_at=datetime.now(UTC) - timedelta(days=days))
    db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {table}_created_at_now"))
    db.commit()
    return row_id


def test_created_at_is_forced_on_insert(db_session, agents):
    ns = agents
    past = datetime.now(UTC) - timedelta(days=400)
    rows = {
        "agent_events": agent_event(db_session, ns, 1, created_at=past),
        "semantic_decision_answers": semantic_answer(db_session, ns, created_at=past),
        "llm_invocations": _insert(db_session, "llm_invocations",
                                   {**invocation_base(ns), "purpose": "x", "created_at": past}),
        "agent_runs": agent_run(db_session, ns, created_at=past),
        "agent_proposals": proposal(db_session, ns, created_at=past),
    }
    db_session.commit()
    for table, row_id in rows.items():
        age = _sql(db_session, f"SELECT now() - created_at FROM {table} WHERE id = :id",
                   id=row_id).scalar()
        assert age < timedelta(days=1), table


def test_agent_events_are_append_only_with_retention_and_deletion_gucs(db_session, agents):
    ns = agents
    old = _backdated(db_session, "agent_events",
                     lambda **kw: agent_event(db_session, ns, 1, **kw))
    new = agent_event(db_session, ns, 2)
    db_session.commit()
    delete = "DELETE FROM agent_events WHERE id = :id"

    _rejects(db_session, "append-only", _sql, db_session,
             "UPDATE agent_events SET type = 'run_failed' WHERE id = :id", id=new)
    _rejects(db_session, "append-only", _sql, db_session, delete, id=old)
    # Raw GUCs without the transaction stamp never work (a session-level SET would leak).
    _guc(db_session, "dclab.retention_horizon", "365 days")
    _guc(db_session, "dclab.retention_workspace", str(ns.ws_a))
    _guc(db_session, "dclab.deleting_workspace", str(ns.ws_a))
    _rejects(db_session, "append-only", _sql, db_session, delete, id=old)
    _sql(db_session, "SET dclab.deleting_workspace = '%s'" % ns.ws_a)  # session level
    _sql(db_session, "SET dclab.retention_xact = '1'")
    db_session.commit()
    _rejects(db_session, "append-only", _sql, db_session, delete, id=old)
    _sql(db_session, "RESET dclab.deleting_workspace")
    _sql(db_session, "RESET dclab.retention_xact")
    db_session.commit()
    # A stamp from an earlier transaction does not carry over.
    allow_workspace_deletion(db_session, ns.ws_a)
    stamp = _sql(db_session, "SELECT current_setting('dclab.retention_xact')").scalar()
    db_session.commit()
    _guc(db_session, "dclab.retention_xact", stamp)
    _guc(db_session, "dclab.deleting_workspace", str(ns.ws_a))
    _rejects(db_session, "append-only", _sql, db_session, delete, id=new)

    # Horizon: at least 30 days, only the named workspace, only rows older than it.
    with pytest.raises(ValueError):
        allow_retention(db_session, ns.ws_a, horizon_days=29)
    _sql(db_session, "SELECT set_config('dclab.retention_xact', pg_current_xact_id()::text, true), "
         "set_config('dclab.retention_horizon', '29 days', true), "
         "set_config('dclab.retention_workspace', :ws, true)", ws=str(ns.ws_a))
    _rejects(db_session, "append-only", _sql, db_session, delete, id=old)
    allow_retention(db_session, ns.ws_b, horizon_days=365)
    _rejects(db_session, "append-only", _sql, db_session, delete, id=old)
    allow_retention(db_session, ns.ws_a, horizon_days=365)
    _rejects(db_session, "append-only", _sql, db_session, delete, id=new)
    allow_retention(db_session, ns.ws_a, horizon_days=365)
    _rejects(db_session, "append-only", _sql, db_session,
             "UPDATE agent_events SET seq = 9 WHERE id = :id", id=old)
    allow_retention(db_session, ns.ws_a, horizon_days=365)
    assert _sql(db_session, delete, id=old).rowcount == 1
    db_session.commit()
    # The permission ended with the transaction.
    _rejects(db_session, "append-only", _sql, db_session, delete, id=new)
    allow_workspace_deletion(db_session, ns.ws_b)
    _rejects(db_session, "append-only", _sql, db_session, delete, id=new)
    allow_workspace_deletion(db_session, ns.ws_a)
    assert _sql(db_session, delete, id=new).rowcount == 1
    db_session.commit()


def test_agent_runs_and_proposals_delete_only_under_retention(db_session, agents):
    ns = agents
    run = agent_run(db_session, ns)
    pid = proposal(db_session, ns, run_id=run)
    db_session.commit()
    _rejects(db_session, "agent_proposals rows are append-only", _sql, db_session,
             "DELETE FROM agent_proposals WHERE id = :id", id=pid)
    _rejects(db_session, "agent_runs rows are append-only", _sql, db_session,
             "DELETE FROM agent_runs WHERE id = :id", id=run)
    allow_workspace_deletion(db_session, ns.ws_a)
    _sql(db_session, "DELETE FROM agent_proposals WHERE id = :id", id=pid)
    _sql(db_session, "DELETE FROM agent_runs WHERE id = :id", id=run)
    db_session.commit()
    old_run = _backdated(db_session, "agent_runs", lambda **kw: agent_run(db_session, ns, **kw))
    allow_retention(db_session, ns.ws_a, horizon_days=365)
    assert _sql(db_session, "DELETE FROM agent_runs WHERE id = :id", id=old_run).rowcount == 1
    db_session.commit()


def test_semantic_answers_are_append_only_and_labels_are_new_rows(db_session, agents):
    ns = agents
    answer = semantic_answer(db_session, ns)
    db_session.commit()
    _rejects(db_session, "append-only", _sql, db_session,
             "UPDATE semantic_decision_answers SET ground_truth = '{}'::jsonb WHERE id = :id",
             id=answer)
    _rejects(db_session, "ck_sda_label", semantic_answer, db_session, ns,
             ground_truth={"value": "id"}, label_source="blind")
    # Item 10: a label version always points at the row it supersedes.
    _rejects(db_session, "ck_sda_label_chain", semantic_answer, db_session, ns,
             ground_truth={"value": "id"}, label_source="blind", labeled_at=datetime.now(UTC),
             labels_version=1)
    label = semantic_answer(
        db_session, ns, ground_truth={"value": "id"}, label_source="blind",
        labeled_at=datetime.now(UTC), labels_version=1, supersedes_label_id=answer,
        labeled_by_user_id=ns.user_a,
    )
    db_session.commit()
    _rejects(db_session, "uq_sda_supersedes_label_id", semantic_answer, db_session, ns,
             ground_truth={"value": "x"}, label_source="blind", labeled_at=datetime.now(UTC),
             labels_version=2, supersedes_label_id=answer)
    _rejects(db_session, "append-only", _sql, db_session,
             "DELETE FROM semantic_decision_answers WHERE id = :id", id=label)
    allow_workspace_deletion(db_session, ns.ws_a)
    _sql(db_session, "DELETE FROM semantic_decision_answers WHERE id IN (:a, :b)", a=label, b=answer)
    db_session.commit()
    index = _sql(db_session, "SELECT indexdef FROM pg_indexes WHERE indexname = "
                 "'ix_sda_workspace_project'").scalar()
    assert "(workspace_id, project_id)" in index and "project_id IS NOT NULL" in index


# --- proposals (item 2) ----------------------------------------------------------------


def test_proposal_transitions_follow_the_state_machine(db_session, agents):
    ns = agents
    pid = proposal(db_session, ns)
    db_session.commit()
    update = "UPDATE agent_proposals SET {} WHERE id = :id"
    decide = "status = '{}', decided_by_user_id = :user, decided_at = now()"

    _rejects(db_session, "may change", _sql, db_session,
             update.format("payload = '{\"verdict\": \"drop\"}'::jsonb"), id=pid)
    _rejects(db_session, "may change", _sql, db_session,
             update.format("proposed_rationale = 'edited'"), id=pid)
    _rejects(db_session, "illegal transition proposed -> applied", _sql, db_session,
             update.format("status = 'applied'"), id=pid)
    _rejects(db_session, "a decision needs decided_by_user_id", _sql, db_session,
             update.format("status = 'accepted'"), id=pid)
    _rejects(db_session, "may change only expires_at", _sql, db_session,
             update.format("decision_record_id = gen_random_uuid()"), id=pid)
    _rejects(db_session, "decided_.* change only", _sql, db_session,
             update.format("decided_by_user_id = :user, decided_at = now()"), id=pid, user=ns.user_a)
    _sql(db_session, update.format("expires_at = now() + interval '7 days'"), id=pid)
    _sql(db_session, update.format(decide.format("accepted")), id=pid, user=ns.user_a)
    _sql(db_session, update.format("status = 'applied'"), id=pid)
    _sql(db_session, update.format("status = 'reverted'"), id=pid)
    db_session.commit()
    for status in ("proposed", "applied", "superseded"):
        _rejects(db_session, "final|illegal", _sql, db_session,
                 update.format(f"status = '{status}'"), id=pid)

    rejected = proposal(db_session, ns)
    _sql(db_session, update.format(decide.format("rejected")), id=rejected, user=ns.user_a)
    superseded = proposal(db_session, ns)
    _sql(db_session, update.format("status = 'superseded', supersede_reason = 'plan_exists'"),
         id=superseded)
    db_session.commit()
    expired = proposal(db_session, ns, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    db_session.commit()
    _sql(db_session, update.format("expires_at = now() - interval '1 second'"), id=expired)
    db_session.commit()
    _rejects(db_session, "has expired", _sql, db_session,
             update.format(decide.format("accepted")), id=expired, user=ns.user_a)
    _sql(db_session, update.format("status = 'expired'"), id=expired)
    db_session.commit()

    # INSERT: decision columns belong to the decision; L0 is shadow; tool calls are L1.
    for overrides, match in (
        ({"status": "accepted"}, "cannot be created as accepted"),
        ({"decided_by_user_id": ns.user_a}, "not at creation"),
        ({"decided_at": datetime.now(UTC)}, "not at creation"),
        ({"decision_record_id": uuid4()}, "not at creation"),
        ({"applied_decision_record_id": uuid4()}, "not at creation"),
        ({"validator_verdict": "rejected"}, "ck_agent_proposals_verdict_status"),
        ({"proposal_type": "ToolCallProposal"}, "ck_agent_proposals_tool_call"),
        ({"status": "applied"}, "applied needs level 2 or 3"),
        ({"status": "shadow"}, "ck_agent_proposals_level_status"),
        ({"level_at_proposal": 0}, "ck_agent_proposals_level_status"),
        ({"level_at_proposal": 3, "answer_ceiling": 2}, "ck_agent_proposals_levels"),
        ({"proposal_type": "ToolCallProposal", "tool_name": "start_experiment",
          "tool_arguments": {}, "status": "applied", "level_at_proposal": 2},
         "ck_agent_proposals_tool_call_level"),
    ):
        _rejects(db_session, match, proposal, db_session, ns, **overrides)
    proposal(db_session, ns, status="shadow", level_at_proposal=0)
    proposal(db_session, ns, proposal_type="ToolCallProposal", tool_name="start_experiment",
             tool_arguments={"intent": "x"})
    _rejects(db_session, "ck_agent_proposals_supersede_reason", _sql, db_session,
             update.format("status = 'expired', supersede_reason = 'plan_exists'"),
             id=proposal(db_session, ns))
    db_session.commit()

    # L2/L3 values are created applied (with their applied record link) and may be reverted;
    # the revert keeps the existing record links.
    applied = proposal(db_session, ns, status="applied", level_at_proposal=2)
    _sql(db_session, update.format("status = 'reverted'"), id=applied)
    db_session.commit()
    # Backstop under the trigger: a decider only on decided statuses.
    def shadow_with_decider():
        db_session.execute(text("ALTER TABLE agent_proposals DISABLE TRIGGER agent_proposals_transition"))
        proposal(db_session, ns, status="shadow", level_at_proposal=0, decided_by_user_id=ns.user_a,
                 decided_at=datetime.now(UTC))

    _rejects(db_session, "ck_agent_proposals_decided_status", shadow_with_decider)


def test_proposal_decision_fields_are_write_once(db_session, agents):
    ns = agents
    update = "UPDATE agent_proposals SET {} WHERE id = :id"
    accepted = proposal(db_session, ns)
    _sql(db_session, update.format(
        "status = 'accepted', decided_by_user_id = :user, decided_at = now()"),
        id=accepted, user=ns.user_a)
    db_session.commit()
    for assignment in (
        "decided_at = now() - interval '1 day'", "decided_by_user_id = NULL",
        "expires_at = now()",
    ):
        _rejects(db_session, "decided_.* change only|expires_at changes only", _sql, db_session,
                 update.format(assignment), id=accepted)
    record = _insert(db_session, "project_decision_records", {
        "workspace_id": ns.ws_a, "project_id": ns.project_a, "decision_type": "proposal_accepted",
        "state": "accepted", "subject_kind": "project", "actor_kind": "human",
        "actor_user_id": ns.user_a, "rationale": "accepted", "rationale_untrusted": False,
        "schema_version": 1, "policy_version": vocab.DECISION_POLICY_VERSION,
    })
    _sql(db_session, update.format("decision_record_id = :record"), id=accepted, record=record)
    db_session.commit()
    _rejects(db_session, "write-once", _sql, db_session,
             update.format("decision_record_id = NULL"), id=accepted)
    _sql(db_session, update.format("status = 'applied'"), id=accepted)
    _sql(db_session, update.format("status = 'reverted'"), id=accepted)
    db_session.commit()
    assert _sql(db_session, "SELECT decision_record_id FROM agent_proposals WHERE id = :id",
                id=accepted).scalar() == record


# --- agent_runs (items 4, 8) ----------------------------------------------------------


def test_agent_run_header_is_frozen_and_digest_and_hold_write_once(db_session, agents):
    ns = agents
    update = "UPDATE agent_runs SET {} WHERE id = :id"
    _sql(db_session, update.format(
        "status = 'running', usage = '{\"steps\": 1}'::jsonb, cost_micros = 5, "
        "provider = 'openai', model = 'gpt-x', started_at = now(), last_activity_at = now()"),
        id=ns.run_a)
    _sql(db_session, update.format("context_digest = repeat('9', 64)"), id=ns.run_a)
    _sql(db_session, update.format("held_micros = 2500"), id=ns.run_a)
    db_session.commit()
    for assignment in (
        "kind = 'ops'", "limits = '{}'::jsonb", "policy_digest = repeat('0', 64)",
        "outcome_scope = 'none'", "agent_key = 'other'", "created_at = now()",
    ):
        _rejects(db_session, "is immutable", _sql, db_session, update.format(assignment), id=ns.run_a)
    for assignment in ("context_digest = repeat('8', 64)", "held_micros = 1", "held_micros = 0"):
        _rejects(db_session, "immutable", _sql, db_session, update.format(assignment), id=ns.run_a)


def test_agent_run_checks_and_outcome_scope_never_holdout(db_session, agents):
    ns = agents
    _rejects(db_session, "ck_agent_runs_outcome_scope", agent_run, db_session, ns,
             outcome_scope="holdout")
    _rejects(db_session, "ck_llm_invocations_outcome_scope", _insert, db_session,
             "llm_invocations", {**invocation_base(ns), "purpose": "x", "outcome_scope": "holdout"})
    _rejects(db_session, "ck_agent_runs_prompt_release", agent_run, db_session, ns,
             runtime="lead_loop")
    agent_run(db_session, ns, runtime="lead_loop", prompt_release_id=ns.release)
    _rejects(db_session, "ck_agent_runs_assistant", agent_run, db_session, ns,
             kind="assistant", agent_key="lead", subject_kind="thread")
    token = _insert(db_session, "service_tokens", {
        "workspace_id": ns.ws_a, "created_by_user_id": ns.user_a, "name": "t71",
        "scopes": ["read"], "secret_hash": uuid4().hex * 2,
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    })
    _rejects(db_session, "ck_agent_runs_assistant", agent_run, db_session, ns,
             kind="assistant", agent_key="lead", subject_kind="thread", created_by_user_id=ns.user_a,
             created_by_service_token_id=token)
    agent_run(db_session, ns, kind="assistant", agent_key="lead", subject_kind="thread",
              created_by_user_id=ns.user_a)
    _rejects(db_session, "ck_agent_runs_subject_matches_kind", agent_run, db_session, ns,
             subject_kind="experiment")
    _rejects(db_session, "ck_agent_runs_project", agent_run, db_session, ns, project_id=None)
    agent_run(db_session, ns, project_id=None, kind="ops", subject_kind=None, purpose="ops.diagnose")
    _rejects(db_session, "ck_agent_runs_data_class", agent_run, db_session, ns, data_class="raw_rows")
    _rejects(db_session, "ck_agent_runs_limits", agent_run, db_session, ns,
             limits={"api_key": "x"})
    db_session.commit()
    # One active run per agent and subject (NULL subject columns still collide).
    agent_run(db_session, ns, status="queued")
    db_session.commit()
    _rejects(db_session, "uq_agent_runs_active_subject", agent_run, db_session, ns, status="running")


def test_composite_fks_reject_cross_workspace_references(db_session, agents):
    ns = agents
    _rejects(db_session, "fk_agent_runs_workspace_project", agent_run, db_session, ns,
             project_id=ns.project_b)
    _rejects(db_session, "fk_agent_runs_workspace_project_experiment", agent_run, db_session, ns,
             subject_kind="experiment", experiment_id=ns.beta.pipeline.id)
    _rejects(db_session, "fk_agent_runs_workspace_parent_run", agent_run, db_session, ns,
             parent_run_id=ns.run_b)
    # Nullable project_id: a subject id without a project is refused by CHECK (MATCH SIMPLE gap).
    _rejects(db_session, "ck_agent_runs_(project|subject_requires_project)", agent_run, db_session,
             ns, project_id=None, kind="ops", subject_kind="experiment",
             experiment_id=ns.beta.pipeline.id)
    _rejects(db_session, "ck_sda_subject_requires_project", semantic_answer, db_session, ns,
             project_id=None, experiment_id=ns.beta.pipeline.id)
    _rejects(db_session, "fk_agent_events_workspace_run", agent_event, db_session, ns, 1,
             run_id=ns.run_b)
    _rejects(db_session, "fk_agent_events_workspace_llm_invocation", agent_event, db_session, ns, 1,
             llm_invocation_id=ns.beta.invocation.id)
    _rejects(db_session, "fk_agent_proposals_workspace_run", proposal, db_session, ns,
             run_id=ns.run_b)
    _rejects(db_session, "fk_sda_workspace_llm_invocation", semantic_answer, db_session, ns,
             llm_invocation_id=ns.beta.invocation.id)
    _rejects(db_session, "fk_llm_invocations_workspace_agent_run", _insert, db_session,
             "llm_invocations", agent_invocation(ns, ns.run_b))


# --- llm_invocations (items 1, 6, 7) ----------------------------------------------------


def test_llm_invocation_identity_frozen_and_finalize_still_works(db_session, agents):
    ns = agents
    invocation = ns.alpha.invocation
    finalize_llm_invocation(
        invocation, status="completed", validator_verdict="validated", reason="ok",
        safe_output={"advisory_status": "PASS"}, latency_ms=3.0,
    )
    db_session.commit()
    db_session.refresh(invocation)
    assert invocation.status == "completed" and invocation.safe_output is not None
    update = "UPDATE llm_invocations SET {} WHERE id = :id"
    _sql(db_session, update.format("workspace_id = workspace_id"), id=invocation.id)  # unchanged
    for assignment in (
        "purpose = 'semantic_target'", "started_at = now()", "created_at = now() - interval '1 year'",
        "input_evidence_digest = repeat('b', 64)", "data_class = 'metadata'", "id = gen_random_uuid()",
        "workflow_run_id = NULL", "experiment_id = NULL", "mode = 'deep'", "llm_used = true",
        "prompt_version = 'v2'", "schema_version = '2'", "provider_kind = 'llm_provider'",
        "agent_role = 'verifier'", "decision_point_key = 'x'",
    ):
        _rejects(db_session, "is immutable", _sql, db_session, update.format(assignment),
                 id=invocation.id)
    _rejects(db_session, "append-only", _sql, db_session,
             update.format("safe_output = NULL"), id=invocation.id)
    allow_retention(db_session, ns.ws_a, horizon_days=365)
    _rejects(db_session, "append-only", _sql, db_session,
             update.format("safe_output = NULL"), id=invocation.id)
    allow_workspace_deletion(db_session, ns.ws_a)
    _rejects(db_session, "may only null safe_output", _sql, db_session,
             update.format("safe_output = NULL, latency_ms = 9"), id=invocation.id)
    allow_workspace_deletion(db_session, ns.ws_a)
    _sql(db_session, update.format("safe_output = NULL"), id=invocation.id)
    db_session.commit()


def test_llm_invocation_ledger_is_final_after_completion(db_session, agents):
    ns = agents
    update = "UPDATE llm_invocations SET {} WHERE id = :id"
    row = _insert(db_session, "llm_invocations", {
        **agent_invocation(ns, ns.run_a), "status": "pending", "validator_verdict": "pending",
    })
    db_session.commit()
    # Before completion: write-once ids fill once, cost fills once.
    _sql(db_session, update.format(
        "cost_micros = 40, provider_request_id = 'req-1', provider_resolved_model = 'gpt-x', "
        "budget_reservation_id = gen_random_uuid(), cache_hit = false"), id=row)
    db_session.commit()
    for assignment in (
        "cost_micros = 41", "cost_micros = NULL", "provider_request_id = 'req-2'",
        "provider_resolved_model = 'gpt-y'", "budget_reservation_id = gen_random_uuid()",
    ):
        _rejects(db_session, "write-once", _sql, db_session, update.format(assignment), id=row)
    # Finalize as the legacy path does (status, verdict, outputs, timings in one UPDATE).
    _sql(db_session, update.format(
        "status = 'completed', validator_verdict = 'validated', reason = 'ok', "
        "safe_output = '{\"a\": 1}'::jsonb, final_decision = '{\"b\": 2}'::jsonb, "
        "latency_ms = 3, completed_at = now()"), id=row)
    db_session.commit()
    for assignment in (
        "status = 'failed'", "validator_verdict = 'x'", "final_decision = NULL", "model = 'other'",
        "cache_hit = true", "refusal_code = 'kill_switch'", "completed_at = now()",
        "safe_output = '{\"a\": 2}'::jsonb",
    ):
        _rejects(db_session, "final once completed", _sql, db_session,
                 update.format(assignment), id=row)
    # Settle may follow finalize; it only moves false -> true.
    _sql(db_session, update.format("budget_settled = true"), id=row)
    db_session.commit()
    _rejects(db_session, "only moves from false to true", _sql, db_session,
             update.format("budget_settled = false"), id=row)


def test_llm_invocation_agent_scope_and_agent_run_link(db_session, agents):
    ns = agents
    update = "UPDATE llm_invocations SET {} WHERE id = :id"
    base = {**invocation_base(ns), "purpose": "experiment.review"}
    _rejects(db_session, "ck_llm_invocations_agent_scope", _insert, db_session, "llm_invocations",
             {**base, "agent_run_id": ns.run_a})
    _rejects(db_session, "ck_llm_invocations_agent_scope", _insert, db_session, "llm_invocations",
             {**base, "prompt_release_id": ns.release, "data_class": "metadata"})

    # agent_run_id: set at insert, never moved or cleared except by its FK's SET NULL.
    _rejects(db_session, "immutable", _sql, db_session, update.format("agent_run_id = :run"),
             id=ns.alpha.invocation.id, run=ns.run_a)
    run = agent_run(db_session, ns)
    attributed = _insert(db_session, "llm_invocations", agent_invocation(ns, run))
    db_session.commit()
    _rejects(db_session, "immutable", _sql, db_session, update.format("agent_run_id = :run"),
             id=attributed, run=ns.run_a)
    _rejects(db_session, "agent_run_id is immutable", _sql, db_session,
             update.format("agent_run_id = NULL"), id=attributed)
    allow_workspace_deletion(db_session, ns.ws_a)
    _sql(db_session, "DELETE FROM agent_runs WHERE id = :id", id=run)
    db_session.commit()
    row = _sql(db_session, "SELECT workspace_id, agent_run_id FROM llm_invocations WHERE id = :id",
               id=attributed).one()
    assert row == (ns.ws_a, None)


def test_llm_invocation_purpose_is_a_key(db_session, agents):
    ns = agents
    base = invocation_base(ns)
    for purpose in (*LEGACY_PURPOSES, "assistant.turn", "column.semantic_role", "jev:target.column"):
        _insert(db_session, "llm_invocations", {**base, "purpose": purpose})
    _insert(db_session, "llm_invocations", {
        **agent_invocation(ns, ns.run_a), "cache_hit": True, "cost_micros": 0, "currency": "USD",
        "agent_role": "specialist", "prompt_release_id": ns.release,
        "provider_resolved_model": "gpt-5.5-2026-09-01",
    })
    db_session.commit()
    for purpose in ("Bad Purpose", "9lives", "semantic/target"):
        _rejects(db_session, "ck_llm_invocations_purpose", _insert, db_session, "llm_invocations",
                 {**base, "purpose": purpose})
    _rejects(db_session, "ck_llm_invocations_agent_role", _insert, db_session, "llm_invocations",
             {**base, "purpose": "x", "agent_role": "boss"})


def test_decision_record_actor_agent_run_fk_and_new_types(db_session, agents):
    ns = agents
    assert {"decision_point_resolved", "proposal_reverted"} <= set(vocab.DECISION_TYPES)
    assert {"decision_point_resolved", "proposal_reverted"} <= vocab.RESERVED_DECISION_TYPES

    def record(run_id):
        return _insert(db_session, "project_decision_records", {
            "workspace_id": ns.ws_a, "project_id": ns.project_a,
            "decision_type": "decision_point_resolved", "state": "accepted",
            "subject_kind": "project", "actor_kind": "agent", "actor_agent_run_id": run_id,
            "rationale": "agent value applied", "rationale_untrusted": True,
            "schema_version": 1, "policy_version": vocab.DECISION_POLICY_VERSION,
        })

    _rejects(db_session, "fk_pdr_actor_agent_run", record, uuid4())
    _rejects(db_session, "fk_pdr_actor_agent_run", record, ns.run_b)
    record(ns.run_a)
    db_session.commit()


def test_experiment_delete_needs_the_deletion_guc(db_session, agents):
    ns = agents
    experiment, invocation = ns.alpha.pipeline.id, ns.alpha.invocation.id
    _sql(db_session, "DELETE FROM client_lab_uploads WHERE id = :id", id=ns.alpha.upload.id)
    db_session.commit()
    _rejects(db_session, "llm_invocations rows are append-only", _sql, db_session,
             "DELETE FROM experiments WHERE id = :id", id=experiment)
    allow_workspace_deletion(db_session, ns.ws_a)
    _sql(db_session, "DELETE FROM experiments WHERE id = :id", id=experiment)
    db_session.commit()
    assert _sql(db_session, "SELECT count(*) FROM llm_invocations WHERE id = :id",
                id=invocation).scalar() == 0


# --- prompt_releases (item 9) ------------------------------------------------------------


def test_prompt_releases_identity_frozen_status_forward_only_and_never_deleted(db_session, agents):
    ns = agents
    update = "UPDATE prompt_releases SET {} WHERE id = :id"
    draft = _insert(db_session, "prompt_releases", {
        "agent_key": "lead", "version": 1, "prompt_digest": "a" * 64,
        "output_schema_digest": "b" * 64, "status": "draft",
    })
    db_session.commit()
    _rejects(db_session, "immutable", _sql, db_session, update.format("status = 'retired'"), id=draft)
    _sql(db_session, update.format("status = 'released', released_at = now()"), id=draft)
    db_session.commit()
    _rejects(db_session, "immutable", _sql, db_session, update.format("released_at = now()"), id=draft)
    _rejects(db_session, "immutable", _sql, db_session, update.format("status = 'draft'"), id=draft)
    _sql(db_session, update.format("status = 'retired'"), id=ns.release)
    db_session.commit()
    _rejects(db_session, "immutable", _sql, db_session, update.format("status = 'released'"),
             id=ns.release)
    _rejects(db_session, "is immutable", _sql, db_session,
             update.format("prompt_digest = repeat('0', 64)"), id=ns.release)
    _rejects(db_session, "immutable", _sql, db_session,
             "DELETE FROM prompt_releases WHERE id = :id", id=ns.release)
    _rejects(db_session, "uq_prompt_releases_agent_key_version", _insert, db_session,
             "prompt_releases", {"agent_key": "experiment_critic", "version": 1,
                                 "prompt_digest": "a" * 64, "output_schema_digest": "b" * 64,
                                 "status": "draft"})
