"""Artifact access boundary used by verification and reporting services.

The current deployment stores artifacts on the local filesystem.  Keeping that
detail behind this small interface lets verification move to object storage
without changing its evidence rules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import pandas as pd


class ArtifactAccess(Protocol):
    def artifact_exists(self, location: str) -> bool: ...

    def load_table(self, location: str) -> pd.DataFrame: ...


class LocalArtifactAccess:
    """Local-filesystem implementation; the only implementation for now."""

    def artifact_exists(self, location: str) -> bool:
        return Path(location).is_file()

    def load_table(self, location: str) -> pd.DataFrame:
        path = Path(location)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix in {".tsv", ".tab"}:
            return pd.read_csv(path, sep="\t")
        if suffix in {".parquet", ".pq"}:
            return pd.read_parquet(path)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        if suffix in {".json", ".jsonl", ".ndjson"}:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return pd.DataFrame()
            if text[0] != "[" and "\n" in text:
                return pd.json_normalize(
                    [json.loads(line) for line in text.splitlines() if line.strip()]
                )
            value = json.loads(text)
            if isinstance(value, list):
                return pd.json_normalize(value)
            if isinstance(value, dict):
                for key in ("records", "data", "rows", "items"):
                    if isinstance(value.get(key), list):
                        return pd.json_normalize(value[key])
                return pd.json_normalize([value])
            raise ValueError("JSON input is not a tabular object or list")
        return pd.read_csv(path, sep=None, engine="python")


def artifact_exists(location: str, access: ArtifactAccess | None = None) -> bool:
    """Small storage-agnostic existence concept used at service boundaries."""
    return (access or LocalArtifactAccess()).artifact_exists(location)
