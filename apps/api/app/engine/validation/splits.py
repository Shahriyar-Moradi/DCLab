"""Train / validation / test split builders. Temporal splits never mix future into train."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split

SOURCE_ROW_COLUMN = "__dclab_source_row__"


def split_frame(
    frame: pd.DataFrame,
    *,
    strategy: str,
    target: str,
    time_col: str | None = None,
    group_col: str | None = None,
    seed: int = 42,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return train, validation, test. Test is the last slice and must stay untouched."""
    if strategy == "time":
        col = time_col or _guess_time_column(frame)
        n_times = (
            pd.to_datetime(frame[col], errors="coerce").nunique(dropna=True) if col and col in frame.columns else 0
        )
        if col is None or n_times < 2:
            strategy = "stratified" if _is_binary(frame, target) else "random"
        else:
            return _time_split(frame, col, train_frac, val_frac)
    if strategy == "group" and group_col and group_col in frame.columns:
        return _group_split(frame, group_col, seed, train_frac, val_frac)
    if strategy == "stratified" and _is_binary(frame, target):
        return _sklearn_split(frame, target, seed, train_frac, val_frac, stratify=True)
    if strategy == "rolling":
        col = time_col or _guess_time_column(frame)
        if col:
            return _time_split(frame, col, train_frac, val_frac, kind="rolling")
    return _sklearn_split(frame, target, seed, train_frac, val_frac, stratify=False)


def _guess_time_column(frame: pd.DataFrame) -> str | None:
    for name in ("as_of_date", "created_at", "event_time", "timestamp", "order_purchase_timestamp"):
        if name in frame.columns:
            return name
    return None


def _is_binary(frame: pd.DataFrame, target: str) -> bool:
    return target in frame.columns and frame[target].nunique(dropna=True) == 2


