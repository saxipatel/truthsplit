import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.comparison.metrics import MetricSpec
from app.models.investigations import EvidenceStatus, FindingType, InvestigationStatus, Side


class MetricDefinitionCreate(MetricSpec):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class MetricDefinitionOut(MetricSpec):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    version: int
    description: str | None
    created_at: datetime


class InputRef(BaseModel):
    dataset_id: uuid.UUID
    metric_definition_id: uuid.UUID


class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    left: InputRef
    right: InputRef


class InputOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    side: Side
    dataset_id: uuid.UUID
    dataset_fingerprint: str
    row_count: int


class ComparisonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key_field: str
    matched_keys: int
    left_only_keys: int
    right_only_keys: int
    left_duplicate_keys: int
    right_duplicate_keys: int
    left_null_keys: int
    right_null_keys: int
    mismatched_keys: int
    summary_json: dict


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_order: int | None
    type: FindingType
    evidence_status: EvidenceStatus
    title: str
    description: str
    amount_impact: Decimal | None
    rule_id: str
    classification_reason: str
    assumptions_json: list[str]
    calculation_json: dict
    proof_hash: str | None
    affected_row_count: int
    overlap_note: str | None


class InvestigationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: InvestigationStatus
    baseline_left: Decimal | None
    baseline_right: Decimal | None
    baseline_delta: Decimal | None
    explained_delta: Decimal | None
    unexplained_delta: Decimal | None
    is_demo: bool
    created_at: datetime


class InvestigationOut(InvestigationSummary):
    left_metric: MetricDefinitionOut
    right_metric: MetricDefinitionOut
    inputs: list[InputOut]
    comparison: ComparisonOut | None
    findings: list[FindingOut]
    config_json: dict
    engine_version: str | None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    side: Side
    business_key: str | None
    source_record_id: str | None
    left_amount: Decimal | None
    right_amount: Decimal | None
    contribution: Decimal | None
    detail: dict
