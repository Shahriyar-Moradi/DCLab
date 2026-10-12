"""P5.5-B-read: GET /v1/experiments/{id}/verdict over seeded, evidence-locked runs on one split plan.

The verdict reads the locked ``cv_fold`` evaluation rows (never ``experiments.result``, never a
final-evaluation value) and corrects for the new candidates a try added; the look count is
conservative and holdout-blind; tenancy, validation and token shaping hold."""

from __future__ import annotations

import math
import re
import typing
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from scipy.stats import t as student_t
from sqlalchemy import select, text, update

from app.agents.tools.shaping import strip_holdout
from app.db.models import (
    CVFoldRun,
    DataQualityFinding,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    MlRunEvent,
    ModelEvaluation,
    ModelSelectionDecision,
)
from app.domain.experiment_verdict import LOOK_COUNTING, SplitPlanLooksRead
from app.services import experiment_verdict_service as service
from app.services.service_token_service import create_service_token
from test_decision_record_service import _force_lock, setup  # noqa: F401 (fixture)
from test_graph_service import _headers, _seed_graph, _split_plan
from test_v1_contract_conventions import _assert_envelope

HOLDOUT = re.compile(r"holdout|final_test", re.IGNORECASE)
PARENT = [0.60, 0.90, 0.70, 0.85, 0.65]
GAIN = [0.03, 0.031, 0.029, 0.03, 0.03]
CHILD = [p + g for p, g in zip(PARENT, GAIN)]
BASE = datetime(2026, 9, 3, tzinfo=UTC)
NB_K5 = math.sqrt(1 / 5 + 1 / 4)
SD_GAIN = math.sqrt(sum((x - 0.03) ** 2 for x in GAIN) / 4)


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def _add_scores(db, g, cand, scores, metric, *, order=None, failed_duplicate=False) -> None:
    for n in order or range(1, len(scores) + 1):
        db.add(CVFoldRun(workspace_id=g.ws, project_id=g.project.id, candidate_id=cand, fold_number=n,
                         train_row_count=400, validation_row_count=100, status="completed"))
        rows = [("completed", scores[n - 1])] + ([("failed", 0.0)] if failed_duplicate and n == 1 else [])
        for status, value in rows:
            evaluation = ModelEvaluation(workspace_id=g.ws, project_id=g.project.id, candidate_id=cand,
                                         evaluation_type="cross_validation", evaluation_scope="cv_fold",
                                         dataset_id=g.prepared.id, status=status,
                                         summary={"fold_number": n, "validation_row_count": 100})
            db.add(evaluation)
            db.flush()
            db.add(EvaluationMetric(model_evaluation_id=evaluation.id, metric_name=metric, metric_value=value))


def _final(db, g, run: int, metric="roc_auc") -> None:
    final = ModelEvaluation(workspace_id=g.ws, project_id=g.project.id, candidate_id=g.cand[run],
                            evaluation_type="final_holdout", evaluation_scope="final_holdout",
                            dataset_id=g.prepared.id, status="completed", summary={})
    db.add(final)
    db.flush()
    db.add(EvaluationMetric(model_evaluation_id=final.id, metric_name=metric, metric_value=0.5 + run / 10))


def _select(db, g, run: int, metric, locked_at) -> None:
    db.add(ModelSelectionDecision(workspace_id=g.ws, project_id=g.project.id, pipeline_run_id=g.exp[run],
                                  selected_candidate_id=g.cand[run], selection_metric=metric, selected_score=0.8,
                                  selection_policy="test", locked_at=locked_at))


@pytest.fixture
def plan(db_session, tmp_path, setup):  # noqa: F811
    return _plan(db_session, tmp_path, setup)


