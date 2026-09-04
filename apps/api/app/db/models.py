import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DEFAULT_ORG_ID = "default"

# Well-known workspace every pre-existing row is backfilled to, and the workspace
# used when a request omits X-Workspace-Id. Fixed (not random) so migrations and
# app code can agree on it without a lookup.
DEFAULT_WORKSPACE_SLUG = "default"
DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
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
    domain_links: Mapped[list["WorkspaceDomain"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    dataset_assets: Mapped[list["DatasetAsset"]] = relationship(back_populates="workspace")
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="workspace")
    ml_workflows: Mapped[list["MlWorkflow"]] = relationship(back_populates="workspace")
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="workspace")
    pipeline_runs: Mapped[list["Experiment"]] = relationship(back_populates="workspace")
    model_assets: Mapped[list["ModelAsset"]] = relationship(back_populates="workspace")
    model_versions: Mapped[list["ModelVersion"]] = relationship(back_populates="workspace")
    ml_run_events: Mapped[list["MlRunEvent"]] = relationship(back_populates="workspace")
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(back_populates="workspace")


class UserRole(str, enum.Enum):
    """Compatibility mirror for identity responses and the legacy users.role column.

    Authorization is derived from membership tables. ``client_user`` remains a
    supported legacy value during the migration window.
    """

    DCLAB_ADMIN = "dclab_admin"
    DCLAB_DEVELOPER = "dclab_developer"
    BUSINESS_ADMIN = "business_admin"
    BUSINESS_DEVELOPER = "business_developer"
    CLIENT_USER = "client_user"


class PlatformRole(str, enum.Enum):
    DCLAB_ADMIN = "dclab_admin"
    DCLAB_DEVELOPER = "dclab_developer"


class WorkspaceRole(str, enum.Enum):
    BUSINESS_ADMIN = "business_admin"
    BUSINESS_DEVELOPER = "business_developer"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('dclab_admin', 'dclab_developer', 'business_admin', "
            "'business_developer', 'client_user')",
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
            "role IN ('business_admin', 'business_developer')",
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
        Index("ix_workspace_domains_workspace_id", "workspace_id"),
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
    workflows: Mapped[list["MlWorkflow"]] = relationship(back_populates="workspace_domain")


class Opportunity(Base):
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
    """Step 5 — one bounded, client-triggered trial run. `insights` stores only the
    already-translated `ClientFacingInsight` payloads (never raw model/metric
    detail) — a trial prospect is exactly the audience the translation layer
    exists to protect, so nothing raw is persisted here even at rest."""

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
    """Step 7 — admin-only. The full, raw `run_use_case` output for a completed
    Client Labs trial — exactly what `ClientLabRun.insights` deliberately leaves
    out. One-to-one with the `ClientLabRun` it audits, so an admin can trace a
    client-triggered "custom prediction" request back to its unrestricted ML
    detail (Admin Model Registry / Monitoring), the same way an admin-run
    simulation already is."""

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
    """Open ingest for Client Labs: the file is saved as-is. Structuring it
    (language tools + DCLab's reading pipeline) is not implemented yet.

    `run_id` is the stable ML-run identity (currently equal to `id`).
    `client_status` is the coarse four-state view a client may see; fine-grained
    execution lives on `pipeline_status`.
    """

    __tablename__ = "client_lab_uploads"
    __table_args__ = (
        CheckConstraint(
            "client_status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_client_lab_uploads_client_status",
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
        index=True,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped["Dataset | None"] = relationship(back_populates="source_uploads")
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="source_upload")


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
    __tablename__ = "environments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, default="dclab", index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="environment")
    tasks: Mapped[list["PredictionTask"]] = relationship(back_populates="environment")


class DatasetAsset(Base):
    """Logical dataset whose immutable physical versions live in Dataset."""

    __tablename__ = "dataset_assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_dataset_assets_workspace_slug"),
        Index("ix_dataset_assets_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
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

    workspace: Mapped[Workspace] = relationship(back_populates="dataset_assets")
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="asset")


class Dataset(Base):
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
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
        index=True,
    )
    dataset_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_assets.id"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="csv")
    location: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    schema_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    environment: Mapped[Environment] = relationship(back_populates="datasets")
    workspace: Mapped[Workspace] = relationship(back_populates="datasets")
    asset: Mapped[DatasetAsset] = relationship(back_populates="datasets")
    source_uploads: Mapped[list[ClientLabUpload]] = relationship(back_populates="dataset")
    profiles: Mapped[list["DatasetProfile"]] = relationship(back_populates="dataset")
    experiments: Mapped[list["Experiment"]] = relationship(back_populates="dataset")
    workflow_inputs: Mapped[list["WorkflowRunInput"]] = relationship(back_populates="dataset")
    model_versions: Mapped[list["ModelVersion"]] = relationship(back_populates="dataset")


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
        Index("ix_ml_workflows_workspace_id", "workspace_id"),
        Index("ix_ml_workflows_workspace_domain_id", "workspace_domain_id"),
        Index("ix_ml_workflows_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
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

    workspace: Mapped[Workspace] = relationship(back_populates="ml_workflows")
    workspace_domain: Mapped[WorkspaceDomain] = relationship(back_populates="workflows")
    runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="workflow")
    model_assets: Mapped[list["ModelAsset"]] = relationship(back_populates="workflow")
    model_versions: Mapped[list["ModelVersion"]] = relationship(back_populates="workflow")


