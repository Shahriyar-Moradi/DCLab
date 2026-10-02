"""P3.1-B3: POST /v1 decisions (propose/record, accept/reject/supersede), ref moves
with If-Match, and GET /v1/model-versions/{id} (ADR 0006 §2, §5, §9)."""

from __future__ import annotations

from typing import get_args
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import (
    IdempotencyKey,
    ModelVersion,
    ProjectDecisionRecord,
    ProjectRef,
    User,
    WorkspaceRole,
)
from app.domain.decision_records import HUMAN_RECORDABLE_DECISION_TYPES, HumanDecisionType
from app.services import decision_record_service as drs
from app.services import idempotency_service
from app.services.workspace_service import add_workspace_member
from test_decision_record_service import _bootstrap, _force_lock, _holdout, g, setup  # noqa: F401 (fixtures)
from test_graph_service import _seed_graph
from test_v1_contract_conventions import _assert_envelope, _headers
from test_v1_resources_experiments import _dataset, _root, _work


def _key() -> str:
    return f"p31b3-{uuid4().hex}"


def _h(g, user=None, key: str | None = None, ws=None, **extra) -> dict[str, str]:
    headers = _headers(user or g.actor, ws or g.ws, **extra)
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _proposal(g, i: int = 0, **extra) -> dict:
    return {
        "action": "propose", "decision_type": "experiment_accepted",
        "subject": {"kind": "experiment", "id": str(g.exp[i])},
        "rationale": "branch improves recall",
        "evidence_refs": [{"kind": "experiment", "id": str(g.exp[i])}],
        **extra,
    }


def _post(client, g, path: str, body: dict, *, key: str | None = None, user=None, **headers):
    return client.post(path, json=body, headers=_h(g, user, key=key or _key(), **headers))


def _records(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(ProjectDecisionRecord))


def _member(db, g, role: WorkspaceRole) -> User:
    membership = add_workspace_member(
        db, actor=g.actor, workspace_id=g.ws, email=f"p31b3-{role.value}-{uuid4().hex}@test.invalid",
        password="test-password", role=role.value,
    )
    db.commit()
    return db.get(User, membership.user_id)


# --- decisions: state machine over HTTP ---------------------------------------------------


