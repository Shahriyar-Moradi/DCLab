"""Choose one authoritative ValidationPlan from a train-only ProblemProfile."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold, KFold, StratifiedGroupKFold, StratifiedKFold, TimeSeriesSplit

from app.engine.modeling.problem_profile import MIN_REPEATED_ENTITY_UNIQUE, ProblemProfile

VALIDATION_PLAN_VERSION = "dclab.validation_plan.v1"
DEFAULT_FOLDS = 5
MIN_FOLDS = 2

STRATIFIED_KFOLD = "StratifiedKFold"
KFOLD = "KFold"
STRATIFIED_GROUP_KFOLD = "StratifiedGroupKFold"
GROUP_KFOLD = "GroupKFold"
TIME_SERIES_SPLIT = "TimeSeriesSplit"
UNSUPPORTED = "unsupported"


class ValidationUnsupportedError(ValueError):
    """Raised when temporal and grouping constraints both apply with no safe splitter."""


@dataclass
class ValidationPlan:
    strategy: str
    requested_folds: int
    actual_folds: int | None
    shuffle: bool
    random_state: int
    group_column: str | None
    time_column: str | None
    stratified: bool
    reason: str
    fallback_reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    version: str = VALIDATION_PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> ValidationPlan:
        from app.engine.modeling.coerce import from_mapping

        plan = from_mapping(cls, payload)
        if plan is None:
            raise ValueError("ValidationPlan evidence is missing.")
        return plan


@dataclass
class FoldSplit:
    fold_number: int
    train_index: np.ndarray
    validation_index: np.ndarray
    train_count: int
    validation_count: int
    group_overlap: list[Any]
    train_time_min: str | None
    train_time_max: str | None
    validation_time_min: str | None
    validation_time_max: str | None


def _top_repeated_entity(profile: ProblemProfile) -> dict[str, Any] | None:
    for item in profile.repeated_entity_candidates:
        unique_count = int(item.get("unique_count") or 0)
        if unique_count < max(MIN_FOLDS, MIN_REPEATED_ENTITY_UNIQUE):
            continue
        if int(item.get("max_rows_per_entity") or 0) >= 2:
            if float(item.get("mean_rows_per_entity") or 0) >= 1.5 or int(item.get("max_rows_per_entity") or 0) >= 3:
                return item
    return None


def _strong_temporal_candidate(profile: ProblemProfile, requested_folds: int) -> dict[str, Any] | None:
    """A date column is not enough. Require ordered, time-varying prediction structure."""
    ranked: list[dict[str, Any]] = []
    for item in profile.time_candidates:
        unique_count = int(item.get("unique_count") or 0)
        unique_ratio = float(item.get("unique_ratio") or 0.0)
        span_seconds = float(item.get("span_seconds") or 0.0)
        strong_name = bool(item.get("strong_name"))
        enough_points = unique_count >= max(requested_folds + 1, 8)
        enough_span = span_seconds >= 24 * 3600 or unique_count >= 20
        time_varying = unique_ratio >= 0.5 or strong_name
        if not (enough_points and enough_span and time_varying):
            continue
        ranked.append(item)
    if not ranked:
        return None
    ranked.sort(
        key=lambda item: (
            -int(bool(item.get("strong_name"))),
            -float(item.get("unique_ratio") or 0),
            -int(item.get("unique_count") or 0),
            item["column"],
        )
    )
    return ranked[0]


def _adapt_folds(requested: int, feasible: int, *, reason: str) -> tuple[int, str | None]:
    actual = min(requested, max(feasible, 0))
    if actual >= requested:
        return requested, None
    if actual >= MIN_FOLDS:
        return actual, reason
    return actual, reason


def _max_stratified_splits(y: pd.Series) -> int:
    counts = y.value_counts(dropna=True)
    if counts.empty:
        return 0
    return int(counts.min())


def _max_group_splits(groups: pd.Series) -> int:
    return int(groups.nunique(dropna=True))


def _max_stratified_group_splits(y: pd.Series, groups: pd.Series) -> int:
    frame = pd.DataFrame({"y": y.to_numpy(), "g": groups.to_numpy()}).dropna()
    if frame.empty:
        return 0
    per_class = frame.groupby("y")["g"].nunique()
    if per_class.empty:
        return 0
    return int(min(int(per_class.min()), int(frame["g"].nunique()), int(frame["y"].value_counts().min())))


def _max_time_splits(unique_times: int, n_rows: int) -> int:
    return max(0, min(unique_times - 1, n_rows - 1))


def plan_validation(
    profile: ProblemProfile,
    *,
    y: pd.Series | None = None,
    frame: pd.DataFrame | None = None,
    requested_folds: int = DEFAULT_FOLDS,
    random_state: int = 42,
) -> ValidationPlan:
    """Return exactly one ValidationPlan. Never silently fall back to random CV."""
    requested = max(int(requested_folds), MIN_FOLDS)
    entity = _top_repeated_entity(profile)
    temporal = _strong_temporal_candidate(profile, requested)
    grouping_needed = entity is not None
    temporal_needed = temporal is not None
    evidence: dict[str, Any] = {
        "repeated_entity": entity,
        "strong_temporal": temporal,
        "time_candidates": [item["column"] for item in profile.time_candidates],
        "grouping_needed": grouping_needed,
        "temporal_needed": temporal_needed,
    }

    if grouping_needed and temporal_needed:
        return ValidationPlan(
            strategy=UNSUPPORTED,
            requested_folds=requested,
            actual_folds=None,
            shuffle=False,
            random_state=random_state,
            group_column=str(entity["column"]),
            time_column=str(temporal["column"]),
            stratified=profile.task_type == "binary",
            reason=(
                "Repeated-entity grouping and strong temporal prediction structure "
                "are both present. No verified joint splitter is available."
            ),
            fallback_reason=None,
            evidence=evidence,
        )

    if temporal_needed:
        time_column = str(temporal["column"])
        unique_times = int(temporal.get("unique_count") or 0)
        n_rows = profile.row_count
        feasible = _max_time_splits(unique_times, n_rows)
        actual, fallback = _adapt_folds(
            requested,
            feasible,
            reason="Reduced folds because the training time axis cannot support five TimeSeriesSplit folds.",
        )
        if actual < MIN_FOLDS:
            return ValidationPlan(
                strategy=UNSUPPORTED,
                requested_folds=requested,
                actual_folds=actual,
                shuffle=False,
                random_state=random_state,
                group_column=None,
                time_column=time_column,
                stratified=False,
                reason="Strong temporal structure was detected but fewer than two chronological folds are possible.",
                fallback_reason=fallback,
                evidence=evidence,
            )
        return ValidationPlan(
            strategy=TIME_SERIES_SPLIT,
            requested_folds=requested,
            actual_folds=actual,
            shuffle=False,
            random_state=random_state,
            group_column=None,
            time_column=time_column,
            stratified=False,
            reason="Training rows have strong temporal prediction structure; TimeSeriesSplit preserves chronology.",
            fallback_reason=fallback,
            evidence=evidence,
        )

    if grouping_needed:
        group_column = str(entity["column"])
        groups = frame[group_column] if frame is not None and group_column in frame.columns else None
        labels = y if y is not None else None
        if profile.task_type == "binary" and labels is not None and groups is not None:
            feasible = _max_stratified_group_splits(labels, groups)
            actual, fallback = _adapt_folds(
                requested,
                feasible,
                reason="Reduced folds because grouped class occupancy cannot support five StratifiedGroupKFold folds.",
            )
            if actual >= MIN_FOLDS:
                return ValidationPlan(
                    strategy=STRATIFIED_GROUP_KFOLD,
                    requested_folds=requested,
                    actual_folds=actual,
                    shuffle=True,
                    random_state=random_state,
                    group_column=group_column,
                    time_column=None,
                    stratified=True,
                    reason="Repeated entities require group-aware CV; StratifiedGroupKFold is feasible.",
                    fallback_reason=fallback,
                    evidence=evidence,
                )
            group_feasible = _max_group_splits(groups)
            actual, group_fallback = _adapt_folds(
                requested,
                group_feasible,
                reason="Reduced folds because unique entities cannot support five GroupKFold folds.",
            )
            return ValidationPlan(
                strategy=GROUP_KFOLD,
                requested_folds=requested,
                actual_folds=max(actual, MIN_FOLDS) if group_feasible >= MIN_FOLDS else actual,
                shuffle=False,
                random_state=random_state,
                group_column=group_column,
                time_column=None,
                stratified=False,
                reason="Repeated entities require group-aware CV; StratifiedGroupKFold was not feasible.",
                fallback_reason=fallback or group_fallback,
                evidence={**evidence, "stratified_group_feasible_folds": feasible},
            )
        if groups is None:
            unique_groups = int(entity.get("unique_count") or 0)
        else:
            unique_groups = _max_group_splits(groups)
        actual, fallback = _adapt_folds(
            requested,
            unique_groups,
            reason="Reduced folds because unique entities cannot support five GroupKFold folds.",
        )
        return ValidationPlan(
            strategy=GROUP_KFOLD,
            requested_folds=requested,
            actual_folds=actual if actual >= MIN_FOLDS else unique_groups,
            shuffle=False,
            random_state=random_state,
            group_column=group_column,
            time_column=None,
            stratified=False,
            reason="Repeated entities require group-aware CV; GroupKFold keeps entities out of both sides of a fold.",
            fallback_reason=fallback,
            evidence=evidence,
        )

    if profile.task_type == "binary":
        if y is not None:
            feasible = _max_stratified_splits(y)
        elif profile.class_distribution:
            feasible = min(profile.class_distribution.values())
        else:
            feasible = requested
        actual, fallback = _adapt_folds(
            requested,
            feasible,
            reason="Reduced folds because the minority class cannot support five stratified folds.",
        )
        return ValidationPlan(
            strategy=STRATIFIED_KFOLD,
            requested_folds=requested,
            actual_folds=max(actual, MIN_FOLDS) if feasible >= MIN_FOLDS else actual,
            shuffle=True,
            random_state=random_state,
            group_column=None,
            time_column=None,
            stratified=True,
            reason="Ordinary binary classification uses StratifiedKFold on the locked training partition.",
            fallback_reason=fallback,
            evidence=evidence,
        )

    n_rows = profile.row_count
    feasible = max(0, n_rows // 2)
    actual, fallback = _adapt_folds(
        requested,
        feasible,
        reason="Reduced folds because the training partition cannot support five KFold splits.",
    )
    return ValidationPlan(
        strategy=KFOLD,
        requested_folds=requested,
        actual_folds=max(actual, MIN_FOLDS) if feasible >= MIN_FOLDS else actual,
        shuffle=True,
        random_state=random_state,
        group_column=None,
        time_column=None,
        stratified=False,
        reason="Ordinary regression uses shuffled KFold on the locked training partition.",
        fallback_reason=fallback,
        evidence=evidence,
    )


def build_splitter(plan: ValidationPlan):
    if plan.strategy == UNSUPPORTED:
        raise ValidationUnsupportedError(plan.reason)
    n_splits = int(plan.actual_folds or 0)
    if n_splits < MIN_FOLDS:
        raise ValidationUnsupportedError(
            plan.fallback_reason or "Validation plan cannot produce at least two folds."
        )
    if plan.strategy == STRATIFIED_KFOLD:
        return StratifiedKFold(n_splits=n_splits, shuffle=plan.shuffle, random_state=plan.random_state)
    if plan.strategy == KFOLD:
        return KFold(n_splits=n_splits, shuffle=plan.shuffle, random_state=plan.random_state)
    if plan.strategy == STRATIFIED_GROUP_KFOLD:
        return StratifiedGroupKFold(n_splits=n_splits, shuffle=plan.shuffle, random_state=plan.random_state)
    if plan.strategy == GROUP_KFOLD:
        return GroupKFold(n_splits=n_splits, shuffle=False)
    if plan.strategy == TIME_SERIES_SPLIT:
        return TimeSeriesSplit(n_splits=n_splits)
    raise ValidationUnsupportedError(f"Unknown validation strategy {plan.strategy!r}.")


def _iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    return stamp.isoformat()


def iter_validation_folds(
    plan: ValidationPlan,
    frame: pd.DataFrame,
    y: np.ndarray | pd.Series,
) -> Iterator[FoldSplit]:
    """Yield fold indices into `frame` and prove group/time invariants."""
    splitter = build_splitter(plan)
    n = len(frame)
    positions = np.arange(n)
    labels = np.asarray(y)
    group_values = (
        frame[plan.group_column].to_numpy()
        if plan.group_column and plan.group_column in frame.columns
        else None
    )
    times = None
    order = positions
    if plan.time_column and plan.time_column in frame.columns:
        times = pd.to_datetime(frame[plan.time_column], errors="coerce")
        order = np.argsort(times.fillna(pd.Timestamp.max).to_numpy(), kind="mergesort")

    if plan.strategy == TIME_SERIES_SPLIT:
        split_iter = splitter.split(order)
        mapped = []
        for train_rel, val_rel in split_iter:
            train_idx = order[np.asarray(train_rel)]
            val_idx = order[np.asarray(val_rel)]
            mapped.append((train_idx, val_idx))
        split_pairs = mapped
    elif plan.strategy in {GROUP_KFOLD, STRATIFIED_GROUP_KFOLD}:
        if group_values is None:
            raise ValidationUnsupportedError("Group-aware validation requires a group column on the training frame.")
        if plan.strategy == STRATIFIED_GROUP_KFOLD:
            split_pairs = list(splitter.split(positions, labels, groups=group_values))
        else:
            split_pairs = list(splitter.split(positions, groups=group_values))
    elif plan.strategy == STRATIFIED_KFOLD:
        split_pairs = list(splitter.split(positions, labels))
    else:
        split_pairs = list(splitter.split(positions))

    for fold_number, (train_idx, val_idx) in enumerate(split_pairs, start=1):
        train_idx = np.asarray(train_idx)
        val_idx = np.asarray(val_idx)
        overlap: list[Any] = []
        if group_values is not None and plan.group_column:
            train_groups = set(pd.unique(group_values[train_idx]))
            val_groups = set(pd.unique(group_values[val_idx]))
            overlap = sorted(train_groups & val_groups, key=str)
            if overlap:
                raise ValueError(
                    f"{plan.strategy} fold {fold_number} leaked groups {overlap!r}."
                )
        train_tmin = train_tmax = val_tmin = val_tmax = None
        if times is not None and plan.time_column:
            train_times = times.iloc[train_idx].dropna()
            val_times = times.iloc[val_idx].dropna()
            train_tmin = _iso(train_times.min()) if len(train_times) else None
            train_tmax = _iso(train_times.max()) if len(train_times) else None
            val_tmin = _iso(val_times.min()) if len(val_times) else None
            val_tmax = _iso(val_times.max()) if len(val_times) else None
            if train_tmax and val_tmin and pd.Timestamp(train_tmax) > pd.Timestamp(val_tmin):
                raise ValueError(
                    f"{plan.strategy} fold {fold_number} is not chronological: "
                    f"train_max={train_tmax} validation_min={val_tmin}."
                )
        yield FoldSplit(
            fold_number=fold_number,
            train_index=train_idx,
            validation_index=val_idx,
            train_count=int(len(train_idx)),
            validation_count=int(len(val_idx)),
            group_overlap=overlap,
            train_time_min=train_tmin,
            train_time_max=train_tmax,
            validation_time_min=val_tmin,
            validation_time_max=val_tmax,
        )
