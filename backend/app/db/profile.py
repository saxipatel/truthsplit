"""Profile a stored dataset with aggregate SQL over its normalized records (no extra storage)."""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

PROFILE_SQL = text(
    """
    SELECT count(*) AS rows,
           count(business_key) AS keyed_rows,
           count(DISTINCT business_key) AS distinct_keys,
           count(amount) AS amount_values,
           sum(amount) AS amount_sum,
           min(amount) AS amount_min,
           max(amount) AS amount_max,
           count(*) FILTER (WHERE amount < 0) AS negative_amounts,
           count(*) FILTER (WHERE amount = 0) AS zero_amounts,
           count(refund_amount) AS refund_values,
           count(*) FILTER (WHERE refund_amount > 0) AS refunded_rows,
           sum(refund_amount) AS refund_sum,
           count(occurred_at) AS timestamp_values,
           min(occurred_at) AS first_at,
           max(occurred_at) AS last_at,
           count(DISTINCT raw_payload_hash) AS distinct_rows
    FROM dataset_records WHERE dataset_id = :d
    """
)

DUPLICATE_KEYS_SQL = text(
    "SELECT count(*) FROM (SELECT business_key FROM dataset_records WHERE dataset_id = :d AND business_key IS NOT NULL"
    " GROUP BY business_key HAVING count(*) > 1) t"
)
GROUPS_SQL = {
    "currencies": text(
        "SELECT currency, count(*) FROM dataset_records WHERE dataset_id = :d GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
    ),
    "statuses": text(
        "SELECT status, count(*) FROM dataset_records WHERE dataset_id = :d GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
    ),
}
ATTRIBUTES_SQL = text(
    "SELECT key, count(DISTINCT value) AS distinct_values, count(*) FILTER (WHERE value IS NULL OR value = 'null') AS empty"
    " FROM dataset_records, jsonb_each_text(attributes) AS kv(key, value) WHERE dataset_id = :d GROUP BY key ORDER BY key"
)
ISSUES_SQL = text(
    "SELECT column_name, count(*) FROM import_issues WHERE dataset_id = :d GROUP BY 1 ORDER BY 2 DESC, 1"
)


def _s(v):
    return None if v is None else str(v)


def profile_dataset(session: Session, dataset_id: uuid.UUID) -> dict:
    p = session.execute(PROFILE_SQL, {"d": dataset_id}).mappings().one()
    rows = p["rows"]
    return {
        "rows": rows,
        "business_keys": {
            "distinct": p["distinct_keys"],
            "rows_without_key": rows - p["keyed_rows"],
            "repeated_keys": session.execute(DUPLICATE_KEYS_SQL, {"d": dataset_id}).scalar(),
        },
        "exact_duplicate_rows": rows - p["distinct_rows"],
        "amount": {
            "values": p["amount_values"],
            "missing": rows - p["amount_values"],
            "sum": _s(p["amount_sum"]),
            "min": _s(p["amount_min"]),
            "max": _s(p["amount_max"]),
            "negative": p["negative_amounts"],
            "zero": p["zero_amounts"],
        },
        "refund": {
            "values": p["refund_values"],
            "refunded_rows": p["refunded_rows"],
            "sum": _s(p["refund_sum"]),
        },
        "occurred_at": {
            "values": p["timestamp_values"],
            "missing": rows - p["timestamp_values"],
            "first": p["first_at"].isoformat() if p["first_at"] else None,
            "last": p["last_at"].isoformat() if p["last_at"] else None,
        },
        **{
            name: [{"value": v, "rows": n} for v, n in session.execute(sql, {"d": dataset_id})]
            for name, sql in GROUPS_SQL.items()
        },
        "attributes": [
            {"name": k, "distinct_values": d, "empty": e}
            for k, d, e in session.execute(ATTRIBUTES_SQL, {"d": dataset_id})
        ],
        "rejected_by_column": [
            {"column": c, "rows": n} for c, n in session.execute(ISSUES_SQL, {"d": dataset_id})
        ],
    }
