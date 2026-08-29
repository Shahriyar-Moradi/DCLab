"""Fine-grained ML-run stages stored on `ClientLabUpload.pipeline_status`.

This extends the existing upload status column rather than adding a second
enum. The normal client only ever sees four values:

    queued → processing → completed
                         → failed

Detailed stages stay on the stored column for execution, logging, and admin
inspection. Client-facing `stage` / `headline` / `pipeline_status` are the
same four-state view. The client never receives a per-stage checklist.
"""

from __future__ import annotations

from typing import Any

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

# Single processing line for queued + every in-progress stored stage.
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


def headline(pipeline_status: str) -> str:
    if lifecycle_status(pipeline_status) in {"queued", "processing"}:
        return PROCESSING_HEADLINE
    return ""


def public_pipeline_status(pipeline_status: str) -> str:
    """Coarse value safe to put on `/app` (never a stored ML stage name)."""
    return lifecycle_status(pipeline_status)


def steps_for(_pipeline_status: str) -> list[dict[str, Any]]:
    """The client run page is not a stage tracker."""
    return []


def stage_after(current: str, milestone: str) -> bool:
    """True when `current` is strictly later than `milestone` in the pipeline."""
    if current == COMPLETED:
        return True
    try:
        return STAGE_ORDER.index(current) > STAGE_ORDER.index(milestone)
    except ValueError:
        return False
