"""Improve loops: improve_runs and improve_iterations.

Revision ID: 0088_improve_runs
Revises: 0087_retrain_policy
Create Date: 2026-10-17

P5.4-A2 (ADR 0007 option A, §9). Expand-only: two new tables, no change to existing ones.

- ``improve_runs``: one loop on one split plan (goal, budget, the pre-registered plan and its T,
  status / outcome / stop reason, the incumbent, the final run a person accepted, the one
  ``labs.improve`` continuation job allowed to step it). Three-column tenant FKs to the start,
  incumbent and final experiments, the split plan and the acceptance record; two-column ones to the
  continuation job and the creating service token. ``ck_improve_runs_acceptance``: a final run exists
  only with an accepting person and an acceptance record. One live loop per split plan (partial
  unique). ``improve_runs_guard``: goal, budget, plan and lineage frozen; acceptance write-once;
  status along the loop transitions only; a finished loop is final.
- ``improve_iterations``: one try (iteration 0 the operating point, recorded; then one sealed
  ``family_include`` branch each). Three-column FK to its run (CASCADE), its experiment and the
  incumbent it was compared with; one live try per loop (partial unique); the idempotency key is
  unique per workspace. ``improve_iterations_guard``: the planned try frozen, experiment / comparison
  / N set once, transitions only forward, a finished try final.

Literal SQL here; the live copies are app/domain/improve.py (CHECKs) and app/db/integrity.py
(functions and triggers, create_all).

Locking: new tables only (their FKs take brief SHARE ROW EXCLUSIVE locks on the referenced tables);
``lock_timeout`` 5s. Downgrade drops both tables and functions (loop history is lost; no existing
table references them).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0088_improve_runs"
down_revision: Union[str, Sequence[str], None] = "0087_retrain_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUN_STATUSES = "'queued', 'running', 'waiting_user', 'finalizing', 'completed', 'failed', 'cancelled'"
_CK_RUN_STATUS = f"status IN ({_RUN_STATUSES})"
_CK_RUN_OUTCOME = (
    "outcome IS NULL OR outcome IN ('improved', 'kept_current', 'goal_met', 'blocked', 'nothing_comparable', "
    "'failed', 'cancelled')"
)
_CK_RUN_STOP_REASON = (
    "stop_reason IS NULL OR stop_reason IN ('cancelled', 'failed', 'blocking_finding', 'goal_met', "
    "'no_improvement', 'unstable_evidence', 'comparison_bound', 'budget_iterations', 'budget_wall', "
    "'budget_compute', 'proposer_exhausted')"
)
_CK_RUN_LABEL = "sealed_set_label IN ('clean', 'carried_over', 'reported_before', 'reused')"
_CK_RUN_GOAL = (
    "jsonb_typeof(goal) = 'object' AND octet_length(goal::text) <= 4096 "
    "AND jsonb_typeof(budget) = 'object' AND jsonb_typeof(plan) = 'array'"
)
_CK_RUN_COUNTS = (
    "planned_comparisons >= 1 AND validation_folds >= 5 AND final_attempts BETWEEN 0 AND 3 "
    "AND step_errors >= 0 AND training_seconds_spent >= 0 AND llm_micros_spent >= 0 AND looks_before >= 0"
)
_CK_RUN_SHAPE = (
    "(status IN ('completed', 'failed', 'cancelled')) = (outcome IS NOT NULL AND finished_at IS NOT NULL) "
    "AND (status <> 'waiting_user' OR waiting_user_expires_at IS NOT NULL)"
)
_CK_RUN_ACCEPTANCE = (
    "(final_experiment_id IS NULL OR (accepted_by_user_id IS NOT NULL AND acceptance_record_id IS NOT NULL "
    "AND accepted_iteration_seq IS NOT NULL)) AND (status <> 'finalizing' OR final_experiment_id IS NOT NULL)"
)
_CK_IT_KIND = "kind IN ('operating_point', 'family_include')"
_CK_IT_STATUS = (
    "status IN ('planned', 'running', 'recorded', 'accepted', 'rejected', 'not_comparable', 'failed', 'cancelled')"
)
_CK_IT_AGENT = "agent IN ('off', 'agree', 'disagree', 'abstain', 'unavailable', 'late')"
_CK_IT_SHAPE = (
    "seq >= 0 AND plan_index >= 0 AND planned_new_candidates >= 0 AND jsonb_typeof(action) = 'object' "
    "AND (comparison IS NULL OR jsonb_typeof(comparison) = 'object') "
    "AND (kind = 'operating_point') = (status = 'recorded') "
    "AND (status NOT IN ('running', 'accepted', 'rejected', 'not_comparable') OR experiment_id IS NOT NULL)"
)

_GUARD_RUN = """
CREATE OR REPLACE FUNCTION guard_improve_run_update()
RETURNS trigger AS $$
BEGIN
    IF ROW(NEW.workspace_id, NEW.project_id, NEW.split_plan_id, NEW.start_experiment_id, NEW.goal, NEW.budget,
           NEW.plan, NEW.planned_comparisons, NEW.validation_folds, NEW.looks_before, NEW.sealed_set_label,
           NEW.created_by_user_id, NEW.created_by_service_token_id, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.workspace_id, OLD.project_id, OLD.split_plan_id, OLD.start_experiment_id, OLD.goal,
           OLD.budget, OLD.plan, OLD.planned_comparisons, OLD.validation_folds, OLD.looks_before,
           OLD.sealed_set_label, OLD.created_by_user_id, OLD.created_by_service_token_id, OLD.created_at) THEN
        RAISE EXCEPTION 'improve_runs: goal, budget, plan and lineage are frozen';
    END IF;
    IF (OLD.accepted_iteration_seq IS NOT NULL AND NEW.accepted_iteration_seq IS DISTINCT FROM OLD.accepted_iteration_seq)
       OR (OLD.accepted_by_user_id IS NOT NULL AND NEW.accepted_by_user_id IS DISTINCT FROM OLD.accepted_by_user_id)
       OR (OLD.acceptance_record_id IS NOT NULL AND NEW.acceptance_record_id IS DISTINCT FROM OLD.acceptance_record_id) THEN
        RAISE EXCEPTION 'improve_runs: the acceptance is write-once';
    END IF;
    IF OLD.status IN ('completed', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'improve_runs: a finished loop is final';
    END IF;
    IF OLD.sealed_set_consulted AND NOT NEW.sealed_set_consulted THEN
        RAISE EXCEPTION 'improve_runs: a consulted final test set stays consulted';
    END IF;
    IF NEW.final_attempts < OLD.final_attempts
       OR (OLD.final_experiment_id IS NOT NULL AND NEW.final_experiment_id IS NULL)
       OR (NEW.final_experiment_id IS DISTINCT FROM OLD.final_experiment_id
           AND NEW.final_attempts <> OLD.final_attempts + 1) THEN
        RAISE EXCEPTION 'improve_runs: a final run changes only with a new attempt';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status = 'queued' AND NEW.status IN ('running', 'completed', 'failed', 'cancelled'))
        OR (OLD.status = 'running' AND NEW.status IN ('waiting_user', 'completed', 'failed', 'cancelled'))
        OR (OLD.status = 'waiting_user' AND NEW.status IN ('finalizing', 'completed', 'failed', 'cancelled'))
        OR (OLD.status = 'finalizing' AND NEW.status IN ('waiting_user', 'completed', 'failed', 'cancelled'))
    ) THEN
        RAISE EXCEPTION 'improve_runs: % -> % is not a loop transition', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
