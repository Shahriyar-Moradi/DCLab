"""Retrain on newest data: a project's retrain policy, the retrain log and its comparisons.

Revision ID: 0087_retrain_policy
Revises: 0086_sealed_evidence
Create Date: 2026-10-17

D5a (ADR 0012). Expand-only: three new tables, nothing existing changes.

- ``retrain_policies``: mutable config, one row per project (``uq_retrain_policies_project``):
  ``policy`` off | ask | auto, an optimistic ``version`` (the PUT's If-Match), the person who set it
  (automatic starts act as them, re-checked). No row = off. ``auto_paused_until``: an automatic
  start refused by a plan limit pauses the rule for that project for 24 hours (a save clears it).
- ``retrain_requests``: append-only log (``prevent_canonical_row_mutation()``): a person's start,
  the rule's question (``ask``: no run) or its automatic start, with the model in use, the newer
  labelled file and the run (``(origin = 'ask') = (experiment_id IS NULL)``). Rule rows name the
  alert week and window; ``uq_retrain_requests_rule_window`` handles one week once per model across
  workers. The window FK is workspace-scoped (``monitoring_windows`` has no project-scoped key yet;
  the service pins project and model).
- ``retrain_comparisons``: append-only, one per started retrain (``UNIQUE retrain_request_id``):
  the comparison result (bounded jsonb object) and the challenger proposal it opened, if any.

Composite tenant FKs to projects, model versions, datasets, experiments, monitoring windows and
decision records. ``created_at`` is ``clock_timestamp()``. Literals are identical in
``app/domain/retrain.py`` and ``app/db/integrity.py``.

Downgrade drops the triggers and the three tables: policies, the retrain log and the stored
comparisons are gone (the runs, proposals and the History entries of policy changes stay; a
policy-change record's ``details.retrain_policy_id`` then names nothing). Like the other
append-only logs, a future workspace purge must delete these rows through the retention guard.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0087_retrain_policy"
down_revision: Union[str, Sequence[str], None] = "0086_sealed_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK_POLICY = "policy IN ('off', 'ask', 'auto')"
_CK_VERSION = "version >= 1"
_CK_ORIGIN = "origin IN ('person', 'ask', 'auto')"
_CK_RUN = "(origin = 'ask') = (experiment_id IS NULL)"
_CK_WINDOW = ("(origin = 'person') = (window_start IS NULL) "
              "AND (origin = 'person') = (monitoring_window_id IS NULL)")
_CK_STATUS = "status IN ('compared', 'not_comparable')"
_CK_REASON = "(status = 'compared') = (reason_code IS NULL)"
_CK_REASON_CODE = (
    "reason_code IS NULL OR reason_code IN ('final_test_overlaps_training', 'no_time_column', 'time_missing', "
    "'time_kinds_differ', 'data_unavailable', 'incumbent_cannot_score', 'target_coding_differs', "
    "'challenger_not_scored', 'not_enough_labels', 'comparison_failed', 'challenger_test_not_latest', "
    "'task_differs')"
)
_CK_RESULT = "jsonb_typeof(result) = 'object' AND octet_length(CAST(result AS TEXT)) <= 16384"
_APPEND_ONLY = tuple(
    f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()"
    for table in ("retrain_requests", "retrain_comparisons")
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.text("now()")
    clock = sa.text("clock_timestamp()")

    def workspace() -> sa.Column:
        return sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)

    op.create_table(
        "retrain_policies",
        sa.Column("id", uuid, primary_key=True),
        workspace(),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("policy", sa.String(length=8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", uuid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auto_paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_retrain_policies_workspace_id"),
        sa.UniqueConstraint("workspace_id", "project_id", name="uq_retrain_policies_project"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
                                name="fk_retrain_policies_workspace_project"),
        sa.CheckConstraint(_CK_POLICY, name="ck_retrain_policies_policy"),
        sa.CheckConstraint(_CK_VERSION, name="ck_retrain_policies_version"),
    )
    op.create_index("ix_retrain_policies_updated_by_user_id", "retrain_policies", ["updated_by_user_id"])
    op.create_index("ix_retrain_policies_active", "retrain_policies", ["policy"],
                    postgresql_where=sa.text("policy <> 'off'"))
    op.create_table(
        "retrain_requests",
        sa.Column("id", uuid, primary_key=True),
        workspace(),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("incumbent_model_version_id", uuid, nullable=False),
        sa.Column("dataset_id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=True),
        sa.Column("monitoring_window_id", uuid, nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=clock, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_retrain_requests_workspace_id"),
        sa.UniqueConstraint("workspace_id", "project_id", "id", name="uq_retrain_requests_workspace_project_id"),
        sa.UniqueConstraint("experiment_id", name="uq_retrain_requests_experiment"),
        sa.UniqueConstraint("workspace_id", "project_id", "id", "experiment_id",
                            name="uq_retrain_requests_workspace_project_id_experiment"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
                                name="fk_retrain_requests_workspace_project"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "incumbent_model_version_id"],
            ["model_versions.workspace_id", "model_versions.project_id", "model_versions.id"],
            name="fk_retrain_requests_workspace_project_incumbent"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "dataset_id"], ["datasets.workspace_id", "datasets.project_id", "datasets.id"],
            name="fk_retrain_requests_workspace_project_dataset"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.project_id", "experiments.id"],
            name="fk_retrain_requests_workspace_project_experiment"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "monitoring_window_id"], ["monitoring_windows.workspace_id", "monitoring_windows.id"],
            name="fk_retrain_requests_workspace_window"),
        sa.CheckConstraint(_CK_ORIGIN, name="ck_retrain_requests_origin"),
        sa.CheckConstraint(_CK_RUN, name="ck_retrain_requests_run"),
        sa.CheckConstraint(_CK_WINDOW, name="ck_retrain_requests_window"),
    )
    op.create_index("ix_retrain_requests_project_created", "retrain_requests", ["project_id", "created_at"])
    op.create_index("uq_retrain_requests_rule_window", "retrain_requests",
                    ["project_id", "incumbent_model_version_id", "window_start"], unique=True,
                    postgresql_where=sa.text("origin <> 'person'"))
    op.create_index("ix_retrain_requests_requested_by_user_id", "retrain_requests", ["requested_by_user_id"])
    op.create_index("ix_retrain_requests_workspace_dataset", "retrain_requests", ["workspace_id", "dataset_id"])
    op.create_index("ix_retrain_requests_workspace_incumbent", "retrain_requests",
                    ["workspace_id", "incumbent_model_version_id"])
    op.create_index("ix_retrain_requests_workspace_window", "retrain_requests",
                    ["workspace_id", "monitoring_window_id"],
                    postgresql_where=sa.text("monitoring_window_id IS NOT NULL"))
    op.create_table(
        "retrain_comparisons",
        sa.Column("id", uuid, primary_key=True),
        workspace(),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("retrain_request_id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("proposal_record_id", uuid, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=clock, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_retrain_comparisons_workspace_id"),
        sa.UniqueConstraint("retrain_request_id", name="uq_retrain_comparisons_request"),
        # The comparison's run is the request's run (the 4-column key pins both).
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "retrain_request_id", "experiment_id"],
            ["retrain_requests.workspace_id", "retrain_requests.project_id", "retrain_requests.id",
             "retrain_requests.experiment_id"],
            name="fk_retrain_comparisons_workspace_project_request"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.project_id", "experiments.id"],
            name="fk_retrain_comparisons_workspace_project_experiment"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "proposal_record_id"],
            ["project_decision_records.workspace_id", "project_decision_records.project_id",
             "project_decision_records.id"],
            name="fk_retrain_comparisons_workspace_project_proposal"),
        sa.CheckConstraint(_CK_STATUS, name="ck_retrain_comparisons_status"),
        sa.CheckConstraint(_CK_REASON, name="ck_retrain_comparisons_reason"),
        sa.CheckConstraint(_CK_REASON_CODE, name="ck_retrain_comparisons_reason_code"),
        sa.CheckConstraint(_CK_RESULT, name="ck_retrain_comparisons_result"),
    )
    op.create_index("ix_retrain_comparisons_project_created", "retrain_comparisons", ["project_id", "created_at"])
    op.create_index("ix_retrain_comparisons_workspace_experiment", "retrain_comparisons",
                    ["workspace_id", "experiment_id"])
    op.create_index("ix_retrain_comparisons_workspace_proposal", "retrain_comparisons",
                    ["workspace_id", "proposal_record_id"],
                    postgresql_where=sa.text("proposal_record_id IS NOT NULL"))
    for statement in _APPEND_ONLY:
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in ("retrain_comparisons", "retrain_requests"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}"))
    op.drop_table("retrain_comparisons")
    op.drop_table("retrain_requests")
    op.drop_table("retrain_policies")
