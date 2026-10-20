import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.sources import FileType, SourceType
from app.normalization.mapping import ColumnMapping


class ColumnProfile(BaseModel):
    name: str
    inferred_type: str
    empty_count: int
    sample: list[str | None]


class Preview(BaseModel):
    sheet: str | None
    header: list[str]
    row_numbers: list[int]
    rows: list[list[str | None]]
    columns: list[ColumnProfile]
    truncated: bool = Field(description="True when the file has more rows than were read for the preview")


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    file_type: FileType
    size_bytes: int
    sha256: str
    sheet_names: list[str]
    created_at: datetime


class UploadResponse(BaseModel):
    upload: UploadOut
    already_uploaded: bool
    # The default sheet may not be previewable (e.g. a cover sheet with no data rows) even though the
    # file is valid; the upload is kept and the client can preview another sheet.
    preview: Preview | None
    preview_error: str | None = None


class DatasetCreate(BaseModel):
    upload_id: uuid.UUID
    sheet: str | None = None
    encoding: str = "utf-8"
    label: str | None = Field(None, max_length=200)
    mapping: ColumnMapping


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    source_type: SourceType
    upload_id: uuid.UUID | None
    sheet_name: str | None
    column_mapping: dict
    source_schema: dict = Field(validation_alias="schema_json")
    fingerprint: str
    row_count: int
    rejected_row_count: int
    is_demo: bool
    created_at: datetime


class DatasetImportResponse(BaseModel):
    dataset: DatasetOut
    already_imported: bool


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    source_record_id: str
    business_key: str | None
    occurred_at: datetime | None
    amount: Decimal | None
    currency: str | None
    status: str | None
    refund_amount: Decimal | None
    attributes: dict
    raw_payload_hash: str


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int | None
    column_name: str | None
    raw_value: str | None
    message: str


class Page[T](BaseModel):
    total: int
    offset: int
    items: list[T]
