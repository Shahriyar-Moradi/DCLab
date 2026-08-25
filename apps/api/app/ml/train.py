"""Train the conversion-probability layer factory and persist the fused artifact.

This module is a facade over ``app.engine.experiments.factory`` so existing
imports (API tests, simulation runner) keep working.
"""

from __future__ import annotations

from pathlib import Path

from app.engine.experiments.factory import (
    SAMPLE_CSV,
    evaluate_binary,
    feature_table,
    generic_feature_table,
    load_labeled_frame,
    opportunity_feature_table,
    run_factory_train,
    slice_matrix,
    split_frame,
)

# Names used by the simulation runner.
_load_labeled_frame = load_labeled_frame
_split = split_frame
_evaluate = evaluate_binary
_feature_table = feature_table
_generic_feature_table = generic_feature_table
_opportunity_feature_table = opportunity_feature_table
_slice_matrix = slice_matrix


def train_and_save(
    csv_path: Path | None = None,
    model_dir: Path | None = None,
    layer_path: Path | None = None,
    target_col: str | None = None,
) -> dict:
    return run_factory_train(csv_path, model_dir, layer_path, target_col)


def main() -> None:
    train_and_save()


if __name__ == "__main__":
    main()
