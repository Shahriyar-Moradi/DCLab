"""Assemble a downloadable notebook and script from ModelBuildReproductionSpec.

The documents follow the scientific holdout/CV order. They never embed raw
rows, credentials, Dataset.location, or object-store keys.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    ModelBuildReproductionSpec,
)
from app.services.model_build_codegen import _py_literal, strip_stage_header

NOTEBOOK_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("run_identity", "Run identity / reproducibility metadata", ()),
    ("imports", "Imports and versions", ()),
    ("dataset_loading", "Dataset loading placeholder + expected digest", ("ingestion",)),
    ("schema_checks", "Schema checks", ()),
    ("task_target", "Task / target", ("target_task",)),
    ("structural_cleanup", "Structural cleanup", ("structural_cleaning",)),
    ("holdout", "Holdout creation and lock", ("final_holdout_plan", "holdout_lock")),
    (
        "train_only_planning",
        "TRAIN-only profiling / planning",
        ("problem_profile", "validation_plan", "metric_plan", "missing_value_decisions"),
    ),
    ("leakage", "Leakage exclusions", ("leakage_audit",)),
    ("feature_engineering", "Feature engineering", ("feature_engineering",)),
    ("preprocessing", "Preprocessing", ("preprocessing",)),
    ("candidates", "Candidate definitions", ("candidate_generation",)),
    ("cv", "CV evaluation", ("cv_training",)),
    ("winner", "CV-only winner selection", ("candidate_comparison", "winner_lock")),
    ("refit", "Winner refit on all training data", ("final_refit",)),
    ("final_holdout", "Final holdout exactly once", ("final_holdout",)),
    ("metrics", "Metrics", ()),
    ("saved_artifacts", "Saved model / artifact information", ("artifact_reproducibility_persistence",)),
)


def _stage_map(spec: ModelBuildReproductionSpec) -> dict[str, str]:
    return {row.key: strip_stage_header(row.source) for row in spec.stage_code}


def _helpers(spec: ModelBuildReproductionSpec) -> list[str]:
    seen: list[str] = []
    for row in spec.stage_code:
        for helper in row.helper_requirements:
            if helper not in seen:
                seen.append(helper)
    return seen


def _combine(bodies: dict[str, str], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in keys:
        body = (bodies.get(key) or "").rstrip()
        if body:
            parts.append(body)
    return "\n\n".join(parts).rstrip() + "\n" if parts else "# No canonical evidence for this section.\n"


def _collect_imports(bodies: dict[str, str]) -> str:
    seen: list[str] = []
    for key, _title, stage_keys in NOTEBOOK_SECTIONS:
        if key in {"run_identity", "imports", "schema_checks", "metrics"}:
            continue
        for stage_key in stage_keys or ():
            for line in (bodies.get(stage_key) or "").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and stripped not in seen:
                    seen.append(stripped)
    extras = [
        "from pathlib import Path",
        "import pandas as pd",
    ]
    for line in extras:
        if line not in seen:
            seen.append(line)
    return "\n".join(sorted(seen)) + "\n"


def _identity_source(spec: ModelBuildReproductionSpec) -> str:
    helpers = _helpers(spec)
    lines = [
        f"PIPELINE_RUN_ID = {_py_literal(str(spec.pipeline_run_id))}",
        f"WORKSPACE_ID = {_py_literal(str(spec.workspace_id))}",
        f"GENERATOR_VERSION = {_py_literal(spec.generator_version)}",
        f"SPEC_DIGEST = {_py_literal(spec.spec_digest)}",
        f"SEED = {spec.task.seed}",
        f"DATASET_CONTENT_DIGEST = {_py_literal(spec.dataset.content_digest)}",
        f"DATASET_SCHEMA_DIGEST = {_py_literal(spec.dataset.schema_digest)}",
        "",
        "# This document is generated from canonical persisted evidence.",
        "# It does not embed raw rows, credentials, Dataset.location, or object-store keys.",
        f"# Authorized dataset path placeholder: {AUTHORIZED_DATASET_PATH_PLACEHOLDER}",
    ]
    if helpers:
        lines.append("")
        lines.append("# DCLab helpers required by this run:")
        lines.extend(f"# {helper}" for helper in helpers)
    return "\n".join(lines) + "\n"


def _imports_source(spec: ModelBuildReproductionSpec, bodies: dict[str, str]) -> str:
    lines = [_collect_imports(bodies).rstrip(), ""]
    versions: list[str] = []
    for candidate in spec.candidates:
        if candidate.library_version:
            versions.append(
                "# "
                f"{candidate.model_family}: {candidate.implementation_class} "
                f"{candidate.implementation_library}=={candidate.library_version}"
            )
    if versions:
        lines.append("# Persisted implementation versions")
        lines.extend(dict.fromkeys(versions))
    else:
        lines.append("# No persisted library versions were recorded for this run.")
    return "\n".join(lines).rstrip() + "\n"


def _schema_source(spec: ModelBuildReproductionSpec) -> str:
    names = [column.name for column in spec.dataset.columns]
    dtypes = {column.name: column.physical_dtype for column in spec.dataset.columns}
    lines = [
        f"EXPECTED_COLUMNS = {_py_literal(names)}",
        f"EXPECTED_PHYSICAL_DTYPES = {_py_literal(dtypes)}",
        f"EXPECTED_CONTENT_DIGEST = {_py_literal(spec.dataset.content_digest)}",
        f"EXPECTED_SCHEMA_DIGEST = {_py_literal(spec.dataset.schema_digest)}",
        "",
        "# Compare schema names only. Do not print raw rows or value samples.",
        "missing_columns = [name for name in EXPECTED_COLUMNS if name not in frame.columns]",
        "assert not missing_columns, missing_columns",
    ]
    return "\n".join(lines) + "\n"


def _metrics_source(spec: ModelBuildReproductionSpec) -> str:
    return (
        "\n".join(
            [
                f"PRIMARY_METRIC = {_py_literal(spec.metric_plan.primary_metric)}",
                f"HOLDOUT_METRICS = {_py_literal(spec.final_holdout.metrics)}",
                f"SELECTED_CV_SCORE = {_py_literal(spec.winner.selected_score)}",
                f"SELECTION_METRIC = {_py_literal(spec.winner.selection_metric)}",
                "",
                "# Holdout metrics are persisted after winner lock.",
                "# Do not use HOLDOUT_METRICS to change candidate selection.",
            ]
        )
        + "\n"
    )


def _section_source(
    spec: ModelBuildReproductionSpec, key: str, stage_keys: tuple[str, ...], bodies: dict[str, str]
) -> str:
    if key == "run_identity":
        return _identity_source(spec)
    if key == "imports":
        return _imports_source(spec, bodies)
    if key == "schema_checks":
        return _schema_source(spec)
    if key == "metrics":
        return _metrics_source(spec)
    return _combine(bodies, stage_keys)


def _markdown(title: str, body: str) -> str:
    return f"## {title}\n\n{body}".rstrip() + "\n"


def _section_markdown(key: str, title: str) -> str:
    notes = {
        "dataset_loading": (
            "Replace the authorized local-path placeholder. Never use a persisted "
            "`Dataset.location`, object-store key, or credential."
        ),
        "holdout": (
            "Create and lock the final holdout before candidate search. "
            "Do not score it until winner lock."
        ),
        "train_only_planning": (
            "ProblemProfile, ValidationPlan, and MetricPlan are train-partition evidence only."
        ),
        "cv": "Cross-validation uses the training partition only. The final holdout must not be used here.",
        "winner": "Lock the winner from CV aggregates only. Never read holdout labels here.",
        "final_holdout": "Evaluate the locked winner exactly once. Do not change selection using these scores.",
        "metrics": "Persisted metrics only. Labels and row values are not emitted.",
        "saved_artifacts": "Digests identify stored artifacts. Object-store keys are not included.",
    }
    extra = notes.get(key)
    if extra:
        return _markdown(title, extra)
    return _markdown(title, "Generated from canonical persisted evidence.")


def _cell_id(index: int, kind: str, key: str) -> str:
    return f"{index:02d}-{kind}-{key}"[:64]


def _markdown_cell(index: int, key: str, source: str) -> dict[str, Any]:
    return {
        "cell_type": "markdown",
        "id": _cell_id(index, "md", key),
        "metadata": {},
        "source": [source if source.endswith("\n") else source + "\n"],
    }


def _code_cell(index: int, key: str, source: str) -> dict[str, Any]:
    text = source if source.endswith("\n") else source + "\n"
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": _cell_id(index, "code", key),
        "metadata": {},
        "outputs": [],
        "source": [text],
    }


def render_reproduction_script(spec: ModelBuildReproductionSpec) -> str:
    bodies = _stage_map(spec)
    lines = [
        '"""DCLab model-build reproduction script.',
        "",
        f"generator={spec.generator_version}",
        f"spec_digest={spec.spec_digest}",
        f"pipeline_run_id={spec.pipeline_run_id}",
        "",
        "Generated from canonical persisted evidence. Not a notebook.",
        "Does not embed raw rows, credentials, Dataset.location, or object-store keys.",
        '"""',
        "",
    ]
    for index, (key, title, stage_keys) in enumerate(NOTEBOOK_SECTIONS, start=1):
        lines.append(f"# === {index}. {title} ===")
        lines.append(_section_source(spec, key, stage_keys, bodies).rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_reproduction_notebook(spec: ModelBuildReproductionSpec) -> dict[str, Any]:
    bodies = _stage_map(spec)
    cells: list[dict[str, Any]] = [
        _markdown_cell(
            0,
            "title",
            "\n".join(
                [
                    "# DCLab model-build reproduction",
                    "",
                    f"Generator `{spec.generator_version}`. Spec digest `{spec.spec_digest}`.",
                    "Scientific order: holdout lock, train-only planning, CV selection, then one final holdout evaluation.",
                ]
            )
            + "\n",
        )
    ]
    for index, (key, title, stage_keys) in enumerate(NOTEBOOK_SECTIONS, start=1):
        cells.append(_markdown_cell(index, key, _section_markdown(key, title)))
        cells.append(_code_cell(index, key, _section_source(spec, key, stage_keys, bodies)))
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "dclab": {
                "generator_version": spec.generator_version,
                "spec_digest": spec.spec_digest,
                "pipeline_run_id": str(spec.pipeline_run_id),
                "role": "reproduction_notebook",
            },
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }


def reproduction_notebook_bytes(spec: ModelBuildReproductionSpec) -> bytes:
    return (
        json.dumps(render_reproduction_notebook(spec), indent=1, sort_keys=True, ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def reproduction_script_bytes(spec: ModelBuildReproductionSpec) -> bytes:
    return render_reproduction_script(spec).encode("utf-8")


def reproduction_filenames(pipeline_run_id: Any) -> tuple[str, str]:
    prefix = str(pipeline_run_id)
    return f"{prefix}-reproduction.ipynb", f"{prefix}-reproduction.py"
