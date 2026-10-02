"""Tenant keys for three evidence tables; attributable llm_invocations.

Revision ID: 0064_tenant_llm_attribution
Revises: 0063_state_graph_nodes
Create Date: 2026-10-01

P2.2-A2, ADR 0006 (Revision 1) §7 and §8 "0064". The revision id is shorter than
the file name because ``alembic_version.version_num`` is varchar(32) (precedent:
0021, 0027). Expand-only:

- ``workspace_id`` on ``workflow_run_inputs``, ``experiment_test_predictions``
  and ``ml_run_verifications``: added NULL, backfilled deterministically from the
  NOT NULL parent (``workflow_runs``, ``experiments``, ``client_lab_uploads`` via
  ``run_id``), checked (raise with per-table counts if any NULL remains), then
  always ``SET NOT NULL``.
- Cross-workspace mismatches behind every secondary composite FK are counted
  first; any mismatch raises with counts (never nulled or skipped).
- Composite FKs with the ADR's delete actions, plus workspace indexes.
- ``llm_invocations``: ``workflow_run_id`` / ``experiment_id`` become nullable;
  ``project_id`` (CFK -> projects, NO ACTION, backfilled from the experiment),
  ``agent_run_id`` (no FK until Phase 6), ``provider_kind`` (CHECK) and the
  attribution CHECK (at least one of run, experiment, project, agent run).

The ``experiment_test_predictions`` backfill disables only the named trigger
``experiment_test_predictions_evidence_locked`` around its UPDATE and asserts it
is enabled again. No try/finally: the revision is one transaction, so a failure
rolls the trigger state back with everything else.

Operational note: the UPDATE rewrites every row of the largest evidence table
and the trigger toggle, NOT NULL changes and new FKs take ACCESS EXCLUSIVE
locks. Run with the worker stopped, in a maintenance window.

Downgrade refuses once an ``llm_invocations`` row has no workflow run or no
experiment (NOT NULL could not be restored): repair forward (ADR 0004). When it
does run, it drops ``llm_invocations.project_id``, ``agent_run_id`` and
``provider_kind`` and the three ``workspace_id`` columns, so those values are
lost (``workspace_id`` and ``project_id`` are re-derivable by upgrading again;
``agent_run_id`` and ``provider_kind`` are not).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_tenant_llm_attribution"
down_revision: Union[str, Sequence[str], None] = "0063_state_graph_nodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT_TABLES = ("workflow_run_inputs", "experiment_test_predictions", "ml_run_verifications")

_BACKFILL_WORKFLOW_RUN_INPUTS_SQL = """
UPDATE workflow_run_inputs AS child
   SET workspace_id = parent.workspace_id
  FROM workflow_runs AS parent
 WHERE parent.id = child.workflow_run_id
   AND child.workspace_id IS NULL
"""

_BACKFILL_EXPERIMENT_TEST_PREDICTIONS_SQL = """
UPDATE experiment_test_predictions AS child
   SET workspace_id = parent.workspace_id
  FROM experiments AS parent
 WHERE parent.id = child.experiment_id
   AND child.workspace_id IS NULL
"""

_BACKFILL_ML_RUN_VERIFICATIONS_SQL = """
UPDATE ml_run_verifications AS child
   SET workspace_id = parent.workspace_id
  FROM client_lab_uploads AS parent
 WHERE parent.id = child.run_id
   AND child.workspace_id IS NULL
"""

_ASSERT_PREDICTION_TRIGGER_ENABLED_SQL = """
DO $$
DECLARE
    state "char";
BEGIN
    SELECT tgenabled INTO state
      FROM pg_trigger
     WHERE tgrelid = 'experiment_test_predictions'::regclass
       AND tgname = 'experiment_test_predictions_evidence_locked';
    IF state IS DISTINCT FROM 'O' THEN
        RAISE EXCEPTION
            '0064: experiment_test_predictions_evidence_locked is not enabled (tgenabled=%)',
            state;
    END IF;
