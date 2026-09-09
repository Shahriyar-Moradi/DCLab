"""Canonical scientific lineage persisted from real Labs auto-train execution."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    DataPreparationDecision,
    DataQualityFinding,
    DatasetColumn,
    Feature,
    FeatureLineage,
    FeatureSet,
    FeatureSetVersion,
    FeatureTransformation,
    LabDecisionRecord,
    PreprocessingStep,
)
from app.services.scientific_lineage_service import create_derived_feature_with_sources


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


def _features_for_run(db_session, experiment):
    feature_set = db_session.scalar(
        select(FeatureSet).where(FeatureSet.name == f"pipeline-run-{experiment.id}")
    )
    assert feature_set is not None
    version = db_session.scalar(
        select(FeatureSetVersion)
        .where(FeatureSetVersion.feature_set_id == feature_set.id)
        .order_by(FeatureSetVersion.version.desc())
        .limit(1)
    )
    assert version is not None
    features = list(
        db_session.scalars(select(Feature).where(Feature.feature_set_version_id == version.id))
    )
    return feature_set, version, features


def test_scientific_lineage_persists_from_real_auto_train(
    auth_client, db_session, monkeypatch, _rule_engine_only
):
    frame = _scientific_probe()
    upload, _workflow_run, experiment, _model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="scientific_lineage.csv",
        target="outcome",
    )
    assert upload.pipeline_status == "completed"
    assert experiment.status == "COMPLETED"

    decisions = list(
        db_session.scalars(
            select(DataPreparationDecision).where(
                DataPreparationDecision.pipeline_run_id == experiment.id
            )
        )
    )
    findings = list(
        db_session.scalars(
            select(DataQualityFinding).where(DataQualityFinding.pipeline_run_id == experiment.id)
        )
    )
    steps = list(
        db_session.scalars(
            select(PreprocessingStep)
            .where(PreprocessingStep.pipeline_run_id == experiment.id)
            .order_by(PreprocessingStep.sequence)
        )
    )
    ledger = list(
        db_session.scalars(
            select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload.id)
        )
    )

    median_rows = [
        row
        for row in decisions
        if row.decision_type == "missing_value" and row.strategy == "median_imputation"
    ]
    assert any(row.evidence.get("column") == "income" for row in median_rows)
    drop_rows = [
        row
        for row in decisions
        if row.decision_type == "missing_value" and row.strategy == "drop_column"
    ]
    assert any(row.evidence.get("column") == "mostly_empty" for row in drop_rows)
    leak_rows = [row for row in decisions if row.decision_type == "leakage" and row.strategy == "exclude"]
    leak_columns = {row.evidence.get("column") for row in leak_rows}
    assert {"post_outcome_feature", "target_proxy"} <= leak_columns
    assert any(
        row.finding_type in {"target_leakage", "prediction_time_leakage"}
        and row.evidence.get("column") in {"post_outcome_feature", "target_proxy"}
        for row in findings
    )
    assert any(row.finding_type == "missing_values" for row in findings)

    datetime_decisions = [
        row
        for row in decisions
        if row.decision_type == "type_conversion" and row.strategy == "datetime"
    ]
    assert any(row.evidence.get("column") == "policy_renewal_date" for row in datetime_decisions)

    assert steps
    assert [row.sequence for row in steps] == list(range(1, len(steps) + 1))
    assert {row.fit_scope for row in steps} == {"fold_train"}
    assert all(row.fit_scope != "all_data" for row in steps)
    assert any(
        row.transformer_class == "sklearn.impute.SimpleImputer"
        and row.parameters.get("strategy") == "median"
        for row in steps
    )
    assert any(row.transformer_class == "sklearn.preprocessing.StandardScaler" for row in steps)
    assert any(row.transformer_class == "sklearn.preprocessing.OneHotEncoder" for row in steps)

    feature_set, version, features = _features_for_run(db_session, experiment)
    assert feature_set.project_id == experiment.project_id
    assert version.locked_at is not None
    by_name = {row.name: row for row in features}
    modeled = {row.name for row in features if row.status == "modeled"}
    excluded = {row.name for row in features if row.status == "excluded"}
    dropped = {row.name for row in features if row.status == "dropped"}
    assert modeled.isdisjoint(excluded)
    assert "post_outcome_feature" in excluded
    assert "target_proxy" in excluded
    assert "post_outcome_feature" not in modeled
    assert "mostly_empty" in dropped
    assert "mostly_empty" not in modeled
    assert "safe_feature" in modeled
    assert "policy_renewal_date" in modeled

    datetime_feature = by_name["policy_renewal_date"]
    transforms = list(
        db_session.scalars(
            select(FeatureTransformation).where(
                FeatureTransformation.feature_id == datetime_feature.id
            )
        )
    )
    assert any(row.transformation_type == "datetime_extract" for row in transforms)
    datetime_lineage = list(
        db_session.scalars(
            select(FeatureLineage).where(FeatureLineage.feature_id == datetime_feature.id)
        )
    )
    assert datetime_lineage
    source = db_session.get(DatasetColumn, datetime_lineage[0].source_dataset_column_id)
    assert source is not None
    assert source.name == "policy_renewal_date"

    safe = by_name["safe_feature"]
    safe_lineage = list(
        db_session.scalars(select(FeatureLineage).where(FeatureLineage.feature_id == safe.id))
    )
    assert safe_lineage
    safe_source = db_session.get(DatasetColumn, safe_lineage[0].source_dataset_column_id)
    assert safe_source is not None
    assert safe_source.name == "safe_feature"

    db_session.commit()
    locked = db_session.get(FeatureSetVersion, version.id)
    locked.content_digest = "00" * 32
    with pytest.raises(ValueError, match="FeatureSetVersion is locked and immutable"):
        db_session.flush()
    db_session.rollback()
    locked = db_session.get(FeatureSetVersion, version.id)
    with pytest.raises(ValueError, match="FeatureSetVersion is locked and immutable"):
        db_session.delete(locked)
        db_session.flush()
    db_session.rollback()

    route = create_derived_feature_with_sources(
        db_session,
        experiment=experiment,
        name="route_distance",
        source_column_names=[
            "source_lat",
            "source_lon",
            "destination_lat",
            "destination_lon",
        ],
        transformation_type="haversine",
        definition="Great-circle distance from source to destination coordinates.",
        parameters={"unit": "km"},
    )
    db_session.commit()
    route_sources = {
        db_session.get(DatasetColumn, row.source_dataset_column_id).name
        for row in db_session.scalars(
            select(FeatureLineage).where(FeatureLineage.feature_id == route.id)
        )
    }
    assert route_sources == {
        "source_lat",
        "source_lon",
        "destination_lat",
        "destination_lon",
    }
    transform = db_session.scalar(
        select(FeatureTransformation).where(FeatureTransformation.feature_id == route.id)
    )
    assert transform is not None
    assert transform.transformation_type == "haversine"

    first_lineage = db_session.scalar(
        select(FeatureLineage).where(FeatureLineage.feature_id == route.id)
    )
    assert first_lineage is not None
    with pytest.raises(IntegrityError):
        db_session.execute(
            FeatureLineage.__table__.insert().values(
                feature_id=route.id,
                source_dataset_column_id=first_lineage.source_dataset_column_id,
                relationship="source",
            )
        )
        db_session.flush()
    db_session.rollback()

    assert ledger
    assert any(row.column == "income" for row in ledger)


def test_learned_preprocessor_cannot_be_recorded_as_all_data():
    from app.services.scientific_lineage_service import _fit_scope_from_evidence, _learned_fit_scope

    with pytest.raises(ValueError, match="all_data"):
        _learned_fit_scope("all_data")
    with pytest.raises(ValueError, match="all_data"):
        _fit_scope_from_evidence("cv_fold_all_data", has_learned_steps=True)
    assert _learned_fit_scope("fold_train") == "fold_train"
    assert _learned_fit_scope("all_train") == "all_train"
    assert _learned_fit_scope("non_learned") == "non_learned"
    assert (
        _fit_scope_from_evidence(
            "cv_fold_train_only_then_full_training_partition",
            has_learned_steps=True,
        )
        == "fold_train"
    )
