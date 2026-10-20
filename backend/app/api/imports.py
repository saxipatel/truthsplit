import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select

from app.adapters.files import IngestionError, UnsupportedFileError, read_table
from app.api.deps import SessionDep, SettingsDep
from app.db.imports import UploadTooLargeError, create_dataset, store_upload, upload_path
from app.models import Dataset, DatasetRecord, FileUpload, ImportIssue
from app.models.sources import FileType
from app.normalization.values import format_cell, infer_type, is_empty
from app.schemas.imports import (
    ColumnProfile,
    DatasetCreate,
    DatasetImportResponse,
    DatasetOut,
    IssueOut,
    Page,
    Preview,
    RecordOut,
    UploadOut,
    UploadResponse,
)

router = APIRouter(prefix="/api", tags=["imports"])

PREVIEW_ROWS = 50
PROFILE_ROWS = 1000


def _preview(upload: FileUpload, settings, sheet: str | None, encoding: str, limit: int) -> Preview:
    if upload.file_type is not FileType.CSV and sheet is None and upload.sheet_names:
        sheet = upload.sheet_names[0]
    path = upload_path(settings.storage_dir, upload)
    if not path.exists():
        raise IngestionError("The stored file for this upload is missing; upload it again.")
    table = read_table(
        path, upload.file_type, sheet=sheet, encoding=encoding, max_rows=PROFILE_ROWS, preview=True
    )
    columns = []
    for i, name in enumerate(table.header):
        values = [row[i] if i < len(row) else None for row in table.rows]
        columns.append(
            ColumnProfile(
                name=name,
                inferred_type=infer_type(values),
                empty_count=sum(1 for v in values if is_empty(v)),
                sample=[format_cell(v) for v in values[:5]],
            )
        )
    shown = table.rows[:limit]
    return Preview(
        sheet=sheet,
        header=table.header,
        row_numbers=table.row_numbers[:limit],
        rows=[[format_cell(c) for c in row] for row in shown],
        columns=columns,
        truncated=table.truncated or len(table.rows) > limit,
    )


def _ingestion_http_error(exc: IngestionError) -> HTTPException:
    code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE if isinstance(exc, UnsupportedFileError) else 422
    return HTTPException(code, detail=str(exc))


@router.post("/imports/file", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_file(
    file: UploadFile, session: SessionDep, settings: SettingsDep, response: Response
) -> UploadResponse:
    try:
        stored = store_upload(
            session,
            file.file,
            file.filename,
            storage_dir=settings.storage_dir,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except IngestionError as exc:
        raise _ingestion_http_error(exc) from exc

    preview, preview_error = None, None
    try:
        preview = _preview(stored.upload, settings, None, "utf-8", PREVIEW_ROWS)
    except IngestionError as exc:
        preview_error = str(exc)
    if not stored.created:
        response.status_code = status.HTTP_200_OK
    return UploadResponse(
        upload=UploadOut.model_validate(stored.upload),
        already_uploaded=not stored.created,
        preview=preview,
        preview_error=preview_error,
    )


def _get_upload(session, upload_id: uuid.UUID) -> FileUpload:
    upload = session.get(FileUpload, upload_id)
    if upload is None:
        raise HTTPException(404, detail="Upload not found.")
    return upload


@router.get("/imports/{upload_id}/preview", response_model=Preview)
def preview_upload(
    upload_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    sheet: str | None = None,
    encoding: str = "utf-8",
    limit: Annotated[int, Query(ge=1, le=500)] = PREVIEW_ROWS,
) -> Preview:
    upload = _get_upload(session, upload_id)
    try:
        return _preview(upload, settings, sheet, encoding, limit)
    except IngestionError as exc:
        raise _ingestion_http_error(exc) from exc


@router.post("/datasets", response_model=DatasetImportResponse, status_code=status.HTTP_201_CREATED)
def import_dataset(
    body: DatasetCreate, session: SessionDep, settings: SettingsDep, response: Response
) -> DatasetImportResponse:
    upload = _get_upload(session, body.upload_id)
    try:
        outcome = create_dataset(
            session,
            upload,
            body.mapping,
            storage_dir=settings.storage_dir,
            max_rows=settings.max_rows,
            sheet=body.sheet,
            encoding=body.encoding,
            label=body.label,
        )
    except IngestionError as exc:
        raise _ingestion_http_error(exc) from exc
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return DatasetImportResponse(
        dataset=DatasetOut.model_validate(outcome.dataset), already_imported=not outcome.created
    )


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(session: SessionDep) -> list[Dataset]:
    return list(session.scalars(select(Dataset).order_by(Dataset.created_at.desc())))


def _get_dataset(session, dataset_id: uuid.UUID) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, detail="Dataset not found.")
    return dataset


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: uuid.UUID, session: SessionDep) -> Dataset:
    return _get_dataset(session, dataset_id)


Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=1000)]


@router.get("/datasets/{dataset_id}/records", response_model=Page[RecordOut])
def list_records(dataset_id: uuid.UUID, session: SessionDep, offset: Offset = 0, limit: Limit = 100) -> Page:
    _get_dataset(session, dataset_id)
    total = session.scalar(select(func.count()).where(DatasetRecord.dataset_id == dataset_id))
    rows = session.scalars(
        select(DatasetRecord)
        .where(DatasetRecord.dataset_id == dataset_id)
        .order_by(DatasetRecord.row_number)
        .offset(offset)
        .limit(limit)
    )
    return Page(total=total, offset=offset, items=[RecordOut.model_validate(r) for r in rows])


@router.get("/datasets/{dataset_id}/issues", response_model=Page[IssueOut])
def list_issues(dataset_id: uuid.UUID, session: SessionDep, offset: Offset = 0, limit: Limit = 100) -> Page:
    _get_dataset(session, dataset_id)
    total = session.scalar(select(func.count()).where(ImportIssue.dataset_id == dataset_id))
    rows = session.scalars(
        select(ImportIssue)
        .where(ImportIssue.dataset_id == dataset_id)
        .order_by(ImportIssue.row_number, ImportIssue.id)
        .offset(offset)
        .limit(limit)
    )
    return Page(total=total, offset=offset, items=[IssueOut.model_validate(r) for r in rows])