END
$$
"""

_ASSERT_NO_NULL_WORKSPACE_SQL = """
DO $$
DECLARE
    inputs integer;
    predictions integer;
    verifications integer;
BEGIN
    SELECT count(*) INTO inputs FROM workflow_run_inputs WHERE workspace_id IS NULL;
    SELECT count(*) INTO predictions FROM experiment_test_predictions WHERE workspace_id IS NULL;
    SELECT count(*) INTO verifications FROM ml_run_verifications WHERE workspace_id IS NULL;
    IF inputs + predictions + verifications > 0 THEN
        RAISE EXCEPTION
            '0064: workspace_id backfill incomplete: workflow_run_inputs=%, '
            'experiment_test_predictions=%, ml_run_verifications=%',
            inputs, predictions, verifications;
    END IF;
END
$$
"""

_ASSERT_NO_CROSS_WORKSPACE_SQL = """
DO $$
DECLARE
    input_datasets integer;
    verification_experiments integer;
    verification_invocations integer;
BEGIN
    SELECT count(*) INTO input_datasets
      FROM workflow_run_inputs AS child
      JOIN datasets AS parent ON parent.id = child.dataset_id
     WHERE parent.workspace_id <> child.workspace_id;
    SELECT count(*) INTO verification_experiments
      FROM ml_run_verifications AS child
      JOIN experiments AS parent ON parent.id = child.experiment_id
     WHERE parent.workspace_id <> child.workspace_id;
    SELECT count(*) INTO verification_invocations
      FROM ml_run_verifications AS child
      JOIN llm_invocations AS parent ON parent.id = child.llm_invocation_id
     WHERE parent.workspace_id <> child.workspace_id;
    IF input_datasets + verification_experiments + verification_invocations > 0 THEN
        RAISE EXCEPTION
            '0064: cross-workspace references: workflow_run_inputs.dataset_id=%, '
            'ml_run_verifications.experiment_id=%, ml_run_verifications.llm_invocation_id=%',
            input_datasets, verification_experiments, verification_invocations;
    END IF;
END
$$
"""

_BACKFILL_LLM_PROJECT_SQL = """
UPDATE llm_invocations AS child
   SET project_id = parent.project_id
  FROM experiments AS parent
 WHERE parent.id = child.experiment_id
   AND parent.workspace_id = child.workspace_id
   AND parent.project_id IS NOT NULL
   AND child.project_id IS NULL
"""

_DOWNGRADE_PRECHECK_SQL = """
DO $$
DECLARE
    unattached integer;
BEGIN
    SELECT count(*) INTO unattached
      FROM llm_invocations
     WHERE workflow_run_id IS NULL OR experiment_id IS NULL;
    IF unattached > 0 THEN
        RAISE EXCEPTION
            '0064 downgrade refused: % llm_invocations have no workflow run or experiment; '
            'repair forward', unattached;
    END IF;
