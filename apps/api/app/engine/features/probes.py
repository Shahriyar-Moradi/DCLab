"""Data-level detectors for the feature-engineering contract (P5.0-A).

A declared fit scope (``contract.py``) is a promise; these probes test the
implementation behind it by changing rows a correct transform may not read and
checking that what it produces for the other rows does not move:

* ``fitted_state_probe`` — the RELIABLE check for a fitted step: for each fold,
  perturb ALL of its validation rows and compare the step's fitted state
  (``statistics_``, ``categories_``, ``scores_`` / ``get_support()``) for that
  fold; a state fit on the fold's training rows only is unchanged. Use it for
  imputers, scalers, one-hot vocabularies and selectors.
* ``fold_influence_probe`` — an OUTPUT check: for each CV fold, perturb HALF of
  the validation rows (their target, or their feature values) and re-run the
  out-of-fold feature producer with the same folds; the other half's features
  must not move (with ``check_perturbed_rows`` the perturbed rows must not move
  either, which is the right setting when only target columns are perturbed:
  a validation row's feature may never read its own target). It catches a
  leak only when the leaked statistic visibly reaches an observed row: a
  full-table most-frequent imputer whose mode does not flip, or a one-hot
  vocabulary, is NOT caught here — use ``fitted_state_probe`` for those.
* ``self_target_influence_probe`` — perturb ONE training row's target and
  check (a) that row's own training-time feature is unchanged and (b) at least
  one PEER (another training row with the same ``peer_column`` value) is also
  unchanged. (a) failing on any row is a leak (naive in-fold encoder). (b) is
  decided on the SHARE of probed rows with peers whose peers ALL moved: a
  cross-fitted encoder with K disjoint inner folds leaves same-inner-fold peers
  untouched, so only rows without a same-fold peer count (about e^(-m/K) for a
  category of m rows); a leave-one-out encoder moves every peer of every row
  (share ≈ 1). The probe flags when that share exceeds
  ``PEER_LEAK_SHARE`` (0.5).
* ``future_influence_probe`` — truncation replay: for each probed row,
  recompute the features on ``rows strictly before the row's time ∪ {the
  row}`` and compare the row's output with the full-frame output; counts,
  frequencies, "is last event", days-to-next, sort+cumsum+shift tie bugs and
  same-timestamp reads all differ under truncation. With ``perturb`` columns
  it also changes the row's OWN values in the truncated frame and expects the
  as-of feature unchanged (a history aggregate never reads the row itself).
  Every row is probed up to ``EXHAUSTIVE_ROWS``; above that, a sample that
  always includes every row with a same-timestamp sibling and each group's
  first and last row (``group_column``).

Pure functions over pandas frames; no database, no engine state. The contract
tests run them against the real engine steps and the real CV loop and against
deliberately broken implementations; P5.1 can reuse them as the time-travel and
new-feature checks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

Fold = tuple[np.ndarray, np.ndarray]
FoldProducer = Callable[[pd.DataFrame, Sequence[Fold]], pd.DataFrame]
RowProducer = Callable[[pd.DataFrame], pd.DataFrame]
TrainProducer = Callable[[pd.DataFrame, np.ndarray], pd.DataFrame]
StateFitter = Callable[[pd.DataFrame, np.ndarray], Mapping[str, Any]]

DEFAULT_PROBE_ROWS = 24
EXHAUSTIVE_ROWS = 2000
# Integer columns with at most this many distinct values are discrete (classes), not quantities.
DISCRETE_MAX_LEVELS = 10
# self_target_influence_probe: share of probed rows (with peers) whose peers all moved that
# marks a leave-one-out style leak; a K-fold cross-fit stays near e^(-m/K), LOO near 1.
PEER_LEAK_SHARE = 0.5
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ProbeResult:
    """``leaked`` is True when some observed row's features (or a fold's fitted state)
    changed; ``affected`` lists the folds or probe row positions where it happened,
    ``tested`` how many folds/rows the probe could test and ``max_abs_change`` the
    largest numeric move seen."""

    probe: str
    leaked: bool
    tested: int
    affected: tuple[int, ...] = ()
    max_abs_change: float = 0.0
    columns: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "probe": self.probe,
            "leaked": self.leaked,
            "tested": self.tested,
            "affected": list(self.affected),
            "max_abs_change": self.max_abs_change,
            "columns": list(self.columns),
        }


def _runner_up_other(series: pd.Series) -> Callable[[Any], Any]:
    """Maps a value to the runner-up EXISTING level (the most frequent level that is not
    the mode) when it differs from the value, else the next level down: the perturbed
    rows pile onto one existing level, which flips a mode or a class balance with the
    fewest rows and never invents a level."""

    counts = series.dropna().astype(object).value_counts()
    levels = list(counts.index)  # most frequent first
    preferred = levels[1:] + levels[:1]

    def other(value: Any) -> Any:
        for level in preferred:
            if level != value:
                return level
        raise ValueError(f"{series.name!r} has a single level; a cell cannot be changed within its vocabulary")

    return other


def _is_discrete_numeric(series: pd.Series, finite: pd.Series) -> bool:
    integral = bool(np.all(np.mod(finite.to_numpy(dtype=float), 1) == 0)) if not finite.empty else False
    return integral and finite.nunique() <= DISCRETE_MAX_LEVELS


def perturb_rows(
    frame: pd.DataFrame, rows: np.ndarray, columns: Sequence[str], *, new_levels: bool = False
) -> pd.DataFrame:
    """A copy of ``frame`` whose ``columns`` differ on every one of ``rows``.

    0/1 columns flip (a missing cell becomes the minority class); other
    low-cardinality integer columns (classes) and discrete columns move to the
    runner-up EXISTING level; continuous numerics shift by more than their range
    (a missing cell becomes a value outside it). ``new_levels=True`` gives every
    perturbed DISCRETE cell a level absent from the column (the adversarial
    case for vocabularies); numeric columns ignore it. Raises when a cell cannot
    be changed (an all-missing numeric column, a single-level discrete column).
    """

    out = frame.copy()
    positions = np.asarray(rows, dtype=int)
    if positions.size == 0:
        return out
    for name in columns:
        series = out[name]
        loc = out.columns.get_loc(name)
        if pd.api.types.is_bool_dtype(series):
            out.iloc[positions, loc] = ~series.iloc[positions]
            continue
        if pd.api.types.is_numeric_dtype(series):
            values = pd.to_numeric(series, errors="coerce")
            finite = values.dropna()
            if finite.empty:
                raise ValueError(f"{name!r} has no finite value; its cells cannot be changed meaningfully")
            if set(finite.unique()).issubset({0, 1}):
                minority = float(finite.value_counts().idxmin()) if finite.nunique() > 1 else 1.0 - float(finite.iloc[0])
                replaced = (1 - values.iloc[positions]).fillna(minority)
            elif _is_discrete_numeric(series, finite):
                other = _runner_up_other(finite)
                replaced = values.iloc[positions].map(lambda v: float(other(v)) if pd.notna(v) else float(other(np.nan)))
            else:
                span = float(finite.max() - finite.min())
                replaced = (values.iloc[positions] + (span + 1.0)).fillna(float(finite.max()) + span + 1.0)
            out.iloc[positions, loc] = replaced.astype(series.dtype)
            continue
        other = (lambda value: f"{value}__probe") if new_levels else _runner_up_other(series)
        out[name] = out[name].astype(object)
        out.iloc[positions, loc] = series.iloc[positions].astype(object).map(other)
    return out


def _zero_or_missing(values: pd.Series) -> bool:
    numeric = pd.to_numeric(values, errors="coerce")
    return bool(((numeric == 0) | numeric.isna()).all())


def _max_change(before: pd.DataFrame, after: pd.DataFrame, rows: np.ndarray) -> tuple[float, list[str]]:
    """Largest difference between the two outputs on ``rows`` (NaN == NaN) and the
    columns that differ. Shared columns are compared; a column only one side has is a
    leak unless it is 0/NaN on the observed rows."""

    moved: list[str] = []
    largest = 0.0
    positions = np.asarray(rows, dtype=int)
    if len(before) != len(after):
        return float("inf"), ["<row count>"]
    for name in before.columns:
        if name not in after.columns:
            if not _zero_or_missing(before[name].iloc[positions]):
                moved.append(name)
                largest = float("inf")
            continue
        a = before[name].iloc[positions]
        b = after[name].iloc[positions]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            av = a.to_numpy(dtype=float)
            bv = b.to_numpy(dtype=float)
            diff = np.abs(av - bv)
            diff[np.isnan(av) & np.isnan(bv)] = 0.0
            if np.isnan(diff).any():
                moved.append(name)
                largest = float("inf")
                continue
            local = float(diff.max()) if diff.size else 0.0
            if local > _TOLERANCE:
                moved.append(name)
                largest = max(largest, local)
        else:
            equal = (a.astype(object).to_numpy() == b.astype(object).to_numpy()) | (a.isna() & b.isna()).to_numpy()
            if not bool(np.all(equal)):
                moved.append(name)
                largest = float("inf")
    for name in after.columns:
        if name not in before.columns and not _zero_or_missing(after[name].iloc[positions]):
            moved.append(name)
            largest = float("inf")
    return largest, moved


def split_validation_rows(val_rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(perturbed, observed) halves of a fold's validation rows: alternate positions in
    sorted order, so both halves cover the fold's range of rows."""

    ordered = np.sort(np.asarray(val_rows, dtype=int))
    return ordered[0::2], ordered[1::2]


