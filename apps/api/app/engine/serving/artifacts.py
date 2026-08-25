"""Local filesystem artifact store. Swap this module later for S3."""

from __future__ import annotations

from pathlib import Path

from app.config import REPO_ROOT


def experiment_dir(experiment_id: str) -> Path:
    path = REPO_ROOT / "artifacts" / "experiments" / experiment_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "members").mkdir(exist_ok=True)
    return path
