import pandas as pd

from app.engine.datasets.synthetic import make_synthetic_customers
from app.engine.leakage.detector import detect_leakage
from app.engine.schema.profiler import profile_frame
from app.engine.targets.builder import build_binary_horizon_target, build_days_to_next_event
from app.engine.datasets.synthetic import synthetic_events
from app.engine.validation.splits import assert_temporal_order, split_frame


def test_profiler_counts_and_identifiers():
    frame = make_synthetic_customers(n=200, seed=1)
    profile = profile_frame(frame)
    assert profile["row_count"] == 200
    assert "entity_id" in profile["identifier_like_columns"]
    assert "entity_id" in profile["likely_identifier_columns"]
    assert profile["column_count"] == frame.shape[1]
    assert profile["column_names"] == list(frame.columns)
    assert set(profile["dtypes"]) == set(frame.columns)
    assert "missing_count" in profile
    assert "missing_percentage" in profile
    assert "unique_count" in profile
    assert "duplicate_count" in profile
    assert "numerical_statistics" in profile
    assert "categorical_statistics" in profile
    assert "constant_columns" in profile
    assert "high_cardinality_columns" in profile


def test_profiler_flags_high_cardinality_text_and_identifiers():
    n = 80
    frame = pd.DataFrame(
        {
            "customer_id": [f"C{i}" for i in range(n)],
            "note": [f"free text {i}" for i in range(n)],
            "plan": (["gold", "silver"] * (n // 2)),
            "amount": list(range(n)),
        }
    )
    profile = profile_frame(frame)
    assert "note" in profile["high_cardinality_columns"]
    assert "plan" not in profile["high_cardinality_columns"]
    assert "amount" not in profile["high_cardinality_columns"]
    assert "customer_id" in profile["likely_identifier_columns"]
    assert profile["numerical_statistics"]["amount"]["mean"] is not None
    assert "plan" in profile["categorical_statistics"]


def test_leakage_flags_future_amount():
    frame = make_synthetic_customers(n=400, seed=2, leak=True)
    report = detect_leakage(frame, target="purchase_within_60d", time_col="as_of_date", entity_col="entity_id")
    assert report["risk"] == "HIGH"
    assert "future_purchase_amount" in report["high_risk_columns"]
    assert "future_purchase_amount" in frame.columns


def test_sampled_and_priority_combination_strategies():
    from app.engine.features.combinations import generate_group_combinations

    sampled = generate_group_combinations(
        ["a", "b", "c", "d"], strategy="sampled", max_combinations=6, seed=3
    )
    assert len(sampled) == 6
    priority = generate_group_combinations(
        ["a", "b", "c"], strategy="priority", max_combinations=4, priority=["c"]
    )
    assert priority[0] == ("c",) or "c" in priority[0]


def test_temporal_split_order():
    frame = make_synthetic_customers(n=500, seed=3)
    train, val, test, meta = split_frame(
        frame, strategy="time", target="purchase_within_60d", time_col="as_of_date"
    )
    assert len(train) and len(val) and len(test)
    assert_temporal_order(meta)
    assert meta["train_max"] <= meta["val_min"] <= meta["val_max"] <= meta["test_min"]


def test_point_in_time_target_uses_future_only():
    events = synthetic_events(n_customers=40, seed=4)
    labels = build_binary_horizon_target(
        events,
        entity_col="entity_id",
        event_time_col="event_time",
        as_of="2022-06-01",
        horizon_days=60,
    )
    assert set(labels["target"].unique()) <= {0, 1}
    days = build_days_to_next_event(
        events, entity_col="entity_id", event_time_col="event_time", as_of="2022-06-01"
    )
    assert days["target"].min() >= 0
