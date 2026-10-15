"""P8.5-A observability: no-op by default, label allow-list, redaction, correlation, alert runbooks."""

from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml

from app import observability
from app.config import REPO_ROOT
from app.observability import labels, metrics, tracing
from app.observability.logging import (
    ArgsRedactionFilter,
    ContextFilter,
    RedactingTextFormatter,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    job_id_var,
    redact_text,
    request_id_var,
)
from app.observability.setup import setup, shutdown

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DEFAULTS = SimpleNamespace(log_format="text", log_level="INFO", observability_enabled=False,
                           otel_exporter_otlp_endpoint="", otel_service_name="dclab")


class _Span:
    def __init__(self, name, attributes):
        self.name, self.attributes = name, dict(attributes)

    def set_attributes(self, attributes):
        self.attributes.update(attributes)

    def update_name(self, name):
        self.name = name


class Recorder:
    def __init__(self):
        self.spans, self.points, self.request_ids = [], [], []

    @contextmanager
    def start(self, name, attributes):
        span = _Span(name, attributes)
        self.spans.append(span)
        yield span

    def trace_ids(self):
        return "a" * 32, "b" * 16

    def add(self, name, value, labels_):
        self.points.append((name, value, dict(labels_)))
        self.request_ids.append(request_id_var.get())

    def observe(self, name, value, labels_):
        self.points.append((name, value, dict(labels_)))

    def labels(self, name):
        return [point[2] for point in self.points if point[0] == name]


@pytest.fixture()
def recorder():
    rec = Recorder()
    tracing.install(rec)
    metrics.install(rec)
    try:
        yield rec
    finally:
        tracing.install(None)
        metrics.install(None)


# --- no-op by default ----------------------------------------------------------------------------


