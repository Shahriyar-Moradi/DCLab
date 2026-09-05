import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    desc,
    event,
    func,
    inspect as orm_inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.data_plane import (
    CK_ARTIFACTS_PROVIDER,
    CK_ARTIFACTS_TYPE,
    CK_DATA_SOURCES_STATUS,
    CK_DATA_SOURCES_TYPE,
    CK_INGESTION_RUNS_STATUS,
)
from app.domain.execution_plane import (
    CK_PIPELINE_STAGE_RUNS_STATUS,
    CK_PIPELINE_VERSION_POSITIVE,
    CK_PIPELINES_STATUS,
    CK_WORKFLOW_RUNS_INITIATED_BY,
    CK_WORKFLOW_VERSION_POSITIVE,
)
from app.domain.reproducibility import CK_CODE_LANGUAGE
from app.domain.ml_jobs import CK_ML_JOB_STATUS, CK_ML_JOB_TYPE
from app.domain.scientific_plane import (
    CK_CV_FOLD_RUN_STATUS,
    CK_DATA_QUALITY_FINDING_TYPE,
    CK_DATA_QUALITY_SEVERITY,
    CK_FEATURE_LINEAGE_RELATIONSHIP,
    CK_FEATURE_SET_VERSION_POSITIVE,
    CK_FEATURE_STATUS,
    CK_HYPERPARAMETER_SOURCE,
    CK_MODEL_EVALUATION_SCOPE,
    CK_MODEL_EVALUATION_STATUS,
    CK_MODEL_EVALUATION_TYPE,
    CK_PREPARATION_DECISION_SOURCE,
    CK_PREPROCESSING_FIT_SCOPE,
)
from app.domain.workspace_identity import (
    PROJECT_PROVENANCE_SYSTEM_LEGACY_IMPORT,
    PROJECT_PROVENANCE_USER,
)

DEFAULT_ORG_ID = "default"

# Well-known workspace every pre-existing row is backfilled to, and the workspace
# used when a request omits X-Workspace-Id. Fixed (not random) so migrations and
# app code can agree on it without a lookup.
DEFAULT_WORKSPACE_SLUG = "default"
DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class WorkspaceKind(str, enum.Enum):
    PERSONAL = "personal"
    BUSINESS = "business"


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('personal', 'business')",
            name="ck_workspaces_kind_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WorkspaceKind.BUSINESS.value,
        server_default="business",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="workspace")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="workspace")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="workspace")
    users: Mapped[list["User"]] = relationship(back_populates="workspace")
    business_profile: Mapped["BusinessProfile | None"] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", uselist=False
    )
    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    capabilities: Mapped[list["WorkspaceCapability"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    entitlements: Mapped[list["WorkspaceEntitlement"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    domain_links: Mapped[list["WorkspaceDomain"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    dataset_assets: Mapped[list["DatasetAsset"]] = relationship(
        back_populates="workspace",
        foreign_keys="DatasetAsset.workspace_id",
    )
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="workspace",
        foreign_keys="Dataset.workspace_id",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="workspace",
        foreign_keys="Artifact.workspace_id",
    )
    data_sources: Mapped[list["DataSource"]] = relationship(
        back_populates="workspace",
        foreign_keys="DataSource.workspace_id",
    )
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        back_populates="workspace",
        foreign_keys="IngestionRun.workspace_id",
    )
    dataset_columns: Mapped[list["DatasetColumn"]] = relationship(
        back_populates="workspace",
        foreign_keys="DatasetColumn.workspace_id",
    )
    ml_workflows: Mapped[list["MlWorkflow"]] = relationship(
        back_populates="workspace",
        foreign_keys="MlWorkflow.workspace_id",
    )
    workflow_versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="workspace",
        foreign_keys="WorkflowVersion.workspace_id",
    )
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workspace",
        foreign_keys="WorkflowRun.workspace_id",
    )
    pipelines: Mapped[list["Pipeline"]] = relationship(
        back_populates="workspace",
        foreign_keys="Pipeline.workspace_id",
    )
    pipeline_versions: Mapped[list["PipelineVersion"]] = relationship(
        back_populates="workspace",
        foreign_keys="PipelineVersion.workspace_id",
    )
    pipeline_runs: Mapped[list["Experiment"]] = relationship(
        back_populates="workspace",
        foreign_keys="Experiment.workspace_id",
    )
    pipeline_stage_runs: Mapped[list["PipelineStageRun"]] = relationship(
        back_populates="workspace",
        foreign_keys="PipelineStageRun.workspace_id",
    )
    data_quality_findings: Mapped[list["DataQualityFinding"]] = relationship(
        back_populates="workspace",
        foreign_keys="DataQualityFinding.workspace_id",
    )
    data_preparation_decisions: Mapped[list["DataPreparationDecision"]] = relationship(
        back_populates="workspace",
        foreign_keys="DataPreparationDecision.workspace_id",
    )
    feature_sets: Mapped[list["FeatureSet"]] = relationship(
        back_populates="workspace",
        foreign_keys="FeatureSet.workspace_id",
    )
    feature_set_versions: Mapped[list["FeatureSetVersion"]] = relationship(
        back_populates="workspace",
        foreign_keys="FeatureSetVersion.workspace_id",
    )
    features: Mapped[list["Feature"]] = relationship(
        back_populates="workspace",
        foreign_keys="Feature.workspace_id",
    )
    preprocessing_steps: Mapped[list["PreprocessingStep"]] = relationship(
        back_populates="workspace",
        foreign_keys="PreprocessingStep.workspace_id",
    )
    experiment_candidates: Mapped[list["ExperimentCandidate"]] = relationship(
        back_populates="workspace",
        foreign_keys="ExperimentCandidate.workspace_id",
    )
    cv_fold_runs: Mapped[list["CVFoldRun"]] = relationship(
        back_populates="workspace",
        foreign_keys="CVFoldRun.workspace_id",
    )
    model_evaluations: Mapped[list["ModelEvaluation"]] = relationship(
        back_populates="workspace",
        foreign_keys="ModelEvaluation.workspace_id",
    )
    model_selection_decisions: Mapped[list["ModelSelectionDecision"]] = relationship(
        back_populates="workspace",
        foreign_keys="ModelSelectionDecision.workspace_id",
    )
    scientific_plans: Mapped[list["PipelineScientificPlan"]] = relationship(
        back_populates="workspace",
        foreign_keys="PipelineScientificPlan.workspace_id",
    )
    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="workspace",
        foreign_keys="CodeSnapshot.workspace_id",
    )
    model_assets: Mapped[list["ModelAsset"]] = relationship(back_populates="workspace")
    model_versions: Mapped[list["ModelVersion"]] = relationship(back_populates="workspace")
    ml_run_events: Mapped[list["MlRunEvent"]] = relationship(back_populates="workspace")
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(back_populates="workspace")
    projects: Mapped[list["Project"]] = relationship(back_populates="workspace")
    problem_specs: Mapped[list["ProblemSpec"]] = relationship(
        back_populates="workspace",
        foreign_keys="ProblemSpec.workspace_id",
    )


class UserRole(str, enum.Enum):
    """Compatibility mirror for identity responses and the legacy users.role column.

    Authorization is derived from membership tables. Internal platform roles stay
    on ``PlatformMembership``. Customer workspace roles live on
    ``WorkspaceMembership``. Legacy ``business_*`` and ``client_user`` values
    remain valid during the migration window.
    """

    DCLAB_ADMIN = "dclab_admin"
    DCLAB_DEVELOPER = "dclab_developer"
    BUSINESS_ADMIN = "business_admin"
    BUSINESS_DEVELOPER = "business_developer"
    CLIENT_USER = "client_user"
    WORKSPACE_OWNER = "workspace_owner"
    WORKSPACE_ADMIN = "workspace_admin"
    ML_ENGINEER = "ml_engineer"
    VIEWER = "viewer"


class PlatformRole(str, enum.Enum):
    DCLAB_ADMIN = "dclab_admin"
    DCLAB_DEVELOPER = "dclab_developer"


class WorkspaceRole(str, enum.Enum):
    """Customer workspace membership roles.

    Canonical roles are ``workspace_owner``, ``workspace_admin``, ``ml_engineer``,
    and ``viewer``. Legacy ``business_admin`` / ``business_developer`` values are
    still stored and accepted; authorization translates them rather than rewriting
    existing rows.
    """

    WORKSPACE_OWNER = "workspace_owner"
    WORKSPACE_ADMIN = "workspace_admin"
    ML_ENGINEER = "ml_engineer"
    VIEWER = "viewer"
    BUSINESS_ADMIN = "business_admin"
    BUSINESS_DEVELOPER = "business_developer"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
            "'business_developer', 'client_user', 'workspace_owner', "
            "'workspace_admin', 'ml_engineer', 'viewer')",
            name="ck_users_role_valid",
        ),
        # A client user is always scoped to exactly one workspace; DCLab admins are
        # not tied to a client account, so their workspace_id stays NULL.
        CheckConstraint(
            "role <> 'client_user' OR workspace_id IS NOT NULL",
            name="ck_users_client_requires_workspace",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace | None] = relationship(back_populates="users")
    platform_membership: Mapped["PlatformMembership | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    workspace_memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class BusinessProfile(Base):
    """Business metadata attached to the canonical Workspace tenant."""

    __tablename__ = "business_profiles"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    legal_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile_data: Mapped[dict] = mapped_column(
        "profile_data", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(back_populates="business_profile")


class PlatformMembership(Base):
    __tablename__ = "platform_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_platform_memberships_user_id"),
        CheckConstraint(
            "role IN ('dclab_admin', 'dclab_developer')",
            name="ck_platform_memberships_role_valid",
        ),
        Index("ix_platform_memberships_role", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="platform_membership")


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_memberships_workspace_user",
        ),
        CheckConstraint(
            "role IN ('business_admin', 'business_developer', 'workspace_owner', "
            "'workspace_admin', 'ml_engineer', 'viewer')",
            name="ck_workspace_memberships_role_valid",
        ),
        Index("ix_workspace_memberships_workspace_id", "workspace_id"),
        Index("ix_workspace_memberships_user_id", "user_id"),
        Index("ix_workspace_memberships_role", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="workspace_memberships")


