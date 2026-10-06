"""Customer CLI over the /v1 SDK (stdlib only). See the package README for exit codes.

Entry points: ``dclab-cli`` (console script) and ``python -m dclab_client``. The
internal ``dclab`` script of the server repo (worker, user seed) is unrelated.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

import httpx

from dclab_client._version import __version__
from dclab_client.client import DCLabClient
from dclab_client.errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DCLabAPIError,
    DCLabClientError,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    PreconditionRequiredError,
    RateLimitedError,
    ServerError,
    UnprocessableEntityError,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5
EXIT_VALIDATION = 6
EXIT_RETRYABLE = 7

ENV_TOKEN = "DCLAB_TOKEN"
ENV_API_URL = "DCLAB_API_URL"
ENV_WORKSPACE = "DCLAB_WORKSPACE"
ENV_ALLOW_INSECURE_HTTP = "DCLAB_ALLOW_INSECURE_HTTP"
DEFAULT_API_URL = "http://localhost:3000/api/backend"
HTTP_HOST_ALLOWLIST = frozenset({"api"})  # the compose API host
WAIT_POLL_SECONDS = 2.0
_sleep, _monotonic = time.sleep, time.monotonic  # patched in tests


class ConfigError(DCLabClientError):
    """Unsafe or inconsistent client configuration; nothing was sent."""


def flag(raw: str | None, default: bool = False) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def check_api_url(url: str, *, allow_insecure_http: bool = False, opt_in_env: str = ENV_ALLOW_INSECURE_HTTP) -> str:
    """https anywhere; plain http only to loopback, ``localhost`` / ``*.localhost`` or the
    compose API host (``api``) unless explicitly opted in, so a token never crosses a
    network in clear text by accident. Shared by the CLI and the MCP server."""

    parsed = httpx.URL(url)
    host = (parsed.host or "").lower()
    if parsed.scheme == "https" and host:
        return url
    if parsed.scheme == "http" and host:
        if allow_insecure_http or host == "localhost" or host.endswith(".localhost") or host in HTTP_HOST_ALLOWLIST:
            return url
        try:
            if ipaddress.ip_address(host).is_loopback:
                return url
        except ValueError:
            pass
    raise ConfigError(f"{ENV_API_URL} must be https (plain http only for localhost or the compose host "
                      f"'api'; set {opt_in_env}=1 to opt in)")


def bind_token_to_url(env: Mapping[str, str], stored: Mapping[str, str], default_url: str) -> tuple[str | None, str]:
    """``(token, api_url)`` with the stored token bound to the URL it was issued for: an
    environment URL that differs from the stored one is refused unless the environment
    also supplies the token."""

    env_url = (env.get(ENV_API_URL) or "").strip() or None
    if env.get(ENV_TOKEN):
        return env[ENV_TOKEN], env_url or stored.get("api_url") or default_url
    token, api_url = stored.get("token"), stored.get("api_url") or env_url or default_url
    if token and not stored.get("api_url"):
        raise ConfigError("the stored token has no stored api_url; log in again with --api-url or set "
                          f"{ENV_TOKEN} and {ENV_API_URL}")
    if token and env_url and env_url.rstrip("/") != api_url.rstrip("/"):
        raise ConfigError(f"{ENV_API_URL} differs from the URL the stored token was issued for; "
                          f"set {ENV_TOKEN} too, or log in again")
    return token, api_url


def exit_code_for(exc: BaseException) -> int:
    """Map an SDK / transport failure to the documented process exit code."""

    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return EXIT_AUTH
    if isinstance(exc, NotFoundError):
        return EXIT_NOT_FOUND
    if isinstance(exc, (ConflictError, PreconditionFailedError, PreconditionRequiredError)):
        return EXIT_CONFLICT
    if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        return EXIT_VALIDATION
    if isinstance(exc, (RateLimitedError, ServerError, httpx.TransportError)):
        return EXIT_RETRYABLE
    if isinstance(exc, DCLabAPIError):
        return EXIT_RETRYABLE if exc.retryable else EXIT_ERROR
    return EXIT_ERROR


# --- config ---------------------------------------------------------------


def config_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "dclab" / "config.json"


def load_config(env: Mapping[str, str]) -> dict[str, str]:
    try:
        data = json.loads(config_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def save_config(env: Mapping[str, str], values: Mapping[str, str]) -> Path:
    path = config_path(env)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(dict(values), handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def resolve_settings(env: Mapping[str, str]) -> dict[str, str | None]:
    """Environment overrides the config file, except that a stored token is only sent to
    its stored URL, and only over https (or loopback http); the token is never logged."""

    stored = load_config(env)
    token, api_url = bind_token_to_url(env, stored, DEFAULT_API_URL)
    check_api_url(api_url, allow_insecure_http=flag(env.get(ENV_ALLOW_INSECURE_HTTP)))
    return {"token": token, "api_url": api_url, "workspace": env.get(ENV_WORKSPACE) or stored.get("workspace")}


# --- output ---------------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return _CONTROL.sub(" ", str(value))  # agent / dataset text never reaches a terminal as escapes


class _Out:
    def __init__(self, stdout: TextIO, as_json: bool, stderr: TextIO | None = None) -> None:
        self.stdout = stdout
        self.as_json = as_json
        self.stderr = stderr or sys.stderr

    def emit(self, value: Any, *, table: tuple[str, ...] | None = None, rows: Any = None) -> None:
        data = _plain(value)
        if self.as_json:
            print(json.dumps(data, indent=2, sort_keys=True, default=str), file=self.stdout)
        elif table is not None:
            self._table(table, [_plain(r) for r in rows], data)
        else:
            self._kv(data)

    def text(self, text: str) -> None:
        print(text, file=self.stdout, end="" if text.endswith("\n") else "\n")

    def _kv(self, data: Mapping[str, Any]) -> None:
        width = max((len(k) for k in data), default=0)
        for key, value in data.items():
            print(f"{key.ljust(width)}  {_cell(value)}", file=self.stdout)

    def _table(self, columns: tuple[str, ...], rows: list[Mapping[str, Any]], page: Any) -> None:
        cells = [[_cell(r.get(c)) for c in columns] for r in rows]
        widths = [max([len(c)] + [len(row[i]) for row in cells]) for i, c in enumerate(columns)]
        print("  ".join(c.upper().ljust(w) for c, w in zip(columns, widths)).rstrip(), file=self.stdout)
        for row in cells:
            print("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip(), file=self.stdout)
        if isinstance(page, Mapping) and page.get("next_cursor"):
            print(f"next cursor: {page['next_cursor']}", file=self.stdout)


# --- commands -------------------------------------------------------------

Ctx = tuple[DCLabClient, _Out]


def _json_arg(raw: str, what: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise DCLabClientError(f"{what} is not valid JSON: {exc.msg}") from None


def _projects_list(c: Ctx, a: argparse.Namespace) -> None:
    items = c[0].projects.list()
    c[1].emit(items, table=("id", "slug", "name", "status"), rows=items)


def _projects_create(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].projects.create(name=a.name, slug=a.slug, description=a.description,
                                   idempotency_key=a.idempotency_key))


def _projects_get(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].projects.get(a.project_id))


def _data_upload(c: Ctx, a: argparse.Namespace) -> None:
    path = Path(a.file)
    if not path.is_file():
        raise DCLabClientError(f"not a file: {a.file}")
    c[1].emit(c[0].datasets.upload(a.project, path, content_type=a.content_type, purpose=a.purpose,
                                   idempotency_key=a.idempotency_key))


def _predict_create(c: Ctx, a: argparse.Namespace) -> int:
    row = c[0].predictions.create(model_version_id=a.model_version, dataset_id=a.dataset,
                                  output_format=a.format, idempotency_key=a.idempotency_key)
    if a.wait:
        deadline = _monotonic() + a.timeout
        while not row.is_terminal and _monotonic() < deadline:
            _sleep(WAIT_POLL_SECONDS)
            row = c[0].predictions.get(row.id)
    c[1].emit(row)
    if a.wait and not row.is_terminal:
        print(f"error: prediction {row.id} still {row.status} after {a.timeout:g}s; "
              "poll with `dclab-cli predict get`", file=c[1].stderr)
        return EXIT_RETRYABLE
    return EXIT_ERROR if row.status == "failed" else EXIT_OK


def _predict_get(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].predictions.get(a.prediction_id))


def _predict_download(c: Ctx, a: argparse.Namespace) -> None:
    content = c[0].predictions.download(a.prediction_id, to=a.output)
    c[1].emit({"prediction_id": a.prediction_id, "written_to": a.output, "size_bytes": len(content)})


def _exp_list(c: Ctx, a: argparse.Namespace) -> None:
    page = c[0].experiments.list(project_id=a.project, status=a.status, parent_id=a.parent,
                                 cursor=a.cursor, limit=a.limit)
    c[1].emit(page, table=("id", "status", "created_at", "parent_experiment_id", "intent"), rows=page.items)


def _exp_get(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].experiments.get(a.experiment_id))


def _exp_run(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].experiments.create(project_id=a.project, dataset_id=a.dataset,
                                      problem_spec_id=a.problem_spec, target_column=a.target,
                                      intent=a.intent, idempotency_key=a.idempotency_key))


def _exp_branch(c: Ctx, a: argparse.Namespace) -> None:
    if bool(a.changes) == bool(a.changes_file):
        raise DCLabClientError("give exactly one of --changes or --changes-file")
    raw = a.changes if a.changes else Path(a.changes_file).read_text(encoding="utf-8")
    changes = _json_arg(raw, "changes")
    if not isinstance(changes, list):
        raise DCLabClientError("changes must be a JSON list of change objects")
    c[1].emit(c[0].experiments.branch(a.experiment_id, changes=changes, intent=a.intent,
                                      idempotency_key=a.idempotency_key))


def _exp_compare(c: Ctx, a: argparse.Namespace) -> None:
    result = c[0].experiments.compare(list(a.experiment_ids))
    c[1].emit(result, table=("experiment_id", "family", "selection_metric", "selected_score",
                             "decision_threshold", "constraint_status"), rows=result.experiments)


def _exp_code(c: Ctx, a: argparse.Namespace) -> None:
    code = c[0].experiments.code(a.experiment_id)
    doc = code.notebook if a.notebook else code.script
    if a.output:
        Path(a.output).write_text(doc.source, encoding="utf-8")
        summary = {"experiment_id": code.experiment_id, "filename": doc.filename,
                   "written_to": a.output, "content_digest": doc.content_digest}
        c[1].emit(summary)
    elif c[1].as_json:
        c[1].emit(code)
    else:
        c[1].text(doc.source)


def _exp_findings(c: Ctx, a: argparse.Namespace) -> None:
    result = c[0].experiments.findings(a.experiment_id)
    if c[1].as_json:
        c[1].emit(result)
    elif not result.investigated:
        c[1].text(f"experiment {result.experiment_id}: no trust checks recorded (run predates them)")
    else:
        c[1].emit(result, table=("check", "status", "severity", "message"), rows=result.checks)


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _models_card(c: Ctx, a: argparse.Namespace) -> None:
    card = c[0].model_versions.card(a.model_version_id)
    if c[1].as_json and not a.markdown:
        c[1].emit(card)
    else:
        # The card carries user data: never pass terminal control sequences through.
        c[1].text(_CONTROL_CHARS.sub("", card.markdown).rstrip("\n"))


def _exp_cancel(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].experiments.cancel(a.experiment_id, idempotency_key=a.idempotency_key))


def _dec_list(c: Ctx, a: argparse.Namespace) -> None:
    page = c[0].projects.decisions(a.project, state=a.state, effective_state=a.effective_state,
                                   decision_type=a.type, subject_kind=a.subject_kind,
                                   subject_id=a.subject_id, actor_kind=a.actor_kind,
                                   cursor=a.cursor, limit=a.limit)
    c[1].emit(page, table=("id", "decision_type", "effective_state", "content_origin"), rows=page.items)


def _dec_propose(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].projects.create_decision(
        a.project, decision_type=a.type, subject_kind=a.subject_kind, subject_id=a.subject_id,
        rationale=a.rationale, idempotency_key=a.idempotency_key))


def _dec_get(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].decisions.get(a.decision_id))


def _prop_list(c: Ctx, a: argparse.Namespace) -> None:
    page = c[0].proposals.list(project_id=a.project, level=a.level, decision_point_key=a.point, status=a.status,
                               proposal_type=a.type, cursor=a.cursor, limit=a.limit)
    c[1].emit(page, table=("id", "proposal_type", "decision_point_key", "status", "proposed_by"), rows=page.items)


def _prop_get(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].proposals.get(a.proposal_id))


def _prop_decide(action: str) -> Callable[[Ctx, argparse.Namespace], None]:
    def run(c: Ctx, a: argparse.Namespace) -> None:
        extra: dict[str, Any] = {}
        if action == "accept" and a.ref_versions:
            extra["ref_versions"] = _json_arg(a.ref_versions, "--ref-versions")
        c[1].emit(getattr(c[0].proposals, action)(a.proposal_id, rationale=a.rationale,
                                                  idempotency_key=a.idempotency_key, **extra))
    return run


def _whoami(c: Ctx, a: argparse.Namespace) -> None:
    c[1].emit(c[0].identity.me())


# --- parser ---------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # exit code 2, documented
        raise _UsageError(message)


class _UsageError(Exception):
    pass


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable JSON on stdout")
    idem = argparse.ArgumentParser(add_help=False)
    idem.add_argument("--idempotency-key", help="stable key so a retry of this command is safe")

    root = _Parser(prog="dclab-cli", description="DCLab customer CLI (/v1 API).")
    root.add_argument("--json", dest="json_out", action="store_true", default=False,
                      help="machine-readable JSON on stdout")
    root.add_argument("--version", action="version", version=f"dclab-cli {__version__}")
    top = root.add_subparsers(dest="group", required=True, parser_class=_Parser)

    def leaf(sub: Any, name: str, func: Callable[..., None] | None, *, key: bool = False, help: str = ""):
        p = sub.add_parser(name, parents=[common] + ([idem] if key else []), help=help)
        p.set_defaults(func=func)
        return p

    login = leaf(top, "login", None, help="store credentials (token never echoed)")
    login.add_argument("--token", help="service/access token (default: stdin or prompt)")
    login.add_argument("--api-url", help=f"API base URL (default {DEFAULT_API_URL})")
    login.add_argument("--workspace", help="workspace id (required for user tokens)")
    leaf(top, "logout", None, help="remove stored credentials")
    leaf(top, "whoami", _whoami, help="show the authenticated principal")

    projects = top.add_parser("projects", help="projects").add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    leaf(projects, "list", _projects_list)
    p = leaf(projects, "create", _projects_create, key=True)
    p.add_argument("--name", required=True)
    p.add_argument("--slug")
    p.add_argument("--description", default="")
    leaf(projects, "get", _projects_get).add_argument("project_id")

    data = top.add_parser("data", help="datasets").add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    p = leaf(data, "upload", _data_upload, key=True)
    p.add_argument("file")
    p.add_argument("--project", required=True, help="project id")
    p.add_argument("--content-type")
    p.add_argument("--purpose", choices=("training", "scoring"),
                   help="scoring: rows to score with `predict create` (default training)")

    exps = top.add_parser("experiments", help="experiments").add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    p = leaf(exps, "list", _exp_list)
    p.add_argument("--project")
    p.add_argument("--status")
    p.add_argument("--parent")
    p.add_argument("--cursor")
    p.add_argument("--limit", type=int)
    leaf(exps, "get", _exp_get).add_argument("experiment_id")
    p = leaf(exps, "run", _exp_run, key=True, help="queue a root run on a published dataset")
    p.add_argument("--project", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--problem-spec")
    p.add_argument("--target")
    p.add_argument("--intent")
    p = leaf(exps, "branch", _exp_branch, key=True, help="branch a completed experiment")
    p.add_argument("experiment_id")
    p.add_argument("--intent", required=True)
    p.add_argument("--changes", help="JSON list of change objects")
    p.add_argument("--changes-file", help="file holding the JSON change list")
    leaf(exps, "compare", _exp_compare, help="compare 2-10 experiments").add_argument("experiment_ids", nargs="+")
    p = leaf(exps, "code", _exp_code, help="reproducible script (or notebook) of an experiment")
    p.add_argument("experiment_id")
    p.add_argument("--notebook", action="store_true", help="the notebook instead of the script")
    p.add_argument("--output", "-o", help="write the source to this file")
    leaf(exps, "findings", _exp_findings, help="trust checks of a run (leakage, overfit, duplicates, "
         "imbalance, too-good score)").add_argument("experiment_id")
    leaf(exps, "cancel", _exp_cancel, key=True).add_argument("experiment_id")

    models = top.add_parser("models", help="model versions").add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    p = leaf(models, "card", _models_card, help="one-page model card (Markdown; --json for the JSON card)")
    p.add_argument("model_version_id")
    p.add_argument("--markdown", action="store_true", help="print the Markdown even with --json")

    pred = top.add_parser("predict", help="batch predictions").add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    p = leaf(pred, "create", _predict_create, key=True, help="score a dataset with a model version")
    p.add_argument("--model-version", required=True, help="model version id")
    p.add_argument("--dataset", required=True, help="dataset id (uploaded with --purpose scoring)")
    p.add_argument("--format", choices=("csv", "parquet"), default="csv", help="output file format")
    p.add_argument("--wait", action="store_true", help="poll until completed or failed")
    p.add_argument("--timeout", type=float, default=600.0, help="seconds to --wait (default 600)")
    leaf(pred, "get", _predict_get).add_argument("prediction_id")
    p = leaf(pred, "download", _predict_download, help="save a completed predictions file")
    p.add_argument("prediction_id")
    p.add_argument("--output", "-o", required=True, help="file to write")

    decs = top.add_parser("decisions", help="decision records").add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    p = leaf(decs, "list", _dec_list)
    p.add_argument("--project", required=True)
    p.add_argument("--state")
    p.add_argument("--effective-state")
    p.add_argument("--type")
    p.add_argument("--subject-kind")
    p.add_argument("--subject-id")
    p.add_argument("--actor-kind")
    p.add_argument("--cursor")
    p.add_argument("--limit", type=int)
    p = leaf(decs, "propose", _dec_propose, key=True)
    p.add_argument("--project", required=True)
    p.add_argument("--type", required=True, help="decision type")
    p.add_argument("--subject-kind", required=True)
    p.add_argument("--subject-id")
    p.add_argument("--rationale", required=True)
    leaf(decs, "get", _dec_get).add_argument("decision_id")

    props = top.add_parser("proposals", help="AI proposals (deciding needs a person's credential, not a service token)"
                           ).add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    p = leaf(props, "list", _prop_list)
    p.add_argument("--project")
    p.add_argument("--level", type=int)
    p.add_argument("--point", help="decision point key")
    p.add_argument("--status")
    p.add_argument("--type", help="proposal type")
    p.add_argument("--cursor")
    p.add_argument("--limit", type=int)
    leaf(props, "get", _prop_get).add_argument("proposal_id")
    for action, text in (("accept", "run its command as you and record the decision"), ("reject", "reject it"),
                         ("revert", "restore the rule value of an applied item")):
        p = leaf(props, action, _prop_decide(action), key=True, help=text)
        p.add_argument("proposal_id")
        p.add_argument("--rationale")
        if action == "accept":
            p.add_argument("--ref-versions", help='JSON, e.g. {"problem_spec": null}: versions you saw of moved refs')
    return root


# --- entry ----------------------------------------------------------------


def _read_token(args: argparse.Namespace, stdin: TextIO, stderr: TextIO) -> str:
    if args.token:
        print("warning: --token is visible in shell history and process lists; prefer stdin or the prompt",
              file=stderr)
        return args.token.strip()
    if stdin.isatty():
        return getpass.getpass("Token: ").strip()
    return stdin.readline().strip()


def _login(args: argparse.Namespace, env: Mapping[str, str], out: _Out, stdin: TextIO, stderr: TextIO,
           http: httpx.Client | None) -> None:
    token = _read_token(args, stdin, stderr)
    if not token:
        raise DCLabClientError("no token given (--token, stdin or prompt)")
    stored = load_config(env)
    api_url = args.api_url or (env.get(ENV_API_URL) or "").strip() or stored.get("api_url") or DEFAULT_API_URL
    check_api_url(api_url, allow_insecure_http=flag(env.get(ENV_ALLOW_INSECURE_HTTP)))
    workspace = args.workspace or env.get(ENV_WORKSPACE) or stored.get("workspace")
    with DCLabClient(api_url, token=token, workspace_id=workspace, http=http) as api:
        principal = api.identity.me()  # fail before storing anything
    values = {"token": token, "api_url": str(api_url)}
    if workspace:
        values["workspace"] = str(workspace)
    path = save_config(env, values)
    out.emit({"logged_in": True, "api_url": api_url, "workspace_id": workspace,
              "principal_id": principal.id, "config": str(path)})


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    http: httpx.Client | None = None,
) -> int:
    """Run the CLI and return the exit code. ``http`` injects an httpx client (tests)."""

    env = os.environ if env is None else env
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    args_list = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args_list
    try:
        args = _build_parser().parse_args(args_list)
    except _UsageError as exc:
        print(f"usage error: {exc}", file=stderr)
        return EXIT_USAGE
    except SystemExit as exc:  # --help / --version
        return int(exc.code or 0)
    out = _Out(stdout, args.json_out, stderr)
    try:
        if args.group == "login":
            _login(args, env, out, stdin, stderr, http)
            return EXIT_OK
        if args.group == "logout":
            config_path(env).unlink(missing_ok=True)
            out.emit({"logged_out": True})
            return EXIT_OK
        settings = resolve_settings(env)
        if not settings["token"]:
            print("error: not logged in (run `dclab-cli login` or set DCLAB_TOKEN)", file=stderr)
            return EXIT_AUTH
        with DCLabClient(str(settings["api_url"]), token=settings["token"],
                         workspace_id=settings["workspace"], http=http) as api:
            result = args.func((api, out), args)
        return result if isinstance(result, int) else EXIT_OK
    except DCLabAPIError as exc:
        _print_api_error(exc, stderr, as_json)
        return exit_code_for(exc)
    except httpx.TransportError as exc:
        print(f"error: cannot reach the API ({type(exc).__name__}); retry later", file=stderr)
        return EXIT_RETRYABLE
    except (DCLabClientError, OSError) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_ERROR


def _print_api_error(exc: DCLabAPIError, stderr: TextIO, as_json: bool) -> None:
    if as_json:
        envelope = {"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable,
                              "request_id": exc.request_id, "status": exc.status_code}}
        print(json.dumps(envelope), file=stderr)
    else:
        print(f"error: {exc.code}: {exc.message} (request_id={exc.request_id or '-'})", file=stderr)
