"""Monitoring calculator (P7.4-A): training reference, PSI drift and outcome metrics. Pure.

The reference is built at the run's final-fit step ONLY from the training partition (the CV
pool) and the locked winner's out-of-fold CV scores, and stored in the model package manifest
as ``reference_summary``. A monitoring window compares the pipeline-input rows of new scorings
(prepared exactly as scoring prepared them) and their stored scores with it. The final holdout
is never a reference and never an outcome here.

PSI is computed in code (no Evidently: a large dependency tree for a simple formula) on bucket
COUNTS with 0.5 added to every bucket of both sides (no fixed floor that turns an empty bucket
into a large term): ``sum((a - e) * ln(a / e))``. Numeric columns use up to 10 quantile bins of
the training values (empty bins merged; a constant column keeps one edge at its value);
categorical columns use the top training categories seen at least 5 times and in 1% of rows,
plus an ``other`` bucket (unseen values land there); missing values are always their own bucket.
A feature counts as ``watch`` / ``alert`` only when its PSI passes the code-owned threshold AND
a G-test on its bucket counts has p below 0.01 / features compared (Bonferroni): noise from small
windows or many features never raises an alarm. Date inputs (epoch seconds) always move later
over time; they are shown with their date ranges but never counted.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

REFERENCE_FORMAT = "dclab.monitoring.reference.v2"
N_BINS = 10
SMOOTHING = 0.5  # added to every bucket count of both sides before shares
TOP_CATEGORIES = 10
CATEGORY_MIN_COUNT = 5  # a value is a named bucket only when this frequent ...
CATEGORY_MIN_SHARE = 0.01  # ... and at least this share of the training rows
LABEL_MAX = 60
SPELLING_MAX = 256  # outcome matching uses full text up to this length (never the 60-character label)
TARGET_SPELLINGS_MAX = 10
REFERENCE_FEATURES_MAX = 200
REFERENCE_ROWS_MAX = 200_000
SHOWN_FEATURES = 20
SHOWN_DATES = 5
PER_CLASS_MAX = 50
PSI_WATCH = 0.10
PSI_ALERT = 0.20
SIGNIFICANCE = 0.01  # family-wise, Bonferroni over the features compared
FLAGGED_SHIFT_WATCH = 0.30  # relative change of the flagged share vs cross-validation ...
FLAGGED_ABS_WATCH = 0.02  # ... and at least 2 percentage points, both with p < SIGNIFICANCE
SEED = 0
# Older packages without recorded target spellings: only these explicit tokens are outcomes.
_YES = frozenset({"1", "true", "yes", "y"})
_NO = frozenset({"0", "false", "no", "n"})


# --- values ---------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A value as text without control characters (integral floats read as codes give their
    integer text); None for missing values."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            value = int(value)
    return "".join(ch for ch in str(value) if ch.isprintable())


def category_label(value: Any) -> str | None:
    """One categorical value as stored and shown in drift buckets: ``_text`` capped at
    ``LABEL_MAX`` characters. Outcome matching never uses this capped form."""

    text = _text(value)
    return None if text is None else text[:LABEL_MAX]


def _label_counts(series: pd.Series) -> tuple[Counter, int]:
    """(count per label, missing rows), labelling each distinct value once."""

    counts: Counter = Counter()
    for value, count in series.value_counts(dropna=True).items():
        label = category_label(value)
        if label is not None:
            counts[label] += int(count)
    return counts, int(len(series) - sum(counts.values()))


def _numeric(series: pd.Series) -> np.ndarray:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan, copy=True)
    values[~np.isfinite(values)] = np.nan
    return values


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def _iso_day(seconds: float | None) -> str | None:
    if seconds is None or not math.isfinite(seconds):
        return None
    try:
        return datetime.fromtimestamp(float(seconds), UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def psi(expected: Sequence[float], actual: Sequence[float]) -> float:
    """PSI of two bucket COUNT vectors with ``SMOOTHING`` added to every bucket of both sides."""

    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    if e.shape != a.shape:
        raise ValueError("expected and actual need the same buckets")
    e = (e + SMOOTHING) / (e.sum() + SMOOTHING * len(e))
    a = (a + SMOOTHING) / (a.sum() + SMOOTHING * len(a))
    return float(np.sum((a - e) * np.log(a / e)))


def p_value(expected: Sequence[float], actual: Sequence[float]) -> float:
    """G-test of homogeneity on the 2 x buckets count table (buckets empty on both sides dropped)."""

    from scipy.stats import chi2_contingency

    table = np.vstack([np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)])
    table = table[:, table.sum(axis=0) > 0]
    if table.shape[1] < 2 or (table.sum(axis=1) == 0).any():
        return 1.0
    return float(chi2_contingency(table, correction=False, lambda_="log-likelihood")[1])


def psi_status(value: float) -> str:
    return "alert" if value >= PSI_ALERT else "watch" if value >= PSI_WATCH else "ok"


def gated_status(value: float, p: float, alpha: float) -> str:
    """The PSI level only when the shift is also statistically clear, else ``ok``."""

    return psi_status(value) if p < alpha else "ok"


# --- buckets --------------------------------------------------------------------------------


def _edges(present: np.ndarray) -> list[float]:
    """Interior quantile edges of the training values; a bin that holds no training value is
    merged into its neighbour. Bins are right-closed: (e[i-1], e[i]]. A constant column keeps
    one edge at its value, so values above it show as a change."""

    if not len(present):
        return []
    if float(present.min()) == float(present.max()):
        return [float(present[0])]
    edges = np.unique(np.quantile(present, np.linspace(0.0, 1.0, N_BINS + 1)[1:-1]))
    while len(edges):
        counts = np.bincount(np.searchsorted(edges, present, side="left"), minlength=len(edges) + 1)
        empty = np.flatnonzero(counts == 0)
        if not len(empty):
            break
        edges = np.delete(edges, min(int(empty[0]), len(edges) - 1))
    return [float(edge) for edge in edges]


def numeric_counts(values: np.ndarray, edges: Sequence[float]) -> list[int]:
    """Rows per bin, then the missing rows."""

    missing = np.isnan(values)
    bins = np.searchsorted(np.asarray(edges, dtype=float), values[~missing], side="left")
    return [int(count) for count in np.bincount(bins, minlength=len(edges) + 1)] + [int(missing.sum())]


def categorical_counts(series: pd.Series, categories: Sequence[str]) -> list[int]:
    """Rows per category, then ``other`` (incl. unseen), then missing."""

    counts, missing = _label_counts(series)
    named = [int(counts.get(category, 0)) for category in categories]
    return named + [int(sum(counts.values()) - sum(named)), missing]


def _shares(counts: Sequence[int]) -> list[float]:
    total = float(sum(counts))
    return [float(count) / total if total else 0.0 for count in counts]


def numeric_reference(series: pd.Series, *, kind: str = "numeric") -> dict[str, Any]:
    values = _numeric(series)
    present = values[~np.isnan(values)]
    edges = _edges(present)
    entry: dict[str, Any] = {"kind": kind, "edges": edges, "counts": numeric_counts(values, edges),
                             "mean": _round(float(present.mean()), 6) if len(present) else None}
    if kind == "date":
        entry.update(min=float(present.min()) if len(present) else None,
                     max=float(present.max()) if len(present) else None)
    return entry


def categorical_reference(series: pd.Series) -> dict[str, Any]:
    counts, _missing = _label_counts(series)
    floor = max(CATEGORY_MIN_COUNT, math.ceil(CATEGORY_MIN_SHARE * len(series)))
    frequent = sorted(((label, n) for label, n in counts.items() if n >= floor), key=lambda item: (-item[1], item[0]))
    categories = [label for label, _n in frequent[:TOP_CATEGORIES]]
    return {"kind": "categorical", "categories": categories, "counts": categorical_counts(series, categories)}


# --- reference (training time) --------------------------------------------------------------


def target_spellings(original: pd.Series, coded: pd.Series) -> dict[str, Any] | None:
    """The binary target's label vocabulary: which spellings meant 1 and which 0 (most frequent
    first, at most 10 each, full text up to ``SPELLING_MAX``; nothing is fitted). ``coded`` is
    the 0/1 target training used. None when either class has no usable spelling."""

    if len(original) == 0 or len(original) != len(coded):
        return None
    pairs = pd.DataFrame({"value": original.to_numpy(dtype=object), "code": pd.to_numeric(coded, errors="coerce")
                          .to_numpy()}).dropna()
    counts: dict[int, Counter] = {0: Counter(), 1: Counter()}
    for (value, code), n in pairs.groupby(["value", "code"], sort=False).size().items():  # distinct pairs only
        text = (_text(value) or "").strip()
        if text and len(text) <= SPELLING_MAX and int(code) in counts:
            counts[int(code)][text] += int(n)
    out = {name: [text for text, _n in sorted(counts[code].items(), key=lambda item: (-item[1], item[0]))]
           [:TARGET_SPELLINGS_MAX] for name, code in (("positive", 1), ("negative", 0))}
    return out if out["positive"] and out["negative"] else None


def informative_spellings(spellings: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``spellings`` unless they are only 0 / 1 (a target already coded, or numeric): then the
    uploaded words are unknown and outcomes fall back to the yes / no tokens."""

    if not spellings or not spellings.get("positive") or not spellings.get("negative"):
        return None
    if set(spellings["positive"]) | set(spellings["negative"]) <= {"0", "1"}:
        return None
    return {"positive": list(spellings["positive"]), "negative": list(spellings["negative"])}