def _result(probe: str, tested: int, affected: list[int], largest: float, columns: set[str]) -> ProbeResult:
    if tested == 0:
        raise ValueError(f"{probe}: nothing could be tested (no fold or row qualified)")
    return ProbeResult(
        probe=probe,
        leaked=bool(affected),
        tested=tested,
        affected=tuple(affected),
        max_abs_change=largest,
        columns=tuple(sorted(columns)),
    )


def fold_influence_probe(
    produce: FoldProducer,
    frame: pd.DataFrame,
    folds: Sequence[Fold],
    *,
    perturb: Sequence[str],
    new_levels: bool = False,
    check_perturbed_rows: bool = False,
) -> ProbeResult:
    """Do some validation rows' ``perturb`` columns influence the out-of-fold features
    of OTHER validation rows of the same fold (and, with ``check_perturbed_rows``,
    their own)?

    ``produce(frame, folds)`` returns one row of produced features per input row,
    in input order: each row's features as produced when that row is in the
    validation part of its fold (the way the engine scores a fold). A
    fold-local transform derives a validation row's features from the fold's
    training rows and the row's own INPUTS only, so perturbing half of the
    validation rows leaves the other half unchanged; when only target columns
    are perturbed the perturbed rows must be unchanged too (set
    ``check_perturbed_rows``). Folds with fewer than two validation rows cannot
    be tested; a probe that tests no fold raises.
    """

    baseline = produce(frame, folds)
    affected: list[int] = []
    largest = 0.0
    columns: set[str] = set()
    tested = 0
    for index, (_train_rows, val_rows) in enumerate(folds):
        perturbed, observed = split_validation_rows(np.asarray(val_rows))
        if perturbed.size == 0 or observed.size == 0:
            continue
        tested += 1
        again = produce(perturb_rows(frame, perturbed, perturb, new_levels=new_levels), folds)
        watched = np.sort(np.concatenate([observed, perturbed])) if check_perturbed_rows else observed
        change, moved = _max_change(baseline, again, watched)
        if moved:
            affected.append(index)
            columns.update(moved)
            largest = max(largest, change)
    return _result("fold_influence", tested, affected, largest, columns)