def _plan(db, tmp_path, setup, *, side="alpha", parent=PARENT, child=CHILD, metric="roc_auc",  # noqa: F811
          child_metric=None, child_new=(), findings=()):
    """e0 root; e1 branch of e0 (fold rows inserted in reverse, plus a failed duplicate of fold 1); e2 a
    sibling with a winner and a final row; e3 failed after final_test_started; e4 queued; e5 COMPLETED with
    a selection row only, not locked; e6 failed with a final-evaluation row only; e7 on another split plan.
    Looks: e6 (1, creation order), e0 (2), e2 (3), e1 (4), e3 (5), e5 (6)."""

    g = _seed_graph(db, tmp_path, setup, side, [{}, {"parent": 0}, {"parent": 0}, {"parent": 0}, {"parent": 0},
                                                 {"parent": 0}, {"parent": 0}, {"legacy": True}])
    g.cand = {i: db.scalar(select(ExperimentCandidate.id).where(ExperimentCandidate.experiment_id == g.exp[i]))
              for i in range(8)}
    child_metric = child_metric or metric
    for i, (key, family) in enumerate(child_new):  # candidates the branch added (trained)
        db.add(ExperimentCandidate(workspace_id=g.ws, project_id=g.project.id, experiment_id=g.exp[1],
                                   candidate_key=key, fingerprint=f"new{i}".ljust(40, "0"), status="trained",
                                   payload={}, model_family=family, algorithm=family, feature_set_version_id=g.fsv[1]))
    _add_scores(db, g, g.cand[0], parent, metric)
    _add_scores(db, g, g.cand[1], child, child_metric, order=range(len(child), 0, -1), failed_duplicate=True)
    _add_scores(db, g, g.cand[5], parent, metric)
    for run, locked_at in ((0, BASE), (2, BASE + timedelta(hours=1)), (1, BASE + timedelta(hours=2))):
        _select(db, g, run, child_metric if run == 1 else metric, locked_at)
        _final(db, g, run, metric)
    _select(db, g, 5, metric, BASE + timedelta(hours=4))
    _final(db, g, 6, metric)
    for check, status in findings:
        db.add(DataQualityFinding(workspace_id=g.ws, project_id=g.project.id, pipeline_run_id=g.exp[1],
                                  dataset_id=g.prepared.id, finding_type=check, severity="critical",
                                  evidence={"source": "investigate", "check": check, "status": status}))
    for run, status in ((3, "FAILED"), (4, "QUEUED"), (6, "FAILED")):
        db.execute(update(Experiment).where(Experiment.id == g.exp[run]).values(status=status))
    g.plan2 = _split_plan(db, tmp_path, workspace_id=g.ws, project_id=g.project.id, dataset_id=g.source.id, version=2)
    db.execute(update(Experiment).where(Experiment.id == g.exp[7]).values(split_plan_id=g.plan2.id,
                                                                         source_dataset_id=g.source.id))
    workflow_run_id = db.scalar(select(Experiment.workflow_run_id).where(Experiment.id == g.exp[3]))
    db.add(MlRunEvent(workspace_id=g.ws, workflow_run_id=workflow_run_id, experiment_id=g.exp[3], sequence=1,
                      stage="final_test", event_type="final_test_started", status="started",
                      timestamp=BASE + timedelta(hours=3), payload={}))
    db.commit()
    _force_lock(db, g.exp[0], g.exp[1], g.exp[2])
    g.headers = _headers(g.actor, g.ws)
    return g


def _verdict(client, g, eid, **params):
    return client.get(f"/v1/experiments/{eid}/verdict", params=params, headers=g.headers)


