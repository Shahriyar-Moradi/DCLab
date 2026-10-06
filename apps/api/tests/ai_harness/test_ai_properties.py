"""P6.10-B property tests, each with a planted-violation meta-test that injects the violation
and asserts the property check fails (so CI keeps proving every check can fail):

* CodeAct ban: no module of the API / worker executes generated code (import aliases
  resolved), no unreviewed runtime, no non-Predict agent class, no sandbox setting on;
* holdout-blind tool registry over every surface (assistant, MCP, Studio forms), the
  export, the committed contract and the live MCP listing, plus every read tool's shaped
  result on a graph seeded with final-holdout values;
* no raw rows, sample values, holdout values, canary cells or secrets in a provider request
  body, with a positive control (CV values DO reach the model when the data policy allows);
* every model call has one complete ``llm_invocations`` row;
* every agent run has its ``agent_runs`` row and ``agent_events``, linked to its calls.
The positive runtime checks also run on every recorded scenario and every chaos case.
"""

from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import replace
from types import SimpleNamespace
from typing import get_args
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import create_model
from sqlalchemy import select

from ai_harness import (
    answers,
    check_codeact_ban,
    check_harness_records,
    check_ledger,
    check_no_raw_rows,
    codeact_findings,
    raw_row_findings,
    registry_findings,
)
from ai_harness.kit import TESTS, numbers_in
from ai_harness.scenarios import (
    CANARY_CELL,
    seed_locked,
    CANARY_HOLDOUT_METRICS,
    CANARY_HOLDOUT_SUMMARY,
    CV_VALUE,
    columns,
    plant_canaries,
    sentinels,
    lead_steps,
    run_lead,
    run_specialist,
    specialist_answer,
)
from app.agents.contracts import AssistantStep
from app.agents.gateway import ledger, redaction
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers import ProviderCall
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.providers.openai import OpenAIProvider
from app.agents.harness import service as svc
from app.agents.lead.fake_driver import ScriptedLeadDriver
from app.agents.tools import catalog as catalog_module
from app.agents.tools.catalog import ToolContext, ToolError
from app.agents.tools.shaping import HOLDOUT_KEY
from app.db.models import EvaluationMetric, ModelEvaluation, ModelSelectionDecision, ModelVersion
from test_agent_classes import ON, _Uncached
from test_decision_record_service import _propose

pytestmark = pytest.mark.ai_harness

CONTRACT = TESTS.parents[2] / "contracts" / "agent_tools.json"
CRITIC = "experiment_critic"


def test_the_harness_refuses_network_access():
    with pytest.raises(AssertionError, match="offline AI harness"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)
    with pytest.raises(AssertionError, match="offline AI harness"):
        socket.getaddrinfo("api.openai.com", 443)


# --- CodeAct ban ---------------------------------------------------------------------------------


def test_no_module_or_runtime_can_execute_generated_code():
    check_codeact_ban()
    assert set(get_args(AssistantStep.model_fields["kind"].annotation)) == {"tool_calls", "answer", "clarify", "done"}


