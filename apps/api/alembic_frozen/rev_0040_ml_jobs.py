"""Frozen 0040 ML-job check-constraint vocabularies.

Copied from ``app.domain.ml_jobs`` at 0040. Do not import the live module.
"""

from __future__ import annotations

from alembic_frozen.clauses import sql_in_clause

ML_JOB_TYPES = ("auto_train",)

ML_JOB_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
)

CK_ML_JOB_TYPE = sql_in_clause("job_type", ML_JOB_TYPES)
CK_ML_JOB_STATUS = sql_in_clause("status", ML_JOB_STATUSES)