def test_propose_accept_reject_supersede_and_idempotent_replay(client, db_session, g):
    db = db_session
    decisions = f"/v1/projects/{g.project.id}/decisions"
    key = _key()
    created = _post(client, g, decisions, _proposal(g), key=key)
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert created.headers["Location"] == f"/v1/decisions/{proposal['id']}" and created.headers["ETag"]
    assert (proposal["state"], proposal["effective_state"], proposal["decision_type"]) == (
        "proposed", "proposed", "experiment_accepted",
    )
    assert proposal["actor"] == {"kind": "human", "user_id": str(g.actor.id), "rule": None,
                                 "agent_run_id": None, "service_token_id": None}
    assert proposal["rationale_untrusted"] is False and proposal["subject"]["id"] == str(g.exp[0])

    # Same key + same request replays; another body under the key is 409; nothing new is written.
    before = _records(db)
    replay = _post(client, g, decisions, _proposal(g), key=key)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == proposal["id"]
    _assert_envelope(_post(client, g, decisions, _proposal(g, rationale="other"), key=key), 409,
                     "idempotency_key_conflict")
    assert _records(db) == before
    stored = db.scalar(select(IdempotencyKey).where(IdempotencyKey.idempotency_key == key))
    assert (stored.resource_kind, stored.response_status, str(stored.resource_id)) == (
        "decision_record", 201, proposal["id"],
    )
    read = client.get(f"/v1/decisions/{proposal['id']}", headers=_h(g))
    assert read.status_code == 200 and read.json()["effective_state"] == "proposed" and read.headers["ETag"]

    # proposed --accept--> accepted (a new row); replay; a second accept is 409.
    accept_path = f"/v1/decisions/{proposal['id']}/accept"
    accept_key = _key()
    accepted = _post(client, g, accept_path, {"rationale": "reviewed"}, key=accept_key)
    assert accepted.status_code == 201, accepted.text
    acc = accepted.json()
    assert (acc["state"], acc["supersedes_id"], acc["actor"]["kind"]) == ("accepted", proposal["id"], "human")
    assert acc["evidence_refs"] == proposal["evidence_refs"]  # carried from the proposal
    again = _post(client, g, accept_path, {"rationale": "reviewed"}, key=accept_key)
    assert again.headers["Idempotent-Replayed"] == "true" and again.json()["id"] == acc["id"]
    second = _assert_envelope(_post(client, g, accept_path, {"rationale": "again"}), 409, "invalid_decision_transition")
    assert second["details"]["reason"] == "already_resolved"
    _assert_envelope(_post(client, g, f"/v1/decisions/{proposal['id']}/reject", {"rationale": "no"}), 409,
                     "invalid_decision_transition")
    now = client.get(f"/v1/decisions/{proposal['id']}", headers=_h(g)).json()
    assert (now["effective_state"], now["superseded_by_id"]) == ("superseded", acc["id"])
    # Accepting an accepted record is not a transition.
    not_proposal = _post(client, g, f"/v1/decisions/{acc['id']}/accept", {"rationale": "x"})
    assert _assert_envelope(not_proposal, 409, "invalid_decision_transition")["details"]["reason"] == "not_a_proposal"

    # proposed --reject--> rejected (terminal: no correction).
    other = _post(client, g, decisions, _proposal(g, 1)).json()
    rejected = _post(client, g, f"/v1/decisions/{other['id']}/reject", {"rationale": "not convincing"})
    assert rejected.status_code == 201 and rejected.json()["state"] == "rejected"
    terminal = _post(client, g, f"/v1/decisions/{rejected.json()['id']}/supersede", {"rationale": "x"})
    assert _assert_envelope(terminal, 409, "invalid_decision_transition")["details"]["reason"] == "not_accepted"

    # accepted --correct--> accepted' (same type/subject); the old one is superseded once.
    corrected = _post(client, g, f"/v1/decisions/{acc['id']}/supersede",
                      {"rationale": "corrected", "facts": {"recall_delta": 0.04}})
    assert corrected.status_code == 201, corrected.text
    fixed = corrected.json()
    assert (fixed["state"], fixed["supersedes_id"], fixed["facts"]) == ("accepted", acc["id"], {"recall_delta": 0.04})
    assert fixed["subject"] == acc["subject"] and fixed["decision_type"] == acc["decision_type"]
    twice = _post(client, g, f"/v1/decisions/{acc['id']}/supersede", {"rationale": "again"})
    assert _assert_envelope(twice, 409, "invalid_decision_transition")["details"]["reason"] == "already_superseded"

    # action=record: a decision made now starts an accepted chain; it shows in the list.
    recorded = _post(client, g, decisions, _proposal(g, 1, action="record", decision_type="experiment_rejected"))
    assert recorded.status_code == 201 and recorded.json()["state"] == "accepted"
    listed = client.get(decisions, headers=_h(g), params={"effective_state": "accepted"}).json()["items"]
    assert {item["id"] for item in listed} == {fixed["id"], recorded.json()["id"]}
    db.expire_all()
    kinds = set(db.scalars(select(ProjectDecisionRecord.actor_kind).where(
        ProjectDecisionRecord.project_id == g.project.id)))
    assert kinds == {"human"}


def test_a_lost_resolution_race_replays_the_winner(client, db_session, g, monkeypatch):
    """Same key, both requests past the key lookup: the loser of uq_pdr_supersedes_id
    answers the winner's record instead of 409."""

    proposal = _post(client, g, f"/v1/projects/{g.project.id}/decisions", _proposal(g)).json()
    path, key = f"/v1/decisions/{proposal['id']}/accept", _key()
    winner = _post(client, g, path, {"rationale": "ok"}, key=key).json()
    real, calls = idempotency_service.find_bound, []

    def first_lookup_misses(db, scope, binding):
        calls.append(scope.operation)
        return None if len(calls) == 1 else real(db, scope, binding)

    monkeypatch.setattr(idempotency_service, "find_bound", first_lookup_misses)
    raced = _post(client, g, path, {"rationale": "ok"}, key=key)
    assert raced.status_code == 201 and raced.headers["Idempotent-Replayed"] == "true"
    assert raced.json()["id"] == winner["id"] and len(calls) == 2
    calls.clear()
    other = _post(client, g, path, {"rationale": "ok"})  # a different key really lost
    assert _assert_envelope(other, 409, "invalid_decision_transition")["details"]["reason"] == "already_resolved"