def _tree(root, files):
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def test_codeact_check_catches_planted_violations(tmp_path):
    planted = {
        "agents/runtime/codeact.py": "def run(out):\n    exec(out.code)\n",
        "services/worker_eval.py": "value = eval(answer)\n",
        "agents/lead/shell.py": "import subprocess\nsubprocess.run(cmd, shell=True)\n",
        "agents/tools/system.py": "import os\nos.system(cmd)\n",
        "agents/classes/script.py": "import runpy\nrunpy.run_path(path)\n",
        "agents/runtime/nooa2.py": "from nooa.strategies.codeact import CodeActStrategy\n",
        "a/from_os.py": "from os import system\nsystem(cmd)\n",
        "a/os_alias.py": "import os as o\no.popen(cmd)\n",
        "a/builtins_attr.py": "import builtins\nbuiltins.eval(src)\n",
        "a/builtins_from.py": "from builtins import exec as run\nrun(src)\n",
        "a/getattr_builtins.py": "getattr(__builtins__, 'eval')(src)\n",
        "a/getattr_dynamic.py": "import os\ngetattr(os, name)(cmd)\n",
        "a/dunder_import.py": "__import__('os').system(cmd)\n",
        "a/import_module.py": "import importlib\nimportlib.import_module(name)\n",
        "a/import_exec_module.py": "from importlib import import_module as im\nim('subprocess')\n",
        "a/asyncio_shell.py": "import asyncio\nasyncio.create_subprocess_shell(cmd)\n",
        "a/exec_module.py": "spec.loader.exec_module(module)\n",
        "a/compile_code.py": "code_obj = compile(src, 'x', 'exec')\n",
        "a/ref_eval.py": "run = eval\nrun(src)\n",
        "a/ref_system.py": "import os\nf = os.system\nf(cmd)\n",
        "a/import_os.py": "import importlib\nimportlib.import_module('posix').system(cmd)\n",
        "a/sys_modules.py": "import sys\nsys.modules['os'].system(cmd)\n",
        "a/dunder_dict.py": "import builtins\nbuiltins.__dict__['exec'](src)\n",
        "a/any_popen.py": "handle.popen(cmd)\n",
        "a/pickle_loads.py": "import pickle\nobj = pickle.loads(blob)\n",
        "services/lab_service.py": "import subprocess\nsubprocess.check_output(['git', 'rev-parse', 'HEAD'])\n",
        "services/fine.py": "import re\nimport importlib\nre.compile('x')\nimportlib.import_module('json')\n",
    }
    none = dict(settings_fields={}, runtimes={}, classes={})
    flagged = {item.split(":")[0] for item in codeact_findings(_tree(tmp_path / "app", planted), **none)}
    assert flagged == set(planted) - {"services/lab_service.py", "services/fine.py"}
    lab = {"services/lab_service.py": "import subprocess\nsubprocess.check_output(['git', 'rev-parse', 'HEAD'])\n"
                                      "subprocess.run(['git', 'log'], shell=True)\nsubprocess.run(argv)\n"
                                      "subprocess.run(['sh', name])\nsubprocess.run(['python', '-'], input=code)\n"
                                      "subprocess.run(['git', 'rev-parse', 'HEAD'], stdin=source)\n"
                                      "subprocess.run(['git', 'log'])\n"}
    found = codeact_findings(_tree(tmp_path / "lab", lab), **none)
    assert sorted(int(f.split(":")[1]) for f in found) == [3, 4, 5, 6, 7, 8]  # only the exact probe passes
    empty = tmp_path / "empty"
    empty.mkdir()
    assert codeact_findings(empty, settings_fields={"agents_sandbox_lane_enabled": True, "sandbox_image": "",
                                                    "reproducible_code_export_enabled": True},
                            runtimes={"fake": None, "nooa_codeact": None},
                            classes={"x": SimpleNamespace(strategy="codeact")}) == [
        "setting agents_sandbox_lane_enabled defaults on", "runtime nooa_codeact is not reviewed",
        "agent class x uses codeact"]
    with pytest.raises(AssertionError, match="generated code"):
        check_codeact_ban(root=tmp_path / "app")  # the property test fails on the planted tree


def test_no_product_code_switches_off_triggers():
    """``session_replication_role`` skips every trigger (the evidence locks): tests only."""

    roots = [TESTS.parent / "app", TESTS.parent / "alembic", TESTS.parent / "alembic_frozen"]
    hits = [str(p) for root in roots if root.is_dir() for p in root.rglob("*.py")
            if "session_replication_role" in p.read_text(encoding="utf-8")]
    assert hits == []


# --- holdout-blind tool registry ----------------------------------------------------------------


def assert_registry_holdout_blind(*, contract=None, mcp_tools=None) -> None:
    contract = contract if contract is not None else json.loads(CONTRACT.read_text(encoding="utf-8"))
    findings = registry_findings(list(catalog_module.catalog().values()), catalog_module.export_payload(), contract,
                                 mcp_tools)
    assert not findings, findings
    assert all(catalog_module.visible(surface) for surface in catalog_module.SURFACES)


def test_no_tool_on_any_surface_reads_holdout_selects_or_promotes():
    assert_registry_holdout_blind()


