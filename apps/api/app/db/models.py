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


class UserRole(str, enum.Enum):
    """Who a caller is. Drives the /admin vs /app split at the router level."""

    DCLAB_ADMIN = "dclab_admin"
    CLIENT_USER = "client_user"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('dclab_admin', 'client_user')",
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

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.DCLAB_ADMIN.value


class Opportunity(Base):
    __tablename__ = "opportunities"

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
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
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
        UUID(as_uuid=True), ForeignKey("client_lab_runs.id"), nullable=False, unique=True, index=True
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


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="csv")
    location: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    schema_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    environment: Mapped[Environment] = relationship(back_populates="datasets")
    profiles: Mapped[list["DatasetProfile"]] = relationship(back_populates="dataset")
    experiments: Mapped[list["Experiment"]] = relationship(back_populates="dataset")


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


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prediction_tasks.id"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED", index=True)
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
    task: Mapped[PredictionTask] = relationship(back_populates="experiments")
    candidates: Mapped[list["ExperimentCandidate"]] = relationship(back_populates="experiment")
    test_predictions: Mapped[list["ExperimentTestPrediction"]] = relationship(back_populates="experiment")


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
    record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    predicted_value: Mapped[object] = mapped_column(JSONB, nullable=False)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    y_true: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    experiment: Mapped[Experiment] = relationship(back_populates="test_predictions")

