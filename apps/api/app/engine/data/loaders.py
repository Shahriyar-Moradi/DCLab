"""Load tabular files through one interface after they are on a local path."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _load_json_table(location: Path) -> pd.DataFrame:
    with location.open("r", encoding="utf-8", errors="replace") as handle:
        text = handle.read().strip()
    if not text:
        return pd.DataFrame()
    if text[0] != "[" and "\n" in text:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return pd.json_normalize(rows)
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return pd.json_normalize(parsed)
    if isinstance(parsed, dict):
        for key in ("records", "data", "rows", "items"):
            if isinstance(parsed.get(key), list):
                return pd.json_normalize(parsed[key])
        return pd.json_normalize([parsed])
    return pd.DataFrame()


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
    if suffix in {".tsv", ".tab"}:
        return pd.read_csv(location, sep="\t")
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(location)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(location)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return _load_json_table(location)
    return pd.read_csv(location, sep=None, engine="python")


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
