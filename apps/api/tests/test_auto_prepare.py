"""Unit tests for the simple-case auto-train "prepare" step (pure functions,
no DB, no client exposure). See docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.lab.auto_prepare import (
    build_preprocessor,
    coerce_numeric_like,
    pick_target_heuristic,
    plan_missing_values,
    split_column_roles,
)


def test_coerce_numeric_like_converts_blank_string_numerics():
    values = [f"{i}.50" for i in range(19)] + [" "]
    frame = pd.DataFrame({"TotalCharges": values})
    out = coerce_numeric_like(frame, ["TotalCharges"])
    assert pd.api.types.is_numeric_dtype(out["TotalCharges"])
    assert out["TotalCharges"].isna().sum() == 1


def test_coerce_numeric_like_leaves_real_text_alone():
    frame = pd.DataFrame({"plan_name": ["Gold", "Silver", "Gold", "Bronze"]})
    out = coerce_numeric_like(frame, ["plan_name"])
    assert not pd.api.types.is_numeric_dtype(out["plan_name"])


def test_pick_target_prefers_known_alias_over_generic_binary_column():
    frame = pd.DataFrame(
        {
            "customer_id": ["a", "b", "c", "d"],
            "is_active": ["yes", "no", "yes", "no"],
            "churn": ["Yes", "No", "No", "Yes"],
        }
    )
    choice = pick_target_heuristic(frame, list(frame.columns))
    assert choice.column == "churn"
    assert "churn" in choice.reason


def test_pick_target_falls_back_to_only_binary_column():
    frame = pd.DataFrame(
        {
            "customer_id": ["a", "b", "c", "d"],
            "renewed": ["Y", "N", "Y", "N"],
        }
    )
    choice = pick_target_heuristic(frame, list(frame.columns))
    assert choice.column == "renewed"


def test_pick_target_fails_cleanly_when_nothing_matches():
    frame = pd.DataFrame(
        {
            "customer_id": ["a", "b", "c", "d"],
            "plan_name": ["Gold", "Silver", "Gold", "Bronze"],
        }
    )
    choice = pick_target_heuristic(frame, list(frame.columns))
    assert choice.column is None
    assert "no label column found" in choice.reason


def test_pick_target_ignores_high_cardinality_identifier_columns():
    n = 50
    frame = pd.DataFrame(
        {
            "record_uuid": [f"id-{i}" for i in range(n)],
            "amount": np.random.uniform(1, 100, n),
        }
    )
    choice = pick_target_heuristic(frame, list(frame.columns))
    assert choice.column is None


def test_plan_missing_values_drops_mostly_empty_columns():
    n = 20
    frame = pd.DataFrame(
        {
            "mostly_empty": [None] * 15 + [1] * 5,
            "tenure": list(range(n)),
        }
    )
    plan = plan_missing_values(frame, ["mostly_empty", "tenure"])
    assert "mostly_empty" in plan.dropped_columns
    assert "tenure" not in plan.dropped_columns


def test_plan_missing_values_recommends_dropping_rows_when_few_are_incomplete():
    n = 100
    values = list(range(n))
    values[3] = None
    frame = pd.DataFrame({"tenure": values})
    plan = plan_missing_values(frame, ["tenure"])
    assert plan.rows_with_missing == 1
    assert plan.drop_rows_recommended is True


def test_plan_missing_values_recommends_imputing_when_many_rows_are_incomplete():
    n = 100
    values = [None if i % 2 == 0 else i for i in range(n)]
    frame = pd.DataFrame({"tenure": values})
    plan = plan_missing_values(frame, ["tenure"])
    assert plan.rows_with_missing == 50
    assert plan.drop_rows_recommended is False


def test_split_column_roles_separates_numeric_and_categorical_and_drops_ids():
    frame = pd.DataFrame(
        {
            "customer_id": [f"C{i}" for i in range(20)],
            "tenure": [i % 6 for i in range(20)],  # realistic: many customers share a tenure value
            "gender": (["Male", "Female"] * 10),
            "constant_col": [1] * 20,
        }
    )
    numerical, categorical = split_column_roles(frame, list(frame.columns))
    assert numerical == ["tenure"]
    assert categorical == ["gender"]


def test_split_column_roles_keeps_high_cardinality_continuous_numeric_columns():
    # A near-all-unique float column (e.g. MonthlyCharges) must stay numeric,
    # not get excluded by the identifier heuristic (which only applies to ids).
    frame = pd.DataFrame({"monthly_charges": [20.0 + i * 0.37 for i in range(50)]})
    numerical, categorical = split_column_roles(frame, list(frame.columns))
    assert numerical == ["monthly_charges"]
    assert categorical == []


def test_build_preprocessor_produces_expected_output_shape():
    frame = pd.DataFrame(
        {
            "tenure": [1.0, 2.0, None, 4.0, 5.0],
            "MonthlyCharges": [10.0, 20.0, 30.0, None, 50.0],
            "gender": ["Male", "Female", "Male", None, "Female"],
            "contract": ["Month-to-month", "One year", "Two year", "One year", None],
        }
    )
    preprocessor = build_preprocessor(["tenure", "MonthlyCharges"], ["gender", "contract"])
    transformed = preprocessor.fit_transform(frame)
    assert transformed.shape[0] == len(frame)
    assert not np.isnan(transformed).any()
