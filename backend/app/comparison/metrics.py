"""Evaluate a metric definition (Metric DNA) over normalized records.

Steps run in a fixed order, and every excluded record is kept together with the reason it was excluded:

  1. date window      occurred_at in [start, end) or [start, end], bounds read as wall-clock time in `timezone`
  2. filters          all must match; comparisons against a null field are false, as in SQL
  3. refund policy    `exclude_refunded` drops rows with refund_amount > 0
  4. grain            `business_key` keeps the first row (by row_number) of each key; null keys are dropped
  5. null policy      sum only: a null value is excluded (`exclude`) or fails the evaluation (`reject`)
  6. aggregation      sum of values, row count, or distinct business keys

Each included record carries the value it contributes, so the metric always equals the sum of
contributions. Per-key reconciliation relies on that.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.metrics import Aggregation, Grain, MetricDefinition, NullPolicy, RefundPolicy
from app.normalization.values import localize, parse_currency

TEXT_FIELDS = ("status", "currency", "business_key", "source_record_id")
NUMERIC_FIELDS = ("amount", "refund_amount")
FilterOp = Literal["eq", "ne", "in", "not_in", "is_null", "not_null", "gt", "gte", "lt", "lte"]
_ORDERING_OPS = {"gt", "gte", "lt", "lte"}


class MetricEvaluationError(ValueError):
    pass


class Filter(BaseModel):
    field: str = Field(description="A record field, or attributes.<column> for a mapped attribute")
    op: FilterOp
    value: Any = None

    @model_validator(mode="after")
    def _check(self) -> "Filter":
        is_attr = self.field.startswith("attributes.") and len(self.field) > len("attributes.")
        if self.field not in TEXT_FIELDS + NUMERIC_FIELDS and not is_attr:
            raise ValueError(f"unknown filter field {self.field!r}")
        numeric = self.field in NUMERIC_FIELDS
        if self.op in _ORDERING_OPS and not numeric:
            raise ValueError(f"{self.op} is only supported on amount and refund_amount")
        if self.op in ("is_null", "not_null"):
            if self.value is not None:
                raise ValueError(f"{self.op} takes no value")
            return self
        values = self.value if self.op in ("in", "not_in") else [self.value]
        if self.op in ("in", "not_in") and (not isinstance(self.value, list) or not self.value):
            raise ValueError(f"{self.op} needs a non-empty list")
        for v in values:
            if v is None or isinstance(v, (bool, dict, list)):
                raise ValueError("filter values must be strings or numbers")
        if numeric:
            try:
                parsed = [Decimal(str(v)) for v in values]
            except ArithmeticError:
                raise ValueError("numeric filter values must be numbers") from None
            self.value = parsed if self.op in ("in", "not_in") else parsed[0]
        else:
            self.value = [str(v) for v in values] if self.op in ("in", "not_in") else str(self.value)
        return self

    def matches(self, record: "Record") -> bool:
        actual = record.field(self.field)
        if self.op == "is_null":
            return actual is None
        if self.op == "not_null":
            return actual is not None
        if actual is None:
            return False  # SQL three-valued logic: NULL = x and NULL <> x are both not true
        match self.op:
            case "eq":
                return actual == self.value
            case "ne":
                return actual != self.value
            case "in":
                return actual in self.value
            case "not_in":
                return actual not in self.value
            case "gt":
                return actual > self.value
            case "gte":
                return actual >= self.value
            case "lt":
                return actual < self.value
            case "lte":
                return actual <= self.value
        raise AssertionError(self.op)


class MetricSpec(BaseModel):
    """The evaluable part of a metric definition."""

    aggregation: Aggregation
    value_field: Literal["amount", "refund_amount"] = "amount"
    grain: Grain = Grain.RECORD
    filters: list[Filter] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None
    window_end_inclusive: bool = False
    timezone: str = "UTC"
    refund_policy: RefundPolicy = RefundPolicy.GROSS
    null_policy: NullPolicy = NullPolicy.EXCLUDE
    currency: str | None = None

    @field_validator("window_start", "window_end")
    @classmethod
    def _wall_clock(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is not None:
            raise ValueError("window bounds are wall-clock times; give the zone in `timezone`, not an offset")
        return v

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {v!r}") from None
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else parse_currency(v)

    @model_validator(mode="after")
    def _window_order(self) -> "MetricSpec":
        if self.window_start and self.window_end and self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.aggregation is not Aggregation.SUM and self.refund_policy is RefundPolicy.NET:
            raise ValueError("net_of_refunds only applies to sum metrics")
        return self

    @classmethod
    def from_model(cls, m: MetricDefinition) -> "MetricSpec":
        return cls.model_validate(
            {name: getattr(m, name) for name in cls.model_fields}, from_attributes=False
        )

    def window_utc(self) -> tuple[datetime | None, datetime | None]:
        tz = ZoneInfo(self.timezone)
        try:
            start = localize(self.window_start, tz).astimezone(UTC) if self.window_start else None
            end = localize(self.window_end, tz).astimezone(UTC) if self.window_end else None
        except ValueError as exc:
            raise MetricEvaluationError(f"window bound: {exc}") from None
        return start, end


@dataclass(frozen=True, slots=True)
class Record:
    """In-memory copy of a dataset_records row."""

    id: int
    row_number: int
    source_record_id: str
    business_key: str | None
    occurred_at: datetime | None
    amount: Decimal | None
    currency: str | None
    status: str | None
    refund_amount: Decimal | None
    attributes: dict[str, str | None]
    raw_payload_hash: str

    def field(self, name: str):
        if name.startswith("attributes."):
            return self.attributes.get(name.removeprefix("attributes."))
        return getattr(self, name)


@dataclass(frozen=True, slots=True)
class Contribution:
    record: Record
    value: Decimal


@dataclass
class Evaluation:
    spec: MetricSpec
    value: Decimal
    contributions: list[Contribution]
    excluded: dict[str, list[Record]] = field(default_factory=dict)
    currency: str | None = None
    window_utc: tuple[datetime | None, datetime | None] = (None, None)

    @property
    def excluded_counts(self) -> dict[str, int]:
        return {reason: len(rows) for reason, rows in self.excluded.items()}


def _record_value(record: Record, spec: MetricSpec) -> Decimal | None:
    base = record.field(spec.value_field)
    if base is None:
        return None
    if spec.refund_policy is RefundPolicy.NET:
        return base - (record.refund_amount or 0)  # no refund recorded means nothing was refunded
    return base


def evaluate(records: list[Record], spec: MetricSpec) -> Evaluation:
    start, end = spec.window_utc()
    excluded: dict[str, list[Record]] = {}

    def drop(reason: str, record: Record) -> None:
        excluded.setdefault(reason, []).append(record)

    kept: list[Record] = []
    for r in sorted(records, key=lambda r: r.row_number):
        if start or end:
            if r.occurred_at is None:
                drop("no_timestamp", r)
                continue
            after_end = end is not None and (
                r.occurred_at > end if spec.window_end_inclusive else r.occurred_at >= end
            )
            if (start is not None and r.occurred_at < start) or after_end:
                drop("outside_window", r)
                continue
        if not all(f.matches(r) for f in spec.filters):
            drop("filtered", r)
            continue
        if spec.refund_policy is RefundPolicy.EXCLUDE_REFUNDED and (r.refund_amount or 0) > 0:
            drop("refunded", r)
            continue
        kept.append(r)

    if spec.grain is Grain.BUSINESS_KEY:
        seen: set[str] = set()
        grained = []
        for r in kept:
            if r.business_key is None:
                drop("null_key", r)
            elif r.business_key in seen:
                drop("duplicate_grain", r)
            else:
                seen.add(r.business_key)
                grained.append(r)
        kept = grained

    contributions: list[Contribution] = []
    if spec.aggregation is Aggregation.SUM:
        for r in kept:
            value = _record_value(r, spec)
            if value is None:
                if spec.null_policy is NullPolicy.REJECT:
                    raise MetricEvaluationError(
                        f"row {r.row_number} has no {spec.value_field} and the null policy is 'reject'"
                    )
                drop("null_value", r)
                continue
            contributions.append(Contribution(r, value))
    elif spec.aggregation is Aggregation.COUNT:
        contributions = [Contribution(r, Decimal(1)) for r in kept]
    else:  # count_distinct: the first row of each key contributes 1, later rows 0
        seen = set()
        for r in kept:
            if r.business_key is None:
                drop("null_key", r)
                continue
            contributions.append(Contribution(r, Decimal(0) if r.business_key in seen else Decimal(1)))
            seen.add(r.business_key)

    currency = _check_currency(contributions, spec)
    return Evaluation(
        spec=spec,
        value=sum((c.value for c in contributions), Decimal(0)),
        contributions=contributions,
        excluded=excluded,
        currency=currency,
        window_utc=(start, end),
    )


def _check_currency(contributions: list[Contribution], spec: MetricSpec) -> str | None:
    """Money from different currencies is never added together; conversion is out of scope."""
    if spec.aggregation is not Aggregation.SUM:
        return spec.currency
    counts = Counter(c.record.currency for c in contributions)
    if None in counts:
        raise MetricEvaluationError(
            f"{counts[None]} included rows have no currency; map a currency column or a default"
        )
    found = sorted(counts)
    if spec.currency and found and found != [spec.currency]:
        others = [c for c in found if c != spec.currency]
        raise MetricEvaluationError(
            f"metric is defined in {spec.currency} but includes rows in {', '.join(others)}"
        )
    if len(found) > 1:
        raise MetricEvaluationError(
            f"included rows mix currencies ({', '.join(found)}) and no conversion rules exist"
        )
    return found[0] if found else spec.currency


def unmapped_fields(spec: MetricSpec, mapping: dict) -> list[str]:
    """Fields the definition reads that the dataset's column mapping never provided.

    Evaluating anyway would be silently wrong: a net-of-refunds metric over data without a refund column
    would equal the gross figure, and a filter on a missing attribute would exclude every row.
    """
    mapped = {name for name in ("amount", "refund_amount", "occurred_at", "status") if mapping.get(name)}
    if mapping.get("currency") or mapping.get("default_currency"):
        mapped.add("currency")
    if mapping.get("business_key"):
        mapped.add("business_key")
    mapped.add("source_record_id")  # falls back to the row number
    mapped |= {f"attributes.{a}" for a in mapping.get("attributes", [])}

    needed: list[str] = []
    if spec.aggregation is Aggregation.SUM:
        needed.append(spec.value_field)
    if spec.refund_policy is not RefundPolicy.GROSS:
        needed.append("refund_amount")
    if spec.window_start or spec.window_end:
        needed.append("occurred_at")
    if spec.grain is Grain.BUSINESS_KEY or spec.aggregation is Aggregation.COUNT_DISTINCT:
        needed.append("business_key")
    needed += [f.field for f in spec.filters]
    return sorted({n for n in needed if n not in mapped})