def _prediction_reference(
    task_type: str, oof_scores: Sequence[np.ndarray], threshold: float | None, class_labels: Sequence[Any] | None,
    folds: str,
) -> dict[str, Any] | None:
    if not oof_scores:
        return None
    scores = np.concatenate([np.asarray(part, dtype=float) for part in oof_scores], axis=0)
    base = {"basis": "out_of_fold_cv", "folds": folds, "n_rows": int(len(scores))}
    if task_type == "binary" and threshold is not None:
        flagged = int(np.sum(scores >= float(threshold)))
        return {**base, "column": "probability", **numeric_reference(pd.Series(scores)),
                "threshold": float(threshold), "flagged_count": flagged,
                "flagged_share": _round(flagged / len(scores), 6) if len(scores) else None}
    if task_type == "multiclass" and class_labels and scores.ndim == 2:
        predicted = [class_labels[int(code)] for code in scores.argmax(axis=1)]
        return {**base, "column": "label", **categorical_reference(pd.Series(predicted, dtype=object))}
    if task_type == "regression":
        return {**base, "column": "prediction", **numeric_reference(pd.Series(scores))}
    return None


def build_reference(
    frame: pd.DataFrame,
    *,
    numeric: Sequence[str],
    categorical: Sequence[str],
    task_type: str,
    oof_scores: Sequence[np.ndarray],
    threshold: float | None,
    class_labels: Sequence[Any] | None,
    date_columns: Sequence[str] = (),
    priority: Sequence[str] = (),
    target: Mapping[str, Any] | None = None,
    folds: str = "all",
) -> dict[str, Any]:
    """The ``reference_summary`` of a model package. ``frame`` holds TRAINING-partition rows of
    the pipeline input columns only; ``oof_scores`` the winner's out-of-fold CV scores (only the
    fold rows the threshold was locked on for time-ordered CV, ``folds="last"``). ``priority``:
    columns by CV importance, monitored first when there are more than 200."""

    rows = frame if len(frame) <= REFERENCE_ROWS_MAX else frame.sample(n=REFERENCE_ROWS_MAX, random_state=SEED)
    numeric_set, categorical_set, dates = set(numeric), set(categorical), set(date_columns)
    names = [name for name in frame.columns if name in numeric_set or name in categorical_set]
    ranked = [name for name in priority if name in set(names)]
    chosen = (ranked + [name for name in names if name not in set(ranked)])[:REFERENCE_FEATURES_MAX]
    features = []
    for name in sorted(chosen, key=names.index):
        if name in categorical_set and name not in numeric_set:
            entry = categorical_reference(rows[name])
        else:
            entry = numeric_reference(rows[name], kind="date" if name in dates else "numeric")
        features.append({"name": str(name), **entry})
    return {
        "format": REFERENCE_FORMAT,
        "basis": "training_partition",
        "n_rows": int(len(frame)),
        "n_rows_used": int(len(rows)),
        "features": features,
        "features_total": len(names),
        "features_not_monitored": len(names) - len(features),
        "prediction": _prediction_reference(task_type, oof_scores, threshold, class_labels, folds),
        "target": dict(target) if target else None,
    }


