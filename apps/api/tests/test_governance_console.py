"""P6.11-A: the governance console API (ADR 0009 §3, §4, §5.5; ADR 0008 §3, §4).

``GET /v1/governance`` (effective policy, levels with R3 evidence links, spend, incidents, changes), policy
proposals that apply only when an owner/admin accepts them (caps enforced), kill switches that stop the next
gateway call, replay of recorded agent runs, tenant isolation, the capability matrix (viewer / engineer /
approver / platform staff / service token), idempotent POSTs and the MCP read tool. Fake providers only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.agents.governance import incidents as inc
from app.agents.governance.decision_points import REGISTRY
from app.agents.tools.catalog import ToolContext, ToolError, catalog
from app.agents.tools.render import to_mcp
from app.agents.governance.policy import _level_head
from app.agents.semantic.releases import sync_jev_releases
from app.db.models import AgentRun, AiPolicy, AiSwitch, UserRole, Workspace, WorkspaceMembership
from sqlalchemy import update
from app.services import r3_evaluation_service as r3
from app.services.auth_service import create_user
from app.services.service_token_service import create_service_token
from test_agent_harness import hz  # noqa: F401  (fixture)
from test_agent_persistence import _insert, agent_run
from test_ai_gateway import gw  # noqa: F401  (fixture)
from test_ai_governance import _policy
from test_decision_record_service import g, setup  # noqa: F401  (fixtures)
from test_v1_contract_conventions import _assert_envelope, _headers

PASSWORD = "test-password"


@pytest.fixture
def gc(client, gw):  # noqa: F811
    db = gw.db

    def user(role, ws, tag):
        return create_user(db, email=f"{tag}-{uuid4().hex[:6]}@gov.test", password=PASSWORD, role=role,
                           workspace_id=ws)

    solo = Workspace(slug=f"solo-{uuid4().hex[:8]}", name="Solo")
    db.add(solo)
    db.flush()
    ns = SimpleNamespace(client=client, gw=gw, db=db, ws=gw.ws_a, ws_b=gw.ws_b, solo_ws=solo.id)
    ns.owner, ns.admin = user(UserRole.WORKSPACE_OWNER, ns.ws, "owner"), user(UserRole.WORKSPACE_ADMIN, ns.ws, "admin")
    ns.eng, ns.viewer = user(UserRole.ML_ENGINEER, ns.ws, "eng"), user(UserRole.VIEWER, ns.ws, "viewer")
    ns.owner_b = user(UserRole.WORKSPACE_OWNER, ns.ws_b, "ownerb")
    ns.solo = user(UserRole.WORKSPACE_OWNER, ns.solo_ws, "solo")
    ns.staff = user(UserRole.DCLAB_ADMIN, None, "staff")
    db.commit()

    def h(who, ws=None, key=False):
        extra = {"Idempotency-Key": key if isinstance(key, str) else f"k-{uuid4().hex}"} if key else {}
        return _headers(who, ws or ns.ws, **extra)

    ns.h = h
    ns.get = lambda who, ws=None: client.get("/v1/governance", headers=h(who, ws))
    ns.post = lambda path, body, who, ws=None, key=True: client.post(path, json=body, headers=h(who, ws, key))
    ns.propose = lambda who, policy=None, ws=None, key=True: ns.post(
        "/v1/governance/policy", {"policy": policy or _policy(budgets__workspace_month_micros=5_000_000),
                                  "rationale": "lower the monthly cap"}, who, ws, key)
    ns.accept = lambda row, who, ws=None, key=True, ack=False, digest=None: ns.post(
        f"/v1/governance/policy/{row['id']}/accept",
        {"policy_digest": digest or row["policy_digest"], "acknowledge_consent_change": ack}, who, ws, key)
    ns.switch = lambda who, state, key_name="all_ai", ws=None, key=True: ns.post(
        "/v1/governance/switches", {"switch_key": key_name, "state": state, "reason": "test <b>x</b>"}, who, ws, key)
    return ns


def _effective_cap(body: dict) -> int:
    return body["policy"]["document"]["budgets"]["workspace_month_micros"]


# --- GET /v1/governance --------------------------------------------------------------------------


def test_console_shape_and_capability_matrix(gc):
    body = gc.get(gc.owner)
    assert body.status_code == 200, body.text
    body = body.json()
    assert body["viewer"] == {"can_approve": True, "can_propose": True, "can_switch_off": True}
    assert len(body["levels"]) == len(REGISTRY) and {lv["key"] for lv in body["levels"]} == set(REGISTRY)
    assert {m["role"] for m in body["model_allowlist"]} == {"lead", "specialist", "legacy_decision", "verifier", "jev"}
    assert body["data_classes"]["max_class"] == "aggregates" and body["data_classes"]["sample_values_per_column"] == 0
    assert body["policy"]["workspace_version"] is None and len(body["policy"]["digest"]) == 64
    assert body["switches"]["platform_ai_blocking"] == "setting:AI_ENABLED"  # AI_ENABLED defaults to false
    assert body["spend"]["workspace"][0]["limit_micros"] == 25_000_000 and body["open_incidents"] == []
    assert gc.get(gc.eng).json()["viewer"] == {"can_approve": False, "can_propose": True, "can_switch_off": False}
    _assert_envelope(gc.get(gc.viewer), 403)  # a viewer-only member has no console
    staff = gc.get(gc.staff)  # platform staff read under the existing platform rules; never an approver
    assert staff.status_code == 200 and staff.json()["viewer"] == {
        "can_approve": False, "can_propose": False, "can_switch_off": True}


def test_cross_tenant_reads_and_commands_are_absent(gc):
    assert gc.get(gc.owner_b, gc.ws).status_code in (403, 404)  # not a member (workspace dependency: 403) of this workspace
    proposal = gc.propose(gc.eng).json()
    cross = gc.accept(proposal, gc.owner_b, gc.ws_b)
    _assert_envelope(cross, 404, "not_found")
    assert gc.propose(gc.owner_b, ws=gc.ws).status_code in (403, 404)
    assert gc.switch(gc.owner_b, "off", ws=gc.ws).status_code in (403, 404)
    inc.open_incident(gc.db, workspace_id=gc.ws_b, kind="manual", subject_kind="workspace", subject_key="b")
    inc.open_incident(gc.db, workspace_id=None, kind="manual", subject_kind="workspace", subject_key="p", platform=True)
    gc.db.commit()
    assert gc.get(gc.owner).json()["open_incidents"] == []  # neither another tenant's nor a platform incident
    assert len(gc.get(gc.owner_b, gc.ws_b).json()["open_incidents"]) == 1


def test_levels_link_stored_r3_runs_by_id_never_the_body_or_tenant_evidence(gc):
    corpus = r3.benchmark_corpus("quick", tasks=["sk-iris"], seed=42)
    report = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=gc.db, actor=gc.staff)
    r3.record_first_levels(gc.db, report, admin=gc.staff)
    gc.db.commit()
    response = gc.get(gc.owner)
    levels = response.json()["levels"]
    assert {lv["r3_evidence"]["run_id"] for lv in levels} == {report["run_id"]}
    evidence = levels[0]["r3_evidence"]
    assert evidence["content_digest"] == report["digest"] and evidence["run_digest"] == report["run_digest"]
    assert evidence["live"] is False and evidence["pair_release"] == report["pair"]["release"]
    assert set(evidence) == {"run_id", "content_digest", "run_digest", "live", "digest_verified", "verdict_current",
                             "pair_release",
                             "model_id", "cases", "recorded_at", "current_platform_level", "promotion_allowed",
                             "demotion_allowed"}
    assert evidence["digest_verified"] is True and evidence["promotion_allowed"] == {}  # offline run: no verdicts shown
    for forbidden in ("workspace_evidence", "pseudonym", "operator", "ablation", "holm_adjusted", "sources"):
        assert forbidden not in response.text
    assert str(gc.staff.id) not in response.text
    # an operator-named live candidate never reaches a tenant; a forged stored run shows no verdicts
    forged = {**report, "run_id": str(uuid4()), "candidate": f"live:jev:admin:{gc.staff.id}", "live": True,
              "points": {**report["points"], "column.semantic_role": {**report["points"]["column.semantic_role"],
                                                                      "promotion": {"L1": {"allowed": True}}}}}
    r3.store_run(gc.db, forged, admin=gc.staff)
    gc.db.commit()
    assert str(gc.staff.id) not in gc.get(gc.owner).text


# --- policy proposals ----------------------------------------------------------------------------


def test_policy_applies_only_on_acceptance_by_another_approver(gc):
    before = _effective_cap(gc.get(gc.owner).json())
    proposed = gc.propose(gc.eng)  # an ML engineer may propose, never decide
    assert proposed.status_code == 200, proposed.text
    row = proposed.json()
    assert (row["state"], row["open"], row["proposed_by"]) == ("proposed", True, f"user:{gc.eng.id}")
    assert _effective_cap(gc.get(gc.owner).json()) == before  # nothing applies yet
    _assert_envelope(gc.accept(row, gc.eng), 403)  # not an approver
    _assert_envelope(gc.accept(row, gc.staff), 403)  # platform staff never approve a customer's policy
    admin_row = gc.propose(gc.admin).json()
    _assert_envelope(gc.accept(admin_row, gc.admin), 403, "self_approval_not_allowed")  # proposer, others exist
    key = f"accept-{uuid4().hex}"
    done = gc.accept(row, gc.owner, key=key)
    assert done.status_code == 200, done.text
    assert (done.json()["state"], done.json()["self_approved"]) == ("accepted", False)
    after = gc.get(gc.owner).json()
    assert _effective_cap(after) == 5_000_000 and after["policy"]["workspace_version"] is not None
    replay = gc.accept(row, gc.owner, key=key)  # idempotent: same row, no second version
    assert replay.headers["Idempotent-Replayed"] == "true" and replay.json()["id"] == done.json()["id"]
    _assert_envelope(gc.accept(row, gc.owner), 409)  # decided already (new key)
    _assert_envelope(gc.accept(admin_row, gc.owner), 409, "policy_conflict")  # its base head moved
    assert [c["state"] for c in after["policy_changes"]][:1] == ["accepted"]


def test_a_sole_approver_may_accept_their_own_proposal_flagged(gc):
    proposed = gc.propose(gc.solo, ws=gc.solo_ws).json()
    done = gc.accept(proposed, gc.solo, gc.solo_ws)
    assert done.status_code == 200 and done.json()["self_approved"] is True


def test_caps_validation_and_command_conventions(gc):
    loose = gc.propose(gc.owner, _policy(budgets__workspace_month_micros=99_000_000))
    error = _assert_envelope(loose, 422, "policy_cap_violation")
    assert "budgets.workspace_month_micros" in error["details"]["violations"]
    _assert_envelope(gc.propose(gc.owner, _policy(data__max_class="sample_values")), 422, "policy_cap_violation")
    _assert_envelope(gc.propose(gc.owner, {"schema_version": 1}), 422, "policy_invalid")
    _assert_envelope(gc.propose(gc.viewer), 403)
    assert gc.db.scalar(select(func.count()).select_from(AiPolicy).where(AiPolicy.workspace_id == gc.ws)) == 0
    _assert_envelope(gc.propose(gc.owner, key=False), 400, "idempotency_key_required")
    key = f"p-{uuid4().hex}"
    first = gc.propose(gc.owner, key=key)
    again = gc.propose(gc.owner, key=key)
    assert first.json()["id"] == again.json()["id"] and again.headers["Idempotent-Replayed"] == "true"
    other = gc.propose(gc.owner, _policy(budgets__workspace_month_micros=4_000_000), key=key)
    _assert_envelope(other, 409, "idempotency_key_conflict")
    assert gc.db.scalar(select(func.count()).select_from(AiPolicy).where(AiPolicy.workspace_id == gc.ws)) == 1


# --- kill switches ---------------------------------------------------------------------------------


def test_switch_flip_stops_the_next_gateway_call_and_is_audited(gc):
    gw = gc.gw
    assert gw.service().complete(gw.db, gw.request()).ok
    flipped = gc.switch(gc.owner, "off")
    assert flipped.status_code == 200, flipped.text
    assert (flipped.json()["state"], flipped.json()["switch_key"]) == ("off", "all_ai")
    refused = gw.service().complete(gw.db, gw.request())  # the NEXT call, same process, no restart
    assert (refused.ok, refused.refusal.code, refused.refusal.scope) == (False, "kill_switch", "workspace:all_ai")
    gw.db.expire_all()
    row = gw.db.get(AiSwitch, UUID(flipped.json()["id"]))
    assert (row.changed_by_user_id, row.workspace_id, row.reason) == (gc.owner.id, gc.ws, "test <b>x</b>")  # audit row
    body = gc.get(gc.owner).json()
    assert body["switches"]["workspace"][0]["state"] == "off" and body["recent_changes"][0]["kind"] == "switch"
    assert body["recent_changes"][0]["rationale"] == "test <b>x</b>"  # plain text, never interpreted
    back = gc.switch(gc.owner, "on")
    assert back.status_code == 200 and back.json()["state"] == "on"
    assert gw.service().complete(gw.db, gw.request()).ok  # and back on for the next call


def test_switch_capabilities_platform_staff_and_idempotency(gc):
    for who in (gc.eng, gc.viewer):
        _assert_envelope(gc.switch(who, "off"), 403)
    _assert_envelope(gc.post("/v1/governance/switches", {"switch_key": "global_ai", "state": "off", "reason": "x"},
                             gc.owner), 422)  # platform keys are not an HTTP surface
    _assert_envelope(gc.switch(gc.owner, "off", key=False), 400, "idempotency_key_required")
    staff_off = gc.switch(gc.staff, "off", "provider:openai")  # platform staff may switch a workspace key off
    assert staff_off.status_code == 200
    _assert_envelope(gc.switch(gc.staff, "on", "provider:openai"), 403)  # never back on
    seen = gc.get(gc.owner)
    assert seen.json()["switches"]["workspace"][0]["changed_by"] == "platform_staff"  # no operator identity
    assert str(gc.staff.id) not in seen.text
    key = f"s-{uuid4().hex}"
    first, again = gc.switch(gc.owner, "off", "agent:x", key=key), gc.switch(gc.owner, "off", "agent:x", key=key)
    assert first.json()["id"] == again.json()["id"] and again.headers["Idempotent-Replayed"] == "true"
    assert gc.db.scalar(select(func.count()).select_from(AiSwitch).where(
        AiSwitch.workspace_id == gc.ws, AiSwitch.switch_key == "agent:x")) == 1


def test_reenable_is_refused_while_an_incident_holds_the_switch(gc):
    incident = inc.open_incident(gc.db, workspace_id=gc.ws, kind="data_exposure", subject_kind="workspace",
                                 subject_key="alpha", evidence={"count": 2, "note": "ids only"})
    gc.db.commit()
    held = gc.get(gc.owner).json()
    assert [(i["kind"], i["action"], i["evidence"]["count"]) for i in held["open_incidents"]] == [
        ("data_exposure", "switch_off", 2)]
    assert held["switches"]["workspace"][0]["held_by_incident"] is True
    _assert_envelope(gc.switch(gc.owner, "on"), 409, "switch_held_by_incident")
    inc.resolve_incident(gc.db, incident_id=incident.id, actor=gc.owner, resolution="reviewed")
    gc.db.commit()
    assert gc.switch(gc.owner, "on").status_code == 200


def test_service_tokens_read_but_never_change_governance(gc):
    _row, raw = create_service_token(gc.db, creator=gc.eng, workspace_id=gc.ws, name="t", scopes=["read"],
                                     expires_in_days=30, current_password=PASSWORD)
    gc.db.commit()
    bearer = {"Authorization": f"Bearer {raw}", "Idempotency-Key": f"t-{uuid4().hex}"}
    assert gc.client.get("/v1/governance", headers=bearer).status_code == 200  # the creator's authority
    body = {"switch_key": "all_ai", "state": "off", "reason": "x"}
    for call in (gc.client.post("/v1/governance/switches", json=body, headers=bearer),
                 gc.client.post("/v1/governance/policy", json={"policy": _policy(), "rationale": "x"}, headers=bearer),
                 gc.client.post(f"/v1/governance/policy/{uuid4()}/accept", json={"policy_digest": "a" * 64},
                                headers=bearer),
                 gc.client.post(f"/v1/agent-runs/{uuid4()}/replay", json={}, headers=bearer)):
        assert call.status_code == 403, call.text
    assert gc.db.scalar(select(func.count()).select_from(AiSwitch).where(AiSwitch.workspace_id == gc.ws)) == 0


def test_no_http_route_raises_a_platform_level():
    import app.api.v1_governance as module
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes if hasattr(route, "path")}
    assert not {p for p in paths if p.startswith("/v1/governance") and "level" in p}
    source = open(module.__file__, encoding="utf-8").read()
    assert not any(name in source for name in ("propose_level", "accept_level", "set_level", "propose_promotion"))


# --- replay ------------------------------------------------------------------------------------------


def test_replay_matches_the_record_and_refuses_lead_runs(hz, client, monkeypatch):  # noqa: F811
    db, g = hz.db, hz.g
    script = (("llm",), ("tool", "get_experiment", {"experiment_id": str(g.exp[0])}), ("llm",))
    result = hz.service(script).run(db, hz.spec(kind="specialist"))
    state = {"script": script}
    from app.agents.harness.replay import replay as real

    monkeypatch.setattr("app.api.v1_governance.replay",
                        lambda db_, **kw: real(db_, service=hz.service(state["script"]), **kw))
    def rp(run, who=g.actor, key=None):
        return client.post(f"/v1/agent-runs/{run}/replay", json={},
                           headers=_headers(who, g.ws, **{"Idempotency-Key": key or f"r-{uuid4().hex}"}))

    headers = _headers(g.actor, g.ws)
    assert client.get(f"/v1/agent-runs/{result.run_id}/replay", headers=headers).status_code == 405  # POST only
    _assert_envelope(client.post(f"/v1/agent-runs/{result.run_id}/replay", json={}, headers=headers), 400,
                     "idempotency_key_required")
    first_key = f"r-{uuid4().hex}"
    got = rp(result.run_id, key=first_key)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["equal"] is True and body["mismatches"] == [] and body["incident_id"] is None
    assert [t["tool"] for t in body["tool_sequence"]] == ["get_experiment"]
    from test_agent_harness import _events

    assert body["output_digest"] == [e for e in _events(db, result.run_id) if e.type == "run_finished"][0].payload[
        "output_digest"]
    state["script"] = (("llm",), ("tool", "get_experiment", {"experiment_id": str(g.exp[1])}), ("llm",))
    again = rp(result.run_id, key=first_key)  # the stored result of the first key, no second replay event
    assert again.headers["Idempotent-Replayed"] == "true" and again.json() == body
    diff = rp(result.run_id).json()
    assert diff["equal"] is False and "tool_sequence" in diff["mismatches"] and diff["incident_id"]
    lead = agent_run(db, SimpleNamespace(ws_a=g.ws, project_a=g.project.id), kind="lead", agent_key="lead",
                     runtime="lead_loop", purpose="assistant.turn", created_by_user_id=g.actor.id,
                     prompt_release_id=hz.release)
    db.commit()
    _assert_envelope(rp(lead), 409, "not_replayable")
    stranger = create_user(db, email=f"x-{uuid4().hex[:6]}@gov.test", password=PASSWORD, role=UserRole.ML_ENGINEER,
                           workspace_id=g.ws)
    db.commit()
    _assert_envelope(rp(lead, stranger), 404, "not_found")
    _assert_envelope(rp(uuid4()), 404, "not_found")
    viewer = create_user(db, email=f"v-{uuid4().hex[:6]}@gov.test", password=PASSWORD, role=UserRole.VIEWER,
                         workspace_id=g.ws)
    db.commit()
    _assert_envelope(rp(result.run_id, viewer), 403)
    assert db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.runtime == "lead_loop")) == 1


# --- MCP read tool -----------------------------------------------------------------------------------


def test_inspect_governance_is_read_only_free_text_free_and_workspace_scoped(gc):
    gc.propose(gc.eng)  # a rationale the tool must not echo
    gc.switch(gc.owner, "off")
    tool = catalog()["inspect_governance"]
    assert tool.effect == "read" and tool.surfaces == {"mcp"} and tool.capability == ("read",)
    shaped = tool.read(ToolContext(gc.db, gc.owner, gc.ws), {})
    payload, text = to_mcp(shaped)
    view = payload["governance"]
    assert view["workspace_id"] == str(gc.ws) and view["open_policy_proposals"] == 1
    assert [s["state"] for s in view["switches"]] == ["off"] and len(view["levels"]) == len(REGISTRY)
    for leaked in ("lower the monthly cap", "test <b>x</b>", "workspace_evidence", str(gc.eng.id)):
        assert leaked not in text
    with pytest.raises(ToolError) as refused:
        tool.read(ToolContext(gc.db, gc.viewer, gc.ws), {})
    assert refused.value.status == 403
    with pytest.raises(ToolError) as other:
        tool.read(ToolContext(gc.db, gc.owner_b, gc.ws), {})  # another tenant's workspace
    assert other.value.status in (403, 404)
    json.dumps(payload)


# --- review fixes: proposals, tokens, idempotent re-check, limits ------------------------------------------------


def test_open_proposals_show_document_and_diffs_to_approvers_and_proposer_only_and_consent_needs_acknowledgement(gc):
    row = gc.propose(gc.eng, _policy(budgets__workspace_month_micros=5_000_000, data__share_r3_aggregates=True)).json()
    for who in (gc.owner, gc.eng):  # approvers and the proposer
        change = gc.get(who).json()["policy_changes"][0]
        assert change["document"]["budgets"]["workspace_month_micros"] == 5_000_000 and change["consent_change"] is True
        paths = {d["path"] for d in change["diff_vs_effective"]}
        assert {"budgets.workspace_month_micros", "data.share_r3_aggregates"} <= paths
        assert {"path": "data.share_r3_aggregates", "before": False, "after": True} in change["diff_vs_head"]
    other = create_user(gc.db, email=f"eng2-{uuid4().hex[:6]}@gov.test", password=PASSWORD,
                        role=UserRole.ML_ENGINEER, workspace_id=gc.ws)
    gc.db.commit()
    assert gc.get(other).json()["policy_changes"][0]["document"] is None  # neither approver nor proposer
    _row, raw = create_service_token(gc.db, creator=gc.eng, workspace_id=gc.ws, name="t", scopes=["read"],
                                     expires_in_days=30, current_password=PASSWORD)
    gc.db.commit()
    token = gc.client.get("/v1/governance", headers={"Authorization": f"Bearer {raw}"}).json()
    assert token["policy_changes"][0]["document"] is None and token["policy_changes"][0]["diff_vs_head"] is None
    stale = gc.accept(row, gc.owner, digest="0" * 64)
    _assert_envelope(stale, 409, "policy_digest_mismatch")
    _assert_envelope(gc.accept(row, gc.owner), 409, "consent_change_unacknowledged")
    done = gc.accept(row, gc.owner, ack=True)
    assert done.status_code == 200, done.text
    assert gc.get(gc.owner).json()["policy"]["document"]["data"]["share_r3_aggregates"] is True


def test_read_token_gets_the_shaped_console_over_http(gc):
    gc.propose(gc.eng)
    gc.switch(gc.owner, "off")
    inc.open_incident(gc.db, workspace_id=gc.ws, kind="manual", subject_kind="workspace", subject_key="a",
                      evidence={"note": "free text"})
    gc.db.commit()
    _row, raw = create_service_token(gc.db, creator=gc.eng, workspace_id=gc.ws, name="t", scopes=["read"],
                                     expires_in_days=30, current_password=PASSWORD)
    gc.db.commit()
    got = gc.client.get("/v1/governance", headers={"Authorization": f"Bearer {raw}"})
    assert got.status_code == 200
    for leaked in ("lower the monthly cap", "test <b>x</b>", "free text", str(gc.eng.id), str(gc.owner.id)):
        assert leaked not in got.text
    body = got.json()
    assert body["switches"]["workspace"][0]["state"] == "off" and body["open_incidents"][0]["evidence"] == {}
    assert body["policy_changes"][0]["open"] is True and body["policy_changes"][0]["rationale"] == ""


def test_a_replayed_key_rechecks_the_capability_and_proposals_expire_and_are_capped(gc, monkeypatch):
    key = f"k-{uuid4().hex}"
    assert gc.switch(gc.owner, "off", key=key).status_code == 200
    gc.db.execute(update(WorkspaceMembership).where(WorkspaceMembership.user_id == gc.owner.id).values(role="viewer"))
    gc.db.commit()
    _assert_envelope(gc.switch(gc.owner, "off", key=key), 403)  # not the stored result: the capability is gone
    from datetime import timedelta as td

    import app.agents.governance.policy as policy_module

    row = gc.propose(gc.eng).json()
    monkeypatch.setattr(policy_module, "_proposal_ttl", lambda db: td(days=-1))
    _assert_envelope(gc.accept(row, gc.admin), 409, "proposal_expired")
    monkeypatch.undo()
    monkeypatch.setattr(policy_module, "OPEN_PROPOSALS_MAX", 2)
    assert gc.propose(gc.eng, _policy(budgets__workspace_month_micros=4_000_000)).status_code == 200
    _assert_envelope(gc.propose(gc.eng, _policy(budgets__workspace_month_micros=3_000_000)), 429,
                     "too_many_open_proposals")


def test_replay_same_failure_staff_attribution_rate_limit_and_missing_digest(hz, client, monkeypatch):  # noqa: F811
    from app.agents.harness import replay as replay_module
    from app.agents.harness.replay import replay as real
    from test_agent_harness import _events

    db, g = hz.db, hz.g
    failing = (("llm",), ("raise",))
    failed = hz.service(failing).run(db, hz.spec(kind="specialist"))
    ok = hz.service((("llm",),)).run(db, hz.spec(kind="specialist"))
    state = {"script": failing}
    monkeypatch.setattr("app.api.v1_governance.replay",
                        lambda db_, **kw: real(db_, service=hz.service(state["script"]), **kw))
    staff = create_user(db, email=f"s-{uuid4().hex[:6]}@gov.test", password=PASSWORD, role=UserRole.DCLAB_ADMIN,
                        workspace_id=None)
    db.commit()

    def rp(run, who=g.actor):
        return client.post(f"/v1/agent-runs/{run}/replay", json={},
                           headers=_headers(who, g.ws, **{"Idempotency-Key": f"r-{uuid4().hex}"}))

    same = rp(failed.run_id).json()
    assert same["same_failure"] is True and same["equal"] is True and same["incident_id"] is None  # failed alike
    by_staff = rp(failed.run_id, staff)
    assert by_staff.status_code == 200
    checked = [e for e in _events(db, failed.run_id) if e.type == "replay_checked"]
    assert [e.payload["replayed_by"] for e in checked] == [str(g.actor.id), "platform_staff"]  # no operator id
    assert str(staff.id) not in json.dumps([e.payload for e in checked])
    original = replay_module._Record

    class Lost(original):  # the best-effort final event was lost: no recorded output digest
        def __init__(self, *a, **k):
            original.__init__(self, *a, **k)
            self.output_digest = None

    monkeypatch.setattr(replay_module, "_Record", Lost)
    state["script"] = (("llm",),)
    unknown = rp(ok.run_id).json()
    assert unknown["not_comparable"] is True and unknown["equal"] is True and unknown["incident_id"] is None
    monkeypatch.undo()
    monkeypatch.setattr("app.api.v1_governance.replay",
                        lambda db_, **kw: real(db_, service=hz.service(state["script"]), **kw))
    codes = [rp(ok.run_id).status_code for _ in range(5)]  # 1 earlier + 4 more fit the 5 per run window
    assert codes[:4] == [200] * 4 and codes[4] == 429


# --- R3 evidence links: verdicts only while verify_platform_raise could still rely on the run ------------------


KEY = "column.semantic_role"


def _live_run(gc, **changes):
    corpus = r3.benchmark_corpus("quick", tasks=["sk-iris"], seed=42)
    base = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=gc.db, actor=gc.staff)
    run = {**base, "run_id": str(uuid4()), "candidate": "live:jev:test", "live": True,
           "created_at": datetime.now(UTC).isoformat(), **changes}
    run["digest"], run["run_digest"] = r3.report_digest(run), None
    run["run_digest"] = r3.run_digest(run)
    return run


@pytest.mark.parametrize("scenario, current", [("current", True), ("stale_pair", False), ("development", False),
                                               ("incident", False), ("resolved_after_run", False), ("forged", False)])
def test_verdicts_show_only_for_a_live_verified_current_pair_run(gc, scenario, current):
    changes = {"stale_pair": {"pair": {"release": "column.semantic_role@v0", "model_id": "jev-1.12.0"}},
               "development": {"partition": "development"}}.get(scenario, {})
    if scenario == "incident":  # opened BEFORE the run is recorded: only the open-incident check can hide the verdict
        inc.open_incident(gc.db, workspace_id=None, kind="data_exposure", subject_kind="workspace", subject_key="p",
                          platform=True)
        gc.db.commit()
    run = _live_run(gc, **changes)
    stored = run
    if scenario == "forged":  # the stored row's points differ from what its digests cover
        stored = {**run, "points": {**run["points"], KEY: {**run["points"][KEY], "promotion": {"L1": {"allowed": True}}}}}
    r3.store_run(gc.db, stored, admin=gc.staff)
    r3.record_first_levels(gc.db, run, admin=gc.staff)  # L0 heads citing the run
    sync_jev_releases(gc.db)  # the database has released the code-pinned Jev releases
    gc.db.commit()
    if scenario == "resolved_after_run":  # resolved, but it demoted / opened after the run: the run predates it
        opened = inc.open_incident(gc.db, workspace_id=None, kind="data_exposure", subject_kind="workspace",
                                   subject_key="p", platform=True)
        inc.resolve_incident(gc.db, incident_id=opened.id, actor=gc.staff, resolution="done")
        gc.db.commit()
    level = next(lv for lv in gc.get(gc.owner).json()["levels"] if lv["key"] == KEY)
    evidence = level["r3_evidence"]
    assert evidence["digest_verified"] is (scenario != "forged") and evidence["live"] is True
    assert evidence["verdict_current"] is current
    if current:
        assert evidence["promotion_allowed"] and evidence["demotion_allowed"] is not None
    else:
        assert evidence["promotion_allowed"] == {} and evidence["demotion_allowed"] is None
    assert level["pair_current"] is (scenario != "stale_pair")


def test_a_level_for_an_old_model_counts_as_l0_and_shows_no_verdict(gc):
    run = _live_run(gc)
    r3.store_run(gc.db, run, admin=gc.staff)
    r3.record_first_levels(gc.db, run, admin=gc.staff)
    sync_jev_releases(gc.db)
    release_id, _model = r3.current_pair(gc.db, KEY)
    root = _level_head(gc.db, None, KEY)
    _insert(gc.db, "decision_point_policies", {
        "workspace_id": None, "decision_point_key": KEY, "level": 1, "cap_level": 2, "prompt_release_id": release_id,
        "model_id": "jev-1.12.0", "state": "accepted", "actor_kind": "human", "actor_user_id": gc.staff.id,
        "decided_by_user_id": gc.staff.id, "rationale": "old model", "supersedes_id": root.id,
        "evidence": [{"kind": "r3_run", "id": run["run_id"]}], "self_approved": True})
    _insert(gc.db, "decision_point_policies", {  # a workspace head for the same old model
        "workspace_id": gc.ws, "decision_point_key": KEY, "level": 1, "cap_level": 2, "prompt_release_id": release_id,
        "model_id": "jev-1.12.0", "state": "accepted", "actor_kind": "human", "actor_user_id": gc.owner.id,
        "decided_by_user_id": gc.owner.id, "rationale": "old model"})
    gc.db.commit()
    level = next(lv for lv in gc.get(gc.owner).json()["levels"] if lv["key"] == KEY)
    assert (level["platform_level"], level["workspace_level"], level["effective_level"], level["pair_current"]) == (
        0, 0, 0, False)
    assert level["r3_evidence"]["verdict_current"] is False and level["r3_evidence"]["promotion_allowed"] == {}


IDENT = "column.is_identifier"


def _heads(gc, key, platform_level, workspace_level, *, workspace_model="jev-1.13.0"):
    """Platform L0 roots citing a live current run, then platform / workspace heads on the current pair."""

    run = _live_run(gc)
    r3.store_run(gc.db, run, admin=gc.staff)
    r3.record_first_levels(gc.db, run, admin=gc.staff)
    sync_jev_releases(gc.db)
    release_id, model = r3.current_pair(gc.db, key)
    base = {"decision_point_key": key, "cap_level": REGISTRY[key].cap, "prompt_release_id": release_id,
            "state": "accepted", "actor_kind": "human", "rationale": "x"}
    if platform_level:
        _insert(gc.db, "decision_point_policies", {
            **base, "workspace_id": None, "level": platform_level, "model_id": model, "actor_user_id": gc.staff.id,
            "decided_by_user_id": gc.staff.id, "supersedes_id": _level_head(gc.db, None, key).id,
            "evidence": [{"kind": "r3_run", "id": run["run_id"]}], "self_approved": True})
    if workspace_level:
        _insert(gc.db, "decision_point_policies", {
            **base, "workspace_id": gc.ws, "level": workspace_level, "model_id": workspace_model,
            "actor_user_id": gc.owner.id, "decided_by_user_id": gc.owner.id})
    gc.db.commit()
    return next(lv for lv in gc.get(gc.owner).json()["levels"] if lv["key"] == key)


def test_matching_l1_heads_give_an_effective_l1_and_a_current_verdict(gc):
    level = _heads(gc, IDENT, 1, 1)
    assert (level["platform_level"], level["workspace_level"], level["effective_level"], level["pair_current"]) == (
        1, 1, 1, True)
    assert level["r3_evidence"]["verdict_current"] is True


def test_a_workspace_head_for_another_model_counts_as_l0(gc):
    level = _heads(gc, IDENT, 1, 1, workspace_model="jev-1.12.0")
    assert (level["platform_level"], level["workspace_level"], level["effective_level"]) == (1, 0, 0)


def test_mixed_points_show_the_strictest_answer_ceiling_and_release_max_level_caps(gc, monkeypatch):
    mixed = _heads(gc, KEY, 2, 2)  # semantic_role: L2 only for numeric / categorical answers (ADR 0008 §1b)
    assert (mixed["platform_level"], mixed["workspace_level"], mixed["effective_level"]) == (2, 2, 1)
    from dataclasses import replace

    from app.agents.semantic.releases import RELEASES

    monkeypatch.setattr(r3, "RELEASES", {**RELEASES, IDENT: replace(RELEASES[IDENT], max_level=0)})
    level = _heads(gc, IDENT, 1, 1)
    assert (level["platform_level"], level["effective_level"]) == (1, 0)  # the runtime clamps to the release max


def test_an_unreleased_code_bump_fails_closed_like_the_runtime_snapshot(gc, monkeypatch):
    level = _heads(gc, IDENT, 1, 1)
    assert level["effective_level"] == 1
    from dataclasses import replace

    from app.agents.semantic import releases

    bumped = replace(releases.RELEASES[IDENT], version=releases.RELEASES[IDENT].version + 1)
    monkeypatch.setattr(releases, "RELEASES", {**releases.RELEASES, IDENT: bumped})
    monkeypatch.setattr(r3, "RELEASES", releases.RELEASES)  # code pins v2; the database only released v1
    level = next(lv for lv in gc.get(gc.owner).json()["levels"] if lv["key"] == IDENT)
    assert (level["platform_level"], level["workspace_level"], level["effective_level"], level["pair_current"]) == (
        0, 0, 0, False)


def test_a_stale_budget_period_shows_the_current_period_with_zero_spend(gc):
    last_month = (datetime.now(UTC).date().replace(day=1) - timedelta(days=1)).replace(day=1)
    _insert(gc.db, "workspace_llm_budgets", {"workspace_id": gc.ws, "scope": "workspace", "period": "month",
                                             "period_start": last_month, "limit_micros": 1_000_000,
                                             "spent_micros": 700_000, "calls": 4})
    gc.db.commit()
    period = gc.get(gc.owner).json()["spend"]["workspace"][0]
    assert (period["spent_micros"], period["calls"]) == (0, 0)
    assert period["period_start"] == datetime.now(UTC).date().replace(day=1).isoformat()
    assert period["limit_micros"] == 1_000_000
