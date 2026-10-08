"""P6.10-A: the shared agent tool catalog (ADR 0009 §6; ADR 0008 §2b, §6).

The catalog is the 15 read + 7 write tools MCP registers (``get_impact`` included,
``accept_proposal`` MCP-only); no tool is a forbidden operation by name or effect; write
tools are L1 ``lead.*`` proposals; capabilities follow the token route scopes; the
export is ``contracts/agent_tools.json`` and its digest is computed from code. Property
tests: in agent consumer mode a HUMAN principal gets no holdout key or scope from any
read tool (the same reads give the human holdout values on ``/v1``), and every
``ContextField`` rendering passes gateway redaction. ``dclab_mcp`` never imports ``app``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.agents.contracts import SYSTEM_SOURCE, WORKSPACE_TEXT_SOURCE, TranscriptItem
from app.agents.gateway import redaction
from app.agents.governance.decision_points import REGISTRY, registry_violations
from app.agents.tools.catalog import (
    FORBIDDEN_OPERATIONS,
    ToolContext,
    ToolError,
    catalog,
    catalog_digest,
    export_payload,
    export_text,
    visible,
)
from app.agents.tools.render import mcp_json, to_context_fields, to_mcp
from app.agents.tools.shaping import Code, Shaped, data_text, text, withhold_model_version
from app.db.models import MlJob, ModelVersion
from app.domain.service_tokens import TOKEN_ROUTE_SCOPES
from app.services import decision_record_service as drs
from app.services.auth_service import create_access_token
from app.services.ml_job_service import process_next_job
from app.services.project_service import create_project
from dclab_client import DCLabClient
from scripts import truth_drift
from test_decision_record_service import _bootstrap, g, setup  # noqa: F401  (fixtures)
from test_v1_resources_experiments import _work
from test_v1_service_tokens import _token, st  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[3]
BASE = "http://testserver"
ALL_SCOPES = ("read", "projects:write", "datasets:write", "experiments:write", "decisions:propose")
FILLED_HOLDOUT_LITERAL = re.compile(r"HOLDOUT_METRICS = \{[^}\s]")
HOLDOUT = re.compile(r"holdout|final_test", re.IGNORECASE)
CLASS_RANK = {"metadata": 0, "aggregates": 1}
SCOPE_RANK = {"none": 0, "cv": 1}


def _holdout_hits(value, path="$") -> list[str]:
    if isinstance(value, dict):
        hits = [f"{path}.{key}" for key in value if HOLDOUT.search(str(key))]
        hits += [f"{path}.{key}" for key in ("scope", "evaluation_scope") if HOLDOUT.search(str(value.get(key) or ""))]
        return hits + [hit for key, item in value.items() for hit in _holdout_hits(item, f"{path}.{key}")]
    if isinstance(value, (list, tuple)):
        return [hit for index, item in enumerate(value) for hit in _holdout_hits(item, f"{path}[{index}]")]
    return []


TENANT_TEXT_KEYS = {"name", "slug", "description", "intent", "rationale", "business_objective"}


def _untrusted_keys(value, key=None) -> set:
    if hasattr(value, "untrusted_text"):
        return {key}
    if isinstance(value, dict):
        return {hit for k, item in value.items() for hit in _untrusted_keys(item, k)}
    if isinstance(value, (list, tuple)):
        return {hit for item in value for hit in _untrusted_keys(item, key)}
    return set()


def _untrusted_items(value) -> list:
    if hasattr(value, "untrusted_text"):
        return [value]
    if isinstance(value, dict):
        return [hit for item in value.values() for hit in _untrusted_items(item)]
    if isinstance(value, (list, tuple)):
        return [hit for item in value for hit in _untrusted_items(item)]
    return []


def test_catalog_is_the_mcp_tool_set_with_get_impact():
    tools = catalog()
    reads = {name for name, item in tools.items() if item.effect == "read"}
    writes = {name for name, item in tools.items() if item.effect == "proposal"}
    assert len(tools) == 23 and len(reads) == 16 and len(writes) == 7 and "get_impact" in reads
    assert writes == {"create_problem_spec", "propose_problem_spec", "run_experiment", "branch_experiment",
                      "predict", "record_decision", "request_agent_review"}
    assert {item.name for item in visible("mcp")} == set(tools)
    # P6.6-A: the assistant surface is unchanged (no accept, no proposal listing, no review request).
    # P5.2-A: get_operating_points is MCP-only (the lead agent's released prompt lists its tools).
    assert {item.name for item in visible("assistant")} == set(tools) - {
        "accept_proposal", "list_proposals", "request_agent_review", "inspect_governance", "get_operating_points"}
    assert tools["accept_proposal"].surfaces == {"mcp"}  # a hand-off; the lead agent never gets it
    assert tools["list_proposals"].surfaces == {"mcp"} and "accept_proposal" not in {
        item.name for item in visible("studio_forms")}
    assert {item.name for item in visible("studio_forms")} == writes


def test_no_forbidden_operation_by_name_or_effect_and_writes_are_l1_proposals():
    assert set(FORBIDDEN_OPERATIONS) == {"read_rows", "read_holdout", "select_winner", "compute_metric",
                                         "build_split", "infer_entity_column", "load_model", "execute_code",
                                         "raw_sql", "move_ref", "promote_champion"}
    operations = set(json.loads((REPO / "contracts" / "openapi_operations.json").read_text(encoding="utf-8")))
    for name, item in catalog().items():
        assert not any(op in name for op in FORBIDDEN_OPERATIONS), name
        assert item.effect in ("read", "proposal")
        assert set(item.operations) <= operations, name
        assert not any("download" in op or HOLDOUT.search(op) for op in item.operations), name
        for service in item.services:
            leaf = service.rsplit(".", 1)[-1]
            assert not any(op in leaf for op in FORBIDDEN_OPERATIONS), service
            assert not re.search(r"holdout|winner|select|metric|split|load|sql|execute|promote", leaf), service
        if item.effect == "read":
            assert all(op.startswith("GET ") for op in item.operations) and item.decision_point_key is None
            assert item.fetch is not None and item.shaper is not None
        else:
            # A proposal never executes from an agent: no fetch, no shaper, refused by read().
            assert item.fetch is None and item.shaper is None
            with pytest.raises(ToolError) as refused:
                item.read(None, {})  # type: ignore[arg-type]
            assert refused.value.code == "not_a_read_tool"
            point = REGISTRY[item.decision_point_key]
            assert item.decision_point_key == f"lead.{name}" and point.cap <= 1 and point.effects == ("write_tool",)
    assert REGISTRY["lead.create_problem_spec"].cap == 1 and registry_violations() == []
    assert all(point.cap <= 1 for key, point in REGISTRY.items() if key.startswith("lead."))


def test_capabilities_follow_the_token_route_scopes():
    for name, item in catalog().items():
        needed = {TOKEN_ROUTE_SCOPES[tuple(op.split(" ", 1))] for op in item.operations}
        assert set(item.capability) == needed, name
        assert (item.capability == ("read",)) is (item.effect == "read"), name


def test_export_is_the_contract_and_the_digest_hashes_its_exact_text():
    rendered = export_text()
    assert rendered == truth_drift._json_dump(export_payload())
    contract = (REPO / "contracts" / "agent_tools.json").read_bytes()
    assert contract.decode("utf-8") == rendered
    assert catalog_digest() == hashlib.sha256(contract).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", catalog_digest())
    exported = {tool["name"]: tool for tool in export_payload()["tools"]}
    assert exported["record_decision"]["decision_point_cap"] == 1
    assert all("idempotency_key" not in tool["input_schema"]["properties"] for tool in exported.values())


_FORBIDDEN_IMPORTS = ("app", "sqlalchemy", "alembic", "psycopg", "psycopg2")


def _forbidden(module: str | None) -> bool:
    return bool(module) and any(module == root or module.startswith(root + ".") for root in _FORBIDDEN_IMPORTS)


def test_dclab_mcp_never_imports_app():
    files = list((REPO / "packages" / "dclab_mcp").rglob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(_forbidden(alias.name) for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert node.level > 0 or not _forbidden(node.module), path
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if called in ("import_module", "__import__"):
                    assert not _forbidden(str(node.args[0].value)), path


def test_write_validators_reject_holdout_and_bad_arguments():
    tools = catalog()
    pid, eid = str(uuid4()), str(uuid4())
    record = tools["record_decision"]
    base = {"project_id": pid, "rationale": "branch holds CV", "decision_type": "experiment_accepted",
            "subject_kind": "experiment", "subject_id": eid}
    assert record.parse({**base, "evidence_refs": [{"kind": "experiment", "id": eid, "scope": "cv_aggregate"}]})
    assert record.parse({"project_id": pid, "rationale": "x", "ref_moves": {"champion_model": eid}})
    for bad, code in (
        ({**base, "evidence_refs": [{"kind": "model_version", "id": eid, "scope": "final_holdout"}]},
         "holdout_not_allowed"),
        ({**base, "evidence_refs": [{"kind": "x", "id": eid, "evaluation_scope": "final_holdout"}]},
         "holdout_not_allowed"),
        ({**base, "facts": {"why": {"holdout_auc": 0.9}}}, "holdout_not_allowed"),
        ({**base, "evidence_refs": [{"kind": "experiment", "id": eid, "metric": "holdout_auc"}]},
         "holdout_not_allowed"),
        ({**base, "facts": {"basis": "final_holdout"}}, "holdout_not_allowed"),
        ({**base, "evidence_refs": [{"kind": "experiment", "id": "nope"}]}, "invalid_arguments"),
        ({"project_id": pid, "rationale": "x"}, "invalid_arguments"),
        ({"project_id": pid, "rationale": "x", "ref_moves": {"bogus": eid}}, "invalid_arguments"),
        ({**base, "project_id": "not-a-uuid"}, "invalid_arguments"),
    ):
        with pytest.raises(ToolError) as refused:
            record.parse(bad)
        assert refused.value.code == code, bad
    branch = tools["branch_experiment"]
    assert branch.parse({"experiment_id": eid, "intent": "x", "changes": [{"kind": "family_exclude", "family": "xgboost"}]})
    with pytest.raises(ToolError) as refused:
        branch.parse({"experiment_id": eid, "intent": "x", "changes": [{"kind": "dataset_swap"}]})
    assert refused.value.code == "invalid_change_set"
    with pytest.raises(ToolError) as refused:
        tools["create_problem_spec"].parse({"project_id": pid, "task_type": "regression", "business_objective": "x",
                                            "constraints": {"holdout_mae": 3}})
    assert refused.value.code == "holdout_not_allowed"
    for tool, args in (("compare_experiments", {"experiment_ids": [eid, eid]}),
                       ("list_decisions", {"project_id": pid, "effective_state": "maybe"})):
        with pytest.raises(ToolError) as refused:
            tools[tool].parse(args)
        assert refused.value.code == "invalid_arguments"


def test_context_rendering_tags_every_field_and_passes_the_gateway():
    dataset = uuid4()
    shaped = Shaped(
        {"thing": {"id": uuid4(), "status": Code("completed"), "scope_name": Code("final_holdout"),
                   "when": datetime(2026, 10, 5, tzinfo=UTC), "digest": "ab" * 32, "name": text("x" * 5000, 5000),
                   "column": data_text("tenure"), "score_text": data_text("CV 0.81", aggregate=True),
                   "holdout": 1, "Bad Key": 2,
                   "metrics": {"cv": {"roc_auc": 0.8}}}},
        data_class="aggregates", outcome_scope="cv", source_datasets=(dataset,), aggregates=("thing.metrics",))
    fields = {f.key: f for f in to_context_fields("get_thing", shaped)}
    assert set(fields) == {"get_thing.thing", "get_thing.thing.text", "get_thing.thing.data",
                           "get_thing.thing.agg", "get_thing.thing.metrics"}
    agg = fields["get_thing.thing.agg"]
    assert (agg.data_class, agg.outcome_scope, set(agg.value)) == ("aggregates", "cv", {"score_text"})
    system = fields["get_thing.thing"]
    assert system.sources == (SYSTEM_SOURCE,) and (system.data_class, system.outcome_scope) == ("metadata", "none")
    assert system.value["status"] == {"completed": True}
    assert system.value["when"] == int(datetime(2026, 10, 5, tzinfo=UTC).timestamp())
    assert {"scope_name", "digest", "holdout", "Bad Key", "name", "column", "score_text"}.isdisjoint(system.value)
    assert fields["get_thing.thing.text"].sources == (WORKSPACE_TEXT_SOURCE,)
    assert len(fields["get_thing.thing.text"].value["name"].untrusted_text) == 4000
    data = fields["get_thing.thing.data"]
    assert [s.dataset_id for s in data.sources] == [dataset] and data.data_class == "metadata"
    metrics = fields["get_thing.thing.metrics"]
    assert (metrics.data_class, metrics.outcome_scope) == ("aggregates", "cv") and metrics.value == {"cv": {"roc_auc": 0.8}}
    for item in fields.values():
        redaction._structural_check(item)
        redaction.render_value(item.value, untrusted=redaction._text_mode(item))
    assert mcp_json({"n": text("x" * 5000)})["n"]["truncated"] is True  # MCP keeps its wire form
    # No recorded source dataset: dataset text is omitted (fail closed; the gateway refuses it).
    assert to_context_fields("get_thing", Shaped({"c": data_text("tenure"), "n": 1})) == (
        to_context_fields("get_thing", Shaped({"n": 1})))


def _redacted(db, ws, fields, label, monkeypatch):
    monkeypatch.setattr(redaction._Labels, "exposure", lambda self, source: (label, "internal"))
    return redaction.redact(db, workspace_id=ws, fields=fields, transcript=(), user_text=(),
                            max_class="aggregates", max_scope="cv")


def _csv(tmp_path, n=240, name="rows.csv"):
    rng = np.random.default_rng(3)
    tenure = rng.integers(1, 72, n).astype(float)
    label = rng.binomial(1, 1 / (1 + np.exp(0.6 + 0.02 * tenure)))
    frame = pd.DataFrame({"tenure": tenure, "spend": rng.uniform(20, 120, n), "plan": rng.choice(["basic", "pro"], n),
                          "label": np.where(label == 1, "yes", "no")})
    path = tmp_path / name
    frame.to_csv(path, index=False)
    return path


def trained_project(client, db, st, tmp_path) -> SimpleNamespace:  # noqa: F811
    """A human's project with a trained root run (the champion), a branch, a batch
    prediction and an agent's decision proposal (shared with the tool corpus recorder)."""

    project = create_project(db, actor=st.admin, workspace_id=st.alpha.id, name="Tool corpus", slug="tool-corpus",
                             description="Churn project used for the agent tool corpus.")
    db.commit()
    human = DCLabClient(BASE, token=create_access_token(st.admin), workspace_id=st.alpha.id, http=client)
    dataset = human.datasets.upload(project.id, _csv(tmp_path))
    spec = human.projects.create_problem_spec(project.id, task_type="binary_classification",
                                              business_objective="reduce churn", target_column="label", status="locked")
    human.projects.move_ref(project.id, "problem_spec", target_id=spec.id, rationale="our spec",
                            evidence_refs=[{"kind": "problem_spec", "id": spec.id}], create=True)
    root = human.experiments.create(project_id=project.id, dataset_id=dataset.id, problem_spec_id=spec.id,
                                    target_column="label", intent="baseline")
    assert _work(db, root.id).status == "completed"
    branch = human.experiments.branch(root.id, changes=[{"kind": "family_exclude", "family": "xgboost"}],
                                      intent="no xgboost")
    assert _work(db, branch.id).status == "completed"
    root_mv = db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == root.id))
    scoring = tmp_path / "score.csv"
    pd.read_csv(_csv(tmp_path, n=30, name="new.csv")).drop(columns=["label"]).to_csv(scoring, index=False)
    rows = human.datasets.upload(project.id, scoring, purpose="scoring")
    prediction = human.predictions.create(model_version_id=root_mv, dataset_id=rows.id)
    db.expire_all()
    job = db.scalar(select(MlJob).where(MlJob.target_id == UUID(str(prediction.id))))
    assert process_next_job(db, job_id=job.id, claimed_by="tool-test").status == "completed"
    # P5.2-A: a person's operating point on the root run (its reason is workspace text).
    session = {"Authorization": f"Bearer {create_access_token(st.admin)}", "X-Workspace-Id": str(st.alpha.id)}
    points = client.get(f"/v1/experiments/{root.id}/operating-points", headers=session).json()
    assert points["status"] == "available", points["reason"]
    chosen = client.post(f"/v1/experiments/{root.id}/operating-point", headers={**session, "Idempotency-Key": "op-1"},
                         json={"threshold": points["pareto"][0]["threshold"], "reason": "staffing allows more flags"})
    assert chosen.status_code == 201, chosen.text
    agent = DCLabClient(BASE, token=_token(db, st, scopes=ALL_SCOPES), http=client)
    decision = agent.projects.create_decision(project.id, decision_type="experiment_accepted",
                                              subject_kind="experiment", subject_id=branch.id,
                                              rationale="branch holds CV quality",
                                              evidence_refs=[{"kind": "experiment", "id": branch.id}])
    headers = {"Authorization": f"Bearer {create_access_token(st.admin)}", "X-Workspace-Id": str(st.alpha.id)}
    return SimpleNamespace(project=str(project.id), dataset=str(dataset.id), root=str(root.id),
                           branch=str(branch.id), root_mv=str(root_mv), prediction=str(prediction.id),
                           decision=str(decision.id), headers=headers)


