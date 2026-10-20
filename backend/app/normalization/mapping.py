"""Map source columns onto the normalized record contract and validate every mapped value.

A row with any invalid mapped value is rejected as a whole and reported; it is never partially imported
or coerced. Empty cells become None, which downstream code treats as unknown rather than zero.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from app.adapters.files import Cell, IngestionError, RawTable
from app.normalization.values import (
    format_cell,
    is_empty,
    parse_amount,
    parse_currency,
    parse_text,
    parse_timestamp,
)


class ColumnMapping(BaseModel):
    """Which source column feeds each normalized field. Every name must exist in the file header."""

    source_record_id: str | None = Field(None, description="Row identifier; defaults to the row number")
    business_key: list[str] = Field(
        default_factory=list, description="One column, or several for a composite key"
    )
    occurred_at: str | None = None
    timezone: str = Field("UTC", description="Zone for timestamps that carry no UTC offset")
    date_format: str | None = Field(None, description="strptime format; ISO 8601 when omitted")
    amount: str | None = None
    refund_amount: str | None = None
    currency: str | None = None
    default_currency: str | None = Field(None, description="Currency for every row when no column has one")
    status: str | None = None
    attributes: list[str] = Field(default_factory=list, description="Extra columns kept for filtering")

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {v!r}") from None
        return v

    @field_validator("default_currency")
    @classmethod
    def _currency_code(cls, v: str | None) -> str | None:
        return None if v is None else parse_currency(v)

    @model_validator(mode="after")
    def _consistent(self) -> "ColumnMapping":
        if self.currency and self.default_currency:
            raise ValueError("set either a currency column or a default currency, not both")
        if len(set(self.business_key)) != len(self.business_key):
            raise ValueError("business key columns must be distinct")
        if not (self.business_key or self.amount or self.source_record_id):
            raise ValueError("map at least a business key, an amount or a record id")
        return self

    def columns(self) -> list[str]:
        singles = [
            self.source_record_id,
            self.occurred_at,
            self.amount,
            self.refund_amount,
            self.currency,
            self.status,
        ]
        cols = [c for c in singles if c] + self.business_key + self.attributes
        return list(dict.fromkeys(cols))


@dataclass
class NormalizedRecord:
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


@dataclass
class RowIssue:
    row_number: int | None
    column_name: str | None
    raw_value: str | None
    message: str


@dataclass
class NormalizationResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    rejected_rows: int = 0
    null_business_keys: int = 0


def composite_key(parts: list[str]) -> str:
    """One column's value is used as is; several are encoded as a JSON array so that no separator
    character inside a value can make two different keys collide."""
    return parts[0] if len(parts) == 1 else json.dumps(parts, ensure_ascii=False)


def row_hash(row: list[Cell]) -> str:
    payload = json.dumps([format_cell(c) for c in row], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize(table: RawTable, mapping: ColumnMapping) -> NormalizationResult:
    missing = [c for c in mapping.columns() if c not in table.header]
    if missing:
        raise IngestionError(f"Mapped columns not found in the file: {', '.join(missing)}.")

    idx = {name: i for i, name in enumerate(table.header)}
    width = len(table.header)
    tz = ZoneInfo(mapping.timezone)
    result = NormalizationResult()

    for row_number, row in zip(table.row_numbers, table.rows, strict=True):
        ragged = len(row) > width and any(not is_empty(c) for c in row[width:])
        if ragged or (table.strict_width and len(row) < width):
            message = f"row has {len(row)} values but the header has {width} columns"
            outcome: NormalizedRecord | list[RowIssue] = [RowIssue(row_number, None, None, message)]
        else:
            cells = (list(row) + [None] * width)[:width]
            outcome = _normalize_row(
                row_number, {c: cells[idx[c]] for c in mapping.columns()}, cells, mapping, tz
            )

        if isinstance(outcome, NormalizedRecord):
            result.records.append(outcome)
            if mapping.business_key and outcome.business_key is None:
                result.null_business_keys += 1
        else:
            result.issues.extend(outcome)
            result.rejected_rows += 1
    return result


def _normalize_row(
    row_number: int, values: dict[str, Cell], cells: list[Cell], mapping: ColumnMapping, tz: ZoneInfo
) -> NormalizedRecord | list[RowIssue]:
    issues: list[RowIssue] = []

    def parsed(column: str | None, parser, *args):
        if column is None:
            return None
        try:
            return parser(values[column], *args)
        except ValueError as exc:
            issues.append(RowIssue(row_number, column, format_cell(values[column]), str(exc)))
            return None

    def text(column: str | None) -> str | None:
        return None if column is None else parse_text(values[column])

    amount = parsed(mapping.amount, parse_amount)
    refund = parsed(mapping.refund_amount, parse_amount)
    occurred_at = parsed(mapping.occurred_at, parse_timestamp, tz, mapping.date_format)
    currency = parsed(mapping.currency, parse_currency) if mapping.currency else mapping.default_currency
    if refund is not None and refund < 0:
        issues.append(
            RowIssue(
                row_number,
                mapping.refund_amount,
                format_cell(values[mapping.refund_amount]),
                "refund amounts must be zero or positive",
            )
        )

    key_parts = [text(c) for c in mapping.business_key]
    business_key = composite_key(key_parts) if key_parts and None not in key_parts else None
    record_id = text(mapping.source_record_id)
    status = text(mapping.status)
    for name, value, limit in (
        ("business key", business_key, 255),
        ("record id", record_id, 255),
        ("status", status, 64),
    ):
        if value is not None and len(value) > limit:
            issues.append(
                RowIssue(row_number, None, value[:100], f"{name} is longer than {limit} characters")
            )

    if issues:
        return issues
    return NormalizedRecord(
        row_number=row_number,
        source_record_id=record_id or f"row:{row_number}",
        business_key=business_key,
        occurred_at=occurred_at,
        amount=amount,
        currency=currency,
        status=status,
        refund_amount=refund,
        attributes={c: text(c) for c in mapping.attributes},
        raw_payload_hash=row_hash(cells),
    )
