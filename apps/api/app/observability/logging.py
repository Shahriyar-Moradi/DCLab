"""Structured logs (P8.5-A): request / job / trace correlation, redaction, optional JSON.

``configure_logging`` installs one stderr handler on the root logger only when nobody else
configured logging (uvicorn and the worker leave the root bare; pytest does not). Every record
through that handler passes ``RedactionFilter``, and uvicorn's access / error lines pass
``ArgsRedactionFilter`` (args kept for uvicorn's formatters): credentials, cookies, tokens,
passwords, URL credentials, presigned-URL signatures, emails, ``Idempotency-Key`` and file contents
are replaced. Below WARNING a DCLab record never carries a stack trace (only the exception type).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.observability import tracing

request_id_var: ContextVar[str | None] = ContextVar("dclab_request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("dclab_job_id", default=None)

REDACTED = "[redacted]"
EMAIL_MASK = "[email]"
# A key is sensitive when it contains one of these (``aws_secret_access_key``, ``X-Amz-Signature``,
# ``csrf_token``, ``new_password``) or ends in ``_key`` / is ``sig``; ``*_id`` keys stay (ids).
_KEY_PARTS = (
    "password", "passwd", "secret", "token", "api_key", "api-key", "apikey", "access_key", "private_key",
    "authorization", "cookie", "session", "idempotency", "csrf", "credential", "signature",
    "file_content", "content_base64", "raw_bytes",
)
_KEYS = r"(?:[\w.-]*(?:" + "|".join(re.escape(part) for part in _KEY_PARTS) + r")[\w.-]*|[\w-]*_key|sig)"
_SENSITIVE_KEY = re.compile(rf"(?i)^{_KEYS}$")
# Header-style lines whose whole value is a credential: redacted to the end of the line
# (``Authorization: Token s3cr3t``, ``Cookie: a=1; b=2``).
_HEADER_LINE = re.compile(
    r"""(?i)(["']?\b(?:proxy-authorization|authorization|set-cookie|cookie|x-dclab-session)["']?\s*:\s*)[^\r\n]*"""
)
_QUOTED = r""""(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*'"""
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host (database and broker URLs)
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^/\s@]+@"), rf"\1:{REDACTED}@"),
    (_HEADER_LINE, rf"\1{REDACTED}"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), rf"\1 {REDACTED}"),
    # key = value (query strings, k=v pairs): a quoted value, else up to whitespace or a separator
    (re.compile(rf"""(?i)(?<![\w.-])(["']?)({_KEYS})(?<!_id)\1(\s*=\s*)(?!\s*\[redacted\])(?:{_QUOTED}|[^\s,;&}}\]'"]+)"""),
     rf"\1\2\1\3{REDACTED}"),
    # key: value (JSON, dict reprs, YAML-ish text): a quoted value, else up to a separator / end of line
    (re.compile(rf"""(?i)(?<![\w.-])(["']?)({_KEYS})(?<!_id)\1(\s*:\s*)(?!\s*\[redacted\])(?:{_QUOTED}|[^,;&}}\]\r\n]+)"""),
     rf"\1\2\1\3{REDACTED}"),
    (re.compile(r"dclab_st_[A-Za-z0-9_-]+"), REDACTED),
    (re.compile(r"dclab_(?:whsec|unsub)_[A-Za-z0-9_-]+"), REDACTED),  # P8.8-A webhook secrets, unsubscribe links
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), REDACTED),  # JWTs
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), EMAIL_MASK),
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\r\n]")
_MAX_TEXT = 4000
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "request_id", "job_id", "trace_id", "span_id", "color_message"}
_HANDLER_MARK = "_dclab_observability"


def is_sensitive_key(key: object) -> bool:
    text = str(key)
    return bool(_SENSITIVE_KEY.match(text)) and not text.lower().endswith("_id")