class WorkspaceEntitlement(Base):
    __tablename__ = "workspace_entitlements"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "entitlement_key",
            name="uq_workspace_entitlements_workspace_key",
        ),
        Index("ix_workspace_entitlements_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    entitlement_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[object] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(back_populates="entitlements")


class Project(Base):
    """Persistent ML/data-science case. Distinct from a Workflow execution graph."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_projects_workspace_id"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status_valid"),
        CheckConstraint(
            f"provenance IN ('{PROJECT_PROVENANCE_USER}', "
            f"'{PROJECT_PROVENANCE_SYSTEM_LEGACY_IMPORT}')",
            name="ck_projects_provenance_valid",
        ),
        CheckConstraint(
            f"provenance <> '{PROJECT_PROVENANCE_USER}' OR created_by IS NOT NULL",
            name="ck_projects_user_provenance_requires_actor",
        ),
        Index("ix_projects_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_projects_workspace_status_created_at",
            "workspace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(4000), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    provenance: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PROJECT_PROVENANCE_USER,
        server_default=PROJECT_PROVENANCE_USER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped[Workspace] = relationship(back_populates="projects")
    problem_specs: Mapped[list["ProblemSpec"]] = relationship(
        back_populates="project",
        foreign_keys="ProblemSpec.project_id",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="project",
        foreign_keys="Artifact.project_id",
    )
    data_sources: Mapped[list["DataSource"]] = relationship(
        back_populates="project",
        foreign_keys="DataSource.project_id",
    )
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        back_populates="project",
        foreign_keys="IngestionRun.project_id",
    )
    dataset_assets: Mapped[list["DatasetAsset"]] = relationship(
        back_populates="project",
        foreign_keys="DatasetAsset.project_id",
    )
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="project",
        foreign_keys="Dataset.project_id",
    )
    ml_workflows: Mapped[list["MlWorkflow"]] = relationship(
        back_populates="project",
        foreign_keys="MlWorkflow.project_id",
    )
    workflow_versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="project",
        foreign_keys="WorkflowVersion.project_id",
    )
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="project",
        foreign_keys="WorkflowRun.project_id",
    )
    pipelines: Mapped[list["Pipeline"]] = relationship(
        back_populates="project",
        foreign_keys="Pipeline.project_id",
    )
    pipeline_versions: Mapped[list["PipelineVersion"]] = relationship(
        back_populates="project",
        foreign_keys="PipelineVersion.project_id",
    )
    pipeline_runs: Mapped[list["Experiment"]] = relationship(
        back_populates="project",
        foreign_keys="Experiment.project_id",
    )
    pipeline_stage_runs: Mapped[list["PipelineStageRun"]] = relationship(
        back_populates="project",
        foreign_keys="PipelineStageRun.project_id",
    )
    data_quality_findings: Mapped[list["DataQualityFinding"]] = relationship(
        back_populates="project",
        foreign_keys="DataQualityFinding.project_id",
    )
    data_preparation_decisions: Mapped[list["DataPreparationDecision"]] = relationship(
        back_populates="project",
        foreign_keys="DataPreparationDecision.project_id",
    )
    feature_sets: Mapped[list["FeatureSet"]] = relationship(
        back_populates="project",
        foreign_keys="FeatureSet.project_id",
    )
    feature_set_versions: Mapped[list["FeatureSetVersion"]] = relationship(
        back_populates="project",
        foreign_keys="FeatureSetVersion.project_id",
    )
    features: Mapped[list["Feature"]] = relationship(
        back_populates="project",
        foreign_keys="Feature.project_id",
    )
    preprocessing_steps: Mapped[list["PreprocessingStep"]] = relationship(
        back_populates="project",
        foreign_keys="PreprocessingStep.project_id",
    )
    experiment_candidates: Mapped[list["ExperimentCandidate"]] = relationship(
        back_populates="project",
        foreign_keys="ExperimentCandidate.project_id",
    )
    cv_fold_runs: Mapped[list["CVFoldRun"]] = relationship(
        back_populates="project",
        foreign_keys="CVFoldRun.project_id",
    )
    model_evaluations: Mapped[list["ModelEvaluation"]] = relationship(
        back_populates="project",
        foreign_keys="ModelEvaluation.project_id",
    )
    model_selection_decisions: Mapped[list["ModelSelectionDecision"]] = relationship(
        back_populates="project",
        foreign_keys="ModelSelectionDecision.project_id",
    )
    scientific_plans: Mapped[list["PipelineScientificPlan"]] = relationship(
        back_populates="project",
        foreign_keys="PipelineScientificPlan.project_id",
    )
    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="project",
        foreign_keys="CodeSnapshot.project_id",
    )


class ProblemSpec(Base):
    """Versioned user-intent statement. Upstream of TaskSpec / model-development plans."""

    __tablename__ = "problem_specs"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_problem_specs_project_version"),
        UniqueConstraint("workspace_id", "id", name="uq_problem_specs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_problem_specs_workspace_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 1", name="ck_problem_specs_version_positive"),
        CheckConstraint(
            "status IN ('draft', 'locked')",
            name="ck_problem_specs_status_valid",
        ),
        Index("ix_problem_specs_workspace_id", "workspace_id"),
        Index("ix_problem_specs_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_column: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prediction_unit: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prediction_time_column: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prediction_horizon: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_metric: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_objective: Mapped[str] = mapped_column(String(4000), nullable=False)
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    success_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="problem_specs",
        foreign_keys="ProblemSpec.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="problem_specs",
        foreign_keys="ProblemSpec.project_id",
    )


class WorkspaceCapability(Base):
    __tablename__ = "workspace_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "capability",
            name="uq_workspace_capabilities_workspace_key",
        ),
        Index("ix_workspace_capabilities_workspace_id", "workspace_id"),
        Index("ix_workspace_capabilities_capability", "capability"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="capabilities")


class BusinessDomain(Base):
    """Configurable catalog entry such as labs, marketing, or sales."""

    __tablename__ = "business_domains"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    default_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace_links: Mapped[list["WorkspaceDomain"]] = relationship(
        back_populates="business_domain"
    )


class WorkspaceDomain(Base):
    __tablename__ = "workspace_domains"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "business_domain_id",
            name="uq_workspace_domains_workspace_domain",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_workspace_domains_workspace_id"),
        Index("ix_workspace_domains_business_domain_id", "business_domain_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    business_domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_domains.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(back_populates="domain_links")
    business_domain: Mapped[BusinessDomain] = relationship(back_populates="workspace_links")
    workflows: Mapped[list["MlWorkflow"]] = relationship(
        back_populates="workspace_domain",
        foreign_keys="MlWorkflow.workspace_domain_id",
    )


class Opportunity(Base):
    """LEGACY product-scoring fact table. Compatibility adapter, still written by /app/opportunities."""

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "external_id",
            name="uq_opportunities_workspace_external_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_ORG_ID, server_default=DEFAULT_ORG_ID
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="AED", server_default="AED")
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_contact_days_ago: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales_rep_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    num_interactions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    converted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    predictions: Mapped[list["Prediction"]] = relationship(back_populates="opportunity")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="opportunity")
    workspace: Mapped[Workspace] = relationship(back_populates="opportunities")


class Prediction(Base):
    """LEGACY product-scoring output. Compatibility adapter, still written beside Opportunity."""

    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
        index=True,
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    conversion_probability: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    opportunity: Mapped[Opportunity] = relationship(back_populates="predictions")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="prediction")
    workspace: Mapped[Workspace] = relationship(back_populates="predictions")


class Decision(Base):
    """LEGACY translated action row for Opportunity/Prediction. Compatibility adapter."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
        index=True,
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), nullable=False, unique=True, index=True
    )
    prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("predictions.id"), nullable=False, index=True
    )
    recommended_action: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    incremental_value: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, server_default="0"
    )
    reasoning: Mapped[list] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_review", server_default="pending_review"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    opportunity: Mapped[Opportunity] = relationship(back_populates="decisions")
    prediction: Mapped[Prediction] = relationship(back_populates="decisions")
    workspace: Mapped[Workspace] = relationship(back_populates="decisions")


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    use_case: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    fusion: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClientLabRun(Base):
    """LEGACY bounded trial run (catalog problems). Keep for history; Labs CSV auto-train uses ClientLabUpload."""

    __tablename__ = "client_lab_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
        index=True,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    use_case: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    insights: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClientLabRunAudit(Base):
    """LEGACY admin-only raw trial payload for ClientLabRun. Keep read-capable; not the canonical pipeline."""

    __tablename__ = "client_lab_run_audits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_lab_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_lab_runs.id"), nullable=False, unique=True
    )
    use_case: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    client_lab_run: Mapped[ClientLabRun] = relationship()


# Fine-grained pipeline_status values that still mean "the job is running"
# from the client's point of view. `queued` is listed here for documentation
# only — coarse status maps it to queued, not processing.
_PIPELINE_IN_PROGRESS = frozenset(
    {
        "queued",
        "ingesting",
        "analyzing",
        "cleaning",
        "feature_engineering",
        "preprocessing",
        "splitting",
        "cross_validation",
        "training",
        "evaluating",
        "predicting",
        "running",
    }
)


def client_status_for(pipeline_status: str) -> str:
    """Coarse four-state view stored on `ClientLabUpload.client_status`."""
    if pipeline_status == "queued":
        return "queued"
    if pipeline_status == "completed":
        return "completed"
    if pipeline_status in _PIPELINE_IN_PROGRESS:
        return "processing"
    return "failed"


class ClientLabUpload(Base):
    """Compatibility adapter: Labs file ingest into DataSource / Ingestion / Dataset lineage.

    `run_id` is the stable ML-run identity (currently equal to `id`).
    `client_status` is the coarse four-state view a client may see; fine-grained
    execution lives on `pipeline_status`.
    """

    __tablename__ = "client_lab_uploads"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_client_lab_uploads_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_client_lab_uploads_workspace_dataset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_client_lab_uploads_workspace_experiment",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_client_lab_uploads_workspace_artifact",
        ),
        CheckConstraint(
            "client_status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_client_lab_uploads_client_status",
        ),
        Index(
            "ix_client_lab_uploads_workspace_created_at",
            "workspace_id",
            desc("created_at"),
        ),
        Index(
            "ix_client_lab_uploads_workspace_status_created_at",
            "workspace_id",
            "pipeline_status",
            desc("created_at"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fields_noticed: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    has_named_fields: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional generic-upload override. Existing clients omit it and continue
    # through deterministic/semantic target inference.
    explicit_target_column: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Simple-case auto-train (admin-only; see docs/LABS_DATA_UNDERSTANDING.md).
    # queued | ingesting | analyzing | cleaning | feature_engineering |
    # preprocessing | splitting | cross_validation | training | evaluating |
    # predicting | completed | skipped | failed | not_applicable | running
    pipeline_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_applicable", server_default="not_applicable", index=True
    )
    # queued | processing | completed | failed — never a pipeline stage name.
    client_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued", index=True
    )
    pipeline_log: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=True
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped["Dataset | None"] = relationship(
        back_populates="source_uploads",
        foreign_keys="ClientLabUpload.dataset_id",
    )
    artifact: Mapped["Artifact | None"] = relationship(
        foreign_keys="ClientLabUpload.artifact_id",
    )
    data_source: Mapped["DataSource | None"] = relationship()
    ingestion_run: Mapped["IngestionRun | None"] = relationship()
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="source_upload",
        foreign_keys="WorkflowRun.source_upload_id",
    )


