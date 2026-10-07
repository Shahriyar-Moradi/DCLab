"""Customer CLI against an httpx MockTransport (no server)."""

from __future__ import annotations

import io
import json
import stat
from pathlib import Path

import httpx
import pytest

from dclab_client import cli

PID = "11111111-1111-1111-1111-111111111111"
EID = "22222222-2222-2222-2222-222222222222"
TOKEN = "dclab_st_secret-token-value"


def _project(**over):
    return {"id": PID, "workspace_id": PID, "name": "Churn", "slug": "churn", "description": "",
            "status": "active", "created_by": None, "provenance": "api",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", **over}


def _run(argv, handler, tmp_path, *, env=None, stdin=""):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")
    out, err = io.StringIO(), io.StringIO()
    # The mock host is plain http on a non-loopback name, so tests opt in explicitly.
    environ = {"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "DCLAB_ALLOW_INSECURE_HTTP": "1", **(env or {})}
    code = cli.main(argv, env=environ, stdin=io.StringIO(stdin), stdout=out, stderr=err, http=http)
    return code, out.getvalue(), err.getvalue()


def _env():
    return {"DCLAB_TOKEN": TOKEN, "DCLAB_API_URL": "http://api.test"}


def _error(status, code, message="nope"):
    body = {"error": {"code": code, "message": message, "retryable": status == 429,
                      "request_id": "req-1", "details": {}}}
    return lambda request: httpx.Response(status, json=body)


def test_login_validates_stores_0600_and_never_prints_token(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": PID, "email": "a@b.c", "role": "ml_engineer", "full_name": "A"})

    code, out, err = _run(["login", "--api-url", "http://api.test", "--workspace", PID],
                          handler, tmp_path, stdin=TOKEN + "\n")
    assert code == 0, err
    assert TOKEN not in out + err
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    path = tmp_path / "cfg" / "dclab" / "config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["workspace"] == PID
    # later commands use the stored config without env
    code, out, err = _run(["projects", "list", "--json"], lambda r: httpx.Response(200, json=[_project()]),
                          tmp_path)
    assert code == 0 and json.loads(out)[0]["slug"] == "churn"


def test_failed_login_stores_nothing(tmp_path):
    code, _, err = _run(["login", "--token", "bad", "--workspace", PID],
                        _error(401, "unauthenticated", "bad token"), tmp_path)
    assert code == cli.EXIT_AUTH
    assert not (tmp_path / "cfg" / "dclab" / "config.json").exists()
    assert "unauthenticated" in err and "req-1" in err and "bad" not in err.replace("bad token", "")


def test_env_overrides_config_file(tmp_path):
    cli.save_config({"XDG_CONFIG_HOME": str(tmp_path / "cfg")},
                    {"token": "stored", "api_url": "http://stored", "workspace": PID})
    settings = cli.resolve_settings({"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "DCLAB_TOKEN": "env",
                                     "DCLAB_API_URL": "https://env.example", "DCLAB_WORKSPACE": EID})
    assert settings == {"token": "env", "api_url": "https://env.example", "workspace": EID}


def test_stored_token_is_bound_to_its_stored_url(tmp_path):
    cfg = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    cli.save_config(cfg, {"token": "stored", "api_url": "https://dclab.example", "workspace": PID})
    assert cli.resolve_settings(cfg)["token"] == "stored"
    assert cli.resolve_settings({**cfg, "DCLAB_API_URL": "https://dclab.example/"})["token"] == "stored"
    with pytest.raises(cli.ConfigError, match="differs"):
        cli.resolve_settings({**cfg, "DCLAB_API_URL": "https://evil.example"})
    seen = []
    code, _, err = _run(["projects", "list"], lambda r: seen.append(r) or httpx.Response(200, json=[]), tmp_path,
                        env={"DCLAB_API_URL": "https://evil.example"})
    assert code == cli.EXIT_ERROR and "differs" in err and not seen


@pytest.mark.parametrize("url", ["http://dclab.example", "http://10.0.0.5:8001", "ftp://dclab.example"])
def test_plain_http_to_a_remote_host_is_refused(tmp_path, url):
    with pytest.raises(cli.ConfigError, match="https"):
        cli.resolve_settings({"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "DCLAB_TOKEN": TOKEN, "DCLAB_API_URL": url})
    code, _, err = _run(["login", "--api-url", url], lambda r: httpx.Response(200, json={}), tmp_path,
                        env={"DCLAB_ALLOW_INSECURE_HTTP": ""}, stdin=TOKEN + "\n")
    assert code == cli.EXIT_ERROR and "https" in err
    assert not (tmp_path / "cfg" / "dclab" / "config.json").exists()


@pytest.mark.parametrize("url", ["https://dclab.example", "http://localhost:3000/api/backend", "http://127.0.0.1:8001",
                                 "http://api:8001", "http://studio.localhost"])
def test_https_and_loopback_urls_are_allowed(url):
    assert cli.check_api_url(url) == url


def test_token_flag_warns_about_shell_history(tmp_path):
    ok = {"id": PID, "email": "a@b.c", "role": "ml_engineer", "full_name": "A"}
    code, out, err = _run(["login", "--token", TOKEN, "--api-url", "http://api.test", "--workspace", PID],
                          lambda r: httpx.Response(200, json=ok), tmp_path)
    assert code == 0 and "shell history" in err and TOKEN not in out + err


def test_not_logged_in_is_auth_exit(tmp_path):
    code, _, err = _run(["projects", "list"], lambda r: httpx.Response(200, json=[]), tmp_path)
    assert code == cli.EXIT_AUTH and "not logged in" in err


@pytest.mark.parametrize(
    ("status", "api_code", "exit_code"),
    [(401, "unauthenticated", 3), (403, "forbidden", 3), (404, "not_found", 4), (409, "conflict", 5),
     (412, "precondition_failed", 5), (400, "invalid_cursor", 6), (422, "validation_failed", 6),
     (429, "rate_limited", 7), (503, "unavailable", 7)],
)
def test_error_envelope_maps_to_exit_codes_on_stderr(tmp_path, status, api_code, exit_code):
    code, out, err = _run(["projects", "get", PID], _error(status, api_code, "boom"), tmp_path, env=_env())
    assert code == exit_code and out == ""
    assert f"{api_code}: boom" in err and "request_id=req-1" in err
    code, _, err = _run(["projects", "get", PID, "--json"], _error(status, api_code, "boom"), tmp_path, env=_env())
    assert json.loads(err)["error"]["request_id"] == "req-1"


def test_transport_failure_is_retryable(tmp_path):
    def handler(request):
        raise httpx.ConnectError("down")

    code, _, err = _run(["projects", "list"], handler, tmp_path, env=_env())
    assert code == cli.EXIT_RETRYABLE and "retry" in err


def test_usage_errors_exit_2(tmp_path):
    for argv in (["bogus"], ["projects"], ["experiments", "run"], []):
        code, _, err = _run(argv, lambda r: httpx.Response(200, json=[]), tmp_path, env=_env())
        assert code == cli.EXIT_USAGE and "usage error" in err


def test_human_and_json_output_and_upload_key(tmp_path):
    recorded = []

    def handler(request):
        recorded.append(request)
        return httpx.Response(200, json=[_project()])

    code, out, _ = _run(["projects", "list"], handler, tmp_path, env=_env())
    assert code == 0 and "SLUG" in out and "churn" in out
    csv = tmp_path / "d.csv"
    csv.write_text("a,b\n1,2\n")
    code, _, err = _run(["data", "upload", str(csv), "--project", PID, "--idempotency-key", "k1"],
                        _error(409, "idempotency_key_conflict"), tmp_path, env=_env())
    assert code == cli.EXIT_CONFLICT and "idempotency_key_conflict" in err
    code, _, err = _run(["data", "upload", str(tmp_path / "missing.csv"), "--project", PID],
                        handler, tmp_path, env=_env())
    assert code == cli.EXIT_ERROR


def test_experiment_code_writes_script(tmp_path):
    doc = {"filename": "run.py", "media_type": "text/x-python", "content_digest": "sha256:x", "source": "print(1)\n"}
    payload = {"experiment_id": EID, "workspace_id": PID, "generator_version": "1", "spec_digest": "d",
               "standalone_cv": True, "script": doc, "notebook": {**doc, "filename": "run.ipynb", "source": "{}"}}
    handler = lambda r: httpx.Response(200, json=payload)  # noqa: E731
    code, out, _ = _run(["experiments", "code", EID], handler, tmp_path, env=_env())
    assert code == 0 and out == "print(1)\n"
    target = tmp_path / "repro.py"
    code, out, _ = _run(["experiments", "code", EID, "-o", str(target), "--json"], handler, tmp_path, env=_env())
    assert code == 0 and target.read_text() == "print(1)\n" and json.loads(out)["filename"] == "run.py"


def test_experiment_findings_table_json_and_old_runs(tmp_path):
    check = {"check": "class_imbalance", "status": "warning", "severity": "warning",
             "message": "The smallest class is 5.0% of the training rows.", "evidence": {"class_count": 2},
             "recommendation_kind": "class_weights"}
    payload = {"experiment_id": EID, "investigated": True, "version": "investigate.v1", "checks": [check],
               "summary": {"passed": 4, "warnings": 1, "failures": 0}}
    handler = lambda r: httpx.Response(200, json=payload)  # noqa: E731
    code, out, _ = _run(["experiments", "findings", EID], handler, tmp_path, env=_env())
    assert code == 0 and out.splitlines()[0].split() == ["CHECK", "STATUS", "SEVERITY", "MESSAGE"]
    assert "class_imbalance  warning" in out and "5.0%" in out
    code, out, _ = _run(["experiments", "findings", EID, "--json"], handler, tmp_path, env=_env())
    assert code == 0 and json.loads(out)["summary"]["warnings"] == 1
    old = {"experiment_id": EID, "investigated": False, "checks": []}
    code, out, _ = _run(["experiments", "findings", EID], lambda r: httpx.Response(200, json=old), tmp_path,
                        env=_env())
    assert code == 0 and "no trust checks recorded" in out


def test_branch_requires_one_change_source_and_valid_json(tmp_path):
    handler = lambda r: httpx.Response(200, json={})  # noqa: E731
    code, _, err = _run(["experiments", "branch", EID, "--intent", "x"], handler, tmp_path, env=_env())
    assert code == cli.EXIT_ERROR and "--changes" in err
    code, _, err = _run(["experiments", "branch", EID, "--intent", "x", "--changes", "{oops"],
                        handler, tmp_path, env=_env())
    assert code == cli.EXIT_ERROR and "JSON" in err


def test_package_runs_as_module():
    import subprocess
    import sys

    import os

    root = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-m", "dclab_client", "--version"], capture_output=True,
                            text=True, env={**os.environ, "PYTHONPATH": root})
    assert result.returncode == 0 and "dclab-cli" in result.stdout


def test_compare_table_and_ids_param(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.url.params["ids"])
        item = {"experiment_id": EID, "family": "logreg", "selection_metric": "auc", "selected_score": 0.8}
        return httpx.Response(200, json={"schema_version": 1, "source": "x", "authoritative": False,
                                         "split_plan_id": PID, "experiments": [item, item],
                                         "common": {"cv": [], "holdout": []}})

    code, out, _ = _run(["experiments", "compare", EID, PID], handler, tmp_path, env=_env())
    assert code == 0 and seen == [f"{EID},{PID}"] and "logreg" in out


def _prediction(status="queued", **over):
    return {"id": EID, "workspace_id": PID, "project_id": PID, "model_version_id": PID, "input_dataset_id": PID,
            "execution_request_id": None, "status": status, "output_format": "csv", "rows_in": None,
            "rows_out": None, "decision_threshold": None, "contract_check": None, "output": None,
            "error_code": None, "error_message": None, "created_at": "2026-01-01T00:00:00Z",
            "started_at": None, "completed_at": None, **over}


def test_scoring_upload_sends_purpose(tmp_path):
    seen = []
    upload = {"id": PID, "workspace_id": PID, "project_id": PID, "dataset_asset_id": PID, "name": "s",
              "version": "v1", "source_type": "csv", "content_digest": None, "schema_digest": None,
              "size_bytes": 4, "row_count": 1, "column_count": 1, "purpose": "scoring",
              "created_at": "2026-01-01T00:00:00Z",
              "ingestion": {"id": PID, "status": "completed", "publication_state": "published",
                            "rows_read": 1, "bytes_read": 4, "completed_at": None}}
    csv = tmp_path / "s.csv"
    csv.write_text("a\n1\n")
    code, out, err = _run(["data", "upload", str(csv), "--project", PID, "--purpose", "scoring", "--json"],
                          lambda r: seen.append(r) or httpx.Response(201, json=upload), tmp_path, env=_env())
    assert code == 0, err
    assert json.loads(out)["purpose"] == "scoring" and b'name="purpose"' in seen[0].content
    code, _, err = _run(["data", "upload", str(csv), "--project", PID, "--purpose", "holdout"],
                        lambda r: httpx.Response(500), tmp_path, env=_env())
    assert code == cli.EXIT_USAGE


def test_predict_create_wait_get_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_sleep", lambda s: None)
    states = iter(["running", "completed"])
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(202, json=_prediction())
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=b"row_number,label\n1,1\n")
        return httpx.Response(200, json=_prediction(next(states, "completed")))

    code, out, err = _run(["predict", "create", "--model-version", PID, "--dataset", PID, "--format", "parquet",
                           "--idempotency-key", "k-1", "--wait", "--json"], handler, tmp_path, env=_env())
    assert code == 0, err
    assert json.loads(out)["status"] == "completed"
    post = seen[0]
    assert post.url.path == f"/v1/model-versions/{PID}/predictions" and post.headers["Idempotency-Key"] == "k-1"
    assert json.loads(post.content) == {"dataset_id": PID, "output_format": "parquet"}
    assert [r.url.path for r in seen[1:]] == [f"/v1/predictions/{EID}"] * 2
    code, out, _ = _run(["predict", "get", EID], handler, tmp_path, env=_env())
    assert code == 0 and "completed" in out
    target = tmp_path / "p.csv"
    code, out, _ = _run(["predict", "download", EID, "-o", str(target), "--json"], handler, tmp_path, env=_env())
    assert code == 0 and target.read_bytes() == b"row_number,label\n1,1\n"
    assert json.loads(out) == {"prediction_id": EID, "written_to": str(target), "size_bytes": 21}
    code, _, err = _run(["predict", "download", EID], handler, tmp_path, env=_env())
    assert code == cli.EXIT_USAGE


def test_predict_wait_reports_failure_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_sleep", lambda s: None)
    failed = _prediction("failed", error_code="feature_contract_failed", completed_at="2026-01-01T00:00:01Z",
                         contract_check={"missing": ["age"]})
    code, out, _ = _run(["predict", "create", "--model-version", PID, "--dataset", PID, "--wait", "--json"],
                        lambda r: httpx.Response(202 if r.method == "POST" else 200, json=failed),
                        tmp_path, env=_env())
    assert code == cli.EXIT_ERROR and json.loads(out)["error_code"] == "feature_contract_failed"
    clock = iter([0.0, 1.0, 5.0])
    monkeypatch.setattr(cli, "_monotonic", lambda: next(clock, 99.0))
    code, out, err = _run(["predict", "create", "--model-version", PID, "--dataset", PID, "--wait",
                           "--timeout", "3"], lambda r: httpx.Response(200, json=_prediction("running")),
                          tmp_path, env=_env())
    assert code == cli.EXIT_RETRYABLE and "still running" in err and "running" in out
    code, _, err = _run(["predict", "get", EID], _error(404, "not_found"), tmp_path, env=_env())
    assert code == cli.EXIT_NOT_FOUND


def _card_payload(mv: str) -> dict:
    return {
        "card_version": "model_card.v1", "model_version_id": mv, "version": "v1", "experiment_id": mv,
        "created_at": "2026-10-04T00:00:00Z", "content_digest": "d" * 64, "family": "logistic_regression",
        "target": {"column": "label", "task_type": "binary"}, "objective": {"primary_metric": "roc_auc"},
        "metric_in_words": {"text": "Of every 100 rows the model flags as positive, about 57 really are.",
                            "basis": "cross_validation_out_of_fold_at_locked_threshold", "numbers": {"precision": 0.57}},
        "cv": {"metric": "roc_auc", "mean": 0.81}, "baseline": {"available": True, "text": "beats it"},
        "drivers": {"status": "computed", "text": "Strongest: plan.",
                    "features": [{"rank": 1, "column": "plan", "importance_mean": 0.1}]},
        "risks": {"investigated": True, "items": [], "text": "none"}, "data": {}, "split": {},
        "llm": {"used": False}, "final_evaluation": {"status": "withheld", "note": "Withheld"},
        "markdown": "# Model card: logistic_regression v1\n\n## Top drivers\n\x1b[2Jcleared\n", "untrusted_fields": [],
    }


def test_models_card_prints_markdown_or_json(tmp_path):
    handler = lambda r: httpx.Response(200, json=_card_payload(EID))  # noqa: E731
    code, out, _ = _run(["models", "card", EID], handler, tmp_path, env=_env())
    assert code == 0 and out.startswith("# Model card: logistic_regression v1") and "## Top drivers" in out
    assert "\x1b" not in out and "[2Jcleared" in out
    code, out, _ = _run(["models", "card", EID, "--json"], handler, tmp_path, env=_env())
    assert code == 0 and json.loads(out)["drivers"]["features"][0]["column"] == "plan"
    code, out, _ = _run(["models", "card", EID, "--json", "--markdown"], handler, tmp_path, env=_env())
    assert code == 0 and out.startswith("# Model card")


def test_activity_table_and_json(tmp_path):
    item = {"id": f"run_queued:{EID}", "kind": "run_queued", "occurred_at": "2026-10-07T09:12:00Z",
            "project_id": PID, "actor": {"kind": "person"}, "subject": {"kind": "experiment", "id": EID},
            "summary": "Run #1 queued", "link": {"kind": "experiment", "id": EID}}
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"items": [item], "next_cursor": None, "limit": 5})

    code, out, _ = _run(["activity", "--project", PID, "--limit", "5"], handler, tmp_path, env=_env())
    assert code == 0 and out.splitlines()[0].split() == ["OCCURRED_AT", "KIND", "SUMMARY"]
    assert "Run #1 queued" in out
    assert seen[0].url.path == "/v1/activity" and seen[0].url.params["project_id"] == PID
    code, out, _ = _run(["activity", "--json"], handler, tmp_path, env=_env())
    assert code == 0 and json.loads(out)["items"][0]["kind"] == "run_queued"


def test_inbox_list_table_and_counts(tmp_path):
    item = {"id": f"agent_proposal:{EID}", "kind": "agent_proposal", "tab": "needs_decision",
            "occurred_at": "2026-10-07T09:12:00Z", "project_id": PID, "summary": "Experiment plan waiting for a decision",
            "status": "proposed", "source": {"kind": "agent_proposal", "id": EID},
            "subject": {"kind": "project", "id": PID}, "can_act": False, "actions": [
                {"name": "accept", "operation": "POST /v1/proposals/{proposal_id}/accept",
                 "path_params": {"proposal_id": EID}, "allowed": False}]}
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v1/inbox/counts":
            return httpx.Response(200, json={"needs_decision": 1, "applied_automatically": 0, "done": 4})
        return httpx.Response(200, json={"tab": "needs_decision", "items": [item], "next_cursor": None, "limit": 5,
                                         "viewer": {"is_agent": False, "can_decide": False,
                                                    "can_approve_ai_policy": False}})

    code, out, _ = _run(["inbox", "list", "--project", PID, "--limit", "5"], handler, tmp_path, env=_env())
    assert code == 0 and out.splitlines()[0].split() == ["OCCURRED_AT", "KIND", "SUMMARY", "CAN_ACT"]
    assert "Experiment plan waiting for a decision" in out
    assert seen[0].url.path == "/v1/inbox" and seen[0].url.params["tab"] == "needs_decision"
    code, out, _ = _run(["inbox", "counts", "--json"], handler, tmp_path, env=_env())
    assert code == 0 and json.loads(out) == {"needs_decision": 1, "applied_automatically": 0, "done": 4}
