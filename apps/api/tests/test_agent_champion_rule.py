"""P6.10-A2 champion evidence rule (ADR 0008 §2b; ADR 0006 §2, Q1; security review of A1).

Agent consumers never see or cite final-holdout ids or scopes. On an agent's champion
proposal (a service token over ``/v1`` here; the harness principal is covered in
``test_agent_harness``) DCLab attaches the promoted model's own single locked final
evaluation itself, marks it for the audit, refuses generically when there is none and
refuses agent-supplied holdout evidence; humans are unchanged, and accepting stays
human with a re-check of the evaluation.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agents.tools.definitions.reads import decision_summary
from app.agents.tools.render import mcp_json
from app.db.models import ModelEvaluation, ProjectDecisionRecord
from app.domain.errors import DecisionActorNotPermittedError, InvalidDecisionRecordError
from app.services import decision_record_service as drs
from app.services import project_ref_service as prs
from app.services.project_ref_service import RefMove
from app.services.service_token_service import create_service_token
from test_decision_record_service import _bootstrap, _force_lock, _holdout, _refs, g, setup  # noqa: F401
from test_graph_service import _headers

HOLDOUT = re.compile(r"holdout|final_test", re.IGNORECASE)


def _token(db, g):  # noqa: F811
    row, raw = create_service_token(db, creator=g.actor, workspace_id=g.ws, name="champion",
                                    scopes=["read", "decisions:propose"], expires_in_days=30,
                                    current_password="test-password")
    db.commit()
    g.bindings[row.id] = g.ws  # the fixture's binding verifier (production: the request's token)
    return row, raw


def _body(g, evidence):  # noqa: F811
    return {"action": "propose_ref_move", "rationale": "mv1 beats mv0 on the same plan (CV)",
            "ref_moves": [{"ref_kind": "champion_model", "target_id": str(g.mv[1])},
                          {"ref_kind": "feature_recipe", "target_id": str(g.fsv[1])}],
            "evidence_refs": evidence}


def _evaluation(db, candidate_id) -> UUID:
    return db.scalar(select(ModelEvaluation.id).where(ModelEvaluation.candidate_id == candidate_id,
                                                      ModelEvaluation.evaluation_scope == "final_holdout"))


def _holdout_hits(value) -> list:
    if isinstance(value, dict):
        return [k for k in value if HOLDOUT.search(str(k))] + [h for v in value.values() for h in _holdout_hits(v)]
    if isinstance(value, list):
        return [h for item in value for h in _holdout_hits(item)]
    return [value] if isinstance(value, str) and HOLDOUT.search(value) else []


def test_token_champion_proposal_gets_the_service_attached_final_evaluation(client, db_session, g, monkeypatch):
    db = db_session
    _bootstrap(db, g)
    _row, raw = _token(db, g)
    url = f"/v1/projects/{g.project.id}/decisions"
    cv = [{"kind": "experiment", "id": str(g.exp[1])}]

    def post(key, evidence):
        return client.post(url, headers={"Authorization": f"Bearer {raw}", "Idempotency-Key": key},
                           json=_body(g, evidence))

    # Not eligible (evidence not locked): one generic refusal, nothing about any value.
    early = post("champ-0", cv)
    assert early.status_code == 422 and early.json()["error"]["details"]["reason"] == "champion_evidence_unavailable"
    _force_lock(db, g.exp[1])
    # An agent never cites the holdout itself.
    cited = post("champ-1", _holdout(g, 1))
    assert cited.status_code == 422 and cited.json()["error"]["details"]["reason"] == "holdout_not_allowed"
    created = post("champ-2", cv)
    assert created.status_code == 201, created.text
    record = created.json()
    assert (record["actor"]["kind"], record["decision_type"], record["state"]) == ("agent", "champion_promoted",
                                                                                   "proposed")
    attached = {"kind": "candidate", "id": str(g.cand[1]), "scope": "final_holdout"}
    stored = db.get(ProjectDecisionRecord, UUID(record["id"]))
    assert stored.evidence_refs == [*cv, attached]
    assert stored.details["service_attached_evidence"] == [
        {**attached, "rule": "champion.final_evaluation.v1", "evaluation_id": str(_evaluation(db, g.cand[1]))}]
    replayed = post("champ-2", cv)  # the same agent call replays (the attached ref is the service's)
    assert replayed.status_code == 201 and replayed.json()["id"] == record["id"]
    # Follow-up 1: the token's REST responses (create, replay, read, list) carry no holdout key
    # or scope; the human reading the same record sees the attached ref.
    token = {"Authorization": f"Bearer {raw}"}
    for body in (record, replayed.json(), client.get(f"/v1/decisions/{record['id']}", headers=token).json(),
                 client.get(url, headers=token).json()):
        assert not _holdout_hits(body), _holdout_hits(body)
    human = client.get(f"/v1/decisions/{record['id']}", headers=_headers(g.actor, g.ws)).json()
    assert attached in [{key: ref[key] for key in ("kind", "id", "scope")} for ref in human["evidence_refs"]]
    assert _holdout_hits(client.get(url, headers=_headers(g.actor, g.ws)).json())  # humans unchanged
    # Agent consumers (MCP / catalog shaping) never see the attached ref or its marker.
    assert not _holdout_hits(mcp_json(decision_summary(drs.record_read(db, stored))))

    # Accepting is human: the evaluation is re-checked against the promoted model...
    moves = [RefMove("champion_model", g.mv[1], 1), RefMove("feature_recipe", g.fsv[1], 1)]
    with monkeypatch.context() as patched:
        patched.setattr(prs, "champion_final_evaluation", lambda *a, **k: SimpleNamespace(
            id=uuid4(), candidate_id=g.cand[1]))
        with pytest.raises(InvalidDecisionRecordError, match="champion_evidence_unavailable"):
            prs.move_ref(db, workspace_id=g.ws, project_id=g.project.id, actor=g.human, proposal_id=stored.id,
                         moves=moves, rationale="reviewed", evidence_refs=cv)
        db.rollback()
    # ...the human sees its values (never a token / agent run)...
    shown = prs.champion_final_evaluation_for_human(db, actor=g.human, workspace_id=g.ws, project_id=g.project.id,
                                                    record_id=stored.id)
    assert shown["metrics"] == {"roc_auc": pytest.approx(0.81)} and shown["candidate_id"] == g.cand[1]
    with pytest.raises(DecisionActorNotPermittedError, match="human_only"):
        prs.champion_final_evaluation_for_human(db, actor=g.agent, workspace_id=g.ws, project_id=g.project.id,
                                                record_id=stored.id)
    # ...and accepts without citing the holdout: the attached ref is carried, re-checked.
    accepted = prs.move_ref(db, workspace_id=g.ws, project_id=g.project.id, actor=g.human, proposal_id=stored.id,
                            moves=moves, rationale="reviewed the final evaluation", evidence_refs=cv,
                            idempotency_key="accept-1")
    db.commit()
    retried = prs.move_ref(db, workspace_id=g.ws, project_id=g.project.id, actor=g.human, proposal_id=stored.id,
                           moves=moves, rationale="reviewed the final evaluation", evidence_refs=cv,
                           idempotency_key="accept-1")
    assert retried.replayed and retried.record.id == accepted.record.id  # the human's retry replays
    assert accepted.record.actor_kind == "human" and attached in accepted.record.evidence_refs
    assert accepted.record.details["service_attached_evidence"][0]["rechecked_at_accept"] is True
    assert _refs(db, g)["champion_model"].model_version_id == g.mv[1]


def test_humans_are_unchanged_and_agents_never_cite_the_holdout(db_session, g):
    db = db_session
    _bootstrap(db, g)
    _force_lock(db, g.exp[1])
    moves = [RefMove("champion_model", g.mv[1], 1), RefMove("feature_recipe", g.fsv[1], 1)]
    # A human still cites the final evaluation explicitly (no service attachment for humans).
    with pytest.raises(InvalidDecisionRecordError, match="champion_requires_final_holdout_evidence"):
        prs.propose_ref_move(db, workspace_id=g.ws, project_id=g.project.id, actor=g.human, moves=moves,
                             rationale="x", evidence_refs=[{"kind": "experiment", "id": str(g.exp[1])}])
    human = prs.propose_ref_move(db, workspace_id=g.ws, project_id=g.project.id, actor=g.human, moves=moves,
                                 rationale="x", evidence_refs=_holdout(g, 1))
    assert "service_attached_evidence" not in human.details and human.evidence_refs == _holdout(g, 1)
    db.rollback()
    for kwargs in ({"evidence_refs": _holdout(g, 0)}, {"facts": {"holdout_auc": 0.8}},
                   {"details": {"basis": "final_holdout"}}):
        with pytest.raises(InvalidDecisionRecordError, match="holdout_not_allowed"):
            drs.propose(db, workspace_id=g.ws, project_id=g.project.id, actor=g.agent,
                        decision_type="experiment_accepted", subject_kind="experiment", subject_id=g.exp[0],
                        rationale="agent", **{"evidence_refs": [{"kind": "experiment", "id": str(g.exp[0])}],
                                              **kwargs})
    with pytest.raises(InvalidDecisionRecordError, match="holdout_not_allowed"):
        prs.propose_ref_move(db, workspace_id=g.ws, project_id=g.project.id, actor=g.agent,
                             moves=[RefMove("split_plan", g.plan2.id, 1)], rationale="x",
                             evidence_refs=[{"kind": "split_plan", "id": str(g.plan2.id)}],
                             facts={"final_test_auc": 0.7})
    # The rule only selects by identity: the promoted model's own single locked evaluation.
    evaluation = prs.champion_final_evaluation(db, workspace_id=g.ws, project_id=g.project.id,
                                               model_version_id=g.mv[1])
    assert evaluation.id == _evaluation(db, g.cand[1])
    for workspace_id, model_version_id in ((g.ws, uuid4()), (uuid4(), g.mv[1])):  # unknown / foreign: one answer
        with pytest.raises(InvalidDecisionRecordError, match="champion_evidence_unavailable"):
            prs.champion_final_evaluation(db, workspace_id=workspace_id, project_id=g.project.id,
                                          model_version_id=model_version_id)
