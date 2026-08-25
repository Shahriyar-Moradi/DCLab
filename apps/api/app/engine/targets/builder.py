"""Point-in-time target construction.

Labels use future events after prediction_time; callers must keep features
on or before prediction_time.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd


def build_binary_horizon_target(
    events: pd.DataFrame,
    *,
    entity_col: str,
    event_time_col: str,
    as_of: pd.Timestamp | str,
    horizon_days: int,
    snapshots: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return one row per entity with target=1 if an event occurs in (as_of, as_of+horizon]."""
    as_of_ts = pd.Timestamp(as_of)
    horizon_end = as_of_ts + timedelta(days=horizon_days)
    work = events.copy()
    work[event_time_col] = pd.to_datetime(work[event_time_col], errors="coerce")
    future = work[(work[event_time_col] > as_of_ts) & (work[event_time_col] <= horizon_end)]
    positives = set(future[entity_col].dropna().astype(str))
    entities = (
        snapshots[entity_col].astype(str).drop_duplicates()
        if snapshots is not None and entity_col in snapshots.columns
        else work[entity_col].astype(str).drop_duplicates()
    )
    out = pd.DataFrame({entity_col: entities.tolist()})
    out["as_of_date"] = as_of_ts
    out["target"] = out[entity_col].isin(positives).astype(int)
    return out.reset_index(drop=True)


def build_value_horizon_target(
    events: pd.DataFrame,
    *,
    entity_col: str,
    event_time_col: str,
    value_col: str,
    as_of: pd.Timestamp | str,
    horizon_days: int,
    snapshots: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Sum ``value_col`` for events in (as_of, as_of+horizon]."""
    as_of_ts = pd.Timestamp(as_of)
    horizon_end = as_of_ts + timedelta(days=horizon_days)
    work = events.copy()
    work[event_time_col] = pd.to_datetime(work[event_time_col], errors="coerce")
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce").fillna(0.0)
    future = work[(work[event_time_col] > as_of_ts) & (work[event_time_col] <= horizon_end)]
    totals = future.groupby(entity_col)[value_col].sum()
    entities = (
        snapshots[entity_col].astype(str).drop_duplicates()
        if snapshots is not None and entity_col in snapshots.columns
        else work[entity_col].astype(str).drop_duplicates()
    )
    out = pd.DataFrame({entity_col: entities.tolist()})
    out["as_of_date"] = as_of_ts
    out["target"] = out[entity_col].map(totals).fillna(0.0)
    return out.reset_index(drop=True)


def build_days_to_next_event(
    events: pd.DataFrame,
    *,
    entity_col: str,
    event_time_col: str,
    as_of: pd.Timestamp | str,
    snapshots: pd.DataFrame | None = None,
    cap_days: int = 365,
) -> pd.DataFrame:
    """Regression target: days until the next event after as_of, capped."""
    as_of_ts = pd.Timestamp(as_of)
    work = events.copy()
    work[event_time_col] = pd.to_datetime(work[event_time_col], errors="coerce")
    future = work[work[event_time_col] > as_of_ts]
    next_event = future.groupby(entity_col)[event_time_col].min()
    entities = (
        snapshots[entity_col].astype(str).drop_duplicates()
        if snapshots is not None and entity_col in snapshots.columns
        else work[entity_col].astype(str).drop_duplicates()
    )
    out = pd.DataFrame({entity_col: entities.tolist()})
    out["as_of_date"] = as_of_ts
    mapped = out[entity_col].map(next_event)
    days = (mapped - as_of_ts).dt.days
    out["target"] = days.fillna(cap_days).clip(lower=0, upper=cap_days)
    return out.reset_index(drop=True)


def filter_features_at_or_before(
    frame: pd.DataFrame, time_col: str, as_of: pd.Timestamp | str
) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of)
    times = pd.to_datetime(frame[time_col], errors="coerce")
    return frame.loc[times.isna() | (times <= cutoff)].copy()