def test_bodies_cannot_choose_the_actor_and_errors_use_the_envelope(client, db_session, g):
    db = db_session
    decisions = f"/v1/projects/{g.project.id}/decisions"
    proposal = _post(client, g, decisions, _proposal(g)).json()
    before = _records(db)
    for extra in ({"actor_kind": "agent"}, {"agent_run_id": str(uuid4())}, {"actor_rule": "refs.bootstrap.v1"},
                  {"actor": {"kind": "rule"}}, {"service_token_id": str(uuid4())}, {"rationale_untrusted": True}):
        _assert_envelope(_post(client, g, decisions, _proposal(g, **extra)), 422, "validation_failed")
        _assert_envelope(_post(client, g, f"/v1/decisions/{proposal['id']}/accept", {"rationale": "x", **extra}),
                         422, "validation_failed")
        move = {"target_id": str(g.source.id), "rationale": "x",
                "evidence_refs": [{"kind": "dataset_version", "id": str(g.source.id)}], **extra}
        _assert_envelope(_post(client, g, f"/v1/projects/{g.project.id}/refs/dataset", move,
                               **{"If-None-Match": "*"}), 422, "validation_failed")
    # Rule-owned, reserved, service-owned and ref-move types are not writable here.
    for decision_type in ("winner_locked", "ref_initialized", "problem_spec_locked", "proposal_accepted",
                          "ref_moved", "champion_promoted"):
        _assert_envelope(_post(client, g, decisions, _proposal(g, decision_type=decision_type)), 422,
                         "validation_failed")
    assert set(get_args(HumanDecisionType)) == set(HUMAN_RECORDABLE_DECISION_TYPES)
    _assert_envelope(_post(client, g, decisions, _proposal(g, action="accept")), 422, "validation_failed")
    _assert_envelope(_post(client, g, decisions, _proposal(g, subject={"kind": "experiment", "id": "x"})), 422,
                     "validation_failed")
    no_key = client.post(decisions, json=_proposal(g), headers=_h(g))
    _assert_envelope(no_key, 400, "idempotency_key_required")

    # Typed service refusals keep stable codes and a reason.
    def refused(body: dict, reason: str, status: int = 422, code: str = "invalid_decision_record") -> None:
        error = _assert_envelope(_post(client, g, decisions, body), status, code)
        assert error["details"]["reason"] == reason, error

    refused(_proposal(g, rationale="   "), "rationale_required")
    refused(_proposal(g, rationale="token sk-live-abcdefghijklmnopqrstuvwx"), "secret_like_text")
    refused(_proposal(g, details={"ref_moves": []}), "reserved_detail_key")
    refused(_proposal(g, evidence_refs=[{"kind": "experiment", "id": str(uuid4())}]), "evidence_ref_not_found")
    refused(_proposal(g, subject={"kind": "experiment", "id": str(uuid4())}), "subject_not_found")
    assert _records(db) == before


def test_viewer_reads_but_cannot_write(client, db_session, g):
    db = db_session
    _bootstrap(db, g)
    viewer = _member(db, g, WorkspaceRole.VIEWER)
    proposal = _post(client, g, f"/v1/projects/{g.project.id}/decisions", _proposal(g)).json()
    for path in (f"/v1/decisions/{proposal['id']}", f"/v1/projects/{g.project.id}/refs",
                 f"/v1/projects/{g.project.id}/refs/champion_model", f"/v1/model-versions/{g.mv[0]}",
                 f"/v1/projects/{g.project.id}/decisions"):
        assert client.get(path, headers=_h(g, viewer)).status_code == 200, path
    writes = [
        (f"/v1/projects/{g.project.id}/decisions", _proposal(g)),
        (f"/v1/decisions/{proposal['id']}/accept", {"rationale": "x"}),
        (f"/v1/decisions/{proposal['id']}/reject", {"rationale": "x"}),
        (f"/v1/decisions/{proposal['id']}/supersede", {"rationale": "x"}),
        (f"/v1/projects/{g.project.id}/refs/split_plan",
         {"target_id": str(g.plan2.id), "rationale": "x", "evidence_refs": [{"kind": "split_plan", "id": str(g.plan2.id)}]}),
    ]
    for path, body in writes:
        _assert_envelope(_post(client, g, path, body, user=viewer, **{"If-Match": '"1"'}), 403, "forbidden")


