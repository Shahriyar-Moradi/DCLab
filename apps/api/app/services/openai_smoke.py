"""Opt-in, synthetic-only verification smoke path for developers and admins."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.config import REPO_ROOT
from app.services.openai_provider import OpenAIPipelineAuditProvider, PipelineAuditProvider
from app.services.pipeline_audit_service import validate_advisory_report
from app.services.verification_evidence import build_verification_evidence

SMOKE_MODEL = "gpt-5.6-luna"


class OpenAISmokeError(RuntimeError):
    """A safe error code suitable for a developer-facing smoke command."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _openai_api_key() -> str | None:
    """Read only the standard server-side key; never log it."""
    value = os.environ.get("OPENAI_API_KEY")
    if value:
        return value
    env_path = Path(REPO_ROOT) / ".env"
    if env_path.is_file():
        configured = dotenv_values(env_path).get("OPENAI_API_KEY")
        if isinstance(configured, str) and configured:
            return configured
    return None


def synthetic_smoke_report() -> dict[str, Any]:
    """A static, non-customer report used solely to exercise the live boundary."""
    return {
        "run": {
            "status": "completed",
            "duration_seconds": 0.01,
            "last_successful_stage": "artifact_persistence",
        },
        "dataset": {"category": "synthetic_smoke", "record_count": 12},
        "raw_profile": {
            "row_count": 12,
            "column_count": 2,
            "columns": [
                {
                    "name": "synthetic_feature",
                    "dtype": "float64",
                    "missing_count": 0,
                    "unique_count": 12,
                    "constant": False,
                },
                {
                    "name": "synthetic_outcome",
                    "dtype": "int64",
                    "missing_count": 0,
                    "unique_count": 2,
                    "constant": False,
                },
            ],
        },
        "target_decision": {
            "target_column": "synthetic_outcome",
            "task_type": "binary",
            "source": "synthetic_smoke",
            "confidence": 1.0,
            "reason": "Static smoke fixture; no uploaded dataset is used.",
        },
        "task": {"target": "synthetic_outcome", "task_type": "binary"},
        "cleaning": {"transformations": [], "rows_in": 12, "rows_out": 12},
        "split": {"strategy": "synthetic", "n_train": 9, "n_test": 3},
        "column_role_evidence": {"columns": []},
        "feature_engineering": {"feature_engineering_actions": []},
        "preprocessing": {"fit_partition": "synthetic"},
        "candidate_models": [],
        "selection": {"selection_source": "cross_validation", "locked": True},
        "final_fit": {"fit_partition": "full_train"},
        "final_test_evaluation": {"evaluation_count": 1},
        "predictions_summary": {"count": 3},
        "artifacts": {},
        "stage_timings": [
            {
                "stage": "ml_execution_total",
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:00:00.010000+00:00",
                "duration_ms": 10.0,
                "status": "completed",
            }
        ],
        "deterministic_verification": {
            "schema_version": 1,
            "overall_status": "VERIFIED",
            "checks": [
                {
                    "check_id": "synthetic_evidence",
                    "stage": "pipeline",
                    "status": "PASS",
                    "message": "Static synthetic evidence was assembled.",
                    "evidence_refs": ["run.status"],
                }
            ],
            "summary": "Synthetic deterministic verification passed.",
        },
    }


def run_openai_verification_smoke(
    *,
    api_key: str | None = None,
    provider_factory: Callable[..., PipelineAuditProvider] = OpenAIPipelineAuditProvider,
) -> dict[str, Any]:
    """Call the production Responses path once using synthetic bounded evidence."""
    key = _openai_api_key() if api_key is None else api_key
    if not key:
        raise OpenAISmokeError("OPENAI_API_KEY unavailable")

    report = synthetic_smoke_report()
    package = build_verification_evidence(report)
    deterministic = dict(report["deterministic_verification"])
    provider = provider_factory(api_key=key)
    started = time.perf_counter()
    try:
        advisory = provider.audit(evidence=package.payload, model=SMOKE_MODEL)
        validated = validate_advisory_report(
            advisory,
            deterministic_status=str(deterministic["overall_status"]),
            deterministic_checks=list(deterministic["checks"]),
            allowed_refs=package.evidence_refs,
        )
    except OpenAISmokeError:
        raise
    except Exception as exc:  # never expose provider exception text or a key
        safe_codes = {
            "sdk_unavailable",
            "invalid_structured_output",
            "provider_temporarily_unavailable",
            "provider_authentication_failed",
            "provider_request_failed",
        }
        code = getattr(exc, "code", "provider_request_failed")
        raise OpenAISmokeError(code if code in safe_codes else "provider_request_failed") from exc

    return {
        "provider": "openai",
        "model": SMOKE_MODEL,
        "status": validated.overall_status,
        "request_duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "evidence_digest": package.digest,
    }