def _time_split(
    frame: pd.DataFrame, time_col: str, train_frac: float, val_frac: float, kind: str = "time"
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dated = frame.copy()
    dated["_t"] = pd.to_datetime(dated[time_col], errors="coerce")
    dated = dated.sort_values("_t")
    n = len(dated)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    train = dated.iloc[:train_end].drop(columns=["_t"])
    val = dated.iloc[train_end:val_end].drop(columns=["_t"])
    test = dated.iloc[val_end:].drop(columns=["_t"])
    meta = {
        "strategy": kind,
        "time_column": time_col,
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_test": int(len(test)),
        "train_max": str(pd.to_datetime(train[time_col], errors="coerce").max()) if len(train) else None,
        "val_min": str(pd.to_datetime(val[time_col], errors="coerce").min()) if len(val) else None,
        "val_max": str(pd.to_datetime(val[time_col], errors="coerce").max()) if len(val) else None,
        "test_min": str(pd.to_datetime(test[time_col], errors="coerce").min()) if len(test) else None,
    }
    return train.reset_index(drop=True), val.reset_index(drop=True), test.reset_index(drop=True), meta


def _sklearn_split(
    frame: pd.DataFrame,
    target: str,
    seed: int,
    train_frac: float,
    val_frac: float,
    *,
    stratify: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    strat = frame[target] if stratify and target in frame.columns else None
    train, rest = train_test_split(
        frame, train_size=train_frac, random_state=seed, stratify=strat
    )
    rest_frac = val_frac / max(1e-9, (1 - train_frac))
    rest_strat = rest[target] if stratify and target in rest.columns else None
    try:
        val, test = train_test_split(rest, train_size=rest_frac, random_state=seed, stratify=rest_strat)
    except ValueError:
        val, test = train_test_split(rest, train_size=rest_frac, random_state=seed, stratify=None)
    meta = {
        "strategy": "stratified" if stratify else "random",
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_test": int(len(test)),
    }
    return train.reset_index(drop=True), val.reset_index(drop=True), test.reset_index(drop=True), meta


def _group_split(
    frame: pd.DataFrame, group_col: str, seed: int, train_frac: float, val_frac: float
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    splitter = GroupShuffleSplit(n_splits=1, train_size=train_frac, random_state=seed)
    groups = frame[group_col]
    train_idx, rest_idx = next(splitter.split(frame, groups=groups))
    train = frame.iloc[train_idx]
    rest = frame.iloc[rest_idx]
    rest_frac = val_frac / max(1e-9, (1 - train_frac))
    splitter2 = GroupShuffleSplit(n_splits=1, train_size=rest_frac, random_state=seed)
    val_rel, test_rel = next(splitter2.split(rest, groups=rest[group_col]))
    val = rest.iloc[val_rel]
    test = rest.iloc[test_rel]
    meta = {
        "strategy": "group",
        "group_column": group_col,
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_test": int(len(test)),
    }
    return train.reset_index(drop=True), val.reset_index(drop=True), test.reset_index(drop=True), meta


def split_train_test_holdout(
    frame: pd.DataFrame,
    *,
    target: str,
    test_size: float = 0.2,
    seed: int = 42,
    stratify: bool = True,
    plan: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Lock a final train/test holdout. Validation is empty; test stays unused until final predict.

    When `plan` is provided, the split must follow that HoldoutPlan. Group and
    temporal isolation never silently fall back to a random split.
    """
    if plan is not None:
        return _split_holdout_from_plan(frame, target=target, plan=plan)
    use_stratify = stratify and target in frame.columns
    strat = frame[target] if use_stratify else None
    try:
        train, test = train_test_split(
            frame, test_size=test_size, random_state=seed, stratify=strat
        )
    except ValueError:
        train, test = train_test_split(frame, test_size=test_size, random_state=seed, stratify=None)
        use_stratify = False
    strategy = "stratified_random" if use_stratify else "random"
    return _finalize_holdout(
        frame,
        train,
        test,
        strategy=strategy,
        requested_test_size=float(test_size),
        random_state=seed,
        stratify=use_stratify,
    )


def _split_holdout_from_plan(
    frame: pd.DataFrame,
    *,
    target: str,
    plan: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    strategy = str(getattr(plan, "strategy", "") or "")
    requested = float(getattr(plan, "test_size", 0.2) or 0.2)
    seed = int(getattr(plan, "random_state", 42) or 42)
    if strategy == "unsupported":
        raise ValueError(str(getattr(plan, "reason", None) or "Holdout plan is unsupported."))
    if strategy == "temporal_future":
        return _temporal_future_holdout(frame, plan=plan, requested_test_size=requested, seed=seed)
    if strategy == "group_disjoint":
        return _group_disjoint_holdout(frame, plan=plan, requested_test_size=requested, seed=seed)
    if strategy == "stratified_random":
        use_stratify = target in frame.columns
        strat = frame[target] if use_stratify else None
        try:
            train, test = train_test_split(
                frame, test_size=requested, random_state=seed, stratify=strat
            )
        except ValueError:
            train, test = train_test_split(
                frame, test_size=requested, random_state=seed, stratify=None
            )
            use_stratify = False
        return _finalize_holdout(
            frame,
            train,
            test,
            strategy="stratified_random" if use_stratify else "random",
            requested_test_size=requested,
            random_state=seed,
            stratify=use_stratify,
        )
    if strategy == "random":
        train, test = train_test_split(frame, test_size=requested, random_state=seed, stratify=None)
        return _finalize_holdout(
            frame,
            train,
            test,
            strategy="random",
            requested_test_size=requested,
            random_state=seed,
            stratify=False,
        )
    raise ValueError(f"Unknown holdout strategy {strategy!r}.")


def _group_disjoint_holdout(
    frame: pd.DataFrame,
    *,
    plan: Any,
    requested_test_size: float,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    group_column = str(getattr(plan, "group_column", None) or "")
    if not group_column or group_column not in frame.columns:
        raise ValueError("Group-disjoint holdout requires a group column on the modeling frame.")
    groups = frame[group_column]
    unique_groups = int(groups.nunique(dropna=True))
    if unique_groups < 2:
        raise ValueError("Group-disjoint holdout needs at least two groups.")
    splitter = GroupShuffleSplit(n_splits=1, test_size=requested_test_size, random_state=seed)
    try:
        train_idx, test_idx = next(splitter.split(frame, groups=groups))
    except ValueError as exc:
        raise ValueError(f"Group-disjoint holdout could not be formed: {exc}") from exc
    train = frame.iloc[np.asarray(train_idx)]
    test = frame.iloc[np.asarray(test_idx)]
    train_groups = set(pd.unique(train[group_column].dropna()))
    test_groups = set(pd.unique(test[group_column].dropna()))
    overlap = sorted(train_groups & test_groups, key=str)
    if overlap:
        raise ValueError(f"Group-disjoint holdout leaked groups {overlap!r}.")
    return _finalize_holdout(
        frame,
        train,
        test,
        strategy="group_disjoint",
        requested_test_size=requested_test_size,
        random_state=seed,
        stratify=False,
        group_column=group_column,
        group_overlap=overlap,
    )


def _temporal_future_holdout(
    frame: pd.DataFrame,
    *,
    plan: Any,
    requested_test_size: float,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    time_column = str(getattr(plan, "time_column", None) or "")
    if not time_column or time_column not in frame.columns:
        raise ValueError("Temporal holdout requires a time column on the modeling frame.")
    n = int(len(frame))
    if n < 2:
        raise ValueError("Temporal holdout needs at least two rows.")
    times = _sortable_timeline(frame[time_column])
    time_values = times.to_numpy()
    fill = _timeline_fill(times)
    order = np.argsort(times.fillna(fill).to_numpy(), kind="mergesort")
    n_test = int(np.ceil(requested_test_size * n))
    n_test = min(max(n_test, 1), n - 1)
    cut = n - n_test
    train_pos = np.asarray(order[:cut])
    test_pos = np.asarray(order[cut:])
    train_pos, test_pos = _prefer_strict_temporal_cut(time_values, train_pos, test_pos)
    train = frame.iloc[train_pos]
    test = frame.iloc[test_pos]
    train_times = pd.Series(time_values[train_pos]).dropna()
    test_times = pd.Series(time_values[test_pos]).dropna()
    train_min = train_times.min() if len(train_times) else None
    train_max = train_times.max() if len(train_times) else None
    test_min = test_times.min() if len(test_times) else None
    test_max = test_times.max() if len(test_times) else None
    if train_max is not None and test_min is not None and train_max > test_min:
        raise ValueError(
            f"Temporal holdout is not chronological: train_max={train_max!r} test_min={test_min!r}."
        )
    strict = bool(train_max is not None and test_min is not None and train_max < test_min)
    return _finalize_holdout(
        frame,
        train,
        test,
        strategy="temporal_future",
        requested_test_size=requested_test_size,
        random_state=seed,
        stratify=False,
        time_column=time_column,
        train_time_min=_iso_time(train_min),
        train_time_max=_iso_time(train_max),
        test_time_min=_iso_time(test_min),
        test_time_max=_iso_time(test_max),
        strict_temporal_order=strict,
    )


def _sortable_timeline(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if len(series) and float(parsed.notna().mean()) >= 0.8:
        return parsed
    return pd.to_numeric(series, errors="coerce")


def _timeline_fill(times: pd.Series) -> Any:
    observed = times.dropna()
    if observed.empty:
        return 0
    if pd.api.types.is_datetime64_any_dtype(times):
        return observed.min()
    return observed.min()


def _prefer_strict_temporal_cut(
    time_values: np.ndarray,
    train_pos: np.ndarray,
    test_pos: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Move the boundary timestamp entirely to one side when that yields train_max < test_min."""
    train_pos = np.asarray(train_pos)
    test_pos = np.asarray(test_pos)
    while train_pos.size and test_pos.size:
        train_times = pd.Series(time_values[train_pos]).dropna()
        test_times = pd.Series(time_values[test_pos]).dropna()
        if train_times.empty or test_times.empty:
            break
        train_max = train_times.max()
        test_min = test_times.min()
        if train_max < test_min:
            break
        if train_max > test_min:
            break
        boundary = train_max
        train_keep = train_pos[~_timeline_equal(time_values[train_pos], boundary)]
        train_move = train_pos[_timeline_equal(time_values[train_pos], boundary)]
        if train_keep.size:
            train_pos = train_keep
            test_pos = np.concatenate([train_move, test_pos])
            continue
        test_keep = test_pos[~_timeline_equal(time_values[test_pos], boundary)]
        test_move = test_pos[_timeline_equal(time_values[test_pos], boundary)]
        if test_keep.size:
            test_pos = test_keep
            train_pos = np.concatenate([train_pos, test_move])
        break
    return train_pos, test_pos


def _timeline_equal(values: np.ndarray, boundary: Any) -> np.ndarray:
    series = pd.Series(values)
    if pd.isna(boundary):
        return series.isna().to_numpy()
    try:
        return (series == boundary).fillna(False).to_numpy()
    except (TypeError, ValueError):
        return np.zeros(len(series), dtype=bool)


def _iso_time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return str(value)
        return stamp.isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _source_rows(partition: pd.DataFrame) -> list[int]:
    if SOURCE_ROW_COLUMN in partition.columns:
        return partition[SOURCE_ROW_COLUMN].astype(int).tolist()
    return [int(value) for value in partition.index]


def _finalize_holdout(
    frame: pd.DataFrame,
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    strategy: str,
    requested_test_size: float,
    random_state: int,
    stratify: bool,
    group_column: str | None = None,
    group_overlap: list[Any] | None = None,
    time_column: str | None = None,
    train_time_min: str | None = None,
    train_time_max: str | None = None,
    test_time_min: str | None = None,
    test_time_max: str | None = None,
    strict_temporal_order: bool | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    val = frame.iloc[0:0].copy()
    train_source = _source_rows(train)
    test_source = _source_rows(test)
    if set(train_source).intersection(test_source):
        raise ValueError("train/test provenance overlap detected")
    n = max(int(len(frame)), 1)
    overlap = list(group_overlap or [])
    meta: dict[str, Any] = {
        "strategy": strategy,
        "test_size": requested_test_size,
        "requested_test_size": requested_test_size,
        "actual_test_size": float(len(test)) / float(n),
        "random_state": random_state,
        "stratify": stratify,
        "n_train": int(len(train)),
        "n_val": 0,
        "n_test": int(len(test)),
        "provenance_column": SOURCE_ROW_COLUMN if SOURCE_ROW_COLUMN in frame.columns else "dataframe_index",
        "split_at": datetime.now(UTC).isoformat(),
        "modeling_row_count": int(len(frame)),
        "all_source_rows": train_source + test_source,
        "train_source_rows": train_source,
        "test_source_rows": test_source,
        "provenance_disjoint": True,
        "train_test_provenance": "disjoint",
    }
    if group_column:
        meta["group_column"] = group_column
        meta["group_overlap"] = overlap
        meta["group_overlap_count"] = int(len(overlap))
    if time_column:
        meta["time_column"] = time_column
        meta["train_time_min"] = train_time_min
        meta["train_time_max"] = train_time_max
        meta["test_time_min"] = test_time_min
        meta["test_time_max"] = test_time_max
        meta["train_time_range"] = [train_time_min, train_time_max]
        meta["test_time_range"] = [test_time_min, test_time_max]
        meta["strict_temporal_order"] = strict_temporal_order
    return train.reset_index(drop=True), val.reset_index(drop=True), test.reset_index(drop=True), meta


def assert_temporal_order(split_meta: dict[str, Any]) -> None:
    train_max = split_meta.get("train_max")
    val_min = split_meta.get("val_min")
    val_max = split_meta.get("val_max")
    test_min = split_meta.get("test_min")
    if not all([train_max, val_min, val_max, test_min]):
        return
    if not (train_max <= val_min and val_max <= test_min):
        raise ValueError(f"Temporal split leaked: {split_meta}")
