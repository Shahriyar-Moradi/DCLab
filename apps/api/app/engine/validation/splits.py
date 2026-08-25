"""Train / validation / test split builders. Temporal splits never mix future into train."""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split


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


def assert_temporal_order(split_meta: dict[str, Any]) -> None:
    train_max = split_meta.get("train_max")
    val_min = split_meta.get("val_min")
    val_max = split_meta.get("val_max")
    test_min = split_meta.get("test_min")
    if not all([train_max, val_min, val_max, test_min]):
        return
    if not (train_max <= val_min and val_max <= test_min):
        raise ValueError(f"Temporal split leaked: {split_meta}")
