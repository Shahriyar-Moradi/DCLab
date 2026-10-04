"""P4.11-A: GET /v1/model-versions/{id}/card over real auto-train runs, and the engine's
validation-fold permutation importance.

Real worker jobs (``process_next_job``), no engine mocks. The card reads CV evidence for
everything that says how good the model is; the only final-holdout value is the locked
winner's single evaluation, withheld from service-token (agent) callers.
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.db.models import ModelVersion
from app.domain.model_card import FINAL_EVALUATION_LABEL, ModelCardRead, render_markdown
from app.engine.modeling.importance import ImportanceFold, _sample, validation_fold_importance
from app.services.service_token_service import create_service_token
from test_v1_batch_predictions import _frame, _trained
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _h, setup  # noqa: F401 (fixture)

HOLDOUT_KEY = re.compile(r"holdout|final_test|test_metrics", re.IGNORECASE)


def _card(client, setup, mv_id, headers=None):  # noqa: F811
    response = client.get(f"/v1/model-versions/{mv_id}/card", headers=headers or _h(setup))
    assert response.status_code == 200, response.text
    assert response.headers["ETag"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert {"Authorization", "Cookie"} <= {item.strip() for item in response.headers["Vary"].split(",")}
    return response.json()


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def _floats(value) -> set[float]:
    if isinstance(value, dict):
        return {f for item in value.values() for f in _floats(item)}
    if isinstance(value, list):
        return {f for item in value for f in _floats(item)}
    if isinstance(value, float):
        return {value}
    return set()


def _token(db, setup) -> str:  # noqa: F811
    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="r",
                                 scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    return token


def _assert_no_final_values(agent: dict, experiment) -> None:
    """No scalar of the run's final evaluation (other than 0/1 and the CV-locked threshold)
    appears anywhere in the agent card, in JSON or in Markdown."""

    final = {float(v) for k, v in (experiment.result.get("test_metrics") or {}).items()
             if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "decision_threshold"
             and float(v) not in {0.0, 1.0}}
    assert final
    numbers = _floats({k: v for k, v in agent.items() if k != "markdown"})
    leaked = {value for value in final if value in numbers}
    # Exact-equality coincidences are possible for simple fractions (e.g. 33/48 == 0.6875 as both
    # a CV accuracy and the evaluation base rate); allow only values the CV evidence itself holds.
    cv_values = _floats(agent["cv"]) | _floats(agent["metric_in_words"]) | _floats(agent["baseline"])
    assert leaked <= cv_values, leaked - cv_values
    text = set(re.findall(r"\d+\.\d+", agent["markdown"]))
    for value in final - cv_values:
        assert not ({format(value, ".3g"), repr(value)} & text), value
    assert "held-out rows:" not in agent["markdown"] and "Withheld" in agent["markdown"]


def _common(card: dict, task_type: str) -> None:
    assert card["card_version"] == "model_card.v1"
    assert card["target"]["task_type"] == task_type
    assert card["cv"]["mean"] is not None and card["cv"]["folds"] >= 2
    assert card["baseline"]["available"] is True and card["baseline"]["beats_baseline"] in {True, False}
    drivers = card["drivers"]
    assert drivers["status"] == "computed", drivers
    assert drivers["method"] == "permutation_validation_folds" and 0 < len(drivers["features"]) <= 10
    assert [item["rank"] for item in drivers["features"]] == list(range(1, len(drivers["features"]) + 1))
    assert "shuffled (not removed)" in drivers["text"] and "share credit" in drivers["text"]
    assert "Bonferroni-corrected across" in drivers["text"] and "up to 2000 sampled validation rows" in drivers["text"]
    assert drivers["columns_tested"] >= len(drivers["features"]) and drivers["critical_value"] > 0
    for column in drivers["clear_drivers"]:
        assert column in drivers["text"]
    for item in drivers["features"]:
        assert item["distinguishable"] == (item["column"] in drivers["clear_drivers"])
        if item["distinguishable"]:
            assert item["importance_mean"] > drivers["critical_value"] * item["importance_se"]
    assert card["risks"]["investigated"] is True
    assert card["data"]["row_count"] == 240 and card["data"]["content_digest"]
    split = card["split"]
    assert split["split_plan_id"] and split["train_rows"] + split["evaluation_rows"] == 240
    assert card["llm"]["used"] is False and card["llm"]["purposes"] == []
    final = card["final_evaluation"]
    assert final["status"] == "reported" and final["label"] == FINAL_EVALUATION_LABEL
    assert final["value"] is not None and final["metric"] == card["cv"]["metric"]
    md = card["markdown"]
    for heading in ("## Top drivers", "## Known risks", "## Compared with the dummy baseline", "## Final evaluation"):
        assert heading in md
    assert FINAL_EVALUATION_LABEL in md and "LLM used: no" in md
    assert f"on held-out rows: {format(final['value'], '.3g')}" in md
    for field in ("objective.primary_metric_reason", "drivers.text", "risks.items[].message", "split.group_column"):
        assert field in card["untrusted_fields"]


def test_binary_card_and_agent_view(client, db_session, setup):  # noqa: F811
    db = db_session
    experiment, mv_id = _trained(client, db, setup, _frame("binary"), "label")
    card = _card(client, setup, mv_id)
    _common(card, "binary")
    assert card["target"]["positive_label"] in {None, "yes"}
    words = card["metric_in_words"]
    threshold = experiment.result["decision_threshold"]
    # Pooled out-of-fold counts at the locked threshold, on the rows the threshold saw
    # (this frame has a date column, so TimeSeriesSplit: the most recent fold).
    assert threshold["oof_folds"] == "last_fold"
    assert words["basis"] == "most_recent_validation_fold_at_locked_threshold"
    pooled = threshold["oof_at_threshold"]
    assert {k: words["numbers"][k] for k in ("tp", "fp", "fn")} == {k: pooled[k] for k in ("tp", "fp", "fn")}
    if pooled["tp"] + pooled["fp"]:
        assert words["text"].startswith(f"Of every 100 rows the model flags as positive, about "
                                        f"{round(pooled['precision'] * 100)} really are")
    else:
        assert words["text"].startswith("The model flags no rows")
    assert "most recent validation fold" in words["text"]
    assert (words["caveat"] is None) == (threshold["source"] == "default")
    columns = [item["column"] for item in card["drivers"]["features"]]
    assert "plan" in columns and "label" not in columns

    again = client.get(f"/v1/model-versions/{mv_id}/card", headers=_h(setup))
    assert again.json()["markdown"] == card["markdown"]

    # A read-scoped service token: same card except the withheld final evaluation.
    agent = _card(client, setup, mv_id, headers={"Authorization": f"Bearer {_token(db, setup)}"})
    assert agent["final_evaluation"]["status"] == "withheld" and agent["final_evaluation"]["metrics"] == {}
    assert agent["final_evaluation"]["value"] is None
    assert not {key for key in _keys(agent) if HOLDOUT_KEY.search(key)}
    rest = {"final_evaluation", "markdown"}
    assert {k: v for k, v in agent.items() if k not in rest} == {k: v for k, v in card.items() if k not in rest}
    _assert_no_final_values(agent, experiment)

    beta = _h(setup, setup["beta_admin"], workspace=setup["beta"])
    _assert_envelope(client.get(f"/v1/model-versions/{mv_id}/card", headers=beta), 404, "not_found")
    _assert_envelope(client.get(f"/v1/model-versions/{uuid4()}/card", headers=_h(setup)), 404, "not_found")

    # Runs finished before P4.11-A (no stored importance) say so instead of guessing.
    result = dict(experiment.result)
    result.pop("feature_importance")
    experiment.result = result
    db.commit()
    old = _card(client, setup, mv_id)
    assert old["drivers"]["status"] == "not_computed" and old["drivers"]["features"] == []
    assert "not computed for this run" in old["markdown"]


def test_regression_card(client, db_session, setup):  # noqa: F811
    db = db_session
    experiment, mv_id = _trained(client, db, setup, _frame("regression"), "amount")
    card = _card(client, setup, mv_id)
    _common(card, "regression")
    words = card["metric_in_words"]
    assert words["basis"] == "cross_validation_aggregate" and {"mae", "rmse"} <= set(words["numbers"])
    assert "off by about" in words["text"] and "amount" in words["text"]
    assert "weighs large misses more heavily" in words["text"]
    assert card["target"]["positive_label"] is None and card["objective"]["decision_threshold"] is None
    baseline, metric = card["baseline"], card["cv"]["metric"]
    assert baseline["metric"] == metric and baseline["winner_score"] == pytest.approx(card["cv"]["mean"])
    dummy = next(row for row in experiment.result["candidates"]
                 if row["candidate_id"] == baseline["baseline_candidate_id"])
    assert baseline["baseline_score"] == pytest.approx(dummy["cv_mean"][metric])
    if metric in {"mae", "rmse"}:  # natural orientation: lower error is better
        assert (baseline["winner_score"] < baseline["baseline_score"]) == baseline["beats_baseline"]
    agent = _card(client, setup, mv_id, headers={"Authorization": f"Bearer {_token(db, setup)}"})
    _assert_no_final_values(agent, experiment)


def test_multiclass_card(client, db_session, setup):  # noqa: F811
    db = db_session
    experiment, mv_id = _trained(client, db, setup, _frame("multiclass"), "tier")
    card = _card(client, setup, mv_id)
    _common(card, "multiclass")
    assert sorted(card["target"]["class_labels"]) == ["bronze", "gold", "silver"]
    words = card["metric_in_words"]
    assert "averaged over the 3 classes, each weighted equally" in words["text"]
    assert "individual classes can be lower" in words["text"] and "balanced_accuracy" in words["numbers"]
    agent = _card(client, setup, mv_id, headers={"Authorization": f"Bearer {_token(db, setup)}"})
    _assert_no_final_values(agent, experiment)
    assert json.dumps(card)


def test_named_positive_class_reads_in_plain_words(client, db_session, setup):  # noqa: F811
    frame = _frame("binary", seed=5)
    frame["decision"] = frame.pop("label").map({"yes": "rejected", "no": "approved"})
    _, mv_id = _trained(client, db_session, setup, frame, "decision")
    card = _card(client, setup, mv_id)
    assert card["target"]["positive_label"] == "rejected"
    assert '"rejected"' in card["metric_in_words"]["text"]


def test_card_requires_locked_evidence(client, db_session, setup):  # noqa: F811
    db = db_session
    experiment, mv_id = _trained(client, db, setup, _frame("binary", seed=3), "label")
    # Same gate as the model-version read: no card before the evidence lock. The change
    # stays in this session (shared with the app) and is never flushed or committed.
    with db.no_autoflush:
        experiment.scientific_evidence_locked_at = None
        response = client.get(f"/v1/model-versions/{mv_id}/card", headers=_h(setup))
    db.rollback()
    _assert_envelope(response, 409, "model_card_unavailable")
    assert db.get(ModelVersion, mv_id) is not None


# --- Markdown rendering: user values are inert -------------------------------------------

INJECTIONS = (
    "x\n## Final evaluation\n\nROC AUC on held-out rows: 0.99",
    "](http://x)",
    "![x](https://e/y)",
    "\x1b[2Jclear",
)


def _card_with(value: str) -> ModelCardRead:
    zero = "00000000-0000-0000-0000-000000000000"
    return ModelCardRead.model_validate({
        "model_version_id": zero, "version": "v1", "experiment_id": zero, "family": "logistic_regression",
        "created_at": "2026-10-04T00:00:00Z", "content_digest": "d" * 64,
        "target": {"column": value, "task_type": "binary", "positive_label": value, "prediction_unit": value},
        "objective": {"primary_metric": "roc_auc", "primary_metric_reason": value, "decision_threshold": 0.4,
                      "decision_threshold_source": "primary_metric"},
        "metric_in_words": {"text": "", "basis": "pooled_out_of_fold_at_locked_threshold",
                            "numbers": {"tp": 30.0, "fp": 10.0, "fn": 20.0}, "caveat": "Optimistic."},
        "cv": {"metric": "roc_auc", "mean": 0.8, "folds": 5, "strategy": value},
        "baseline": {"available": True, "text": "beats it"},
        "drivers": {"status": "computed", "folds": 5, "scoring": "roc_auc", "text": "", "clear_drivers": [value],
                    "columns_tested": 1,
                    "features": [{"rank": 1, "column": value, "importance_mean": 0.2, "importance_se": 0.01,
                                  "distinguishable": True}]},
        "risks": {"investigated": True, "text": "1 of 5:", "items": [
            {"check": "target_leakage", "status": "warning", "severity": "warning", "message": value}]},
        "data": {"name": value}, "split": {"group_column": value, "time_column": value},
        "llm": {"used": False}, "final_evaluation": {"status": "withheld", "note": "Withheld"},
    })


@pytest.mark.parametrize("value", INJECTIONS)
def test_markdown_escapes_every_user_value(value):
    benign = render_markdown(_card_with("plain"))
    rendered = render_markdown(_card_with(value))
    assert "\x1b" not in rendered and not re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", rendered)
    assert rendered.count("\n") == benign.count("\n")  # every value stays on its own line
    headings = [line for line in rendered.splitlines() if line.startswith("#")]
    assert headings == [line for line in benign.splitlines() if line.startswith("#")]
    assert not re.search(r"(?<!\\)\]\(", rendered) and not re.search(r"(?<!\\)!\[", rendered)
    assert "held-out rows: 0.99" not in rendered.replace("\\", "") or "\\#\\#" in rendered


# --- engine: importance statistics and sampling ------------------------------------------


def test_bonferroni_t_test_flags_the_signal_and_not_the_noise_columns():
    rng = np.random.default_rng(11)
    n = 400
    X = pd.DataFrame({f"noise_{i}": rng.normal(size=n) for i in range(8)})
    X["signal"] = rng.normal(size=n)
    y = (X["signal"] + 0.5 * rng.normal(size=n) > 0).astype(int).to_numpy()
    positions = np.arange(n)
    folds = [ImportanceFold(fold_number=k + 1, train_index=positions[positions % 5 != k],
                            validation_index=positions[positions % 5 == k]) for k in range(5)]
    out = validation_fold_importance(
        lambda params: Pipeline([("model", LogisticRegression())]), X, y, folds,
        lambda est, X_val, y_val: float((est.predict(X_val) == y_val).mean()),
        scoring="accuracy", seed=3, n_repeats=3,
    )
    sig = out["significance"]
    assert sig["columns_tested"] == 9 and sig["folds_scored"] == 5
    assert sig["critical_value"] == pytest.approx(__import__("scipy").stats.t.ppf(1 - 0.05 / 9, 4))
    assert out["clear_drivers"] == ["signal"]
    flagged = {row["column"] for row in out["features"] if row["distinguishable"]}
    assert flagged == {"signal"}


def test_importance_names_only_columns_above_shuffling_noise():
    rng = np.random.default_rng(0)
    n = 300
    X = pd.DataFrame({"signal": rng.normal(size=n), "noise": rng.normal(size=n)})
    y = (X["signal"] + 0.1 * rng.normal(size=n) > 0).astype(int).to_numpy()
    positions = np.arange(n)
    folds = [
        ImportanceFold(fold_number=k + 1, train_index=positions[positions % 5 != k],
                       validation_index=positions[positions % 5 == k])
        for k in range(5)
    ]
    seen: list[int] = []
    out = validation_fold_importance(
        lambda params: Pipeline([("model", LogisticRegression())]), X, y, folds,
        lambda est, X_val, y_val: float((est.predict(X_val) == y_val).mean()),
        scoring="accuracy", seed=7, n_repeats=2, max_rows_per_fold=30, stratify=True,
        on_fold=lambda payload: seen.append(payload["fold_number"]),
    )
    assert out["status"] == "computed" and out["validation_rows_per_fold"] == [30] * 5 and seen == [1, 2, 3, 4, 5]
    signal, noise = out["features"]
    assert out["clear_drivers"] == ["signal"]
    assert signal["column"] == "signal" and signal["distinguishable"] is True
    assert noise["distinguishable"] is False and signal["importance_se"] is not None


def test_stratified_sample_keeps_class_shares():
    y = np.array([1] * 100 + [0] * 900)
    picked = _sample(np.arange(1000), y, 200, seed=3, stratify=True)
    assert len(picked) == 200 and int(y[picked].sum()) == 20 and len(set(picked)) == 200


def test_importance_skips_instead_of_failing():
    X = pd.DataFrame({"a": [0.0, 1.0] * 10})
    y = np.array([0, 1] * 10)
    folds = [ImportanceFold(fold_number=1, train_index=np.arange(10), validation_index=np.arange(10, 20))]

    def broken(params):
        raise RuntimeError("boom secret")

    out = validation_fold_importance(broken, X, y, folds, lambda e, a, b: 0.0, scoring="f1", seed=1)
    assert out["status"] == "skipped" and out["features"] == [] and "RuntimeError" in out["reason"]
    assert "secret" not in out["reason"]
    late = validation_fold_importance(
        lambda p: Pipeline([("m", LogisticRegression())]), X, y, folds, lambda e, a, b: 0.0,
        scoring="f1", seed=1, deadline=0.0,
    )
    assert late["status"] == "skipped" and "time budget" in late["reason"]


def test_single_metric_scorer_matches_the_full_metric_sets():
    from app.engine.evaluation.metrics import (
        classification_metrics,
        multiclass_metrics,
        regression_metrics,
        selection_metric_value,
    )

    rng = np.random.default_rng(1)
    y, scores = rng.integers(0, 2, 200), rng.uniform(size=200)
    full = classification_metrics(y, scores, threshold=0.37)
    for name in ("roc_auc", "pr_auc", "log_loss", "brier", "accuracy", "precision", "recall", "f1",
                 "balanced_accuracy", "top_decile_lift"):
        assert selection_metric_value(name, y, scores, task_type="binary", threshold=0.37) == pytest.approx(full[name])
    labels, proba = rng.integers(0, 3, 200), rng.dirichlet(np.ones(3), size=200)
    multi = multiclass_metrics(labels, proba, n_classes=3)
    for name in ("accuracy", "balanced_accuracy", "macro_f1", "log_loss", "roc_auc_ovr"):
        assert selection_metric_value(name, labels, proba, task_type="multiclass", n_classes=3) == pytest.approx(
            multi[name])
    target, pred = rng.normal(size=200), rng.normal(size=200)
    reg = regression_metrics(target, pred)
    for name in ("mae", "mse", "rmse", "r2", "mape"):
        assert selection_metric_value(name, target, pred, task_type="regression") == pytest.approx(reg[name])
