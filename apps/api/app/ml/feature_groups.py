"""Feature perspectives for the conversion-probability layer.

Groups are named views of the same Opportunity. They exist so candidate models
can see different slices of reality — not so we invent extra data sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.ml.features import FEATURE_NAMES

def load_layer_config(path: Path | None = None) -> dict[str, Any]:
    layer_path = Path(path or get_settings().layer_path)
    with layer_path.open() as handle:
        config = yaml.safe_load(handle)
    if not config or "version" not in config:
        raise ValueError(f"Layer config at {layer_path} is missing a version field")
    return config


def group_features(config: dict[str, Any]) -> dict[str, list[str]]:
    groups = dict(config.get("feature_groups") or {})
    for name, columns in groups.items():
        unknown = [col for col in columns if col not in FEATURE_NAMES]
        if unknown:
            raise ValueError(f"Feature group {name!r} references unknown features: {unknown}")
    return {name: list(cols) for name, cols in groups.items()}


def features_for_groups(config: dict[str, Any], group_names: list[str]) -> list[str]:
    groups = group_features(config)
    ordered: list[str] = []
    seen: set[str] = set()
    for group in group_names:
        if group not in groups:
            raise KeyError(f"Unknown feature group: {group}")
        for column in groups[group]:
            if column not in seen:
                ordered.append(column)
                seen.add(column)
    return ordered
