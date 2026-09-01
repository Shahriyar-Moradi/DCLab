"""Fine-grained ML-run stages stored on `ClientLabUpload.pipeline_status`.

This extends the existing upload status column rather than adding a second
enum. The normal client only ever sees four lifecycle values:

    queued → processing → completed
                         → failed

Detailed stages stay on the stored column for execution, logging, and admin
inspection. Client-facing `stage` / `pipeline_status` stay that four-state
view. Progress on the run page uses five server-mapped milestones in
`milestone` + `steps` — never the raw internal stage names.
"""

from __future__ import annotations

from typing import Any

from app.translation.banned_terms import is_clean

# Stored on client_lab_uploads.pipeline_status (lowercase, matching queued/failed).
QUEUED = "queued"
INGESTING = "ingesting"
ANALYZING = "analyzing"
CLEANING = "cleaning"
FEATURE_ENGINEERING = "feature_engineering"
PREPROCESSING = "preprocessing"
SPLITTING = "splitting"
CROSS_VALIDATION = "cross_validation"
TRAINING = "training"
EVALUATING = "evaluating"
PREDICTING = "predicting"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"
RUNNING = "running"  # legacy coarse in-progress value
NOT_APPLICABLE = "not_applicable"

IN_PROGRESS_STAGES = frozenset(
    {
        QUEUED,
        INGESTING,
        ANALYZING,
        CLEANING,
        FEATURE_ENGINEERING,
        PREPROCESSING,
        SPLITTING,
        CROSS_VALIDATION,
        TRAINING,
        EVALUATING,
        PREDICTING,
        RUNNING,
    }
)

CLIENT_STATUSES = frozenset({"queued", "processing", "completed", "failed"})

# Five client-safe milestones. Internal stages never leave this table.
CLIENT_MILESTONES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("uploading", "Uploading", frozenset({QUEUED})),
    ("looking", "Analyzing your data", frozenset({INGESTING, ANALYZING, RUNNING})),
    (
        "preparing",
        "Preparing your data",
        frozenset({CLEANING, FEATURE_ENGINEERING, PREPROCESSING}),
    ),
    (
        "building",
        "Building your model",
        frozenset({SPLITTING, CROSS_VALIDATION, TRAINING}),
    ),
    ("finishing", "Finishing up", frozenset({EVALUATING, PREDICTING})),
)

GENERIC_FAILURE_MESSAGE = "We could not finish this analysis."
PROCESSING_HEADLINE = "Analyzing your data..."

# Execution order used by admin processing-summary flags. FAILED/SKIPPED are
# omitted so a crashed run is inferred from persisted artifacts instead.
STAGE_ORDER: tuple[str, ...] = (
    QUEUED,
    INGESTING,
    ANALYZING,
    CLEANING,
    FEATURE_ENGINEERING,
    PREPROCESSING,
    SPLITTING,
    CROSS_VALIDATION,
    TRAINING,
    EVALUATING,
    PREDICTING,
    COMPLETED,
)


def lifecycle_status(pipeline_status: str) -> str:
    """The only four values a normal client may see."""
    if pipeline_status == QUEUED:
        return "queued"
    if pipeline_status == COMPLETED:
        return "completed"
    if pipeline_status in IN_PROGRESS_STAGES:
        return "processing"
    return "failed"


def client_stage(pipeline_status: str) -> str:
    return lifecycle_status(pipeline_status)


def _milestone_index(pipeline_status: str) -> int | None:
    if pipeline_status == COMPLETED:
        return len(CLIENT_MILESTONES) - 1
    for index, (_sid, _label, stages) in enumerate(CLIENT_MILESTONES):
        if pipeline_status in stages:
            return index
    if pipeline_status in IN_PROGRESS_STAGES:
        return 1
    return None


def milestone_for(pipeline_status: str) -> str:
    """Client-safe label for the current coarse milestone. Empty when finished."""
    if lifecycle_status(pipeline_status) not in {"queued", "processing"}:
        return ""
    index = _milestone_index(pipeline_status)
    if index is None:
        return ""
    return CLIENT_MILESTONES[index][1]


def headline(pipeline_status: str) -> str:
    return milestone_for(pipeline_status)


def public_pipeline_status(pipeline_status: str) -> str:
    """Coarse value safe to put on `/app` (never a stored ML stage name)."""
    return lifecycle_status(pipeline_status)


def steps_for(pipeline_status: str) -> list[dict[str, Any]]:
    """Five client milestones with done / current / upcoming — never raw stages."""
    current = _milestone_index(pipeline_status)
    failed = pipeline_status in {FAILED, SKIPPED, NOT_APPLICABLE}
    steps: list[dict[str, Any]] = []
    for index, (sid, label, _stages) in enumerate(CLIENT_MILESTONES):
        if pipeline_status == COMPLETED:
            state = "done"
        elif failed or current is None:
            state = "upcoming"
        elif index < current:
            state = "done"
        elif index == current:
            state = "current"
        else:
            state = "upcoming"
        steps.append({"id": sid, "label": label, "state": state})
    return steps


def client_error_message(reason: str | None) -> str:
    """Plain-language failure copy. Never forwards column names or engine internals."""
    text = (reason or "").strip()
    if not text:
        return GENERIC_FAILURE_MESSAGE
    lowered = text.lower()
    if "target selection is ambiguous" in lowered:
        return "Choose the outcome column you want DCLab to predict, then upload the file again."
    if "no label column" in lowered or "no target" in lowered:
        return "We could not find an outcome column to analyze."
    if "no usable feature" in lowered:
        return "No usable columns were left after preparing your file."
    if "no usable rows" in lowered or ("no usable" in lowered and "column" in lowered):
        return "We could not use this file."
    if "rows left after cleaning" in lowered or "need at least" in lowered:
        return "There were not enough rows left to continue."
    if (
        "not a simple tabular" in lowered
        or "no named fields" in lowered
        or "file kind" in lowered
    ):
        return "This file isn't in a format we can analyze yet."
    if lowered.startswith("unexpected error:"):
        detail = text.split(":", 1)[1].strip()
        if detail and is_clean(detail) and "dropped columns" not in detail.lower():
            return detail
        return GENERIC_FAILURE_MESSAGE
    if is_clean(text) and "dropped columns" not in lowered:
        return text
    return GENERIC_FAILURE_MESSAGE


def stage_after(current: str, milestone: str) -> bool:
    """True when `current` is strictly later than `milestone` in the pipeline."""
    if current == COMPLETED:
        return True
    try:
        return STAGE_ORDER.index(current) > STAGE_ORDER.index(milestone)
    except ValueError:
        return False
