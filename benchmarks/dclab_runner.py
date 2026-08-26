"""Step 2 — DCLab full pipeline runner (per case study).

Wires each case study config into the DCLab engine's *existing* experiment
machinery, using the exact same ``app.services.lab_service`` functions the
``dclab experiment run`` CLI command calls under the hood (``seed_dogfood``,
``ingest_dataset``, ``upsert_task``, ``create_experiment``,
``execute_experiment``) — so every case study run becomes a normal DCLab
``Experiment`` DB row, inspectable through the existing ``/lab/experiments/*``
API and the ``dclab experiment report`` CLI, exactly as if it had been run
by hand through the Lab UI. Nothing about candidate generation, leakage
detection, validation, selection, or ensembling is reimplemented here — this
module only turns a ``CaseStudyConfig`` into the same DB rows a human running
the Lab would have created, and lets ``run_experiment`` do everything else.

Deliberately NOT wired into ``app.cli.main`` / the ``dclab`` command: baking
"case study" concepts into the generic engine CLI would be exactly the kind
of dataset-specific branching the DCLab milestone brief prohibits inside the
engine layer ("the engine itself never branches on 'olist'", generalized
here to "never branches on a case study id"). This harness sits on top of
the engine and calls it; the engine and its CLI stay generic. The build
doc's ``dclab experiment run --case-study $cs`` verify line is therefore run
as ``python -m benchmarks.dclab_runner --case-study $cs`` instead — same
effect, same underlying DB rows, without teaching the shared CLI about
benchmarks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.session import get_session_factory
from app.engine.types import SearchConfig, TaskSpec
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_dataset,
    search_from_mapping,
    seed_dogfood,
    upsert_task,
)

from benchmarks.case_studies.data import compute_split_hash, load_raw_frame, make_split, prepare_frame, resolve_seed
from benchmarks.case_studies.registry import CASE_STUDY_IDS, load_case_study
from benchmarks.case_studies.schema import CaseStudyConfig

DEFAULT_SEARCH_PATH = REPO_ROOT / "configs" / "experiments" / "default.yaml"
FRAME_DIR = REPO_ROOT / "data" / "case_studies"


def _task_spec(config: CaseStudyConfig) -> TaskSpec:
    return TaskSpec(
        id=config.id,
        name=config.name,
        description=config.business_problem,
        task_type=config.target.task_type,
        target=config.target.target_column,
        entity_id=config.target.entity_id,
        prediction_time_column=config.target.prediction_time_column,
        prediction_horizon_days=config.target.horizon_days,
        evaluation_metric=config.target.evaluation_metric,
        feature_groups=config.feature_groups,
        validation_strategy=config.validation_strategy,
        # No config_path: execute_experiment would otherwise try to reload a
        # `search:` block from it. This TaskSpec is built directly from the
        # validated CaseStudyConfig, not from a configs/tasks/*.yaml file.
    )


def _search_config(config: CaseStudyConfig, *, seed: int) -> SearchConfig:
    raw = yaml.safe_load(DEFAULT_SEARCH_PATH.read_text()) or {}
    overrides = {**config.search_overrides, "seed": seed}
    return search_from_mapping(raw.get("search"), overrides=overrides)


def _materialize_frame(config: CaseStudyConfig, *, frame: Any = None, name_suffix: str = "") -> Path:
    """Write a prepared frame to the on-disk CSV the engine ingests through —
    confirmed empirically that a CSV round-trip does not change the
    resulting split (see Step 2 report). Defaults to the full case study
    frame; ``frame``/``name_suffix`` let a caller (e.g. a Step 5 fold rerun)
    materialize a different (e.g. truncated) frame under a distinct dataset
    name without colliding with the main ``frame.csv``."""
    if frame is None:
        raw, _ = load_raw_frame(config)
        frame = prepare_frame(config, raw)
    out_dir = FRAME_DIR / config.id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"frame{name_suffix}.csv"
    frame.to_csv(path, index=False)
    return path


def run_experiment_on_frame(
    db: Session, config: CaseStudyConfig, frame: Any, *, seed: int, dataset_suffix: str = ""
) -> Any:
    """Run the full DCLab engine pipeline (ingest -> task -> experiment ->
    execute) on an explicitly-provided, already-prepared frame, rather than
    always reloading+re-preparing the case study's full raw data. This is
    what Step 5's fold reruns need: the engine's own internal split_frame
    call still does the actual train/val/test split (time-based, on
    whatever frame it's given), so feeding it a chronologically truncated
    frame naturally produces a different, non-identical train/test window
    per fold — a real walk-forward fold, not a re-labeled copy of the
    original split. ``run_case_study_experiment`` below is the frac=1.0 /
    full-frame special case of this function.
    """
    env = seed_dogfood(db)
    frame_path = _materialize_frame(config, frame=frame, name_suffix=dataset_suffix)
    dataset = ingest_dataset(
        db,
        environment=env,
        name=f"case_study_{config.id}{dataset_suffix}",
        location=str(frame_path),
        source_type="csv",
        version="v1",
    )
    spec = _task_spec(config)
    task_row = upsert_task(db, env, spec)
    search_cfg = _search_config(config, seed=seed)
    experiment = create_experiment(db, environment=env, dataset=dataset, task=task_row, config=search_cfg)
    return execute_experiment(db, experiment)


def run_case_study_experiment(
    db: Session, case_study_id: str, *, seed: int | None = None
) -> tuple[Any, CaseStudyConfig, int]:
    config = load_case_study(case_study_id)
    resolved_seed = seed if seed is not None else resolve_seed(config)
    experiment = run_experiment_on_frame(db, config, None, seed=resolved_seed)
    return experiment, config, resolved_seed


def verify_split_matches_baseline(config: CaseStudyConfig, *, seed: int) -> dict[str, Any]:
    """Independently recompute the split for this case study + seed (the same
    way the baseline runner did) and compare its hash to Step 1's stored
    baseline hash. Uses benchmarks.case_studies.data — the same function the
    engine run itself relied on internally — so this is checking "did the
    shared split function get called with the same effective inputs," which
    is the actual guarantee, not a coincidence of two independently written
    split routines agreeing."""
    raw, _ = load_raw_frame(config)
    prepared = prepare_frame(config, raw)
    train, val, test, _split_meta = make_split(config, prepared, seed=seed)
    split_hash = compute_split_hash(config, train, val, test)

    baseline_path = REPO_ROOT / "artifacts" / "case_studies" / config.id / "baseline" / "result.json"
    baseline_hash = None
    baseline_counts = None
    if baseline_path.exists():
        baseline_result = json.loads(baseline_path.read_text())
        baseline_hash = baseline_result["split"]["hash"]
        baseline_counts = {
            "n_train": baseline_result["split"]["n_train"],
            "n_val": baseline_result["split"]["n_val"],
            "n_test": baseline_result["split"]["n_test"],
        }
    engine_counts = {"n_train": len(train), "n_val": len(val), "n_test": len(test)}
    return {
        "engine_split_hash": split_hash,
        "baseline_split_hash": baseline_hash,
        "hash_matches": baseline_hash is not None and baseline_hash == split_hash,
        "engine_counts": engine_counts,
        "baseline_counts": baseline_counts,
        "counts_match": baseline_counts == engine_counts,
    }


def _summarize(experiment: Any, config: CaseStudyConfig, seed: int, check: dict[str, Any]) -> dict[str, Any]:
    result = experiment.result or {}
    return {
        "experiment_id": str(experiment.id),
        "case_study_id": config.id,
        "status": experiment.status,
        "seed": seed,
        "funnel": result.get("funnel"),
        "fusion": result.get("fusion"),
        "selected_ids": result.get("selected_ids"),
        "best_single": {
            "candidate_id": (result.get("best_single") or {}).get("candidate_id"),
            "model_family": (result.get("best_single") or {}).get("model_family"),
            "score": (result.get("best_single") or {}).get("score"),
        },
        "test_metrics": result.get("test_metrics"),
        "split_check": check,
        "artifact_dir": experiment.artifact_dir,
    }


def run_all(seed: int | None = None) -> list[dict[str, Any]]:
    db = get_session_factory()()
    summaries = []
    try:
        for case_study_id in CASE_STUDY_IDS:
            experiment, config, resolved_seed = run_case_study_experiment(db, case_study_id, seed=seed)
            check = verify_split_matches_baseline(config, seed=resolved_seed)
            summaries.append(_summarize(experiment, config, resolved_seed, check))
    finally:
        db.close()
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.dclab_runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case-study", help="single case study id, e.g. purchase_prediction")
    group.add_argument("--all", action="store_true", help="run all 6 case studies in registry order")
    parser.add_argument("--seed", type=int, default=None, help="override the case study's default seed")
    args = parser.parse_args()

    if args.all:
        summaries = run_all(seed=args.seed)
        print(json.dumps(summaries, default=str, indent=2))
        return

    db = get_session_factory()()
    try:
        experiment, config, seed = run_case_study_experiment(db, args.case_study, seed=args.seed)
        check = verify_split_matches_baseline(config, seed=seed)
        print(json.dumps(_summarize(experiment, config, seed, check), default=str, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