def test_default_setup_is_a_noop_that_imports_nothing_from_opentelemetry():
    script = (
        "import sys; from types import SimpleNamespace as N\n"
        "from app.observability.setup import setup\n"
        "from app.observability import tracing, metrics, span\n"
        "before = {m for m in sys.modules if m.startswith('opentelemetry')}\n"
        "s = N(log_format='text', log_level='INFO', observability_enabled=False, otel_exporter_otlp_endpoint='',"
        " otel_service_name='dclab')\n"
        "assert setup('api', settings=s) is False and setup('worker', settings=s) is False\n"
        "with span('x', route='/v1/x'): pass\n"
        "metrics.add('dclab_http_requests', route='/v1/x')\n"
        "after = {m for m in sys.modules if m.startswith('opentelemetry')}\n"
        "assert after == before, sorted(after - before)\n"
        "assert 'app.observability.otel' not in sys.modules\n"
        "assert not any(m.startswith(('opentelemetry.sdk', 'opentelemetry.exporter')) for m in sys.modules)\n"
        "assert not tracing.enabled() and not metrics.enabled()\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
                            env={"PYTHONPATH": str(REPO_ROOT / "apps" / "api"), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr[-2000:]


def test_requested_without_the_extra_warns_once_and_stays_noop(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "app.observability.otel", None)  # as if the SDK were missing
    import app.observability.setup as setup_module

    monkeypatch.setitem(setup_module._STATE, "warned", False)
    enabled = SimpleNamespace(**{**vars(DEFAULTS), "observability_enabled": True})
    with caplog.at_level(logging.WARNING, logger="dclab.observability"):
        assert setup("api", settings=enabled) is False
        assert setup("api", settings=enabled) is False
    assert [r.getMessage() for r in caplog.records].count(
        "observability requested but the [observability] extra is not installed; traces and metrics stay off") == 1
    assert not tracing.enabled() and not metrics.enabled()
    assert setup_module.requested(SimpleNamespace(**{**vars(DEFAULTS), "otel_exporter_otlp_endpoint": "http://c:4318"}))


# --- labels and attributes ------------------------------------------------------------------------


def test_label_allow_list_never_admits_ids_names_or_free_text(recorder):
    assert not labels.ALLOWED_KEYS & labels.FORBIDDEN_KEYS
    for name, (_kind, _unit, _description, allowed) in metrics.INSTRUMENTS.items():
        assert allowed <= labels.ALLOWED_KEYS, name
        assert name.startswith("dclab_") and re.fullmatch(r"[a-z_]+", name)
    secret = {"workspace_id": str(uuid4()), "user_id": str(uuid4()), "project_id": "p", "email": "a@b.co",
              "dataset_name": "salaries", "file_name": "x.csv", "error": "boom", "name": "Acme"}
    cleaned = labels.clean({**secret, "route": "/v1/projects/{project_id}", "status": str(uuid4()),
                            "model": "gpt-6.1-sol", "outcome": "rate limited: try later", "provider": "a@b.co",
                            "handler_key": "deadbeefdeadbeefdead", "attempt": 2})
    assert cleaned == {"route": "/v1/projects/{project_id}", "model": "gpt-6.1-sol", "attempt": 2}
    with observability.span("http.request", **secret, method="GET") as current:
        current.set(**secret, status_code=200)
    metrics.add("dclab_http_requests", **secret, route="/v1/x", method="GET", status_class="2xx", job_type="t")
    span = recorder.spans[-1]
    assert span.attributes == {"method": "GET", "status_code": 200}
    assert recorder.labels("dclab_http_requests") == [{"route": "/v1/x", "method": "GET", "status_class": "2xx"}]
    assert tracing.span_name("GET /v1/x?token=abc") == "span"


def test_http_requests_are_labelled_by_route_template(client, recorder):
    project = uuid4()
    assert client.get(f"/v1/projects/{project}", headers={"X-Request-Id": "req-abc"}).status_code == 401
    assert client.get(f"/no/such/{project}?token=abc").status_code == 404
    routes = [point["route"] for point in recorder.labels("dclab_http_requests")]
    assert recorder.request_ids[0] == "req-abc" and recorder.request_ids[-1]  # log correlation id bound
    assert routes == ["/v1/projects/{project_id}", "<unmatched>"]
    assert recorder.labels("dclab_http_requests")[0] == {
        "route": "/v1/projects/{project_id}", "method": "GET", "status_class": "4xx"}
    assert [span.name for span in recorder.spans] == ["GET /v1/projects/{project_id}", "GET <unmatched>"]
    dump = json.dumps([span.attributes for span in recorder.spans] + recorder.points, default=str)
    assert str(project) not in dump and "token" not in dump and not UUID_RE.search(dump)


def test_worker_jobs_record_type_handler_and_status_only(db_session, recorder):
    from app.db.models import DEFAULT_WORKSPACE_ID
    from app.services.ml_job_service import create_ml_job, process_next_job

    seen = {}

    def ok(_db, _target):
        seen["job_id"] = job_id_var.get()

    def boom(_db, _target):
        raise ValueError("customer jane@acme.test has salary 91000")

    for runner in (ok, boom):
        job = create_ml_job(db_session, workspace_id=DEFAULT_WORKSPACE_ID, job_type="auth_cleanup",
                            handler_key="auth.session_cleanup", target_id=uuid4(), max_attempts=1)
        db_session.commit()
        process_next_job(db_session, job_id=job.id, runner=runner)
        if runner is ok:
            assert seen["job_id"] == str(job.id)  # logs inside the job carry its id
    assert job_id_var.get() is None
    assert [point for point in recorder.labels("dclab_ml_jobs")] == [
        {"job_type": "auth_cleanup", "handler_key": "auth.session_cleanup", "status": "completed"},
        {"job_type": "auth_cleanup", "handler_key": "auth.session_cleanup", "status": "failed"},
    ]
    dump = json.dumps([span.attributes for span in recorder.spans] + recorder.points, default=str)
    assert "acme" not in dump and "91000" not in dump and not UUID_RE.search(dump)


def test_llm_agent_and_tick_helpers_record_codes(recorder):
    with observability.llm_call("completion") as call:
        call.status, call.labels = "budget_exhausted", {"provider": "openai", "model": "gpt-6-luna"}
    with observability.agent_run("specialist") as run:
        run.status = "completed"
    with pytest.raises(RuntimeError):
        with observability.scheduled_tick():
            raise RuntimeError("tick")
    with observability.scheduled_tick() as tick:
        tick.labels = {"runs_queued": 2, "runs_skipped": 1}
    assert recorder.labels("dclab_llm_calls") == [
        {"llm_kind": "completion", "outcome": "budget_exhausted", "provider": "openai", "model": "gpt-6-luna"}]
    assert recorder.labels("dclab_agent_runs") == [{"agent_kind": "specialist", "status": "completed"}]
    assert recorder.labels("dclab_scheduled_ticks") == [{"outcome": "error"}, {"outcome": "ok"}]
    runs = [(p[1], p[2]) for p in recorder.points if p[0] == "dclab_scheduled_runs"]
    assert runs == [(2, {"outcome": "queued"}), (1, {"outcome": "skipped"})]


# --- logs -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kept", "gone"),
    [
        ("Authorization: Bearer eyJhbGciOi.eyJzdWIi.sig", "Authorization:", "eyJ"),
        ("cookie: dclab_session=abc123; dclab_csrf=zz", "cookie:", "abc123"),
        ("Set-Cookie: dclab_session=s3cr3t; Path=/", "Set-Cookie:", "s3cr3t"),
        ("Idempotency-Key: run-42-retry", "Idempotency-Key:", "run-42"),
        ('{"email": "jane@acme.test", "password": "hunter2", "n": 3}', '"n": 3', "hunter2"),
        ("GET /download?token=tok123&format=csv HTTP/1.1", "format=csv", "tok123"),
        ("GET /x?access_token=t0k&api_key=k3y", "/x?", "k3y"),
        ("service token dclab_st_0123_abcdef used", "service token", "dclab_st_"),
        ("provider key sk-live1234567890 rejected", "provider key", "sk-live"),
        ("invited jane.doe+x@acme.test to the workspace", "invited", "jane.doe"),
        # Python dict reprs (single quotes around keys and values), a logged dict argument
        (str({"password": "hunter2", "token": "abc123", "Idempotency-Key": "idem-1"}), "'password': ", "hunter2"),
        (str({"password": "hunter2", "token": "abc123", "Idempotency-Key": "idem-1"}), "'token': ", "abc123"),
        (str({"password": "hunter2", "token": "abc123", "Idempotency-Key": "idem-1"}), "'Idempotency-Key': ", "idem-1"),
        ("msg {'token': 'T1'}", "msg {'token': ", "T1"),
        (str({"authorization": "Token s3cr3t", "cookie": "dclab_session=SESSVAL"}), "'authorization'", "s3cr3t"),
        (str({"authorization": "Token s3cr3t", "cookie": "dclab_session=SESSVAL"}), "'authorization'", "SESSVAL"),
        # authorization / cookie headers: redacted to the end of the line, whatever the scheme
        ("Authorization: Token s3cr3tvalue", "Authorization:", "s3cr3tvalue"),
        ("Authorization: DCLab abc.def\nnext line", "next line", "abc.def"),
        ("Cookie: dclab_session=SESSVAL; other=1", "Cookie:", "other=1"),
        ("X-DCLab-Session: SESSVAL", "X-DCLab-Session:", "SESSVAL"),
        # URL credentials and presigned URLs
        ("postgresql+psycopg://dclab:PGPASS@db:5432/decisionai", "dclab:[redacted]@db:5432", "PGPASS"),
        ("GET /o?X-Amz-Signature=AMZSIG&X-Amz-Credential=AKIAXYZ&X-Amz-Security-Token=STS1&sig=S1&signature=S2",
         "/o?", "AMZSIG"),
        ("GET /o?X-Amz-Credential=AKIAXYZ&sig=S1", "/o?", "AKIAXYZ"),
        ("GET /o?X-Amz-Security-Token=STS1&signature=S2", "/o?", "STS1"),
        ("GET /o?sig=SIGVAL&design=ok", "design=ok", "SIGVAL"),
        # key suffixes / substrings
        ("aws_secret_access_key=wJalrXUtnFEMI", "aws_secret_access_key=", "wJalr"),
        ("stripe_key=rk_live_1 db_password=pw1", "stripe_key=", "rk_live"),
        ("stripe_key=rk_live_1 db_password=pw1", "db_password=", "pw1"),
        ("ValueError: invalid key api_key=sk-proj-ABCDEFGHIJ1234", "invalid key", "ABCDEFGHIJ"),
        ("service_token_id=7c9e6679 request_id=r1", "service_token_id=7c9e6679", "[redacted]"),  # ids stay
        # partial leaks: escaped quotes, quoted values with spaces, unquoted values with spaces
        ('{"password": "ab\\"cdSECRET"}', '{"password": ', "SECRET"),
        ("password='quoted value'", "password=", "value"),
        ('{ "secret" :   "with spaces inside" }', '"secret"', "spaces"),
        ("client_secret: abc def", "client_secret:", "def"),
    ],
)
def test_redaction_cases(raw, kept, gone):
    out = redact_text(raw)
    assert kept in out and gone not in out, out


def _record(msg, *args, level=logging.INFO, exc_info=None, **extra):
    record = logging.LogRecord("dclab.test", level, __file__, 1, msg, args, exc_info)
    record.__dict__.update(extra)
    return record


def test_redaction_filter_rewrites_messages_extras_and_tracebacks():
    try:
        raise ValueError("row for jane@acme.test")
    except ValueError:
        info = sys.exc_info()
    record = _record("login %s with %s", "jane@acme.test", "password=hunter2", exc_info=info,
                     headers={"Authorization": "Bearer abc", "Accept": "json"}, file_contents=b"a,b\n1,2")
    assert RedactionFilter().filter(record)
    assert record.getMessage() == "login [email] with password=[redacted] [ValueError]"
    assert record.headers == {"Authorization": "[redacted]", "Accept": "json"}
    assert record.file_contents == "[redacted]" and record.exc_info is None  # INFO: no traceback
    error = _record("failed", level=logging.ERROR, exc_info=info)
    RedactionFilter().filter(error)
    formatted = json.loads(JsonFormatter().format(error))
    assert formatted["exc_type"] == "ValueError" and "jane@acme.test" not in formatted["exc"]


def test_json_lines_carry_request_job_and_trace_ids(recorder):
    request_token, job_token = request_id_var.set("req-1"), job_id_var.set("job-1")
    try:
        record = _record("hello %s", "world", route="/v1/x")
        ContextFilter().filter(record)
        RedactionFilter().filter(record)
        body = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(request_token)
        job_id_var.reset(job_token)
    assert body["message"] == "hello world" and body["route"] == "/v1/x"
    assert (body["request_id"], body["job_id"], body["trace_id"], body["span_id"]) == (
        "req-1", "job-1", "a" * 32, "b" * 16)


def test_configure_logging_owns_only_a_bare_root_and_redacts_access_lines():
    root, access = logging.getLogger(), logging.getLogger("uvicorn.access")
    uv, uv_error = logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")
    saved = (root.handlers[:], root.level, access.handlers[:], access.propagate, access.disabled, access.filters[:],
             uv.handlers[:], uv.propagate, uv_error.handlers[:], uv_error.propagate)
    try:
        assert configure_logging(log_format="json") is False  # pytest owns the root: untouched
        assert any(isinstance(item, ArgsRedactionFilter) for item in access.filters)
        root.handlers = []
        assert configure_logging(log_format="json", log_level="warning") is True
        assert configure_logging(log_format="json") is False  # idempotent
        handler = root.handlers[0]
        handler.stream = io.StringIO()
        assert root.level == logging.WARNING and access.disabled
        logging.getLogger("dclab.test").warning("user %s", "jane@acme.test")
        line = json.loads(handler.stream.getvalue().strip())
        assert line["message"] == "user [email]" and line["level"] == "warning"
    finally:
        (root.handlers, root.level, access.handlers, access.propagate, access.disabled, access.filters,
         uv.handlers, uv.propagate, uv_error.handlers, uv_error.propagate) = saved
        root.setLevel(saved[1])


@contextmanager
def _uvicorn_logging():
    """uvicorn's own logging config (as ``uvicorn app.main:app`` installs it), restored after."""

    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).filters[:],
                    logging.getLogger(name).propagate, logging.getLogger(name).level,
                    logging.getLogger(name).disabled) for name in names}
    try:
        logging.config.dictConfig(LOGGING_CONFIG)
        streams = {}
        for name in ("uvicorn", "uvicorn.access"):
            for handler in logging.getLogger(name).handlers:
                handler.stream = streams[name] = io.StringIO()
        yield streams
    finally:
        for name, (handlers, filters, propagate, level, disabled) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers, logger.filters, logger.propagate, logger.disabled = handlers, filters, propagate, disabled
            logger.setLevel(level)