def test_the_live_mcp_listing_is_the_holdout_blind_catalog():
    try:
        import dclab_mcp  # noqa: F401
    except ImportError as exc:
        pytest.skip(f"dclab_mcp is not importable here: {exc}")
    try:  # dclab_mcp is present: an mcp import failure is a failure (in CI always)
        import anyio
        from mcp import Client
        from mcp.client._memory import InMemoryTransport

        from dclab_mcp import Settings, build_server
    except ImportError:
        if os.environ.get("CI"):
            raise
        pytest.fail("dclab_mcp imports but the mcp SDK does not (install requirements-mcp.lock)")
    server = build_server(Settings(api_url="http://127.0.0.1:9", token="dclab_st_" + "a" * 32 + "_" + "B" * 43,
                                   write_enabled=True))

    async def go():
        async with Client(InMemoryTransport(server), mode="legacy") as client:
            return (await client.list_tools()).tools

    assert_registry_holdout_blind(mcp_tools={tool.name: tool.input_schema for tool in anyio.run(go)})


def test_registry_check_catches_planted_tools(monkeypatch):
    tools = dict(catalog_module.catalog())
    read, write = tools["get_experiment"], tools["record_decision"]
    schema = create_model("PlantedArgs", experiment_id=(str, ...), final_test_rows=(int, 0))
    planted = {
        "get_holdout_metrics": replace(read, name="get_holdout_metrics"),
        "get_summary": replace(read, name="get_summary", services=("metrics_service.select_winner",)),
        "get_scores": replace(read, name="get_scores", outcome_scope="holdout"),
        "get_rows": replace(read, name="get_rows", input_schema=schema),
        "set_level": replace(write, name="set_level", decision_point_key="column.semantic_role"),  # an L2 cap
        "list_runs": replace(read, name="list_runs", operations=("POST /v1/experiments",)),
        "get_raw": replace(read, name="get_raw", shaper=None),
    }
    for name, tool in planted.items():
        monkeypatch.setattr(catalog_module, "catalog", lambda tool=tool: {**tools, tool.name: tool})
        export = catalog_module.export_payload()  # the planted catalog's own contract: no drift
        assert [f for f in registry_findings(catalog_module.catalog().values(), export, export, None)
                if f.startswith(f"{name}:")], name
        with pytest.raises(AssertionError, match=name):
            assert_registry_holdout_blind(contract=export)  # fails on the planted tool itself
    monkeypatch.undo()
    honest = list(tools.values())
    export = catalog_module.export_payload()
    mcp = {t.name: t.input_schema.model_json_schema() for t in honest if "mcp" in t.surfaces}
    assert registry_findings(honest, export, export, mcp) == []
    assert registry_findings(honest, export, export, {**mcp, "read_holdout": {}}) != []
    assert registry_findings(honest, export, export, {**mcp, "get_impact": {"properties": {"holdout_split": {}}}}) != []
    leaky = {**export, "tools": [*export["tools"][:-1], {**export["tools"][-1], "result": {"outcome_scope": "holdout"}}]}
    assert registry_findings(honest, export, leaky, mcp) != []


def _critic(ac, provider):  # uncached: a repeated input reaches the provider again
    return run_specialist(ac, CRITIC, _Uncached(providers={"openai": provider}, limits=GatewayLimits(),
                                                settings=lambda: ON))


def _keys(value, pattern) -> list[str]:
    if isinstance(value, dict):
        return [k for k in value if pattern.search(str(k))] + [x for v in value.values() for x in _keys(v, pattern)]
    return [x for v in value for x in _keys(v, pattern)] if isinstance(value, list) else []


_PROBE = ProviderCall(model="probe", instructions="", input_json="{}", output_schema=AssistantStep,
                      max_output_tokens=1, temperature=None, timeout_s=1.0, purpose="probe", agent_key=None)


