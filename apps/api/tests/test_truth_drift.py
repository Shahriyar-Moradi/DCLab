"""S0-P01B truth-drift checks pass on the current tree and fail on synthetic violations."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from scripts.generate_truth_artifacts import verify_idempotent
from scripts.record_repo_truth import collect as collect_repo_truth
from scripts.truth_drift import (
    INSECURE_JWT_SECRET,
    alembic_graph_problems,
    check_alembic_graph,
    check_alembic_metadata,
    check_baseline_counts,
    check_current_status_docs,
    check_docs_links,
    check_generated_artifacts,
    check_openapi_operations_snapshot,
    check_production_secrets,
    check_sdk_routes,
    check_sdk_types,
    check_table_snapshot,
    check_tracked_generated_output,
    check_v1_openapi_snapshot,
    collect_reports,
    extract_sdk_v1_paths,
    generated_file_contents,
    parse_alembic_revisions,
)


def test_repo_truth_is_deterministic_and_records_lineage_shas():
    first = collect_repo_truth()
    second = collect_repo_truth()
    assert first == second
    assert "recorded_at" not in first
    assert first["git"]["product_sha"]
    assert first["git"]["documentation_sha"]


def test_drift_checks_pass_on_current_tree():
    reports = collect_reports(include_alembic_metadata=False)
    failed = [f"{item.check}: {item.problems}" for item in reports if not item.ok]
    assert failed == []


def test_alembic_parser_matches_one_head_0059():
    records = parse_alembic_revisions()
    assert len(records) == 59
    report = check_alembic_graph(records, expected_head="0059_auth_session_constraints")
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


def test_stale_generated_artifact_is_detected():
    report = check_generated_artifacts(
        expected_files={"contracts/example.json": "expected\n"},
        actual_files={"contracts/example.json": "stale\n"},
    )
    assert not report.ok
    assert "contracts/example.json" in report.problems[0]
    assert check_generated_artifacts(
        expected_files={"contracts/example.json": "expected\n"},
        actual_files={"contracts/example.json": "expected\n"},
    ).ok


def test_generated_manifest_has_source_sha_and_artifact_digests():
    fixture_source_sha = "a" * 64
    files = generated_file_contents(source_sha=fixture_source_sha)
    manifest = json.loads(files["contracts/truth_manifest.json"])
    assert manifest["generator"]["name"] == "scripts.generate_truth_artifacts"
    assert manifest["generator"]["version"] == "1.0.0"
    assert manifest["source_sha"] == fixture_source_sha
    for rel, metadata in manifest["artifacts"].items():
        assert metadata["sha256"] == hashlib.sha256(
            files[rel].encode("utf-8")
        ).hexdigest()


def test_two_generations_are_identical_and_leave_a_clean_git_diff(tmp_path: Path):
    assert verify_idempotent(output_root=tmp_path) == []
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "contracts"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Truth Fixture",
            "-c",
            "user.email=truth-fixture@invalid.example",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture baseline",
        ],
        cwd=tmp_path,
        check=True,
    )
    assert verify_idempotent(output_root=tmp_path) == []
    clean = subprocess.run(
        ["git", "diff", "--exit-code", "--", "contracts"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr


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


def test_docs_links_ignore_link_examples_in_code(tmp_path: Path):
    page = tmp_path / "page.md"
    page.write_text(
        "`[inline](inline-missing.md)`\n\n"
        "```text\n[fenced](fenced-missing.md)\n```\n",
        encoding="utf-8",
    )
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


def test_current_indexes_can_delegate_head_to_canonical_artifact(tmp_path: Path):
    truth = tmp_path / "current.md"
    truth.write_text(
        "**Status:** CURRENT\n[truth](contracts/truth_baseline.json)\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "S0_P01A_CURRENT_TRUTH.md HISTORICAL truth_baseline.json\n",
        encoding="utf-8",
    )
    historical = tmp_path / "old.md"
    historical.write_text("> **Status: HISTORICAL.**\n", encoding="utf-8")
    report = check_current_status_docs(
        repo_root=tmp_path,
        expected_head="0058_simulation_workspace",
        historical_files=["old.md"],
        current_truth=truth,
        ledger=ledger,
        index=ledger,
        current_scan_files=[],
    )
    assert report.ok


def test_current_truth_must_name_current_product_sha(tmp_path: Path):
    truth = tmp_path / "current.md"
    truth.write_text(
        "**Status:** CURRENT\n0058_simulation_workspace\nold-sha\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "S0_P01A_CURRENT_TRUTH.md HISTORICAL 0058_simulation_workspace\n",
        encoding="utf-8",
    )
    historical = tmp_path / "old.md"
    historical.write_text("> **Status: HISTORICAL.**\n", encoding="utf-8")
    report = check_current_status_docs(
        repo_root=tmp_path,
        expected_head="0058_simulation_workspace",
        expected_product_sha="current-product-sha",
        historical_files=["old.md"],
        current_truth=truth,
        ledger=ledger,
        index=ledger,
        current_scan_files=[],
    )
    assert not report.ok
    assert any("current-product-sha" in item for item in report.problems)


def test_current_truth_can_cover_the_product_commit_that_contains_it(tmp_path: Path):
    truth = tmp_path / "current.md"
    truth.write_text(
        "**Status:** CURRENT\n0058_simulation_workspace\nparent-product-sha\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "S0_P01A_CURRENT_TRUTH.md HISTORICAL 0058_simulation_workspace\n",
        encoding="utf-8",
    )
    historical = tmp_path / "old.md"
    historical.write_text("> **Status: HISTORICAL.**\n", encoding="utf-8")
    report = check_current_status_docs(
        repo_root=tmp_path,
        expected_head="0058_simulation_workspace",
        expected_product_sha="combined-product-and-truth-commit",
        current_truth_commit_sha="combined-product-and-truth-commit",
        historical_files=["old.md"],
        current_truth=truth,
        ledger=ledger,
        index=ledger,
        current_scan_files=[],
    )
    assert report.ok


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


def test_tracked_runtime_artifacts_are_detected_with_bounded_fixture():
    object_store, playwright = check_tracked_generated_output(
        object_store_rule_present=False,
        tracked_object_store=["data/object_store/private.csv"],
        playwright_missing_rules=["apps/web/playwright-report/"],
        tracked_playwright=["apps/web/test-results/trace.zip"],
    )
    assert not object_store.ok
    assert "private.csv" in " ".join(object_store.problems)
    assert not playwright.ok
    assert "trace.zip" in " ".join(playwright.problems)


def test_alembic_metadata_reports_synthetic_diffs():
    report = check_alembic_metadata(diffs=[("add_table", "agents")])
    assert not report.ok
    assert any("agents" in item for item in report.problems)


def test_alembic_metadata_passes_when_no_diffs():
    assert check_alembic_metadata(diffs=[]).ok