def test_branch_beats_its_parent_from_locked_folds(client, plan):
    g = plan
    response = _verdict(client, g, g.exp[1])
    assert response.status_code == 200, response.text
    assert response.headers["ETag"] and response.headers["Cache-Control"] == "private, no-store"
    assert {v.strip() for v in response.headers["Vary"].split(",")} == {"Authorization", "Cookie", "Origin"}
    body = response.json()
    assert (body["verdict"], body["reason"], body["metric"], body["direction"]) == (
        "beats_the_noise", None, "roc_auc", "higher_is_better")
    assert body["against_experiment_id"] == str(g.exp[0]) and body["against_is_parent"] is True
    assert body["outcome_scope"] == "cv" and body["basis"] == "locked_cv_fold_evaluations"
    assert body["folds"] == 5 and body["mean_difference"] == pytest.approx(0.03)
    # Same candidate keys (a column change): T = 1, although five experiments on the plan count.
    assert (body["comparisons"], body["new_candidates"], body["comparisons_on_plan"]) == (1, 0, 5)
    assert body["margin"] == pytest.approx(float(student_t.ppf(0.95, 4)) * SD_GAIN * NB_K5)
    assert body["minimum_effect_basis"] == "relative"
    assert [f["fold_number"] for f in body["fold_scores"]] == [1, 2, 3, 4, 5]
    assert [f["difference"] for f in body["fold_scores"]] == pytest.approx(GAIN)
    assert body["message"].startswith("This try's model scores ROC AUC higher by 0.03")
    assert "it beats the noise for this one comparison (evidence, not proof)." in body["message"]
    assert body["message"].endswith(
        "Best of 5 tries on the same folds: the cross-validation number is a little optimistic, and each try also "
        "scored the final test set once, so no single number here is a fresh estimate.")
    assert "honest estimate" not in body["message"]
    assert body["test_set_looks"] == {"split_plan_id": str(g.plan.id), "looks_on_plan": 6, "experiment_look": 4,
                                      "against_look": 2, "counting": LOOK_COUNTING}
    reverse = _verdict(client, g, g.exp[0], against=str(g.exp[1])).json()
    assert reverse["verdict"] == "worse" and reverse["against_is_parent"] is False
    assert reverse["mean_difference"] == pytest.approx(-0.03)


def test_new_candidates_set_the_multiplicity(client, db_session, tmp_path, setup):  # noqa: F811
    g = _plan(db_session, tmp_path, setup, child_new=[("extra_trees", "extra_trees"), ("hgb", "hist_gradient_boosting"),
                                                      ("majority", "majority")])
    body = _verdict(client, g, g.exp[1]).json()
    assert (body["new_candidates"], body["comparisons"]) == (2, 2)  # the dummy baseline is not a try
    assert body["t_quantile"] == pytest.approx(float(student_t.ppf(1 - 0.05 / 2, 4)))
    assert body["margin"] == pytest.approx(float(student_t.ppf(1 - 0.05 / 2, 4)) * SD_GAIN * NB_K5)
    assert "This try added 2 new models" in body["message"]
    assert service.new_candidate_count(db_session, g.ws, g.exp[0], g.exp[1]) == 0


def test_verdict_ignores_the_result_cache_and_final_evaluation_values(client, db_session, plan):
    g, db = plan, db_session
    before = _verdict(client, g, g.exp[1]).json()
    fake_folds = [{"fold_number": n, "metrics": {"roc_auc": 0.1}} for n in range(1, 6)]
    for i in (0, 1):  # experiments.result stays writable after the lock: it must never be the source
        db.execute(update(Experiment).where(Experiment.id == g.exp[i]).values(result={
            "candidates": [{"candidate_id": "x", "status": "trained", "folds": fake_folds, "cv_mean": {"roc_auc": 0.1}}],
            "selection": {"selection_metric": "pr_auc"}, "test_metrics": {"roc_auc": 0.99}}))
    db.commit()
    assert _verdict(client, g, g.exp[1]).json() == before
    db.execute(text("ALTER TABLE evaluation_metrics DISABLE TRIGGER evaluation_metrics_evidence_locked"))
    db.execute(text(
        "UPDATE evaluation_metrics SET metric_value = 0.01 WHERE model_evaluation_id IN "
        "(SELECT id FROM model_evaluations WHERE evaluation_scope = 'final_holdout' AND workspace_id = :ws)"),
        {"ws": g.ws})
    db.execute(text("ALTER TABLE evaluation_metrics ENABLE TRIGGER evaluation_metrics_evidence_locked"))
    db.commit()
    assert _verdict(client, g, g.exp[1]).json() == before  # verdict, numbers and look counts are holdout-blind


