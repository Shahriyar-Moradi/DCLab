"""P5.4-A1a: sealed evidence mode (ADR 0007 §6.1, Alembic 0086).

A sealed run is a CV-only improve try: a real auto-train branch that ends at the winner
lock, scores nothing on the final test set, writes no predictions, ModelVersion or model
artifact, and still locks its CV evidence. Tests (1)-(6) of the ADR table run on one real
parent and one real sealed branch; (2) also pins the SQL predicate text. Migration
round trips (7) are covered by the Alembic catalog tests and the frozen fixture.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from alembic_frozen.rev_0043_evidence_lock import EVIDENCE_COMPLETE_SQL as FROZEN_0043_SQL
from app.api.v1_conventions import domain_error
from app.db.evidence_lock import (
    EVIDENCE_COMPLETE_SQL,
    SCORED_EVIDENCE_PREDICATE,
    SEALED_CLAUSE_SWAPS,
    SEALED_EVIDENCE_PREDICATE,
    sealed_predicate,
)
from app.db.models import (
    Artifact,
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    ExperimentTestPrediction,
    MlRunEvent,
    ModelEvaluation,
    ModelSelectionDecision,
    ModelVersion,
)
import app.engine.experiments.runner as runner
from app.domain.errors import ExperimentNotBranchableError
from app.domain.pipeline_run_branch import SEALED_FINAL_TEST_NOTE, ServiceOverrides
from app.engine.evaluation.comparability import selection_mismatch
from app.engine.investigate.checks import TEST_PARTITION_CHECKS
from app.engine.modeling.operating_points import curve_digest
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.auth_service import create_access_token
from app.services.auto_train_service import run_auto_train_job
from app.services.experiment_verdict_service import locked_run_evidence
from app.services.service_token_service import create_service_token
from app.services.evidence_lock_service import missing_scientific_evidence
from app.services.experiment_branch_service import branch_experiment, load_parent_context
from app.services.model_build_notebook import reproduction_notebook_bytes, reproduction_script_bytes
from app.services.model_build_reproduction_service import _reload_experiment, build_model_build_reproduction
from conftest import ENGINEER_PASSWORD
from test_experiment_branching import _other_tenant, _parent

_HEADER = (
    "\nCREATE OR REPLACE FUNCTION pipeline_run_scientific_evidence_complete(wanted_run uuid)\n"
    "RETURNS boolean AS $$\n"
)
_FOOTER = ";\n$$ LANGUAGE sql STABLE\n"


def _sealed_branch(db, actor, parent, monkeypatch, *, mutate_test_rows: bool = False) -> Experiment:
    # The improve loop (P5.4-A2) inserts its probes sealed through the service-only overrides.
    result = branch_experiment(
        db, actor=actor, workspace_id=parent.workspace_id, parent_id=parent.id,
        changes=[{"kind": "family_include", "family": "extra_trees"}],
        intent="improve try", service_overrides=ServiceOverrides(evidence_mode="sealed"),
    )
    assert result.experiment.evidence_mode == "sealed"

    def _never(*_args, **_kwargs):
        raise AssertionError("a sealed run never fits on or scores the final test set")

    real_candidates = runner._run_open_ingest_candidates

    def _scrambled_test(candidates, train, val, test, task, **kwargs):
        # Every final-test value (labels and features) reversed; the row ids stay.
        scrambled = test.copy()
        for column in scrambled.columns:
            if column != SOURCE_ROW_COLUMN:
                scrambled[column] = scrambled[column].iloc[::-1].to_numpy()
        return real_candidates(candidates, train, val, scrambled, task, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("app.engine.experiments.runner._fit_and_score_holdout", _never)
        if mutate_test_rows:
            patch.setattr(runner, "_run_open_ingest_candidates", _scrambled_test)
        run_auto_train_job(db, result.upload.id)
    db.expire_all()
    upload = db.get(ClientLabUpload, result.upload.id)
    assert upload.pipeline_status == "completed", upload.pipeline_log.get("reason")
    return db.get(Experiment, result.experiment.id)


def _complete(db, run_id) -> bool:
    return db.execute(text("SELECT pipeline_run_scientific_evidence_complete(:r)"), {"r": run_id}).scalar()


def _probe(db, run_id, *statements: str, disable: tuple[str, ...] = ()) -> bool:
    """The predicate after ``statements``, with the named lock triggers off; rolled back."""

    for name in disable:
        table = name.removesuffix("_evidence_locked").removesuffix("_lineage_guard")
        db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {name}"))
    for statement in statements:
        db.execute(text(statement), {"r": run_id})
    value = _complete(db, run_id)
    db.rollback()
    return value


def test_scored_branch_of_the_predicate_is_byte_identical_and_sealed_swaps_four_clauses():
    assert FROZEN_0043_SQL.startswith(_HEADER) and FROZEN_0043_SQL.endswith(_FOOTER)
    frozen_body = FROZEN_0043_SQL[len(_HEADER):-len(_FOOTER)]
    assert SCORED_EVIDENCE_PREDICATE == frozen_body
    assert EVIDENCE_COMPLETE_SQL.count(frozen_body) == 1
    assert sealed_predicate(frozen_body) == SEALED_EVIDENCE_PREDICATE
    assert len(SEALED_CLAUSE_SWAPS) == 4
    for clause, replacement in SEALED_CLAUSE_SWAPS:
        assert clause in frozen_body and clause not in SEALED_EVIDENCE_PREDICATE
        assert replacement.lstrip().startswith("AND NOT EXISTS")
    # Removing the four clauses from each side leaves the same shared text.
    scored_rest, sealed_rest = frozen_body, SEALED_EVIDENCE_PREDICATE
    for clause, replacement in SEALED_CLAUSE_SWAPS:
        scored_rest, sealed_rest = scored_rest.replace(clause, ""), sealed_rest.replace(replacement, "")
    assert scored_rest == sealed_rest
    with pytest.raises(RuntimeError, match="re-derive"):
        sealed_predicate(frozen_body.replace("experiment_test_predictions", "renamed_predictions"))


def test_sealed_run_is_cv_only_locked_and_refused_everywhere_a_holdout_is_needed(
    engineer_client, db_session, engineer_user, monkeypatch
):
    db = db_session
    parent = _parent(engineer_client, db, engineer_user, monkeypatch)
    child = _sealed_branch(db, engineer_user, parent, monkeypatch)

    # (1) COMPLETED + locked through the real pipeline; nothing of the final test exists.
    assert child.status == "COMPLETED" and child.scientific_evidence_locked_at is not None
    assert missing_scientific_evidence(db, child) == []
    assert child.result["holdout"] == {"scored": False, "reason": "improve_probe",
                                       "rows": child.result["split"]["n_test"]}
    assert not child.result["test_metrics"] and not child.result["test_predictions"]
    assert child.result["final_fit"] == {} and child.result["final_test_evaluation"]["evaluation_count"] == 0
    events = db.scalars(select(MlRunEvent.event_type).where(MlRunEvent.experiment_id == child.id)).all()
    assert "winner_locked" in events and not [name for name in events if name.startswith("final_")]
    candidate_ids = select(ExperimentCandidate.id).where(ExperimentCandidate.experiment_id == child.id)
    assert db.scalar(select(ModelEvaluation.id).where(
        ModelEvaluation.candidate_id.in_(candidate_ids), ModelEvaluation.evaluation_scope == "final_holdout")) is None
    assert db.scalar(select(ExperimentTestPrediction.id).where(ExperimentTestPrediction.experiment_id == child.id)) is None
    assert db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == child.id)) is None
    roles = {(row.extra_metadata or {}).get("role")
             for row in db.scalars(select(Artifact).where(Artifact.pipeline_run_id == child.id))}
    assert "model" not in roles and {"dependency_lock", "feature_manifest"} <= roles
    assert db.scalar(select(ModelSelectionDecision.id).where(ModelSelectionDecision.pipeline_run_id == child.id))
    assert load_parent_context(db, actor=engineer_user, workspace_id=child.workspace_id, parent_id=child.id)
    verification = child.result["deterministic_verification"]
    sealed_checks = {row["check_id"]: row["status"] for row in verification["checks"]
                     if row["check_id"] in {"final_fit_after_lock", "winner_only_final_test",
                                      "prediction_provenance_complete", "model_artifacts_persisted"}}
    assert set(sealed_checks.values()) == {"PASS"}, sealed_checks

    # (2) The installed function carries the unchanged scored branch; the predicate gates.
    installed = db.execute(text(
        "SELECT pg_get_functiondef('pipeline_run_scientific_evidence_complete(uuid)'::regprocedure)")).scalar()
    assert SCORED_EVIDENCE_PREDICATE in installed
    assert _complete(db, child.id) and _complete(db, parent.id)
    winner = child.result["selection"]["selected_candidate_id"]
    winner_id = db.scalar(select(ExperimentCandidate.id).where(
        ExperimentCandidate.experiment_id == child.id, ExperimentCandidate.candidate_key == winner))
    stray_holdout = (
        "INSERT INTO model_evaluations (id, workspace_id, project_id, candidate_id, evaluation_type, "
        "evaluation_scope, dataset_id, status, summary) SELECT gen_random_uuid(), workspace_id, project_id, "
        f"'{winner_id}', 'final_holdout', 'final_holdout', dataset_id, 'completed', '{{}}' "
        "FROM experiments WHERE id = :r"
    )
    assert _probe(db, child.id, stray_holdout, disable=("model_evaluations_evidence_locked",)) is False
    stray_prediction = (
        "INSERT INTO experiment_test_predictions (id, workspace_id, experiment_id, row_index, record_id, "
        "predicted_value) SELECT gen_random_uuid(), workspace_id, id, 0, '0', '1'::jsonb FROM experiments WHERE id = :r"
    )
    assert _probe(db, child.id, stray_prediction, disable=("experiment_test_predictions_evidence_locked",)) is False
    stray_model = (
        "INSERT INTO artifacts SELECT (jsonb_populate_record(NULL::artifacts, to_jsonb(a) || jsonb_build_object("
        "'id', gen_random_uuid(), 'object_key', a.object_key || '.stray', 'metadata', '{\"role\": \"model\"}'::jsonb))).* "
        "FROM artifacts AS a WHERE a.pipeline_run_id = :r AND a.metadata->>'role' = 'feature_manifest'"
    )
    assert _probe(db, child.id, stray_model) is False
    missing_fold = (
        "DELETE FROM model_evaluations WHERE id = (SELECT e.id FROM model_evaluations e JOIN experiment_candidates c "
        "ON c.id = e.candidate_id WHERE c.experiment_id = :r AND e.evaluation_scope = 'cv_fold' LIMIT 1)"
    )
    assert _probe(db, child.id, missing_fold, disable=(
        "model_evaluations_evidence_locked", "evaluation_metrics_evidence_locked")) is False
    # The same CV evidence read as a scored run lacks its holdout: a scored run without one blocks.
    as_scored = "UPDATE experiments SET evidence_mode = 'scored' WHERE id = :r"
    assert _probe(db, child.id, as_scored, disable=("experiments_lineage_guard",)) is False
    with pytest.raises(DBAPIError, match="evidence_mode is insert-only"):
        db.execute(text(as_scored), {"r": child.id})
    db.rollback()

    # (3) The test-partition checks never ran; multicollinearity did; overfit_gap had no final fit.
    checks = {row["check"]: row for row in child.result["investigation"]["checks"]}
    for name in TEST_PARTITION_CHECKS:
        assert checks[name]["status"] == "not_evaluated"
        assert checks[name]["evidence"]["not_evaluated_reason"] == "holdout_sealed"
    assert checks["multicollinearity"]["evidence"].get("not_evaluated_reason") not in {
        "holdout_sealed", "partition_unavailable", "check_error"}
    assert checks["overfit_gap"]["status"] == "not_evaluated"

    # (4) The reproduction script and notebook never name the holdout matrices.
    sealed_spec = build_model_build_reproduction(_reload_experiment(db, child))
    for blob in (reproduction_script_bytes(sealed_spec), reproduction_notebook_bytes(sealed_spec)):
        assert b"X_holdout" not in blob and b"holdout_scores" not in blob
        assert b"Final test set: not used (improve try)" in blob
    assert b"X_holdout" in reproduction_script_bytes(build_model_build_reproduction(_reload_experiment(db, parent)))

    # (5) A ModelVersion of a sealed run is refused at INSERT (so no champion, ref move or scoring).
    version = db.scalar(select(ModelVersion).where(ModelVersion.pipeline_run_id == parent.id))
    values = {attr.key: getattr(version, attr.key) for attr in ModelVersion.__mapper__.column_attrs}
    values.update(id=uuid4(), version="sealed-probe", pipeline_run_id=child.id, selected_candidate_id=winner_id)
    with pytest.raises(DBAPIError, match="sealed pipeline run"):
        db.add(ModelVersion(**values))
        db.flush()
    db.rollback()

    # (6) A service token cannot branch a sealed parent (a person can: load_parent_context above).
    with pytest.raises(ExperimentNotBranchableError) as refused:
        branch_experiment(
            db, actor=engineer_user, workspace_id=child.workspace_id, parent_id=child.id,
            changes=[{"kind": "family_include", "family": "gradient_boosting"}],
            intent="token branch", initiated_by_service_token_id=uuid4(),
        )
    error = domain_error(refused.value)
    assert (error.status_code, refused.value.code) == (403, "sealed_parent_human_only")


def test_sealed_evidence_ignores_the_final_test_rows_and_every_read_says_not_used(
    engineer_client, db_session, engineer_user, monkeypatch
):
    db = db_session
    parent = _parent(engineer_client, db, engineer_user, monkeypatch)
    first = _sealed_branch(db, engineer_user, parent, monkeypatch)
    second = _sealed_branch(db, engineer_user, parent, monkeypatch, mutate_test_rows=True)

    # Scrambling every final-test label and feature changes nothing a sealed run stores: the same
    # candidates, fingerprints, per-fold CV metrics (1e-9), winner, threshold and out-of-fold curve.
    evidence = [locked_run_evidence(db, run.workspace_id, run.id) for run in (first, second)]
    assert evidence[0] is not None and selection_mismatch(evidence[0], evidence[1]) is None
    assert selection_mismatch(evidence[1], evidence[0]) is None
    for key in ("operating_curve", "decision_threshold", "selection", "holdout"):
        left, right = first.result[key], second.result[key]
        if key == "selection":
            left, right = ({k: v for k, v in side.items() if k != "locked_at"} for side in (left, right))
        assert (curve_digest(left), key) == (curve_digest(right), key)

    # Readers: the technical report and the /v1 reads say "not used", never a value.
    report = first.result["technical_report"]
    assert report["holdout"]["note"] == SEALED_FINAL_TEST_NOTE and report["holdout"]["scored"] is False
    assert report["artifacts"]["model"] is None and report["artifacts"]["predictions"] is None
    human = engineer_client.get(f"/v1/experiments/{first.id}")
    assert human.status_code == 200, human.text
    body = human.json()
    assert (body["evidence_mode"], body["evidence_note"]) == ("sealed", SEALED_FINAL_TEST_NOTE)
    assert body["metrics"]["holdout"] == {} and body["diff_vs_parent"]["evidence_mode"] == {
        "parent": "scored", "child": "sealed"}
    assert body["diff_vs_parent"]["holdout"] == {}
    scored = engineer_client.get(f"/v1/experiments/{parent.id}").json()
    assert (scored["evidence_mode"], scored["evidence_note"]) == ("scored", None) and scored["metrics"]["holdout"]
    listed = {item["id"]: item["evidence_mode"] for item in engineer_client.get(
        "/v1/experiments", params={"project_id": str(parent.project_id)}).json()["items"]}
    assert listed[str(first.id)] == "sealed" and listed[str(parent.id)] == "scored"
    compared = engineer_client.get("/v1/experiments/compare", params={"ids": f"{parent.id},{first.id}"})
    assert compared.status_code == 200, compared.text
    items = {item["experiment_id"]: item for item in compared.json()["experiments"]}
    assert items[str(first.id)]["evidence_mode"] == "sealed" and items[str(first.id)]["holdout"] == {}
    assert compared.json()["common"]["holdout"] == []
    verdict = engineer_client.get(f"/v1/experiments/{first.id}/verdict").json()
    assert verdict["test_set_looks"]["experiment_look"] is None  # a sealed try is never a look
    assert verdict["test_set_looks"]["against_look"] is not None

    # A service token reads the same evidence mode, and cannot branch the sealed try (HTTP 403).
    token = create_service_token(db, creator=engineer_user, workspace_id=first.workspace_id, name="d4b",
                                 scopes=("read", "experiments:write"), expires_in_days=1,
                                 current_password=ENGINEER_PASSWORD)[1]
    db.commit()
    bearer = {"Authorization": f"Bearer {token}"}
    as_token = engineer_client.get(f"/v1/experiments/{first.id}", headers=bearer)
    assert as_token.status_code == 200, as_token.text
    assert (as_token.json()["evidence_mode"], as_token.json()["evidence_note"]) == ("sealed", SEALED_FINAL_TEST_NOTE)
    refused = engineer_client.post(
        f"/v1/experiments/{first.id}/branches", headers={**bearer, "Idempotency-Key": "d4b-token-branch"},
        json={"intent": "token branch", "changes": [{"kind": "family_include", "family": "gradient_boosting"}]})
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "sealed_parent_human_only"
    # Another tenant's member: 404 on the sealed run's read and branch.
    other_workspace, other_user = _other_tenant(db)
    other = {"Authorization": f"Bearer {create_access_token(other_user)}", "X-Workspace-Id": str(other_workspace.id)}
    assert engineer_client.get(f"/v1/experiments/{first.id}", headers=other).status_code == 404
    assert engineer_client.get(f"/v1/experiments/{first.id}/verdict", headers=other).status_code == 404
    assert engineer_client.post(
        f"/v1/experiments/{first.id}/branches", headers={**other, "Idempotency-Key": "d4b-other-branch"},
        json={"intent": "x", "changes": [{"kind": "family_include", "family": "gradient_boosting"}]}).status_code == 404
