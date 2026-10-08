"""Stored R3 evaluation runs: the only promotion evidence a platform level may cite.

Revision ID: 0077_r3_runs
Revises: 0076_assistant_turn_lock
Create Date: 2026-10-06

P6.8-A (ADR 0008 §3, §4). ``dclab r3 run`` persists every run server-side when a database is
in reach (run id, candidate, (release, model) pair, content and run digests, the digest-covered
report); ``propose_promotion`` loads the cited run by id and re-runs the rule engine on the
stored content, so a caller can no longer hand the policy layer an arbitrary report, and the
"previous" run of the stability check is the stored run immediately before it for the same
pair. Platform rows (no workspace column, never tenant data); append-only through the existing
``prevent_canonical_row_mutation()`` trigger function. Additive: one new table, no change to
any existing object. The report body (≤ 512 KB, ~60 KB today, ~50 rows a year) stays in the row on
purpose — a deliberate, bounded exception to "large bodies go to object storage": the row IS the
evidence and the append-only trigger guards it better than an object key; crossing the cap means
moving the body to object storage behind ``content_digest``. The stored content holds tenant-derived
aggregates only (small buckets suppressed), never tenant rows. Downgrade drops the trigger, index
and table: stored R3 evidence is gone for good, level rows citing a run id then point at nothing,
and the forward repair is a new run.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_r3_runs"
down_revision: Union[str, Sequence[str], None] = "0076_assistant_turn_lock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Identical literals in app/domain/ai_governance.py.
_REPORT_MAX_BYTES = 512 * 1024
_CK_CANDIDATE = "char_length(btrim(candidate)) > 0"
_CK_DIGESTS = "content_digest ~ '^[0-9a-f]{64}$' AND run_digest ~ '^[0-9a-f]{64}$'"
_CK_REPORT = f"jsonb_typeof(report) = 'object' AND octet_length(report::text) <= {_REPORT_MAX_BYTES}"
_CK_LIVE = "live = (left(candidate, 5) = 'live:')"
_CK_CREATED_AT = "created_at <= recorded_at + interval '5 minutes'"


def upgrade() -> None:
    op.create_table(
        "r3_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate", sa.String(length=128), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("pair_release", sa.String(length=512), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("run_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_CK_CANDIDATE, name="ck_r3_runs_candidate"),
        sa.CheckConstraint(_CK_DIGESTS, name="ck_r3_runs_digests"),
        sa.CheckConstraint(_CK_REPORT, name="ck_r3_runs_report"),
        sa.CheckConstraint(_CK_LIVE, name="ck_r3_runs_live"),
        sa.CheckConstraint(_CK_CREATED_AT, name="ck_r3_runs_created_at"),
    )
    op.create_index("ix_r3_runs_pair_recorded_at", "r3_runs",
                    ["pair_release", "model_id", sa.text("recorded_at DESC")])
    op.execute(sa.text(
        "CREATE TRIGGER r3_runs_append_only BEFORE UPDATE OR DELETE ON r3_runs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS r3_runs_append_only ON r3_runs"))
    op.drop_index("ix_r3_runs_pair_recorded_at", table_name="r3_runs")
    op.drop_table("r3_runs")
