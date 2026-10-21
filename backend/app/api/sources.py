import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.adapters.postgres import (
    Profile,
    QueryTooLargeError,
    SourceError,
    catalog,
    get_profile,
    run_registered_query,
    source_profiles,
    test_connection,
)
from app.adapters.registered_queries import REGISTRY, RegisteredQuery, coerce_params
from app.api.deps import SessionDep, SettingsDep
from app.db.sources import check_freshness, snapshot_query
from app.models import Dataset
from app.models.sources import SourceType
from app.normalization.values import format_cell
from app.schemas.imports import DatasetImportResponse, DatasetOut
from app.schemas.sources import (
    ConnectionOut,
    ConnectionTestOut,
    FreshnessOut,
    QueryPreviewOut,
    QueryRun,
    RegisteredQueryOut,
    SnapshotCreate,
    TableOut,
)

router = APIRouter(prefix="/api", tags=["sources"])
PREVIEW_ROWS = 50


def _profile(name: str) -> Profile:
    try:
        return get_profile(name)
    except SourceError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


def _source_failure(exc: SourceError) -> HTTPException:
    if isinstance(exc, QueryTooLargeError):
        return HTTPException(422, detail=str(exc))
    # 502: the request was valid but the upstream database refused or failed it.
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(settings: SettingsDep) -> list[ConnectionOut]:
    out = []
    for name, url in sorted(source_profiles().items()):
        info = Profile(name, url).describe()
        out.append(
            ConnectionOut(
                **info, host_allowed=(info["host"] or "").lower() in settings.allowed_source_host_set
            )
        )
    return out


@router.post("/connections/{name}/test", response_model=ConnectionTestOut)
def test_source(name: str, settings: SettingsDep) -> ConnectionTestOut:
    try:
        report = test_connection(_profile(name), settings)
    except SourceError as exc:
        raise _source_failure(exc) from exc
    return ConnectionTestOut(
        name=name,
        ok=report.safe,
        user=report.user,
        server_version=report.server_version,
        role_read_only_default=report.read_only_default,
        problems=report.problems,
    )


@router.get("/connections/{name}/catalog", response_model=list[TableOut])
def source_catalog(name: str, settings: SettingsDep) -> list[TableOut]:
    try:
        return [TableOut(**t) for t in catalog(_profile(name), settings)]
    except SourceError as exc:
        raise _source_failure(exc) from exc


def _describe(q: RegisteredQuery) -> RegisteredQueryOut:
    return RegisteredQueryOut(
        key=q.key,
        title=q.title,
        description=q.description,
        connection=q.connection,
        sql=q.sql,
        sql_sha256=q.sql_hash,
        params=[
            {"name": p.name, "type": p.type, "description": p.description, "choices": list(p.choices)}
            for p in q.params
        ],
        mapping=q.mapping,
        tables=list(q.tables),
        joins=[j.__dict__ for j in q.joins],
        grain=q.grain,
        notes=list(q.notes),
    )


@router.get("/registered-queries", response_model=list[RegisteredQueryOut])
def list_registered_queries() -> list[RegisteredQueryOut]:
    return [_describe(q) for q in REGISTRY.values()]


def _query(key: str) -> RegisteredQuery:
    query = REGISTRY.get(key)
    if query is None:
        raise HTTPException(404, detail="Registered query not found.")
    return query


def _params(query: RegisteredQuery, values: dict) -> dict:
    try:
        return coerce_params(query, values)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/registered-queries/{key}", response_model=RegisteredQueryOut)
def get_registered_query(key: str) -> RegisteredQueryOut:
    return _describe(_query(key))


@router.post("/registered-queries/{key}/preview", response_model=QueryPreviewOut)
def preview_query(key: str, body: QueryRun, settings: SettingsDep) -> QueryPreviewOut:
    query = _query(key)
    params = _params(query, body.params)
    try:
        result = run_registered_query(
            _profile(query.connection), settings, query, params, max_rows=PREVIEW_ROWS + 1, preview=True
        )
    except SourceError as exc:
        raise _source_failure(exc) from exc
    rows = [[format_cell(v) for v in r] for r in result.rows[:PREVIEW_ROWS]]
    return QueryPreviewOut(columns=result.columns, rows=rows, truncated=len(result.rows) > PREVIEW_ROWS)


@router.post(
    "/registered-queries/{key}/snapshots",
    response_model=DatasetImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def snapshot(
    key: str, body: SnapshotCreate, session: SessionDep, settings: SettingsDep, response: Response
) -> DatasetImportResponse:
    query = _query(key)
    params = _params(query, body.params)
    try:
        outcome = snapshot_query(
            session, settings, _profile(query.connection), query, params, label=body.label
        )
    except SourceError as exc:
        raise _source_failure(exc) from exc
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return DatasetImportResponse(
        dataset=DatasetOut.model_validate(outcome.dataset), already_imported=not outcome.created
    )


@router.post("/datasets/{dataset_id}/freshness", response_model=FreshnessOut)
def snapshot_freshness(dataset_id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> FreshnessOut:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, detail="Dataset not found.")
    if dataset.source_type is not SourceType.POSTGRES_QUERY:
        raise HTTPException(422, detail="Only query snapshots have a source to check against.")
    try:
        return FreshnessOut(**check_freshness(session, settings, dataset))
    except SourceError as exc:
        raise _source_failure(exc) from exc
