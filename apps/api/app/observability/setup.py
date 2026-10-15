"""One entry point per process (P8.5-A): ``setup("api")`` from the app lifespan and
``setup("worker")`` from ``dclab worker run``.

Logging is configured always (``LOG_FORMAT``, ``LOG_LEVEL``). OpenTelemetry is requested only by
``OTEL_EXPORTER_OTLP_ENDPOINT`` or ``OBSERVABILITY_ENABLED=true``; otherwise nothing from the OTel
SDK is imported and every span / metric call is a no-op. Requested without the
``[observability]`` extra installed: one warning, and the process stays no-op.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.observability import metrics, tracing
from app.observability.logging import configure_logging

logger = logging.getLogger("dclab.observability")
PROCESSES = ("api", "worker", "cli")
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"process": None, "providers": None, "warned": False}


def requested(settings: Any) -> bool:
    return bool(str(getattr(settings, "otel_exporter_otlp_endpoint", "") or "").strip()) or bool(
        getattr(settings, "observability_enabled", False)
    )


def setup(process: str, *, settings: Any = None, span_exporter: Any = None, metric_reader: Any = None) -> bool:
    """Idempotent. Returns True when OpenTelemetry is active in this process."""

    if process not in PROCESSES:
        raise ValueError(f"unknown process {process!r}")
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    configure_logging(log_format=settings.log_format, log_level=settings.log_level)
    with _LOCK:
        if _STATE["providers"] is not None:
            return True
        if not requested(settings):
            return False
        try:
            from app.observability import otel
        except ImportError:
            if not _STATE["warned"]:
                _STATE["warned"] = True
                logger.warning(
                    "observability requested but the [observability] extra is not installed; "
                    "traces and metrics stay off"
                )
            return False
        _STATE["providers"] = otel.install(
            service_name=f"{settings.otel_service_name}-{process}",
            endpoint=str(settings.otel_exporter_otlp_endpoint or ""),
            gauges=process == "api",
            span_exporter=span_exporter,
            metric_reader=metric_reader,
        )
        _STATE["process"] = process
    if process == "api":
        from app.services.system_health_service import gauge_snapshot

        metrics.set_gauge_source(gauge_snapshot)
    return True


def shutdown() -> None:
    """Flush and drop the providers (process exit, tests)."""

    with _LOCK:
        providers = _STATE["providers"]
        _STATE.update(process=None, providers=None)
    tracing.install(None)
    metrics.install(None)
    metrics.set_gauge_source(None)
    if providers is None:
        return
    for provider in providers:
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            logger.debug("observability shutdown failed", exc_info=True)
