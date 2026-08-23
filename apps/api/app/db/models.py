import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DEFAULT_ORG_ID = "default"


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_ORG_ID, server_default=DEFAULT_ORG_ID
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


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), nullable=False, unique=True, index=True
    )
    prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("predictions.id"), nullable=False, index=True
    )
    recommended_action: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
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