def manifest_contract(manifest: Mapping[str, Any]) -> tuple[list[str], list[str], list[str], dict[str, tuple[Any, ...]]]:
    """The pipeline input contract as the package manifest recorded it (for
    ``batch_scoring.contract_spec``), so a window replays scoring's preparation without loading
    the model. Categories beyond the manifest's cap only steer value types, never PSI."""

    columns = [column for column in ((manifest.get("feature_contract") or {}).get("columns") or [])
               if isinstance(column, dict) and column.get("name")]
    names = [str(column["name"]) for column in columns]
    numeric = [str(column["name"]) for column in columns if column.get("role") == "numeric"]
    categorical = [str(column["name"]) for column in columns if column.get("role") == "categorical"]
    categories = {str(column["name"]): tuple(column.get("allowed_values") or ())
                  for column in columns if column.get("role") == "categorical"}
    return names, numeric, categorical, categories


def usable_reference(manifest: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The manifest's reference summary when this calculator can read it, else None (a model
    saved before monitoring existed, or another format)."""

    reference = (manifest or {}).get("reference_summary")
    if not isinstance(reference, dict) or reference.get("format") != REFERENCE_FORMAT:
        return None
    return reference if isinstance(reference.get("features"), list) else None


# --- window comparison ----------------------------------------------------------------------


def _top_change(entry: Mapping[str, Any], counts: Sequence[int]) -> dict[str, Any]:
    shares, expected = _shares(counts), _shares(entry["counts"])
    changes = [actual - reference for actual, reference in zip(shares, expected)]
    index = int(np.argmax(np.abs(changes))) if changes else 0
    categories = list(entry["categories"])
    bucket = "category" if index < len(categories) else "other" if index == len(categories) else "missing"
    return {"bucket": bucket, "value": categories[index] if bucket == "category" else None,
            "share": _round(shares[index]), "share_reference": _round(expected[index])}


def _compare_feature(entry: Mapping[str, Any], series: pd.Series, kind: str) -> tuple[dict[str, Any], float, float]:
    if kind in ("numeric", "date"):
        values = _numeric(series)
        counts = numeric_counts(values, entry["edges"])
        present = values[~np.isnan(values)]
        mean = float(present.mean()) if len(present) else None
        if kind == "date":
            extra: dict[str, Any] = {
                "range": [_iso_day(float(present.min())), _iso_day(float(present.max()))] if len(present) else None,
                "range_reference": [_iso_day(entry.get("min")), _iso_day(entry.get("max"))],
                "later_than_training": bool(len(present) and entry.get("max") is not None
                                            and float(present.max()) > float(entry["max"])),
            }
        else:
            ref_mean = entry.get("mean")
            extra = {"mean": _round(mean, 6), "mean_reference": ref_mean,
                     "mean_change_relative": _round((mean - ref_mean) / abs(ref_mean))
                     if mean is not None and ref_mean not in (None, 0) else None}
    else:
        counts = categorical_counts(series, entry["categories"])
        extra = {"top_change": _top_change(entry, counts)}
    shares, reference = _shares(counts), _shares(entry["counts"])
    row = {"name": category_label(entry.get("name")) or "", "kind": kind, "missing_share": _round(shares[-1]),
           "missing_share_reference": _round(reference[-1]), **extra}
    return row, psi(entry["counts"], counts), p_value(entry["counts"], counts)


def compare_inputs(frame: pd.DataFrame, reference: Mapping[str, Any], *, date_columns: Sequence[str] = ()) -> dict[str, Any]:
    """Per original feature: PSI, G-test p, gated status, missing-share change and a numeric
    mean change, the categorical bucket that moved most or (dates) the date ranges. Top
    ``SHOWN_FEATURES`` by PSI plus counts of the rest; dates are shown but never counted."""

    dates = set(date_columns)
    compared: list[tuple[dict[str, Any], float, float]] = []
    for entry in reference.get("features") or []:
        name = entry.get("name")
        if name not in frame.columns:
            continue
        kind = "date" if entry.get("kind") == "date" or name in dates else str(entry.get("kind"))
        if kind == "date" and "edges" not in entry:
            continue  # a categorical entry the scoring plan now parses as a date: not comparable
        compared.append(_compare_feature(entry, frame[name], kind))
    counted = [item for item in compared if item[0]["kind"] != "date"]
    alpha = SIGNIFICANCE / max(1, len(counted))
    rows, date_rows = [], []
    for row, value, p in compared:
        row.update(psi=_round(value), p_value=_round(p, 6), significant=bool(p < alpha))
        if row["kind"] == "date":
            row.update(status="ok", counted=False)
            date_rows.append(row)
        else:
            row.update(status=gated_status(value, p, alpha), counted=True)
            rows.append(row)
    rows.sort(key=lambda row: (-(row["psi"] or 0.0), row["name"]))
    date_rows.sort(key=lambda row: row["name"])
    counts = Counter(row["status"] for row in rows)
    return {
        "available": True,
        "n_rows_compared": int(len(frame)),
        "features": rows[:SHOWN_FEATURES] + date_rows[:SHOWN_DATES],
        "features_compared": len(rows),
        "features_not_shown": max(0, len(rows) - SHOWN_FEATURES),
        "features_not_monitored": int(reference.get("features_not_monitored") or 0),
        "dates_compared": len(date_rows),
        "status_counts": {status: int(counts.get(status, 0)) for status in ("alert", "watch", "ok")},
        "max_psi": rows[0]["psi"] if rows else 0.0,
        "significance": {"test": "g_test_bonferroni", "alpha": SIGNIFICANCE, "per_feature_alpha": _round(alpha, 8)},
    }


def _flagged_change(present: np.ndarray, reference: Mapping[str, Any]) -> dict[str, Any]:
    from scipy.stats import chi2_contingency

    n, n_ref = int(len(present)), int(reference.get("n_rows") or 0)
    flagged = int(np.sum(present >= float(reference["threshold"])))
    ref_flagged = int(reference.get("flagged_count") or round(float(reference["flagged_share"]) * n_ref))
    share, base = (flagged / n if n else None), float(reference["flagged_share"])
    change = (share - base) / base if share is not None and base > 0 else None
    p = 1.0
    table = np.array([[flagged, n - flagged], [ref_flagged, n_ref - ref_flagged]], dtype=float)
    if n and n_ref and (table.sum(axis=0) > 0).all():
        p = float(chi2_contingency(table, correction=True)[1])
    moved = (share is not None and change is not None and abs(change) >= FLAGGED_SHIFT_WATCH
             and abs(share - base) >= FLAGGED_ABS_WATCH and p < SIGNIFICANCE)
    return {"flagged_share": _round(share), "flagged_share_reference": _round(base),
            "flagged_share_change_relative": _round(change), "flagged_p_value": _round(p, 6),
            "flagged_status": "watch" if moved else "ok"}


def compare_predictions(scored: pd.DataFrame, reference: Mapping[str, Any] | None, *, min_rows: int) -> dict[str, Any]:
    """PSI of the stored scores vs the out-of-fold CV scores (gated by a G-test at p < 0.01);
    binary: flagged share vs the share the locked threshold flagged in cross-validation."""

    if not reference:
        return {"available": False, "reason_code": "no_reference"}
    column = reference.get("column")
    if column not in scored.columns:
        return {"available": False, "reason_code": "scores_missing"}
    numeric = reference.get("kind") == "numeric"
    values = _numeric(scored[column]) if numeric else None
    present = values[~np.isnan(values)] if values is not None else None
    n = int(len(present)) if present is not None else int(scored[column].notna().sum())
    if n < min_rows:
        return {"available": False, "reason_code": "too_few_scores", "n_scores": n}
    counts = (numeric_counts(values, reference["edges"]) if numeric
              else categorical_counts(scored[column], reference["categories"]))
    value, p = psi(reference["counts"], counts), p_value(reference["counts"], counts)
    out: dict[str, Any] = {"available": True, "column": column, "kind": reference.get("kind"), "n_scores": n,
                           "psi": _round(value), "p_value": _round(p, 6),
                           "status": gated_status(value, p, SIGNIFICANCE)}
    if reference.get("flagged_share") is not None and present is not None:
        out.update(_flagged_change(present, reference))
    return out


_SEVERITY = {"ok": 0, "watch": 1, "alert": 2}


def window_verdict(min_rows: int, input_drift: Mapping[str, Any], prediction_drift: Mapping[str, Any]) -> tuple[str, str]:
    """(status, summary_code) from code-owned thresholds. Never an alert from a model without a
    training reference or from fewer than ``min_rows`` compared input rows (``not_enough_data``)."""

    if not input_drift.get("available"):
        return "not_enough_data", str(input_drift.get("reason_code") or "no_reference")
    if int(input_drift.get("n_rows_compared") or 0) < min_rows:
        return "not_enough_data", "too_few_rows"
    counts = input_drift.get("status_counts") or {}
    inputs = "alert" if counts.get("alert") else "watch" if counts.get("watch") else "ok"
    scores = str(prediction_drift.get("status") or "ok") if prediction_drift.get("available") else "ok"
    if prediction_drift.get("flagged_status") == "watch" and scores == "ok":
        scores = "watch"
    status = max(inputs, scores, key=_SEVERITY.__getitem__)
    moved = (inputs != "ok", scores != "ok")
    code = {(True, True): "inputs_and_scores_moved", (True, False): "inputs_moved",
            (False, True): "scores_moved", (False, False): "stable"}[moved]
    return status, code


# --- outcomes (real production labels; never the final test set) ---------------------------


_AMBIGUOUS = object()


def _match_codes(values: pd.Series, spellings: Mapping[str, float]) -> tuple[pd.Series, int]:
    """(code / NaN per value, non-missing values matching no spelling). A value matches its exact
    full text, else its stripped text, else (when unambiguous) its stripped text ignoring case;
    a spelling shared by two codes never matches. Each distinct value is resolved once."""

    stripped: dict[str, Any] = {}
    folded: dict[str, Any] = {}
    for spelling, code in spellings.items():
        for table, key in ((stripped, spelling.strip()), (folded, spelling.strip().casefold())):
            table[key] = code if table.get(key, code) == code else _AMBIGUOUS
    exact = {spelling: code for spelling, code in spellings.items() if stripped.get(spelling.strip()) is not _AMBIGUOUS}

    def resolve(text: str) -> Any:
        for table, key in ((exact, text), (stripped, text.strip()), (folded, text.strip().casefold())):
            code = table.get(key)
            if code is not None:
                return None if code is _AMBIGUOUS else code
        return None

    resolved: dict[str, Any] = {}
    codes, unrecognised = [], 0
    for value in values.tolist():
        text = _text(value)
        if text is None or not text.strip():
            codes.append(np.nan)
            continue
        if text not in resolved:
            resolved[text] = resolve(text)
        code = resolved[text]
        if code is None:
            unrecognised += 1
            codes.append(np.nan)
        else:
            codes.append(float(code))
    return pd.Series(codes, index=values.index, dtype=float), unrecognised


def binary_outcome_codes(values: pd.Series, target: Mapping[str, Any] | None) -> tuple[pd.Series, int]:
    """(1.0 / 0.0 / NaN per value, non-missing values recognised as neither class). Only the
    training target's own spellings count; a package without them (saved before monitoring
    recorded them, or a target coded before the run) accepts only yes / no tokens."""

    if target and target.get("positive") and target.get("negative"):
        spellings = {**{str(s): 0.0 for s in target["negative"]}, **{str(s): 1.0 for s in target["positive"]}}
        if set(map(str, target["negative"])) & set(map(str, target["positive"])):
            spellings = {}
    else:
        spellings = {**{s: 0.0 for s in _NO}, **{s: 1.0 for s in _YES}}
    return _match_codes(values, spellings)


def multiclass_outcome_codes(values: pd.Series, class_labels: Sequence[Any]) -> tuple[pd.Series, int]:
    """(class index / NaN per value, non-missing values matching no training class label), with
    the same exact, stripped, then case-insensitive rule as binary outcomes."""

    spellings: dict[str, float] = {}
    for index, label in enumerate(class_labels):
        text = _text(label)
        if text is not None:
            spellings[text] = float(index)
    return _match_codes(values, spellings)


def _not_enough(n: int, **extra: Any) -> dict[str, Any]:
    return {"status": "not_enough_labels", "n_labelled": int(n), **extra}


def outcome_metrics(
    task_type: str,
    outcomes: pd.Series,
    scored: pd.DataFrame,
    *,
    metric: str | None,
    threshold: float | None,
    class_labels: Sequence[Any] | None,
    min_labels: int,
    min_per_class: int,
) -> dict[str, Any]:
    """Metrics on joined rows only (``outcomes`` aligned with ``scored``; binary outcomes already
    coded 1 / 0 / NaN by ``binary_outcome_codes``): the run's primary metric plus
    precision/recall/confusion at the locked threshold (binary), accuracy/macro F1 (multiclass)
    or MAE/RMSE/R2 (regression). Below the minimums: ``not_enough_labels``."""

    from app.engine.evaluation.metrics import (
        classification_metrics,
        multiclass_metrics,
        regression_metrics,
        selection_metric_value,
    )

    outcomes = outcomes.reset_index(drop=True)
    scored = scored.reset_index(drop=True)
    if task_type == "binary":
        y = pd.to_numeric(outcomes, errors="coerce").to_numpy(dtype=float, copy=True)
        y[~np.isin(y, (0.0, 1.0))] = np.nan
        scores = _numeric(scored["probability"]) if "probability" in scored.columns else np.full(len(y), np.nan)
        keep = ~np.isnan(y) & ~np.isnan(scores)
        truth, pred = y[keep].astype(int), scores[keep]
        per_class = [int((truth == 0).sum()), int((truth == 1).sum())]
        if len(truth) < min_labels or min(per_class) < min_per_class:
            return _not_enough(len(truth), per_class=per_class)
        cut = float(threshold if threshold is not None else 0.5)
        full = classification_metrics(truth, pred, threshold=cut)
        metrics = {key: _round(full[key]) for key in ("precision", "recall", "f1", "accuracy", "roc_auc", "pr_auc")}
        extra: dict[str, Any] = {"confusion": full["confusion_matrix"], "label_positive_rate": _round(float(truth.mean())),
                                 "flagged_rate": _round(float(np.mean(pred >= cut)))}
        kwargs: dict[str, Any] = {"threshold": cut}
    elif task_type == "multiclass":
        labels = list(class_labels or [])
        columns = [f"probability_{label}" for label in labels]
        if not labels or any(column not in scored.columns for column in columns):
            return _not_enough(0)
        mapped = multiclass_outcome_codes(outcomes, labels)[0].fillna(-1).to_numpy(dtype=int)
        keep = mapped >= 0
        truth = mapped[keep]
        pred = scored.loc[keep, columns].to_numpy(dtype=float)
        counts = Counter(int(code) for code in truth)
        per_class = [int(counts.get(index, 0)) for index in range(len(labels))][:PER_CLASS_MAX]
        # At least two classes, and every class that occurs needs ``min_per_class`` labelled rows.
        if len(truth) < min_labels or len(counts) < 2 or min(counts.values()) < min_per_class:
            return _not_enough(len(truth), per_class=per_class)
        full = multiclass_metrics(truth, pred, n_classes=len(labels))
        metrics = {key: _round(full[key]) for key in ("accuracy", "balanced_accuracy", "macro_f1", "weighted_f1")}
        extra, kwargs = {"per_class": per_class}, {"n_classes": len(labels)}
    else:
        y = _numeric(outcomes)
        pred_all = _numeric(scored["prediction"]) if "prediction" in scored.columns else np.full(len(y), np.nan)
        keep = ~np.isnan(y) & ~np.isnan(pred_all)
        truth, pred = y[keep], pred_all[keep]
        if len(truth) < min_labels:
            return _not_enough(len(truth))
        full = regression_metrics(truth, pred)
        metrics = {key: _round(full[key]) for key in ("mae", "rmse", "r2")}
        extra, kwargs = {}, {}
    primary = None
    if metric:
        try:
            primary = _round(selection_metric_value(metric, truth, pred, task_type=task_type, **kwargs))
        except (ValueError, TypeError):  # a metric the joined rows cannot support: shown as None
            primary = None
    return {"status": "measured", "n_labelled": int(len(truth)), "metric": metric, "metric_value": primary,
            "metrics": metrics, **extra}
