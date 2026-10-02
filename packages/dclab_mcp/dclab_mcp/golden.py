"""Golden MCP scenarios: a deterministic client script that drives the DCLab MCP tools in a
fixed order and records a normalised transcript.

Used two ways:

* the API test-suite runs each scenario against the FastAPI test app and compares the
  normalised transcript with ``apps/api/tests/golden/mcp/<scenario>.json``;
* a founder runs it against a live stack (worker running, token with all scopes)::

      DCLAB_MCP_WRITE_ENABLED=1 python -m dclab_mcp.golden --scenario classification

It speaks real MCP (stdio subprocess ``python -m dclab_mcp``) and uses the SDK only to
create a project and upload the synthetic CSV (there is no MCP tool for that, by design).
A human acceptance in Studio is never simulated: scenarios stop at proposals.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import re
import sys
import time
from collections.abc import Callable
from typing import Any

Call = Callable[[str, dict[str, Any] | None], dict[str, Any]]
Wait = Callable[[str], None]

SCENARIOS = ("classification", "regression", "branch_compare")
POLL_SECONDS = 3.0
POLL_LIMIT = 200

# --- deterministic synthetic data (stdlib only, seeded) ---------------------------------------


def _rows(n: int, seed: int, label: Callable[[random.Random, float, float, str], Any], target: str) -> bytes:
    rng = random.Random(seed)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["tenure", "spend", "plan", target])
    for _ in range(n):
        tenure, spend, plan = float(rng.randint(1, 71)), round(rng.uniform(20, 120), 2), rng.choice(["basic", "pro"])
        writer.writerow([tenure, spend, plan, label(rng, tenure, spend, plan)])
    return out.getvalue().encode()


def classification_csv(n: int = 240, seed: int = 3) -> bytes:
    def label(rng: random.Random, tenure: float, spend: float, plan: str) -> str:
        p = 1 / (1 + math.exp(0.6 + 0.04 * tenure - 0.01 * spend + (0.8 if plan == "pro" else 0)))
        return "yes" if rng.random() < p else "no"
    return _rows(n, seed, label, "churned")


def regression_csv(n: int = 240, seed: int = 5) -> bytes:
    def label(rng: random.Random, tenure: float, spend: float, plan: str) -> float:
        return round(40 + 1.5 * tenure + 0.8 * spend + (25 if plan == "pro" else 0) + rng.gauss(0, 8), 2)
    return _rows(n, seed, label, "value_90d")


DATASETS = {"classification": (classification_csv, "churned"), "regression": (regression_csv, "value_90d"),
            "branch_compare": (classification_csv, "churned")}

# --- scenarios ---------------------------------------------------------------------------------


def _spec_and_run(call: Call, wait: Wait, project_id: str, dataset_id: str, *, task_type: str, target: str,
                  objective: str) -> tuple[str, str]:
    call("inspect_project", {"project_id": project_id})
    call("inspect_dataset", {"dataset_id": dataset_id})
    spec = call("propose_problem_spec", {
        "project_id": project_id, "task_type": task_type, "target_column": target,
        "business_objective": objective, "rationale": "Agent-drafted spec from the dataset schema."})
    spec_id = spec["problem_spec"]["id"]
    run = call("run_experiment", {"project_id": project_id, "dataset_id": dataset_id, "problem_spec_id": spec_id,
                                  "target_column": target, "intent": "baseline"})["experiment"]
    wait(run["id"])
    call("get_experiment", {"experiment_id": run["id"]})
    return spec["proposal"]["id"], run["id"]


def _evidence_and_propose(call: Call, project_id: str, experiment_id: str, rationale: str) -> None:
    call("get_evidence", {"experiment_id": experiment_id})
    call("get_experiment_code", {"experiment_id": experiment_id})
    proposed = call("record_decision", {
        "project_id": project_id, "decision_type": "experiment_accepted", "subject_kind": "experiment",
        "subject_id": experiment_id, "rationale": rationale,
        "evidence_refs": [{"kind": "experiment", "id": experiment_id}]})
    call("accept_proposal", {"proposal_id": proposed["proposal"]["id"]})  # hand-off; never accepts
    call("list_decisions", {"project_id": project_id, "effective_state": "proposed"})


def classification(call: Call, wait: Wait, project_id: str, dataset_id: str) -> None:
    _, run_id = _spec_and_run(call, wait, project_id, dataset_id, task_type="binary_classification",
                              target="churned", objective="Reduce churn")
    _evidence_and_propose(call, project_id, run_id, "Baseline CV is acceptable; propose keeping it.")


def regression(call: Call, wait: Wait, project_id: str, dataset_id: str) -> None:
    _, run_id = _spec_and_run(call, wait, project_id, dataset_id, task_type="regression",
                              target="value_90d", objective="Forecast 90-day customer value")
    _evidence_and_propose(call, project_id, run_id, "Baseline CV error is acceptable; propose keeping it.")


def branch_compare(call: Call, wait: Wait, project_id: str, dataset_id: str) -> None:
    _, run_id = _spec_and_run(call, wait, project_id, dataset_id, task_type="binary_classification",
                              target="churned", objective="Reduce churn")
    branch = call("branch_experiment", {"experiment_id": run_id, "intent": "no xgboost",
                                        "changes": [{"kind": "family_exclude", "family": "xgboost"}]})["experiment"]
    wait(branch["id"])
    call("get_experiment", {"experiment_id": branch["id"]})
    call("compare_experiments", {"experiment_ids": [run_id, branch["id"]]})
    _evidence_and_propose(call, project_id, branch["id"], "The branch holds CV quality with a simpler search.")


RUNNERS = {"classification": classification, "regression": regression, "branch_compare": branch_compare}


def record(call: Call) -> tuple[Call, list[dict[str, Any]]]:
    """Wrap ``call`` so every tool call lands in a raw transcript."""

    log: list[dict[str, Any]] = []

    def recorded(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        result = call(name, args)
        log.append({"tool": name, "args": args or {}, "result": result})
        return result
    return recorded, log


# --- normalisation -----------------------------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
_VOLATILE_KEYS = {"request_id": "<request_id>", "duration_ms": "<ms>", "size_bytes": "<bytes>",
                  "source": "<generated code>", "content_digest": "<digest>"}
FLOAT_DIGITS = 2


class Normalizer:
    """Ids become ``<kind:N>`` by first appearance, so the order of events (not the random
    values) is what the golden file pins. Floats are rounded; durations/sizes are blanked."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}

    def _sub_str(self, text: str) -> str:
        text = _UUID.sub(lambda m: self._ids.setdefault(m.group(0), f"<id:{len(self._ids) + 1}>"), text)
        return _DIGEST.sub("<digest>", _TS.sub("<ts>", text))

    @staticmethod
    def _ordered(key: str, value: Any) -> Any:
        """The API lists artifacts in storage order, which is not stable: sort before ids are assigned."""

        if key == "artifacts" and isinstance(value, list) and all(isinstance(a, dict) for a in value):
            return sorted(value, key=lambda a: (str(a.get("role", "")), str(a.get("artifact_type", ""))))
        return value

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (_VOLATILE_KEYS[k] if k in _VOLATILE_KEYS and v is not None else self(self._ordered(k, v)))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            return self._sub_str(value)
        if isinstance(value, float):
            return round(value, FLOAT_DIGITS)
        return value


