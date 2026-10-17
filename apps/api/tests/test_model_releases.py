"""P7.2-A: model releases — the immutable approval behind "model in use" (approved for scoring).

Putting a model in use (``move_ref`` of ``champion_model``, the existing "Put this model in use"
route) writes the accepted decision, moves the ref and inserts one release in one transaction;
a refusal leaves nothing. Releases are append-only and tenant-keyed; reads drop the approving
person's id for service tokens. Scoring with the model in use carries its release, and the
worker re-checks it against the package manifest (no TOCTOU; test_v1_batch_predictions.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import (
    Artifact,
    BatchPrediction,
    ModelRelease,
    ModelVersion,
    ProjectDecisionRecord,
    ProjectRef,
    WorkspaceRole,
)
from app.domain.errors import InvalidDecisionRecordError, InvalidDecisionTransitionError, RefVersionConflictError
from app.services import decision_record_service as drs
from app.services import model_release_service as mrs
from app.services import project_ref_service as prs
from app.services.project_ref_service import RefMove
from app.services.service_token_service import create_service_token
from test_decision_record_service import _bootstrap, _force_lock, _holdout, g, setup  # noqa: F401 (fixtures)
from test_graph_service import _seed_graph
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_decisions_models import _h, _key, _member


def _releases(db, g) -> list[ModelRelease]:
    db.expire_all()
    return list(db.scalars(select(ModelRelease).where(ModelRelease.project_id == g.project.id)
                           .order_by(ModelRelease.created_at, ModelRelease.id)))


def _count(db, model) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model))


def _manifest_digest(db, mv_id) -> str:
    model = db.get(ModelVersion, mv_id)
    return db.get(Artifact, model.feature_manifest_artifact_id).content_digest


def _champion_version(db, g) -> int:
    db.expire_all()
    return db.scalar(select(ProjectRef.version).where(ProjectRef.project_id == g.project.id,
                                                      ProjectRef.ref_kind == "champion_model"))


def _data_refs_record(db, g) -> ProjectDecisionRecord:
    """``_bootstrap``'s first record (``ref_moved``, data refs): a real record of the project no release uses."""

    return db.scalar(select(ProjectDecisionRecord).where(ProjectDecisionRecord.project_id == g.project.id,
                                                         ProjectDecisionRecord.decision_type == "ref_moved"))


def _constraint(caught) -> str:
    return caught.value.orig.diag.constraint_name


def _promote(db, g, i: int, *, key: str | None = None, rationale: str | None = None) -> prs.RefMoveResult:
    refs = prs.current_refs(db, workspace_id=g.ws, project_id=g.project.id)
    result = prs.move_ref(
        db, workspace_id=g.ws, project_id=g.project.id, actor=g.human,
        moves=[RefMove("champion_model", g.mv[i], refs["champion_model"].version),
               RefMove("feature_recipe", g.fsv[i], refs["feature_recipe"].version)],
        rationale=rationale or f"put v{i + 1} in use", evidence_refs=_holdout(g, i), idempotency_key=key,
    )
    db.commit()
    return result


# --- approve = put in use, one transaction ---------------------------------------------------------