def test_foreign_projects_decisions_refs_and_model_versions_are_not_found(client, db_session, g, setup, tmp_path):
    db = db_session
    beta = _seed_graph(db, tmp_path, setup, "beta", [{}])
    from app.domain.decision_records import DecisionActor

    beta_record = drs.propose(db, workspace_id=beta.ws, project_id=beta.project.id,
                              actor=DecisionActor.human(beta.actor), decision_type="experiment_accepted",
                              subject_kind="experiment", subject_id=beta.exp[0], rationale="beta")
    db.commit()
    _bootstrap(db, g)
    before = _records(db)
    for method, path, body in (
        ("GET", f"/v1/decisions/{beta_record.id}", None),
        ("POST", f"/v1/decisions/{beta_record.id}/accept", {"rationale": "x"}),
        ("POST", f"/v1/decisions/{beta_record.id}/reject", {"rationale": "x"}),
        ("POST", f"/v1/decisions/{beta_record.id}/supersede", {"rationale": "x"}),
        ("POST", f"/v1/projects/{beta.project.id}/decisions", _proposal(beta)),
        ("GET", f"/v1/projects/{beta.project.id}/refs", None),
        ("GET", f"/v1/projects/{beta.project.id}/refs/dataset", None),
        ("POST", f"/v1/projects/{beta.project.id}/refs/dataset",
         {"target_id": str(beta.source.id), "rationale": "x",
          "evidence_refs": [{"kind": "dataset_version", "id": str(beta.source.id)}]}),
        ("GET", f"/v1/model-versions/{beta.mv[0]}", None),
        ("GET", f"/v1/model-versions/{uuid4()}", None),
    ):
        response = client.request(method, path, json=body, headers=_h(g, key=_key(), **{"If-None-Match": "*"}))
        _assert_envelope(response, 404, "not_found")
        # Naming the other workspace without membership is 403, never data.
        foreign = client.request(method, path, json=body, headers=_h(g, key=_key(), ws=beta.ws))
        _assert_envelope(foreign, 403, "forbidden")
    # A foreign ref target or evidence node in our own project is refused, never moved.
    ref = client.get(f"/v1/projects/{g.project.id}/refs/split_plan", headers=_h(g)).json()
    stray = _post(client, g, f"/v1/projects/{g.project.id}/refs/split_plan",
                  {"target_id": str(beta.plan.id), "rationale": "x",
                   "evidence_refs": [{"kind": "split_plan", "id": str(g.plan.id)}]}, **{"If-Match": ref["etag"]})
    assert _assert_envelope(stray, 404, "ref_target_not_found")["details"]["ref_kind"] == "split_plan"
    cites = _post(client, g, f"/v1/projects/{g.project.id}/decisions",
                  _proposal(g, evidence_refs=[{"kind": "experiment", "id": str(beta.exp[0])}]))
    assert _assert_envelope(cites, 422, "invalid_decision_record")["details"]["reason"] == "evidence_ref_not_found"
    assert _records(db) == before


# --- refs: If-Match / If-None-Match ------------------------------------------------------------------


