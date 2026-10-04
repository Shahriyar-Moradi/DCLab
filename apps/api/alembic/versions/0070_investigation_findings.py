"""Trust-check finding types on data_quality_findings.

Revision ID: 0070_investigation_findings
Revises: 0069_batch_predictions
Create Date: 2026-10-04

P4.10-A (five core trust checks). Expand-only: ``ck_data_quality_findings_type_valid``
widens to a superset adding ``overfit_gap``, ``class_imbalance`` and
``implausible_score`` (duplicates and target_leakage already exist). The trust checks
reuse the existing findings owner instead of a new table; their rows carry
``evidence.source = 'investigate'``. The CHECK is re-added ``NOT VALID`` then
validated (no existing row can violate a superset).

Locking: one transaction; the swap holds ``data_quality_findings``' ACCESS EXCLUSIVE
lock until commit (a small table at MVP); ``lock_timeout`` 5s fails fast instead of
queueing behind workers.

Downgrade refuses while any row uses a 0070 type: those rows are locked scientific
evidence (immutable) with no pre-0070 representation. Repair forward.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0070_investigation_findings"
down_revision: Union[str, Sequence[str], None] = "0069_batch_predictions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_data_quality_findings_type_valid"
_OLD_TYPES = (
    "missing_values",
    "duplicates",
    "outlier",
    "high_cardinality",
    "constant",
    "schema_problem",
    "target_leakage",
    "prediction_time_leakage",
)
_ADDED_TYPES = ("overfit_gap", "class_imbalance", "implausible_score")


def _check(types: tuple[str, ...]) -> str:
    return "finding_type IN (" + ", ".join(f"'{value}'" for value in types) + ")"


def _swap(types: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE data_quality_findings DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(f"ALTER TABLE data_quality_findings ADD CONSTRAINT {_CONSTRAINT} CHECK ({_check(types)}) NOT VALID")
    op.execute(f"ALTER TABLE data_quality_findings VALIDATE CONSTRAINT {_CONSTRAINT}")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    _swap(_OLD_TYPES + _ADDED_TYPES)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    added = ", ".join(f"'{value}'" for value in _ADDED_TYPES)
    op.execute(
        f"""
        LOCK TABLE data_quality_findings IN SHARE MODE;
        DO $$
        DECLARE
            referencing bigint;
        BEGIN
            SELECT count(*) INTO referencing FROM data_quality_findings WHERE finding_type IN ({added});
            IF referencing > 0 THEN
                RAISE EXCEPTION '0070 downgrade refused: % trust-check findings use 0070 types; repair forward',
                    referencing;
            END IF;
        END
        $$
        """
    )
    _swap(_OLD_TYPES)