def test_every_read_tool_result_is_holdout_free_on_the_seeded_graph(ac):
    g, db = ac.g, ac.db
    plant_canaries(ac, db, g.ws)
    seeded = sentinels(db, g.ws)
    assert set(CANARY_HOLDOUT_METRICS.values()) <= set(seeded["numbers"]) and 0.7001 in seeded["numbers"]
    proposed = _propose(db, g)  # a pending decision record: the accept_proposal hand-off
    model = db.scalar(select(ModelVersion.id).where(ModelVersion.workspace_id == g.ws, ModelVersion.pipeline_run_id
                                                     == g.exp[1]).order_by(ModelVersion.created_at, ModelVersion.id))
    _seed_winner(db, g, 0)  # exp[0] gets a locked CV winner too, so compare_experiments runs
    args = {"inspect_project": {"project_id": g.project.id},
            "inspect_dataset": {"dataset_id": g.source.id, "project_id": g.project.id},
            "compare_experiments": {"experiment_ids": [str(g.exp[0]), str(g.exp[1])]},
            "list_decisions": {"project_id": g.project.id}, "list_proposals": {"project_id": g.project.id},
            "get_model": {"model_version_id": model}, "get_model_card": {"model_version_id": model},
            "get_impact": {"kind": "experiment", "node_id": g.exp[1]},
            "accept_proposal": {"proposal_id": proposed.id},
            "get_prediction": {"prediction_id": uuid4()}}  # no prediction in the graph: refused, typed
    ctx = ToolContext(db=db, actor=g.actor, workspace_id=g.ws)
    checked, refused = [], {}
    for name, tool in catalog_module.catalog().items():
        if tool.effect != "read":
            continue
        arguments = {k: v if isinstance(v, list) else str(v)
                     for k, v in args.get(name, {"experiment_id": g.exp[1]}).items()}
        try:
            shaped = json.loads(json.dumps(tool.read(ctx, arguments).payload, default=str))
        except ToolError as exc:
            refused[name] = exc.code
            db.rollback()
            continue
        text = json.dumps(shaped)  # the request-body scan (code text may name its holdout section)
        found = raw_row_findings(replace(_PROBE, input_json=text), columns=columns(db, g.ws), **seeded)
        if name == "get_experiment_code":
            found = [f for f in found if not f.startswith("holdout_name:$.source")]
            assert not re.search(r"HOLDOUT_METRICS = \{[^}\s]", text)  # no filled holdout literal
        if name == "get_evidence":  # holdout stages are listed by label only, their results withheld
            labels = [f for f in found if re.fullmatch(r"holdout_name:\$\.model_build\.stages\[\d+\]\.(key|title)", f)]
            for index in {int(re.search(r"\[(\d+)\]", f).group(1)) for f in labels}:
                stage = shaped["model_build"]["stages"][index]
                assert all(v is None for k, v in stage.items() if k not in ("key", "title", "status")), stage
            found = [f for f in found if f not in labels]
        found = [f for f in found if f != "holdout_name:$.note"]  # the fixed "never final-holdout" notice
        assert not found and not _keys(shaped, HOLDOUT_KEY), (name, found)
        checked.append(name)
    # The seeded graph has no prediction; every other read tool returned a shaped result.
    assert refused == {"get_prediction": "not_found"}, refused
    assert len(checked) == len([t for t in catalog_module.catalog().values() if t.effect == "read"]) - 1


def _seed_winner(db, g, index) -> None:
    candidate = g.cand[index]
    seed_locked(db, ModelSelectionDecision(
        workspace_id=g.ws, project_id=g.project.id, pipeline_run_id=g.exp[index], selected_candidate_id=candidate,
        selection_metric="roc_auc", selected_score=0.7712, selection_policy="cv_best", reason="best CV",
        evidence={}, locked_at=datetime(2026, 9, 2, tzinfo=UTC)))
    evaluation = ModelEvaluation(workspace_id=g.ws, project_id=g.project.id, candidate_id=candidate,
                                 evaluation_type="cross_validation", evaluation_scope="cv_aggregate",
                                 dataset_id=g.prepared.id, status="completed", summary={})
    seed_locked(db, evaluation, lambda: [EvaluationMetric(model_evaluation_id=evaluation.id, metric_name="roc_auc",
                                                          metric_value=0.7712)])


def test_a_comparison_refusal_is_a_typed_tool_error(ac):
    """``ExperimentComparisonError`` maps to its code (409), as on ``/v1`` — not an internal error."""

    g = ac.g
    with pytest.raises(ToolError) as refused:
        catalog_module.catalog()["compare_experiments"].read(
            ToolContext(db=ac.db, actor=g.actor, workspace_id=g.ws),
            {"experiment_ids": [str(g.exp[0]), str(g.exp[1])]})
    assert (refused.value.code, refused.value.status) == ("winner_missing", 409)