def test_ref_moves_require_if_match_and_move_the_graph(client, db_session, g):
    db = db_session
    refs_path = f"/v1/projects/{g.project.id}/refs"
    empty = client.get(refs_path, headers=_h(g)).json()
    assert (empty["refs_initialized"], empty["items"]) == (False, [])
    assert empty["missing_kinds"] == ["problem_spec", "dataset", "split_plan", "feature_recipe", "champion_model"]
    _assert_envelope(client.get(f"{refs_path}/dataset", headers=_h(g)), 404, "ref_not_found")

    def move(kind, target, evidence, *, key=None, companions=(), **headers):
        body = {"target_id": str(target), "rationale": f"move {kind}", "evidence_refs": evidence,
                "companion_moves": list(companions)}
        return _post(client, g, f"{refs_path}/{kind}", body, key=key, **headers)

    data_ev = [{"kind": "dataset_version", "id": str(g.source.id)}]
    _assert_envelope(move("dataset", g.source.id, data_ev), 428, "precondition_required")
    missing = _assert_envelope(move("dataset", g.source.id, data_ev, **{"If-Match": '"1"'}), 412, "precondition_failed")
    assert missing["details"] == {"ref_kind": "dataset", "current_etag": None}
    _assert_envelope(move("dataset", g.source.id, data_ev, **{"If-None-Match": '"1"'}), 400, "invalid_precondition")
    created = move("dataset", g.source.id, data_ev, **{"If-None-Match": "*"})
    assert created.status_code == 200, created.text
    assert created.headers["ETag"] == '"1"'
    decision = created.json()["decision"]
    assert (decision["decision_type"], decision["state"], decision["actor"]["kind"]) == ("ref_moved", "accepted", "human")
    assert decision["details"]["ref_moves"][0]["from"] is None
    _assert_envelope(move("dataset", g.source.id, data_ev, **{"If-None-Match": "*"}), 412, "precondition_failed")
    plan_ev = [{"kind": "split_plan", "id": str(g.plan.id)}]
    assert move("split_plan", g.plan.id, plan_ev, **{"If-None-Match": "*"}).status_code == 200
    first = move("champion_model", g.mv[0], _holdout(g, 0), **{"If-None-Match": "*"},
                 companions=[{"ref_kind": "feature_recipe", "target_id": str(g.fsv[0]), "expected_version": None}])
    assert first.status_code == 200, first.text
    assert first.json()["decision"]["decision_type"] == "champion_promoted"
    champion = client.get(f"{refs_path}/champion_model", headers=_h(g))
    assert champion.headers["ETag"] == '"1"' and champion.json()["target"]["id"] == str(g.mv[0])

    # Promote mv1 (same split plan): the precondition gates the path ref.
    _force_lock(db, g.exp[1])
    recipe = [{"ref_kind": "feature_recipe", "target_id": str(g.fsv[1]), "expected_version": 1}]
    promote_ev = _holdout(g, 1)
    _assert_envelope(move("champion_model", g.mv[1], promote_ev, companions=recipe), 428, "precondition_required")
    for wildcard in ("*", "*,", '*, "1"', " , "):
        _assert_envelope(move("champion_model", g.mv[1], promote_ev, companions=recipe, **{"If-Match": wildcard}),
                         428, "precondition_required")
    stale = move("champion_model", g.mv[1], promote_ev, companions=recipe, **{"If-Match": '"7"'})
    assert _assert_envelope(stale, 412, "precondition_failed")["details"]["current_etag"] == '"1"'
    _assert_envelope(move("champion_model", g.mv[1], promote_ev, companions=recipe, **{"If-Match": 'W/"1"'}), 412,
                     "precondition_failed")
    stale_companion = [{**recipe[0], "expected_version": 5}]
    conflict = move("champion_model", g.mv[1], promote_ev, companions=stale_companion, **{"If-Match": '"1"'})
    assert _assert_envelope(conflict, 409, "ref_version_conflict")["details"]["ref_kind"] == "feature_recipe"
    no_holdout = move("champion_model", g.mv[1], [{"kind": "candidate", "id": str(g.cand[1])}], companions=recipe,
                      **{"If-Match": '"1"'})
    assert _assert_envelope(no_holdout, 422)["details"]["reason"] == "champion_requires_final_holdout_evidence"

    before = _records(db)
    key = _key()
    promoted = move("champion_model", g.mv[1], promote_ev, key=key, companions=recipe, **{"If-Match": '"1"'})
    assert promoted.status_code == 200, promoted.text
    assert promoted.headers["ETag"] == '"2"'
    body = promoted.json()
    refs = {ref["ref_kind"]: ref for ref in body["refs"]}
    assert refs["champion_model"]["target"]["id"] == str(g.mv[1]) and refs["champion_model"]["etag"] == '"2"'
    assert refs["feature_recipe"]["target"]["id"] == str(g.fsv[1]) and refs["feature_recipe"]["version"] == 2
    assert refs["champion_model"]["decision_record_id"] == body["decision"]["id"]
    # A retry with the same key and (now stale) If-Match replays instead of 412.
    replay = move("champion_model", g.mv[1], promote_ev, key=key, companions=recipe, **{"If-Match": '"1"'})
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["decision"]["id"] == body["decision"]["id"] and replay.headers["ETag"] == '"2"'
    _assert_envelope(move("champion_model", g.mv[0], _holdout(g, 0), key=key, companions=recipe,
                          **{"If-Match": '"1"'}), 409, "idempotency_key_conflict")
    assert _records(db) == before + 1

    # The graph and the model versions see the move.
    graph = client.get(f"/v1/projects/{g.project.id}/graph", headers=_h(g)).json()
    graph_champion = next(ref for ref in graph["refs"] if ref["ref_kind"] == "champion_model")
    assert (graph_champion["target"]["id"], graph_champion["version"]) == (str(g.mv[1]), 2)
    assert client.get(f"/v1/model-versions/{g.mv[1]}", headers=_h(g)).json()["is_champion"] is True
    assert client.get(f"/v1/model-versions/{g.mv[0]}", headers=_h(g)).json()["is_champion"] is False

    # Rev 2: e3 was evaluated on plan2; it cannot replace a champion on plan.
    mismatch = move("champion_model", g.mv[3], _holdout(g, 3), **{"If-Match": '"2"'},
                    companions=[{"ref_kind": "feature_recipe", "target_id": str(g.fsv[3]), "expected_version": 2}])
    error = _assert_envelope(mismatch, 409, "champion_split_plan_mismatch")
    assert error["details"]["expected_split_plan_id"] == str(g.plan.id)
    # Ref history is corrected by a new move, never in place.
    history = _post(client, g, f"/v1/decisions/{body['decision']['id']}/supersede", {"rationale": "rewrite"})
    assert _assert_envelope(history, 409, "invalid_decision_transition")["details"]["reason"] == "ref_history_not_correctable"
    db.expire_all()
    assert db.scalar(select(ProjectRef.model_version_id).where(
        ProjectRef.project_id == g.project.id, ProjectRef.ref_kind == "champion_model")) == g.mv[1]