class MlJob(Base):
    """Durable ML work item. API persists the row; a worker claims and runs it."""

    __tablename__ = "ml_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_ml_jobs_workspace_id"),
        UniqueConstraint("job_type", "target_id", name="uq_ml_jobs_type_target"),
        UniqueConstraint("upload_id", name="uq_ml_jobs_upload_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_ml_jobs_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "upload_id"],
            ["client_lab_uploads.workspace_id", "client_lab_uploads.id"],
            name="fk_ml_jobs_workspace_upload",
        ),
        CheckConstraint(CK_ML_JOB_TYPE, name="ck_ml_jobs_type_valid"),
        CheckConstraint(CK_ML_JOB_STATUS, name="ck_ml_jobs_status_valid"),
        CheckConstraint("attempts >= 0", name="ck_ml_jobs_attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="ck_ml_jobs_max_attempts_positive"),
        Index("ix_ml_jobs_status_queued_at", "status", "queued_at"),
        Index("ix_ml_jobs_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    upload_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_lab_uploads.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    upload: Mapped["ClientLabUpload | None"] = relationship(foreign_keys="MlJob.upload_id")


class MlRunVerification(Base):
    """One persisted deterministic/OpenAI verification attempt for an ML run."""

    __tablename__ = "ml_run_verifications"
    __table_args__ = (
        CheckConstraint(
            "audit_mode IN ('routine', 'deep')",
            name="ck_ml_run_verifications_audit_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    llm_invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_invocations.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_lab_uploads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    audit_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    deterministic_status: Mapped[str] = mapped_column(String(32), nullable=False)
    deterministic_checks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    deterministic_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    llm_model: Mapped[str] = mapped_column(String(128), nullable=False)
    llm_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    redaction_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    llm_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


@event.listens_for(ClientLabUpload, "before_insert")
def _assign_run_id_and_client_status(_mapper, _connection, target: ClientLabUpload) -> None:
    if target.id is None:
        target.id = uuid.uuid4()
    target.run_id = target.id
    target.client_status = client_status_for(target.pipeline_status)


@event.listens_for(ClientLabUpload, "before_update")
def _sync_client_status(_mapper, _connection, target: ClientLabUpload) -> None:
    target.client_status = client_status_for(target.pipeline_status)


class LabDecisionRecord(Base):
    """Audit trail for every missing-value decision on a Labs upload.

    One row per feature column, including rule-engine-only calls. `rule_decision`
    is auto_prepare's original action; `final_decision` is what was applied
    (`source` is `rule`, `llm`, or `fallback`).
    """

    __tablename__ = "lab_decision_records"
    __table_args__ = (
        CheckConstraint(
            "source IN ('rule', 'llm', 'fallback')",
            name="ck_lab_decision_records_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    llm_invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_invocations.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_lab_uploads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    column: Mapped[str] = mapped_column("column", String(256), nullable=False)
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_llm_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validator_verdict: Mapped[str] = mapped_column(String(1024), nullable=False)
    rule_decision: Mapped[str] = mapped_column(String(64), nullable=False)
    final_decision: Mapped[str] = mapped_column(String(64), nullable=False)
    fill_value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Environment(Base):
    """LEGACY Lab execution container.

    Workspace and Project are the customer-facing boundaries. ``environment_id``
    remains on Dataset and Experiment because existing ML execution still
    depends on it. Do not require customers to understand this concept.
    """

    __tablename__ = "environments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, default="dclab", index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="environment")
    tasks: Mapped[list["PredictionTask"]] = relationship(back_populates="environment")


class Artifact(Base):
    """Registry metadata for a blob in object storage. Bytes are never stored here."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "object_key", name="uq_artifacts_workspace_object_key"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_artifacts_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_artifacts_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_artifacts_workspace_pipeline_run",
            ondelete="SET NULL",
            use_alter=True,
        ),
        CheckConstraint(CK_ARTIFACTS_TYPE, name="ck_artifacts_type_valid"),
        CheckConstraint(CK_ARTIFACTS_PROVIDER, name="ck_artifacts_provider_valid"),
        Index("ix_artifacts_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_artifacts_project_id", "project_id"),
        Index("ix_artifacts_pipeline_run_id", "pipeline_run_id"),
        Index(
            "ix_artifacts_workspace_pipeline_run_id",
            "workspace_id",
            "pipeline_run_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "experiments.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_artifacts_pipeline_run_id",
        ),
        nullable=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str | None] = mapped_column(String(256), nullable=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="artifacts",
        foreign_keys="Artifact.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="artifacts",
        foreign_keys="Artifact.project_id",
    )
    pipeline_run: Mapped["Experiment | None"] = relationship(
        back_populates="artifacts",
        foreign_keys="Artifact.pipeline_run_id",
    )
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="artifact",
        foreign_keys="Dataset.artifact_id",
    )
    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="source_artifact",
        foreign_keys="CodeSnapshot.artifact_id",
    )
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="model_artifact",
        foreign_keys="ModelVersion.model_artifact_id",
    )


class DataSource(Base):
    """How a Project obtains data. Configuration must not contain secrets."""

    __tablename__ = "data_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_data_sources_workspace_project",
        ),
        CheckConstraint(CK_DATA_SOURCES_TYPE, name="ck_data_sources_type_valid"),
        CheckConstraint(CK_DATA_SOURCES_STATUS, name="ck_data_sources_status_valid"),
        Index("ix_data_sources_workspace_id", "workspace_id"),
        Index("ix_data_sources_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    credential_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="data_sources",
        foreign_keys="DataSource.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="data_sources",
        foreign_keys="DataSource.project_id",
    )
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        back_populates="data_source"
    )


class IngestionRun(Base):
    """One attempt to pull a DataSource into DatasetAsset / Dataset versions."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_ingestion_runs_workspace_project",
        ),
        CheckConstraint(CK_INGESTION_RUNS_STATUS, name="ck_ingestion_runs_status_valid"),
        Index("ix_ingestion_runs_workspace_id", "workspace_id"),
        Index("ix_ingestion_runs_project_id", "project_id"),
        Index("ix_ingestion_runs_data_source_id", "data_source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_read: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    schema_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="ingestion_runs",
        foreign_keys="IngestionRun.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="ingestion_runs",
        foreign_keys="IngestionRun.project_id",
    )
    data_source: Mapped[DataSource] = relationship(back_populates="ingestion_runs")
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="ingestion_run",
        foreign_keys="Dataset.ingestion_run_id",
    )


class DatasetAsset(Base):
    """Logical dataset whose immutable physical versions live in Dataset."""

    __tablename__ = "dataset_assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_dataset_assets_workspace_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_dataset_assets_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_dataset_assets_workspace_project",
        ),
        Index("ix_dataset_assets_workspace_id", "workspace_id"),
        Index("ix_dataset_assets_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="dataset_assets",
        foreign_keys="DatasetAsset.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="dataset_assets",
        foreign_keys="DatasetAsset.project_id",
    )
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="asset",
        foreign_keys="Dataset.dataset_asset_id",
    )


class Dataset(Base):
    """Immutable physical DatasetVersion of a DatasetAsset.

    Customers address data through Project. ``environment_id`` is a LEGACY Lab
    execution pointer and is not removed while existing ingest/train code needs it.
    """

    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint(
            "dataset_asset_id", "version", name="uq_datasets_asset_version"
        ),
        UniqueConstraint(
            "dataset_asset_id",
            "content_digest",
            name="uq_datasets_asset_content_digest",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_datasets_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_datasets_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_asset_id"],
            ["dataset_assets.workspace_id", "dataset_assets.id"],
            name="fk_datasets_workspace_dataset_asset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_datasets_workspace_artifact",
        ),
        Index("ix_datasets_workspace_created_at", "workspace_id", desc("created_at")),
        Index("ix_datasets_project_created_at", "project_id", desc("created_at")),
        Index("ix_datasets_ingestion_run_id", "ingestion_run_id"),
        Index("ix_datasets_artifact_id", "artifact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
    )
    dataset_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_assets.id"), nullable=False, index=True
    )
    # LEGACY: Lab execution container. Prefer project_id for customer-facing scope.
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="csv")
    location: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    schema_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    schema_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    environment: Mapped[Environment] = relationship(back_populates="datasets")
    workspace: Mapped[Workspace] = relationship(
        back_populates="datasets",
        foreign_keys="Dataset.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="datasets",
        foreign_keys="Dataset.project_id",
    )
    asset: Mapped[DatasetAsset] = relationship(
        back_populates="datasets",
        foreign_keys="Dataset.dataset_asset_id",
    )
    ingestion_run: Mapped[IngestionRun | None] = relationship(
        back_populates="datasets",
        foreign_keys="Dataset.ingestion_run_id",
    )
    artifact: Mapped[Artifact | None] = relationship(
        back_populates="datasets",
        foreign_keys="Dataset.artifact_id",
    )
    source_uploads: Mapped[list[ClientLabUpload]] = relationship(
        back_populates="dataset",
        foreign_keys="ClientLabUpload.dataset_id",
    )
    columns: Mapped[list["DatasetColumn"]] = relationship(
        back_populates="dataset",
        foreign_keys="DatasetColumn.dataset_id",
        passive_deletes=True,
    )
    profiles: Mapped[list["DatasetProfile"]] = relationship(back_populates="dataset")
    experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="dataset",
        foreign_keys="Experiment.dataset_id",
    )
    workflow_inputs: Mapped[list["WorkflowRunInput"]] = relationship(back_populates="dataset")
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="dataset",
        foreign_keys="ModelVersion.dataset_id",
    )
    model_evaluations: Mapped[list["ModelEvaluation"]] = relationship(
        back_populates="dataset",
        foreign_keys="ModelEvaluation.dataset_id",
    )
    data_quality_findings: Mapped[list["DataQualityFinding"]] = relationship(
        back_populates="dataset",
        foreign_keys="DataQualityFinding.dataset_id",
    )
    data_preparation_decisions: Mapped[list["DataPreparationDecision"]] = relationship(
        back_populates="dataset",
        foreign_keys="DataPreparationDecision.dataset_id",
    )