_GUARD_ITERATION = """
CREATE OR REPLACE FUNCTION guard_improve_iteration_update()
RETURNS trigger AS $$
BEGIN
    IF ROW(NEW.workspace_id, NEW.project_id, NEW.improve_run_id, NEW.seq, NEW.plan_index, NEW.kind, NEW.action,
           NEW.incumbent_experiment_id, NEW.planned_new_candidates, NEW.planned_candidate_ids,
           NEW.idempotency_key, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.workspace_id, OLD.project_id, OLD.improve_run_id, OLD.seq, OLD.plan_index, OLD.kind,
           OLD.action, OLD.incumbent_experiment_id, OLD.planned_new_candidates, OLD.planned_candidate_ids,
           OLD.idempotency_key, OLD.created_at) THEN
        RAISE EXCEPTION 'improve_iterations: the planned try is frozen';
    END IF;
    IF (OLD.experiment_id IS NOT NULL AND NEW.experiment_id IS DISTINCT FROM OLD.experiment_id)
       OR (OLD.comparison IS NOT NULL AND NEW.comparison IS DISTINCT FROM OLD.comparison)
       OR (OLD.comparisons_before IS NOT NULL AND NEW.comparisons_before IS DISTINCT FROM OLD.comparisons_before) THEN
        RAISE EXCEPTION 'improve_iterations: experiment, comparison and N are set once';
    END IF;
    IF OLD.status NOT IN ('planned', 'running') THEN
        RAISE EXCEPTION 'improve_iterations: a finished try is final';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status = 'planned' AND NEW.status IN ('running', 'failed', 'cancelled'))
        OR (OLD.status = 'running' AND NEW.status IN ('accepted', 'rejected', 'not_comparable', 'failed', 'cancelled'))
    ) THEN
        RAISE EXCEPTION 'improve_iterations: % -> % is not a try transition', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
_TRIGGERS = (
    "CREATE TRIGGER improve_runs_guard BEFORE UPDATE ON improve_runs "
    "FOR EACH ROW EXECUTE FUNCTION guard_improve_run_update()",
    "CREATE TRIGGER improve_iterations_guard BEFORE UPDATE ON improve_iterations "
    "FOR EACH ROW EXECUTE FUNCTION guard_improve_iteration_update()",
)


def _experiment_fk(column: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "project_id", column],
        ["experiments.workspace_id", "experiments.project_id", "experiments.id"],
        name=name,
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()
    stamp = sa.DateTime(timezone=True)
    op.create_table(
        "improve_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("split_plan_id", uuid, nullable=False),
        sa.Column("start_experiment_id", uuid, nullable=False),
        sa.Column("incumbent_experiment_id", uuid, nullable=False),
        sa.Column("final_experiment_id", uuid, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.Column("stop_detail", sa.String(length=64), nullable=True),
        sa.Column("goal", jsonb, nullable=False),
        sa.Column("budget", jsonb, nullable=False),
        sa.Column("plan", jsonb, nullable=False),
        sa.Column("planned_comparisons", sa.Integer(), nullable=False),
        sa.Column("validation_folds", sa.Integer(), nullable=False),
        sa.Column("looks_before", sa.Integer(), nullable=False),
        sa.Column("sealed_set_label", sa.String(length=16), nullable=False),
        sa.Column("sealed_set_consulted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("continuation_job_id", uuid, nullable=True),
        sa.Column("accepted_iteration_seq", sa.Integer(), nullable=True),
        sa.Column("accepted_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acceptance_record_id", uuid, nullable=True),
        sa.Column("final_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("training_seconds_spent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("llm_micros_spent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_by_service_token_id", uuid, nullable=True),
        sa.Column("cancel_requested_at", stamp, nullable=True),
        sa.Column("waiting_user_expires_at", stamp, nullable=True),
        sa.Column("created_at", stamp, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", stamp, nullable=True),
        sa.Column("finished_at", stamp, nullable=True),
        sa.UniqueConstraint("workspace_id", "project_id", "id", name="uq_improve_runs_workspace_project_id"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
                                name="fk_improve_runs_workspace_project", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id", "split_plan_id"],
                                ["split_plans.workspace_id", "split_plans.project_id", "split_plans.id"],
                                name="fk_improve_runs_split_plan"),
        _experiment_fk("start_experiment_id", "fk_improve_runs_start_experiment"),
        _experiment_fk("incumbent_experiment_id", "fk_improve_runs_incumbent_experiment"),
        _experiment_fk("final_experiment_id", "fk_improve_runs_final_experiment"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id", "acceptance_record_id"],
                                ["project_decision_records.workspace_id", "project_decision_records.project_id",
                                 "project_decision_records.id"], name="fk_improve_runs_acceptance_record"),
        sa.ForeignKeyConstraint(["workspace_id", "continuation_job_id"], ["ml_jobs.workspace_id", "ml_jobs.id"],
                                name="fk_improve_runs_continuation_job"),
        sa.ForeignKeyConstraint(["workspace_id", "created_by_service_token_id"],
                                ["service_tokens.workspace_id", "service_tokens.id"],
                                name="fk_improve_runs_service_token"),
        sa.CheckConstraint(_CK_RUN_STATUS, name="ck_improve_runs_status"),
        sa.CheckConstraint(_CK_RUN_OUTCOME, name="ck_improve_runs_outcome"),
        sa.CheckConstraint(_CK_RUN_STOP_REASON, name="ck_improve_runs_stop_reason"),
        sa.CheckConstraint(_CK_RUN_LABEL, name="ck_improve_runs_sealed_set_label"),
        sa.CheckConstraint(_CK_RUN_GOAL, name="ck_improve_runs_goal"),
        sa.CheckConstraint(_CK_RUN_COUNTS, name="ck_improve_runs_counts"),
        sa.CheckConstraint(_CK_RUN_SHAPE, name="ck_improve_runs_shape"),
        sa.CheckConstraint(_CK_RUN_ACCEPTANCE, name="ck_improve_runs_acceptance"),
    )
    op.create_index("uq_improve_runs_live_split_plan", "improve_runs", ["split_plan_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_user', 'finalizing')"))
    op.create_index("ix_improve_runs_workspace_status", "improve_runs", ["workspace_id", "status"])
    op.create_index("ix_improve_runs_service_token", "improve_runs",
                    ["workspace_id", "created_by_service_token_id", "created_at"],
                    postgresql_where=sa.text("created_by_service_token_id IS NOT NULL"))
    op.create_index("ix_improve_runs_final_experiment", "improve_runs", ["workspace_id", "final_experiment_id"],
                    postgresql_where=sa.text("final_experiment_id IS NOT NULL"))
    op.create_table(
        "improve_iterations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("improve_run_id", uuid, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("plan_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("action", jsonb, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("experiment_id", uuid, nullable=True),
        sa.Column("incumbent_experiment_id", uuid, nullable=False),
        sa.Column("planned_new_candidates", sa.Integer(), nullable=False),
        sa.Column("planned_candidate_ids", jsonb, nullable=False),
        sa.Column("comparisons_before", sa.Integer(), nullable=True),
        sa.Column("comparison", jsonb, nullable=True),
        sa.Column("agent", sa.String(length=16), nullable=False, server_default="off"),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("training_seconds", sa.Float(), nullable=True),
        sa.Column("created_at", stamp, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", stamp, nullable=True),
        sa.UniqueConstraint("improve_run_id", "seq", name="uq_improve_iterations_run_seq"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_improve_iterations_workspace_key"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id", "improve_run_id"],
                                ["improve_runs.workspace_id", "improve_runs.project_id", "improve_runs.id"],
                                name="fk_improve_iterations_run", ondelete="CASCADE"),
        _experiment_fk("experiment_id", "fk_improve_iterations_experiment"),
        _experiment_fk("incumbent_experiment_id", "fk_improve_iterations_incumbent"),
        sa.CheckConstraint(_CK_IT_KIND, name="ck_improve_iterations_kind"),
        sa.CheckConstraint(_CK_IT_STATUS, name="ck_improve_iterations_status"),
        sa.CheckConstraint(_CK_IT_AGENT, name="ck_improve_iterations_agent"),
        sa.CheckConstraint(_CK_IT_SHAPE, name="ck_improve_iterations_shape"),
    )
    op.create_index("uq_improve_iterations_live", "improve_iterations", ["improve_run_id"], unique=True,
                    postgresql_where=sa.text("status IN ('planned', 'running')"))
    op.create_index("ix_improve_iterations_experiment", "improve_iterations", ["workspace_id", "experiment_id"])
    op.execute(sa.text(_GUARD_RUN))
    op.execute(sa.text(_GUARD_ITERATION))
    for statement in _TRIGGERS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(sa.text("DROP TRIGGER IF EXISTS improve_iterations_guard ON improve_iterations"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS improve_runs_guard ON improve_runs"))
    op.drop_table("improve_iterations")
    op.drop_table("improve_runs")
    op.execute(sa.text("DROP FUNCTION IF EXISTS guard_improve_iteration_update()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS guard_improve_run_update()"))
