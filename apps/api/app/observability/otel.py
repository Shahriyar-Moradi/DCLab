"""OpenTelemetry backends (P8.5-A). Imported only by ``setup`` when observability is requested;
needs the optional ``[observability]`` extra (``opentelemetry-sdk`` + the OTLP/HTTP exporter)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace as trace_api
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

from app.observability import metrics, tracing

EXPORT_INTERVAL_MS = 30_000


class OtelTracing:
    def __init__(self, provider: TracerProvider) -> None:
        self._tracer = provider.get_tracer("dclab")

    @contextmanager
    def start(self, name: str, attributes: dict[str, Any]) -> Iterator[Any]:
        with self._tracer.start_as_current_span(
            name, attributes=attributes, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                yield span
            except BaseException as exc:
                # The type only: messages and tracebacks can carry customer values.
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    def trace_ids(self) -> tuple[str | None, str | None]:
        context = trace_api.get_current_span().get_span_context()
        if not context.is_valid:
            return None, None
        return f"{context.trace_id:032x}", f"{context.span_id:016x}"


class OtelMetrics:
    def __init__(self, provider: MeterProvider, *, gauges: bool) -> None:
        meter = provider.get_meter("dclab")
        self._instruments: dict[str, Any] = {}
        for name, (kind, unit, description, _labels) in metrics.INSTRUMENTS.items():
            if kind == metrics.COUNTER:
                self._instruments[name] = meter.create_counter(name, unit=unit, description=description)
            elif kind == metrics.HISTOGRAM:
                self._instruments[name] = meter.create_histogram(name, unit=unit, description=description)
            elif gauges:
                meter.create_observable_gauge(
                    name, callbacks=[self._callback(name)], unit=unit, description=description
                )

    @staticmethod
    def _callback(name: str):
        def observe(_options: CallbackOptions) -> Iterator[Observation]:
            for value, labels in metrics.gauge_values(name):
                yield Observation(value, labels)

        return observe

    def add(self, name: str, value: float, labels: dict[str, Any]) -> None:
        self._instruments[name].add(value, labels)

    def observe(self, name: str, value: float, labels: dict[str, Any]) -> None:
        self._instruments[name].record(value, labels)


def install(
    *,
    service_name: str,
    endpoint: str,
    gauges: bool,
    span_exporter: Any = None,
    metric_reader: Any = None,
) -> tuple[TracerProvider, MeterProvider]:
    """Providers are kept local (not the global OTel providers), so nothing else in the
    process starts exporting through them."""

    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    readers: list[Any] = []
    base = endpoint.strip().rstrip("/")
    if span_exporter is not None:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif base:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    if metric_reader is not None:
        readers.append(metric_reader)
    elif base:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        readers.append(PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{base}/v1/metrics"), export_interval_millis=EXPORT_INTERVAL_MS))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    tracing.install(OtelTracing(tracer_provider))
    metrics.install(OtelMetrics(meter_provider, gauges=gauges))
    return tracer_provider, meter_provider
