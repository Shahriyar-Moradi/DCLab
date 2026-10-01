"""ML state graph nodes: split plans, project refs, decision records, lineage columns.

Revision ID: 0063_state_graph_nodes
Revises: 0062_run_artifact_types
Create Date: 2026-10-01

P2.2-A1, ADR 0006 (Revision 1) §2-§5, §7, §8. Expand-only:

- ``UNIQUE(workspace_id, project_id, id)`` on the six graph-node tables so every
  graph pointer can be a three-column FK (same workspace *and* project).
- ``split_plans`` (immutable), ``project_decision_records`` (append-only) and
  ``project_refs`` (the only mutable pointers; identity frozen, never deleted).
- ``experiments.source_dataset_id`` / ``split_plan_id`` / ``intent`` /
  ``change_set`` with the ``experiments_lineage_guard`` trigger.
- ``split_assignment`` artifact type; partial index for the graph loader.
- Data: ``experiments.source_dataset_id`` backfilled only from the 1:1 upload
  link (``client_lab_uploads.experiment_id`` -> ``dataset_id``); everything else
  stays NULL. No split plans, refs or decision records are synthesized.

All DDL is inlined literally (revisions never import ``app.*``); the create_all
helpers in ``app.db.integrity`` carry the identical trigger SQL.

Operational note: the six ``ADD CONSTRAINT ... UNIQUE`` statements take ACCESS
EXCLUSIVE locks while building their indexes, and the new validated CHECKs scan
``experiments``. Run with the worker stopped, in a maintenance window.

Downgrade refuses while any state-graph data exists (split plans, split
assignment artifacts, refs, decision records, or experiment lineage values
other than the backfilled source dataset); repair forward instead.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063_state_graph_nodes"
down_revision: Union[str, Sequence[str], None] = "0062_run_artifact_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROJECT_SCOPED_TABLES = (
    "problem_specs",
    "datasets",
    "feature_set_versions",
    "model_versions",
    "experiments",
    "experiment_candidates",
)

_OLD_ARTIFACT_TYPES = (
    "dataset",
    "source_code",
    "training_script",
    "model",
    "preprocessor",
    "report",
    "plot",
    "result_json",
    "feature_manifest",
    "dependency_lock",
    "reproduction_notebook",
    "reproduction_script",
    "predictions",
    "derived_dataset",
)
_NEW_ARTIFACT_TYPES = _OLD_ARTIFACT_TYPES + ("split_assignment",)

_FORBIDDEN_KEYS = (
    "ARRAY['password', 'secret', 'token', 'api_key', 'access_key', 'credentials', "
    "'authorization', 'rows', 'records', 'dataset_rows', 'csv', 'file_bytes', "
    "'contents']::text[]"
)


def _artifact_clause(values: tuple[str, ...]) -> str:
    inner = ", ".join(f"'{value}'" for value in values)
    return f"artifact_type IN ({inner})"


def _no_secrets(column: str) -> str:
    return f"NOT jsonb_exists_any({column}, {_FORBIDDEN_KEYS})"


def _uuid(name: str, *, nullable: bool) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _now(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _three_column_fk(column: str, table: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "project_id", column],
        [f"{table}.workspace_id", f"{table}.project_id", f"{table}.id"],
        name=name,
    )


PREVENT_EXPERIMENT_LINEAGE_VIOLATION_SQL = """
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

EXPERIMENTS_LINEAGE_GUARD_TRIGGER_SQL = """
CREATE TRIGGER experiments_lineage_guard
BEFORE INSERT OR UPDATE OF split_plan_id, parent_pipeline_run_id, change_set, intent,
    source_dataset_id, project_id, workspace_id ON experiments
FOR EACH ROW EXECUTE FUNCTION prevent_experiment_lineage_violation()
"""

