"""S0-P01B truth-drift checks pass on the current tree and fail on synthetic violations."""

from __future__ import annotations

from pathlib import Path

from scripts.truth_drift import (
    INSECURE_JWT_SECRET,
    alembic_graph_problems,
    check_alembic_graph,
    check_alembic_metadata,
    check_baseline_counts,
    check_current_status_docs,
    check_docs_links,
    check_openapi_operations_snapshot,
    check_production_secrets,
    check_sdk_routes,
    check_sdk_types,
    check_table_snapshot,
    check_v1_openapi_snapshot,
    collect_reports,
    extract_sdk_v1_paths,
    parse_alembic_revisions,
)


def test_drift_checks_pass_on_current_tree():
    reports = collect_reports(include_alembic_metadata=False)
    failed = [f"{item.check}: {item.problems}" for item in reports if not item.ok]
    assert failed == []


def test_alembic_parser_matches_one_head_0058():
    records = parse_alembic_revisions()
    assert len(records) == 58
    report = check_alembic_graph(records, expected_head="0058_simulation_workspace")
    assert report.ok


def test_duplicate_revision_is_detected(tmp_path: Path):
    versions = tmp_path / "versions"
    versions.mkdir()
    versions.joinpath("a.py").write_text(
        'revision = "0001_one"\ndown_revision = None\n', encoding="utf-8"
    )
    versions.joinpath("b.py").write_text(
        'revision = "0001_one"\ndown_revision = None\n', encoding="utf-8"
    )
    problems = alembic_graph_problems(parse_alembic_revisions(versions))
    assert any("duplicate" in item for item in problems)


def test_multiple_heads_are_detected(tmp_path: Path):
    versions = tmp_path / "versions"
    versions.mkdir()
    versions.joinpath("a.py").write_text(
        'revision = "0001_root"\ndown_revision = None\n', encoding="utf-8"
    )
    versions.joinpath("b.py").write_text(
        'revision = "0002_left"\ndown_revision = "0001_root"\n', encoding="utf-8"
    )
    versions.joinpath("c.py").write_text(
        'revision = "0003_right"\ndown_revision = "0001_root"\n', encoding="utf-8"
    )
    report = check_alembic_graph(parse_alembic_revisions(versions), expected_head="0002_left")
    assert not report.ok
    assert any("one Alembic head" in item for item in report.problems)


def test_table_snapshot_detects_added_and_removed_tables():
    snapshot = ["workspaces", "projects"]
    report = check_table_snapshot(
        tables=["workspaces", "experiments"], snapshot=snapshot
    )
    assert not report.ok
    blob = " ".join(report.problems)
    assert "projects" in blob
    assert "experiments" in blob


def test_v1_snapshot_detects_removed_path():
    snapshot = {
        "paths": {
            "/v1/me": {
                "GET": {
                    "operationId": "read_principal_v1_me_get",
                    "parameters": [],
                    "requestSchema": None,
                    "successSchema": "PrincipalRead",
                    "statusCodes": ["200"],
                }
            }
        },
        "sdk_schemas": {},
    }
    actual = {"paths": {}, "components": {"schemas": {}}}
    report = check_v1_openapi_snapshot(actual, snapshot)
    assert not report.ok
    assert "intentional contract change" in report.problems[0]


def test_openapi_operations_detect_breaking_removal_and_additive_drift():
    snapshot = ["GET /v1/me", "POST /v1/execution-requests"]
    report = check_openapi_operations_snapshot(
        actual=["GET /v1/me", "GET /v1/new"],
        snapshot=snapshot,
    )
    assert not report.ok
    blob = " ".join(report.problems)
    assert "POST /v1/execution-requests" in blob
    assert "GET /v1/new" in blob


def test_sdk_route_extract_and_mismatch():
    source = (
        'self._transport.request("GET", "/v1/me")\n'
        'self._transport.request("GET", f"/v1/projects/{_id(project_id)}")\n'
    )
    paths = extract_sdk_v1_paths(source)
    assert "/v1/me" in paths
    assert "/v1/projects/{project_id}" in paths
    report = check_sdk_routes(
        sdk_paths=["/v1/me", "/v1/does-not-exist"],
        openapi_paths=["/v1/me", "/v1/workspaces"],
    )
    assert not report.ok
    blob = " ".join(report.problems)
    assert "/v1/does-not-exist" in blob
    assert "/v1/workspaces" in blob


