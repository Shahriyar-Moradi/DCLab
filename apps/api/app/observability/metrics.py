"""Low-cardinality metrics (P8.5-A). A no-op until ``setup`` installs a backend.

Every instrument is declared here with the only labels it may carry (a subset of
``labels.ALLOWED_KEYS``). Prometheus names after OTLP translation: counters gain ``_total``
(``dclab_http_requests`` -> ``dclab_http_requests_total``); see ``ops/alerts/dclab.rules.yaml``.
Gauges are computed at collection time by ``gauge_snapshot`` (bounded queries, API process).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from app.observability.labels import clean

logger = logging.getLogger("dclab.observability")

COUNTER, HISTOGRAM, GAUGE = "counter", "histogram", "gauge"
# name -> (kind, unit, description, allowed labels)
INSTRUMENTS: dict[str, tuple[str, str, str, frozenset[str]]] = {
    "dclab_http_requests": (COUNTER, "1", "HTTP requests", frozenset({"route", "method", "status_class"})),
    "dclab_http_request_duration_seconds": (
        HISTOGRAM, "s", "HTTP request duration", frozenset({"route", "method", "status_class"})),
    "dclab_ml_jobs": (COUNTER, "1", "ML jobs finished by the worker", frozenset({"job_type", "handler_key", "status"})),
    "dclab_ml_job_duration_seconds": (HISTOGRAM, "s", "ML job run time", frozenset({"job_type", "status"})),
    "dclab_scheduled_ticks": (COUNTER, "1", "Scheduled-scoring ticks", frozenset({"outcome"})),
    "dclab_scheduled_runs": (COUNTER, "1", "Scheduled-scoring slots handled", frozenset({"outcome"})),
    "dclab_llm_calls": (COUNTER, "1", "AI gateway calls", frozenset({"llm_kind", "provider", "model", "outcome"})),
    "dclab_agent_runs": (COUNTER, "1", "Agent runs", frozenset({"agent_kind", "status"})),
    "dclab_ml_job_queue_depth": (GAUGE, "1", "Queued ML jobs", frozenset()),
    "dclab_ml_job_queue_age_seconds": (GAUGE, "s", "Age of the oldest queued ML job", frozenset()),
    "dclab_worker_heartbeat_age_seconds": (GAUGE, "s", "Seconds since the freshest worker was seen", frozenset()),
    "dclab_workers_seen": (GAUGE, "1", "Worker processes connected to the database", frozenset()),
    "dclab_scoring_schedules_overdue": (GAUGE, "1", "Enabled weekly schedules past their slot", frozenset()),
    "dclab_readiness_ok": (GAUGE, "1", "1 when a readiness check passes, 0 when it fails", frozenset({"check"})),
}
GAUGE_CACHE_SECONDS = 10.0


class MetricsBackend(Protocol):
    def add(self, name: str, value: float, labels: dict[str, Any]) -> None: ...

    def observe(self, name: str, value: float, labels: dict[str, Any]) -> None: ...


_STATE: dict[str, Any] = {"backend": None}


def install(backend: MetricsBackend | None) -> None:
    _STATE["backend"] = backend


def enabled() -> bool:
    return _STATE["backend"] is not None


def labels_for(name: str, labels: dict[str, object]) -> dict[str, Any]:
    allowed = INSTRUMENTS[name][3]
    return clean({key: value for key, value in labels.items() if key in allowed})


def add(name: str, value: float = 1, /, **labels: object) -> None:
    backend = _STATE["backend"]
    if backend is None or value <= 0:
        return
    try:
        backend.add(name, value, labels_for(name, labels))
    except Exception:  # noqa: BLE001 - telemetry never breaks a request or a job
        logger.debug("metric add failed", exc_info=True)


def observe(name: str, value: float, /, **labels: object) -> None:
    backend = _STATE["backend"]
    if backend is None:
        return
    try:
        backend.observe(name, value, labels_for(name, labels))
    except Exception:  # noqa: BLE001
        logger.debug("metric observe failed", exc_info=True)


# --- gauges -----------------------------------------------------------------------------------

_GAUGES: dict[str, Any] = {"source": None, "at": 0.0, "values": {}}
_GAUGE_LOCK = threading.Lock()
GaugeValues = dict[str, list[tuple[float, dict[str, Any]]]]


def set_gauge_source(source: Callable[[], GaugeValues] | None) -> None:
    with _GAUGE_LOCK:
        _GAUGES.update(source=source, at=0.0, values={})


def gauge_values(name: str) -> list[tuple[float, dict[str, Any]]]:
    """The values of one gauge; the snapshot is shared by every gauge for a few seconds."""

    with _GAUGE_LOCK:
        source = _GAUGES["source"]
        if source is None:
            return []
        if time.monotonic() - _GAUGES["at"] > GAUGE_CACHE_SECONDS:
            try:
                _GAUGES["values"] = source()
            except Exception:  # noqa: BLE001 - a failed snapshot exports nothing this round
                logger.warning("gauge snapshot failed")
                _GAUGES["values"] = {}
            _GAUGES["at"] = time.monotonic()
        return [(value, labels_for(name, labels)) for value, labels in _GAUGES["values"].get(name, [])]
