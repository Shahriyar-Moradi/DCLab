"""P5.1-A probe and branch checks (group C).

``time_travel`` reuses P5.0-A's truncation replay (``future_influence_probe``) on
training-partition rows: recomputing a row's as-of features from strictly earlier rows
must not change them. ``new_feature`` compares this branch's CV with its parent's stored
CV on the same SplitPlan (P5.0-A: comparisons are CV-only on one SplitPlan) when the
branch adds exactly one model column. Neither reads a final-holdout value.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.features.probes import DEFAULT_PROBE_ROWS, future_influence_probe
from app.engine.investigate.checks import (
    BOUNDED_SCORE_METRICS,
    COLUMN_DETAIL_LIMIT,
    _finding,
    _not_evaluated,
)
from app.engine.investigate.types import Finding, ParentEvidence, RunEvidence, TimeTravelProbe

# Time travel: the replay recomputes the features once per probed row, so it runs on at
# most 500 training rows (whole groups kept, seeded) -- exhaustive below that. Any moved
# value is a leak of future rows into a feature: fail (critical).
TIME_TRAVEL_MAX_ROWS = 500
SAMPLE_SEED = 0
# New feature: one added column removes at least half of the parent's remaining error
# (1 - score for bounded scores, the error itself for error metrics). It compares winners, so
# a changed winner family is not_evaluated. With per-fold metrics of both runs (same SplitPlan
# -> same folds) the paired fold deltas must be significant (one-sided t, alpha 0.01): then
# 50% warns and 80% fails. Without them the jump must exceed 3x the combined fold standard
# deviations and the finding is at most a warning.
NEW_FEATURE_WARN = 0.5
NEW_FEATURE_FAIL = 0.8
NEW_FEATURE_NOISE_STD = 3.0
NEW_FEATURE_ALPHA = 0.01
NEW_FEATURE_MIN_FOLDS = 3


def _r(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), 6)


def _bounded(frame: pd.DataFrame, group_column: str | None) -> pd.DataFrame:
    if len(frame) <= TIME_TRAVEL_MAX_ROWS:
        return frame.reset_index(drop=True)
    if group_column and group_column in frame.columns:
        sizes = frame.groupby(group_column, sort=True, dropna=True).size()
        order = np.random.default_rng(SAMPLE_SEED).permutation(len(sizes))
        keep, total = [], 0
        for position in order:
            size = int(sizes.iloc[position])
            if total + size <= TIME_TRAVEL_MAX_ROWS:
                keep.append(sizes.index[position])
                total += size
        picked = frame[frame[group_column].isin(keep)]
        if len(picked) >= 2:
            return picked.reset_index(drop=True)
    return frame.sample(n=TIME_TRAVEL_MAX_ROWS, random_state=SAMPLE_SEED).sort_index().reset_index(drop=True)


# --- 14. time travel --------------------------------------------------------------------


def check_time_travel(ev: RunEvidence, probe: TimeTravelProbe | None) -> Finding:
    if not ev.time_column:
        return _not_evaluated("time_travel", "no_time_column", "time_travel.not_applicable")
    evidence: dict[str, Any] = {"time_column": ev.time_column, "as_of_features": list(ev.as_of_features)}
    if not ev.as_of_features:
        return _not_evaluated("time_travel", "no_as_of_features", "time_travel.no_as_of_features", evidence)
    if probe is None:
        return _not_evaluated("time_travel", "probe_unavailable", "time_travel.probe_unavailable", evidence)
    group = ev.group_column if ev.group_column and ev.group_column in probe.frame.columns else None
    frame = _bounded(probe.frame, group)
    result = future_influence_probe(probe.produce, frame, time_column=ev.time_column, group_column=group,
                                    probe_rows=DEFAULT_PROBE_ROWS, seed=SAMPLE_SEED)
    evidence.update(rows_used=int(len(frame)), probed_rows=result.tested, affected_rows=len(result.affected),
                    moved_columns=list(result.columns)[:COLUMN_DETAIL_LIMIT],
                    max_abs_change=_r(result.max_abs_change), group_column=group)
    if result.leaked:
        return _finding("time_travel", "fail", evidence, ("time_travel.leak",), "review_columns", "critical")
    return _finding("time_travel", "pass", evidence, ("time_travel.pass",))


# --- 15. new feature --------------------------------------------------------------------


def _fold_value(row: Any, metric: str) -> float | None:
    value = row.get(metric) if hasattr(row, "get") else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def check_new_feature(ev: RunEvidence, parent: ParentEvidence | None) -> Finding:
    if parent is None:
        return _not_evaluated("new_feature", "no_parent", "new_feature.no_parent")
    evidence: dict[str, Any] = {"parent_experiment_id": parent.experiment_id, "metric": ev.primary_metric,
                                "winner_family": ev.winner_family, "parent_winner_family": parent.winner_family}
    if not parent.same_split_plan:
        return _not_evaluated("new_feature", "different_split_plan", "new_feature.other_split", evidence)
    added = sorted(set(ev.winner_features) - set(parent.features))
    removed = sorted(set(parent.features) - set(ev.winner_features))
    evidence.update(added_count=len(added), removed_count=len(removed), added_columns=added[:COLUMN_DETAIL_LIMIT])
    if len(added) != 1 or removed:
        return _not_evaluated("new_feature", "not_single_feature_change", "new_feature.not_single", evidence)
    if ev.winner_family and parent.winner_family and ev.winner_family != parent.winner_family:
        return _not_evaluated("new_feature", "different_winner_family", "new_feature.family_changed", evidence)
    metric = ev.primary_metric
    lower = metric in LOWER_IS_BETTER
    cv, before = (ev.winner_cv.get(metric), parent.cv.get(metric)) if metric else (None, None)
    evidence.update(added_feature=added[0], direction="lower_is_better" if lower else "higher_is_better",
                    parent_cv_score=before, cv_score=cv)
    if cv is None or before is None:
        return _not_evaluated("new_feature", "cv_missing", "new_feature.missing_cv", evidence)
    if not lower and metric not in BOUNDED_SCORE_METRICS:
        return _not_evaluated("new_feature", "unbounded_metric", "new_feature.unbounded", evidence)
    jump = (before - cv) if lower else (cv - before)
    remaining = before if lower else 1.0 - before
    closed = jump / remaining if remaining > 0 else 0.0
    spread = math.hypot(ev.winner_cv_std.get(metric) or 0.0, parent.cv_std.get(metric) or 0.0)
    evidence.update(cv_jump=_r(jump), residual_closed_fraction=_r(closed),
                    jump_in_cv_std=_r(jump / spread) if spread else None,
                    threshold={"residual_closed": NEW_FEATURE_WARN, "residual_closed_fail": NEW_FEATURE_FAIL,
                               "cv_std_multiple": NEW_FEATURE_NOISE_STD, "paired_alpha": NEW_FEATURE_ALPHA})
    # Paired by fold index (same SplitPlan -> the same outer folds); a fold missing a finite
    # score on either side drops that pair only. Caveat (Nadeau-Bengio): CV folds share training
    # rows, so the paired t statistic is optimistic; the 0.01 level and the 50% / 80% effect
    # sizes are what keep it from firing on ordinary changes.
    pairs = [(c, q) for c, q in ((_fold_value(a, metric), _fold_value(b, metric))
                                 for a, b in zip(ev.winner_fold_metrics, parent.fold_metrics))
             if c is not None and q is not None] if len(ev.winner_fold_metrics) == len(parent.fold_metrics) else []
    if len(pairs) >= NEW_FEATURE_MIN_FOLDS:
        deltas = np.asarray([c - q for c, q in pairs])
        deltas = -deltas if lower else deltas
        sd = float(np.std(deltas, ddof=1))
        t_value = float(deltas.mean()) / (sd / math.sqrt(len(deltas))) if sd > 0 else (
            math.inf if deltas.mean() > 0 else 0.0)
        critical = float(student_t.ppf(1.0 - NEW_FEATURE_ALPHA, len(deltas) - 1))
        evidence.update(paired_folds=len(deltas), paired_mean_delta=_r(float(deltas.mean())), paired_t=_r(t_value),
                        t_critical=_r(critical))
        if closed >= NEW_FEATURE_WARN and t_value >= critical:
            same = bool(ev.winner_family) and ev.winner_family == parent.winner_family
            status = "fail" if closed >= NEW_FEATURE_FAIL and same else "warning"
            family = ("new_feature.same_family",) if same else ()
            return _finding("new_feature", status, evidence, ("new_feature.outsized", *family, "new_feature.paired"),
                            "investigate_leakage")
        return _finding("new_feature", "pass", evidence, ("new_feature.pass",))
    if closed >= NEW_FEATURE_WARN and jump > NEW_FEATURE_NOISE_STD * spread:  # unpaired: at most a warning
        return _finding("new_feature", "warning", evidence, ("new_feature.outsized", "new_feature.unpaired"),
                        "investigate_leakage")
    return _finding("new_feature", "pass", evidence, ("new_feature.pass",))
