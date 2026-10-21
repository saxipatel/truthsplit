"""Snapshot registered query results into datasets, so investigations over database sources replay from
stored data exactly like file imports do."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.files import RawTable
from app.adapters.postgres import Profile, get_profile, run_registered_query
from app.adapters.registered_queries import REGISTRY, RegisteredQuery, coerce_params
from app.core.config import Settings
from app.db.imports import NORMALIZER_VERSION, ImportOutcome, store_dataset
from app.models import Dataset, SourceConnection
from app.models.sources import SourceType
from app.normalization.mapping import ColumnMapping, normalize, row_hash


def get_or_create_connection(session: Session, profile: Profile) -> SourceConnection:
    conn = session.scalar(select(SourceConnection).where(SourceConnection.config_reference == profile.name))
    if conn is None:
        conn = SourceConnection(label=profile.name, config_reference=profile.name)
        session.add(conn)
        session.flush()
    return conn


def query_hash(query: RegisteredQuery, params: dict) -> str:
    """Identifies the exact SQL text plus parameter values that produced a snapshot."""
    payload = json.dumps({"sql": query.sql, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def content_hash(rows) -> str:
    """Hash of a query result in its stored (fully sorted) order."""
    return hashlib.sha256("".join(row_hash(list(r)) for r in rows).encode()).hexdigest()


def snapshot_query(
    session: Session,
    settings: Settings,
    profile: Profile,
    query: RegisteredQuery,
    params: dict,
    *,
    label: str | None = None,
    is_demo: bool = False,
) -> ImportOutcome:
    result = run_registered_query(profile, settings, query, params, max_rows=settings.max_rows)
    # Query rows have no header line, so their row numbers are result positions starting at 1.
    table = RawTable(
        header=result.columns,
        rows=[list(r) for r in result.rows],
        row_numbers=list(range(1, len(result.rows) + 1)),
        strict_width=True,
    )
    mapping = ColumnMapping(**query.mapping)
    normalized = normalize(table, mapping)

    content = content_hash(table.rows)
    qhash = query_hash(query, params)
    # The same query, parameters and returned data map to one dataset; if the source data changed, a new
    # snapshot is created and the earlier one is kept.
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "query": qhash,
                "content": content,
                "mapping": mapping.model_dump(mode="json"),
                "normalizer": NORMALIZER_VERSION,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = session.scalar(select(Dataset).where(Dataset.fingerprint == fingerprint))
    if existing:
        return ImportOutcome(existing, created=False)

    connection = get_or_create_connection(session, profile)
    params_json = {k: str(v) for k, v in params.items()}
    dataset = Dataset(
        id=uuid.uuid4(),
        label=label or f"{query.title} ({', '.join(f'{k}={v}' for k, v in params_json.items())})",
        source_type=SourceType.POSTGRES_QUERY,
        source_connection_id=connection.id,
        query_key=query.key,
        query_params=params_json,
        query_hash=qhash,
        column_mapping=mapping.model_dump(mode="json"),
        schema_json={
            "header": result.columns,
            "rows_returned": len(result.rows),
            "content_sha256": content,
            "sql": query.sql,
            "sql_sha256": query.sql_hash,
            "tables": list(query.tables),
            "joins": [j.__dict__ for j in query.joins],
            "grain": query.grain,
            "executed_at": datetime.now(UTC).isoformat(),
            "server_version": result.server_version,
            "role": result.role,
            "null_business_keys": normalized.null_business_keys,
            "normalizer_version": NORMALIZER_VERSION,
        },
        fingerprint=fingerprint,
        row_count=len(normalized.records),
        rejected_row_count=normalized.rejected_rows,
        is_demo=is_demo,
    )
    return store_dataset(session, dataset, normalized)


def check_freshness(session: Session, settings: Settings, dataset: Dataset) -> dict:
    """Compare a stored snapshot with what its query returns now. Snapshots are never updated in place;
    a changed result means taking a new snapshot (and re-running investigations against it)."""
    snap = dataset.schema_json
    base = {
        "dataset_id": str(dataset.id),
        "query_key": dataset.query_key,
        "snapshot_executed_at": snap.get("executed_at"),
        "snapshot_content_sha256": snap.get("content_sha256"),
        "snapshot_rows": snap.get("rows_returned"),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    query = REGISTRY.get(dataset.query_key or "")
    if query is None:
        return {**base, "status": "query_removed", "detail": "The registered query no longer exists."}
    if query.sql_hash != snap.get("sql_sha256"):
        return {
            **base,
            "status": "query_changed",
            "detail": "The registered SQL changed since this snapshot; results are not comparable.",
        }
    params = coerce_params(query, dataset.query_params or {})
    profile = get_profile(dataset.source_connection.config_reference)
    result = run_registered_query(profile, settings, query, params, max_rows=settings.max_rows)
    current = content_hash(result.rows)
    status = "current" if current == snap.get("content_sha256") else "source_changed"
    return {**base, "status": status, "current_content_sha256": current, "current_rows": len(result.rows)}