def test_not_comparable_reasons(client, db_session, plan):
    g, db = plan, db_session
    assert _verdict(client, g, g.exp[1], against=str(g.exp[1])).json()["reason"] == "same_experiment"
    assert _verdict(client, g, g.exp[0]).json()["reason"] == "no_parent"
    assert _verdict(client, g, g.exp[3], against=str(g.exp[0])).json()["reason"] == "not_finished"
    assert _verdict(client, g, g.exp[5]).json()["reason"] == "evidence_not_locked"  # COMPLETED, unlocked
    assert _verdict(client, g, g.exp[7], against=str(g.exp[0])).json()["reason"] == "different_split_plan"
    missing = _verdict(client, g, g.exp[2]).json()  # a winner without locked per-fold rows
    assert (missing["verdict"], missing["reason"], missing["fold_scores"]) == ("not_comparable",
                                                                               "missing_locked_folds", [])
    assert missing["message"].startswith("A locked per-fold score is missing")
    assert "honest estimate" not in missing["message"]
    assert missing["test_set_looks"]["experiment_look"] == 3
    db.execute(text("ALTER TABLE split_plans DISABLE TRIGGER split_plans_immutable"))
    db.execute(text("UPDATE split_plans SET validation_strategy = 'TimeSeriesSplit' WHERE id = :id"), {"id": g.plan.id})
    db.execute(text("ALTER TABLE split_plans ENABLE TRIGGER split_plans_immutable"))
    db.commit()
    timed = _verdict(client, g, g.exp[1]).json()
    assert (timed["verdict"], timed["reason"]) == ("not_comparable", "time_ordered_folds")


def test_suspicious_child_never_beats_the_noise(client, db_session, tmp_path, setup):  # noqa: F811
    g = _plan(db_session, tmp_path, setup, findings=[("target_leakage", "fail")])
    body = _verdict(client, g, g.exp[1]).json()
    assert (body["verdict"], body["reason"]) == ("not_comparable", "suspicious_result")
    assert body["mean_difference"] == pytest.approx(0.03)  # the numbers stay visible
    assert body["message"].startswith("The result looks too good to be true")
    h = _plan(db_session, tmp_path, setup, side="beta", findings=[("implausible_score", "warning")])
    assert _verdict(client, h, h.exp[1]).json()["verdict"] == "beats_the_noise"


def test_different_metric_and_too_few_folds(client, db_session, tmp_path, setup):  # noqa: F811
    g = _plan(db_session, tmp_path, setup, child_metric="pr_auc")
    assert _verdict(client, g, g.exp[1]).json()["reason"] == "different_metric"
    h = _plan(db_session, tmp_path, setup, side="beta", parent=PARENT[:4], child=CHILD[:4])
    body = _verdict(client, h, h.exp[1]).json()
    assert (body["verdict"], body["reason"], body["folds"]) == ("not_comparable", "too_few_folds", 4)


def test_error_metric_is_reported_in_natural_units(client, db_session, tmp_path, setup):  # noqa: F811
    rmse = [10.0, 11.0, 9.0, 10.5, 9.5]
    g = _plan(db_session, tmp_path, setup, parent=rmse, child=[v - d for v, d in zip(rmse, [1, 1.2, 0.8, 1, 1])],
              metric="rmse")
    body = _verdict(client, g, g.exp[1]).json()
    assert (body["verdict"], body["direction"]) == ("beats_the_noise", "lower_is_better")
    assert body["mean_difference"] == pytest.approx(-1.0) and body["margin"] > 0
    assert body["message"].startswith("This try's model scores RMSE lower by 1")


def test_same_scores_are_no_change(client, db_session, tmp_path, setup):  # noqa: F811
    g = _plan(db_session, tmp_path, setup, child=PARENT)
    body = _verdict(client, g, g.exp[1]).json()
    assert (body["verdict"], body["reason"], body["mean_difference"]) == ("within_the_noise", "no_change", 0.0)


def test_workspace_filters_hold_in_the_helpers(db_session, plan, setup):  # noqa: F811
    g, beta = plan, setup["beta"].id
    assert service.plan_looks(db_session, beta, g.plan.id) == ({}, 0)
    assert service._selection(db_session, beta, g.exp[0]) is None
    assert service._selection(db_session, g.ws, g.exp[0]) is not None
    assert service.locked_fold_scores(db_session, beta, g.cand[0], "roc_auc") is None


RAW_METRIC_KEY = re.compile(r"\b(roc_auc|roc_auc_ovr|pr_auc|rmse|mae|mse|mape|smape|r2|log_loss|brier|brier_score|"
                            r"macro_f1|weighted_f1|balanced_accuracy|median_absolute_error|f1)\b")