END
$$
"""

# (table, name, columns, referred table, referred columns, ON DELETE clause)
_COMPOSITE_FKS = (
    (
        "workflow_run_inputs",
        "fk_workflow_run_inputs_workspace_workflow_run",
        ("workspace_id", "workflow_run_id"),
        "workflow_runs",
        "ON DELETE CASCADE",
    ),
    (
        "workflow_run_inputs",
        "fk_workflow_run_inputs_workspace_dataset",
        ("workspace_id", "dataset_id"),
        "datasets",
        "",
    ),
    (
        "experiment_test_predictions",
        "fk_experiment_test_predictions_workspace_experiment",
        ("workspace_id", "experiment_id"),
        "experiments",
        "ON DELETE CASCADE",
    ),
    (
        "ml_run_verifications",
        "fk_ml_run_verifications_workspace_run",
        ("workspace_id", "run_id"),
        "client_lab_uploads",
        "ON DELETE CASCADE",
    ),
    (
        "ml_run_verifications",
        "fk_ml_run_verifications_workspace_experiment",
        ("workspace_id", "experiment_id"),
        "experiments",
        'ON DELETE SET NULL ("experiment_id")',
    ),
    (
        "ml_run_verifications",
        "fk_ml_run_verifications_workspace_llm_invocation",
        ("workspace_id", "llm_invocation_id"),
        "llm_invocations",
        'ON DELETE SET NULL ("llm_invocation_id")',
    ),
)


def upgrade() -> None:
    for table in _TENANT_TABLES:
        op.add_column(
            table, sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True)
        )

    op.execute(_BACKFILL_WORKFLOW_RUN_INPUTS_SQL)
    op.execute(
        "ALTER TABLE experiment_test_predictions "
        "DISABLE TRIGGER experiment_test_predictions_evidence_locked"
    )
    op.execute(_BACKFILL_EXPERIMENT_TEST_PREDICTIONS_SQL)
    op.execute(
        "ALTER TABLE experiment_test_predictions "
        "ENABLE TRIGGER experiment_test_predictions_evidence_locked"
    )
    op.execute(_ASSERT_PREDICTION_TRIGGER_ENABLED_SQL)
    op.execute(_BACKFILL_ML_RUN_VERIFICATIONS_SQL)
    op.execute(_ASSERT_NO_NULL_WORKSPACE_SQL)

    for table in _TENANT_TABLES:
        op.alter_column(table, "workspace_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_workspace_id", table, "workspaces", ["workspace_id"], ["id"]
        )
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])

    op.execute(_ASSERT_NO_CROSS_WORKSPACE_SQL)
    for table, name, columns, referred, on_delete in _COMPOSITE_FKS:
        local = ", ".join(f'"{column}"' for column in columns)
        op.execute(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ({local}) REFERENCES "{referred}" ("workspace_id", "id") {on_delete}'
        )

    op.alter_column("llm_invocations", "workflow_run_id", nullable=True)
    op.alter_column("llm_invocations", "experiment_id", nullable=True)
    op.add_column(
        "llm_invocations", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "llm_invocations", sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("llm_invocations", sa.Column("provider_kind", sa.String(32), nullable=True))
    op.execute(_BACKFILL_LLM_PROJECT_SQL)
    op.create_foreign_key(
        "fk_llm_invocations_workspace_project",
        "llm_invocations",
        "projects",
        ["workspace_id", "project_id"],
        ["workspace_id", "id"],
    )
    op.create_index("ix_llm_invocations_project_id", "llm_invocations", ["project_id"])
    op.create_check_constraint(
        "ck_llm_invocations_provider_kind",
        "llm_invocations",
        "provider_kind IS NULL OR provider_kind IN ('llm_provider', 'semantic_decision', "
        "'agent_runtime', 'deterministic_fallback')",
    )
    op.create_check_constraint(
        "ck_llm_invocations_attributed",
        "llm_invocations",
        "num_nonnulls(workflow_run_id, experiment_id, project_id, agent_run_id) >= 1",
    )


def downgrade() -> None:
    op.execute(_DOWNGRADE_PRECHECK_SQL)

    op.drop_constraint("ck_llm_invocations_attributed", "llm_invocations", type_="check")
    op.drop_constraint("ck_llm_invocations_provider_kind", "llm_invocations", type_="check")
    op.drop_index("ix_llm_invocations_project_id", table_name="llm_invocations")
    op.drop_constraint(
        "fk_llm_invocations_workspace_project", "llm_invocations", type_="foreignkey"
    )
    for column in ("provider_kind", "agent_run_id", "project_id"):
        op.drop_column("llm_invocations", column)
    op.alter_column("llm_invocations", "experiment_id", nullable=False)
    op.alter_column("llm_invocations", "workflow_run_id", nullable=False)

    for table, name, _columns, _referred, _on_delete in reversed(_COMPOSITE_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
    for table in reversed(_TENANT_TABLES):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_constraint(f"fk_{table}_workspace_id", table, type_="foreignkey")
        op.drop_column(table, "workspace_id")