def test_uvicorn_access_lines_keep_their_args_and_are_redacted(capsys):
    with _uvicorn_logging() as streams:
        configure_logging(log_format="text")
        configure_logging(log_format="text")  # idempotent: one filter per target
        access = logging.getLogger("uvicorn.access")
        assert sum(isinstance(item, ArgsRedactionFilter) for item in access.filters) == 1
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/v1/x?access_token=SECRET&n=1", "1.1", 200)
        line = streams["uvicorn.access"].getvalue()
    assert "Logging error" not in capsys.readouterr().err
    assert '"GET /v1/x?access_token=[redacted]&n=1 HTTP/1.1" 200' in line and "SECRET" not in line


def test_uvicorn_error_lines_are_redacted_in_text_mode(capsys):
    with _uvicorn_logging() as streams:
        configure_logging(log_format="text")
        try:
            raise RuntimeError("upstream said Authorization: Bearer tok-SECRET-1 for jane@acme.test")
        except RuntimeError:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application %s", "token=ARGSECRET")
        output = streams["uvicorn"].getvalue()
    assert "Exception in ASGI application" in output and "RuntimeError" in output
    for leaked in ("tok-SECRET-1", "jane@acme.test", "ARGSECRET"):
        assert leaked not in output, leaked
    assert "Logging error" not in capsys.readouterr().err


