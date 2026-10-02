"""Historical Alembic revisions must not import live runtime modules."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from _alembic_catalog import dump_constraints_triggers, metadata_diffs
from conftest import ADMIN_URL

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
FROZEN = Path(__file__).resolve().parents[1] / "alembic_frozen"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "alembic_head_constraints_triggers.json"
)
REPRESENTATIVE_OLD_REVISION = "0028_semantic_leakage_purpose"

FORBIDDEN_PREFIXES = (
    "app.domain",
    "app.services",
    "app.db.integrity",
    "app.db.evidence_lock",
    "app.db.legacy_import",
)


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


def _forbidden(names: set[str]) -> list[str]:
    hits = []
    for name in sorted(names):
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in FORBIDDEN_PREFIXES
        ):
            hits.append(name)
    return hits


def test_historical_revisions_do_not_import_live_runtime_modules():
    offenders: list[str] = []
    for path in sorted(VERSIONS.glob("*.py")):
        hits = _forbidden(_imported_names(path))
        if hits:
            offenders.append(f"{path.name}: {', '.join(hits)}")
    assert offenders == []


def test_frozen_alembic_modules_do_not_import_live_runtime_modules():
    offenders: list[str] = []
    for path in sorted(FROZEN.glob("*.py")):
        hits = _forbidden(_imported_names(path))
        if hits:
            offenders.append(f"{path.name}: {', '.join(hits)}")
    assert offenders == []


def test_0030_frozen_artifact_types_exclude_later_reproduction_types():
    from alembic_frozen.rev_0030_data_plane import ARTIFACT_TYPES

    assert "reproduction_notebook" not in ARTIFACT_TYPES
    assert "reproduction_script" not in ARTIFACT_TYPES


def _isolated_database(monkeypatch, *, suffix: str):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_alembic_{suffix}_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated alembic database: {exc}")
    rendered = database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    from app.config import get_settings

    get_settings.cache_clear()
    return admin_engine, database_name, database_url, _alembic_config()


def _drop_isolated(admin_engine, database_name: str) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    with admin_engine.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :database_name"
            ),
            {"database_name": database_name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    admin_engine.dispose()


def _assert_head_catalog(engine, alembic_config: Config) -> None:
    command.check(alembic_config)
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    assert current == head
    dump = dump_constraints_triggers(engine)
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert dump == expected
    diffs = metadata_diffs(engine)
    assert diffs == []


def test_fresh_database_upgrade_to_head_matches_frozen_catalog(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="fresh"
    )
    try:
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_representative_old_revision_upgrade_to_head_matches_frozen_catalog(
    monkeypatch,
):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from28"
    )
    try:
        command.upgrade(alembic_config, REPRESENTATIVE_OLD_REVISION)
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_previous_head_dataset_policy_upgrade_preserves_nullable_legacy_fields(
    monkeypatch,
):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from59"
    )
    try:
        command.upgrade(alembic_config, "0059_auth_session_constraints")
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            _assert_head_catalog(engine, alembic_config)
            with engine.connect() as connection:
                nullable = connection.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'dataset_columns' AND column_name IN "
                        "('sensitivity_class', 'llm_exposure_policy', 'retention_class', "
                        "'residency_class', 'classification_confidence', 'policy_schema_version')"
                    )
                ).all()
            assert len(nullable) == 6
            assert all(value == "YES" for _, value in nullable)
            command.downgrade(alembic_config, "0059_auth_session_constraints")
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT to_regclass('public.dataset_policy_revisions')")
                ).scalar() is None
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_previous_head_ingestion_publication_upgrade_is_additive(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from60"
    )
    try:
        command.upgrade(alembic_config, "0060_dataset_policy_bootstrap")
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            _assert_head_catalog(engine, alembic_config)
            with engine.connect() as connection:
                columns = connection.execute(text(
                    "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
                    "WHERE table_name='ingestion_runs' AND column_name IN "
                    "('artifact_id','publication_state','publication_version','publication_digest')"
                )).all()
                assert len(columns) == 4
                assert {name: nullable for name, _, nullable in columns}["artifact_id"] == "YES"
                assert {name: default for name, default, _ in columns}["publication_state"] == "'received'::character varying"
            command.downgrade(alembic_config, "0060_dataset_policy_bootstrap")
            with engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT to_regclass('public.ingestion_publication_events')"
                )).scalar() is None
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def _seed_state_graph_links(engine) -> dict[str, object]:
    """Minimal 0062 rows: one single-link, one ambiguous and one cross-project upload."""

    ids = {name: uuid4() for name in (
        "ws", "p1", "p2", "env", "asset", "ds_a", "ds_b", "ds_other",
        "single", "ambiguous", "cross",
    )}
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO workspaces (id, slug, name) VALUES (:ws, :slug, 'Graph')"),
            {"ws": ids["ws"], "slug": f"graph-{ids['ws'].hex[:8]}"},
        )
        for project, slug in (("p1", "one"), ("p2", "two")):
            connection.execute(
                text(
                    "INSERT INTO projects (id, workspace_id, name, slug, description, status, "
                    "provenance) VALUES (:id, :ws, :slug, :slug, '', 'active', "
                    "'system_legacy_import')"
                ),
                {"id": ids[project], "ws": ids["ws"], "slug": slug},
            )
        connection.execute(
            text("INSERT INTO environments (id, org_id, name) VALUES (:id, 'org', 'env')"),
            {"id": ids["env"]},
        )
        connection.execute(
            text(
                "INSERT INTO dataset_assets (id, workspace_id, name, slug, description) "
                "VALUES (:id, :ws, 'asset', 'asset', '')"
            ),
            {"id": ids["asset"], "ws": ids["ws"]},
        )
        for dataset, project, version in (
            ("ds_a", "p1", "v1"), ("ds_b", "p1", "v2"), ("ds_other", "p2", "v3"),
        ):
            connection.execute(
                text(
                    "INSERT INTO datasets (id, workspace_id, dataset_asset_id, environment_id, "
                    "project_id, name, source_type, location, version, row_count, column_count) "
                    "VALUES (:id, :ws, :asset, :env, :project, 'd', 'csv', '/tmp/d.csv', "
                    ":version, 2, 2)"
                ),
                {
                    "id": ids[dataset], "ws": ids["ws"], "asset": ids["asset"],
                    "env": ids["env"], "project": ids[project], "version": version,
                },
            )
        for experiment in ("single", "ambiguous", "cross"):
            connection.execute(
                text(
                    "INSERT INTO experiments (id, workspace_id, project_id, environment_id, "
                    "dataset_id, status, config, seed) VALUES (:id, :ws, :p1, :env, :ds, "
                    "'COMPLETED', '{}'::jsonb, 42)"
                ),
                {
                    "id": ids[experiment], "ws": ids["ws"], "p1": ids["p1"],
                    "env": ids["env"], "ds": ids["ds_a"],
                },
            )
        for experiment, dataset in (
            ("single", "ds_a"), ("ambiguous", "ds_a"), ("ambiguous", "ds_b"),
            ("cross", "ds_other"),
        ):
            upload = uuid4()
            connection.execute(
                text(
                    "INSERT INTO client_lab_uploads (id, run_id, workspace_id, category, "
                    "original_filename, stored_path, kind, record_count, fields_noticed, "
                    "has_named_fields, client_status, experiment_id, dataset_id) VALUES "
                    "(:id, :id, :ws, 'Revenue', 'u.csv', '/tmp/u.csv', 'spreadsheet', 2, "
                    "'[]'::jsonb, true, 'queued', :experiment, :dataset)"
                ),
                {
                    "id": upload, "ws": ids["ws"], "experiment": ids[experiment],
                    "dataset": ids[dataset],
                },
            )
    return ids


def _source_datasets(engine, ids) -> dict:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT id, source_dataset_id FROM experiments "
                    "WHERE id IN (:single, :ambiguous, :cross)"
                ),
                {key: ids[key] for key in ("single", "ambiguous", "cross")},
            ).all()
        )


def test_previous_head_state_graph_backfill_and_guarded_downgrade(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from62"
    )
    try:
        command.upgrade(alembic_config, "0062_run_artifact_types")
        engine = create_engine(database_url)
        try:
            ids = _seed_state_graph_links(engine)
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
            expected = {ids["single"]: ids["ds_a"], ids["ambiguous"]: None, ids["cross"]: None}
            assert _source_datasets(engine, ids) == expected

            # Only the deterministic backfill exists: downgrade is allowed and repeatable.
            command.downgrade(alembic_config, "0062_run_artifact_types")
            command.upgrade(alembic_config, "head")
            assert _source_datasets(engine, ids) == expected

            # Exercise the 0063 downgrade guard directly from its own revision.
            command.downgrade(alembic_config, "0063_state_graph_nodes")
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO experiments (id, workspace_id, project_id, environment_id, "
                        "dataset_id, status, config, seed, intent) VALUES (:id, :ws, :p1, :env, "
                        ":ds, 'CREATED', '{}'::jsonb, 42, 'compare against the champion')"
                    ),
                    {
                        "id": uuid4(), "ws": ids["ws"], "p1": ids["p1"],
                        "env": ids["env"], "ds": ids["ds_a"],
                    },
                )
            with pytest.raises(Exception, match="0063 downgrade refused"):
                command.downgrade(alembic_config, "-1")
            with engine.connect() as connection:
                head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert head == "0063_state_graph_nodes"
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def _seed_tenant_backfill_rows(engine) -> dict[str, object]:
    """0063 rows in all four 0064 tables for one new workspace.

    Includes a prediction of a locked run and an invocation whose experiment has
    no project (its ``project_id`` must stay NULL after the backfill).
    """

    ids = _seed_state_graph_links(engine)
    ids.update({name: uuid4() for name in (
        "workflow", "run", "input", "prediction", "invocation", "verification",
        "noproj", "noproj_invocation",
    )})
    params = {key: value for key, value in ids.items()}
    statements = (
        "INSERT INTO ml_workflows (id, workspace_id, name, slug, description, "
        "business_objective, status, config) VALUES (:workflow, :ws, 'w', 'w', '', 'o', "
        "'active', '{}'::jsonb)",
        "INSERT INTO workflow_runs (id, workspace_id, workflow_id, project_id, trigger_type, "
        "source_type, status) VALUES (:run, :ws, :workflow, :p1, 'manual', 'dataset', 'queued')",
        "INSERT INTO workflow_run_inputs (id, workflow_run_id, dataset_id, input_role, position) "
        "VALUES (:input, :run, :ds_a, 'training', 0)",
        "INSERT INTO experiment_test_predictions (id, experiment_id, row_index, record_id, "
        "predicted_value) VALUES (:prediction, :single, 0, 'r0', '1'::jsonb)",
        "INSERT INTO llm_invocations (id, workspace_id, workflow_run_id, experiment_id, purpose, "
        "mode, prompt_version, schema_version, input_evidence_digest, redaction_summary, "
        "llm_used, reason, status, validator_verdict, started_at) VALUES (:invocation, :ws, "
        ":run, :single, 'pipeline_audit_routine', 'routine', 'v1', '1', repeat('a', 64), "
        "'{}'::jsonb, false, 'r', 'not_used', 'not_run', now())",
        "INSERT INTO ml_run_verifications (id, run_id, experiment_id, llm_invocation_id, "
        "audit_mode, deterministic_status, deterministic_checks, deterministic_schema_version, "
        "llm_provider, llm_model, llm_status, prompt_version, schema_version, input_digest, "
        "redaction_summary, started_at) SELECT :verification, u.id, :single, :invocation, "
        "'routine', 'PASS', '[]'::jsonb, 1, 'openai', 'm', 'pending', 'v1', 1, repeat('b', 64), "
        "'{}'::jsonb, now() FROM client_lab_uploads AS u WHERE u.experiment_id = :single",
        "INSERT INTO experiments (id, workspace_id, project_id, environment_id, dataset_id, "
        "status, config, seed) VALUES (:noproj, :ws, NULL, :env, :ds_a, 'COMPLETED', "
        "'{}'::jsonb, 42)",
        "INSERT INTO llm_invocations (id, workspace_id, workflow_run_id, experiment_id, purpose, "
        "mode, prompt_version, schema_version, input_evidence_digest, redaction_summary, "
        "llm_used, reason, status, validator_verdict, started_at) VALUES (:noproj_invocation, "
        ":ws, :run, :noproj, 'pipeline_audit_routine', 'routine', 'v1', '1', repeat('e', 64), "
        "'{}'::jsonb, false, 'r', 'not_used', 'not_run', now())",
        # Lock the run so the prediction backfill must pass the named evidence trigger.
        "ALTER TABLE experiments DISABLE TRIGGER experiments_evidence_lock_stamp",
        "UPDATE experiments SET scientific_evidence_locked_at = now() WHERE id = :single",
        "ALTER TABLE experiments ENABLE TRIGGER experiments_evidence_lock_stamp",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement), params)
    return ids


def test_previous_head_tenant_backfill_and_llm_attribution(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from63"
    )
    try:
        command.upgrade(alembic_config, "0063_state_graph_nodes")
        engine = create_engine(database_url)
        try:
            ids = _seed_tenant_backfill_rows(engine)
            other = _seed_tenant_backfill_rows(engine)  # a second workspace
            assert other["ws"] != ids["ws"]
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
            with engine.connect() as connection:
                for seeded in (ids, other):
                    for table, key in (
                        ("workflow_run_inputs", "input"),
                        ("experiment_test_predictions", "prediction"),
                        ("ml_run_verifications", "verification"),
                    ):
                        workspace = connection.execute(
                            text(f"SELECT workspace_id FROM {table} WHERE id = :id"),
                            {"id": seeded[key]},
                        ).scalar()
                        assert workspace == seeded["ws"], table
                nullable = dict(
                    connection.execute(
                        text(
                            "SELECT table_name, is_nullable FROM information_schema.columns "
                            "WHERE column_name = 'workspace_id' AND table_name IN "
                            "('workflow_run_inputs', 'experiment_test_predictions', "
                            "'ml_run_verifications')"
                        )
                    ).all()
                )
                assert set(nullable.values()) == {"NO"} and len(nullable) == 3
                enabled = connection.execute(
                    text(
                        "SELECT tgenabled FROM pg_trigger "
                        "WHERE tgname = 'experiment_test_predictions_evidence_locked'"
                    )
                ).scalar()
                assert enabled == "O"
                project = connection.execute(
                    text("SELECT project_id FROM llm_invocations WHERE id = :id"),
                    {"id": ids["invocation"]},
                ).scalar()
                assert project == ids["p1"]
                unprojected = connection.execute(
                    text("SELECT project_id FROM llm_invocations WHERE id = :id"),
                    {"id": ids["noproj_invocation"]},
                ).scalar()
                assert unprojected is None

            # Nothing project-only yet: downgrade and upgrade are repeatable.
            command.downgrade(alembic_config, "0063_state_graph_nodes")
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO llm_invocations (id, workspace_id, project_id, purpose, "
                        "mode, prompt_version, schema_version, input_evidence_digest, "
                        "redaction_summary, llm_used, reason, status, validator_verdict, "
                        "started_at) VALUES (gen_random_uuid(), :ws, :p1, "
                        "'pipeline_audit_routine', 'routine', 'v1', '1', repeat('c', 64), "
                        "'{}'::jsonb, false, 'r', 'not_used', 'not_run', now())"
                    ),
                    {"ws": ids["ws"], "p1": ids["p1"]},
                )
            with pytest.raises(Exception, match="0064 downgrade refused"):
                command.downgrade(alembic_config, "0063_state_graph_nodes")
            with engine.connect() as connection:
                head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            # One transaction: the refused downgrade leaves the database at head.
            assert head == ScriptDirectory.from_config(alembic_config).get_current_head()
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_previous_head_tenant_backfill_refuses_cross_workspace_input(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="xws63"
    )
    try:
        command.upgrade(alembic_config, "0063_state_graph_nodes")
        engine = create_engine(database_url)
        try:
            ids = _seed_tenant_backfill_rows(engine)
            other = _seed_state_graph_links(engine)
            # Single-column FKs allowed this; the 0064 composite FK must not.
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workflow_run_inputs (id, workflow_run_id, dataset_id, "
                        "input_role, position) VALUES (:id, :run, :dataset, 'foreign', 0)"
                    ),
                    {"id": uuid4(), "run": ids["run"], "dataset": other["ds_a"]},
                )
            with pytest.raises(
                Exception,
                match=r"0064: cross-workspace references: workflow_run_inputs.dataset_id=1, "
                r"ml_run_verifications.experiment_id=0, "
                r"ml_run_verifications.llm_invocation_id=0",
            ):
                command.upgrade(alembic_config, "head")
            with engine.connect() as connection:
                head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                columns = connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'workflow_run_inputs' AND column_name = 'workspace_id'"
                    )
                ).scalar()
            assert head == "0063_state_graph_nodes"
            assert columns == 0
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_previous_head_idempotency_keys_upgrade_is_additive_and_reversible(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from64"
    )
    try:
        command.upgrade(alembic_config, "0064_tenant_llm_attribution")
        engine = create_engine(database_url)
        try:
            ids = _seed_state_graph_links(engine)
            command.upgrade(alembic_config, "0065_idempotency_keys")
            insert = text(
                "INSERT INTO idempotency_keys (id, workspace_id, principal_kind, principal_id, "
                "operation, idempotency_key, request_digest, resource_kind, resource_id, "
                "response_status) VALUES (gen_random_uuid(), :ws, :kind, gen_random_uuid(), "
                "'POST /v1/projects', :key, repeat('a', 64), 'project', :p1, 201)"
            )
            with engine.begin() as connection:
                connection.execute(insert, {"ws": ids["ws"], "kind": "user", "key": "k-1", "p1": ids["p1"]})
            for kind, key, match in (
                ("robot", "k-2", "ck_idempotency_keys_principal_kind"),
                ("user", "bad key", "ck_idempotency_keys_key"),
            ):
                with pytest.raises(Exception, match=match), engine.begin() as connection:
                    connection.execute(insert, {"ws": ids["ws"], "kind": kind, "key": key, "p1": ids["p1"]})
            columns = {
                "request_digest": "repeat('a', 64)", "response_status": "201",
                "operation": "'POST /v1/projects'", "resource_kind": "'project'", "expires_at": "NULL",
            }
            for column, value, match in (
                ("request_digest", "'ABC'", "ck_idempotency_keys_digest"),
                ("response_status", "500", "ck_idempotency_keys_status"),
                ("operation", "'GET /v1/projects'", "ck_idempotency_keys_operation"),
                ("resource_kind", "'Project'", "ck_idempotency_keys_resource_kind"),
                ("expires_at", "now() - interval '1 day'", "ck_idempotency_keys_expiry"),
            ):
                values = {**columns, column: value}
                with pytest.raises(Exception, match=match), engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO idempotency_keys (id, workspace_id, principal_kind, "
                            "principal_id, operation, idempotency_key, request_digest, resource_kind, "
                            "resource_id, response_status, expires_at) VALUES (gen_random_uuid(), "
                            f":ws, 'user', gen_random_uuid(), {values['operation']}, 'k-3', "
                            f"{values['request_digest']}, {values['resource_kind']}, :p1, "
                            f"{values['response_status']}, {values['expires_at']})"
                        ),
                        {"ws": ids["ws"], "p1": ids["p1"]},
                    )
            with pytest.raises(Exception, match="uq_idempotency_keys_scope"), engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO idempotency_keys (id, workspace_id, principal_kind, principal_id, "
                    "operation, idempotency_key, request_digest, resource_kind, resource_id, "
                    "response_status) SELECT gen_random_uuid(), workspace_id, principal_kind, "
                    "principal_id, operation, idempotency_key, request_digest, resource_kind, "
                    "resource_id, response_status FROM idempotency_keys"
                ))
            with pytest.raises(Exception, match="immutable"), engine.begin() as connection:
                connection.execute(text("UPDATE idempotency_keys SET response_status = 200"))
            # DELETE stays available for the ops expiry job and the workspace cascade.
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM idempotency_keys WHERE idempotency_key = 'k-1'"))
                connection.execute(insert, {"ws": ids["ws"], "kind": "user", "key": "k-4", "p1": ids["p1"]})
                cascade = connection.execute(
                    text(
                        "SELECT confdeltype FROM pg_constraint "
                        "WHERE conname = 'idempotency_keys_workspace_id_fkey'"
                    )
                ).scalar()
            assert cascade == "c"

            command.downgrade(alembic_config, "0064_tenant_llm_attribution")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT to_regclass('idempotency_keys')")).scalar() is None
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_previous_head_run_cancellation_widens_job_status_and_guards_downgrade(monkeypatch):
    """0066 (P3.1-B2): additive column + superset status CHECK; downgrade refuses cancelled rows."""

    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="from65"
    )
    try:
        command.upgrade(alembic_config, "0065_idempotency_keys")
        engine = create_engine(database_url)
        try:
            ws = uuid4()
            insert = text(
                "INSERT INTO ml_jobs (id, workspace_id, job_type, handler_key, target_id, status) "
                "VALUES (gen_random_uuid(), :ws, 'auth_cleanup', 'auth.session_cleanup', "
                "gen_random_uuid(), :status)"
            )
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO workspaces (id, slug, name) VALUES (:ws, :slug, 'Jobs')"),
                    {"ws": ws, "slug": f"jobs-{ws.hex[:8]}"},
                )
                connection.execute(insert, {"ws": ws, "status": "running"})
            with pytest.raises(Exception, match="ck_ml_jobs_status_valid"), engine.begin() as connection:
                connection.execute(insert, {"ws": ws, "status": "cancelled"})

            command.upgrade(alembic_config, "0066_run_cancellation")
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT count(*) FROM ml_jobs WHERE cancel_requested_at IS NULL "
                        "AND cancel_requested_by_user_id IS NULL"
                    )
                ).scalar() == 1  # existing rows: never requested
                assert connection.execute(
                    text(
                        "SELECT confdeltype FROM pg_constraint "
                        "WHERE conname = 'ml_jobs_cancel_requested_by_user_id_fkey'"
                    )
                ).scalar() == "n"  # ON DELETE SET NULL
                connection.execute(text("UPDATE ml_jobs SET cancel_requested_at = now()"))
                connection.execute(insert, {"ws": ws, "status": "cancelled"})
                validated = connection.execute(
                    text("SELECT convalidated FROM pg_constraint WHERE conname = 'ck_ml_jobs_status_valid'")
                ).scalar()
            assert validated is True
            with pytest.raises(Exception, match="ck_ml_jobs_status_valid"), engine.begin() as connection:
                connection.execute(insert, {"ws": ws, "status": "cancelling"})
            with pytest.raises(Exception, match="0066 downgrade refused: 1 ml_jobs are cancelled"):
                command.downgrade(alembic_config, "0065_idempotency_keys")
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM ml_jobs WHERE status = 'cancelled'"))
            command.downgrade(alembic_config, "0065_idempotency_keys")
            with engine.connect() as connection:
                column = connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'ml_jobs' AND column_name IN "
                        "('cancel_requested_at', 'cancel_requested_by_user_id')"
                    )
                ).scalar()
            assert column == 0
            with pytest.raises(Exception, match="ck_ml_jobs_status_valid"), engine.begin() as connection:
                connection.execute(insert, {"ws": ws, "status": "cancelled"})
            command.upgrade(alembic_config, "head")
            _assert_head_catalog(engine, alembic_config)
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)
