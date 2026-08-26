"""Shared frame + split builder for the case-study benchmark harness.

This is the single code path that turns a ``CaseStudyConfig`` into a
train/val/test split. Both the baseline runner (Step 1) and the DCLab engine
runner (Step 2) call this module and nothing else to get their frame and
split. That is what makes "the baseline used the identical split" a
structural guarantee instead of a hope — there is exactly one function that
can produce a split for a given case study + seed, and every runner in this
harness calls it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pandas as pd

from app.engine.datasets import olist as olist_adapter
from app.engine.validation.splits import split_frame

from benchmarks.case_studies.schema import CaseStudyConfig
from benchmarks.case_studies.synthetic_generators import GENERATORS

# Matches configs/experiments/default.yaml's search.seed — the seed every
# DCLab experiment uses unless a case study's search_overrides says
# otherwise. Baseline and engine runs must agree on this to get the same split.
DEFAULT_SEED = 42


@dataclass
class CaseStudyData:
    frame: pd.DataFrame
    ground_truth: pd.DataFrame | None
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    split_meta: dict
    split_hash: str
    seed: int


def resolve_seed(config: CaseStudyConfig) -> int:
    return int(config.search_overrides.get("seed", DEFAULT_SEED))


def _apply_entity_filter(frame: pd.DataFrame, config: CaseStudyConfig) -> pd.DataFrame:
    """Apply the population filter documented in data_source.entity_filter.

    entity_filter is a human-readable string carried in the config for
    reporting; the actual filter predicate is implemented per case study
    here (there's no filter DSL — six case studies don't warrant one).
    """
    entity_filter = config.data_source.entity_filter
    if not entity_filter:
        return frame
    if config.id == "reactivation":
        return frame[frame["days_since_last_order"] >= 90].reset_index(drop=True)
    raise NotImplementedError(
        f"case study {config.id!r} declares entity_filter={entity_filter!r} but no filter is implemented for it"
    )


def load_raw_frame(config: CaseStudyConfig) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Returns (observed_frame, ground_truth_frame_or_none). No target cleanup yet."""
    source = config.data_source
    if source.kind == "olist":
        frame = olist_adapter.build_analytical(as_of_dates=source.as_of_dates)
        frame = _apply_entity_filter(frame, config)
        return frame, None
    if source.kind == "synthetic":
        generator = GENERATORS.get(source.generator or "")
        if generator is None:
            raise ValueError(f"unknown synthetic generator {source.generator!r}; known: {sorted(GENERATORS)}")
        observed, ground_truth = generator(n=source.n_entities, seed=source.seed)
        return observed, ground_truth
    raise ValueError(f"unknown data_source.kind {source.kind!r}")


def prepare_frame(config: CaseStudyConfig, frame: pd.DataFrame) -> pd.DataFrame:
    """Target cleanup only — mirrors, exactly, the `work` frame
    app.engine.experiments.runner.run_experiment builds right before calling
    split_frame:

        if task.task_type == "binary":
            work = work.dropna(subset=[task.target])
            work[task.target] = work[task.target].astype(int)

    Note the engine only drops NaN targets for binary tasks, not regression —
    so this deliberately does the same (verified empirically: none of the six
    case studies' target columns actually contain NaNs today, so this is a
    no-op in practice, but it must match the engine's real behavior, not an
    idealized version of it, for the split to be provably identical)."""
    target_col = config.target.target_column
    if target_col not in frame.columns:
        raise ValueError(f"frame is missing target column {target_col!r}")
    work = frame.copy()
    if config.target.task_type == "binary":
        work = work.dropna(subset=[target_col])
        work[target_col] = work[target_col].astype(int)
    return work.reset_index(drop=True)


def make_split(
    config: CaseStudyConfig, frame: pd.DataFrame, *, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    return split_frame(
        frame,
        strategy=config.validation_strategy,
        target=config.target.target_column,
        time_col=config.target.prediction_time_column,
        group_col=config.target.entity_id,
        seed=seed,
    )


def compute_split_hash(config: CaseStudyConfig, train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame) -> str:
    """Hash of exactly which rows landed in which split, keyed by (entity_id, as_of_date).

    Two independent calls that produce the same hash used the identical
    split, not just splits that "should" be the same size.
    """
    entity_col = config.target.entity_id
    time_col = config.target.prediction_time_column

    def _keys(part: pd.DataFrame) -> list[list[str]]:
        cols = [c for c in (entity_col, time_col) if c in part.columns]
        if not cols:
            return sorted([[str(i)] for i in part.index])
        sub = part.loc[:, cols].astype(str)
        return sorted(sub.values.tolist())

    payload = {"train": _keys(train), "val": _keys(val), "test": _keys(test)}
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def load_case_study_data(config: CaseStudyConfig, *, seed: int | None = None) -> CaseStudyData:
    resolved_seed = seed if seed is not None else resolve_seed(config)
    raw, ground_truth = load_raw_frame(config)
    prepared = prepare_frame(config, raw)
    train, val, test, split_meta = make_split(config, prepared, seed=resolved_seed)
    split_hash = compute_split_hash(config, train, val, test)
    return CaseStudyData(
        frame=prepared,
        ground_truth=ground_truth,
        train=train,
        val=val,
        test=test,
        split_meta=split_meta,
        split_hash=split_hash,
        seed=resolved_seed,
    )


def load_case_study_data_fold(config: CaseStudyConfig, *, seed: int, frac: float) -> CaseStudyData:
    """Walk-forward fold for Step 5's cross-fold stability check.

    Sorts the full prepared frame chronologically, truncates it to the first
    ``frac`` of rows, then applies the case study's own ``make_split`` (the
    same time-based 70/15/15 splitter every other runner uses) to that
    truncated history. Increasing ``frac`` across folds (e.g. 0.4, 0.6, 0.8,
    1.0) produces successive, non-identical train/test windows that all
    still respect "never train on the future" — a walk-forward CV, not a
    random K-fold, because every case study here is temporal (``as_of_date``
    exists in all six). ``frac=1.0`` reproduces the exact Step 1/2/3 split.
    """
    if not (0 < frac <= 1.0):
        raise ValueError(f"frac must be in (0, 1.0], got {frac}")
    raw, ground_truth = load_raw_frame(config)
    prepared = prepare_frame(config, raw)
    time_col = config.target.prediction_time_column
    sorted_frame = prepared.sort_values(time_col, kind="stable").reset_index(drop=True)
    n = max(int(len(sorted_frame) * frac), 10)
    truncated = sorted_frame.iloc[:n].reset_index(drop=True)

    fold_ground_truth = ground_truth
    if ground_truth is not None:
        kept_entities = set(truncated[config.target.entity_id].astype(str))
        fold_ground_truth = ground_truth[ground_truth["entity_id"].astype(str).isin(kept_entities)].reset_index(
            drop=True
        )

    train, val, test, split_meta = make_split(config, truncated, seed=seed)
    split_hash = compute_split_hash(config, train, val, test)
    return CaseStudyData(
        frame=truncated,
        ground_truth=fold_ground_truth,
        train=train,
        val=val,
        test=test,
        split_meta={**split_meta, "fold_frac": frac},
        split_hash=split_hash,
        seed=seed,
    )
