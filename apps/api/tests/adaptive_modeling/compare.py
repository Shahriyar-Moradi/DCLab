"""Compare naive / old-detector behavior with Phase 1 planning.

Not imported by production services. A lower Phase 1 holdout score after
removing a post-outcome feature is leakage correction, not a regression.
"""

from __future__ import annotations

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

from app.engine.leakage.detector import detect_leakage


def naive_holdout_accuracy(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target: str,
    *,
    seed: int = 42,
) -> float:
    y = frame[target]
    X = frame.loc[:, feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=seed,
        stratify=y if y.nunique(dropna=True) == 2 else None,
    )
    model = LogisticRegression(max_iter=500, random_state=seed)
    model.fit(X_train, y_train)
    return float(accuracy_score(y_test, model.predict(X_test)))


def old_detector_high_risk_columns(frame: pd.DataFrame, target: str) -> list[str]:
    report = detect_leakage(frame, target=target)
    return list(report.get("high_risk_columns") or [])