def normalize(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    norm = Normalizer()
    return [norm(step) for step in log]


# --- live runner -------------------------------------------------------------------------------


def _poll(call: Call) -> Wait:
    def wait(experiment_id: str) -> None:
        for _ in range(POLL_LIMIT):
            status = call("get_experiment", {"experiment_id": experiment_id})["experiment"]["status"]
            if status in ("completed", "failed", "cancelled", "canceled"):
                if status != "completed":
                    raise SystemExit(f"experiment {experiment_id} ended as {status}")
                return
            time.sleep(POLL_SECONDS)
        raise SystemExit(f"experiment {experiment_id} did not finish; is the worker running?")
    return wait


def _stdio_caller(env: dict[str, str]) -> tuple[Call, Callable[[], None]]:
    import anyio
    from mcp import Client, StdioServerParameters  # noqa: F401  (client API of mcp==2.2.0)

    def call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        async def go() -> dict[str, Any]:
            async with Client(StdioServerParameters(command=sys.executable, args=["-m", "dclab_mcp"], env=env)) as c:
                return (await c.call_tool(name, args or {})).structured_content
        return anyio.run(go)
    return call, lambda: None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dclab_mcp.golden", description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--project-id", help="existing project; default: create a new one")
    parser.add_argument("--output", help="write the normalised transcript here")
    args = parser.parse_args(argv)

    from dclab_client import DCLabClient

    from dclab_mcp.server import settings_from_env
    env = {**os.environ, "DCLAB_MCP_WRITE_ENABLED": "1"}
    settings = settings_from_env(env)
    make_csv, _target = DATASETS[args.scenario]
    with DCLabClient(settings.api_url, token=settings.token, workspace_id=settings.workspace or None) as api:
        project_id = args.project_id or str(api.projects.create(name=f"MCP golden {args.scenario}").id)
        dataset_id = str(api.datasets.upload(project_id, io.BytesIO(make_csv()), filename=f"{args.scenario}.csv").id)
    raw_call, _close = _stdio_caller(env)
    call, log = record(raw_call)
    RUNNERS[args.scenario](call, _poll(raw_call), project_id, dataset_id)
    text = json.dumps(normalize(log), indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(f"{args.scenario}: {len(log)} tool calls recorded, project {project_id}")
    print("Open the project in Studio (Decisions tab) to accept or reject the proposals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