FORBIDDEN_WORDS = re.compile(r"experiment|candidate|honest estimate", re.IGNORECASE)


def test_messages_are_plain_and_never_call_a_number_fresh():
    from app.domain.experiment_verdict import REASON_MESSAGES, verdict_message
    from app.engine.evaluation.metrics import LOWER_IS_BETTER

    metrics = ["roc_auc", "pr_auc", "f1", "macro_f1", "balanced_accuracy", "accuracy", "rmse", "mae", "log_loss",
               "r2", "brier_score", "median_absolute_error"]
    messages = []
    for plan_count in (0, 2, 6):
        for reason in REASON_MESSAGES:
            messages.append(verdict_message("not_comparable", reason, metric=None, direction=None, folds=None,
                                            mean_difference=None, margin=None, comparisons_on_plan=plan_count))
        for metric in metrics:
            direction = "lower_is_better" if metric in LOWER_IS_BETTER else "higher_is_better"
            for verdict, reason, diff in (("beats_the_noise", None, 0.05), ("worse", None, -0.05),
                                          ("within_the_noise", "inside_margin", 0.001),
                                          ("within_the_noise", "below_minimum_effect", 0.0001),
                                          ("within_the_noise", "no_change", 0.0)):
                messages.append(verdict_message(verdict, reason, metric=metric, direction=direction, folds=5,
                                                mean_difference=diff, margin=0.01, new_candidates=2,
                                                comparisons_on_plan=plan_count))
                if reason != "no_change":
                    assert "for this one comparison (evidence, not proof)" in messages[-1], messages[-1]
    for message in messages:
        assert not RAW_METRIC_KEY.search(message), message
        assert not FORBIDDEN_WORDS.search(message), message


def test_look_fields_are_counts_only():
    allowed = {int, UUID, str, type(None)}
    for name, field in SplitPlanLooksRead.model_fields.items():
        assert set(typing.get_args(field.annotation) or (field.annotation,)) <= allowed, name
    assert SplitPlanLooksRead.model_fields["counting"].default == LOOK_COUNTING


def test_validation_tenancy_and_token_shaping(client, db_session, tmp_path, plan, setup):  # noqa: F811
    g, db = plan, db_session
    url = f"/v1/experiments/{g.exp[1]}/verdict"
    _assert_envelope(client.get(url, params={"against": "not-a-uuid"}, headers=g.headers), 422, "validation_failed")
    _assert_envelope(client.get(url, params={"against": "x" * 5000}, headers=g.headers), 422, "validation_failed")
    _assert_envelope(client.get(f"{url}?against={g.exp[0]}&against={g.exp[2]}", headers=g.headers), 422,
                     "validation_failed")
    # A non-member of the workspace: 403; another workspace's experiment (either id): 404.
    _assert_envelope(client.get(url, headers=_headers(setup["beta_admin"], g.ws)), 403, "forbidden")
    assert client.get(url, headers=_headers(setup["beta_admin"], setup["beta"].id)).status_code == 404
    own = _plan(db, tmp_path, setup, side="beta")
    _assert_envelope(client.get(f"/v1/experiments/{own.exp[1]}/verdict", params={"against": str(g.exp[0])},
                                headers=own.headers), 404, "not_found")
    beta_token = create_service_token(db, creator=setup["beta_admin"], workspace_id=setup["beta"].id, name="b",
                                      scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    beta_bearer = {"Authorization": f"Bearer {beta_token}"}
    _assert_envelope(client.get(url, headers=beta_bearer), 404, "not_found")
    _assert_envelope(client.get(f"/v1/experiments/{own.exp[1]}/verdict", params={"against": str(g.exp[0])},
                                headers=beta_bearer), 404, "not_found")
    body = _verdict(client, g, g.exp[1]).json()
    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="r",
                                 scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    as_token = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert as_token.status_code == 200, as_token.text
    # Decision: tokens see the look counts (metadata, no outcome) and the CV verdict: the same body.
    assert as_token.json() == body and body["test_set_looks"]["looks_on_plan"] == 6
    assert not {k for k in _keys(body) if HOLDOUT.search(k)} and strip_holdout(body) == body
