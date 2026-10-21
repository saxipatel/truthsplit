"""The allowlist of queries TruthSplit may run against a source database.

Users choose a registered query and supply parameter values; they cannot submit SQL. Each entry carries the
metadata Metric DNA shows (tables, joins, output grain), the mapping of its result columns onto the
normalized record contract, and a hash of its SQL so a snapshot records exactly which version ran.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

ParamType = Literal["date", "text", "integer"]


@dataclass(frozen=True)
class QueryParam:
    name: str
    type: ParamType
    description: str
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Join:
    left: str
    right: str
    cardinality: Literal["1:1", "N:1", "1:N"]


@dataclass(frozen=True)
class RegisteredQuery:
    key: str
    title: str
    description: str
    connection: str
    sql: str
    params: tuple[QueryParam, ...]
    mapping: dict
    tables: tuple[str, ...]
    joins: tuple[Join, ...] = ()
    # What one result row represents. Differs from the business key's grain when a join fans out.
    grain: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sql_hash(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def coerce_params(query: RegisteredQuery, values: dict) -> dict:
    """Validate user-supplied values against the declared parameters. Unknown or missing names are errors."""
    declared = {p.name: p for p in query.params}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValueError(f"unknown parameters: {', '.join(unknown)}")
    missing = sorted(set(declared) - set(values))
    if missing:
        raise ValueError(f"missing parameters: {', '.join(missing)}")
    out = {}
    for name, p in declared.items():
        raw = values[name]
        try:
            if p.type == "date":
                out[name] = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
            elif p.type == "integer":
                if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                    raise ValueError
                out[name] = int(raw)
            else:
                out[name] = str(raw)
        except ValueError:
            raise ValueError(f"parameter {name!r} must be a {p.type}") from None
        if p.choices and out[name] not in p.choices:
            raise ValueError(f"parameter {name!r} must be one of {', '.join(p.choices)}")
    return out


_WINDOW = (
    QueryParam("start_date", "date", "First day included"),
    QueryParam("end_date", "date", "First day excluded"),
)

ORDER_MAPPING = {
    "source_record_id": "order_number",
    "business_key": ["order_number"],
    "amount": "total_amount",
    "currency": "currency",
    "occurred_at": "ordered_at",
    "status": "status",
}

REGISTRY: dict[str, RegisteredQuery] = {
    q.key: q
    for q in (
        RegisteredQuery(
            key="finance_order_revenue",
            title="Finance: order revenue with refunds",
            description=(
                "One row per non-cancelled order with its refunded amount. The window is applied in "
                "America/New_York, the finance team's reporting zone."
            ),
            connection="demo",
            sql="""
SELECT o.order_number, o.ordered_at, o.total_amount, o.currency, o.status,
       COALESCE(r.refunded, 0) AS refunded, c.region
FROM orders o
JOIN customers c ON c.id = o.customer_id
LEFT JOIN (SELECT order_id, SUM(amount) AS refunded FROM refunds GROUP BY order_id) r ON r.order_id = o.id
WHERE o.status <> 'cancelled'
  AND o.ordered_at >= CAST(:start_date AS timestamp) AT TIME ZONE 'America/New_York'
  AND o.ordered_at < CAST(:end_date AS timestamp) AT TIME ZONE 'America/New_York'
""".strip(),
            params=_WINDOW,
            mapping={**ORDER_MAPPING, "refund_amount": "refunded", "attributes": ["region"]},
            tables=("orders", "customers", "refunds"),
            joins=(
                Join("orders.customer_id", "customers.id", "N:1"),
                Join("orders.id", "refunds.order_id (aggregated)", "1:1"),
            ),
            grain="order",
        ),
        RegisteredQuery(
            key="sales_order_payments",
            title="Sales: orders joined to payments",
            description=(
                "Order totals joined to their payments, as the sales dashboard does. The window is applied in UTC."
            ),
            connection="demo",
            sql="""
SELECT o.order_number, o.ordered_at, o.total_amount, o.currency, o.status, p.method
FROM orders o
JOIN payments p ON p.order_id = o.id
WHERE o.ordered_at >= CAST(:start_date AS timestamp) AT TIME ZONE 'UTC'
  AND o.ordered_at < CAST(:end_date AS timestamp) AT TIME ZONE 'UTC'
""".strip(),
            params=_WINDOW,
            mapping={**ORDER_MAPPING, "attributes": ["method"]},
            tables=("orders", "payments"),
            joins=(Join("orders.id", "payments.order_id", "1:N"),),
            grain="payment",
            notes=(
                "Orders paid in several parts appear once per payment, each row carrying the full order total.",
            ),
        ),
        RegisteredQuery(
            key="order_lines",
            title="Order lines",
            description="One row per order line, keyed by order number and line number.",
            connection="demo",
            sql="""
SELECT o.order_number, i.line_no, o.ordered_at, i.line_amount, o.currency, o.status, p.category
FROM order_items i
JOIN orders o ON o.id = i.order_id
JOIN products p ON p.id = i.product_id
WHERE o.ordered_at >= CAST(:start_date AS timestamp) AT TIME ZONE 'UTC'
  AND o.ordered_at < CAST(:end_date AS timestamp) AT TIME ZONE 'UTC'
""".strip(),
            params=_WINDOW,
            mapping={
                "business_key": ["order_number", "line_no"],
                "amount": "line_amount",
                "currency": "currency",
                "occurred_at": "ordered_at",
                "status": "status",
                "attributes": ["category"],
            },
            tables=("order_items", "orders", "products"),
            joins=(
                Join("order_items.order_id", "orders.id", "N:1"),
                Join("order_items.product_id", "products.id", "N:1"),
            ),
            grain="order line",
        ),
    )
}