class DatasetColumn(Base):
    """Searchable per-column facts for a Dataset (physical DatasetVersion)."""

    __tablename__ = "dataset_columns"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_dataset_columns_dataset_name"),
        UniqueConstraint("workspace_id", "id", name="uq_dataset_columns_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_dataset_columns_workspace_dataset",
            ondelete="CASCADE",
        ),
        Index("ix_dataset_columns_dataset_id", "dataset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    physical_dtype: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unique_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cardinality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    max_value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    mean_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="dataset_columns",
        foreign_keys="DatasetColumn.workspace_id",
    )
    dataset: Mapped[Dataset] = relationship(
        back_populates="columns",
        foreign_keys="DatasetColumn.dataset_id",
    )
    data_quality_findings: Mapped[list["DataQualityFinding"]] = relationship(
        back_populates="dataset_column",
        foreign_keys="DataQualityFinding.dataset_column_id",
    )
    data_preparation_decisions: Mapped[list["DataPreparationDecision"]] = relationship(
        back_populates="dataset_column",
        foreign_keys="DataPreparationDecision.dataset_column_id",
    )
    feature_lineage: Mapped[list["FeatureLineage"]] = relationship(
        back_populates="source_dataset_column",
        foreign_keys="FeatureLineage.source_dataset_column_id",
    )


class DatasetProfile(Base):
    __tablename__ = "dataset_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False, index=True
    )
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped[Dataset] = relationship(back_populates="profiles")


class PredictionTask(Base):
    """LEGACY Lab TaskSpec container. Still required by ingest/train; not a customer Project."""
    __tablename__ = "prediction_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="binary")
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    config_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    environment: Mapped[Environment] = relationship(back_populates="tasks")
    experiments: Mapped[list["Experiment"]] = relationship(back_populates="task")


class MlWorkflow(Base):
    """Reusable business objective and configuration, not an execution."""

    __tablename__ = "ml_workflows"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_ml_workflows_workspace_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_ml_workflows_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_ml_workflows_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workspace_domain_id"],
            ["workspace_domains.workspace_id", "workspace_domains.id"],
            name="fk_ml_workflows_workspace_workspace_domain",
        ),
        Index("ix_ml_workflows_workspace_id", "workspace_id"),
        Index("ix_ml_workflows_workspace_domain_id", "workspace_domain_id"),
        Index("ix_ml_workflows_project_id", "project_id"),
        Index("ix_ml_workflows_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    workspace_domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace_domains.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    business_objective: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="ml_workflows",
        foreign_keys="MlWorkflow.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="ml_workflows",
        foreign_keys="MlWorkflow.project_id",
    )
    workspace_domain: Mapped[WorkspaceDomain] = relationship(
        back_populates="workflows",
        foreign_keys="MlWorkflow.workspace_domain_id",
    )
    versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="workflow",
        foreign_keys="WorkflowVersion.workflow_id",
    )
    pipelines: Mapped[list["Pipeline"]] = relationship(
        back_populates="workflow",
        foreign_keys="Pipeline.workflow_id",
    )
    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow",
        foreign_keys="WorkflowRun.workflow_id",
    )
    model_assets: Mapped[list["ModelAsset"]] = relationship(
        back_populates="workflow",
        foreign_keys="ModelAsset.workflow_id",
    )
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="workflow",
        foreign_keys="ModelVersion.workflow_id",
    )


class WorkflowVersion(Base):
    """Immutable-after-lock definition snapshot of an MlWorkflow."""

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
        UniqueConstraint("workspace_id", "id", name="uq_workflow_versions_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_workflow_versions_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_workflow_versions_workspace_workflow",
        ),
        CheckConstraint(CK_WORKFLOW_VERSION_POSITIVE, name="ck_workflow_versions_version_positive"),
        Index("ix_workflow_versions_workspace_id", "workspace_id"),
        Index("ix_workflow_versions_project_id", "project_id"),
        Index("ix_workflow_versions_workflow_id", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ml_workflows.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(
        back_populates="workflow_versions",
        foreign_keys="WorkflowVersion.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="workflow_versions",
        foreign_keys="WorkflowVersion.project_id",
    )
    workflow: Mapped[MlWorkflow] = relationship(
        back_populates="versions",
        foreign_keys="WorkflowVersion.workflow_id",
    )
    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow_version",
        foreign_keys="WorkflowRun.workflow_version_id",
    )
    pipeline_versions: Mapped[list["PipelineVersion"]] = relationship(
        back_populates="workflow_version",
        foreign_keys="PipelineVersion.workflow_version_id",
    )


class Pipeline(Base):
    """Reusable pipeline definition owned by a Workflow. Not an execution."""

    __tablename__ = "pipelines"
    __table_args__ = (
        UniqueConstraint("workflow_id", "slug", name="uq_pipelines_workflow_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_pipelines_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipelines_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_pipelines_workspace_workflow",
        ),
        CheckConstraint(CK_PIPELINES_STATUS, name="ck_pipelines_status_valid"),
        Index("ix_pipelines_workspace_id", "workspace_id"),
        Index("ix_pipelines_project_id", "project_id"),
        Index("ix_pipelines_workflow_id", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ml_workflows.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False, default="training")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="pipelines",
        foreign_keys="Pipeline.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="pipelines",
        foreign_keys="Pipeline.project_id",
    )
    workflow: Mapped[MlWorkflow] = relationship(
        back_populates="pipelines",
        foreign_keys="Pipeline.workflow_id",
    )
    versions: Mapped[list["PipelineVersion"]] = relationship(
        back_populates="pipeline",
        foreign_keys="PipelineVersion.pipeline_id",
    )
    pipeline_runs: Mapped[list["Experiment"]] = relationship(
        back_populates="pipeline",
        foreign_keys="Experiment.pipeline_id",
    )


class PipelineVersion(Base):
    """Immutable-after-lock graph/config snapshot of a Pipeline."""

    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "version", name="uq_pipeline_versions_pipeline_version"),
        UniqueConstraint("workspace_id", "id", name="uq_pipeline_versions_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipeline_versions_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_id"],
            ["pipelines.workspace_id", "pipelines.id"],
            name="fk_pipeline_versions_workspace_pipeline",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_version_id"],
            ["workflow_versions.workspace_id", "workflow_versions.id"],
            name="fk_pipeline_versions_workspace_workflow_version",
        ),
        CheckConstraint(CK_PIPELINE_VERSION_POSITIVE, name="ck_pipeline_versions_version_positive"),
        Index("ix_pipeline_versions_workspace_id", "workspace_id"),
        Index("ix_pipeline_versions_project_id", "project_id"),
        Index("ix_pipeline_versions_pipeline_id", "pipeline_id"),
        Index("ix_pipeline_versions_workflow_version_id", "workflow_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_definition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(
        back_populates="pipeline_versions",
        foreign_keys="PipelineVersion.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="pipeline_versions",
        foreign_keys="PipelineVersion.project_id",
    )
    pipeline: Mapped[Pipeline] = relationship(
        back_populates="versions",
        foreign_keys="PipelineVersion.pipeline_id",
    )
    workflow_version: Mapped[WorkflowVersion] = relationship(
        back_populates="pipeline_versions",
        foreign_keys="PipelineVersion.workflow_version_id",
    )
    pipeline_runs: Mapped[list["Experiment"]] = relationship(
        back_populates="pipeline_version",
        foreign_keys="Experiment.pipeline_version_id",
    )


