"""Operational telemetry (P8.5-A): spans, metrics and structured logs, no-op unless configured.

Not the ML pipeline observatory (``services/observability_service``: run events of a build).
Call sites use the helpers below; every label passes ``labels.clean``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.observability import metrics, tracing
from app.observability.logging import job_id_var
from app.observability.tracing import span

# Process setup: ``app.observability.setup.setup("api" | "worker")`` (kept out of this facade so
# the module stays addressable).
__all__ = ["agent_run", "job", "llm_call", "metrics", "scheduled_tick", "span", "tracing"]


class Outcome:
    """Filled in by the caller inside the ``with`` block; read on exit."""

    def __init__(self) -> None:
        self.status: str | None = None
        self.result: Any = None
        self.labels: dict[str, object] = {}


@contextmanager
def job(*, job_id: Any, job_type: str, handler_key: str, attempt: int) -> Iterator[Outcome]:
    """One worker job execution: span, ``dclab_ml_jobs`` and its duration; logs carry ``job_id``."""

    outcome, started = Outcome(), time.perf_counter()
    token = job_id_var.set(str(job_id))
    try:
        with span("ml_job.execute", job_type=job_type, handler_key=handler_key, attempt=attempt) as current:
            try:
                yield outcome
            except BaseException:
                outcome.status = outcome.status or "error"
                raise
            finally:
                status = outcome.status or "unknown"
                current.set(status=status)
                metrics.add("dclab_ml_jobs", job_type=job_type, handler_key=handler_key, status=status)
                metrics.observe("dclab_ml_job_duration_seconds", time.perf_counter() - started,
                                job_type=job_type, status=status)
    finally:
        job_id_var.reset(token)


@contextmanager
def llm_call(kind: str) -> Iterator[Outcome]:
    """One AI gateway call. Set ``outcome.status`` (ok / a refusal code) and ``labels``
    (provider, model): never prompts, outputs or ids."""

    outcome = Outcome()
    with span("llm.call", llm_kind=kind) as current:
        try:
            yield outcome
        finally:
            status = outcome.status or "error"
            current.set(outcome=status, **outcome.labels)
            metrics.add("dclab_llm_calls", llm_kind=kind, outcome=status, **outcome.labels)


@contextmanager
def agent_run(kind: str) -> Iterator[Outcome]:
    outcome = Outcome()
    with span("agent.run", agent_kind=kind) as current:
        try:
            yield outcome
        finally:
            status = outcome.status or "error"
            current.set(status=status)
            metrics.add("dclab_agent_runs", agent_kind=kind, status=status)


@contextmanager
def scheduled_tick() -> Iterator[Outcome]:
    """Set ``outcome.labels`` ``runs_queued`` / ``runs_skipped`` (counts) and ``status``."""

    outcome = Outcome()
    with span("scheduled_scoring.tick") as current:
        try:
            yield outcome
        except BaseException:
            outcome.status = "error"
            raise
        finally:
            status = outcome.status or "ok"
            queued = int(outcome.labels.get("runs_queued", 0) or 0)
            skipped = int(outcome.labels.get("runs_skipped", 0) or 0)
            current.set(outcome=status, runs_queued=queued, runs_skipped=skipped)
            metrics.add("dclab_scheduled_ticks", outcome=status)
            metrics.add("dclab_scheduled_runs", queued, outcome="queued")
            metrics.add("dclab_scheduled_runs", skipped, outcome="skipped")
