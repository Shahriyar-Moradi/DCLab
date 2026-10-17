"""Sealed evidence mode: CV-only improve tries that never score the final test set.

Revision ID: 0086_sealed_evidence
Revises: 0085_upload_attempts
Create Date: 2026-10-16

P5.4-A1a (ADR 0007 §6.1, option A chosen 2026-10-16). Expand-only.

- ``experiments.evidence_mode varchar(8) NOT NULL DEFAULT 'scored'`` with
  ``ck_experiments_evidence_mode`` (scored | sealed). Every existing run is ``scored``.
- ``pipeline_run_scientific_evidence_complete``: ``CASE`` on the run's mode. The scored
  branch is the 0043 predicate byte-for-byte; the sealed branch is the same text with exactly
  four holdout clauses (ModelVersion links, final_holdout evaluation, test predictions,
  role=model artifact) swapped for ``NOT EXISTS`` of each.
- ``evidence_mode`` is insert-only: ``prevent_experiment_lineage_violation`` refuses a change
  and ``experiments_lineage_guard`` is recreated with ``evidence_mode`` in its ``UPDATE OF``.
- ``model_versions_refuse_sealed_run``: a ModelVersion of a sealed run is refused at INSERT.

Literal SQL lives here (the scored predicate comes from the frozen 0043 module); the live
copies are in app/db/evidence_lock.py and app/db/integrity.py (create_all).

Locking: ADD COLUMN with a constant default is metadata-only; the CHECK scans experiments
under ACCESS EXCLUSIVE (small table); ``lock_timeout`` 5s.

Downgrade: already-locked sealed rows would look like scored runs with no holdout, and the
ModelVersion refusal would disappear, so the downgrade refuses while sealed rows exist unless
the session sets ``dclab.allow_sealed_downgrade = 'on'`` (e.g. ``PGOPTIONS``).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from alembic_frozen.rev_0043_evidence_lock import EVIDENCE_COMPLETE_SQL as SCORED_0043_SQL

revision: str = "0086_sealed_evidence"
down_revision: Union[str, Sequence[str], None] = "0085_upload_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HEADER = """
CREATE OR REPLACE FUNCTION pipeline_run_scientific_evidence_complete(wanted_run uuid)
RETURNS boolean AS $$
"""
_FOOTER = """;
$$ LANGUAGE sql STABLE
"""
if not (SCORED_0043_SQL.startswith(_HEADER) and SCORED_0043_SQL.endswith(_FOOTER)):
    raise RuntimeError("the frozen 0043 predicate changed shape")
_SCORED = SCORED_0043_SQL[len(_HEADER):-len(_FOOTER)]

_SWAPS: tuple[tuple[str, str], ...] = (
    (
        """          AND (
              (
                  run.workflow_run_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM model_versions AS unexpected_version
                      WHERE unexpected_version.pipeline_run_id = run.id
                  )
              )
              OR EXISTS (
                  SELECT 1
                  FROM model_versions AS version_row
                  WHERE version_row.pipeline_run_id = run.id
                    AND version_row.selected_candidate_id = winner.id
                    AND version_row.dataset_id = run.dataset_id
                    AND version_row.feature_set_version_id = winner.feature_set_version_id
                    AND version_row.runtime_environment_id IS NOT NULL
                    AND version_row.model_artifact_id IS NOT NULL
                    AND version_row.feature_manifest_artifact_id IS NOT NULL
              )
          )
""",
        """          AND NOT EXISTS (
              SELECT 1
              FROM model_versions AS unexpected_version
              WHERE unexpected_version.pipeline_run_id = run.id
          )
""",
    ),
    (
        """          AND EXISTS (
              SELECT 1
              FROM model_evaluations AS holdout
              WHERE holdout.candidate_id = winner.id
                AND holdout.evaluation_scope = 'final_holdout'
                AND holdout.status = 'completed'
                AND EXISTS (
                    SELECT 1
                    FROM evaluation_metrics AS metric
                    WHERE metric.model_evaluation_id = holdout.id
                )
                AND (
                    (
                        run.workflow_run_id IS NULL
                        AND holdout.model_version_id IS NULL
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM model_versions AS version_row
                        WHERE version_row.pipeline_run_id = run.id
                          AND version_row.selected_candidate_id = winner.id
                          AND holdout.model_version_id = version_row.id
                    )
                )
          )
