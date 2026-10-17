"""Spans (P8.5-A). A no-op until ``setup`` installs a backend; attributes pass ``labels.clean``.

Exceptions mark a span failed with the exception *type* only: messages and stack traces can
carry customer values, so they are never recorded on a span.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Any, Protocol

from app.observability.labels import clean

logger = logging.getLogger("dclab.observability")
_NAME = re.compile(r"^[A-Za-z0-9_.:/{}<>\- ]{1,160}$")


class SpanHandle(Protocol):
    def set(self, **attributes: object) -> None: ...

    def rename(self, name: str) -> None: ...


class TracingBackend(Protocol):
    def start(self, name: str, attributes: dict[str, Any]) -> AbstractContextManager[Any]: ...

    def trace_ids(self) -> tuple[str | None, str | None]: ...


class _NoopSpan:
    def set(self, **attributes: object) -> None:
        return None

    def rename(self, name: str) -> None:
        return None


NOOP_SPAN = _NoopSpan()
_STATE: dict[str, TracingBackend | None] = {"backend": None}


def install(backend: TracingBackend | None) -> None:
    _STATE["backend"] = backend


def enabled() -> bool:
    return _STATE["backend"] is not None


def span_name(name: str) -> str:
    return name if _NAME.fullmatch(name or "") else "span"


class _Handle:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def set(self, **attributes: object) -> None:
        cleaned = clean(attributes)
        try:
            if cleaned:
                self._raw.set_attributes(cleaned)
        except Exception:  # noqa: BLE001
            logger.debug("span attributes failed", exc_info=True)

    def rename(self, name: str) -> None:
        try:
            self._raw.update_name(span_name(name))
        except Exception:  # noqa: BLE001
            logger.debug("span rename failed", exc_info=True)


@contextmanager
def span(name: str, /, **attributes: object) -> Iterator[SpanHandle]:
    backend = _STATE["backend"]
    if backend is None:
        yield NOOP_SPAN
        return
    stack = ExitStack()
    try:
        raw = stack.enter_context(backend.start(span_name(name), clean(attributes)))
    except Exception:  # noqa: BLE001 - a broken exporter never breaks a request or a job
        logger.debug("span start failed", exc_info=True)
        yield NOOP_SPAN
        return
    with stack:
        yield _Handle(raw)


def trace_ids() -> tuple[str | None, str | None]:
    """(trace_id, span_id) of the current span, for log correlation; (None, None) when off."""

    backend = _STATE["backend"]
    if backend is None:
        return None, None
    try:
        return backend.trace_ids()
    except Exception:  # noqa: BLE001 - correlation is best effort
        return None, None
