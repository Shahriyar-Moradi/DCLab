"""Load CSV and Parquet frames through one interface."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_table(path: str | Path) -> pd.DataFrame:
    location = Path(path)
    if location.is_dir():
        tables = sorted(location.glob("*.csv")) + sorted(location.glob("*.parquet"))
        if not tables:
            raise FileNotFoundError(f"No CSV or Parquet files in {location}")
        location = tables[0]
    suffix = location.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(location)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(location)
    raise ValueError(f"Unsupported dataset format: {location.suffix}")


def infer_schema(frame: pd.DataFrame) -> dict:
    columns = []
    for name, dtype in frame.dtypes.items():
        kind = str(dtype)
        if pd.api.types.is_bool_dtype(dtype):
            semantic = "boolean"
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            semantic = "datetime"
        elif pd.api.types.is_numeric_dtype(dtype):
            semantic = "numeric"
        else:
            semantic = "categorical"
        columns.append({"name": str(name), "dtype": kind, "semantic": semantic})
    return {"columns": columns, "row_count": int(len(frame)), "column_count": int(frame.shape[1])}
