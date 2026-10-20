import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, enum_column, uuid_pk


class Aggregation(StrEnum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"


class Grain(StrEnum):
    RECORD = "record"  # every row counts
    BUSINESS_KEY = "business_key"  # one row per business key (first by row_number)


class RefundPolicy(StrEnum):
    GROSS = "gross"  # ignore refunds
    NET = "net_of_refunds"  # amount - refund_amount
    EXCLUDE_REFUNDED = "exclude_refunded"  # drop rows with any refund


class NullPolicy(StrEnum):
    EXCLUDE = "exclude"  # rows with a null value field are left out and counted
    REJECT = "reject"  # evaluation fails if any in-scope value is null


class MetricDefinition(Base):
    """Metric DNA. Independent of any dataset, so one definition can be evaluated against many snapshots.

    Rows are immutable: changing a definition means inserting the next version.
    """

    __tablename__ = "metric_definitions"
    __table_args__ = (
        sa.UniqueConstraint("name", "version"),
        sa.CheckConstraint("version > 0", name="version"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.String(120))
    version: Mapped[int] = mapped_column(sa.Integer)
    description: Mapped[str | None] = mapped_column(sa.Text)
    aggregation: Mapped[Aggregation] = enum_column(Aggregation, "aggregation")
    value_field: Mapped[str] = mapped_column(sa.String(64), default="amount")
    grain: Mapped[Grain] = enum_column(Grain, "grain")
    # List of {"field": ..., "op": ..., "value": ...}; see app/metrics for the supported operators.
    filters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    date_field: Mapped[str] = mapped_column(sa.String(64), default="occurred_at")
    # Window bounds are wall-clock times in `timezone`; they are converted to UTC at evaluation.
    window_start: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=False))
    window_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=False))
    window_end_inclusive: Mapped[bool] = mapped_column(default=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), default="UTC")
    refund_policy: Mapped[RefundPolicy] = enum_column(RefundPolicy, "refund_policy")
    null_policy: Mapped[NullPolicy] = enum_column(NullPolicy, "null_policy")
    currency: Mapped[str | None] = mapped_column(sa.String(3))
    created_at: Mapped[datetime] = created_at()
