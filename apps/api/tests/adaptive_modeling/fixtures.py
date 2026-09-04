"""Benchmark/test fixtures for Adaptive Model Builder Phase 1.

These frames are scientific probes, not production datasets. They are sized for
an 80/20 holdout plus five-fold validation on the training partition.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def binary_balanced(n: int = 200, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    outcome = np.array([0, 1] * (n // 2) + [0] * (n % 2))
    rng.shuffle(outcome)
    return pd.DataFrame(
        {
            "age": rng.normal(40, 12, n),
            "income": rng.normal(50_000, 8_000, n),
            "region": rng.choice(["N", "S"], n),
            "outcome": outcome,
        }
    )


def binary_imbalanced(n: int = 250, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    outcome = np.array([1] * 20 + [0] * (n - 20))
    rng.shuffle(outcome)
    return pd.DataFrame(
        {
            "age": rng.normal(40, 12, n) + outcome * 8,
            "spend": rng.normal(100, 20, n),
            "segment": rng.choice(["a", "b", "c"], n),
            "outcome": outcome,
        }
    )


def regression(n: int = 180, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 40, n)
    usage = rng.normal(20, 5, n)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "usage": usage,
            "segment": rng.choice(["small", "mid"], n),
            "revenue": 80 + tenure * 3.2 + usage * 1.4 + rng.normal(0, 4, n),
        }
    )


def repeated_entity(n_entities: int = 20, repeats: int = 5, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for entity in range(n_entities):
        label = int(entity % 4 == 0)
        for visit in range(repeats):
            rows.append(
                {
                    "customer_id": f"C{entity:03d}",
                    "visit": visit,
                    "amount": float(rng.normal(50, 10) + 20 * label),
                    "channel": "web" if visit % 2 == 0 else "store",
                    "outcome": label,
                }
            )
    return pd.DataFrame(rows)


def temporal(n: int = 120, seed: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-01")
    return pd.DataFrame(
        {
            "as_of_date": [start + pd.Timedelta(days=i) for i in range(n)],
            "demand": rng.normal(20, 3, n) + np.arange(n) * 0.2,
            "promo": rng.choice(["none", "on"], n),
            "revenue": 40 + np.arange(n) * 0.4 + rng.normal(0, 2, n),
        }
    )


def leakage_fixture(n: int = 200, seed: int = 1) -> pd.DataFrame:
    frame = binary_balanced(n=n, seed=seed)
    frame = frame.copy()
    frame["result_code"] = frame["outcome"] * 100
    return frame


def datetime_detection(n: int = 80, seed: int = 6) -> pd.DataFrame:
    return temporal(n=n, seed=seed)


def geo_detection(n: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "latitude": rng.uniform(25.0, 48.0, n),
            "longitude": rng.uniform(-122.0, -70.0, n),
            "score": rng.normal(0, 1, n),
            "outcome": rng.integers(0, 2, n),
        }
    )


def name_only_suspicious(n: int = 200, seed: int = 9) -> pd.DataFrame:
    """Legitimate predictors plus a post-outcome-looking name with no statistical leak."""
    frame = binary_balanced(n=n, seed=seed)
    rng = np.random.default_rng(seed)
    frame = frame.copy()
    frame["final_status_hint"] = rng.normal(0, 1, n)
    return frame


def fixture_catalog() -> dict[str, dict[str, Any]]:
    return {
        "binary_balanced": {"frame": binary_balanced(), "target": "outcome", "task_type": "binary"},
        "binary_imbalanced": {"frame": binary_imbalanced(), "target": "outcome", "task_type": "binary"},
        "regression": {"frame": regression(), "target": "revenue", "task_type": "regression"},
        "repeated_entity": {"frame": repeated_entity(), "target": "outcome", "task_type": "binary"},
        "temporal": {"frame": temporal(), "target": "revenue", "task_type": "regression"},
        "leakage_fixture": {"frame": leakage_fixture(), "target": "outcome", "task_type": "binary"},
        "datetime_detection": {"frame": datetime_detection(), "target": "revenue", "task_type": "regression"},
        "geo_detection": {"frame": geo_detection(), "target": "outcome", "task_type": "binary"},
    }
