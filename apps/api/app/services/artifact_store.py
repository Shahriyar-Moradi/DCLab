"""Artifact access boundary used by verification and reporting services.

The current deployment stores artifacts on the local filesystem.  Keeping that
detail behind this small interface lets verification move to object storage
without changing its evidence rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

from app.engine.data.loaders import load_table as load_table_from_path


class ArtifactAccess(Protocol):
    def artifact_exists(self, location: str) -> bool: ...

    def load_table(self, location: str) -> pd.DataFrame: ...


class LocalArtifactAccess:
    """Local-filesystem implementation; the only implementation for now."""

    def artifact_exists(self, location: str) -> bool:
        return Path(location).is_file()

    def load_table(self, location: str) -> pd.DataFrame:
        return load_table_from_path(location)


def artifact_exists(location: str, access: ArtifactAccess | None = None) -> bool:
    """Small storage-agnostic existence concept used at service boundaries."""
    return (access or LocalArtifactAccess()).artifact_exists(location)
