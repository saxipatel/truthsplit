from pydantic import BaseModel, Field


class ConnectionOut(BaseModel):
    name: str
    host: str | None
    port: int | None
    database: str | None
    host_allowed: bool


class ConnectionTestOut(BaseModel):
    name: str
    ok: bool
    user: str
    server_version: str
    role_read_only_default: bool
    problems: list[str]


class ColumnOut(BaseModel):
    name: str
    type: str


class TableOut(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str
    columns: list[ColumnOut]

    model_config = {"populate_by_name": True}


class ParamOut(BaseModel):
    name: str
    type: str
    description: str
    choices: list[str]


class JoinOut(BaseModel):
    left: str
    right: str
    cardinality: str


class RegisteredQueryOut(BaseModel):
    key: str
    title: str
    description: str
    connection: str
    sql: str
    sql_sha256: str
    params: list[ParamOut]
    mapping: dict
    tables: list[str]
    joins: list[JoinOut]
    grain: str
    notes: list[str]


class QueryRun(BaseModel):
    params: dict = Field(default_factory=dict)


class SnapshotCreate(QueryRun):
    label: str | None = Field(None, max_length=200)


class QueryPreviewOut(BaseModel):
    columns: list[str]
    rows: list[list[str | None]]
    truncated: bool


class FreshnessOut(BaseModel):
    dataset_id: str
    query_key: str | None
    status: str = Field(description="current | source_changed | query_changed | query_removed")
    detail: str | None = None
    snapshot_executed_at: str | None
    snapshot_content_sha256: str | None
    snapshot_rows: int | None
    current_content_sha256: str | None = None
    current_rows: int | None = None
    checked_at: str