class WorkflowRun(Base):
    """One invocation of an MlWorkflow."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_workflow_runs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_workflow_runs_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_workflow_runs_workspace_workflow",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_version_id"],
            ["workflow_versions.workspace_id", "workflow_versions.id"],
            name="fk_workflow_runs_workspace_workflow_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "problem_spec_id"],
            ["problem_specs.workspace_id", "problem_specs.id"],
            name="fk_workflow_runs_workspace_problem_spec",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_upload_id"],
            ["client_lab_uploads.workspace_id", "client_lab_uploads.id"],
            name="fk_workflow_runs_workspace_source_upload",
            ondelete="SET NULL",
            use_alter=True,
        ),
        CheckConstraint(CK_WORKFLOW_RUNS_INITIATED_BY, name="ck_workflow_runs_initiated_by_type"),
        Index(
            "ix_workflow_runs_workspace_created_at",
            "workspace_id",
            desc("created_at"),
        ),
        Index(
            "ix_workflow_runs_workspace_status_created_at",
            "workspace_id",
            "status",
            desc("created_at"),
        ),
        Index("ix_workflow_runs_project_created_at", "project_id", desc("created_at")),
        Index("ix_workflow_runs_workflow_id", "workflow_id"),
        Index("ix_workflow_runs_workflow_version_id", "workflow_version_id"),
        Index("ix_workflow_runs_problem_spec_id", "problem_spec_id"),
        Index("ix_workflow_runs_source_upload_id", "source_upload_id"),
        Index("ix_workflow_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ml_workflows.id"), nullable=False
    )
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    problem_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("problem_specs.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    initiated_by_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_upload_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "client_lab_uploads.id",
            ondelete="SET NULL",
            use_alter=True,
            name="workflow_runs_source_upload_id_fkey",
        ),
        nullable=True,
    )
    explicit_target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    resolved_target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    failure_reason: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="workflow_runs",
        foreign_keys="WorkflowRun.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="workflow_runs",
        foreign_keys="WorkflowRun.project_id",
    )
    workflow: Mapped[MlWorkflow] = relationship(
        back_populates="runs",
        foreign_keys="WorkflowRun.workflow_id",
    )
    workflow_version: Mapped[WorkflowVersion | None] = relationship(
        back_populates="runs",
        foreign_keys="WorkflowRun.workflow_version_id",
    )
    problem_spec: Mapped[ProblemSpec | None] = relationship(
        foreign_keys="WorkflowRun.problem_spec_id",
    )
    source_upload: Mapped[ClientLabUpload | None] = relationship(
        back_populates="workflow_runs",
        foreign_keys="WorkflowRun.source_upload_id",
    )
    inputs: Mapped[list["WorkflowRunInput"]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan"
    )
    pipeline_runs: Mapped[list["Experiment"]] = relationship(
        back_populates="workflow_run",
        foreign_keys="Experiment.workflow_run_id",
    )
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="workflow_run",
        foreign_keys="ModelVersion.workflow_run_id",
    )
    events: Mapped[list["MlRunEvent"]] = relationship(
        back_populates="workflow_run",
        foreign_keys="MlRunEvent.workflow_run_id",
    )
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(
        back_populates="workflow_run",
        foreign_keys="LlmInvocation.workflow_run_id",
    )


class WorkflowRunInput(Base):
    __tablename__ = "workflow_run_inputs"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "dataset_id",
            "input_role",
            name="uq_workflow_run_inputs_run_dataset_role",
        ),
        Index("ix_workflow_run_inputs_workflow_run_id", "workflow_run_id"),
        Index("ix_workflow_run_inputs_dataset_id", "dataset_id"),
        Index("ix_workflow_run_inputs_input_role", "input_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False
    )
    input_role: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="inputs")
    dataset: Mapped[Dataset] = relationship(back_populates="workflow_inputs")


class Experiment(Base):
    """Physical PipelineRun. Table stays ``experiments`` during the compatibility window."""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "pipeline_index",
            name="uq_experiments_workflow_run_pipeline_index",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_experiments_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_experiments_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_id"],
            ["pipelines.workspace_id", "pipelines.id"],
            name="fk_experiments_workspace_pipeline",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_version_id"],
            ["pipeline_versions.workspace_id", "pipeline_versions.id"],
            name="fk_experiments_workspace_pipeline_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_run_id"],
            ["workflow_runs.workspace_id", "workflow_runs.id"],
            name="fk_experiments_workspace_workflow_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_experiments_workspace_dataset",
        ),
        Index(
            "ix_experiments_workspace_created_at",
            "workspace_id",
            desc("created_at"),
        ),
        Index(
            "ix_experiments_workspace_status_created_at",
            "workspace_id",
            "status",
            desc("created_at"),
        ),
        Index("ix_experiments_project_created_at", "project_id", desc("created_at")),
        Index("ix_experiments_workflow_run_created_at", "workflow_run_id", "created_at"),
        Index("ix_experiments_pipeline_id", "pipeline_id"),
        Index("ix_experiments_pipeline_version_id", "pipeline_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=True
    )
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pipeline_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="deterministic_ml", server_default="deterministic_ml"
    )
    pipeline_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    pipeline_purpose: Mapped[str] = mapped_column(
        String(128), nullable=False, default="training", server_default="training"
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prediction_tasks.id"), nullable=True, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED", index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifact_dir: Mapped[str | None] = mapped_column(String(512), nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped[Dataset] = relationship(
        back_populates="experiments",
        foreign_keys="Experiment.dataset_id",
    )
    workspace: Mapped[Workspace] = relationship(
        back_populates="pipeline_runs",
        foreign_keys="Experiment.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="pipeline_runs",
        foreign_keys="Experiment.project_id",
    )
    workflow_run: Mapped[WorkflowRun | None] = relationship(
        back_populates="pipeline_runs",
        foreign_keys="Experiment.workflow_run_id",
    )
    pipeline: Mapped[Pipeline | None] = relationship(
        back_populates="pipeline_runs",
        foreign_keys="Experiment.pipeline_id",
    )
    pipeline_version: Mapped[PipelineVersion | None] = relationship(
        back_populates="pipeline_runs",
        foreign_keys="Experiment.pipeline_version_id",
    )
    task: Mapped[PredictionTask | None] = relationship(back_populates="experiments")
    candidates: Mapped[list["ExperimentCandidate"]] = relationship(
        back_populates="experiment",
        foreign_keys="ExperimentCandidate.experiment_id",
        passive_deletes=True,
    )
    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="CodeSnapshot.pipeline_run_id",
        passive_deletes=True,
    )
    test_predictions: Mapped[list["ExperimentTestPrediction"]] = relationship(back_populates="experiment")
    model_version: Mapped["ModelVersion | None"] = relationship(
        back_populates="pipeline_run",
        uselist=False,
        foreign_keys="ModelVersion.pipeline_run_id",
    )
    events: Mapped[list["MlRunEvent"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="MlRunEvent.experiment_id",
    )
    stage_runs: Mapped[list["PipelineStageRun"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="PipelineStageRun.pipeline_run_id",
        passive_deletes=True,
    )
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="LlmInvocation.experiment_id",
    )
    data_quality_findings: Mapped[list["DataQualityFinding"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="DataQualityFinding.pipeline_run_id",
        passive_deletes=True,
    )
    data_preparation_decisions: Mapped[list["DataPreparationDecision"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="DataPreparationDecision.pipeline_run_id",
        passive_deletes=True,
    )
    preprocessing_steps: Mapped[list["PreprocessingStep"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="PreprocessingStep.pipeline_run_id",
        passive_deletes=True,
    )
    model_selection_decisions: Mapped[list["ModelSelectionDecision"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="ModelSelectionDecision.pipeline_run_id",
        passive_deletes=True,
    )
    scientific_plan: Mapped["PipelineScientificPlan | None"] = relationship(
        back_populates="pipeline_run",
        uselist=False,
        foreign_keys="PipelineScientificPlan.pipeline_run_id",
        passive_deletes=True,
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="pipeline_run",
        foreign_keys="Artifact.pipeline_run_id",
    )


class PipelineScientificPlan(Base):
    """One locked scientific plan per PipelineRun.

    Queryable holdout/validation/metric facts. ``full_plan`` JSONB is compatibility
    evidence beside the columns, not the explorer source of truth.
    """

    __tablename__ = "pipeline_scientific_plans"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", name="uq_pipeline_scientific_plans_pipeline_run"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_pipeline_scientific_plans_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipeline_scientific_plans_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_pipeline_scientific_plans_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        Index("ix_pipeline_scientific_plans_workspace_id", "workspace_id"),
        Index("ix_pipeline_scientific_plans_project_id", "project_id"),
        Index("ix_pipeline_scientific_plans_pipeline_run_id", "pipeline_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    holdout_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    holdout_test_size: Mapped[float] = mapped_column(Float, nullable=False)
    validation_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_folds: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_folds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_metric: Mapped[str] = mapped_column(String(64), nullable=False)
    group_column: Mapped[str | None] = mapped_column(String(256), nullable=True)
    time_column: Mapped[str | None] = mapped_column(String(256), nullable=True)
    allowed_feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    holdout_plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_development_plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    full_plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="scientific_plans",
        foreign_keys="PipelineScientificPlan.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="scientific_plans",
        foreign_keys="PipelineScientificPlan.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="scientific_plan",
        foreign_keys="PipelineScientificPlan.pipeline_run_id",
    )


class PipelineStageRun(Base):
    """Queryable stage state for a physical PipelineRun (Experiment). Events remain the timeline."""

    __tablename__ = "pipeline_stage_runs"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "sequence", name="uq_pipeline_stage_runs_run_sequence"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_pipeline_stage_runs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_pipeline_stage_runs_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_pipeline_stage_runs_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            CK_PIPELINE_STAGE_RUNS_STATUS, name="ck_pipeline_stage_runs_status_valid"
        ),
        Index("ix_pipeline_stage_runs_workspace_id", "workspace_id"),
        Index("ix_pipeline_stage_runs_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    stage_type: Mapped[str] = mapped_column(String(64), nullable=False, default="execution")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="pipeline_stage_runs",
        foreign_keys="PipelineStageRun.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="pipeline_stage_runs",
        foreign_keys="PipelineStageRun.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="stage_runs",
        foreign_keys="PipelineStageRun.pipeline_run_id",
    )
    data_quality_findings: Mapped[list["DataQualityFinding"]] = relationship(
        back_populates="pipeline_stage_run",
        foreign_keys="DataQualityFinding.pipeline_stage_run_id",
    )
    data_preparation_decisions: Mapped[list["DataPreparationDecision"]] = relationship(
        back_populates="pipeline_stage_run",
        foreign_keys="DataPreparationDecision.pipeline_stage_run_id",
    )
    preprocessing_steps: Mapped[list["PreprocessingStep"]] = relationship(
        back_populates="pipeline_stage_run",
        foreign_keys="PreprocessingStep.pipeline_stage_run_id",
    )
    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="pipeline_stage_run",
        foreign_keys="CodeSnapshot.pipeline_stage_run_id",
    )


class DataQualityFinding(Base):
    """Queryable data-quality fact for a PipelineRun. Events remain the timeline."""

    __tablename__ = "data_quality_findings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_data_quality_findings_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_data_quality_findings_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_data_quality_findings_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_data_quality_findings_workspace_dataset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_data_quality_findings_workspace_pipeline_stage_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_column_id"],
            ["dataset_columns.workspace_id", "dataset_columns.id"],
            name="fk_data_quality_findings_workspace_dataset_column",
        ),
        CheckConstraint(
            CK_DATA_QUALITY_FINDING_TYPE, name="ck_data_quality_findings_type_valid"
        ),
        CheckConstraint(
            CK_DATA_QUALITY_SEVERITY, name="ck_data_quality_findings_severity_valid"
        ),
        Index("ix_data_quality_findings_workspace_id", "workspace_id"),
        Index("ix_data_quality_findings_project_id", "project_id"),
        Index("ix_data_quality_findings_pipeline_run_id", "pipeline_run_id"),
        Index("ix_data_quality_findings_dataset_id", "dataset_id"),
        Index("ix_data_quality_findings_dataset_column_id", "dataset_column_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_stage_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False
    )
    dataset_column_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_columns.id", ondelete="SET NULL"), nullable=True
    )
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="data_quality_findings",
        foreign_keys="DataQualityFinding.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="data_quality_findings",
        foreign_keys="DataQualityFinding.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="data_quality_findings",
        foreign_keys="DataQualityFinding.pipeline_run_id",
    )
    pipeline_stage_run: Mapped[PipelineStageRun | None] = relationship(
        back_populates="data_quality_findings",
        foreign_keys="DataQualityFinding.pipeline_stage_run_id",
    )
    dataset: Mapped[Dataset] = relationship(
        back_populates="data_quality_findings",
        foreign_keys="DataQualityFinding.dataset_id",
    )
    dataset_column: Mapped[DatasetColumn | None] = relationship(
        back_populates="data_quality_findings",
        foreign_keys="DataQualityFinding.dataset_column_id",
    )


class DataPreparationDecision(Base):
    """Canonical preparation decision. LabDecisionRecord remains compatibility audit evidence."""

    __tablename__ = "data_preparation_decisions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_data_preparation_decisions_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_data_preparation_decisions_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_data_preparation_decisions_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_data_preparation_decisions_workspace_dataset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_data_preparation_decisions_workspace_pipeline_stage_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_column_id"],
            ["dataset_columns.workspace_id", "dataset_columns.id"],
            name="fk_data_preparation_decisions_workspace_dataset_column",
        ),
        CheckConstraint(
            CK_PREPARATION_DECISION_SOURCE,
            name="ck_data_preparation_decisions_source_valid",
        ),
        Index("ix_data_preparation_decisions_workspace_id", "workspace_id"),
        Index("ix_data_preparation_decisions_project_id", "project_id"),
        Index("ix_data_preparation_decisions_pipeline_run_id", "pipeline_run_id"),
        Index("ix_data_preparation_decisions_dataset_id", "dataset_id"),
        Index("ix_data_preparation_decisions_dataset_column_id", "dataset_column_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_stage_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False
    )
    dataset_column_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_columns.id", ondelete="SET NULL"), nullable=True
    )
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decision_source: Mapped[str] = mapped_column(String(32), nullable=False, default="deterministic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="data_preparation_decisions",
        foreign_keys="DataPreparationDecision.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="data_preparation_decisions",
        foreign_keys="DataPreparationDecision.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="data_preparation_decisions",
        foreign_keys="DataPreparationDecision.pipeline_run_id",
    )
    pipeline_stage_run: Mapped[PipelineStageRun | None] = relationship(
        back_populates="data_preparation_decisions",
        foreign_keys="DataPreparationDecision.pipeline_stage_run_id",
    )
    dataset: Mapped[Dataset] = relationship(
        back_populates="data_preparation_decisions",
        foreign_keys="DataPreparationDecision.dataset_id",
    )
    dataset_column: Mapped[DatasetColumn | None] = relationship(
        back_populates="data_preparation_decisions",
        foreign_keys="DataPreparationDecision.dataset_column_id",
    )


class FeatureSet(Base):
    """Logical reusable feature collection owned by a Project."""

    __tablename__ = "feature_sets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_feature_sets_workspace_name"),
        UniqueConstraint("workspace_id", "id", name="uq_feature_sets_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_feature_sets_workspace_project",
        ),
        Index("ix_feature_sets_workspace_id", "workspace_id"),
        Index("ix_feature_sets_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="feature_sets",
        foreign_keys="FeatureSet.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="feature_sets",
        foreign_keys="FeatureSet.project_id",
    )
    versions: Mapped[list["FeatureSetVersion"]] = relationship(
        back_populates="feature_set",
        foreign_keys="FeatureSetVersion.feature_set_id",
    )


class FeatureSetVersion(Base):
    """Immutable-after-lock snapshot of a FeatureSet."""

    __tablename__ = "feature_set_versions"
    __table_args__ = (
        UniqueConstraint(
            "feature_set_id", "version", name="uq_feature_set_versions_set_version"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_feature_set_versions_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_feature_set_versions_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "feature_set_id"],
            ["feature_sets.workspace_id", "feature_sets.id"],
            name="fk_feature_set_versions_workspace_feature_set",
        ),
        CheckConstraint(
            CK_FEATURE_SET_VERSION_POSITIVE, name="ck_feature_set_versions_version_positive"
        ),
        Index("ix_feature_set_versions_workspace_id", "workspace_id"),
        Index("ix_feature_set_versions_project_id", "project_id"),
        Index("ix_feature_set_versions_feature_set_id", "feature_set_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    feature_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feature_sets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(
        back_populates="feature_set_versions",
        foreign_keys="FeatureSetVersion.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="feature_set_versions",
        foreign_keys="FeatureSetVersion.project_id",
    )
    feature_set: Mapped[FeatureSet] = relationship(
        back_populates="versions",
        foreign_keys="FeatureSetVersion.feature_set_id",
    )
    features: Mapped[list["Feature"]] = relationship(
        back_populates="feature_set_version",
        foreign_keys="Feature.feature_set_version_id",
        passive_deletes=True,
    )
    candidates: Mapped[list["ExperimentCandidate"]] = relationship(
        back_populates="feature_set_version",
        foreign_keys="ExperimentCandidate.feature_set_version_id",
    )
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="feature_set_version",
        foreign_keys="ModelVersion.feature_set_version_id",
        viewonly=True,
    )


class Feature(Base):
    """Named feature inside a locked FeatureSetVersion."""

    __tablename__ = "features"
    __table_args__ = (
        UniqueConstraint(
            "feature_set_version_id", "name", name="uq_features_version_name"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_features_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_features_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "feature_set_version_id"],
            ["feature_set_versions.workspace_id", "feature_set_versions.id"],
            name="fk_features_workspace_feature_set_version",
        ),
        CheckConstraint(CK_FEATURE_STATUS, name="ck_features_status_valid"),
        Index("ix_features_workspace_id", "workspace_id"),
        Index("ix_features_project_id", "project_id"),
        Index("ix_features_feature_set_version_id", "feature_set_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    feature_set_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feature_set_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    feature_type: Mapped[str] = mapped_column(String(64), nullable=False, default="numeric")
    output_dtype: Mapped[str] = mapped_column(String(64), nullable=False, default="float64")
    definition: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="modeled")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="features",
        foreign_keys="Feature.workspace_id",
    )
    project: Mapped[Project] = relationship(
        back_populates="features",
        foreign_keys="Feature.project_id",
    )
    feature_set_version: Mapped[FeatureSetVersion] = relationship(
        back_populates="features",
        foreign_keys="Feature.feature_set_version_id",
    )
    transformations: Mapped[list["FeatureTransformation"]] = relationship(
        back_populates="feature",
        foreign_keys="FeatureTransformation.feature_id",
        passive_deletes=True,
    )
    lineage: Mapped[list["FeatureLineage"]] = relationship(
        back_populates="feature",
        foreign_keys="FeatureLineage.feature_id",
        passive_deletes=True,
    )


class FeatureTransformation(Base):
    """Ordered transform that produced a Feature."""

    __tablename__ = "feature_transformations"
    __table_args__ = (
        UniqueConstraint(
            "feature_id", "sequence", name="uq_feature_transformations_feature_sequence"
        ),
        Index("ix_feature_transformations_feature_id", "feature_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    feature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("features.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transformation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    transformer_class: Mapped[str | None] = mapped_column(String(256), nullable=True)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fit_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    feature: Mapped[Feature] = relationship(
        back_populates="transformations",
        foreign_keys="FeatureTransformation.feature_id",
    )


class FeatureLineage(Base):
    """Source DatasetColumn inputs that produced a Feature.

    Uniqueness is the composite primary key ``(feature_id, source_dataset_column_id)``.
    """

    __tablename__ = "feature_lineage"
    __table_args__ = (
        CheckConstraint(
            CK_FEATURE_LINEAGE_RELATIONSHIP, name="ck_feature_lineage_relationship_valid"
        ),
        Index("ix_feature_lineage_feature_id", "feature_id"),
        Index("ix_feature_lineage_source_dataset_column_id", "source_dataset_column_id"),
    )

    feature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("features.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_dataset_column_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_columns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    feature: Mapped[Feature] = relationship(
        back_populates="lineage",
        foreign_keys="FeatureLineage.feature_id",
    )
    source_dataset_column: Mapped[DatasetColumn] = relationship(
        back_populates="feature_lineage",
        foreign_keys="FeatureLineage.source_dataset_column_id",
    )
    relationship: Mapped[str] = mapped_column(
        String(32), nullable=False, default="source", server_default="source"
    )


class PreprocessingStep(Base):
    """Actual preprocessor step fitted during a PipelineRun."""

    __tablename__ = "preprocessing_steps"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "sequence", name="uq_preprocessing_steps_run_sequence"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_preprocessing_steps_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_preprocessing_steps_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_preprocessing_steps_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_preprocessing_steps_workspace_pipeline_stage_run",
        ),
        CheckConstraint(
            CK_PREPROCESSING_FIT_SCOPE, name="ck_preprocessing_steps_fit_scope_valid"
        ),
        Index("ix_preprocessing_steps_workspace_id", "workspace_id"),
        Index("ix_preprocessing_steps_project_id", "project_id"),
        Index("ix_preprocessing_steps_pipeline_run_id", "pipeline_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_stage_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    column_scope: Mapped[str] = mapped_column(String(2048), nullable=False)
    transformer_type: Mapped[str] = mapped_column(String(64), nullable=False)
    transformer_class: Mapped[str] = mapped_column(String(256), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fit_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="preprocessing_steps",
        foreign_keys="PreprocessingStep.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="preprocessing_steps",
        foreign_keys="PreprocessingStep.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="preprocessing_steps",
        foreign_keys="PreprocessingStep.pipeline_run_id",
    )
    pipeline_stage_run: Mapped[PipelineStageRun | None] = relationship(
        back_populates="preprocessing_steps",
        foreign_keys="PreprocessingStep.pipeline_stage_run_id",
    )


class ExperimentCandidate(Base):
    """Physical trained-candidate row. payload JSONB stays as compatibility evidence."""

    __tablename__ = "experiment_candidates"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "fingerprint", name="uq_experiment_candidates_experiment_fingerprint"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_experiment_candidates_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_experiment_candidates_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_experiment_candidates_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "feature_set_version_id"],
            ["feature_set_versions.workspace_id", "feature_set_versions.id"],
            name="fk_experiment_candidates_workspace_feature_set_version",
        ),
        Index("ix_experiment_candidates_workspace_id", "workspace_id"),
        Index("ix_experiment_candidates_project_id", "project_id"),
        Index("ix_experiment_candidates_model_family", "model_family"),
        Index("ix_experiment_candidates_feature_set_version_id", "feature_set_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(256), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generated")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model_family: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    implementation_library: Mapped[str | None] = mapped_column(String(64), nullable=True)
    implementation_class: Mapped[str | None] = mapped_column(String(256), nullable=True)
    library_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    search_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trial_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_set_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feature_set_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="experiment_candidates",
        foreign_keys="ExperimentCandidate.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="experiment_candidates",
        foreign_keys="ExperimentCandidate.project_id",
    )
    experiment: Mapped[Experiment] = relationship(
        back_populates="candidates",
        foreign_keys="ExperimentCandidate.experiment_id",
    )
    feature_set_version: Mapped[FeatureSetVersion | None] = relationship(
        back_populates="candidates",
        foreign_keys="ExperimentCandidate.feature_set_version_id",
    )
    model_version: Mapped["ModelVersion | None"] = relationship(
        back_populates="selected_candidate",
        uselist=False,
        foreign_keys="ModelVersion.selected_candidate_id",
    )
    hyperparameters: Mapped[list["ModelHyperparameter"]] = relationship(
        back_populates="candidate",
        foreign_keys="ModelHyperparameter.candidate_id",
        passive_deletes=True,
    )
    cv_fold_runs: Mapped[list["CVFoldRun"]] = relationship(
        back_populates="candidate",
        foreign_keys="CVFoldRun.candidate_id",
        passive_deletes=True,
    )
    evaluations: Mapped[list["ModelEvaluation"]] = relationship(
        back_populates="candidate",
        foreign_keys="ModelEvaluation.candidate_id",
        passive_deletes=True,
    )
    selected_in: Mapped[list["ModelSelectionDecision"]] = relationship(
        back_populates="selected_candidate",
        foreign_keys="ModelSelectionDecision.selected_candidate_id",
    )
    runner_up_in: Mapped[list["ModelSelectionDecision"]] = relationship(
        back_populates="runner_up_candidate",
        foreign_keys="ModelSelectionDecision.runner_up_candidate_id",
    )
    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="candidate",
        foreign_keys="CodeSnapshot.candidate_id",
    )


class ModelHyperparameter(Base):
    """One named hyperparameter actually applied to a candidate."""

    __tablename__ = "model_hyperparameters"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "parameter_name", name="uq_model_hyperparameters_candidate_name"
        ),
        CheckConstraint(CK_HYPERPARAMETER_SOURCE, name="ck_model_hyperparameters_source_valid"),
        Index("ix_model_hyperparameters_parameter_name", "parameter_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    parameter_name: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[object] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[ExperimentCandidate] = relationship(
        back_populates="hyperparameters",
        foreign_keys="ModelHyperparameter.candidate_id",
    )


class CVFoldRun(Base):
    """One CV fold actually fitted for a candidate. Never holdout/test evidence."""

    __tablename__ = "cv_fold_runs"
    __table_args__ = (
        UniqueConstraint("candidate_id", "fold_number", name="uq_cv_fold_runs_candidate_fold"),
        UniqueConstraint("workspace_id", "id", name="uq_cv_fold_runs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_cv_fold_runs_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_cv_fold_runs_workspace_candidate",
            ondelete="CASCADE",
        ),
        CheckConstraint(CK_CV_FOLD_RUN_STATUS, name="ck_cv_fold_runs_status_valid"),
        Index("ix_cv_fold_runs_workspace_id", "workspace_id"),
        Index("ix_cv_fold_runs_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    fold_number: Mapped[int] = mapped_column(Integer, nullable=False)
    train_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    train_group_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_group_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    train_time_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    train_time_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_time_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validation_time_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="cv_fold_runs",
        foreign_keys="CVFoldRun.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="cv_fold_runs",
        foreign_keys="CVFoldRun.project_id",
    )
    candidate: Mapped[ExperimentCandidate] = relationship(
        back_populates="cv_fold_runs",
        foreign_keys="CVFoldRun.candidate_id",
    )


class ModelEvaluation(Base):
    """One evaluation bundle for a candidate or published ModelVersion."""

    __tablename__ = "model_evaluations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_model_evaluations_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_model_evaluations_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_model_evaluations_workspace_dataset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_model_evaluations_workspace_candidate",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_version_id"],
            ["model_versions.workspace_id", "model_versions.id"],
            name="fk_model_evaluations_workspace_model_version",
        ),
        CheckConstraint(CK_MODEL_EVALUATION_TYPE, name="ck_model_evaluations_type_valid"),
        CheckConstraint(CK_MODEL_EVALUATION_SCOPE, name="ck_model_evaluations_scope_valid"),
        CheckConstraint(CK_MODEL_EVALUATION_STATUS, name="ck_model_evaluations_status_valid"),
        CheckConstraint(
            "candidate_id IS NOT NULL OR model_version_id IS NOT NULL",
            name="ck_model_evaluations_subject_present",
        ),
        Index("ix_model_evaluations_workspace_id", "workspace_id"),
        Index("ix_model_evaluations_project_id", "project_id"),
        Index("ix_model_evaluations_candidate_id", "candidate_id"),
        Index("ix_model_evaluations_model_version_id", "model_version_id"),
        Index("ix_model_evaluations_dataset_id", "dataset_id"),
        Index("ix_model_evaluations_scope", "evaluation_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
        nullable=True,
    )
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    evaluation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="model_evaluations",
        foreign_keys="ModelEvaluation.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="model_evaluations",
        foreign_keys="ModelEvaluation.project_id",
    )
    candidate: Mapped[ExperimentCandidate | None] = relationship(
        back_populates="evaluations",
        foreign_keys="ModelEvaluation.candidate_id",
    )
    model_version: Mapped["ModelVersion | None"] = relationship(
        back_populates="evaluations",
        foreign_keys="ModelEvaluation.model_version_id",
    )
    dataset: Mapped[Dataset] = relationship(
        back_populates="model_evaluations",
        foreign_keys="ModelEvaluation.dataset_id",
    )
    metrics: Mapped[list["EvaluationMetric"]] = relationship(
        back_populates="model_evaluation",
        foreign_keys="EvaluationMetric.model_evaluation_id",
        passive_deletes=True,
    )


class EvaluationMetric(Base):
    """One named metric from a ModelEvaluation. JSON metric blobs remain compatibility."""

    __tablename__ = "evaluation_metrics"
    __table_args__ = (
        UniqueConstraint(
            "model_evaluation_id",
            "metric_name",
            name="uq_evaluation_metrics_evaluation_name",
        ),
        Index("ix_evaluation_metrics_name_value", "metric_name", "metric_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_evaluations.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_evaluation: Mapped[ModelEvaluation] = relationship(
        back_populates="metrics",
        foreign_keys="EvaluationMetric.model_evaluation_id",
    )


class ModelSelectionDecision(Base):
    """Canonical winner-lock for a PipelineRun. CV-only; one row per run."""

    __tablename__ = "model_selection_decisions"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", name="uq_model_selection_decisions_pipeline_run"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_model_selection_decisions_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_model_selection_decisions_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_model_selection_decisions_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "selected_candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_model_selection_decisions_workspace_selected_candidate",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "runner_up_candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_model_selection_decisions_workspace_runner_up_candidate",
        ),
        Index("ix_model_selection_decisions_workspace_id", "workspace_id"),
        Index("ix_model_selection_decisions_project_id", "project_id"),
        Index("ix_model_selection_decisions_pipeline_run_id", "pipeline_run_id"),
        Index("ix_model_selection_decisions_selected_candidate_id", "selected_candidate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    selected_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    selection_metric: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_score: Mapped[float] = mapped_column(Float, nullable=False)
    selection_policy: Mapped[str] = mapped_column(String(256), nullable=False)
    runner_up_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="model_selection_decisions",
        foreign_keys="ModelSelectionDecision.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="model_selection_decisions",
        foreign_keys="ModelSelectionDecision.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="model_selection_decisions",
        foreign_keys="ModelSelectionDecision.pipeline_run_id",
    )
    selected_candidate: Mapped[ExperimentCandidate] = relationship(
        back_populates="selected_in",
        foreign_keys="ModelSelectionDecision.selected_candidate_id",
    )
    runner_up_candidate: Mapped[ExperimentCandidate | None] = relationship(
        back_populates="runner_up_in",
        foreign_keys="ModelSelectionDecision.runner_up_candidate_id",
    )


class ExperimentTestPrediction(Base):
    """Holdout-test scores for one Labs experiment. Not opportunity scoring."""

    __tablename__ = "experiment_test_predictions"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "row_index",
            name="uq_experiment_test_predictions_experiment_row",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    predicted_value: Mapped[object] = mapped_column(JSONB, nullable=False)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    y_true: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    experiment: Mapped[Experiment] = relationship(back_populates="test_predictions")


class RuntimeEnvironment(Base):
    """Globally reusable interpreter / OS / dependency fingerprint.

    Deduplicated by ``environment_digest``. Workspace-owned lockfile bytes live
    on ``CodeSnapshot.dependency_lock_artifact_id``, not here.
    """

    __tablename__ = "runtime_environments"
    __table_args__ = (
        UniqueConstraint("environment_digest", name="uq_runtime_environments_digest"),
        Index("ix_runtime_environments_python_version", "python_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    python_version: Mapped[str] = mapped_column(String(32), nullable=False)
    os_name: Mapped[str] = mapped_column(String(64), nullable=False)
    os_version: Mapped[str] = mapped_column(String(128), nullable=False)
    architecture: Mapped[str] = mapped_column(String(64), nullable=False)
    container_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    container_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hardware: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    environment_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    code_snapshots: Mapped[list["CodeSnapshot"]] = relationship(
        back_populates="runtime_environment",
        foreign_keys="CodeSnapshot.runtime_environment_id",
    )
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="runtime_environment",
        foreign_keys="ModelVersion.runtime_environment_id",
    )


class CodeSnapshot(Base):
    """Queryable source package and workspace-owned lockfile for a PipelineRun."""

    __tablename__ = "code_snapshots"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", name="uq_code_snapshots_pipeline_run"),
        UniqueConstraint("workspace_id", "id", name="uq_code_snapshots_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_code_snapshots_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_code_snapshots_workspace_pipeline_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_stage_run_id"],
            ["pipeline_stage_runs.workspace_id", "pipeline_stage_runs.id"],
            name="fk_code_snapshots_workspace_pipeline_stage_run",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_code_snapshots_workspace_candidate",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_code_snapshots_workspace_artifact",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dependency_lock_artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_code_snapshots_workspace_dependency_lock_artifact",
        ),
        CheckConstraint(CK_CODE_LANGUAGE, name="ck_code_snapshots_language_valid"),
        Index("ix_code_snapshots_workspace_id", "workspace_id"),
        Index("ix_code_snapshots_project_id", "project_id"),
        Index("ix_code_snapshots_artifact_id", "artifact_id"),
        Index(
            "ix_code_snapshots_dependency_lock_artifact_id",
            "dependency_lock_artifact_id",
        ),
        Index("ix_code_snapshots_runtime_environment_id", "runtime_environment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_stage_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_stage_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="python")
    entrypoint: Mapped[str] = mapped_column(String(256), nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_lock_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dependency_lock_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runtime_environments.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.workspace_id",
    )
    project: Mapped[Project | None] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.project_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.pipeline_run_id",
    )
    pipeline_stage_run: Mapped[PipelineStageRun | None] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.pipeline_stage_run_id",
    )
    candidate: Mapped[ExperimentCandidate | None] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.candidate_id",
    )
    source_artifact: Mapped[Artifact] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.artifact_id",
    )
    dependency_lock_artifact: Mapped[Artifact | None] = relationship(
        foreign_keys="CodeSnapshot.dependency_lock_artifact_id",
    )
    runtime_environment: Mapped[RuntimeEnvironment] = relationship(
        back_populates="code_snapshots",
        foreign_keys="CodeSnapshot.runtime_environment_id",
    )
    model_versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="code_snapshot",
        foreign_keys="ModelVersion.code_snapshot_id",
    )


class ModelAsset(Base):
    """Logical managed model whose immutable releases are ModelVersion rows."""

    __tablename__ = "model_assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_model_assets_workspace_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_model_assets_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_model_assets_workspace_workflow",
        ),
        Index("ix_model_assets_workflow_id", "workflow_id"),
        Index("ix_model_assets_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ml_workflows.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workspace: Mapped[Workspace] = relationship(back_populates="model_assets")
    workflow: Mapped[MlWorkflow] = relationship(
        back_populates="model_assets",
        foreign_keys="ModelAsset.workflow_id",
    )
    versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="model_asset",
        foreign_keys="ModelVersion.model_asset_id",
    )


class ModelVersion(Base):
    """Append-only selected model release with complete lineage."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_asset_id", "version", name="uq_model_versions_asset_version"),
        UniqueConstraint("pipeline_run_id", name="uq_model_versions_pipeline_run_id"),
        UniqueConstraint(
            "selected_candidate_id", name="uq_model_versions_selected_candidate_id"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_model_versions_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_model_versions_workspace_project",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_version_id"],
            ["workflow_versions.workspace_id", "workflow_versions.id"],
            name="fk_model_versions_workspace_workflow_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_id"],
            ["pipelines.workspace_id", "pipelines.id"],
            name="fk_model_versions_workspace_pipeline",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_version_id"],
            ["pipeline_versions.workspace_id", "pipeline_versions.id"],
            name="fk_model_versions_workspace_pipeline_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "feature_set_version_id"],
            ["feature_set_versions.workspace_id", "feature_set_versions.id"],
            name="fk_model_versions_workspace_feature_set_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "code_snapshot_id"],
            ["code_snapshots.workspace_id", "code_snapshots.id"],
            name="fk_model_versions_workspace_code_snapshot",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_model_versions_workspace_model_artifact",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "preprocessor_artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_model_versions_workspace_preprocessor_artifact",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "feature_manifest_artifact_id"],
            ["artifacts.workspace_id", "artifacts.id"],
            name="fk_model_versions_workspace_feature_manifest_artifact",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["ml_workflows.workspace_id", "ml_workflows.id"],
            name="fk_model_versions_workspace_workflow",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_run_id"],
            ["workflow_runs.workspace_id", "workflow_runs.id"],
            name="fk_model_versions_workspace_workflow_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pipeline_run_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_model_versions_workspace_pipeline_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "selected_candidate_id"],
            ["experiment_candidates.workspace_id", "experiment_candidates.id"],
            name="fk_model_versions_workspace_selected_candidate",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_model_versions_workspace_dataset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_asset_id"],
            ["model_assets.workspace_id", "model_assets.id"],
            name="fk_model_versions_workspace_model_asset",
        ),
        Index("ix_model_versions_workspace_id", "workspace_id"),
        Index("ix_model_versions_project_id", "project_id"),
        Index("ix_model_versions_workflow_id", "workflow_id"),
        Index("ix_model_versions_workflow_run_id", "workflow_run_id"),
        Index("ix_model_versions_dataset_id", "dataset_id"),
        Index("ix_model_versions_model_artifact_id", "model_artifact_id"),
        Index("ix_model_versions_runtime_environment_id", "runtime_environment_id"),
        Index("ix_model_versions_code_snapshot_id", "code_snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    model_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_assets.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ml_workflows.id"), nullable=False
    )
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=False
    )
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    selected_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiment_candidates.id"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False
    )
    feature_set_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feature_set_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runtime_environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    code_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("code_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    preprocessor_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    feature_manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_asset: Mapped[ModelAsset] = relationship(
        back_populates="versions",
        foreign_keys="ModelVersion.model_asset_id",
    )
    workspace: Mapped[Workspace] = relationship(back_populates="model_versions")
    project: Mapped[Project | None] = relationship(
        foreign_keys="ModelVersion.project_id",
    )
    workflow: Mapped[MlWorkflow] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.workflow_id",
    )
    workflow_version: Mapped[WorkflowVersion | None] = relationship(
        foreign_keys="ModelVersion.workflow_version_id",
    )
    workflow_run: Mapped[WorkflowRun] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.workflow_run_id",
    )
    pipeline: Mapped[Pipeline | None] = relationship(
        foreign_keys="ModelVersion.pipeline_id",
    )
    pipeline_version: Mapped[PipelineVersion | None] = relationship(
        foreign_keys="ModelVersion.pipeline_version_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="model_version",
        foreign_keys="ModelVersion.pipeline_run_id",
    )
    selected_candidate: Mapped[ExperimentCandidate] = relationship(
        back_populates="model_version",
        foreign_keys="ModelVersion.selected_candidate_id",
    )
    dataset: Mapped[Dataset] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.dataset_id",
    )
    feature_set_version: Mapped[FeatureSetVersion | None] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.feature_set_version_id",
    )
    runtime_environment: Mapped[RuntimeEnvironment | None] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.runtime_environment_id",
    )
    code_snapshot: Mapped[CodeSnapshot | None] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.code_snapshot_id",
    )
    model_artifact: Mapped[Artifact | None] = relationship(
        back_populates="model_versions",
        foreign_keys="ModelVersion.model_artifact_id",
    )
    preprocessor_artifact: Mapped[Artifact | None] = relationship(
        foreign_keys="ModelVersion.preprocessor_artifact_id",
    )
    feature_manifest_artifact: Mapped[Artifact | None] = relationship(
        foreign_keys="ModelVersion.feature_manifest_artifact_id",
    )
    evaluations: Mapped[list["ModelEvaluation"]] = relationship(
        back_populates="model_version",
        foreign_keys="ModelEvaluation.model_version_id",
    )


