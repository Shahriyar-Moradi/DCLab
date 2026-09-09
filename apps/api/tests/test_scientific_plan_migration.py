"""0039 scientific-plan backfill is frozen and must not follow live service changes."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from conftest import ADMIN_URL

PREVIOUS_REVISION = "0038_runtime_env_lock_scope"
MIGRATION_0039 = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0039_scientific_plans.py"
)
DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"

NESTED_RESULT = {
    "holdout_plan": {
        "plan_version": "dclab.holdout_plan.v1",
        "strategy": "stratified_random",
        "test_size": 0.2,
        "random_state": 42,
        "stratified": True,
        "group_column": None,
        "time_column": None,
        "reason": "binary random holdout",
    },
    "model_development_plan": {
        "plan_version": "dclab.model_development_plan.v1",
        "problem_profile": {"task_type": "binary", "version": "dclab.problem_profile.v1"},
        "validation_plan": {
            "version": "dclab.validation_plan.v1",
            "strategy": "StratifiedKFold",
            "requested_folds": 5,
            "actual_folds": 5,
            "shuffle": True,
            "random_state": 42,
            "group_column": None,
            "time_column": None,
            "stratified": True,
            "reason": "binary stratified",
        },
        "metric_plan": {
            "version": "dclab.metric_plan.v1",
            "primary_metric": "pr_auc",
            "reason": "binary",
        },
        "allowed_features": ["age", "income"],
        "excluded_features": [{"column": "customer_id", "risk": "HIGH"}],
        "group_column": None,
        "time_column": None,
    },
    "split": {"n_train": 80, "n_test": 20, "strategy": "stratified_random"},
    "validation_plan": {"strategy": "KFold"},
    "metric_plan": {"primary_metric": "accuracy"},
}

FALLBACK_RESULT = {
    "holdout_plan": {
        "strategy": "group_disjoint",
        "test_size": 0.25,
        "group_column": "customer_id",
    },
    "model_development_plan": {
        "problem_profile": {"task_type": "binary"},
        "allowed_features": ["x"],
        "excluded_features": [],
    },
    "validation_plan": {
        "strategy": "StratifiedGroupKFold",
        "requested_folds": None,
        "actual_folds": 3,
        "group_column": "should_not_win",
    },
    "metric_plan": {"primary_metric": "roc_auc"},
    "split": {"n_train": 10},
}

TEMPORAL_RESULT = {
    "holdout_plan": {"strategy": "temporal_future", "test_size": 0.3},
    "model_development_plan": {
        "problem_profile": {"task_type": "regression"},
        "time_column": "as_of_date",
        "allowed_features": ["revenue_lag"],
        "excluded_features": ["leak"],
    },
    "validation_plan": {
        "strategy": "TimeSeriesSplit",
        "requested_folds": 4,
        "actual_folds": 4,
        "time_column": "ignored",
    },
    "metric_plan": {"primary_metric": "mae"},
}

INCOMPLETE_RESULT = {
    "holdout_plan": {"strategy": "random"},
    "model_development_plan": {"problem_profile": {"task_type": "binary"}},
}

# Captured from the live mapper at the moment 0039 was frozen. If the live
# service later changes, keep these expected dicts and the migration copy.
GOLDEN_NESTED = {
    "actual_folds": 5,
    "allowed_feature_count": 2,
    "excluded_feature_count": 1,
    "full_plan": {
        "holdout_plan": {
            "group_column": None,
            "plan_version": "dclab.holdout_plan.v1",
            "random_state": 42,
            "reason": "binary random holdout",
            "strategy": "stratified_random",
            "stratified": True,
            "test_size": 0.2,
            "time_column": None,
        },
        "metric_plan": {
            "primary_metric": "pr_auc",
            "reason": "binary",
            "version": "dclab.metric_plan.v1",
        },
        "model_development_plan": {
            "allowed_features": ["age", "income"],
            "excluded_features": [{"column": "customer_id", "risk": "HIGH"}],
            "group_column": None,
            "metric_plan": {
                "primary_metric": "pr_auc",
                "reason": "binary",
                "version": "dclab.metric_plan.v1",
            },
            "plan_version": "dclab.model_development_plan.v1",
            "problem_profile": {
                "task_type": "binary",
                "version": "dclab.problem_profile.v1",
            },
            "time_column": None,
            "validation_plan": {
                "actual_folds": 5,
                "group_column": None,
                "random_state": 42,
                "reason": "binary stratified",
                "requested_folds": 5,
                "shuffle": True,
                "strategy": "StratifiedKFold",
                "stratified": True,
                "time_column": None,
                "version": "dclab.validation_plan.v1",
            },
        },
        "split": {"n_test": 20, "n_train": 80, "strategy": "stratified_random"},
        "validation_plan": {
            "actual_folds": 5,
            "group_column": None,
            "random_state": 42,
            "reason": "binary stratified",
            "requested_folds": 5,
            "shuffle": True,
            "strategy": "StratifiedKFold",
            "stratified": True,
            "time_column": None,
            "version": "dclab.validation_plan.v1",
        },
    },
    "group_column": None,
    "holdout_plan_digest": "7580669e81ac5e1765e5b21a0ae637022758f0d77c7ebdba7bce14f0990c9607",
    "holdout_strategy": "stratified_random",
    "holdout_test_size": 0.2,
    "model_development_plan_digest": (
        "647c2233337b7a6045b41ffac44f750827538e7f673838177770f4700c30759a"
    ),
    "primary_metric": "pr_auc",
    "requested_folds": 5,
    "task_type": "binary",
    "time_column": None,
    "validation_strategy": "StratifiedKFold",
}

GOLDEN_FALLBACK = {
    "actual_folds": 3,
    "allowed_feature_count": 1,
    "excluded_feature_count": 0,
    "full_plan": {
        "holdout_plan": {
            "group_column": "customer_id",
            "strategy": "group_disjoint",
            "test_size": 0.25,
        },
        "metric_plan": {"primary_metric": "roc_auc"},
        "model_development_plan": {
            "allowed_features": ["x"],
            "excluded_features": [],
            "metric_plan": {"primary_metric": "roc_auc"},
            "problem_profile": {"task_type": "binary"},
            "validation_plan": {
                "actual_folds": 3,
                "group_column": "should_not_win",
                "requested_folds": None,
                "strategy": "StratifiedGroupKFold",
            },
        },
        "split": {"n_train": 10},
        "validation_plan": {
            "actual_folds": 3,
            "group_column": "should_not_win",
            "requested_folds": None,
            "strategy": "StratifiedGroupKFold",
        },
    },
    "group_column": "customer_id",
    "holdout_plan_digest": "7f933cb7f0289a38077df15a2452fc3820bdad1de2f126b8cdcf904a8270cb1a",
    "holdout_strategy": "group_disjoint",
    "holdout_test_size": 0.25,
    "model_development_plan_digest": (
        "b0cffba4eab69b53d4495899edb8a10dea14673266caf0e486a10dcb89220117"
    ),
    "primary_metric": "roc_auc",
    "requested_folds": 5,
    "task_type": "binary",
    "time_column": None,
    "validation_strategy": "StratifiedGroupKFold",
}

GOLDEN_TEMPORAL = {
    "actual_folds": 4,
    "allowed_feature_count": 1,
    "excluded_feature_count": 1,
    "full_plan": {
        "holdout_plan": {"strategy": "temporal_future", "test_size": 0.3},
        "metric_plan": {"primary_metric": "mae"},
        "model_development_plan": {
            "allowed_features": ["revenue_lag"],
            "excluded_features": ["leak"],
            "metric_plan": {"primary_metric": "mae"},
            "problem_profile": {"task_type": "regression"},
            "time_column": "as_of_date",
            "validation_plan": {
                "actual_folds": 4,
                "requested_folds": 4,
                "strategy": "TimeSeriesSplit",
                "time_column": "ignored",
            },
        },
        "split": {},
        "validation_plan": {
            "actual_folds": 4,
            "requested_folds": 4,
            "strategy": "TimeSeriesSplit",
            "time_column": "ignored",
        },
    },
    "group_column": None,
    "holdout_plan_digest": "146d3c6263d9233818b7cd3c7bc5c97ba9a5e3e94f79d44bf18df32b7ac52278",
    "holdout_strategy": "temporal_future",
    "holdout_test_size": 0.3,
    "model_development_plan_digest": (
        "4b1686e5ca5638cadcacb4e92288b16d896a757de856a680c4c39fad0c1ffb26"
    ),
    "primary_metric": "mae",
    "requested_folds": 4,
    "task_type": "regression",
    "time_column": "as_of_date",
    "validation_strategy": "TimeSeriesSplit",
}

GOLDEN_CASES = (
    (NESTED_RESULT, GOLDEN_NESTED),
    (FALLBACK_RESULT, GOLDEN_FALLBACK),
    (TEMPORAL_RESULT, GOLDEN_TEMPORAL),
    (INCOMPLETE_RESULT, None),
)

PLAN_SELECT = """
    SELECT
        task_type, holdout_strategy, holdout_test_size,
        validation_strategy, requested_folds, actual_folds,
        primary_metric, group_column, time_column,
        allowed_feature_count, excluded_feature_count,
        holdout_plan_digest, model_development_plan_digest, full_plan
    FROM pipeline_scientific_plans
    WHERE pipeline_run_id = :id
