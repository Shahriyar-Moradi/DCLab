"""Reproduce a persisted DCLab experiment's own predictions on an arbitrary
frame (namely: the shared test split), using its saved winner. No retraining —
strictly "ask the experiment that already ran what it would say".

P7.1-A: runs save the winner as a model package (``model_package.zip``: skops +
native booster formats, no pickle), loaded with the package loader. Runs saved
before that (pickled members, weighted blends) are no longer read here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Experiment, PredictionTask
from app.engine.serving.model_package import PACKAGE_FILENAME, read_package, sha256_hex


def latest_experiment_for(db: Session, case_study_id: str) -> Experiment:
    row = (
        db.query(Experiment)
        .join(PredictionTask, Experiment.task_id == PredictionTask.id)
        .filter(PredictionTask.slug == case_study_id)
        .order_by(Experiment.created_at.desc())
        .first()
    )
    if row is None:
        raise ValueError(f"no DCLab experiment found for case study {case_study_id!r} — run Step 2 first")
    return row


def _candidate_features(result: dict[str, Any], candidate_id: str) -> list[str]:
    for row in result.get("candidates") or []:
        if row.get("candidate_id") == candidate_id:
            return list(row.get("features") or [])
    raise KeyError(f"candidate {candidate_id!r} not found in experiment result's candidate list")


def score_frame(experiment: Experiment, frame: pd.DataFrame, *, task_type: str) -> np.ndarray:
    result = experiment.result or {}
    if not experiment.artifact_dir:
        raise ValueError(f"experiment {experiment.id} has no artifact_dir")
    if result.get("fusion") == "weighted_blend":
        raise ValueError(f"experiment {experiment.id} is a pickled weighted blend; retrain it to get a model package")
    candidate_id = (result.get("best_single") or {}).get("candidate_id")
    if candidate_id is None:
        raise ValueError(f"experiment {experiment.id} has no best_single")
    from app.engine.serving.package_loader import load_model_package

    payload = (Path(experiment.artifact_dir) / PACKAGE_FILENAME).read_bytes()
    model = load_model_package(payload, manifest_digest=sha256_hex(read_package(payload).manifest_bytes)).pipeline
    X = frame.loc[:, _candidate_features(result, candidate_id)]
    if task_type == "binary":
        proba = model.predict_proba(X)
        return np.asarray(proba[:, 1] if proba.shape[1] > 1 else proba[:, 0], dtype=float)
    return np.asarray(model.predict(X), dtype=float)