def test_text_lines_cannot_be_forged_with_newlines():
    record = _record("user said %s", "ok\n2026-10-16 INFO dclab.auth [req=x] admin logged in\r\x1b[31m")
    ContextFilter().filter(record)
    line = RedactingTextFormatter("%(levelname)s %(message)s").format(record)
    assert "\n" not in line and "\r" not in line and "\x1b" not in line
    assert line.endswith("ok\\n2026-10-16 INFO dclab.auth [req=x] admin logged in\\r\\x1b[31m")


def test_a_broken_tracing_backend_never_breaks_the_caller():
    class Broken:
        def start(self, name, attributes):
            raise RuntimeError("exporter down")

        def trace_ids(self):
            raise RuntimeError("exporter down")

    tracing.install(Broken())
    try:
        with observability.span("x", route="/v1/x") as current:
            current.set(status="ok")
            result = 42
        assert result == 42 and tracing.trace_ids() == (None, None)
        with pytest.raises(ValueError):  # the caller's own exceptions still propagate
            with observability.span("x"):
                raise ValueError("caller")
    finally:
        tracing.install(None)


# --- alert rules and runbooks ---------------------------------------------------------------------------

RULES = REPO_ROOT / "ops" / "alerts" / "dclab.rules.yaml"
RUNBOOKS = REPO_ROOT / "docs" / "runbooks"


