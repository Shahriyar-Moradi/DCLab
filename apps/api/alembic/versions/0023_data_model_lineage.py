"""add configurable business, workflow, dataset, pipeline, and model lineage

Revision ID: 0023_data_model_lineage
Revises: 0022_multi_tenant_identity
Create Date: 2026-09-02
"""

from typing import Sequence, Union
from uuid import NAMESPACE_URL, uuid4, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_data_model_lineage"
down_revision: Union[str, Sequence[str], None] = "0022_multi_tenant_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
DOMAIN_SEEDS = (
    ("labs", "Labs"),
    ("marketing", "Marketing"),
    ("sales", "Sales"),
    ("revenue", "Revenue"),
    ("customer", "Customer"),
)


def upgrade() -> None:
    op.create_table(
        "business_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("default_config", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_business_domains_slug", "business_domains", ["slug"], unique=True
    )
    op.create_table(
        "workspace_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "business_domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("business_domains.id"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "business_domain_id",
            name="uq_workspace_domains_workspace_domain",
        ),
    )
    op.create_index(
        "ix_workspace_domains_workspace_id", "workspace_domains", ["workspace_id"]
    )
    op.create_index(
        "ix_workspace_domains_business_domain_id",
        "workspace_domains",
        ["business_domain_id"],
    )

    connection = op.get_bind()
    domain_rows = [
        {
            "id": uuid5(NAMESPACE_URL, f"dclab:business-domain:{slug}"),
            "slug": slug,
            "name": name,
            "description": f"{name} workflows",
            "default_config": {},
        }
        for slug, name in DOMAIN_SEEDS
    ]
    domain_table = sa.table(
        "business_domains",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("default_config", postgresql.JSONB()),
    )
    op.bulk_insert(domain_table, domain_rows)
    workspace_ids = list(
        connection.execute(sa.text("SELECT id FROM workspaces")).scalars()
    )
    workspace_domain_table = sa.table(
        "workspace_domains",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("business_domain_id", postgresql.UUID(as_uuid=True)),
        sa.column("config", postgresql.JSONB()),
    )
    workspace_domain_rows = [
        {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "business_domain_id": domain["id"],
            "config": {},
        }
        for workspace_id in workspace_ids
        for domain in domain_rows
    ]
    if workspace_domain_rows:
        op.bulk_insert(workspace_domain_table, workspace_domain_rows)

    op.create_table(
        "dataset_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_dataset_assets_workspace_slug"
        ),
    )
    op.create_index(
        "ix_dataset_assets_workspace_id", "dataset_assets", ["workspace_id"]
    )
    op.add_column(
        "datasets", sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("dataset_asset_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("datasets", sa.Column("content_digest", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_datasets_workspace_id_workspaces",
        "datasets",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_datasets_dataset_asset_id_dataset_assets",
        "datasets",
        "dataset_assets",
        ["dataset_asset_id"],
        ["id"],
    )

    dataset_rows = list(
        connection.execute(sa.text("SELECT id, name FROM datasets")).mappings()
    )
    dataset_asset_table = sa.table(
        "dataset_assets",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
        sa.column("description", sa.String()),
    )
    backfilled_assets: list[dict[str, object]] = []
    for row in dataset_rows:
        asset_id = uuid4()
        backfilled_assets.append(
            {
                "id": asset_id,
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "name": row["name"],
                "slug": f"legacy-{str(row['id']).replace('-', '')[:12]}",
                "description": "Backfilled logical asset for an existing Lab dataset.",
            }
        )
    if backfilled_assets:
        op.bulk_insert(dataset_asset_table, backfilled_assets)
    # Referenced logical assets exist before the physical dataset rows are linked.
    for row, asset in zip(dataset_rows, backfilled_assets, strict=True):
        connection.execute(
            sa.text(
                "UPDATE datasets SET workspace_id = :workspace_id, "
                "dataset_asset_id = :asset_id WHERE id = :dataset_id"
            ),
            {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "asset_id": asset["id"],
                "dataset_id": row["id"],
            },
        )
    op.alter_column(
        "datasets",
        "workspace_id",
        nullable=False,
        server_default=DEFAULT_WORKSPACE_ID,
    )
    op.alter_column("datasets", "dataset_asset_id", nullable=False)
    op.create_index("ix_datasets_workspace_id", "datasets", ["workspace_id"])
    op.create_index("ix_datasets_dataset_asset_id", "datasets", ["dataset_asset_id"])
    op.create_index("ix_datasets_content_digest", "datasets", ["content_digest"])
    op.create_unique_constraint(
        "uq_datasets_asset_version", "datasets", ["dataset_asset_id", "version"]
    )
    op.create_unique_constraint(
        "uq_datasets_asset_content_digest",
        "datasets",
        ["dataset_asset_id", "content_digest"],
    )

    op.create_table(
        "ml_workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_domains.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("description", sa.String(2048), nullable=False),
        sa.Column("business_objective", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_ml_workflows_workspace_slug"
        ),
    )
    op.create_index("ix_ml_workflows_workspace_id", "ml_workflows", ["workspace_id"])
    op.create_index(
        "ix_ml_workflows_workspace_domain_id", "ml_workflows", ["workspace_domain_id"]
    )
    op.create_index("ix_ml_workflows_status", "ml_workflows", ["status"])
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ml_workflows.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("trigger_type", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column(
            "source_upload_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("explicit_target", sa.String(256), nullable=True),
        sa.Column("resolved_target", sa.String(256), nullable=True),
        sa.Column("task_type", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_workflow_runs_workspace_id", "workflow_runs", ["workspace_id"])
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"])
    op.create_index(
        "ix_workflow_runs_source_upload_id", "workflow_runs", ["source_upload_id"]
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_foreign_key(
        "workflow_runs_source_upload_id_fkey",
        "workflow_runs",
        "client_lab_uploads",
        ["source_upload_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "workflow_run_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id"),
            nullable=False,
        ),
        sa.Column("input_role", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workflow_run_id",
            "dataset_id",
            "input_role",
            name="uq_workflow_run_inputs_run_dataset_role",
        ),
    )
    op.create_index(
        "ix_workflow_run_inputs_workflow_run_id",
        "workflow_run_inputs",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_run_inputs_dataset_id", "workflow_run_inputs", ["dataset_id"]
    )
    op.create_index(
        "ix_workflow_run_inputs_input_role", "workflow_run_inputs", ["input_role"]
    )

    op.add_column(
        "experiments", sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "experiments", sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "experiments",
        sa.Column(
            "pipeline_name", sa.String(128), server_default="deterministic_ml", nullable=False
        ),
    )
    op.add_column(
        "experiments",
        sa.Column("pipeline_index", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "experiments",
        sa.Column("pipeline_purpose", sa.String(128), server_default="training", nullable=False),
    )
    op.create_foreign_key(
        "fk_experiments_workspace_id_workspaces",
        "experiments",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_experiments_workflow_run_id_workflow_runs",
        "experiments",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
    )
    connection.execute(
        sa.text(
            "UPDATE experiments SET workspace_id = datasets.workspace_id "
            "FROM datasets WHERE experiments.dataset_id = datasets.id"
        )
    )
    op.alter_column(
        "experiments",
        "workspace_id",
        nullable=False,
        server_default=DEFAULT_WORKSPACE_ID,
    )
    op.create_index("ix_experiments_workspace_id", "experiments", ["workspace_id"])
    op.create_index("ix_experiments_workflow_run_id", "experiments", ["workflow_run_id"])
    op.create_unique_constraint(
        "uq_experiments_workflow_run_pipeline_index",
        "experiments",
        ["workflow_run_id", "pipeline_index"],
    )

    op.create_table(
        "model_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ml_workflows.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_model_assets_workspace_slug"
        ),
    )
    op.create_index("ix_model_assets_workspace_id", "model_assets", ["workspace_id"])
    op.create_index("ix_model_assets_workflow_id", "model_assets", ["workflow_id"])
    op.create_index("ix_model_assets_status", "model_assets", ["status"])
    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "model_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_assets.id"),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ml_workflows.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id"),
            nullable=False,
        ),
        sa.Column(
            "selected_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id"),
            nullable=False,
        ),
        sa.Column("artifact_uri", sa.String(1024), nullable=True),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "model_asset_id", "version", name="uq_model_versions_asset_version"
        ),
        sa.UniqueConstraint(
            "pipeline_run_id", name="uq_model_versions_pipeline_run_id"
        ),
        sa.UniqueConstraint(
            "selected_candidate_id", name="uq_model_versions_selected_candidate_id"
        ),
    )
    op.create_index("ix_model_versions_workspace_id", "model_versions", ["workspace_id"])
    op.create_index("ix_model_versions_workflow_id", "model_versions", ["workflow_id"])
    op.create_index(
        "ix_model_versions_workflow_run_id", "model_versions", ["workflow_run_id"]
    )
    op.create_index("ix_model_versions_dataset_id", "model_versions", ["dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_model_versions_dataset_id", table_name="model_versions")
    op.drop_index("ix_model_versions_workflow_run_id", table_name="model_versions")
    op.drop_index("ix_model_versions_workflow_id", table_name="model_versions")
    op.drop_index("ix_model_versions_workspace_id", table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_index("ix_model_assets_status", table_name="model_assets")
    op.drop_index("ix_model_assets_workflow_id", table_name="model_assets")
    op.drop_index("ix_model_assets_workspace_id", table_name="model_assets")
    op.drop_table("model_assets")

    op.drop_constraint(
        "uq_experiments_workflow_run_pipeline_index", "experiments", type_="unique"
    )
    op.drop_index("ix_experiments_workflow_run_id", table_name="experiments")
    op.drop_index("ix_experiments_workspace_id", table_name="experiments")
    op.drop_constraint(
        "fk_experiments_workflow_run_id_workflow_runs",
        "experiments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_experiments_workspace_id_workspaces", "experiments", type_="foreignkey"
    )
    op.drop_column("experiments", "pipeline_purpose")
    op.drop_column("experiments", "pipeline_index")
    op.drop_column("experiments", "pipeline_name")
    op.drop_column("experiments", "workflow_run_id")
    op.drop_column("experiments", "workspace_id")

    op.drop_index("ix_workflow_run_inputs_input_role", table_name="workflow_run_inputs")
    op.drop_index("ix_workflow_run_inputs_dataset_id", table_name="workflow_run_inputs")
    op.drop_index(
        "ix_workflow_run_inputs_workflow_run_id", table_name="workflow_run_inputs"
    )
    op.drop_table("workflow_run_inputs")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_constraint(
        "workflow_runs_source_upload_id_fkey",
        "workflow_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_workflow_runs_source_upload_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workspace_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_ml_workflows_status", table_name="ml_workflows")
    op.drop_index("ix_ml_workflows_workspace_domain_id", table_name="ml_workflows")
    op.drop_index("ix_ml_workflows_workspace_id", table_name="ml_workflows")
    op.drop_table("ml_workflows")

    op.drop_constraint("uq_datasets_asset_content_digest", "datasets", type_="unique")
    op.drop_constraint("uq_datasets_asset_version", "datasets", type_="unique")
    op.drop_index("ix_datasets_content_digest", table_name="datasets")
    op.drop_index("ix_datasets_dataset_asset_id", table_name="datasets")
    op.drop_index("ix_datasets_workspace_id", table_name="datasets")
    op.drop_constraint(
        "fk_datasets_dataset_asset_id_dataset_assets", "datasets", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_datasets_workspace_id_workspaces", "datasets", type_="foreignkey"
    )
    op.drop_column("datasets", "content_digest")
    op.drop_column("datasets", "dataset_asset_id")
    op.drop_column("datasets", "workspace_id")
    op.drop_index("ix_dataset_assets_workspace_id", table_name="dataset_assets")
    op.drop_table("dataset_assets")
    op.drop_index(
        "ix_workspace_domains_business_domain_id", table_name="workspace_domains"
    )
    op.drop_index("ix_workspace_domains_workspace_id", table_name="workspace_domains")
    op.drop_table("workspace_domains")
    op.drop_index("ix_business_domains_slug", table_name="business_domains")
    op.drop_table("business_domains")
