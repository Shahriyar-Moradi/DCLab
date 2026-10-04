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
