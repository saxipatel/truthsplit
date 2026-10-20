import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at, enum_column, uuid_pk


class SourceType(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    XLS = "xls"
    POSTGRES_QUERY = "postgres_query"


class FileType(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    XLS = "xls"


class SourceConnection(Base):
    """A PostgreSQL source the backend may read from. Credentials stay in the environment."""

    __tablename__ = "source_connections"

    id: Mapped[uuid.UUID] = uuid_pk()
    label: Mapped[str] = mapped_column(sa.String(120), unique=True)
    # Name of the environment profile holding the URL, e.g. "demo" -> TRUTHSPLIT_SOURCE_DEMO_URL.
    config_reference: Mapped[str] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = created_at()


class FileUpload(Base):
    """An uploaded file, stored once by content hash. Parsing into a dataset is a separate step."""

    __tablename__ = "file_uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    original_filename: Mapped[str] = mapped_column(sa.String(255))
    sha256: Mapped[str] = mapped_column(sa.String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    file_type: Mapped[FileType] = enum_column(FileType, "file_type")
    sheet_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = created_at()


class Dataset(Base):
    """An immutable, normalized snapshot of one source: a parsed file sheet or a query result."""

    __tablename__ = "datasets"
    __table_args__ = (
        sa.CheckConstraint(
            "(source_type = 'postgres_query') = (source_connection_id IS NOT NULL AND query_hash IS NOT NULL)",
            name="query_provenance",
        ),
        sa.CheckConstraint(
            "(source_type = 'postgres_query') OR upload_id IS NOT NULL", name="file_provenance"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    label: Mapped[str] = mapped_column(sa.String(200))
    source_type: Mapped[SourceType] = enum_column(SourceType, "source_type")
    upload_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("file_uploads.id", ondelete="RESTRICT"))
    sheet_name: Mapped[str | None] = mapped_column(sa.String(255))
    source_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("source_connections.id", ondelete="RESTRICT")
    )
    query_key: Mapped[str | None] = mapped_column(sa.String(120))
    query_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    query_hash: Mapped[str | None] = mapped_column(sa.String(64))
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Hash of source content + sheet + mapping (+ query params). Re-importing the same thing is a no-op.
    fingerprint: Mapped[str] = mapped_column(sa.String(64), unique=True)
    row_count: Mapped[int] = mapped_column(sa.Integer)
    rejected_row_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at()

    upload: Mapped[FileUpload | None] = relationship()
    source_connection: Mapped[SourceConnection | None] = relationship()


class DatasetRecord(Base):
    """One row in the normalized record contract. `row_number` points back to the original file row."""

    __tablename__ = "dataset_records"
    __table_args__ = (
        sa.UniqueConstraint("dataset_id", "row_number"),
        sa.Index(None, "dataset_id", "business_key"),
        sa.Index(None, "dataset_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("datasets.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(sa.Integer)
    source_record_id: Mapped[str] = mapped_column(sa.String(255))
    business_key: Mapped[str | None] = mapped_column(sa.String(255))
    occurred_at: Mapped[datetime | None]
    amount: Mapped[Decimal | None]
    currency: Mapped[str | None] = mapped_column(sa.String(3))
    status: Mapped[str | None] = mapped_column(sa.String(64))
    refund_amount: Mapped[Decimal | None]
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    raw_payload_hash: Mapped[str] = mapped_column(sa.String(64))


class ImportIssue(Base):
    """A source row or value that failed validation. Rejected rows are recorded, never coerced."""

    __tablename__ = "import_issues"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int | None] = mapped_column(sa.Integer)
    column_name: Mapped[str | None] = mapped_column(sa.String(255))
    raw_value: Mapped[str | None] = mapped_column(sa.Text)
    message: Mapped[str] = mapped_column(sa.Text)