def _read_calls(t: SimpleNamespace) -> list[tuple[str, dict]]:
    return [
        ("inspect_project", {}), ("inspect_project", {"project_id": t.project}), ("inspect_dataset", {}),
        ("inspect_dataset", {"project_id": t.project}), ("inspect_dataset", {"dataset_id": t.dataset}),
        ("get_experiment", {"experiment_id": t.branch}), ("compare_experiments", {"experiment_ids": [t.root, t.branch]}),
        ("get_experiment_code", {"experiment_id": t.branch}), ("get_evidence", {"experiment_id": t.branch}),
        ("get_evidence", {"experiment_id": t.root}), ("get_findings", {"experiment_id": t.branch}),
        ("list_decisions", {"project_id": t.project}), ("get_model", {"model_version_id": t.root_mv}),
        ("get_model_card", {"model_version_id": t.root_mv}), ("get_prediction", {"prediction_id": t.prediction}),
        ("get_impact", {"kind": "experiment", "node_id": t.root}), ("accept_proposal", {"proposal_id": t.decision}),
        ("list_proposals", {"project_id": t.project}), ("inspect_governance", {}),
        ("get_operating_points", {"experiment_id": t.root}),
    ]


def _numbers(value, out=None) -> list[float]:
    out = [] if out is None else out
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            _numbers(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _numbers(item, out)
    elif hasattr(value, "untrusted_text"):
        _numbers(value.untrusted_text, out)
    elif isinstance(value, str):
        out.extend(float(item) for item in re.findall(r"-?\d+\.\d+", value))
    return out


def _holdout_values(human: dict) -> set[float]:
    """The human's final-holdout numbers that no CV / other field of the same responses
    shows (a value-based oracle: none of them may reach an agent)."""

    from app.agents.tools.shaping import strip_holdout

    found: list[float] = []
    for body in human.values():
        for section in ("metrics", "common", "experiments"):
            _numbers(_holdout_sections(body.get(section) if isinstance(body, dict) else None), found)
        card = body.get("final_evaluation") if isinstance(body, dict) else None
        if card:
            _numbers({"metrics": card.get("metrics"), "value": card.get("value")}, found)
        script = (body.get("script") or {}).get("source", "") if isinstance(body, dict) else ""
        literal = re.search(r"HOLDOUT_METRICS = \{[^}]*\}", script)
        if literal:
            _numbers(literal.group(0), found)
    others: list[float] = []
    for body in human.values():
        visible = strip_holdout(body)
        if isinstance(visible, dict):
            visible.pop("final_evaluation", None)
            if "script" in visible:
                visible["script"] = re.sub(r"HOLDOUT_METRICS = \{[^}]*\}", "", str(visible["script"]))
                visible.pop("notebook", None)
        _numbers(visible, others)
    # Small-denominator fractions (accuracy, precision on a small holdout) can coincide with
    # legitimate CV numbers by chance; the distinctive values (AUC, log loss, ...) cannot.
    return {value for value in found if value not in set(others)
            and abs(float(Fraction(value).limit_denominator(100)) - value) > 1e-12}


def _holdout_sections(value):
    if isinstance(value, dict):
        return [item if HOLDOUT.search(str(key)) else _holdout_sections(item) for key, item in value.items()]
    if isinstance(value, list):
        return [_holdout_sections(item) for item in value]
    return None


def _assert_no_holdout_values(values: set[float], tool, payload, fields) -> None:
    seen = set(_numbers(payload)) | set(_numbers([item.value for item in fields]))
    leaked = {value for value in values if value in seen}
    assert not leaked, (tool, leaked)


def _check_consumer_result(db, ws, tool, definition, shaped, monkeypatch) -> dict:
    payload, _text = to_mcp(shaped)
    assert not _holdout_hits(payload) and not _holdout_hits(mcp_json(shaped.payload)), (tool, _holdout_hits(payload))
    assert CLASS_RANK[shaped.data_class] <= CLASS_RANK[definition.data_class], tool
    assert SCOPE_RANK[shaped.outcome_scope] <= SCOPE_RANK[definition.outcome_scope], tool
    fields = to_context_fields(tool, shaped)
    for item in fields:
        assert not HOLDOUT.search(item.key) and item.outcome_scope in ("none", "cv")
        redaction._structural_check(item)
        redaction.render_value(item.value, untrusted=redaction._text_mode(item))
        assert all(set(u.model_dump()) == {"untrusted_text"} for u in _untrusted_items(item.value))
        assert all(len(u.untrusted_text) <= 4000 for u in _untrusted_items(item.value))
    allowed = _redacted(db, ws, fields, "allow", monkeypatch)
    # Target labels are sample values (A2): dropped unless the policy allows sample values.
    assert allowed.summary["fields_kept"] == sum(
        1 for item in fields if item.sources and item.data_class != "sample_values"), tool
    denied = _redacted(db, ws, fields, "deny", monkeypatch)
    label_free = [item for item in fields if item.sources and all(s.kind in ("system", "workspace_text")
                                                                  for s in item.sources)]
    assert denied.summary["fields_kept"] == len(label_free), tool
    # Label-free fields never carry data-derived numbers: system fields are metadata and
    # workspace text is only what a tenant wrote; aggregates need a dataset label.
    assert all(item.data_class == "metadata" for item in label_free), tool
    workspace_keys = {key for item in label_free if item.sources == (WORKSPACE_TEXT_SOURCE,)
                      for key in _untrusted_keys(item.value)}
    assert workspace_keys <= TENANT_TEXT_KEYS, (tool, workspace_keys)
    metadata_only = _redacted(db, ws, fields, "metadata_only", monkeypatch)
    assert metadata_only.summary["dropped"]["data_class"] == sum(
        1 for item in fields if item.data_class in ("aggregates", "sample_values") and item.sources), tool
    transcript = (TranscriptItem(kind="tool_result", tool_name=tool, fields=fields),)
    redaction.redact(db, workspace_id=ws, fields=(), transcript=transcript, user_text=(),
                     max_class="aggregates", max_scope="cv")
    return payload


def test_consumer_mode_is_holdout_free_for_a_human_principal(client, db_session, st, tmp_path, monkeypatch):  # noqa: F811
    db = db_session
    t = trained_project(client, db, st, tmp_path)
    # The human reads holdout values on /v1; the agent consumer (same human) never does.
    human = {path: client.get(path, headers=t.headers).json() for path in (
        f"/v1/experiments/{t.branch}", f"/v1/experiments/compare?ids={t.root},{t.branch}",
        f"/v1/model-versions/{t.root_mv}", f"/v1/model-versions/{t.root_mv}/card",
        f"/v1/projects/{t.project}/decisions", f"/v1/experiments/{t.branch}/code")}
    assert human[f"/v1/experiments/{t.branch}"]["metrics"]["holdout"]
    assert human[f"/v1/experiments/compare?ids={t.root},{t.branch}"]["common"]["holdout"]
    assert human[f"/v1/model-versions/{t.root_mv}"]["metrics"]["holdout"]
    assert human[f"/v1/model-versions/{t.root_mv}/card"]["final_evaluation"]["status"] == "reported"
    assert _holdout_hits(human[f"/v1/projects/{t.project}/decisions"])
    assert FILLED_HOLDOUT_LITERAL.search(human[f"/v1/experiments/{t.branch}/code"]["script"]["source"])

    # A1 review note 1: value-based too: the human's holdout numbers never reach an agent.
    holdout_values = _holdout_values(human)
    assert holdout_values, "the fixture must expose distinctive holdout values to the human"
    # Follow-up 1: a service token's REST decision responses are holdout-free too (keys, scopes, values).
    token = {"Authorization": f"Bearer {_token(db, st, scopes=ALL_SCOPES)}"}
    page = client.get(f"/v1/projects/{t.project}/decisions", headers=token).json()
    assert page["items"] and len(page["items"]) == len(human[f"/v1/projects/{t.project}/decisions"]["items"])
    for body in (page, *(client.get(f"/v1/decisions/{item['id']}", headers=token).json() for item in page["items"])):
        assert not _holdout_hits(body), _holdout_hits(body)
        _assert_no_holdout_values(holdout_values, "rest_decisions", body, ())

    ctx = ToolContext(db=db, actor=st.admin, workspace_id=st.alpha.id)
    calls = _read_calls(t)
    assert {tool for tool, _ in calls} == {name for name, item in catalog().items() if item.effect == "read"}
    for tool, args in calls:
        definition = catalog()[tool]
        shaped = definition.read(ctx, args)
        payload = _check_consumer_result(db, st.alpha.id, tool, definition, shaped, monkeypatch)
        _assert_no_holdout_values(holdout_values, tool, payload, to_context_fields(tool, shaped))
        if tool == "get_experiment_code":
            assert not FILLED_HOLDOUT_LITERAL.search(payload["source"]["untrusted_text"])
        if tool == "get_model_card":
            assert payload["model_card"]["final_evaluation"]["status"] == "withheld"
            assert "## Final evaluation" not in payload["model_card"]["markdown"]["untrusted_text"]
    with pytest.raises(ToolError) as refused:
        catalog()["get_prediction"].read(ctx, {"prediction_id": str(uuid4())})
    assert refused.value.code == "not_found"


def test_seeded_holdout_evidence_never_reaches_the_agent(db_session, g, monkeypatch):  # noqa: F811
    db = db_session
    champion = _bootstrap(db, g).record  # a human champion promotion citing the final holdout
    page = drs.list_decisions(db, actor=g.actor, workspace_id=g.ws, project_id=g.project.id)
    assert _holdout_hits([r.model_dump(mode="json") for r in page.items])
    ctx = ToolContext(db=db, actor=g.actor, workspace_id=g.ws)
    for tool, args in (("list_decisions", {"project_id": str(g.project.id)}),
                       ("accept_proposal", {"proposal_id": str(champion.id)}),
                       ("inspect_project", {"project_id": str(g.project.id)})):
        definition = catalog()[tool]
        _check_consumer_result(db, g.ws, tool, definition, definition.read(ctx, args), monkeypatch)


def test_in_process_reads_authorize_like_v1(db_session, g, setup):  # noqa: F811
    db = db_session
    outsider = ToolContext(db=db, actor=setup["beta_admin"], workspace_id=g.ws)
    with pytest.raises(ToolError) as refused:
        catalog()["inspect_project"].read(outsider, {"project_id": str(g.project.id)})
    assert refused.value.code == "forbidden"
    foreign = ToolContext(db=db, actor=setup["beta_admin"], workspace_id=setup["beta"].id)
    for tool, args in (("inspect_project", {"project_id": str(g.project.id)}),
                       ("get_experiment", {"experiment_id": str(g.exp[0])}),
                       ("get_model", {"model_version_id": str(g.mv[0])})):
        with pytest.raises(ToolError) as refused:
            catalog()[tool].read(foreign, args)
        assert refused.value.code == "not_found", tool


def test_unexpected_read_failures_are_typed(db_session, g, monkeypatch):  # noqa: F811
    from dataclasses import replace

    def broken(reads, args):
        raise RuntimeError("secret internal detail")

    definition = replace(catalog()["get_experiment"], fetch=broken)
    with pytest.raises(ToolError) as refused:
        definition.read(ToolContext(db=db_session, actor=g.actor, workspace_id=g.ws), {"experiment_id": str(g.exp[0])})
    assert refused.value.code == "internal_error" and "secret" not in refused.value.message


def test_token_model_version_view_has_no_champion_exception():
    from app.domain.experiment_resources import ExperimentWinner, ModelVersionLineage, ModelVersionResourceRead

    body = ModelVersionResourceRead(
        id=uuid4(), workspace_id=uuid4(), version="v1", created_at=datetime.now(UTC), content_digest="0" * 64,
        lineage=ModelVersionLineage(experiment_id=uuid4(), candidate_id=uuid4()), is_champion=True,
        metrics=ExperimentWinner(cv={"roc_auc": 0.8}, holdout={"roc_auc": 0.7}),
        holdout_report_only={"roc_auc": 0.7},
    )
    withheld = withhold_model_version(body)
    assert withheld.holdout_report_only is None and withheld.metrics.holdout == {}
    assert withheld.metrics.cv == {"roc_auc": 0.8}