# --- no raw rows leave the gateway ----------------------------------------------------------------


@pytest.mark.parametrize("agent", [CRITIC, "lead"])
def test_cv_values_reach_the_model_but_holdout_values_and_raw_cells_never(agent, request, monkeypatch):
    monkeypatch.setattr(redaction._Labels, "exposure", lambda self, source: ("allow", "internal"))
    if agent == CRITIC:
        ns = request.getfixturevalue("ac")
        provider = FakeProvider(handler=answers(specialist_answer(ns, CRITIC)), environment="test")
        result, calls = _critic(ns, provider), provider.calls
    else:
        ns = request.getfixturevalue("lead")
        result, calls = run_lead(ns, ScriptedLeadDriver(lead_steps(ns.g))), ns.fake.calls
    assert result.status == "completed", result
    assert CV_VALUE in set().union(*(numbers_in(c.input_json) for c in calls))  # positive control
    check_no_raw_rows(calls, columns=columns(ns.db, ns.g.ws), **sentinels(ns.db, ns.g.ws))


HOLDOUT_SHAPES = {  # each planted through the real redaction path -> the finding it must raise
    "openai_audit": ({"overall_status": "FAILED"}, "holdout_name:$.openai_audit"),
    "llm_report": ({"verdict": "ok"}, "holdout_name:$.llm_report"),
    "verification_attempt": (2, "holdout_name:$.verification_attempt"),
    "advisory_status": ("FAILED", "holdout_name:$.advisory_status"),
    "checks": ([{"check": "objective_constraints_met_holdout", "status": "fail"}], "holdout_name:$.checks[0].check"),
    "metrics": ({"holdout": 0.5}, "holdout_name:$.metrics.holdout"),
    "final_holdout": ({"status": "done"}, "holdout_name:$.final_holdout"),
    "split": ({"test_metrics": {"auc": 0.5}}, "holdout_name:$.split.test_metrics"),
    # context fields as the harness renders them: the field name is a dotted key-string path
    "ctx_report": ([{"key": "get_evidence.openai_audit", "value": {"overall_status": "FAILED"}}],
                   "holdout_name:$.ctx_report[0].key"),
    "ctx_nested": ({"key": "x.llm_report.overall_status", "value": "FAILED"}, "holdout_name:$.ctx_nested.key"),
    "ctx_metrics": ({"key": "get_experiment.test_metrics.roc_auc", "value": 0.5}, "holdout_name:$.ctx_metrics.key"),
    "ctx_path_value": ({"source": "verifier.checks[3]:objective_constraints_met_holdout"},
                       "holdout_name:$.ctx_path_value.source"),
    "labels": (CANARY_HOLDOUT_SUMMARY["label_counts"], "holdout_count:812"),
    "y": ([0, 1, 1, 0, 1, 0, 0, 1, 1, 0], "value_vector:$.y"),
    "rate": (CANARY_HOLDOUT_METRICS["positive_rate"], f"holdout_value:{CANARY_HOLDOUT_METRICS['positive_rate']}"),
    "rounded": (0.70, "holdout_value:0.7001"), "three_digits": ("about 0.700", "holdout_value:0.7001"),
    "percent": ("70.01%", "holdout_rendering:pct:70.0"), "dp2": ("auc 0.64", "holdout_rendering:dp2:0.64"),
    "pct_int": ("64%", "holdout_rendering:pct:64.0"), "pct_dec": ("64.2 percent", "holdout_rendering:pct:64.2"),
    "evidence": ([{"feature": 1, "region": "n", "target": 0}, {"feature": 2, "region": "s", "target": 1}],
                 "row_table:$.evidence"),
    "one": ({"feature": 3, "region": "e"}, "row_dict:$.one"),
    "cols": ({"feature": [1, 2, 3], "target": [0, 1, 0]}, "column_table:$.cols"),
    "lines": (["1,n,0", "2,s,1"], "row_strings:$.lines"), "csv": ("feature,region,target\n1,n,0\n2,s,1",
                                                                  "csv_text:$.csv"),
    "m": ([[1, 0], [2, 1]], "matrix:$.m"), "profile": ({"top_values": ["n"]}, "row_carrier:$.profile.top_values"),
    "cell": (CANARY_CELL, f"cell:{CANARY_CELL}"),
    "debug": (os.environ.get("DCLAB_TYPESAFE_API_KEY", ""), "secret:value"),  # the conftest canary
}
# Allowed (orchestrator decision): holdout SIZE and FRACTION are SplitPlan design metadata; CV
# fold scores, free-text data naming a holdout, and bare integers are not outcomes.
ALLOWED_SHAPES = {
    "model_card": {"split": {"evaluation_rows": CANARY_HOLDOUT_SUMMARY["n_rows"], "evaluation_fraction": 0.2}},
    "findings": {"test_rows": 1237, "train_test_duplicate_rows": 0, "n_test": 1237, "test_row_count": 1237},
    "folds": {"cv_fold_scores": [0.8123, 0.80, 0.79, 0.82, 0.84, 0.77, 0.86, 0.78]},
    "data": {"question": {"untrusted_text": "is the holdout set fine?"}, "column": "final_test",
             "note": "rows in the final_test column"},
    "ints": {"row_count": 81, "n_features": 64},
}