def _state_differs(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    moved: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key not in before or key not in after:
            moved.append(key)
            continue
        a, b = before[key], after[key]
        try:
            a_arr = np.asarray(a, dtype=float)
            b_arr = np.asarray(b, dtype=float)
            same = a_arr.shape == b_arr.shape and bool(np.allclose(a_arr, b_arr, atol=_TOLERANCE, equal_nan=True))
        except (TypeError, ValueError):
            a_obj, b_obj = np.asarray(a, dtype=object), np.asarray(b, dtype=object)
            same = a_obj.shape == b_obj.shape and bool(np.all(a_obj == b_obj))
        if not same:
            moved.append(key)
    return moved


def fitted_state_probe(
    fit: StateFitter,
    frame: pd.DataFrame,
    folds: Sequence[Fold],
    *,
    perturb: Sequence[str],
    new_levels: bool = False,
) -> ProbeResult:
    """Does a fold's fitted state depend on that fold's validation rows?

    ``fit(frame, train_rows)`` fits the step as the engine would for one fold and
    returns its fitted state (e.g. ``{"statistics_": ..., "categories_": ...}``
    or ``{"scores_": ..., "support": ...}``). For each fold ALL validation rows'
    ``perturb`` columns are changed; the state refit for that fold must be
    identical. A step fit on the full table or the whole training partition
    before the folds changes.
    """

    affected: list[int] = []
    columns: set[str] = set()
    tested = 0
    for index, (train_rows, val_rows) in enumerate(folds):
        if np.asarray(val_rows).size == 0:
            continue
        tested += 1
        before = fit(frame, np.asarray(train_rows))
        after = fit(perturb_rows(frame, np.asarray(val_rows), perturb, new_levels=new_levels), np.asarray(train_rows))
        moved = _state_differs(before, after)
        if moved:
            affected.append(index)
            columns.update(moved)
    return _result("fitted_state", tested, affected, float("inf") if affected else 0.0, columns)


def self_target_influence_probe(
    produce_train: TrainProducer,
    frame: pd.DataFrame,
    train_rows: np.ndarray,
    *,
    target: str,
    peer_column: str | None = None,
    probe_rows: int = DEFAULT_PROBE_ROWS,
    seed: int = 0,
) -> ProbeResult:
    """Does a training row's OWN target influence its own training-time feature, or
    the feature of EVERY peer?

    ``produce_train(frame, train_rows)`` returns the features the transform
    emits for ``train_rows`` while fitting on them (one output row per training
    row, in ``train_rows`` order), as the Pipeline's ``fit_transform`` would.
    For each sampled training row only that row's target is changed: (a) the
    row's own feature must be unchanged (any row failing is a leak), and (b)
    with ``peer_column``, the share of probed rows (that have peers) whose
    peers ALL moved must stay at or below ``PEER_LEAK_SHARE``. A cross-fitted
    encoder (K disjoint inner folds) passes both; a naive in-fold encoder fails
    (a); a leave-one-out encoder passes (a) and fails (b).
    """

    rows = np.asarray(train_rows, dtype=int)
    baseline = produce_train(frame, rows)
    if len(baseline) != rows.size:
        raise ValueError("produce_train must return one row per training row")
    rng = np.random.default_rng(seed)
    sample = np.arange(rows.size)
    if probe_rows < rows.size:
        sample = np.sort(rng.choice(sample, size=probe_rows, replace=False))
    peers_of = None
    if peer_column is not None:
        values = frame.iloc[rows][peer_column].astype(object).to_numpy()
        peers_of = lambda position: np.flatnonzero((values == values[position]) & (np.arange(rows.size) != position))
    affected: list[int] = []
    largest = 0.0
    columns: set[str] = set()
    with_peers = 0
    peers_all_moved: list[int] = []
    for position in sample:
        again = produce_train(perturb_rows(frame, np.asarray([rows[position]]), [target]), rows)
        change, moved = _max_change(baseline, again, np.asarray([position]))
        if moved:
            affected.append(int(rows[position]))
            columns.update(moved)
            largest = max(largest, change)
        if peers_of is not None:
            peers = peers_of(position)
            if peers.size:
                with_peers += 1
                if all(_max_change(baseline, again, np.asarray([peer]))[1] for peer in peers):
                    peers_all_moved.append(int(rows[position]))
    if with_peers and len(peers_all_moved) / with_peers > PEER_LEAK_SHARE:
        affected.extend(row for row in peers_all_moved if row not in affected)
        columns.add(f"<every peer moved for {len(peers_all_moved)}/{with_peers} probed rows>")
        largest = float("inf")
    return _result("self_target_influence", int(sample.size), sorted(affected), largest, columns)


def _probe_positions(
    frame: pd.DataFrame, times: pd.Series, group_column: str | None, probe_rows: int, seed: int
) -> np.ndarray:
    n = len(frame)
    if n <= EXHAUSTIVE_ROWS:
        return np.arange(n)
    must: set[int] = set()
    if group_column is not None:
        keyed = pd.DataFrame({"t": times.to_numpy(), "g": frame[group_column].to_numpy()})
        must.update(int(i) for i in keyed[keyed.duplicated(["t", "g"], keep=False)].index)
        for _group, part in keyed.groupby("g", sort=False):
            ordered = part.sort_values("t", kind="stable")
            must.update((int(ordered.index[0]), int(ordered.index[-1])))
    else:
        must.update(int(i) for i in np.flatnonzero(times.duplicated(keep=False).to_numpy()))
    rest = np.setdiff1d(np.arange(n), np.fromiter(must, dtype=int))
    rng = np.random.default_rng(seed)
    extra = rng.choice(rest, size=min(probe_rows, rest.size), replace=False) if rest.size else np.array([], dtype=int)
    return np.sort(np.concatenate([np.fromiter(must, dtype=int), extra]).astype(int))


def future_influence_probe(
    produce: RowProducer,
    frame: pd.DataFrame,
    *,
    time_column: str,
    perturb: Sequence[str] = (),
    group_column: str | None = None,
    probe_rows: int = DEFAULT_PROBE_ROWS,
    seed: int = 0,
) -> ProbeResult:
    """Truncation replay: is a row's feature the same when the frame holds only the
    rows strictly before its time (plus the row itself)?

    ``produce(frame)`` returns one row of features per input row, in input
    order. Every row is probed when the frame has at most ``EXHAUSTIVE_ROWS``
    rows; otherwise every row with a same-timestamp sibling (within
    ``group_column`` when given) and each group's first and last row, plus
    ``probe_rows`` sampled rows. With ``perturb`` columns the row's OWN values
    are also changed in the truncated frame: an as-of aggregate never reads the
    row itself, so its output must still match.
    """

    times = pd.to_datetime(frame[time_column], errors="coerce")
    if times.isna().any():
        raise ValueError(f"{time_column!r} must parse as datetimes for every row")
    baseline = produce(frame)
    if len(baseline) != len(frame):
        raise ValueError("produce must return one row per input row")
    candidates = _probe_positions(frame, times, group_column, probe_rows, seed)
    affected: list[int] = []
    largest = 0.0
    columns: set[str] = set()
    time_values = times.to_numpy()
    for position in candidates:
        keep = np.flatnonzero(time_values < time_values[position])
        order = np.concatenate([keep, [position]])
        truncated = frame.iloc[order].reset_index(drop=True)
        if perturb:
            truncated = perturb_rows(truncated, np.asarray([len(order) - 1]), perturb)
        again = produce(truncated)
        if len(again) != len(order):
            raise ValueError("produce must return one row per input row")
        replayed = again.iloc[[len(order) - 1]].reset_index(drop=True)
        original = baseline.iloc[[position]].reset_index(drop=True)
        change, moved = _max_change(original, replayed, np.asarray([0]))
        if moved:
            affected.append(int(position))
            columns.update(moved)
            largest = max(largest, change)
    return _result("future_influence", int(candidates.size), affected, largest, columns)


__all__ = [
    "DEFAULT_PROBE_ROWS",
    "DISCRETE_MAX_LEVELS",
    "EXHAUSTIVE_ROWS",
    "Fold",
    "FoldProducer",
    "ProbeResult",
    "RowProducer",
    "StateFitter",
    "TrainProducer",
    "fitted_state_probe",
    "fold_influence_probe",
    "future_influence_probe",
    "perturb_rows",
    "self_target_influence_probe",
    "split_validation_rows",
]