class LlmInvocation(Base):
    """Generic safe observability record; specialized ledgers remain authoritative."""

    __tablename__ = "llm_invocations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_llm_invocations_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_run_id"],
            ["workflow_runs.workspace_id", "workflow_runs.id"],
            name="fk_llm_invocations_workspace_workflow_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_llm_invocations_workspace_experiment",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "purpose IN ('semantic_target', 'semantic_missing_value', "
            "'semantic_column_type', 'semantic_leakage', 'pipeline_audit_routine', "
            "'pipeline_audit_deep')",
            name="ck_llm_invocations_purpose",
        ),
        Index(
            "ix_llm_invocations_workspace_created_at",
            "workspace_id",
            desc("created_at"),
        ),
        Index("ix_llm_invocations_workflow_run_id", "workflow_run_id"),
        Index("ix_llm_invocations_experiment_id", "experiment_id"),
        Index("ix_llm_invocations_purpose", "purpose"),
        Index("ix_llm_invocations_status", "status"),
        Index("ix_llm_invocations_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    redaction_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validator_verdict: Mapped[str] = mapped_column(String(1024), nullable=False)
    safe_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    final_decision: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="llm_invocations")
    workflow_run: Mapped[WorkflowRun] = relationship(
        back_populates="llm_invocations",
        foreign_keys="LlmInvocation.workflow_run_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="llm_invocations",
        foreign_keys="LlmInvocation.experiment_id",
    )


