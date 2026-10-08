"""P5.1-A train -> test checks over FEATURE columns only (group B).

``SplitFeatures`` holds the model columns of the training partition and of the test
partition (plus the plan's time / group column values), built inside the worker exactly
like the duplicate check's row hashes. No test label, prediction or metric is an input.
Every number derived from test rows is HOLDOUT SCOPE: it is stored under a ``holdout_*``
key or inside ``holdout_comparison`` (people only; ``strip_holdout`` removes it for
agents and service tokens, who read status, severity, recommendation kind and a fixed
message -- ``domain.findings.HOLDOUT_FEATURE_CHECKS``). No row, value, category label or
group id is ever stored. Missing evidence is ``not_evaluated``, never a pass.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, ks_2samp

from app.engine.investigate.checks import (
    COLUMN_DETAIL_LIMIT,
    DUPLICATE_CAPACITY_FACTOR,
    NEAR_UNIQUE_RATIO,
    _finding,
    _not_evaluated,
    row_hashes,
)
from app.engine.features.encode import infer_datetime_format
from app.engine.investigate.types import Finding, SplitFeatures
from app.engine.modeling.holdout_planner import GROUP_HOLDOUT_STRATEGIES, TEMPORAL_HOLDOUT_STRATEGIES
from app.engine.validation.splits import _sortable_timeline

# Bounds for every train -> test check: 50,000 rows per side (seeded sample) and 200 columns
# (recorded as ``columns_truncated``).
SPLIT_MAX_ROWS = 50_000
SPLIT_MAX_COLUMNS = 200
SAMPLE_SEED = 0
# Feature drift: population stability index (0.10 moderate, 0.25 major: industry convention)
# over training-quantile bins (numeric) or the top training categories + "other", with 0.5
# added to every bin count; at most 10 bins and about 10 test rows per bin. A column drifts
# only when it is also significant after a Bonferroni correction over the columns compared
# (family alpha 0.01): the bin table's chi-square test, or a two-sample KS statistic >= 0.10.
# The test score may differ from CV, which stays honest: warn only. At least 30 test rows.
PSI_WARN = 0.10
PSI_MAJOR = 0.25
DRIFT_BINS = 10
DRIFT_ROWS_PER_BIN = 10
DRIFT_SMOOTHING = 0.5
DRIFT_ALPHA = 0.01
KS_WARN = 0.10
DRIFT_MIN_TEST_ROWS = 30
# Temporal shift: under a time-ordered split more than 1% of test rows earlier than the
# latest training row means the model trained on part of its own future (fail); under
# any other split, half the test rows inside the training period means the test score
# measures the same period, not the future (warn).
TEMPORAL_OVERLAP_FAIL = 0.01
TEMPORAL_RANDOM_OVERLAP_WARN = 0.5
TIME_MIN_READABLE = 0.8  # the single reading must read 80% of the rows on EACH side, else not_evaluated
# Missingness shift: a column's missing share moves by 5 percentage points AND a
# two-proportion z of 3.29 (p < 0.001); "new" missingness (none in training) needs at least
# 5 missing test cells and a test rate under which zero missing training cells would have
# had probability < 0.001. Fill-ins may not fit, but nothing is invalid: warn only.
MISSING_SHIFT_WARN = 0.05
MISSING_Z = 3.29
MISSING_NEW_MIN_ROWS = 5
MISSING_NEW_ALPHA = 0.001
# Contamination beyond exact duplicates. Near-duplicates (numbers rounded to 3 significant
# digits, text normalized, not exact copies): the test rows and a seeded 20% slice of training
# rows are matched against the SAME reference, the other 80% of training rows; the test share
# warns when it exceeds the training slice's share by 1 percentage point and 3 standard
# errors. When both shares are >= 1% without such an excess, near-copies spread across a random
# split cannot be told from natural repeats: a pass that says so, never "no contamination".
# Near-duplicates alone never fail. Shared group / entity values
# fail only when a group-disjoint split was planned; under a time-ordered split they are the
# intended design (pass, informational); otherwise 1% of test rows warns. Training rows sharing
# every model value with a row of a different label warn at 1% (training labels only).
CONTAMINATION_WARN = 0.01
NEAR_DUPLICATE_Z = 3.0
NEAR_BASELINE_SLICE = 0.2
NEAR_BASELINE_MIN_ROWS = 60
SIGNIFICANT_DIGITS = 3
# Re-entered records survive finer rounding; natural repeats of correlated or coarse columns do not
# (legitimate shapes measured at 0.0% at 5 digits; records re-entered with <= 1e-5 relative edits keep
# 0.6-6%): near-copies count as dataset copies only if >= 0.5% (and >= 2) of the training baseline
# rows still agree at 5 significant digits, and the rounded columns could identify rows at all.
FINE_DIGITS = 5
FINE_COPY_MIN = 0.005
FINE_COPY_MIN_ROWS = 2


def _r(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), 6)


def _positions(n: int) -> np.ndarray:
    """Every row, or a seeded sample of ``SPLIT_MAX_ROWS`` positions in row order."""

    if n <= SPLIT_MAX_ROWS:
        return np.arange(n)
    return np.sort(np.random.default_rng(SAMPLE_SEED).choice(n, SPLIT_MAX_ROWS, replace=False))


def _sample(values: Any) -> Any:
    return values.iloc[_positions(len(values))] if len(values) > SPLIT_MAX_ROWS else values


def _sampled(evidence: dict[str, Any], *sizes: int) -> tuple[str, ...]:
    """Record (and word) that counts come from a sample when a side was sampled."""

    sampled = any(size > SPLIT_MAX_ROWS for size in sizes)
    evidence.update(sampled=sampled, sample_rows=SPLIT_MAX_ROWS if sampled else None)
    return ("split_checks.sampled",) if sampled else ()


def _columns(split: SplitFeatures) -> tuple[list[str], bool]:
    shared = [c for c in sorted(split.train.columns, key=str) if c in split.test.columns]
    return shared[:SPLIT_MAX_COLUMNS], len(shared) > SPLIT_MAX_COLUMNS


def _numeric(series: Any) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = (expected + DRIFT_SMOOTHING) / (expected.sum() + DRIFT_SMOOTHING * len(expected))
    a = (actual + DRIFT_SMOOTHING) / (actual.sum() + DRIFT_SMOOTHING * len(actual))
    return float(((a - e) * np.log(a / e)).sum())


def _table_p(train_counts: np.ndarray, test_counts: np.ndarray) -> float:
    table = np.vstack([train_counts, test_counts])
    table = table[:, table.sum(axis=0) > 0]
    if table.shape[1] < 2:
        return 1.0
    return float(chi2_contingency(table, correction=False)[1])


def _column_drift(train: pd.Series, test: pd.Series, bins: int) -> dict[str, Any] | None:
    if _numeric(train) and _numeric(test):
        tr = pd.to_numeric(train, errors="coerce").dropna().to_numpy(dtype=float)
        te = pd.to_numeric(test, errors="coerce").dropna().to_numpy(dtype=float)
        if len(tr) < 2 or len(te) < 2:
            return None
        # Bin edges from the TRAINING distribution only; the test rows are counted into them.
        edges = np.unique(np.quantile(tr, np.linspace(0, 1, bins + 1))[1:-1])
        size = len(edges) + 1
        counts = [np.bincount(np.searchsorted(edges, v, side="right"), minlength=size) for v in (tr, te)]
        ks = ks_2samp(tr, te)
        return {"psi": _psi(*counts), "p": _table_p(*counts), "ks": float(ks.statistic), "ks_p": float(ks.pvalue)}
    tr, te = train.dropna().astype(str), test.dropna().astype(str)
    if len(tr) < 2 or len(te) < 2:
        return None
    kept = set(tr.value_counts().index[: max(bins - 1, 1)])
    levels = [*sorted(kept), "\0other"]
    counts = [s.where(s.isin(kept), "\0other").value_counts().reindex(levels, fill_value=0).to_numpy() for s in (tr, te)]
    return {"psi": _psi(*counts), "p": _table_p(*counts), "ks": None, "ks_p": None,
            "unseen_fraction": float((~te.isin(set(tr.unique()))).mean())}


def _strategy_key(check: str, strategy: str | None) -> str:
    if strategy in TEMPORAL_HOLDOUT_STRATEGIES:
        return f"{check}.temporal_split"
    return f"{check}.group_split" if strategy in GROUP_HOLDOUT_STRATEGIES else f"{check}.random_split"


# --- 10. feature drift ------------------------------------------------------------------


def check_feature_drift(split: SplitFeatures | None, split_strategy: str | None = None) -> Finding:
    if split is None:
        return _not_evaluated("feature_drift", "partition_unavailable", "feature_drift.not_evaluated")
    columns, truncated = _columns(split)
    evidence: dict[str, Any] = {"train_rows": int(len(split.train)), "holdout_rows": int(len(split.test)),
                                "split_strategy": split_strategy, "min_rows": DRIFT_MIN_TEST_ROWS,
                                "columns_checked": len(columns), "columns_truncated": truncated}
    if len(split.test) < DRIFT_MIN_TEST_ROWS or len(split.train) < DRIFT_MIN_TEST_ROWS:
        return _not_evaluated("feature_drift", "too_few_rows", "feature_drift.too_few_rows", evidence)
    sample_keys = _sampled(evidence, len(split.train), len(split.test))
    train, test = _sample(split.train), _sample(split.test)
    bins = max(2, min(DRIFT_BINS, len(test) // DRIFT_ROWS_PER_BIN))
    alpha = DRIFT_ALPHA / max(len(columns), 1)  # Bonferroni over the columns compared
    rows: list[dict[str, Any]] = []
    for column in columns:
        drift = _column_drift(train[column], test[column], bins)
        if drift is None:
            continue
        shifted = drift["p"] < alpha
        ks_shift = drift["ks"] is not None and drift["ks"] >= KS_WARN and drift["ks_p"] < alpha
        rows.append({"column": str(column), "psi": _r(drift["psi"]), "ks": _r(drift["ks"]),
                     "unseen_fraction": _r(drift.get("unseen_fraction")),
                     "drifted": bool((drift["psi"] >= PSI_WARN and shifted) or ks_shift),
                     "major": bool(drift["psi"] >= PSI_MAJOR and shifted)})
    if not rows:  # every column empty or unreadable on one side
        return _not_evaluated("feature_drift", "no_readable_columns", "feature_drift.no_readable_columns", evidence)
    rows.sort(key=lambda row: (-row["psi"], row["column"]))
    drifted = [row["column"] for row in rows if row["drifted"]]
    evidence["holdout_comparison"] = {
        "bins": bins, "alpha_per_column": _r(alpha), "drifted_count": len(drifted), "drifted_columns": drifted,
        "major_columns": [row["column"] for row in rows if row["major"]], "max_psi": rows[0]["psi"],
        "max_psi_column": rows[0]["column"], "columns": rows[:COLUMN_DETAIL_LIMIT],
    }
    evidence["threshold"] = {"psi": PSI_WARN, "psi_major": PSI_MAJOR, "ks": KS_WARN, "family_alpha": DRIFT_ALPHA}
    if drifted:
        return _finding("feature_drift", "warning", evidence,
                        ("feature_drift.drift", _strategy_key("feature_drift", split_strategy), *sample_keys),
                        "review_split")
    return _finding("feature_drift", "pass", evidence, ("feature_drift.pass", *sample_keys))


# --- 11. temporal shift -----------------------------------------------------------------


def _times(series: Any) -> tuple[np.ndarray, bool]:
    """Seconds (datetimes) or raw ordinal numbers, NaN where unreadable, over the FULL series
    (vectorized) with ONE reading: the splitter's own timeline reading (``_sortable_timeline``),
    then the format the engine infers for the column (``infer_datetime_format``), then day-first."""

    values = pd.Series(series).reset_index(drop=True)
    if _numeric(values):
        return pd.to_numeric(values, errors="coerce").to_numpy(dtype=float), False
    if pd.api.types.is_datetime64_any_dtype(values):
        parsed = values
    else:
        with warnings.catch_warnings():  # per-element parsing of mixed formats is expected here
            warnings.simplefilter("ignore", UserWarning)
            guessed = infer_datetime_format(values)
            readings = [lambda: _sortable_timeline(values),
                        lambda: pd.to_datetime(values, errors="coerce", format=guessed) if guessed not in (None, "mixed")
                        else None,
                        lambda: pd.to_datetime(values, errors="coerce", dayfirst=True),
                        lambda: pd.to_datetime(values, errors="coerce", utc=True)]  # mixed UTC offsets
            parsed, best = None, -1.0
            for read in readings:
                try:
                    candidate = read()
                except (ValueError, TypeError, OverflowError):  # e.g. mixed offsets without utc=True
                    continue
                if candidate is None or not pd.api.types.is_datetime64_any_dtype(candidate):
                    continue
                rate = float(candidate.notna().mean()) if len(candidate) else 0.0
                if rate > best:
                    parsed, best = candidate, rate
                if rate >= 0.8:
                    break
        if parsed is None:
            return np.full(len(values), np.nan), False
    epoch = pd.Timestamp("1970-01-01", tz=parsed.dt.tz)
    return ((parsed - epoch) / pd.Timedelta(seconds=1)).to_numpy(dtype=float), True


def _when(value: float, is_datetime: bool) -> Any:
    return pd.Timestamp(value, unit="s").isoformat() if is_datetime else _r(value)


def check_temporal_shift(split: SplitFeatures | None, split_strategy: str | None = None) -> Finding:
    if split is None:
        return _not_evaluated("temporal_shift", "partition_unavailable", "temporal_shift.not_evaluated")
    if not split.time_column or split.train_times is None or split.test_times is None:
        return _not_evaluated("temporal_shift", "no_time_column", "temporal_shift.not_applicable")
    evidence: dict[str, Any] = {"time_column": split.time_column, "split_strategy": split_strategy}
    # Parse ONCE, then split: both sides share one reading (two separate guesses could read a
    # day-first test period month-first and invent an overlap).
    if split.all_times is not None and split.time_masks is not None:
        values, tr_dt = _times(split.all_times)
        train_mask, test_mask = (np.asarray(mask, dtype=bool) for mask in split.time_masks)
        tr_all, te_all = values[train_mask], values[test_mask]
    else:
        train_side = pd.Series(split.train_times).reset_index(drop=True)
        values, tr_dt = _times(pd.concat([train_side, pd.Series(split.test_times).reset_index(drop=True)],
                                         ignore_index=True))
        tr_all, te_all = values[: len(train_side)], values[len(train_side):]
    readable = min(float(np.isfinite(tr_all).mean()) if len(tr_all) else 0.0,
                   float(np.isfinite(te_all).mean()) if len(te_all) else 0.0)
    tr, te = tr_all[np.isfinite(tr_all)], te_all[np.isfinite(te_all)]
    if readable < TIME_MIN_READABLE:  # the one reading does not hold on both sides: no verdict
        return _not_evaluated("temporal_shift", "time_unparsable", "temporal_shift.unparsable", evidence)
    start, end = float(tr.min()), float(tr.max())
    before = float((te < end).mean())  # strictly earlier: a test row tied with the last training row is fine
    within = float(((te >= start) & (te <= end)).mean())
    evidence.update(train_rows=int(len(tr)), holdout_rows=int(len(te)), train_start=_when(start, tr_dt),
                    train_end=_when(end, tr_dt),
                    holdout_comparison={
                        "holdout_start": _when(float(te.min()), tr_dt), "holdout_end": _when(float(te.max()), tr_dt),
                        "holdout_gap_days": _r((float(te.min()) - end) / 86_400) if tr_dt else None,
                        "holdout_rows_before_train_end_fraction": _r(before),
                        "holdout_rows_within_train_period_fraction": _r(within)},
                    threshold={"temporal_overlap": TEMPORAL_OVERLAP_FAIL, "same_period": TEMPORAL_RANDOM_OVERLAP_WARN})
    if split_strategy in TEMPORAL_HOLDOUT_STRATEGIES:
        if before > TEMPORAL_OVERLAP_FAIL:
            return _finding("temporal_shift", "fail", evidence, ("temporal_shift.overlap",), "review_split")
        return _finding("temporal_shift", "pass", evidence, ("temporal_shift.ordered",))
    if within >= TEMPORAL_RANDOM_OVERLAP_WARN:
        return _finding("temporal_shift", "warning", evidence, ("temporal_shift.random_split",), "review_split")
    return _finding("temporal_shift", "pass", evidence, ("temporal_shift.periods",))


# --- 12. missingness shift --------------------------------------------------------------


def check_missingness_shift(split: SplitFeatures | None) -> Finding:
    if split is None or not len(split.train) or not len(split.test):
        return _not_evaluated("missingness_shift", "partition_unavailable", "missingness_shift.not_evaluated")
    columns, truncated = _columns(split)
    train, test = _sample(split.train[columns]), _sample(split.test[columns])
    n_tr, n_te = len(train), len(test)
    rows: list[dict[str, Any]] = []
    for column in columns:
        k_te = int(test[column].isna().sum())
        p_tr, p_te = float(train[column].isna().mean()), k_te / n_te
        pooled = (p_tr * n_tr + p_te * n_te) / (n_tr + n_te)
        se = math.sqrt(pooled * (1 - pooled) * (1 / n_tr + 1 / n_te))
        z = (p_te - p_tr) / se if se > 0 else 0.0
        # None missing in training is surprising only if the test rate makes that improbable.
        unseen = p_tr == 0.0 and k_te >= MISSING_NEW_MIN_ROWS and (1.0 - p_te) ** n_tr < MISSING_NEW_ALPHA
        rows.append({"column": str(column), "train_missing_fraction": _r(p_tr), "holdout_missing_fraction": _r(p_te),
                     "shift": _r(p_te - p_tr), "z": _r(z),
                     "shifted": bool(abs(p_te - p_tr) >= MISSING_SHIFT_WARN and abs(z) >= MISSING_Z), "new": unseen})
    rows.sort(key=lambda row: (-abs(row["shift"]), row["column"]))
    shifted = [row["column"] for row in rows if row["shifted"]]
    new = [row["column"] for row in rows if row["new"]]
    evidence: dict[str, Any] = {
        "columns_checked": len(rows), "columns_truncated": truncated, "train_rows": n_tr, "holdout_rows": n_te,
        "train_rows_with_missing_fraction": _r(train.isna().any(axis=1).mean()) if columns else 0.0,
        "holdout_comparison": {
            "holdout_rows_with_missing_fraction": _r(test.isna().any(axis=1).mean()) if columns else 0.0,
            "shifted_count": len(shifted), "shifted_columns": shifted, "new_missing_columns": new,
            "max_shift_column": rows[0]["column"] if rows else None,
            "max_missing_shift_fraction": abs(rows[0]["shift"]) if rows else None,
            "columns": [{k: v for k, v in row.items() if k not in {"shifted", "new"}} for row in rows
                        if row["train_missing_fraction"] or row["holdout_missing_fraction"]][:COLUMN_DETAIL_LIMIT],
        },
        "threshold": {"shift": MISSING_SHIFT_WARN, "z": MISSING_Z, "new_missing_rows": MISSING_NEW_MIN_ROWS,
                      "new_missing_alpha": MISSING_NEW_ALPHA},
    }
    sample_keys = _sampled(evidence, len(split.train), len(split.test))
    keys = (*(("missingness_shift.shift",) if shifted else ()), *(("missingness_shift.new_missing",) if new else ()))
    if keys:
        return _finding("missingness_shift", "warning", evidence, (*keys, *sample_keys), "review_split")
    return _finding("missingness_shift", "pass", evidence, ("missingness_shift.pass", *sample_keys))


# --- 13. contamination beyond exact duplicates --------------------------------------------


def _normalized(frame: pd.DataFrame, digits: int = SIGNIFICANT_DIGITS) -> pd.DataFrame:
    """Numbers rounded to ``digits`` significant digits, text trimmed/lower-cased/
    single-spaced; missing stays missing."""

    out: dict[str, Any] = {}
    for column in frame.columns:
        series = frame[column]
        if _numeric(series):
            values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                magnitude = np.where(values != 0, np.floor(np.log10(np.abs(values))), 0.0)
                scale = np.power(10.0, digits - 1 - np.nan_to_num(magnitude))
                out[column] = np.round(values * scale) / scale
        elif pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.is_bool_dtype(series):
            out[column] = series.to_numpy()
        else:
            text = series.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
            out[column] = text.where(series.notna()).to_numpy()
    return pd.DataFrame(out, columns=list(frame.columns))


def _identifying(train: pd.DataFrame, total_rows: int) -> bool:
    """A repeat cannot be natural: a near-unique column, or >= 100x more combinations than rows."""

    distinct = [int(train[c].nunique(dropna=False)) for c in train.columns]
    near_unique = any(k >= NEAR_UNIQUE_RATIO * max(len(train), 1) for k in distinct)
    capacity = sum(math.log(max(k, 1)) for k in distinct)
    return near_unique or capacity >= math.log(DUPLICATE_CAPACITY_FACTOR * max(total_rows, 1))


def _near_only(norm: np.ndarray, exact: np.ndarray, ref_norm: np.ndarray, ref_exact: np.ndarray) -> np.ndarray:
    """Rows matching a reference row after normalization but not exactly."""

    return np.isin(norm, ref_norm) & ~np.isin(exact, ref_exact)


def check_contamination(split: SplitFeatures | None, split_strategy: str | None = None) -> Finding:
    if split is None or split.train.shape[1] == 0 or not len(split.test):
        return _not_evaluated("contamination", "partition_unavailable", "contamination.not_evaluated")
    columns, truncated = _columns(split)
    kept = _positions(len(split.train))  # the training rows (and their labels) the check reads
    train, test = split.train[columns].iloc[kept], _sample(split.test[columns])
    n_tr, n_te = len(train), len(test)
    evidence: dict[str, Any] = {"train_rows": n_tr, "holdout_rows": n_te, "significant_digits": SIGNIFICANT_DIGITS,
                                "columns_checked": len(columns), "columns_truncated": truncated,
                                "group_column": split.group_column, "min_rows": NEAR_BASELINE_MIN_ROWS}
    if n_tr < NEAR_BASELINE_MIN_ROWS:
        return _not_evaluated("contamination", "no_baseline", "contamination.no_baseline", evidence)
    sample_keys = _sampled(evidence, len(split.train), len(split.test))
    exact_tr, exact_te = row_hashes(train), row_hashes(test)
    norm_frame = _normalized(train)
    norm_tr, norm_te = row_hashes(norm_frame), row_hashes(_normalized(test))
    # One reference for both: a seeded 80% of training rows. The other 20% (the baseline slice)
    # and the test rows are each matched against it.
    order = np.random.default_rng(SAMPLE_SEED).permutation(n_tr)
    cut = max(1, int(round(n_tr * NEAR_BASELINE_SLICE)))
    piece, rest = order[:cut], order[cut:]
    base = float(_near_only(norm_tr[piece], exact_tr[piece], norm_tr[rest], exact_tr[rest]).mean())
    near = int(_near_only(norm_te, exact_te, norm_tr[rest], exact_tr[rest]).sum())
    rate = near / n_te
    floor = max(base, 0.5 / cut)  # half a hit: a zero baseline still has sampling error
    se = math.sqrt(floor * (1 - min(floor, 0.999)) * (1 / n_te + 1 / cut))
    excess, z = rate - base, (rate - base) / se
    comparison: dict[str, Any] = {
        "near_duplicate_holdout_rows": near, "near_duplicate_holdout_fraction": _r(rate),
        "expected_near_duplicate_holdout_rows": round(base * n_te, 1),
        "excess_near_duplicate_holdout_fraction": _r(excess), "near_duplicate_holdout_z": _r(z)}
    evidence.update(baseline_rows=cut, reference_rows=int(len(rest)), train_near_duplicate_fraction=_r(base),
                    holdout_comparison=comparison,
                    threshold={"excess": CONTAMINATION_WARN, "z": NEAR_DUPLICATE_Z, "shared_groups": CONTAMINATION_WARN,
                               "natural_repeats": CONTAMINATION_WARN})
    keys: list[str] = []
    info: list[str] = []
    fail = False
    if excess >= CONTAMINATION_WARN and z >= NEAR_DUPLICATE_Z:
        keys.append("contamination.near_duplicates")  # never a fail on its own
    repeats = base >= CONTAMINATION_WARN and rate >= CONTAMINATION_WARN and not keys
    # Both shares are high (the test share is part of the trigger, so this stays holdout scope).
    # Possible re-entered records only when the rounded columns could identify a row (a near-unique
    # column or >= 100x more combinations than rows, as the duplicate check judges) AND the
    # training near-copies survive rounding to 5 significant digits; otherwise natural repeats.
    identifying = _identifying(norm_frame, n_tr)
    evidence["normalized_columns_identify_rows"] = identifying
    if repeats and identifying:
        fine = row_hashes(_normalized(train, FINE_DIGITS))
        survivors = int(_near_only(fine[piece], exact_tr[piece], fine[rest], exact_tr[rest]).sum())
        evidence.update(fine_digits=FINE_DIGITS, train_fine_near_duplicate_fraction=_r(survivors / cut))
        if survivors >= max(FINE_COPY_MIN_ROWS, FINE_COPY_MIN * cut):
            keys.append("contamination.near_copies_dataset")
    natural = repeats and not keys
    small = not keys and z >= NEAR_DUPLICATE_Z and 0 < excess < CONTAMINATION_WARN
    shared = 0.0
    if split.group_column and split.train_groups is not None and split.test_groups is not None:
        seen = set(pd.Series(split.train_groups).dropna().unique().astype(str))  # every training group
        groups = _sample(pd.Series(split.test_groups)).dropna().astype(str)
        shared = float(groups.isin(seen).sum()) / max(len(groups), 1)
        comparison.update(holdout_shared_group_count=len(set(groups) & seen),
                          holdout_rows_sharing_group_fraction=_r(shared))
        if split_strategy in GROUP_HOLDOUT_STRATEGIES:
            if shared > 0:  # a group-disjoint split was planned and still shares groups
                keys.append("contamination.shared_groups")
                fail = True
        elif split_strategy in TEMPORAL_HOLDOUT_STRATEGIES:
            if shared > 0:  # entities observed over time, split forward in time: the intended design
                info.append("contamination.shared_groups_temporal")
        elif shared >= CONTAMINATION_WARN:
            keys.append("contamination.shared_groups_random")
    labels = split.train_labels
    if labels is not None and len(labels) == len(split.train):
        keyed = pd.DataFrame({"key": exact_tr, "label": pd.Series(labels).iloc[kept].astype(str).to_numpy()})
        rows = int((keyed.groupby("key")["label"].transform("nunique") > 1).sum())
        evidence.update(train_conflicting_label_rows=rows, train_conflicting_label_fraction=_r(rows / max(n_tr, 1)))
        if rows / max(n_tr, 1) >= CONTAMINATION_WARN and _identifying(train, n_tr):
            keys.append("contamination.conflicting_labels")
    head = (("contamination.natural_repeats",) if natural else ("contamination.small_excess",) if small
            else ("contamination.pass",))
    if not keys:
        disjoint = ("contamination.groups_disjoint",) if split.group_column and "holdout_shared_group_count" in \
            comparison and not shared else ()
        return _finding("contamination", "pass", evidence, (*head, *disjoint, *info, *sample_keys))
    split_keys = {"contamination.shared_groups", "contamination.shared_groups_random"}
    recommendation = "review_split" if split_keys & set(keys) else "deduplicate"
    extra = ("contamination.natural_repeats",) if natural else ()
    return _finding("contamination", "fail" if fail else "warning", evidence, (*keys, *extra, *info, *sample_keys),
                    recommendation)