_TRIGGERS = (
    "CREATE TRIGGER split_plans_immutable BEFORE UPDATE OR DELETE ON split_plans "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
    "CREATE TRIGGER project_decision_records_append_only "
    "BEFORE UPDATE OR DELETE ON project_decision_records "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
    "CREATE TRIGGER project_refs_columns_immutable BEFORE UPDATE ON project_refs "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_column_mutation("
    "'id,workspace_id,project_id,ref_kind,created_at')",
    "CREATE TRIGGER project_refs_no_delete BEFORE DELETE ON project_refs "
    "FOR EACH ROW EXECUTE FUNCTION prevent_canonical_row_mutation()",
)

_SOURCE_DATASET_WORKSPACE_PRECHECK_SQL = """
DO $$
DECLARE
    mismatches integer;
BEGIN
    SELECT count(*) INTO mismatches
      FROM client_lab_uploads AS u
      JOIN experiments AS e ON e.id = u.experiment_id
     WHERE u.dataset_id IS NOT NULL
       AND e.workspace_id IS DISTINCT FROM u.workspace_id;
    IF mismatches > 0 THEN
        RAISE EXCEPTION
            '0063: % client_lab_uploads link an experiment in another workspace', mismatches;
    END IF;
END
$$
"""

# One distinct upload dataset per experiment, same workspace, and no project
# conflict between dataset and experiment; otherwise NULL.
_SOURCE_DATASET_BACKFILL_SQL = """
UPDATE experiments AS e
   SET source_dataset_id = links.dataset_id
  FROM (
        SELECT u.workspace_id,
               u.experiment_id,
               (array_agg(DISTINCT u.dataset_id))[1] AS dataset_id
          FROM client_lab_uploads AS u
         WHERE u.experiment_id IS NOT NULL
           AND u.dataset_id IS NOT NULL
         GROUP BY u.workspace_id, u.experiment_id
        HAVING count(DISTINCT u.dataset_id) = 1
       ) AS links
  JOIN datasets AS d ON d.id = links.dataset_id
 WHERE e.id = links.experiment_id
   AND e.workspace_id = links.workspace_id
   AND d.workspace_id = links.workspace_id
   AND (d.project_id IS NULL OR e.project_id IS NULL OR d.project_id = e.project_id)
   AND e.source_dataset_id IS NULL
"""

_DOWNGRADE_PRECHECK_SQL = """
DO $$
DECLARE
    plans integer;
    assignments integer;
    refs integer;
    records integer;
    lineage integer;
BEGIN
    SELECT count(*) INTO plans FROM split_plans;
    SELECT count(*) INTO assignments FROM artifacts WHERE artifact_type = 'split_assignment';
    SELECT count(*) INTO refs FROM project_refs;
    SELECT count(*) INTO records FROM project_decision_records;
    SELECT count(*) INTO lineage FROM experiments
     WHERE split_plan_id IS NOT NULL OR intent IS NOT NULL OR change_set IS NOT NULL;
    IF plans + assignments + refs + records + lineage > 0 THEN
        RAISE EXCEPTION
            '0063 downgrade refused: % split_plans, % split_assignment artifacts, '
            '% project_refs, % project_decision_records, % experiments with lineage; '
            'repair forward', plans, assignments, refs, records, lineage;
    END IF;
END
$$
"""


def upgrade() -> None:
    for table in _PROJECT_SCOPED_TABLES:
        op.create_unique_constraint(
            f"uq_{table}_workspace_project_id", table, ["workspace_id", "project_id", "id"]
        )

    op.drop_constraint("ck_artifacts_type_valid", "artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifacts_type_valid", "artifacts", _artifact_clause(_NEW_ARTIFACT_TYPES)
    )

    op.create_table(
        "split_plans",
        _uuid("id", nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _uuid("project_id", nullable=False),
        _uuid("dataset_id", nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("target_column", sa.String(256), nullable=False),
        sa.Column("holdout_strategy", sa.String(64), nullable=False),
        sa.Column("holdout_test_size", sa.Float(), nullable=False),
        sa.Column("holdout_seed", sa.Integer(), nullable=False),
        sa.Column("stratified", sa.Boolean(), nullable=False),
        sa.Column("group_column", sa.String(256), nullable=True),
        sa.Column("time_column", sa.String(256), nullable=True),
        sa.Column("validation_strategy", sa.String(64), nullable=False),
        sa.Column("validation_folds", sa.Integer(), nullable=False),
        sa.Column("validation_seed", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("train_row_count", sa.Integer(), nullable=False),
        sa.Column("holdout_row_count", sa.Integer(), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        _uuid("assignment_artifact_id", nullable=False),
        sa.Column("assignment_digest", sa.String(64), nullable=False),
        sa.Column("holdout_plan_digest", sa.String(64), nullable=False),
        sa.Column("holdout_planner_version", sa.String(64), nullable=False),
        sa.Column("validation_planner_version", sa.String(64), nullable=False),
        sa.Column("plan_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(2048), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        _now("created_at"),
        _now("locked_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_split_plans_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name="uq_split_plans_workspace_project_id"
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_split_plans_project_version"),
        sa.UniqueConstraint(
            "project_id", "plan_digest", name="uq_split_plans_project_plan_digest"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_split_plans_workspace_project",
            ondelete="CASCADE",
        ),
        _three_column_fk("dataset_id", "datasets", "fk_split_plans_workspace_project_dataset"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "assignment_artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_split_plans_workspace_assignment_artifact",
        ),
        sa.CheckConstraint("version >= 1", name="ck_split_plans_version"),
        sa.CheckConstraint(
            "holdout_strategy IN ('stratified_random', 'random', 'group_disjoint', "
            "'temporal_future')",
            name="ck_split_plans_holdout_strategy",
        ),
        sa.CheckConstraint(
            "holdout_test_size > 0 AND holdout_test_size < 1",
            name="ck_split_plans_holdout_test_size",
        ),
        sa.CheckConstraint("validation_folds >= 2", name="ck_split_plans_validation_folds"),
        sa.CheckConstraint(
            "train_row_count >= 0 AND holdout_row_count >= 0 "
            "AND train_row_count + holdout_row_count = row_count",
            name="ck_split_plans_row_counts",
        ),
        sa.CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$'", name="ck_split_plans_plan_digest"
        ),
        sa.CheckConstraint(
            "assignment_digest ~ '^[0-9a-f]{64}$'", name="ck_split_plans_assignment_digest"
        ),
        sa.CheckConstraint(
            "holdout_plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_split_plans_holdout_plan_digest",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(plan_evidence) = 'object'",
            name="ck_split_plans_plan_evidence_object",
        ),
        sa.CheckConstraint(
            "octet_length(CAST(plan_evidence AS TEXT)) <= 32768",
            name="ck_split_plans_plan_evidence_bounded",
        ),
        sa.CheckConstraint(
            _no_secrets("plan_evidence"), name="ck_split_plans_plan_evidence_no_secrets"
        ),
    )
    op.create_index(
        "ix_split_plans_project_created_at",
        "split_plans",
        ["project_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_split_plans_dataset_id", "split_plans", ["dataset_id"])
    op.create_index(
        "ix_split_plans_assignment_artifact_id", "split_plans", ["assignment_artifact_id"]
    )
    op.create_index("ix_split_plans_created_by", "split_plans", ["created_by"])

    op.create_table(
        "project_decision_records",
        _uuid("id", nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _uuid("project_id", nullable=False),
        sa.Column("decision_type", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        _uuid("problem_spec_id", nullable=True),
        _uuid("dataset_id", nullable=True),
        _uuid("split_plan_id", nullable=True),
        _uuid("feature_set_version_id", nullable=True),
        _uuid("experiment_id", nullable=True),
        _uuid("candidate_id", nullable=True),
        _uuid("model_version_id", nullable=True),
        sa.Column("subject_digest", sa.String(64), nullable=True),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("actor_rule", sa.String(128), nullable=True),
        _uuid("actor_agent_run_id", nullable=True),
        _uuid("actor_service_token_id", nullable=True),
        sa.Column("rationale", sa.String(4000), nullable=False),
        sa.Column("rationale_untrusted", sa.Boolean(), nullable=False),
        sa.Column(
            "facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        _uuid("supersedes_id", nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        _now("event_at"),
        _now("recorded_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_pdr_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name="uq_pdr_workspace_project_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pdr_workspace_project",
            ondelete="CASCADE",
        ),
        _three_column_fk("problem_spec_id", "problem_specs", "fk_pdr_workspace_project_problem_spec"),
        _three_column_fk("dataset_id", "datasets", "fk_pdr_workspace_project_dataset"),
        _three_column_fk("split_plan_id", "split_plans", "fk_pdr_workspace_project_split_plan"),
        _three_column_fk(
            "feature_set_version_id",
            "feature_set_versions",
            "fk_pdr_workspace_project_feature_set_version",
        ),
        _three_column_fk("experiment_id", "experiments", "fk_pdr_workspace_project_experiment"),
        _three_column_fk(
            "candidate_id", "experiment_candidates", "fk_pdr_workspace_project_candidate"
        ),
        _three_column_fk(
            "model_version_id", "model_versions", "fk_pdr_workspace_project_model_version"
        ),
        _three_column_fk(
            "supersedes_id", "project_decision_records", "fk_pdr_workspace_project_supersedes"
        ),
        sa.CheckConstraint(
            "decision_type ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_pdr_decision_type"
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'accepted', 'rejected')", name="ck_pdr_state"
        ),
        sa.CheckConstraint(
            "subject_kind IN ('project', 'problem_spec', 'dataset_version', 'split_plan', "
            "'feature_recipe', 'experiment', 'candidate', 'model_version')",
            name="ck_pdr_subject_kind",
        ),
        sa.CheckConstraint(
            "(subject_kind = 'project' AND num_nonnulls(problem_spec_id, dataset_id, "
            "split_plan_id, feature_set_version_id, experiment_id, candidate_id, "
            "model_version_id) = 0) OR (num_nonnulls(problem_spec_id, dataset_id, "
            "split_plan_id, feature_set_version_id, experiment_id, candidate_id, "
            "model_version_id) = 1 AND ((subject_kind = 'problem_spec' AND problem_spec_id "
            "IS NOT NULL) OR (subject_kind = 'dataset_version' AND dataset_id IS NOT NULL) "
            "OR (subject_kind = 'split_plan' AND split_plan_id IS NOT NULL) OR "
            "(subject_kind = 'feature_recipe' AND feature_set_version_id IS NOT NULL) OR "
            "(subject_kind = 'experiment' AND experiment_id IS NOT NULL) OR "
            "(subject_kind = 'candidate' AND candidate_id IS NOT NULL) OR "
            "(subject_kind = 'model_version' AND model_version_id IS NOT NULL)))",
            name="ck_pdr_subject_matches_kind",
        ),
        sa.CheckConstraint(
            "subject_digest IS NULL OR subject_digest ~ '^[0-9a-f]{1,64}$'",
            name="ck_pdr_subject_digest",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('human', 'rule', 'agent')", name="ck_pdr_actor_kind"
        ),
        sa.CheckConstraint(
            "(actor_kind = 'human' AND actor_user_id IS NOT NULL AND actor_rule IS NULL "
            "AND actor_agent_run_id IS NULL AND actor_service_token_id IS NULL) "
            "OR (actor_kind = 'rule' AND actor_rule IS NOT NULL "
            "AND actor_agent_run_id IS NULL AND actor_service_token_id IS NULL) "
            "OR (actor_kind = 'agent' "
            "AND (actor_agent_run_id IS NOT NULL OR actor_service_token_id IS NOT NULL))",
            name="ck_pdr_actor",
        ),
        sa.CheckConstraint("char_length(btrim(rationale)) > 0", name="ck_pdr_rationale"),
        sa.CheckConstraint(
            "rationale_untrusted = (actor_kind = 'agent')", name="ck_pdr_rationale_untrusted"
        ),
        sa.CheckConstraint("jsonb_typeof(facts) = 'object'", name="ck_pdr_facts_object"),
        sa.CheckConstraint(
            "octet_length(CAST(facts AS TEXT)) <= 16384", name="ck_pdr_facts_bounded"
        ),
        sa.CheckConstraint(_no_secrets("facts"), name="ck_pdr_facts_no_secrets"),
        # No forbidden-key CHECK on evidence_refs: an array of objects has no
        # meaningful top-level keys; the Pydantic write schema is the gate.
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(evidence_refs) = 'array' "
            "THEN jsonb_array_length(evidence_refs) <= 64 ELSE false END",
            name="ck_pdr_evidence_refs_array",
        ),
        sa.CheckConstraint(
            "octet_length(CAST(evidence_refs AS TEXT)) <= 16384",
            name="ck_pdr_evidence_refs_bounded",
        ),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_pdr_details_object"),
        sa.CheckConstraint(
            "octet_length(CAST(details AS TEXT)) <= 16384", name="ck_pdr_details_bounded"
        ),
        sa.CheckConstraint(_no_secrets("details"), name="ck_pdr_details_no_secrets"),
        sa.CheckConstraint("schema_version >= 1", name="ck_pdr_schema_version"),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="ck_pdr_supersedes_not_self"
        ),
    )
    op.create_index(
        "ix_pdr_project_recorded_at",
        "project_decision_records",
        ["project_id", sa.text("recorded_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_pdr_project_type_recorded_at",
        "project_decision_records",
        ["project_id", "decision_type", sa.text("recorded_at DESC"), sa.text("id DESC")],
    )
    for column in (
        "problem_spec_id",
        "dataset_id",
        "split_plan_id",
        "feature_set_version_id",
        "experiment_id",
        "candidate_id",
        "model_version_id",
        "actor_agent_run_id",
        "actor_service_token_id",
    ):
        op.create_index(
            f"ix_pdr_{column}",
            "project_decision_records",
            [column],
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )
    op.create_index("ix_pdr_actor_user_id", "project_decision_records", ["actor_user_id"])
    op.create_index(
        "uq_pdr_supersedes_id",
        "project_decision_records",
        ["supersedes_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )
    op.create_index(
        "uq_pdr_workspace_idempotency_key",
        "project_decision_records",
        ["workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "project_refs",
        _uuid("id", nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _uuid("project_id", nullable=False),
        sa.Column("ref_kind", sa.String(32), nullable=False),
        _uuid("problem_spec_id", nullable=True),
        _uuid("dataset_id", nullable=True),
        _uuid("split_plan_id", nullable=True),
        _uuid("feature_set_version_id", nullable=True),
        _uuid("model_version_id", nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _uuid("decision_record_id", nullable=False),
        _now("moved_at"),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_project_refs_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name="uq_project_refs_workspace_project_id"
        ),
        sa.UniqueConstraint("project_id", "ref_kind", name="uq_project_refs_project_ref_kind"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_project_refs_workspace_project",
            ondelete="CASCADE",
        ),
        _three_column_fk(
            "problem_spec_id", "problem_specs", "fk_project_refs_workspace_project_problem_spec"
        ),
        _three_column_fk("dataset_id", "datasets", "fk_project_refs_workspace_project_dataset"),
        _three_column_fk(
            "split_plan_id", "split_plans", "fk_project_refs_workspace_project_split_plan"
        ),
        _three_column_fk(
            "feature_set_version_id",
            "feature_set_versions",
            "fk_project_refs_workspace_project_feature_set_version",
        ),
        _three_column_fk(
            "model_version_id", "model_versions", "fk_project_refs_workspace_project_model_version"
        ),
        _three_column_fk(
            "decision_record_id",
            "project_decision_records",
            "fk_project_refs_workspace_project_decision_record",
        ),
        sa.CheckConstraint(
            "ref_kind IN ('problem_spec', 'dataset', 'split_plan', 'feature_recipe', "
            "'champion_model')",
            name="ck_project_refs_ref_kind",
        ),
        sa.CheckConstraint(
            "(num_nonnulls(problem_spec_id, dataset_id, split_plan_id, "
            "feature_set_version_id, model_version_id) = 1 AND ((ref_kind = 'problem_spec' "
            "AND problem_spec_id IS NOT NULL) OR (ref_kind = 'dataset' AND dataset_id IS "
            "NOT NULL) OR (ref_kind = 'split_plan' AND split_plan_id IS NOT NULL) OR "
            "(ref_kind = 'feature_recipe' AND feature_set_version_id IS NOT NULL) OR "
            "(ref_kind = 'champion_model' AND model_version_id IS NOT NULL)))",
            name="ck_project_refs_target_matches_kind",
        ),
        sa.CheckConstraint("version >= 1", name="ck_project_refs_version"),
    )
    for column in (
        "problem_spec_id",
        "dataset_id",
        "split_plan_id",
        "feature_set_version_id",
        "model_version_id",
        "decision_record_id",
    ):
        op.create_index(f"ix_project_refs_{column}", "project_refs", [column])

    op.add_column("experiments", _uuid("source_dataset_id", nullable=True))
    op.add_column("experiments", _uuid("split_plan_id", nullable=True))
    op.add_column("experiments", sa.Column("intent", sa.String(2000), nullable=True))
    op.add_column("experiments", sa.Column("change_set", postgresql.JSONB(), nullable=True))
    op.create_foreign_key(
        "fk_experiments_workspace_source_dataset",
        "experiments",
        "datasets",
        ["workspace_id", "source_dataset_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_experiments_workspace_project_split_plan",
        "experiments",
        "split_plans",
        ["workspace_id", "project_id", "split_plan_id"],
        ["workspace_id", "project_id", "id"],
    )
    op.create_index("ix_experiments_source_dataset_id", "experiments", ["source_dataset_id"])
    op.create_index("ix_experiments_split_plan_id", "experiments", ["split_plan_id"])
    for name, clause in (
        (
            "ck_experiments_split_plan_requires_project",
            "split_plan_id IS NULL OR project_id IS NOT NULL",
        ),
        ("ck_experiments_intent_not_blank", "intent IS NULL OR char_length(btrim(intent)) > 0"),
        (
            "ck_experiments_change_set_object",
            "change_set IS NULL OR jsonb_typeof(change_set) = 'object'",
        ),
        (
            "ck_experiments_change_set_schema_version",
            "change_set IS NULL OR "
            "COALESCE((change_set ->> 'schema_version') ~ '^[0-9]+$', false)",
        ),
        (
            "ck_experiments_change_set_changes",
            "change_set IS NULL OR CASE WHEN jsonb_typeof(change_set -> 'changes') = 'array' "
            "THEN jsonb_array_length(change_set -> 'changes') BETWEEN 1 AND 32 ELSE false END",
        ),
        (
            "ck_experiments_change_set_bounded",
            "change_set IS NULL OR octet_length(CAST(change_set AS TEXT)) <= 16384",
        ),
        (
            "ck_experiments_change_set_no_secrets",
            f"change_set IS NULL OR {_no_secrets('change_set')}",
        ),
        (
            "ck_experiments_change_set_requires_parent",
            "change_set IS NULL OR parent_pipeline_run_id IS NOT NULL",
        ),
    ):
        op.create_check_constraint(name, "experiments", clause)

    op.create_index(
        "ix_experiment_candidates_experiment_feature_set_version",
        "experiment_candidates",
        ["experiment_id", "feature_set_version_id"],
        postgresql_where=sa.text("feature_set_version_id IS NOT NULL"),
    )

    op.execute(_SOURCE_DATASET_WORKSPACE_PRECHECK_SQL)
    op.execute(_SOURCE_DATASET_BACKFILL_SQL)

    for statement in _TRIGGERS:
        op.execute(statement)
    op.execute(PREVENT_EXPERIMENT_LINEAGE_VIOLATION_SQL)
    op.execute(EXPERIMENTS_LINEAGE_GUARD_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_PRECHECK_SQL)

    op.execute("DROP TRIGGER IF EXISTS experiments_lineage_guard ON experiments")
    op.execute("DROP FUNCTION IF EXISTS prevent_experiment_lineage_violation()")
    op.execute("DROP TRIGGER IF EXISTS project_refs_no_delete ON project_refs")
    op.execute("DROP TRIGGER IF EXISTS project_refs_columns_immutable ON project_refs")
    op.execute(
        "DROP TRIGGER IF EXISTS project_decision_records_append_only "
        "ON project_decision_records"
    )
    op.execute("DROP TRIGGER IF EXISTS split_plans_immutable ON split_plans")

    op.drop_index(
        "ix_experiment_candidates_experiment_feature_set_version",
        table_name="experiment_candidates",
    )

    for name in (
        "ck_experiments_change_set_requires_parent",
        "ck_experiments_change_set_no_secrets",
        "ck_experiments_change_set_bounded",
        "ck_experiments_change_set_changes",
        "ck_experiments_change_set_schema_version",
        "ck_experiments_change_set_object",
        "ck_experiments_intent_not_blank",
        "ck_experiments_split_plan_requires_project",
    ):
        op.drop_constraint(name, "experiments", type_="check")
    op.drop_index("ix_experiments_split_plan_id", table_name="experiments")
    op.drop_index("ix_experiments_source_dataset_id", table_name="experiments")
    op.drop_constraint(
        "fk_experiments_workspace_project_split_plan", "experiments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_experiments_workspace_source_dataset", "experiments", type_="foreignkey"
    )
    for column in ("change_set", "intent", "split_plan_id", "source_dataset_id"):
        op.drop_column("experiments", column)

    op.drop_table("project_refs")
    op.drop_table("project_decision_records")
    op.drop_table("split_plans")

    op.drop_constraint("ck_artifacts_type_valid", "artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifacts_type_valid", "artifacts", _artifact_clause(_OLD_ARTIFACT_TYPES)
    )

    for table in reversed(_PROJECT_SCOPED_TABLES):
        op.drop_constraint(f"uq_{table}_workspace_project_id", table, type_="unique")