def test_raw_rows_check_catches_every_planted_shape_through_the_gateway(ac, monkeypatch):
    real, planted = redaction.redact, {}

    def leaky(*args, **kwargs):  # a redaction regression forwarding one shape
        result = real(*args, **kwargs)
        return replace(result, payload={**result.payload, **planted})

    monkeypatch.setattr(redaction, "redact", leaky)
    provider = FakeProvider(handler=lambda _call: specialist_answer(ac, CRITIC), environment="test")
    honest = len(provider.calls)
    _critic(ac, provider)
    seeded = {**sentinels(ac.db, ac.g.ws), "columns": ("feature", "region", "target")}
    assert raw_row_findings(provider.calls[honest], **seeded) == []  # the unpatched body is clean
    HOLDOUT_SHAPES["debug"] = (os.environ["DCLAB_TYPESAFE_API_KEY"], "secret:value")
    for key, (value, finding) in HOLDOUT_SHAPES.items():
        planted.clear()
        planted[key] = value
        _critic(ac, provider)
        found = raw_row_findings(provider.calls[-1], **seeded)
        assert finding in found, (key, found)
        with pytest.raises(AssertionError, match="raw rows"):
            check_no_raw_rows(provider.calls[-1:], **seeded)
    probe = provider.calls[-1]
    assert raw_row_findings(replace(probe, instructions="", input_json=json.dumps({"x": CV_VALUE})),
                            **seeded) == []  # a CV value is not a holdout value
    for key, value in ALLOWED_SHAPES.items():
        planted.clear()
        planted[key] = value
        _critic(ac, provider)
        assert raw_row_findings(provider.calls[-1], **seeded) == [], key  # sizes pass, outcomes do not
    planted.clear()
    planted["model_card"] = {**ALLOWED_SHAPES["model_card"], "label_counts": CANARY_HOLDOUT_SUMMARY["label_counts"]}
    _critic(ac, provider)
    assert raw_row_findings(provider.calls[-1], **seeded) == ["holdout_count:425", "holdout_count:812"]
    assert any(f.startswith("secret:eyJ") for f in raw_row_findings(replace(probe, input_json=json.dumps(
        {"t": "Bearer eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.c2lnbmF0dXJl"}))))


# --- every model call has a ledger row -------------------------------------------------------------


def test_ledger_check_catches_model_calls_without_a_row(ac, monkeypatch):
    provider = FakeProvider(handler=answers(*[specialist_answer(ac, CRITIC)] * 3), environment="test")
    assert _critic(ac, provider).status == "completed"
    check_ledger(ac.db, provider.calls)
    # A caller that bypasses the gateway: a provider call with no ledger row.
    provider.complete(replace(provider.calls[0], input_digest=None, invocation_id=None))
    with pytest.raises(AssertionError, match="no ledger row"):
        check_ledger(ac.db, provider.calls)
    # A gateway regression that never writes the pending row.
    monkeypatch.setattr(ledger, "insert_pending", lambda *_a, **_k: SimpleNamespace(id=uuid4()))
    calls = len(provider.calls)
    _critic(ac, provider)
    assert len(provider.calls) == calls + 1
    with pytest.raises(AssertionError, match="no ledger row"):
        check_ledger(ac.db, provider.calls[calls:])


