"""Frozen 0053 PipelineRun branch-lineage check constraints.

Copied from ``app.domain.pipeline_run_branch`` at 0053. Do not import the live
module.
"""

from __future__ import annotations

BRANCH_REASON_MAX_CHARS = 512

CK_EXPERIMENTS_PARENT_NOT_SELF = (
    "parent_pipeline_run_id IS NULL OR parent_pipeline_run_id <> id"
)
CK_EXPERIMENTS_BRANCH_KEY = (
    "branch_key IS NULL OR branch_key ~ '^[a-z][a-z0-9_]{0,63}$'"
)
CK_EXPERIMENTS_BRANCH_REASON = (
    "branch_reason IS NULL OR char_length(branch_reason) BETWEEN 1 AND "
    f"{BRANCH_REASON_MAX_CHARS}"
)
CK_EXPERIMENTS_BRANCH_REQUIRES_PARENT = (
    "parent_pipeline_run_id IS NOT NULL "
    "OR (branch_key IS NULL AND branch_reason IS NULL)"
)