def test_ref_move_proposal_is_accepted_through_the_refs_route(client, db_session, g):
    db = db_session
    _bootstrap(db, g)
    _force_lock(db, g.exp[1])
    proposed = _post(client, g, f"/v1/projects/{g.project.id}/decisions", {
        "action": "propose_ref_move",
        "ref_moves": [{"ref_kind": "champion_model", "target_id": str(g.mv[1])},
                      {"ref_kind": "feature_recipe", "target_id": str(g.fsv[1])}],
        "rationale": "mv1 beats mv0 on the same plan", "evidence_refs": _holdout(g, 1),
    })
    assert proposed.status_code == 201, proposed.text
    proposal = proposed.json()
    assert (proposal["state"], proposal["decision_type"]) == ("proposed", "champion_promoted")
    assert client.get(f"/v1/projects/{g.project.id}/refs/champion_model", headers=_h(g)).json()["version"] == 1

    via_accept = _post(client, g, f"/v1/decisions/{proposal['id']}/accept", {"rationale": "ok"})
    reason = _assert_envelope(via_accept, 409, "invalid_decision_transition")["details"]["reason"]
    assert reason == "ref_move_requires_move_ref"
    body = {"target_id": str(g.mv[1]), "rationale": "reviewed", "evidence_refs": _holdout(g, 1),
            "proposal_id": proposal["id"],
            "companion_moves": [{"ref_kind": "feature_recipe", "target_id": str(g.fsv[1]), "expected_version": 1}]}
    moved = _post(client, g, f"/v1/projects/{g.project.id}/refs/champion_model", body, **{"If-Match": '"1"'})
    assert moved.status_code == 200, moved.text
    assert moved.json()["decision"]["supersedes_id"] == proposal["id"]
    after = client.get(f"/v1/decisions/{proposal['id']}", headers=_h(g)).json()
    assert after["effective_state"] == "superseded"
    # The proposal is resolved: accepting it again is an invalid transition.
    body["companion_moves"][0]["expected_version"] = 2
    again = _post(client, g, f"/v1/projects/{g.project.id}/refs/champion_model", body, **{"If-Match": '"2"'})
    _assert_envelope(again, 409, "invalid_decision_transition")