def redact_text(text: str) -> str:
    out = text[:_MAX_TEXT]
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {
            str(key)[:64]: REDACTED if is_sensitive_key(key) else redact_value(item, depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(item, depth + 1) for item in list(value)[:50]]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


def _redacted_exc_text(record: logging.LogRecord) -> None:
    """Pre-format the traceback redacted: every formatter then prints ``exc_text`` as is."""

    if record.exc_info and not record.exc_text:
        record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))


class RedactionFilter(logging.Filter):
    """For DCLab's own handler: message formatted then redacted, extras redacted, and no
    traceback below WARNING (only the exception type)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not drop the line
            message = str(record.msg)
        record.msg, record.args = redact_text(message), None
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            setattr(record, key, REDACTED if is_sensitive_key(key) else redact_value(value))
        if record.exc_info and record.levelno < logging.WARNING:
            exc_type = record.exc_info[0]
            record.exc_info, record.exc_text = None, None
            record.msg = f"{record.msg} [{getattr(exc_type, '__name__', 'error')}]"
        _redacted_exc_text(record)
        return True


class ArgsRedactionFilter(logging.Filter):
    """For handlers DCLab does not own (uvicorn's): their formatters unpack ``record.args``
    (uvicorn's access formatter needs all five), so the args tuple is kept and only string
    arguments, the message template and the traceback are redacted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_text(arg) if isinstance(arg, str) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = redact_value(record.args)
        _redacted_exc_text(record)
        return True


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.job_id = job_id_var.get() or "-"
        trace_id, span_id = tracing.trace_ids()
        record.trace_id, record.span_id = trace_id or "-", span_id or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "job_id", "trace_id", "span_id"):
            value = getattr(record, key, "-")
            if value and value != "-":
                body[key] = value
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_") and key not in body:
                body[key] = value if isinstance(value, str | int | float | bool | type(None)) else str(value)[:500]
        if record.exc_info:
            body["exc_type"] = getattr(record.exc_info[0], "__name__", "error")
            body["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(body, default=str)


class RedactingTextFormatter(logging.Formatter):
    """Text lines: the message is one line (CR, LF and control characters escaped, so a value
    cannot forge a log line); the traceback is redacted."""

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802 - logging API
        record.message = _CONTROL.sub(lambda m: repr(m.group())[1:-1], record.message)
        return super().formatMessage(record)

    def formatException(self, ei: Any) -> str:  # noqa: N802 - logging API
        return redact_text(super().formatException(ei))


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [req=%(request_id)s job=%(job_id)s] %(message)s"


def _level(name: str) -> int:
    level = logging.getLevelName((name or "INFO").strip().upper())
    return level if isinstance(level, int) else logging.INFO


def configure_logging(*, log_format: str = "text", log_level: str = "INFO") -> bool:
    """Idempotent. Returns True when this call installed the DCLab handler."""

    json_mode = (log_format or "").strip().lower() == "json"
    access = logging.getLogger("uvicorn.access")
    # uvicorn's lines (access: raw paths and query strings; error: exception messages) in both
    # modes: on the loggers that emit them and on their handlers (records of child loggers).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for target in (logger, *logger.handlers):
            if not any(isinstance(item, ArgsRedactionFilter) for item in target.filters):
                target.addFilter(ArgsRedactionFilter())
    root = logging.getLogger()
    if any(getattr(handler, _HANDLER_MARK, False) for handler in root.handlers):
        return False
    if root.handlers:  # someone else (pytest, an embedding app) owns logging
        return False
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter() if json_mode else RedactingTextFormatter(TEXT_FORMAT))
    root.addHandler(handler)
    root.setLevel(_level(log_level))
    if json_mode:
        # One JSON stream: uvicorn's own lines go through the same handler; its access lines are
        # replaced by ``dclab.access`` (route templates, no raw paths or query strings).
        for name in ("uvicorn", "uvicorn.error"):
            logger = logging.getLogger(name)
            logger.handlers, logger.propagate = [], True
        access.handlers, access.propagate, access.disabled = [], False, True
    return True


def json_access_logs() -> bool:
    return logging.getLogger("uvicorn.access").disabled