class MlRunEvent(Base):
    """Append-only, bounded event emitted by a real PipelineRun operation."""

    __tablename__ = "ml_run_events"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "sequence", name="uq_ml_run_events_experiment_sequence"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_ml_run_events_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_run_id"],
            ["workflow_runs.workspace_id", "workflow_runs.id"],
            name="fk_ml_run_events_workspace_workflow_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["experiments.workspace_id", "experiments.id"],
            name="fk_ml_run_events_workspace_experiment",
            ondelete="CASCADE",
        ),
        Index(
            "ix_ml_run_events_workspace_created_at",
            "workspace_id",
            desc("created_at"),
        ),
        Index("ix_ml_run_events_workflow_run_created_at", "workflow_run_id", "created_at"),
        Index("ix_ml_run_events_stage", "stage"),
        Index("ix_ml_run_events_timestamp", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="ml_run_events")
    workflow_run: Mapped[WorkflowRun] = relationship(
        back_populates="events",
        foreign_keys="MlRunEvent.workflow_run_id",
    )
    pipeline_run: Mapped[Experiment] = relationship(
        back_populates="events",
        foreign_keys="MlRunEvent.experiment_id",
    )


@event.listens_for(Dataset, "before_update")
@event.listens_for(Dataset, "before_delete")
def _protect_immutable_dataset(_mapper, _connection, _target: Dataset) -> None:
    raise ValueError("Dataset physical versions are immutable; create a new version")


