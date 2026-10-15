"""P8.6-A: ``app.engine`` stays separable from the server (see scripts/engine_boundary.py).

No database: the checks read source with ``ast``. A new engine import of the server
layers or stack fails; so does a non-engine ``app.*`` import without an allowlist entry,
and an allowlist entry nothing uses any more.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from scripts import engine_boundary as boundary


def _describe(edges) -> list[str]:
    return [f"{e.path}:{e.line} {e.module} -> {e.target}" for e in edges]


def test_engine_imports_no_server_layer_or_server_stack():
    assert _describe(boundary.check().forbidden) == []


def test_every_other_non_engine_app_import_is_allowlisted():
    assert _describe(boundary.check().unlisted) == []


def test_allowlist_is_small_used_and_never_covers_a_forbidden_module():
    result = boundary.check()
    assert [(e.engine, e.target) for e in result.stale] == []
    assert [(e.engine, e.target) for e in result.bad_entries] == []
    assert len(boundary.ALLOWLIST) <= 8, "shrink the allowlist; do not grow it"
    for entry in boundary.ALLOWLIST:
        assert entry.reason.strip() and entry.owner.strip(), entry


def test_engine_owns_the_stage_and_scoring_codes_the_domain_reexports():
    from app.domain import batch_predictions, lab_run_stages
    from app.engine.experiments import stages
    from app.engine.serving import codes

    assert lab_run_stages.SPLITTING is stages.SPLITTING
    assert {stages.SPLITTING, stages.CROSS_VALIDATION, stages.TRAINING} <= lab_run_stages.IN_PROGRESS_STAGES
    assert batch_predictions.SCORING_FAILED is codes.SCORING_FAILED
    assert str(codes.CONTRACT_CHECK_MAX_BYTES) in batch_predictions.CK_BATCH_PREDICTIONS_CONTRACT_BOUNDED


def _plant(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in {"app/__init__.py": "", "app/engine/__init__.py": "", **files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_checker_catches_planted_violations_in_every_import_form(tmp_path):
    src = _plant(
        tmp_path,
        {
            "app/engine/lab/__init__.py": "",
            "app/engine/lab/bad.py": """
                import sqlalchemy.orm
                from app.services import lab_service
                from ...db.models import Experiment
                from app.domain.findings import FindingCheck

                def later():
                    from fastapi import APIRouter
                    import importlib
                    return importlib.import_module("app.api.v1")
            """,
            "app/engine/ok.py": "from app.engine.lab import bad\nimport numpy\n",
        },
    )
    result = boundary.check(engine_root=src / "app" / "engine", src_root=src, allowlist=())
    forbidden = {(e.target, e.lazy) for e in result.forbidden}
    assert forbidden == {
        ("sqlalchemy.orm", False),
        ("app.services.lab_service", False),
        ("app.db.models.Experiment", False),
        ("fastapi.APIRouter", True),
        ("app.api.v1", True),
    }
    assert [e.target for e in result.unlisted] == ["app.domain.findings.FindingCheck"]
    assert not result.ok


def test_checker_reports_stale_and_invalid_allowlist_entries(tmp_path):
    src = _plant(tmp_path, {"app/engine/clean.py": "import pandas\n"})
    entries = (
        boundary.AllowedEdge("app.engine.clean", "app.config", "no longer used", "test"),
        boundary.AllowedEdge("app.engine.clean", "app.db", "can never be allowed", "test"),
    )
    result = boundary.check(engine_root=src / "app" / "engine", src_root=src, allowlist=entries)
    assert result.stale == list(entries)
    assert result.bad_entries == [entries[1]]