def test_ledger_check_catches_an_incomplete_row(ac, monkeypatch):
    monkeypatch.setattr(ledger, "finalize", lambda *_a, **_k: None)  # the outcome is never written
    provider = FakeProvider(handler=answers(specialist_answer(ac, CRITIC)), environment="test")
    _critic(ac, provider)
    with pytest.raises(AssertionError, match="status=pending"):
        check_ledger(ac.db, provider.calls)


# --- every agent run has a harness record ----------------------------------------------------------


def test_harness_check_catches_a_run_without_its_record(ac, monkeypatch):
    provider = FakeProvider(handler=answers(*[specialist_answer(ac, CRITIC)] * 2), environment="test")
    honest = _critic(ac, provider)
    check_harness_records(ac.db, [honest], provider.calls)
    with pytest.raises(AssertionError, match="without a provider call"):  # the run's calls are linked
        check_harness_records(ac.db, [honest], [])
    real = svc._Execution.record
    monkeypatch.setattr(svc._Execution, "record", lambda self, kind, payload, **kw: None if kind in (
        "run_finished", "run_failed", "llm_call_finished") else real(self, kind, payload, **kw))
    dropped = _critic(ac, provider)
    with pytest.raises(AssertionError, match="events do not end with the run outcome"):
        check_harness_records(ac.db, [dropped])
    with pytest.raises(AssertionError, match="unrecorded run"):  # a run that called without a row
        check_harness_records(ac.db, [dropped.model_copy(update={"run_id": None})])
    with pytest.raises(AssertionError, match="no agent_runs row"):
        check_harness_records(ac.db, [dropped.model_copy(update={"run_id": uuid4()})])


# --- adapters never send the harness seam ----------------------------------------------------------


def _seamed_call(**values) -> ProviderCall:
    base = dict(model="gpt-6-luna", instructions="i", input_json="{}", output_schema=AssistantStep,
                max_output_tokens=10, temperature=0.0, timeout_s=5.0, purpose="p", agent_key="a")
    return ProviderCall(**{**base, **values}, input_digest="d" * 64, invocation_id=uuid4())


def test_provider_adapters_never_send_the_ledger_digest_or_row_id(monkeypatch):
    seen = {}

    def parse(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(output_parsed=AssistantStep(kind="done"), usage=None, model="gpt-6-luna")

    call = _seamed_call()
    OpenAIProvider(client_factory=lambda **_kw: SimpleNamespace(responses=SimpleNamespace(parse=parse))).complete(call)
    assert set(seen) == {"model", "instructions", "input", "text_format", "max_output_tokens", "store", "temperature"}
    assert str(call.invocation_id) not in repr(seen) and call.input_digest not in repr(seen)
    sdk = pytest.importorskip("typesafe_sdk", reason="the agents extra (typesafe-sdk) is not installed")
    httpx2 = pytest.importorskip("httpx2")
    from app.agents.gateway.contract import SemanticAnswers
    from app.agents.gateway.providers.typesafe_jev import TypeSafeJevProvider

    sent = []

    def respond(request):
        sent.append(request.content.decode())
        return httpx2.Response(200, json={"model": "jev-1.13.0", "usage": {"input_tokens": 1, "output_tokens": 0},
                                          "answers": {"q0": {"type": "noul", "noul": 0.5}}})

    payload = {"purpose": "column.is_identifier", "user_text": [], "state": {"c0": {"dtype": "integer"}},
               "questions": [{"question_key": {"untrusted_text": "x"}, "primitive": "noul", "choices": [],
                              "subject": "c0"}]}
    jev_call = _seamed_call(model="jev-1.13.0", instructions=json.dumps({"question": "Q?", "criteria": None}),
                            input_json=json.dumps(payload), output_schema=SemanticAnswers, temperature=None)
    TypeSafeJevProvider(client_factory=lambda **kw: sdk.TypeSafeClient(
        **kw, transport=httpx2.MockTransport(respond))).complete(jev_call)
    assert sent and str(jev_call.invocation_id) not in sent[0] and jev_call.input_digest not in sent[0]
