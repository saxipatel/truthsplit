"""Render a metric definition as one SQL query over `dataset_records`.

The investigation engine evaluates metrics in Python, which keeps per-record contributions as evidence.
This query is the independent, reproducible form of the same number: reports include it, and tests check
it returns exactly what the Python evaluator does. User input only ever reaches the query as bind
parameters; attribute names included, since they are passed to the `->>` operator as values.
"""

import hashlib
import json
import uuid
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, NUMERIC, TEXT
from sqlalchemy.orm import Session

from app.comparison.metrics import NUMERIC_FIELDS, TEXT_FIELDS, MetricSpec
from app.models.metrics import Aggregation, Grain, RefundPolicy

_OPS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
# The only identifiers that can appear in generated SQL. Anything else raises KeyError.
_COLUMNS = {name: f"r.{name}" for name in (*TEXT_FIELDS, *NUMERIC_FIELDS)}


def metric_sql(spec: MetricSpec) -> tuple[str, dict]:
    params: dict = {}
    where = ["r.dataset_id = :dataset_id"]
    start, end = spec.window_utc()
    if start:
        where.append("r.occurred_at >= :window_start")
        params["window_start"] = start
    if end:
        where.append(f"r.occurred_at {'<=' if spec.window_end_inclusive else '<'} :window_end")
        params["window_end"] = end

    for i, f in enumerate(spec.filters):
        if f.field.startswith("attributes."):
            params[f"attr_{i}"] = f.field.removeprefix("attributes.")
            column = f"(r.attributes ->> :attr_{i})"
        else:
            column = _COLUMNS[f.field]
        p = f"f_{i}"
        match f.op:
            case "is_null":
                where.append(f"{column} IS NULL")
            case "not_null":
                where.append(f"{column} IS NOT NULL")
            case "in":
                where.append(f"{column} = ANY(:{p})")
                params[p] = f.value
            case "not_in":
                where.append(f"{column} <> ALL(:{p})")
                params[p] = f.value
            case _:
                where.append(f"{column} {_OPS[f.op]} :{p}")
                params[p] = f.value

    if spec.refund_policy is RefundPolicy.EXCLUDE_REFUNDED:
        where.append("COALESCE(r.refund_amount, 0) <= 0")

    value = _COLUMNS[spec.value_field]
    if spec.refund_policy is RefundPolicy.NET:
        value = f"{value} - COALESCE(r.refund_amount, 0)"

    # Only fixed fragments from this module are interpolated; every input value is a bind parameter.
    scoped = f"SELECT r.business_key, r.row_number, {value} AS metric_value FROM dataset_records r WHERE {' AND '.join(where)}"  # nosec B608
    if spec.grain is Grain.BUSINESS_KEY:
        grained = "SELECT DISTINCT ON (business_key) * FROM scoped WHERE business_key IS NOT NULL ORDER BY business_key, row_number"
    else:
        grained = "SELECT * FROM scoped"

    aggregate = {
        Aggregation.SUM: "COALESCE(SUM(metric_value), 0)",  # SUM skips NULLs: the `exclude` null policy
        Aggregation.COUNT: "COUNT(*)",
        Aggregation.COUNT_DISTINCT: "COUNT(DISTINCT business_key)",
    }[spec.aggregation]
    sql = (
        f"WITH scoped AS ({scoped}), grained AS ({grained}) "  # nosec B608 - fixed fragments, see above
        f"SELECT {aggregate} AS value, COUNT(*) FILTER (WHERE metric_value IS NULL) AS null_values FROM grained"
    )
    return sql, params


def _bind_types(params: dict):
    binds = []
    for name, value in params.items():
        if isinstance(value, list):
            binds.append(
                bindparam(name, type_=ARRAY(NUMERIC if value and isinstance(value[0], Decimal) else TEXT))
            )
    return binds


def run_metric_sql(session: Session, spec: MetricSpec, dataset_id: uuid.UUID) -> tuple[Decimal, int]:
    sql, params = metric_sql(spec)
    stmt = text(sql).bindparams(*_bind_types(params))
    row = session.execute(stmt, {**params, "dataset_id": dataset_id}).one()
    return Decimal(row.value), row.null_values


def query_hash(sql: str, params: dict) -> str:
    """Stable identifier for a proof query and its parameters (dataset id excluded, so it names the logic)."""
    payload = json.dumps({"sql": sql, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
