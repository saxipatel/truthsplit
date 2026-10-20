import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Total, created_at, enum_column, uuid_pk
from app.models.metrics import MetricDefinition
from app.models.sources import Dataset


class InvestigationStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class Side(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"


class FindingType(StrEnum):
    DATE_CUTOFF = "date_cutoff"
    FILTER_DIFFERENCE = "filter_difference"
    REFUND_POLICY = "refund_policy"
    JOIN_AMPLIFICATION = "join_amplification"
    DUPLICATE_RECORDS = "duplicate_records"
    MISSING_RECORDS = "missing_records"
    EXTRA_RECORDS = "extra_records"
    AMOUNT_MISMATCH = "amount_mismatch"
    KEY_QUALITY = "key_quality"
    UNEXPLAINED = "unexplained"


class EvidenceStatus(StrEnum):
    CONFIRMED = "confirmed"
    SUSPECTED = "suspected"
    UNEXPLAINED = "unexplained"


class SimulationRule(StrEnum):
    REMOVE_DUPLICATES = "remove_duplicates"
    ALIGN_WINDOW = "align_window"
    REFUND_POLICY = "refund_policy"
    ALIGN_DEFINITION = "align_definition"
    ADJUST_MAPPING = "adjust_mapping"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMMENT = "comment"


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (sa.Index(None, "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(sa.String(200))
    left_metric_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("metric_definitions.id", ondelete="RESTRICT")
    )
    right_metric_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("metric_definitions.id", ondelete="RESTRICT")
    )
    status: Mapped[InvestigationStatus] = enum_column(
        InvestigationStatus, "status", default=InvestigationStatus.OPEN
    )
    # Null until the engine has run. delta = right - left.
    baseline_left: Mapped[Decimal | None] = mapped_column(Total)
    baseline_right: Mapped[Decimal | None] = mapped_column(Total)
    baseline_delta: Mapped[Decimal | None] = mapped_column(Total)
    explained_delta: Mapped[Decimal | None] = mapped_column(Total)
    unexplained_delta: Mapped[Decimal | None] = mapped_column(Total)
    # Everything needed to replay the run: key field, attribution order, tolerances.
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    engine_version: Mapped[str | None] = mapped_column(sa.String(32))
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), onupdate=sa.func.now())

    left_metric: Mapped[MetricDefinition] = relationship(foreign_keys=[left_metric_id])
    right_metric: Mapped[MetricDefinition] = relationship(foreign_keys=[right_metric_id])
    inputs: Mapped[list["InvestigationInput"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", passive_deletes=True
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Finding.step_order",
    )


class InvestigationInput(Base):
    __tablename__ = "investigation_inputs"
    __table_args__ = (
        sa.UniqueConstraint("investigation_id", "side"),
        sa.CheckConstraint("side IN ('left', 'right')", name="side_not_both"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("investigations.id", ondelete="CASCADE")
    )
    side: Mapped[Side] = enum_column(Side, "side")
    dataset_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("datasets.id", ondelete="RESTRICT"))
    dataset_fingerprint: Mapped[str] = mapped_column(sa.String(64))
    row_count: Mapped[int] = mapped_column(sa.Integer)

    investigation: Mapped[Investigation] = relationship(back_populates="inputs")
    dataset: Mapped[Dataset] = relationship()


class ComparisonResult(Base):
    """Record-level key matching between the two in-scope record sets."""

    __tablename__ = "comparison_results"

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    key_field: Mapped[str] = mapped_column(sa.String(64))
    matched_keys: Mapped[int] = mapped_column(sa.Integer)
    left_only_keys: Mapped[int] = mapped_column(sa.Integer)
    right_only_keys: Mapped[int] = mapped_column(sa.Integer)
    left_duplicate_keys: Mapped[int] = mapped_column(sa.Integer)
    right_duplicate_keys: Mapped[int] = mapped_column(sa.Integer)
    left_null_keys: Mapped[int] = mapped_column(sa.Integer)
    right_null_keys: Mapped[int] = mapped_column(sa.Integer)
    mismatched_keys: Mapped[int] = mapped_column(sa.Integer)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (sa.Index(None, "investigation_id", "type"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("investigations.id", ondelete="CASCADE")
    )
    # Position in the attribution replay; null for diagnostic findings that carry no attributed amount.
    step_order: Mapped[int | None] = mapped_column(sa.Integer)
    type: Mapped[FindingType] = enum_column(FindingType, "type")
    evidence_status: Mapped[EvidenceStatus] = enum_column(EvidenceStatus, "evidence_status")
    title: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str] = mapped_column(sa.Text)
    amount_impact: Mapped[Decimal | None] = mapped_column(Total)
    rule_id: Mapped[str] = mapped_column(sa.String(64))
    classification_reason: Mapped[str] = mapped_column(sa.Text)
    assumptions_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    calculation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    proof_hash: Mapped[str | None] = mapped_column(sa.String(64))
    affected_row_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    overlap_note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at()

    investigation: Mapped[Investigation] = relationship(back_populates="findings")


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[Side] = enum_column(Side, "side")
    dataset_record_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("dataset_records.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str | None] = mapped_column(sa.String(255))
    business_key: Mapped[str | None] = mapped_column(sa.String(255))
    # Per-key totals can exceed a single record's numeric(18,4), so these use the total type.
    left_amount: Mapped[Decimal | None] = mapped_column(Total)
    right_amount: Mapped[Decimal | None] = mapped_column(Total)
    contribution: Mapped[Decimal | None] = mapped_column(Total)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    rule_type: Mapped[SimulationRule] = enum_column(SimulationRule, "rule_type")
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    before_left: Mapped[Decimal] = mapped_column(Total)
    before_right: Mapped[Decimal] = mapped_column(Total)
    before_delta: Mapped[Decimal] = mapped_column(Total)
    after_left: Mapped[Decimal] = mapped_column(Total)
    after_right: Mapped[Decimal] = mapped_column(Total)
    after_delta: Mapped[Decimal] = mapped_column(Total)
    resolved_amount: Mapped[Decimal] = mapped_column(Total)
    affected_rows: Mapped[int] = mapped_column(sa.Integer)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()


class ResolutionNote(Base):
    __tablename__ = "resolution_notes"

    id: Mapped[uuid.UUID] = uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("findings.id", ondelete="SET NULL"))
    decision: Mapped[Decision] = enum_column(Decision, "decision")
    note: Mapped[str] = mapped_column(sa.Text)
    reviewer: Mapped[str | None] = mapped_column(sa.String(120))
    reviewed_at: Mapped[datetime] = created_at()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (sa.Index(None, "occurred_at"), sa.Index(None, "target_type", "target_id"))

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    actor: Mapped[str] = mapped_column(sa.String(120), default="local-user")
    action: Mapped[str] = mapped_column(sa.String(64))
    target_type: Mapped[str] = mapped_column(sa.String(64))
    target_id: Mapped[str] = mapped_column(sa.String(64))
    occurred_at: Mapped[datetime] = created_at()
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
