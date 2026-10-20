"""Store uploaded files and turn them into normalized datasets.

Both steps are idempotent: the same bytes map to one `file_uploads` row, and the same upload + sheet +
encoding + mapping maps to one dataset (by fingerprint), so retrying an import never duplicates records.
"""

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from psycopg.types.json import Jsonb
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.files import (
    EncodingError,
    IngestionError,
    check_xlsx_container,
    detect_file_type,
    list_sheets,
    read_table,
)
from app.models import Dataset, FileUpload, ImportIssue
from app.models.sources import FileType, SourceType
from app.normalization.mapping import ColumnMapping, NormalizationResult, normalize

log = logging.getLogger(__name__)

# Bump when parsing or normalization rules change, so that re-imports produce a new dataset.
NORMALIZER_VERSION = "1"
_CHUNK = 1024 * 1024


class UploadTooLargeError(ValueError):
    pass


@dataclass
class StoredUpload:
    upload: FileUpload
    created: bool


def upload_path(storage_dir: Path, upload: FileUpload) -> Path:
    return storage_dir / "uploads" / f"{upload.sha256}.{upload.file_type.value}"


def sanitize_filename(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:255] or "upload"


def store_upload(
    session: Session, stream: BinaryIO, filename: str | None, *, storage_dir: Path, max_bytes: int
) -> StoredUpload:
    """Stream to a temp file while hashing, enforce the size limit before anything is parsed,
    check the file signature, then keep one copy per content hash."""
    filename = sanitize_filename(filename)
    target_dir = storage_dir / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    head = b""
    # openpyxl decides by extension whether it can open a file, so the temp file keeps the upload's.
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=".incoming-", suffix=Path(filename).suffix.lower())
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(
                        f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit."
                    )
                if len(head) < 8192:
                    head += chunk[: 8192 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise IngestionError("The file is empty.")

        file_type = detect_file_type(filename, head)
        if file_type is FileType.XLSX:
            check_xlsx_container(tmp)
        if file_type is FileType.CSV:
            # A CSV is one table, so a structurally broken one is refused before it is stored. A decoding
            # failure is not structural: the user can preview it again with another encoding.
            with contextlib.suppress(EncodingError):
                read_table(tmp, file_type, max_rows=1, preview=True)
        sha = digest.hexdigest()

        existing = session.scalar(select(FileUpload).where(FileUpload.sha256 == sha))
        if existing:
            return StoredUpload(existing, created=False)

        sheets = list_sheets(tmp, file_type)
        upload = FileUpload(
            original_filename=filename, sha256=sha, size_bytes=size, file_type=file_type, sheet_names=sheets
        )
        final = target_dir / f"{sha}.{file_type.value}"
        os.replace(tmp, final)
        session.add(upload)
        try:
            session.commit()
        except IntegrityError:
            # A concurrent request stored the same bytes first; the file on disk is identical.
            session.rollback()
            return StoredUpload(
                session.scalar(select(FileUpload).where(FileUpload.sha256 == sha)), created=False
            )
        log.info(
            "upload stored", extra={"upload_id": str(upload.id), "file_type": file_type.value, "bytes": size}
        )
        return StoredUpload(upload, created=True)
    finally:
        tmp.unlink(missing_ok=True)


def dataset_fingerprint(upload: FileUpload, sheet: str | None, encoding: str, mapping: ColumnMapping) -> str:
    payload = {
        "upload_sha256": upload.sha256,
        "sheet": sheet,
        "encoding": encoding if upload.file_type is FileType.CSV else None,
        "mapping": mapping.model_dump(mode="json"),
        "normalizer": NORMALIZER_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass
class ImportOutcome:
    dataset: Dataset
    created: bool


def create_dataset(
    session: Session,
    upload: FileUpload,
    mapping: ColumnMapping,
    *,
    storage_dir: Path,
    max_rows: int,
    sheet: str | None = None,
    encoding: str = "utf-8",
    label: str | None = None,
    is_demo: bool = False,
) -> ImportOutcome:
    if upload.file_type is not FileType.CSV and sheet is None and upload.sheet_names:
        sheet = upload.sheet_names[0]
    fingerprint = dataset_fingerprint(upload, sheet, encoding, mapping)
    existing = session.scalar(select(Dataset).where(Dataset.fingerprint == fingerprint))
    if existing:
        return ImportOutcome(existing, created=False)

    path = upload_path(storage_dir, upload)
    if not path.exists():
        raise IngestionError("The stored file for this upload is missing; upload it again.")
    table = read_table(path, upload.file_type, sheet=sheet, encoding=encoding, max_rows=max_rows)
    result = normalize(table, mapping)

    dataset = Dataset(
        id=uuid.uuid4(),
        label=label or (f"{upload.original_filename} [{sheet}]" if sheet else upload.original_filename),
        source_type=SourceType(upload.file_type.value),
        upload_id=upload.id,
        sheet_name=sheet,
        column_mapping=mapping.model_dump(mode="json"),
        schema_json={
            "header": table.header,
            "encoding": encoding if upload.file_type is FileType.CSV else None,
            "data_rows_read": len(table.rows),
            "blank_rows_skipped": table.blank_rows_skipped,
            "null_business_keys": result.null_business_keys,
            "normalizer_version": NORMALIZER_VERSION,
        },
        fingerprint=fingerprint,
        row_count=len(result.records),
        rejected_row_count=result.rejected_rows,
        is_demo=is_demo,
    )
    session.add(dataset)
    session.flush()
    _copy_records(session, dataset.id, result)
    session.add_all(
        ImportIssue(
            dataset_id=dataset.id,
            row_number=i.row_number,
            column_name=i.column_name,
            raw_value=i.raw_value,
            message=i.message,
        )
        for i in result.issues
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return ImportOutcome(
            session.scalar(select(Dataset).where(Dataset.fingerprint == fingerprint)), created=False
        )
    # Planner statistics are stale right after a bulk COPY. Without this, ad-hoc and proof queries over a
    # fresh dataset can pick nested-loop plans (measured: >2 min instead of <1 s on 2 x 100k rows).
    session.execute(text("ANALYZE dataset_records"))
    session.commit()
    log.info(
        "dataset imported",
        extra={
            "dataset_id": str(dataset.id),
            "rows": dataset.row_count,
            "rejected": dataset.rejected_row_count,
        },
    )
    return ImportOutcome(dataset, created=True)


def _copy_records(session: Session, dataset_id: uuid.UUID, result: NormalizationResult) -> None:
    """Bulk load with COPY; row-by-row INSERTs are an order of magnitude slower at 100k+ rows."""
    columns = (
        "dataset_id, row_number, source_record_id, business_key, occurred_at, amount, currency, status,"
        " refund_amount, attributes, raw_payload_hash"
    )
    raw = session.connection().connection.driver_connection
    with raw.cursor() as cur, cur.copy(f"COPY dataset_records ({columns}) FROM STDIN") as copy:
        copy.set_types(
            [
                "uuid",
                "int4",
                "varchar",
                "varchar",
                "timestamptz",
                "numeric",
                "varchar",
                "varchar",
                "numeric",
                "jsonb",
                "varchar",
            ]
        )
        for r in result.records:
            copy.write_row(
                (
                    dataset_id,
                    r.row_number,
                    r.source_record_id,
                    r.business_key,
                    r.occurred_at,
                    r.amount,
                    r.currency,
                    r.status,
                    r.refund_amount,
                    Jsonb(r.attributes),
                    r.raw_payload_hash,
                )
            )