def test_putting_a_model_in_use_writes_one_release_with_its_record_and_ref_move(db_session, g):
    db = db_session
    first = _bootstrap(db, g)  # a person's first champion (from: null)
    [release] = _releases(db, g)
    assert (release.model_version_id, release.decision_record_id, release.approved_by_user_id) == (
        g.mv[0], first.record.id, g.actor.id)
    assert first.record.decision_type == "champion_promoted" and release.previous_release_id is None
    assert release.package_manifest_digest == _manifest_digest(db, g.mv[0]) and release.saved_format == "package_v1"
    assert release.rationale == "first champion" == first.record.rationale
    assert mrs.current_release(db, workspace_id=g.ws, project_id=g.project.id).id == release.id

    # Moving to another model approves it: the chain links the previous approval.
    _force_lock(db, g.exp[1])
    key = f"p72a-{uuid4().hex}"
    second = _promote(db, g, 1, key=key)
    chain = _releases(db, g)
    assert [r.model_version_id for r in chain] == [g.mv[0], g.mv[1]]
    assert chain[1].previous_release_id == chain[0].id and chain[1].decision_record_id == second.record.id
    current = mrs.current_release(db, workspace_id=g.ws, project_id=g.project.id)
    assert current.id == chain[1].id
    assert mrs.release_for_version(db, workspace_id=g.ws, model_version=db.get(ModelVersion, g.mv[0])) is None

    # An idempotent replay writes nothing new.
    replay = _promote(db, g, 1, key=key)
    assert replay.replayed and replay.record.id == second.record.id and len(_releases(db, g)) == 2

    # Rollback (P7.5-A) = approve the previous version again: a new row, never an edit.
    _promote(db, g, 0)
    chain = _releases(db, g)
    assert [r.model_version_id for r in chain] == [g.mv[0], g.mv[1], g.mv[0]]
    assert chain[2].previous_release_id == chain[1].id
    assert mrs.current_release(db, workspace_id=g.ws, project_id=g.project.id).id == chain[2].id

    # The approval is ref history: corrected by a new move, never superseded in place.
    with pytest.raises(InvalidDecisionTransitionError):
        drs.supersede(db, workspace_id=g.ws, project_id=g.project.id, record_id=second.record.id,
                      actor=g.human, rationale="correct it")
    db.rollback()