@event.listens_for(ModelVersion, "before_update")
@event.listens_for(ModelVersion, "before_delete")
def _protect_immutable_model_version(_mapper, _connection, _target: ModelVersion) -> None:
    raise ValueError("ModelVersion rows are immutable; publish a new version")


@event.listens_for(MlRunEvent, "before_update")
@event.listens_for(MlRunEvent, "before_delete")
def _protect_immutable_ml_run_event(_mapper, _connection, _target: MlRunEvent) -> None:
    raise ValueError("MlRunEvent rows are append-only")


def _previous_locked_at(target) -> datetime | None:
    state = orm_inspect(target)
    hist = state.attrs.locked_at.history
    if hist.deleted:
        return hist.deleted[0]
    if hist.has_changes():
        return None
    return target.locked_at


@event.listens_for(WorkflowVersion, "before_update")
@event.listens_for(WorkflowVersion, "before_delete")
def _protect_locked_workflow_version(_mapper, _connection, target: WorkflowVersion) -> None:
    previous = _previous_locked_at(target)
    if previous is not None:
        raise ValueError("WorkflowVersion is locked and immutable")


@event.listens_for(PipelineVersion, "before_update")
@event.listens_for(PipelineVersion, "before_delete")
def _protect_locked_pipeline_version(_mapper, _connection, target: PipelineVersion) -> None:
    previous = _previous_locked_at(target)
    if previous is not None:
        raise ValueError("PipelineVersion is locked and immutable")


@event.listens_for(FeatureSetVersion, "before_update")
@event.listens_for(FeatureSetVersion, "before_delete")
def _protect_locked_feature_set_version(_mapper, _connection, target: FeatureSetVersion) -> None:
    previous = _previous_locked_at(target)
    if previous is not None:
        raise ValueError("FeatureSetVersion is locked and immutable")


@event.listens_for(ProblemSpec, "before_update")
@event.listens_for(ProblemSpec, "before_delete")
def _protect_locked_problem_spec(_mapper, _connection, target: ProblemSpec) -> None:
    previous = _previous_locked_at(target)
    if previous is not None:
        raise ValueError("ProblemSpec is locked and immutable")


@event.listens_for(ModelSelectionDecision, "before_update")
@event.listens_for(ModelSelectionDecision, "before_delete")
def _protect_immutable_selection_decision(
    _mapper, _connection, _target: ModelSelectionDecision
) -> None:
    raise ValueError("ModelSelectionDecision winner rows are immutable")