""",
        """          AND NOT EXISTS (
              SELECT 1
              FROM model_evaluations AS holdout
              JOIN experiment_candidates AS holdout_candidate
                ON holdout_candidate.id = holdout.candidate_id
              WHERE holdout_candidate.experiment_id = run.id
                AND holdout.evaluation_scope = 'final_holdout'
          )
""",
    ),
    (
        """          AND EXISTS (
              SELECT 1
              FROM experiment_test_predictions AS prediction
""",
        """          AND NOT EXISTS (
              SELECT 1
              FROM experiment_test_predictions AS prediction
""",
    ),
    (
        """          AND EXISTS (
              SELECT 1
              FROM artifacts AS artifact
              WHERE artifact.pipeline_run_id = run.id
                AND artifact.metadata->>'role' = 'model'
""",
        """          AND NOT EXISTS (
              SELECT 1
              FROM artifacts AS artifact
              WHERE artifact.pipeline_run_id = run.id
                AND artifact.metadata->>'role' = 'model'
""",
    ),
)

_SEALED = _SCORED
for _clause, _replacement in _SWAPS:
    if _SEALED.count(_clause) != 1:
        raise RuntimeError("the frozen 0043 predicate must contain each clause once")
    _SEALED = _SEALED.replace(_clause, _replacement)

_PREDICATE_SQL = f"""
CREATE OR REPLACE FUNCTION pipeline_run_scientific_evidence_complete(wanted_run uuid)
RETURNS boolean AS $$
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM experiments AS sealed_run
        WHERE sealed_run.id = wanted_run AND sealed_run.evidence_mode = 'sealed'
    ) THEN (
{_SEALED}
    ) ELSE (
{_SCORED}
    ) END;
$$ LANGUAGE sql STABLE
"""

# The 0063 lineage guard (restored on downgrade); the 0086 one adds the evidence_mode refusal.
_LINEAGE_0063_SQL = """
CREATE OR REPLACE FUNCTION prevent_experiment_lineage_violation()
RETURNS trigger AS $$
DECLARE
    parent_row record;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.parent_pipeline_run_id IS DISTINCT FROM OLD.parent_pipeline_run_id
            OR NEW.change_set IS DISTINCT FROM OLD.change_set
            OR NEW.intent IS DISTINCT FROM OLD.intent THEN
            RAISE EXCEPTION
                'experiments.parent_pipeline_run_id, change_set and intent are insert-only';
        END IF;
        IF OLD.source_dataset_id IS NOT NULL
            AND NEW.source_dataset_id IS DISTINCT FROM OLD.source_dataset_id THEN
            RAISE EXCEPTION 'experiments.source_dataset_id is write-once';
        END IF;
        IF NEW.split_plan_id IS DISTINCT FROM OLD.split_plan_id THEN
            IF OLD.split_plan_id IS NOT NULL THEN
                RAISE EXCEPTION 'experiments.split_plan_id is write-once';
            END IF;
            IF OLD.scientific_evidence_locked_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'experiments.split_plan_id cannot be set after the run is locked';
            END IF;
        END IF;
    END IF;
    IF NEW.change_set IS NOT NULL THEN
        SELECT workspace_id, project_id, source_dataset_id, split_plan_id
          INTO parent_row
          FROM experiments
         WHERE id = NEW.parent_pipeline_run_id;
        IF NOT FOUND OR parent_row.workspace_id IS DISTINCT FROM NEW.workspace_id THEN
            RAISE EXCEPTION 'branch parent must exist in the same workspace';
        END IF;
        IF NEW.project_id IS NULL
            OR NEW.project_id IS DISTINCT FROM parent_row.project_id THEN
            RAISE EXCEPTION 'branch must share project_id with its parent';
        END IF;
        IF NEW.source_dataset_id IS NULL
            OR NEW.source_dataset_id IS DISTINCT FROM parent_row.source_dataset_id THEN
            RAISE EXCEPTION 'branch must share source_dataset_id with its parent';
        END IF;
        IF parent_row.split_plan_id IS NULL
            OR NEW.split_plan_id IS DISTINCT FROM parent_row.split_plan_id THEN
            RAISE EXCEPTION 'branch must share split_plan_id with its parent';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
_ANCHOR = "        IF OLD.source_dataset_id IS NOT NULL\n"
if _LINEAGE_0063_SQL.count(_ANCHOR) != 1:
    raise RuntimeError("the 0063 lineage guard text changed")
_LINEAGE_SQL = _LINEAGE_0063_SQL.replace(
    _ANCHOR,
    "        IF NEW.evidence_mode IS DISTINCT FROM OLD.evidence_mode THEN\n"
    "            RAISE EXCEPTION 'experiments.evidence_mode is insert-only';\n"
    "        END IF;\n" + _ANCHOR,
)

_LINEAGE_TRIGGER_0063_SQL = """
CREATE TRIGGER experiments_lineage_guard
BEFORE INSERT OR UPDATE OF split_plan_id, parent_pipeline_run_id, change_set, intent,
    source_dataset_id, project_id, workspace_id ON experiments
FOR EACH ROW EXECUTE FUNCTION prevent_experiment_lineage_violation()
"""
_LINEAGE_TRIGGER_SQL = _LINEAGE_TRIGGER_0063_SQL.replace(
    "project_id, workspace_id ON", "project_id, workspace_id, evidence_mode ON"
)

_MODEL_VERSION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION prevent_sealed_run_model_version()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM experiments AS run
        WHERE run.id = NEW.pipeline_run_id AND run.evidence_mode = 'sealed'
    ) THEN
        RAISE EXCEPTION 'model_versions cannot reference a sealed pipeline run (improve try)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
_MODEL_VERSION_TRIGGER_SQL = (
    "CREATE TRIGGER model_versions_refuse_sealed_run BEFORE INSERT ON model_versions "
    "FOR EACH ROW EXECUTE FUNCTION prevent_sealed_run_model_version()"
)

_DOWNGRADE_PRECHECK_SQL = """
DO $$
DECLARE
    sealed_rows integer;
BEGIN
    SELECT count(*) INTO sealed_rows FROM experiments WHERE evidence_mode = 'sealed';
    IF sealed_rows > 0
        AND coalesce(current_setting('dclab.allow_sealed_downgrade', true), '') <> 'on' THEN
        RAISE EXCEPTION
            'refusing to downgrade 0086: % sealed experiments would look like scored runs '
            'without a holdout; set dclab.allow_sealed_downgrade = on to force', sealed_rows;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "experiments",
        sa.Column("evidence_mode", sa.String(length=8), nullable=False, server_default="scored"),
    )
    op.create_check_constraint(
        "ck_experiments_evidence_mode", "experiments", "evidence_mode IN ('scored', 'sealed')"
    )
    op.execute(sa.text(_PREDICATE_SQL))
    op.execute(sa.text(_LINEAGE_SQL))
    op.execute("DROP TRIGGER IF EXISTS experiments_lineage_guard ON experiments")
    op.execute(sa.text(_LINEAGE_TRIGGER_SQL))
    op.execute(sa.text(_MODEL_VERSION_GUARD_SQL))
    op.execute(sa.text(_MODEL_VERSION_TRIGGER_SQL))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(sa.text(_DOWNGRADE_PRECHECK_SQL))
    op.execute("DROP TRIGGER IF EXISTS model_versions_refuse_sealed_run ON model_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_sealed_run_model_version()")
    op.execute("DROP TRIGGER IF EXISTS experiments_lineage_guard ON experiments")
    op.execute(sa.text(_LINEAGE_0063_SQL))
    op.execute(sa.text(_LINEAGE_TRIGGER_0063_SQL))
    op.execute(sa.text(SCORED_0043_SQL))
    op.drop_constraint("ck_experiments_evidence_mode", "experiments", type_="check")
    op.drop_column("experiments", "evidence_mode")