# --- model versions ----------------------------------------------------------------------------------


def test_model_version_detail_from_real_runs_and_champion_promotion(client, db_session, setup):  # noqa: F811
    db = db_session
    root = _root(client, setup, _dataset(client, setup)).json()
    assert _work(db, root["id"]).status == "completed"
    experiment = client.get(f"/v1/experiments/{root['id']}", headers=_h_setup(setup)).json()
    mv_id = db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == root["id"]))
    read = client.get(f"/v1/model-versions/{mv_id}", headers=_h_setup(setup))
    assert read.status_code == 200, read.text
    detail = read.json()
    assert read.headers["ETag"] == client.get(f"/v1/model-versions/{mv_id}", headers=_h_setup(setup)).headers["ETag"]
    metrics = detail["metrics"]
    assert metrics["cv"] and metrics["holdout"] == experiment["metrics"]["holdout"]
    assert metrics["decision_threshold"] == experiment["metrics"]["decision_threshold"] is not None
    assert metrics["constraint_status"] == experiment["metrics"]["constraint_status"]
    assert detail["family"] == metrics["family"] and detail["candidate_key"] == metrics["candidate_id"]
    lineage = detail["lineage"]
    assert lineage["experiment_id"] == root["id"]
    assert lineage["split_plan_id"] == experiment["lineage"]["split_plan_id"]
    assert lineage["source_dataset_id"] == experiment["lineage"]["source_dataset_id"]
    assert lineage["prepared_dataset_id"] == experiment["lineage"]["prepared_dataset_id"]
    assert detail["is_champion"] is True and detail["ref_kinds"] == ["champion_model"]  # refs.bootstrap.v1
    assert detail["artifacts"] and all(item["content_digest"] and item["id"] for item in detail["artifacts"])
    text = read.text
    assert "object_key" not in text and "artifact_uri" not in text and "bucket" not in text and "s3://" not in text

    # A branch on the same split plan is promoted over HTTP with the champion's ETag.
    branch = client.post(
        f"/v1/experiments/{root['id']}/branches",
        json={"intent": "drop boosting", "changes": [{"kind": "family_exclude", "family": "xgboost"}]},
        headers={**_h_setup(setup), "Idempotency-Key": _key()},
    ).json()
    assert _work(db, branch["id"]).status == "completed"
    child_mv = db.scalar(select(ModelVersion).where(ModelVersion.pipeline_run_id == branch["id"]))
    child = client.get(f"/v1/model-versions/{child_mv.id}", headers=_h_setup(setup)).json()
    assert child["is_champion"] is False and child["lineage"]["split_plan_id"] == lineage["split_plan_id"]
    project_id = setup["alpha_project"].id
    refs = {ref["ref_kind"]: ref for ref in
            client.get(f"/v1/projects/{project_id}/refs", headers=_h_setup(setup)).json()["items"]}
    companions = []
    if child["lineage"]["feature_recipe_id"] and "feature_recipe" in refs:
        companions = [{"ref_kind": "feature_recipe", "target_id": child["lineage"]["feature_recipe_id"],
                       "expected_version": refs["feature_recipe"]["version"]}]
    evidence = [{"kind": "model_version", "id": str(child_mv.id), "scope": "final_holdout"}]
    promoted = client.post(
        f"/v1/projects/{project_id}/refs/champion_model",
        json={"target_id": str(child_mv.id), "rationale": "branch wins on the same holdout",
              "evidence_refs": evidence, "companion_moves": companions},
        headers={**_h_setup(setup), "Idempotency-Key": _key(), "If-Match": refs["champion_model"]["etag"]},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.headers["ETag"] == '"2"'
    assert client.get(f"/v1/model-versions/{child_mv.id}", headers=_h_setup(setup)).json()["is_champion"] is True
    assert client.get(f"/v1/model-versions/{mv_id}", headers=_h_setup(setup)).json()["is_champion"] is False
    hidden = client.get(f"/v1/model-versions/{mv_id}",
                        headers=_headers(setup["beta_admin"], setup["beta"].id))
    _assert_envelope(hidden, 404, "not_found")


def _h_setup(setup) -> dict[str, str]:
    return _headers(setup["alpha_admin"], setup["alpha"].id)


def test_ref_move_races_foreign_references_and_key_scopes(client, db_session, g, setup, tmp_path, monkeypatch):
    db = db_session
    _bootstrap(db, g)
    _force_lock(db, g.exp[1])
    beta = _seed_graph(db, tmp_path, setup, "beta", [{}])
    from app.domain.decision_records import DecisionActor

    beta_record = drs.propose(db, workspace_id=beta.ws, project_id=beta.project.id,
                              actor=DecisionActor.human(beta.actor), decision_type="experiment_accepted",
                              subject_kind="experiment", subject_id=beta.exp[0], rationale="beta")
    db.commit()
    path = f"/v1/projects/{g.project.id}/refs/champion_model"
    recipe = {"ref_kind": "feature_recipe", "target_id": str(g.fsv[1]), "expected_version": 1}

    def body(**extra):
        return {"target_id": str(g.mv[1]), "rationale": "promote", "evidence_refs": _holdout(g, 1),
                "companion_moves": [recipe], **extra}

    before = _records(db)
    # A foreign project is 404 before any precondition (never 428).
    _assert_envelope(_post(client, g, f"/v1/projects/{beta.project.id}/refs/champion_model", body()), 404, "not_found")
    # A foreign proposal or companion target is never applied.
    _assert_envelope(_post(client, g, path, body(proposal_id=str(beta_record.id)), **{"If-Match": '"1"'}), 404,
                     "not_found")
    foreign = body(companion_moves=[{**recipe, "target_id": str(beta.fsv[0])}])
    error = _assert_envelope(_post(client, g, path, foreign, **{"If-Match": '"1"'}), 404, "ref_target_not_found")
    assert error["details"]["ref_kind"] == "feature_recipe"
    assert _records(db) == before

    # Two same-key requests both past the key lookup: the loser of the locked
    # version check replays the winner instead of 412.
    key = _key()
    winner = _post(client, g, path, body(), key=key, **{"If-Match": '"1"'})
    assert winner.status_code == 200, winner.text
    real, calls = idempotency_service.find_bound, []

    def first_lookup_misses(db, scope, binding):
        calls.append(scope.operation)
        return None if len(calls) == 1 else real(db, scope, binding)

    monkeypatch.setattr(idempotency_service, "find_bound", first_lookup_misses)
    twin = _post(client, g, path, body(), key=key, **{"If-Match": '"1"'})
    assert twin.status_code == 200 and twin.headers["Idempotent-Replayed"] == "true", twin.text
    assert twin.json()["decision"]["id"] == winner.json()["decision"]["id"] and len(calls) == 2
    monkeypatch.setattr(idempotency_service, "find_bound", real)
    # Another principal's identical key is its own key: no replay of the first's move.
    engineer = _member(db, g, WorkspaceRole.ML_ENGINEER)
    theirs = _post(client, g, path, body(), key=key, user=engineer, **{"If-Match": '"1"'})
    assert _assert_envelope(theirs, 412, "precondition_failed")["details"]["current_etag"] == '"2"'
    proposal_key = _key()
    mine = _post(client, g, f"/v1/projects/{g.project.id}/decisions", _proposal(g), key=proposal_key).json()
    other = _post(client, g, f"/v1/projects/{g.project.id}/decisions", _proposal(g), key=proposal_key, user=engineer)
    assert other.status_code == 201 and "Idempotent-Replayed" not in other.headers
    assert other.json()["id"] != mine["id"] and other.json()["actor"]["user_id"] == str(engineer.id)
