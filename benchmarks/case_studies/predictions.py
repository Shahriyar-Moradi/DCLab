"""Reproduce a persisted DCLab experiment's own predictions on an arbitrary
frame (namely: the shared test split), using its fitted, persisted member
models and its own fusion/weights decision. No retraining, no change to
which members ``run_experiment`` selected or how it weighted them — this is
strictly "ask the experiment that already ran what it would say," reused
from ``app.engine.ensemble.blend_probabilities`` for the blend math.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Experiment, PredictionTask
from app.engine.ensemble import blend_probabilities

from benchmarks.case_studies.features import to_matrix


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
    fusion = result.get("fusion")
    if not experiment.artifact_dir:
        raise ValueError(f"experiment {experiment.id} has no artifact_dir")
    members_dir = Path(experiment.artifact_dir) / "members"
    classifier = task_type == "binary"

    def _predict_one(candidate_id: str) -> np.ndarray:
        model = joblib.load(members_dir / f"{candidate_id}.joblib")
        features = _candidate_features(result, candidate_id)
        X = to_matrix(frame, features)
        if classifier:
            proba = model.predict_proba(X)
            return np.asarray(proba[:, 1] if proba.shape[1] > 1 else proba[:, 0], dtype=float)
        return np.asarray(model.predict(X), dtype=float)

    if fusion == "weighted_blend":
        weights = result.get("weights") or {}
        if not weights:
            raise ValueError(f"experiment {experiment.id} fusion=weighted_blend but has no weights")
        member_preds = {member_id: _predict_one(member_id) for member_id in weights}
        return blend_probabilities(member_preds, weights)

    best_single = result.get("best_single") or {}
    candidate_id = best_single.get("candidate_id")
    if candidate_id is None:
        raise ValueError(f"experiment {experiment.id} has no usable fusion ({fusion!r}) or best_single")
    return _predict_one(candidate_id)