class WorkflowRun(Base):
    """One invocation of an MlWorkflow."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_workspace_id", "workspace_id"),
        Index("ix_workflow_runs_workflow_id", "workflow_id"),
        Index("ix_workflow_runs_source_upload_id", "source_upload_id"),
        Index("ix_workflow_runs_status", "status"),
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
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
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

    workspace: Mapped[Workspace] = relationship(back_populates="workflow_runs")
    workflow: Mapped[MlWorkflow] = relationship(back_populates="runs")
    source_upload: Mapped[ClientLabUpload | None] = relationship(back_populates="workflow_runs")
    inputs: Mapped[list["WorkflowRunInput"]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan"
    )
    pipeline_runs: Mapped[list["Experiment"]] = relationship(back_populates="workflow_run")
    model_versions: Mapped[list["ModelVersion"]] = relationship(back_populates="workflow_run")
    events: Mapped[list["MlRunEvent"]] = relationship(back_populates="workflow_run")
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(
        back_populates="workflow_run"
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
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "pipeline_index",
            name="uq_experiments_workflow_run_pipeline_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        server_default=str(DEFAULT_WORKSPACE_ID),
        index=True,
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
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

    dataset: Mapped[Dataset] = relationship(back_populates="experiments")
    workspace: Mapped[Workspace] = relationship(back_populates="pipeline_runs")
    workflow_run: Mapped[WorkflowRun | None] = relationship(back_populates="pipeline_runs")
    task: Mapped[PredictionTask | None] = relationship(back_populates="experiments")
    candidates: Mapped[list["ExperimentCandidate"]] = relationship(back_populates="experiment")
    test_predictions: Mapped[list["ExperimentTestPrediction"]] = relationship(back_populates="experiment")
    model_version: Mapped["ModelVersion | None"] = relationship(
        back_populates="pipeline_run", uselist=False
    )
    events: Mapped[list["MlRunEvent"]] = relationship(back_populates="pipeline_run")
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(
        back_populates="pipeline_run"
    )


class ExperimentCandidate(Base):
    __tablename__ = "experiment_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(256), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generated")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    experiment: Mapped[Experiment] = relationship(back_populates="candidates")
    model_version: Mapped["ModelVersion | None"] = relationship(
        back_populates="selected_candidate", uselist=False
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


class ModelAsset(Base):
    """Logical managed model whose immutable releases are ModelVersion rows."""

    __tablename__ = "model_assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_model_assets_workspace_slug"),
        Index("ix_model_assets_workspace_id", "workspace_id"),
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
    workflow: Mapped[MlWorkflow] = relationship(back_populates="model_assets")
    versions: Mapped[list["ModelVersion"]] = relationship(back_populates="model_asset")


class ModelVersion(Base):
    """Append-only selected model release with complete lineage."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_asset_id", "version", name="uq_model_versions_asset_version"),
        UniqueConstraint("pipeline_run_id", name="uq_model_versions_pipeline_run_id"),
        UniqueConstraint(
            "selected_candidate_id", name="uq_model_versions_selected_candidate_id"
        ),
        Index("ix_model_versions_workspace_id", "workspace_id"),
        Index("ix_model_versions_workflow_id", "workflow_id"),
        Index("ix_model_versions_workflow_run_id", "workflow_run_id"),
        Index("ix_model_versions_dataset_id", "dataset_id"),
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
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ml_workflows.id"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=False
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
    artifact_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_asset: Mapped[ModelAsset] = relationship(back_populates="versions")
    workspace: Mapped[Workspace] = relationship(back_populates="model_versions")
    workflow: Mapped[MlWorkflow] = relationship(back_populates="model_versions")
    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="model_versions")
    pipeline_run: Mapped[Experiment] = relationship(back_populates="model_version")
    selected_candidate: Mapped[ExperimentCandidate] = relationship(
        back_populates="model_version"
    )
    dataset: Mapped[Dataset] = relationship(back_populates="model_versions")


class LlmInvocation(Base):
    """Generic safe observability record; specialized ledgers remain authoritative."""

    __tablename__ = "llm_invocations"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('semantic_target', 'semantic_missing_value', "
            "'semantic_column_type', 'pipeline_audit_routine', "
            "'pipeline_audit_deep')",
            name="ck_llm_invocations_purpose",
        ),
        Index("ix_llm_invocations_workspace_id", "workspace_id"),
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
    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="llm_invocations")
    pipeline_run: Mapped[Experiment] = relationship(back_populates="llm_invocations")


class MlRunEvent(Base):
    """Append-only, bounded event emitted by a real PipelineRun operation."""

    __tablename__ = "ml_run_events"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "sequence", name="uq_ml_run_events_experiment_sequence"
        ),
        Index("ix_ml_run_events_workspace_id", "workspace_id"),
        Index("ix_ml_run_events_workflow_run_id", "workflow_run_id"),
        Index("ix_ml_run_events_experiment_id", "experiment_id"),
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
    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="events")
    pipeline_run: Mapped[Experiment] = relationship(back_populates="events")


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