def test_sdk_types_detect_missing_required_field():
    report = check_sdk_types(
        sdk_fields={"PrincipalRead": {"id", "email"}},
        openapi_schemas={
            "PrincipalRead": {
                "properties": ["id", "email", "role"],
                "required": ["id", "email", "role"],
            }
        },
    )
    assert not report.ok
    assert any("role" in item for item in report.problems)


def test_baseline_count_drift():
    report = check_baseline_counts(
        baseline={
            "alembic_heads": ["0054_execution_needs_input"],
            "alembic_revision_count": 54,
            "sqlalchemy_table_count": 64,
            "openapi_path_count": 150,
            "openapi_operation_count": 157,
            "v1_operation_count": 13,
        },
        heads=["0054_execution_needs_input"],
        revision_count=54,
        table_count=64,
        path_count=150,
        operation_count=999,
        v1_count=13,
    )
    assert not report.ok
    assert any("openapi_operation_count" in item for item in report.problems)


def test_docs_links_fail_on_missing_target(tmp_path: Path):
    page = tmp_path / "page.md"
    page.write_text("[missing](no-such-file.md)\n", encoding="utf-8")
    report = check_docs_links([page], repo_root=tmp_path)
    assert not report.ok
    assert any("no-such-file.md" in item for item in report.problems)


def test_docs_links_pass_on_existing_relative_target(tmp_path: Path):
    target = tmp_path / "ok.md"
    target.write_text("# ok\n", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text("[ok](ok.md)\n", encoding="utf-8")
    report = check_docs_links([page], repo_root=tmp_path)
    assert report.ok


def test_historical_banner_required(tmp_path: Path):
    hist = tmp_path / "old.md"
    hist.write_text("# Old report\nAlembic 0027\n", encoding="utf-8")
    truth = tmp_path / "current.md"
    truth.write_text(
        "**Status:** CURRENT\nHead `0054_execution_needs_input`\n", encoding="utf-8"
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "S0_P01A_CURRENT_TRUTH.md CURRENT HISTORICAL 0054_execution_needs_input\n",
        encoding="utf-8",
    )
    index = tmp_path / "index.md"
    index.write_text(
        "S0_P01A_CURRENT_TRUTH.md HISTORICAL 0054_execution_needs_input\n",
        encoding="utf-8",
    )
    report = check_current_status_docs(
        repo_root=tmp_path,
        expected_head="0054_execution_needs_input",
        historical_files=["old.md"],
        current_truth=truth,
        ledger=ledger,
        index=index,
        current_scan_files=[],
    )
    assert not report.ok
    assert any("HISTORICAL" in item for item in report.problems)


def test_current_doc_with_wrong_head_is_contradiction(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    stale = tmp_path / "stale.md"
    stale.write_text(
        "**Status:** CURRENT\nAlembic head `0027_repair_legacy_tenant_lineage`\n",
        encoding="utf-8",
    )
    truth = tmp_path / "current.md"
    truth.write_text(
        "**Status:** CURRENT\n0054_execution_needs_input\n", encoding="utf-8"
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "S0_P01A_CURRENT_TRUTH.md HISTORICAL 0054_execution_needs_input\n",
        encoding="utf-8",
    )
    hist = tmp_path / "old.md"
    hist.write_text("> **Status: HISTORICAL.**\n", encoding="utf-8")
    report = check_current_status_docs(
        repo_root=tmp_path,
        expected_head="0054_execution_needs_input",
        historical_files=["old.md"],
        current_truth=truth,
        ledger=ledger,
        index=ledger,
        current_scan_files=["stale.md"],
    )
    assert not report.ok
    assert any("0027_repair_legacy_tenant_lineage" in item for item in report.problems)


def test_production_env_rejects_default_jwt():
    report = check_production_secrets(
        tracked_files=[],
        environ={"DCLAB_ENV": "production"},
        jwt_secret=INSECURE_JWT_SECRET,
    )
    assert not report.ok
    assert any("production" in item for item in report.problems)


def test_insecure_secret_in_compose_is_rejected(tmp_path: Path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        f"JWT_SECRET: {INSECURE_JWT_SECRET}\n", encoding="utf-8"
    )
    report = check_production_secrets(
        repo_root=tmp_path,
        tracked_files=["docker-compose.yml"],
        environ={},
        jwt_secret="unique-deployed-secret",
    )
    assert not report.ok
    assert any("docker-compose.yml" in item for item in report.problems)


def test_alembic_metadata_reports_synthetic_diffs():
    report = check_alembic_metadata(diffs=[("add_table", "agents")])
    assert not report.ok
    assert any("agents" in item for item in report.problems)


def test_alembic_metadata_passes_when_no_diffs():
    assert check_alembic_metadata(diffs=[]).ok
