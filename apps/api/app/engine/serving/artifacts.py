"""Per-run working directory for the engine (worker-local scratch).

Durable run outputs are published to object storage by the services layer
(``reproducibility_service.store_report_artifacts``); readers fetch them from
there. This directory is only the engine's workspace while a job runs.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def run_scratch_root() -> Path:
    from app.config import get_settings

    configured = get_settings().run_scratch_root
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "dclab-runs"


def experiment_dir(experiment_id: str) -> Path:
    path = run_scratch_root() / "experiments" / experiment_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "members").mkdir(exist_ok=True)
    return path