def test_a_refused_approval_leaves_no_record_release_or_ref_move(db_session, g, monkeypatch):
    db = db_session
    _bootstrap(db, g)
    # An unlocked version is refused before any write (the existing champion rule).
    releases = _count(db, ModelRelease)
    with pytest.raises(InvalidDecisionRecordError) as unlocked:
        _promote(db, g, 1)
    db.rollback()
    assert unlocked.value.reason == "champion_not_locked" and _count(db, ModelRelease) == releases
    _force_lock(db, g.exp[1])
    model_artifact = db.get(ModelVersion, g.mv[1]).model_artifact_id
    records, releases = _count(db, ProjectDecisionRecord), _count(db, ModelRelease)
    champion = db.scalar(select(ProjectRef.version).where(ProjectRef.project_id == g.project.id,
                                                          ProjectRef.ref_kind == "champion_model"))

    def refused(reason: str) -> None:
        with pytest.raises(InvalidDecisionRecordError) as caught:
            _promote(db, g, 1)
        db.rollback()
        assert caught.value.reason == reason
        assert (_count(db, ProjectDecisionRecord), _count(db, ModelRelease)) == (records, releases)
        assert db.scalar(select(ProjectRef.version).where(ProjectRef.project_id == g.project.id,
                                                          ProjectRef.ref_kind == "champion_model")) == champion

    # A legacy pickle (no package format) is quarantined unless the server allows legacy models.
    db.execute(text("UPDATE artifacts SET metadata = '{\"role\": \"model\"}' WHERE id = :id"), {"id": model_artifact})
    db.commit()
    monkeypatch.setenv("LEGACY_PICKLE_MODELS_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        refused("legacy_model_quarantined")
        monkeypatch.setenv("LEGACY_PICKLE_MODELS_ENABLED", "true")
        get_settings.cache_clear()
        _promote(db, g, 1)  # explicitly allowed: approved, and the release says how it is saved
        assert _releases(db, g)[-1].saved_format == "legacy_quarantined"
    finally:
        get_settings.cache_clear()

    # A manifest digest the release cannot pin (not lowercase 64-hex) is no package: 422, not a 500.
    records, releases = _count(db, ProjectDecisionRecord), _count(db, ModelRelease)
    champion = _champion_version(db, g)
    manifest_id = db.get(ModelVersion, g.mv[0]).feature_manifest_artifact_id
    real_artifact = mrs._artifact

    def malformed(db, workspace_id, artifact_id):  # artifacts.content_digest is immutable: read it malformed
        row = real_artifact(db, workspace_id, artifact_id)
        return SimpleNamespace(content_digest="F" * 64) if artifact_id == manifest_id else row

    monkeypatch.setattr(mrs, "_artifact", malformed)
    with pytest.raises(InvalidDecisionRecordError) as caught:
        _promote(db, g, 0)
    db.rollback()
    monkeypatch.setattr(mrs, "_artifact", real_artifact)
    assert caught.value.reason == "model_package_missing" and caught.value.status_code == 422
    assert (_count(db, ProjectDecisionRecord), _count(db, ModelRelease), _champion_version(db, g)) == (
        records, releases, champion)

    # No stored model package: refused the same way (nothing written).
    records, releases = _count(db, ProjectDecisionRecord), _count(db, ModelRelease)
    champion = db.scalar(select(ProjectRef.version).where(ProjectRef.project_id == g.project.id,
                                                          ProjectRef.ref_kind == "champion_model"))
    db.execute(text("UPDATE artifacts SET artifact_type = 'report' WHERE id = :id"),
               {"id": db.get(ModelVersion, g.mv[0]).model_artifact_id})
    db.commit()
    with pytest.raises(InvalidDecisionRecordError) as caught:
        _promote(db, g, 0)
    db.rollback()
    assert caught.value.reason == "model_package_missing"
    assert (_count(db, ProjectDecisionRecord), _count(db, ModelRelease)) == (records, releases)
    assert db.scalar(select(ProjectRef.version).where(ProjectRef.project_id == g.project.id,
                                                      ProjectRef.ref_kind == "champion_model")) == champion

    # A failure while writing the release (after the record was flushed) rolls back the record too.
    db.execute(text("UPDATE artifacts SET artifact_type = 'model' WHERE id = :id"),
               {"id": db.get(ModelVersion, g.mv[0]).model_artifact_id})
    db.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("release write failed")

    monkeypatch.setattr(prs, "insert_release", boom)
    with pytest.raises(RuntimeError):
        _promote(db, g, 0)
    db.rollback()
    assert (_count(db, ProjectDecisionRecord), _count(db, ModelRelease)) == (records, releases)
    assert db.scalar(select(ProjectRef.version).where(ProjectRef.project_id == g.project.id,
                                                      ProjectRef.ref_kind == "champion_model")) == champion


# --- DB guarantees -----------------------------------------------------------------------------------


def test_releases_are_append_only_and_tenant_keyed(db_session, g, setup):
    db = db_session
    _bootstrap(db, g)
    [release] = _releases(db, g)
    for statement in ("UPDATE model_releases SET rationale = 'edited' WHERE id = :id",
                      "DELETE FROM model_releases WHERE id = :id"):
        with pytest.raises(DBAPIError):
            db.execute(text(statement), {"id": release.id})
        db.rollback()
    # A real record of the project that no release uses, so each insert below fails on the
    # constraint it names (not on the decision-record FK or its unique).
    spare = _data_refs_record(db, g).id
    tenant_fks = {"fk_model_releases_workspace_project", "fk_model_releases_workspace_project_model_version",
                  "fk_model_releases_workspace_project_previous_release",
                  "fk_model_releases_workspace_project_decision_record"}

    def insert(expected: str | set[str], **overrides) -> None:
        values = dict(id=uuid4(), workspace_id=g.ws, project_id=g.project.id, model_version_id=g.mv[0],
                      previous_release_id=release.id, decision_record_id=spare,
                      approved_by_user_id=g.actor.id, rationale="x", package_manifest_digest="a" * 64,
                      saved_format="package_v1")
        with pytest.raises(IntegrityError) as caught:
            db.add(ModelRelease(**{**values, **overrides}))
            db.flush()
        db.rollback()
        assert _constraint(caught) in ({expected} if isinstance(expected, str) else expected)

    beta_project = setup["beta_project"]
    insert(tenant_fks, project_id=beta_project.id)  # another tenant's project with this workspace
    insert(tenant_fks, workspace_id=setup["beta"].id)  # the same ids under another workspace
    insert("fk_model_releases_workspace_project_model_version", model_version_id=uuid4())
    # One approval per decision record.
    insert("uq_model_releases_decision_record_id", decision_record_id=release.decision_record_id)
    insert("uq_model_releases_project_first", previous_release_id=None)  # a second "first" release
    insert("ck_model_releases_digest", package_manifest_digest="A" * 64)
    same = uuid4()
    insert("ck_model_releases_previous_not_self", id=same, previous_release_id=same)

    # The chain stays linear: a release has at most one successor.
    _force_lock(db, g.exp[1])
    _promote(db, g, 1)
    first, second = _releases(db, g)
    assert second.previous_release_id == first.id
    insert("uq_model_releases_previous_release_id", previous_release_id=first.id)


def test_a_concurrent_approval_is_a_ref_conflict_and_writes_nothing(db_session, g, monkeypatch):
    """Two people approving at once both read the same chain head; the loser collides on the
    chain's unique indexes and gets ``release_created_concurrently`` (409), with nothing written."""

    db = db_session
    _bootstrap(db, g)
    _force_lock(db, g.exp[1])
    _promote(db, g, 1)
    first, second = _releases(db, g)
    records, releases, champion = _count(db, ProjectDecisionRecord), _count(db, ModelRelease), _champion_version(db, g)
    for stale in (first, None):  # the head before the other approval committed; no head at all
        monkeypatch.setattr(mrs, "latest_release", lambda db, *, workspace_id, project_id, head=stale: head)
        with pytest.raises(RefVersionConflictError) as caught:
            _promote(db, g, 0)
        db.rollback()
        assert (caught.value.reason, caught.value.status_code) == ("release_created_concurrently", 409)
        assert (_count(db, ProjectDecisionRecord), _count(db, ModelRelease), _champion_version(db, g)) == (
            records, releases, champion)
    monkeypatch.undo()
    assert [r.id for r in _releases(db, g)] == [first.id, second.id]


def test_a_scoring_row_carries_only_a_release_of_its_own_version_and_tenant(db_session, g, setup, tmp_path):
    db = db_session
    _bootstrap(db, g)
    [release] = _releases(db, g)  # approves g.mv[0]
    beta = _seed_graph(db, tmp_path, setup, "beta", [{}])
    db.commit()

    def scoring(**values) -> BatchPrediction:
        row = dict(workspace_id=g.ws, project_id=g.project.id, model_version_id=g.mv[0], model_release_id=release.id,
                   input_dataset_id=g.source.id, status="queued", output_format="csv")
        return BatchPrediction(**{**row, **values})

    db.add(scoring())  # its own version's approval: accepted
    db.flush()
    db.rollback()
    for values in (
        {"model_version_id": g.mv[1]},  # same workspace, another version: the approval is not for it
        {"workspace_id": beta.ws, "project_id": beta.project.id, "model_version_id": beta.mv[0],
         "input_dataset_id": beta.source.id},  # another tenant's (valid) row naming this release
    ):
        with pytest.raises(IntegrityError) as caught:
            db.add(scoring(**values))
            db.flush()
        db.rollback()
        assert _constraint(caught) == "fk_batch_predictions_workspace_model_release"


# --- /v1 ---------------------------------------------------------------------------------------------


def test_put_in_use_route_is_human_only_and_releases_read_is_shaped(client, db_session, g, setup):
    db = db_session
    _bootstrap(db, g)
    _force_lock(db, g.exp[1])
    refs_path = f"/v1/projects/{g.project.id}/refs/champion_model"
    body = {"target_id": str(g.mv[1]), "rationale": "<b>better</b> recall on CV", "evidence_refs": _holdout(g, 1),
            "companion_moves": [{"ref_kind": "feature_recipe", "target_id": str(g.fsv[1]), "expected_version": 1}]}
    before = _count(db, ModelRelease)

    token = create_service_token(db, creator=g.actor, workspace_id=g.ws, name="agent", scopes=["read"],
                                 expires_in_days=1, current_password="test-password")[1]
    db.commit()
    as_token = {"Authorization": f"Bearer {token}"}
    refused = client.post(refs_path, json=body, headers={**as_token, "Idempotency-Key": _key(), "If-Match": '"1"'})
    assert refused.status_code == 403, refused.text
    viewer = _member(db, g, WorkspaceRole.VIEWER)
    _assert_envelope(client.post(refs_path, json=body, headers=_h(g, viewer, key=_key(), **{"If-Match": '"1"'})), 403)
    beta = client.post(f"/v1/projects/{g.project.id}/refs/champion_model", json=body,
                       headers=_h(g, setup["beta_admin"], key=_key(), ws=setup["beta"].id, **{"If-Match": '"1"'}))
    assert beta.status_code == 404, beta.text
    assert _count(db, ModelRelease) == before

    moved = client.post(refs_path, json=body, headers=_h(g, key=_key(), **{"If-Match": '"1"'}))
    assert moved.status_code == 200, moved.text
    record_id = moved.json()["decision"]["id"]

    releases = client.get(f"/v1/projects/{g.project.id}/releases", headers=_h(g))
    assert releases.status_code == 200, releases.text
    page = releases.json()
    newest, first = page["items"]
    assert page["current_release_id"] == newest["id"] and newest["is_current"] and not first["is_current"]
    assert (newest["model_version_id"], newest["decision_record_id"], newest["previous_release_id"]) == (
        str(g.mv[1]), record_id, first["id"])
    assert newest["approved_by_user_id"] == str(g.actor.id) and newest["model_version"] == "v2"
    assert newest["rationale"] == "<b>better</b> recall on CV"  # stored and returned as plain data
    assert newest["package_manifest_digest"] == _manifest_digest(db, g.mv[1])

    by_token = client.get(f"/v1/projects/{g.project.id}/releases", headers=as_token)
    assert by_token.status_code == 200 and [item["approved_by_user_id"] for item in by_token.json()["items"]] == [
        None, None]
    other = client.get(f"/v1/projects/{g.project.id}/releases",
                       headers=_h(g, setup["beta_admin"], ws=setup["beta"].id))
    assert other.status_code == 404

    model = client.get(f"/v1/model-versions/{g.mv[1]}", headers=_h(g)).json()
    assert model["release"]["id"] == newest["id"] and model["release"]["is_current"]
    assert "approved_by_user_id" not in model["release"]
    assert client.get(f"/v1/model-versions/{g.mv[0]}", headers=_h(g)).json()["release"] is None

    # The approval decision: people see who acted; agents (tokens) never get the person's id.
    for path in (f"/v1/decisions/{record_id}", f"/v1/projects/{g.project.id}/decisions"):
        human, agent = client.get(path, headers=_h(g)).json(), client.get(path, headers=as_token).json()
        humans = human.get("items", [human])
        assert str(g.actor.id) in {item["actor"]["user_id"] for item in humans}
        assert {item["actor"]["user_id"] for item in agent.get("items", [agent])} == {None}
        assert [item["actor"]["kind"] for item in agent.get("items", [agent])] == [i["actor"]["kind"] for i in humans]

    # A reason that names the final holdout: people read it; agent views (tokens, catalog) withhold it.
    reason = "final_holdout AUC 0.91 beats v2"
    _promote(db, g, 0, rationale=reason)
    human_newest = client.get(f"/v1/projects/{g.project.id}/releases", headers=_h(g)).json()["items"][0]
    token_newest = client.get(f"/v1/projects/{g.project.id}/releases", headers=as_token).json()["items"][0]
    assert human_newest["rationale"] == reason and human_newest["id"] == token_newest["id"]
    assert token_newest["rationale"] is None and token_newest["approved_by_user_id"] is None
    from app.agents.tools.definitions.reads import release_summary
    from app.domain.model_releases import ModelReleaseRead

    assert release_summary(ModelReleaseRead.model_validate(human_newest))["rationale"] is None
    plain = release_summary(ModelReleaseRead.model_validate({**human_newest, "rationale": "recall on CV"}))["rationale"]
    assert (plain.text, plain.origin) == ("recall on CV", "dataset")  # untrusted data text