def test_every_alert_rule_has_a_runbook_and_every_runbook_link_resolves():
    rules = [rule for group in yaml.safe_load(RULES.read_text())["groups"] for rule in group["rules"]]
    assert {rule["alert"] for rule in rules} == {
        "DclabJobFailureRateHigh", "DclabQueueAgeHigh", "DclabWorkerHeartbeatStale", "DclabHttp5xxRateHigh",
        "DclabReadinessFailing", "DclabScheduledRunsMissed"}
    linked = set()
    for rule in rules:
        annotations = rule["annotations"]
        assert annotations["summary"] and annotations["threshold"], rule["alert"]
        target = REPO_ROOT / annotations["runbook_url"]
        assert target.is_file() and target.parent == RUNBOOKS, rule["alert"]
        linked.add(target.name)
        for metric in re.findall(r"\b(dclab_[a-z_]+)", rule["expr"]):
            base = re.sub(r"_(total|bucket|sum|count)$", "", metric)
            assert base in metrics.INSTRUMENTS or metric in metrics.INSTRUMENTS, (rule["alert"], metric)
    index = (RUNBOOKS / "README.md").read_text()
    assert len(linked) == len(rules)  # one page per alert
    for page in linked:
        text = (RUNBOOKS / page).read_text()
        assert len(text.splitlines()) <= 40, page
        for section in ("**Symptom.**", "Checks", "**Safe actions.**", "**Escalation.**"):
            assert section in text, (page, section)
    for page in {path.name for path in RUNBOOKS.glob("*.md")} - {"README.md"}:
        assert f"({page})" in index, page  # the index lists every runbook, older ones too
    for path in [RUNBOOKS / "README.md", *(RUNBOOKS / page for page in linked)]:
        for link in re.findall(r"\]\(([^)#]+)\)", path.read_text()):
            if not link.startswith("http"):
                assert (path.parent / link).resolve().exists(), (path.name, link)


# --- with the [observability] extra installed ---------------------------------------------------------------


def test_otel_export_to_memory_when_the_extra_is_installed():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    settings = SimpleNamespace(**{**vars(DEFAULTS), "observability_enabled": True})
    shutdown()
    try:
        assert setup("worker", settings=settings, span_exporter=spans, metric_reader=reader) is True
        with observability.job(job_id=uuid4(), job_type="auth_cleanup", handler_key="auth.session_cleanup",
                               attempt=1) as outcome:
            outcome.status = "completed"
        finished = spans.get_finished_spans()
        assert [span.name for span in finished] == ["ml_job.execute"]
        assert dict(finished[0].attributes) == {"job_type": "auth_cleanup", "handler_key": "auth.session_cleanup",
                                                "attempt": 1, "status": "completed"}
        data = reader.get_metrics_data()
        points = {metric.name: [dict(point.attributes) for point in metric.data.data_points]
                  for resource in data.resource_metrics for scope in resource.scope_metrics
                  for metric in scope.metrics}
        assert points["dclab_ml_jobs"] == [
            {"job_type": "auth_cleanup", "handler_key": "auth.session_cleanup", "status": "completed"}]
        assert "dclab_ml_job_duration_seconds" in points
    finally:
        shutdown()
