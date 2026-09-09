"""Train-partition problem profile for adaptive validation and metric planning.

Built only from the locked training frame. Detection does not create features
or choose a splitter by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.engine.lab.auto_prepare import infer_column_roles
from app.engine.lab.schema_inference import identifier_likelihood, looks_like_identifier, normalize_name
from app.engine.validation.splits import SOURCE_ROW_COLUMN

PROBLEM_PROFILE_VERSION = "dclab.problem_profile.v1"
HIGH_CARDINALITY_UNIQUE = 50
MIN_REPEATED_ENTITY_UNIQUE = 10
CATEGORY_NAME_TOKENS = {
    "category",
    "type",
    "status",
    "segment",
    "channel",
    "region",
    "gender",
    "class",
    "flag",
}
HIGH_MISSING_FRACTION = 0.2
GEO_NAME_LAT = {"lat", "latitude", "y_coord", "ycoord"}
GEO_NAME_LON = {"lon", "lng", "long", "longitude", "x_coord", "xcoord"}
TIME_NAME_TOKENS = {
    "as_of",
    "asof",
    "event_time",
    "event_date",
    "timestamp",
    "prediction_time",
    "observation_time",
    "observation_date",
    "obs_time",
    "obs_date",
}
WEAK_TIME_TOKENS = {"date", "time", "datetime", "created", "updated", "dt"}
ENTITY_NAME_TOKENS = {
    "customer",
    "user",
    "account",
    "entity",
    "patient",
    "household",
    "subject",
    "member",
    "client",
    "person",
}


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _tokens(name: str) -> set[str]:
    return set(normalize_name(name).split("_")) - {""}


def _missing_fraction(series: pd.Series) -> float:
    n = max(len(series), 1)
    return float(series.isna().mean()) if n else 0.0


def _parse_datetime(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce", utc=True)
        return parsed.dt.tz_convert(None) if getattr(parsed.dt, "tz", None) is not None else parsed
    return pd.to_datetime(series, errors="coerce")


def datetime_parse_success(series: pd.Series) -> float:
    observed = series.dropna()
    if len(observed) == 0:
        return 0.0
    if pd.api.types.is_datetime64_any_dtype(series):
        return 1.0
    name = str(series.name)
    key = normalize_name(name)
    tokens = _tokens(name)
    named = bool(tokens & (TIME_NAME_TOKENS | WEAK_TIME_TOKENS)) or any(
        needle in key
        for needle in ("as_of", "asof", "created_at", "updated_at", "timestamp")
    )
    if pd.api.types.is_bool_dtype(series):
        return 0.0
    if pd.api.types.is_numeric_dtype(series) and not named:
        return 0.0
    if not named:
        return 0.0
    parsed = _parse_datetime(observed)
    return float(parsed.notna().mean())


def _entity_evidence(series: pd.Series) -> dict[str, Any]:
    observed = series.dropna()
    n = int(len(series))
    unique_count = int(observed.nunique())
    counts = observed.astype("string").value_counts(dropna=True)
    max_rows = int(counts.max()) if len(counts) else 0
    mean_rows = float(counts.mean()) if len(counts) else 0.0
    repeated_rows = int((counts[counts > 1]).sum()) if len(counts) else 0
    return {
        "unique_count": unique_count,
        "unique_ratio": float(unique_count / max(n, 1)),
        "repeated_rows": repeated_rows,
        "max_rows_per_entity": max_rows,
        "mean_rows_per_entity": mean_rows,
        "row_count": n,
        "missing_count": int(series.isna().sum()),
        "identifier_likelihood": float(identifier_likelihood(str(series.name), series, n)),
    }


def _is_entity_like(name: str, series: pd.Series, evidence: dict[str, Any]) -> bool:
    tokens = _tokens(name)
    unique_count = int(evidence.get("unique_count") or 0)
    # Ordinary low-cardinality codes (delivery_category_id with 3 levels) are
    # categoricals, not grouping keys — even when the name ends in _id.
    if unique_count < MIN_REPEATED_ENTITY_UNIQUE:
        return False
    if tokens & CATEGORY_NAME_TOKENS and not (tokens & ENTITY_NAME_TOKENS):
        return False
    if evidence["identifier_likelihood"] >= 0.8:
        return True
    if looks_like_identifier(name, series, len(series)):
        return True
    if name.endswith(("_id", "_uuid", "_guid")) or normalize_name(name) in {"id", "uuid", "guid"}:
        return True
    return bool(tokens & ENTITY_NAME_TOKENS) and "id" in tokens


def _plausible_lat(series: pd.Series) -> bool:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) < 3:
        return False
    return bool(numeric.between(-90, 90).mean() >= 0.9)


def _plausible_lon(series: pd.Series) -> bool:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) < 3:
        return False
    return bool(numeric.between(-180, 180).mean() >= 0.9)


@dataclass
class ProblemProfile:
    task_type: str
    target: str
    row_count: int
    feature_count: int
    class_distribution: dict[str, int] | None = None
    minority_class_fraction: float | None = None
    imbalance_ratio: float | None = None
    regression_target: dict[str, Any] | None = None
    numeric_columns: list[str] = field(default_factory=list)
    categorical_columns: list[str] = field(default_factory=list)
    boolean_columns: list[str] = field(default_factory=list)
    datetime_columns: list[str] = field(default_factory=list)
    identifier_columns: list[str] = field(default_factory=list)
    free_text_columns: list[str] = field(default_factory=list)
    high_cardinality_columns: list[str] = field(default_factory=list)
    high_missing_columns: list[str] = field(default_factory=list)
    constant_columns: list[str] = field(default_factory=list)
    repeated_entity_candidates: list[dict[str, Any]] = field(default_factory=list)
    time_candidates: list[dict[str, Any]] = field(default_factory=list)
    geo_coordinate_candidates: list[dict[str, Any]] = field(default_factory=list)
    version: str = PROBLEM_PROFILE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))

    @classmethod
    def from_dict(cls, payload: Any) -> ProblemProfile:
        from app.engine.modeling.coerce import from_mapping

        profile = from_mapping(cls, payload)
        if profile is None:
            raise ValueError("ProblemProfile evidence is missing.")
        return profile


def build_problem_profile(
    train: pd.DataFrame,
    *,
    target: str,
    task_type: str,
    feature_columns: list[str] | None = None,
) -> ProblemProfile:
    """Summarize the locked training partition. Never inspect holdout rows."""
    if target not in train.columns:
        raise ValueError(f"training partition is missing target {target!r}")
    skip = {target, SOURCE_ROW_COLUMN}
    columns = [name for name in (feature_columns or list(train.columns)) if name in train.columns and name not in skip]
    n = int(len(train))
    roles = infer_column_roles(train, columns)
    y = train[target]
    class_distribution: dict[str, int] | None = None
    minority_fraction: float | None = None
    imbalance_ratio: float | None = None
    regression_target: dict[str, Any] | None = None
    if task_type == "binary":
        counts = y.value_counts(dropna=True)
        class_distribution = {str(_native(key)): int(value) for key, value in counts.items()}
        if len(counts):
            minority = int(counts.min())
            majority = int(counts.max())
            minority_fraction = float(minority / max(int(counts.sum()), 1))
            imbalance_ratio = float(majority / max(minority, 1))
    else:
        numeric = pd.to_numeric(y, errors="coerce")
        regression_target = {
            "count": int(numeric.notna().sum()),
            "missing_count": int(numeric.isna().sum()),
            "mean": float(numeric.mean()) if numeric.notna().any() else None,
            "std": float(numeric.std()) if numeric.notna().sum() > 1 else None,
            "min": float(numeric.min()) if numeric.notna().any() else None,
            "max": float(numeric.max()) if numeric.notna().any() else None,
            "median": float(numeric.median()) if numeric.notna().any() else None,
        }

    high_cardinality: list[str] = []
    high_missing: list[str] = []
    constant: list[str] = []
    for name in columns:
        series = train[name]
        unique = int(series.nunique(dropna=True))
        if unique <= 1:
            constant.append(name)
        if _missing_fraction(series) > HIGH_MISSING_FRACTION:
            high_missing.append(name)
        if unique > HIGH_CARDINALITY_UNIQUE and name not in roles.identifier:
            high_cardinality.append(name)

    repeated: list[dict[str, Any]] = []
    for name in columns:
        series = train[name]
        evidence = _entity_evidence(series)
        if evidence["max_rows_per_entity"] < 2:
            continue
        if evidence["unique_count"] < 2:
            continue
        if evidence["unique_ratio"] >= 0.95:
            continue
        if name in roles.datetime or pd.api.types.is_bool_dtype(series):
            continue
        if not _is_entity_like(name, series, evidence):
            continue
        repeated.append({"column": name, **evidence})
    repeated.sort(
        key=lambda item: (
            -float(item["identifier_likelihood"]),
            -int(item["max_rows_per_entity"]),
            -float(item["mean_rows_per_entity"]),
            item["column"],
        )
    )

    time_candidates: list[dict[str, Any]] = []
    for name in columns:
        series = train[name]
        key = normalize_name(name)
        tokens = _tokens(name)
        named = bool(tokens & (TIME_NAME_TOKENS | WEAK_TIME_TOKENS)) or any(
            needle in key for needle in ("as_of", "asof", "created_at", "updated_at", "timestamp")
        )
        parse_rate = datetime_parse_success(series)
        if parse_rate < 0.8 and name not in roles.datetime:
            continue
        if parse_rate < 0.8:
            continue
        if pd.api.types.is_numeric_dtype(series) and not named:
            continue
        parsed = _parse_datetime(series)
        valid = parsed.dropna()
        unique_count = int(valid.nunique())
        span_seconds = float((valid.max() - valid.min()).total_seconds()) if len(valid) else 0.0
        ordered = valid.sort_values()
        monotonic = float(ordered.is_monotonic_increasing) if len(ordered) > 1 else 0.0
        unique_ratio = float(unique_count / max(n, 1))
        strong_name = any(
            needle in key
            for needle in (
                "as_of",
                "asof",
                "event_time",
                "event_date",
                "timestamp",
                "prediction_time",
                "observation_time",
                "observation_date",
                "obs_time",
                "obs_date",
            )
        )
        # A date column is only a *candidate*. Strong temporal structure is a
        # higher bar used by the validation planner, never assumed here.
        time_candidates.append(
            {
                "column": name,
                "parse_rate": float(parse_rate),
                "unique_count": unique_count,
                "unique_ratio": unique_ratio,
                "span_seconds": span_seconds,
                "monotonic_increasing": monotonic,
                "name_tokens": sorted(tokens),
                "strong_name": strong_name,
                "datetime_role": name in roles.datetime,
            }
        )

    geo: list[dict[str, Any]] = []
    lat_cols = [name for name in columns if _tokens(name) & GEO_NAME_LAT or normalize_name(name) in GEO_NAME_LAT]
    lon_cols = [name for name in columns if _tokens(name) & GEO_NAME_LON or normalize_name(name) in GEO_NAME_LON]
    for lat_name in lat_cols:
        if not _plausible_lat(train[lat_name]):
            continue
        for lon_name in lon_cols:
            if lat_name == lon_name:
                continue
            if not _plausible_lon(train[lon_name]):
                continue
            geo.append(
                {
                    "lat_column": lat_name,
                    "lon_column": lon_name,
                    "row_count": n,
                    "lat_in_range_fraction": float(
                        pd.to_numeric(train[lat_name], errors="coerce").between(-90, 90).mean()
                    ),
                    "lon_in_range_fraction": float(
                        pd.to_numeric(train[lon_name], errors="coerce").between(-180, 180).mean()
                    ),
                }
            )

    modeled = roles.numerical + roles.categorical + roles.boolean
    return ProblemProfile(
        task_type=task_type,
        target=target,
        row_count=n,
        feature_count=len(modeled) if modeled else len(columns),
        class_distribution=class_distribution,
        minority_class_fraction=minority_fraction,
        imbalance_ratio=imbalance_ratio,
        regression_target=regression_target,
        numeric_columns=list(roles.numerical),
        categorical_columns=list(roles.categorical),
        boolean_columns=list(roles.boolean),
        datetime_columns=list(roles.datetime),
        identifier_columns=list(roles.identifier),
        free_text_columns=list(roles.ignored_free_text),
        high_cardinality_columns=high_cardinality,
        high_missing_columns=high_missing,
        constant_columns=constant,
        repeated_entity_candidates=repeated,
        time_candidates=time_candidates,
        geo_coordinate_candidates=geo,
    )
