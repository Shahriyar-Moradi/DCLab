"""Feature-matrix helper shared by every runner in the harness.

Deliberately mirrors ``app.engine.experiments.runner._matrix`` byte-for-byte
(numeric coercion, ``fillna(0.0)``, no categorical encoding). The DCLab
engine's candidate matrix builder does not one-hot or ordinal-encode
non-numeric columns today — it coerces them to numeric and treats anything
that fails to parse as 0.0. If the baseline runner encoded categoricals more
richly than that, it would get *better* feature engineering than the
ensemble, which is exactly the kind of comparison-skewing asymmetry this
harness exists to avoid (see the "no strawman" guardrail — it cuts both
ways). See the Step 1 report for how this affects which columns currently
carry signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def full_feature_list(feature_groups: dict[str, list[str]]) -> list[str]:
    """Union of every column across every declared feature group, in group order.

    This is deliberately the *full* feature set — "not an artificially
    restricted one" per the build doc — rather than one of the subsets the
    DCLab engine's candidate search explores.
    """
    seen: list[str] = []
    for columns in feature_groups.values():
        for column in columns:
            if column not in seen:
                seen.append(column)
    return seen


def available_features(
    frame: pd.DataFrame, feature_groups: dict[str, list[str]], *, exclude: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Full declared feature list, filtered down to columns the frame actually has.

    Mirrors ``app.engine.experiments.runner.run_experiment``'s own group
    filtering (``[col for col in cols if col in work.columns ...]``) — a
    declared column that a real dataset doesn't have is dropped the same way
    for the baseline as it is for every DCLab candidate, so this isn't an
    asymmetry between the two runners. Returns (available, dropped).
    """
    exclude = exclude or set()
    declared = full_feature_list(feature_groups)
    available = [c for c in declared if c in frame.columns and c not in exclude]
    dropped = [c for c in declared if c not in available]
    return available, dropped


def to_matrix(frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    missing = [column for column in features if column not in frame.columns]
    if missing:
        raise ValueError(f"missing features: {missing}")
    return frame.loc[:, features].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