"""


def _load_frozen_mapper():
    spec = importlib.util.spec_from_file_location(
        "alembic_0039_scientific_plans", MIGRATION_0039
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scientific_plan_columns_from_payloads


def _columns_from(mapper, result: dict):
    return mapper(
        holdout_plan=result.get("holdout_plan"),
        development_plan=result.get("model_development_plan"),
        split=result.get("split"),
        validation_plan=result.get("validation_plan"),
        metric_plan=result.get("metric_plan"),
    )


def test_0039_does_not_import_live_service_modules():
    source = MIGRATION_0039.read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0] if node.level == 0 else "relative")
            imported.add(node.module)
    assert "app.services.scientific_lineage_service" not in source
    assert "scientific_lineage_service" not in imported
    assert not any(module.startswith("app.services") for module in imported)
    assert "from app.services" not in source


def test_frozen_0039_mapper_matches_golden_payloads_field_for_field():
    frozen = _load_frozen_mapper()
    from app.services.scientific_lineage_service import (
        scientific_plan_columns_from_payloads as live,
    )

    for result, expected in GOLDEN_CASES:
        got = _columns_from(frozen, result)
        assert got == expected
        # Freeze-time copy check. If live persistence later changes, keep the
        # golden dicts and the 0039 copy; drop this comparison, do not edit 0039.
        assert _columns_from(live, result) == expected


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _alembic_head(alembic_config: Config) -> str:
    return ScriptDirectory.from_config(alembic_config).get_current_head()


def _isolated_database(monkeypatch):
    admin_url = make_url(ADMIN_URL)
    database_name = f"decisionai_plan_{uuid4().hex[:12]}"
    database_url = admin_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment availability
        admin_engine.dispose()
        pytest.skip(f"cannot create isolated scientific-plan database: {exc}")
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


def _insert_legacy_runs(connection, rows: list[tuple[object, dict | None]]) -> None:
    environment_id = uuid4()
    task_id = uuid4()
    connection.execute(
        text("INSERT INTO environments (id, org_id, name) VALUES (:id, 'legacy', 'Legacy env')"),
        {"id": environment_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO prediction_tasks (id, environment_id, slug, name, spec)
            VALUES (:id, :environment_id, 'legacy-task', 'Legacy task', '{}'::jsonb)
            """
        ),
        {"id": task_id, "environment_id": environment_id},
    )
    for experiment_id, result in rows:
        asset_id = uuid4()
        dataset_id = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO dataset_assets (id, workspace_id, name, slug, description)
                VALUES (:id, :workspace_id, :name, :slug, 'Legacy fixture asset')
                """
            ),
            {
                "id": asset_id,
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "name": f"asset-{experiment_id.hex[:8]}",
                "slug": f"asset-{experiment_id.hex[:12]}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO datasets
                    (id, environment_id, name, source_type, location, version,
                     workspace_id, dataset_asset_id)
                VALUES
                    (:id, :environment_id, :name, 'file', '/legacy.csv', 'v1',
                     :workspace_id, :asset_id)
                """
            ),
            {
                "id": dataset_id,
                "environment_id": environment_id,
                "name": f"dataset-{experiment_id.hex[:8]}",
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "asset_id": asset_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO experiments
                    (id, environment_id, task_id, dataset_id, config, workspace_id, result)
                VALUES
                    (:id, :environment_id, :task_id, :dataset_id, '{}'::jsonb,
                     :workspace_id, CAST(:result AS jsonb))
                """
            ),
            {
                "id": experiment_id,
                "environment_id": environment_id,
                "task_id": task_id,
                "dataset_id": dataset_id,
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "result": json.dumps(result) if result is not None else None,
            },
        )


def _assert_plan_matches(row, expected: dict) -> None:
    for key, value in expected.items():
        if key == "holdout_test_size":
            assert row[key] == pytest.approx(value)
        else:
            assert row[key] == value, key


def test_fresh_empty_database_upgrades_to_head(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch
    )
    try:
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                _alembic_head(alembic_config)
            )
            assert connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'pipeline_scientific_plans' "
                    "AND column_name = 'holdout_plan_digest'"
                )
            ).scalar() == "holdout_plan_digest"
            assert (
                connection.execute(text("SELECT COUNT(*) FROM pipeline_scientific_plans")).scalar()
                == 0
            )
        engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_upgrade_from_0038_to_head_backfills_legacy_fixture(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch
    )
    nested_id = uuid4()
    fallback_id = uuid4()
    temporal_id = uuid4()
    incomplete_id = uuid4()
    empty_id = uuid4()
    try:
        command.upgrade(alembic_config, PREVIOUS_REVISION)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            assert not connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'pipeline_scientific_plans'"
                )
            ).scalar()
            _insert_legacy_runs(
                connection,
                [
                    (nested_id, NESTED_RESULT),
                    (fallback_id, FALLBACK_RESULT),
                    (temporal_id, TEMPORAL_RESULT),
                    (incomplete_id, INCOMPLETE_RESULT),
                    (empty_id, None),
                ],
            )
        engine.dispose()

        command.upgrade(alembic_config, "head")
        command.check(alembic_config)

        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                _alembic_head(alembic_config)
            )
            nested = connection.execute(text(PLAN_SELECT), {"id": nested_id}).mappings().one()
            fallback = connection.execute(text(PLAN_SELECT), {"id": fallback_id}).mappings().one()
            temporal = connection.execute(text(PLAN_SELECT), {"id": temporal_id}).mappings().one()
            _assert_plan_matches(nested, GOLDEN_NESTED)
            _assert_plan_matches(fallback, GOLDEN_FALLBACK)
            _assert_plan_matches(temporal, GOLDEN_TEMPORAL)
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM pipeline_scientific_plans WHERE pipeline_run_id = :id"),
                    {"id": incomplete_id},
                ).scalar()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM pipeline_scientific_plans WHERE pipeline_run_id = :id"),
                    {"id": empty_id},
                ).scalar()
                == 0
            )
            assert connection.execute(text("SELECT COUNT(*) FROM pipeline_scientific_plans")).scalar() == 3
        engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_0039_downgrade_upgrade_round_trip_preserves_backfill(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch
    )
    nested_id = uuid4()
    fallback_id = uuid4()
    try:
        command.upgrade(alembic_config, PREVIOUS_REVISION)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            _insert_legacy_runs(
                connection,
                [(nested_id, NESTED_RESULT), (fallback_id, FALLBACK_RESULT)],
            )
        engine.dispose()

        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            first = {
                row_id: dict(connection.execute(text(PLAN_SELECT), {"id": row_id}).mappings().one())
                for row_id in (nested_id, fallback_id)
            }
        engine.dispose()

        from app.config import get_settings

        get_settings.cache_clear()
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert not connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'pipeline_scientific_plans'"
                )
            ).scalar()
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                PREVIOUS_REVISION
            )
        engine.dispose()

        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            for row_id, expected in (
                (nested_id, GOLDEN_NESTED),
                (fallback_id, GOLDEN_FALLBACK),
            ):
                row = dict(connection.execute(text(PLAN_SELECT), {"id": row_id}).mappings().one())
                _assert_plan_matches(row, expected)
                _assert_plan_matches(row, first[row_id])
        engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)
