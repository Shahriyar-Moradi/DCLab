"""Opt-in, synthetic-only live check of the gateway's OpenAI adapter for developers and admins.

One call to ``app.agents.gateway.providers.openai.OpenAIProvider`` (the only reader of the
provider key, ADR 0009 §1 rule e) with the verification package of a static synthetic
report: no workspace, no dataset, no ledger row. It still leaves the process and costs
money, so ``live_smoke_refusal`` requires ``--live`` and the platform AI gates
(``AI_ENABLED``, the platform ``global_ai`` and provider / agent switches). Errors are safe
codes, never provider text.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agents.gateway.providers import Provider, ProviderCall, ProviderError
from app.agents.gateway.redaction import UNTRUSTED_NOTICE
from app.agents.governance.platform_default import PLATFORM_DEFAULT
from app.agents.prompt_releases import prompt_text
from app.domain.ml_verification import PipelineAuditReport
from app.services.pipeline_audit_service import AGENT_KEY, AUDITOR_PROMPT_VERSION, validate_advisory_report
from app.services.verification_evidence import build_verification_evidence

SMOKE_MODEL = PLATFORM_DEFAULT.models.roles.verifier.default


def live_smoke_refusal(db: Any, *, live: bool, ai_enabled: bool) -> str | None:
    """Why the live smoke may not run, or ``None``."""
    from app.agents.governance.switches import platform_switches

    if not live:
        return "live_flag_missing"
    blocking = platform_switches(db).blocking(ai_enabled=ai_enabled, agent_key=AGENT_KEY, provider="openai")
    return f"kill_switch:{blocking}" if blocking else None


class OpenAISmokeError(RuntimeError):
    """A safe error code suitable for a developer-facing smoke command."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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


def run_openai_verification_smoke(*, provider: Provider | None = None) -> dict[str, Any]:
    """Call the production provider adapter once using synthetic bounded evidence."""
    from app.agents.gateway.providers.openai import OpenAIProvider

    report = synthetic_smoke_report()
    package = build_verification_evidence(report)
    deterministic = dict(report["deterministic_verification"])
    adapter = provider or OpenAIProvider()
    call = ProviderCall(
        model=SMOKE_MODEL,
        instructions=f"{prompt_text(AGENT_KEY, AUDITOR_PROMPT_VERSION).rstrip()}\n\n{UNTRUSTED_NOTICE}",
        input_json=json.dumps(package.payload, sort_keys=True, separators=(",", ":")),
        output_schema=PipelineAuditReport,
        max_output_tokens=8000,
        temperature=None,
        timeout_s=30.0,
        purpose="pipeline_audit_routine",
        agent_key=AGENT_KEY,
    )
    started = time.perf_counter()
    try:
        result = adapter.complete(call)
        validated = validate_advisory_report(
            PipelineAuditReport.model_validate(result.output),
            deterministic_status=str(deterministic["overall_status"]),
            deterministic_checks=list(deterministic["checks"]),
            allowed_refs=package.evidence_refs,
        )
    except ProviderError as exc:  # never expose provider exception text or a key
        raise OpenAISmokeError(f"provider_{exc.kind}") from exc
    except Exception as exc:
        raise OpenAISmokeError("provider_request_failed") from exc

    return {
        "provider": "openai",
        "model": SMOKE_MODEL,
        "status": validated.overall_status,
        "request_duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "evidence_digest": package.digest,
    }
