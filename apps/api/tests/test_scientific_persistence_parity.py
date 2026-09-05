"""Labs auto-train and direct execute_experiment persist the same canonical tables."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    CVFoldRun,
    DataPreparationDecision,
    ExperimentCandidate,
    Feature,
    FeatureSet,
    FeatureSetVersion,
    FeatureTransformation,
    ModelEvaluation,
    ModelHyperparameter,
    ModelSelectionDecision,
    PreprocessingStep,
)
from app.engine.types import SearchConfig, TaskSpec
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_dataset,
    seed_dogfood,
    upsert_task,
)
from app.services.project_service import get_or_create_labs_project


REQUIRED_CATEGORIES = (
    "missing_value_decisions",
    "leakage_exclusions",
    "feature_actions",
    "preprocessing_fit_scope",
    "candidates",
    "applied_hyperparameters",
    "cv_fold_evidence",
    "evaluations",
    "winner_selection",
)


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _scientific_probe(n: int = 200, seed: int = 21) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    outcome = np.array([0, 1] * (n // 2) + [0] * (n % 2))
    rng.shuffle(outcome)
    income = rng.normal(50_000, 8_000, n)
    income = np.where(rng.random(n) < 0.12, np.nan, income)
    mostly_empty = rng.normal(1.0, 0.2, n)
    mostly_empty = np.where(rng.random(n) < 0.72, np.nan, mostly_empty)
    dates = np.array(["2024-01-15", "2024-06-01", "2024-11-20"])
    return pd.DataFrame(
        {
            "safe_feature": rng.normal(40, 12, n) + 0.4 * outcome,
            "income": income,
            "mostly_empty": mostly_empty,
            "policy_renewal_date": rng.choice(dates, n),
            "region": rng.choice(["N", "S"], n),
            "source_lat": rng.uniform(25.0, 48.0, n),
            "source_lon": rng.uniform(-122.0, -70.0, n),
            "destination_lat": rng.uniform(25.0, 48.0, n),
            "destination_lon": rng.uniform(-122.0, -70.0, n),
            "post_outcome_feature": outcome.astype(float),
            "target_proxy": outcome * 100,
            "outcome": outcome,
        }
    )


def _populated_categories(db_session, experiment) -> set[str]:
    decisions = list(
        db_session.scalars(
            select(DataPreparationDecision).where(
                DataPreparationDecision.pipeline_run_id == experiment.id
            )
        )
    )
    steps = list(
        db_session.scalars(
            select(PreprocessingStep).where(PreprocessingStep.pipeline_run_id == experiment.id)
        )
    )
    candidates = list(
        db_session.scalars(
            select(ExperimentCandidate).where(ExperimentCandidate.experiment_id == experiment.id)
        )
    )
    candidate_ids = [row.id for row in candidates]
    hyperparameters = list(
        db_session.scalars(
            select(ModelHyperparameter).where(ModelHyperparameter.candidate_id.in_(candidate_ids))
        )
    ) if candidate_ids else []
    folds = list(
        db_session.scalars(select(CVFoldRun).where(CVFoldRun.candidate_id.in_(candidate_ids)))
    ) if candidate_ids else []
    evaluations = list(
        db_session.scalars(
            select(ModelEvaluation).where(ModelEvaluation.candidate_id.in_(candidate_ids))
        )
    ) if candidate_ids else []
    selection = db_session.scalar(
        select(ModelSelectionDecision).where(
            ModelSelectionDecision.pipeline_run_id == experiment.id
        )
    )
    feature_set = db_session.scalar(
        select(FeatureSet).where(FeatureSet.name == f"pipeline-run-{experiment.id}")
    )
    transformations: list[FeatureTransformation] = []
    if feature_set is not None:
        version = db_session.scalar(
            select(FeatureSetVersion)
            .where(FeatureSetVersion.feature_set_id == feature_set.id)
            .order_by(FeatureSetVersion.version.desc())
            .limit(1)
        )
        if version is not None:
            features = list(
                db_session.scalars(
                    select(Feature).where(Feature.feature_set_version_id == version.id)
                )
            )
            feature_ids = [row.id for row in features]
            if feature_ids:
                transformations = list(
                    db_session.scalars(
                        select(FeatureTransformation).where(
                            FeatureTransformation.feature_id.in_(feature_ids)
                        )
                    )
                )
    type_conversions = [
        row for row in decisions if row.decision_type == "type_conversion"
    ]
    populated = set()
    if any(row.decision_type == "missing_value" for row in decisions):
        populated.add("missing_value_decisions")
    if any(row.decision_type == "leakage" for row in decisions):
        populated.add("leakage_exclusions")
    if transformations or type_conversions:
        populated.add("feature_actions")
    if steps and all(row.fit_scope != "all_data" for row in steps):
        populated.add("preprocessing_fit_scope")
    if candidates:
        populated.add("candidates")
    if hyperparameters:
        populated.add("applied_hyperparameters")
    if folds:
        populated.add("cv_fold_evidence")
    if evaluations:
        populated.add("evaluations")
    if selection is not None:
        populated.add("winner_selection")
    return populated


def _execute_direct_experiment(db_session, client_user, tmp_path, frame: pd.DataFrame):
    project = get_or_create_labs_project(
        db_session, workspace_id=DEFAULT_WORKSPACE_ID, actor=client_user
    )
    path = tmp_path / "scientific_persistence_parity.csv"
    frame.to_csv(path, index=False)
    env = seed_dogfood(db_session)
    dataset = ingest_dataset(
        db_session,
        environment=env,
        name=f"parity-direct-{uuid4().hex[:8]}",
        location=str(path),
        source_type="csv",
        version="v1",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
    )
    spec = TaskSpec(
        id=f"parity_direct_{uuid4().hex[:12]}",
        name="parity direct experiment",
        task_type="binary",
        target="outcome",
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric="pr_auc",
        feature_groups={"features": [name for name in frame.columns if name != "outcome"]},
        validation_strategy="stratified",
        column_roles={},
    )
    task = upsert_task(db_session, env, spec)
    experiment = create_experiment(
        db_session,
        environment=env,
        dataset=dataset,
        task=task,
        project_id=project.id,
        config=SearchConfig(
            strategy="open_ingest",
            max_candidates=8,
            max_feature_group_combinations=1,
            max_ensemble_size=1,
            n_robustness_folds=5,
            min_metric=0.0,
            retain_min=1,
            retain_max=1,
            seed=42,
        ),
    )
    return execute_experiment(db_session, experiment)


def test_labs_and_direct_experiment_persist_the_same_canonical_categories(
    auth_client, db_session, monkeypatch, client_user, tmp_path, _rule_engine_only
):
    frame = _scientific_probe()
    _upload, _workflow_run, labs_experiment, _model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="scientific_persistence_parity.csv",
        target="outcome",
    )
    assert labs_experiment.status == "COMPLETED"

    direct = _execute_direct_experiment(db_session, client_user, tmp_path, frame)
    assert direct.status == "COMPLETED"
    evidence = (direct.result or {}).get("scientific_evidence") or {}
    assert evidence.get("missing_value_plan")
    assert evidence.get("leakage_exclusions")
    assert evidence.get("feature_actions")
    assert evidence.get("preprocessing_fit_scope") == "fold_train"

    labs_categories = _populated_categories(db_session, labs_experiment)
    direct_categories = _populated_categories(db_session, direct)
    required = set(REQUIRED_CATEGORIES)
    assert labs_categories == required
    assert direct_categories == required
    assert labs_categories == direct_categories
