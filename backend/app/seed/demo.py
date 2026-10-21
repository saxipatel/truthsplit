"""Demo scenarios and their oracle manifest.

Scenario A compares two registered queries over the demo source. Scenario B compares the finance query with a
ledger export (a CSV generated here) that has deliberately planted problems. Scenario C compares the sales query
with a settlement export whose amounts are already net of refunds. Every expected value below is
computed from the pure-Python source model in shop.py, not from the database or the investigation engine.

    python -m app.seed.demo     # rewrites the CSV files and manifest.json in sample_data/
"""

import csv
import io
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.seed.shop import Order, build_orders, local_month_bounds

ZERO = Decimal("0.00")
NY, UTC_ZONE = ZoneInfo("America/New_York"), ZoneInfo("UTC")
MARCH = {"start_date": "2026-03-01", "end_date": "2026-04-01"}
LEDGER_FILE = "finance_ledger_march.csv"
SETTLEMENT_FILE = "settlement_march.csv"
SETTLEMENT_COLUMNS = [
    "order_number",
    "settled_at",
    "gross_amount",
    "net_amount",
    "refunded",
    "currency",
    "status",
    "processor_ref",
]
SETTLEMENT_MAPPING = {
    "business_key": ["order_number"],
    "amount": "net_amount",
    "refund_amount": "refunded",
    "currency": "currency",
    "occurred_at": "settled_at",
    "status": "status",
    "attributes": ["processor_ref"],
}
LEDGER_COLUMNS = ["order_number", "posted_at", "gross_amount", "refunded", "currency", "status", "memo"]
LEDGER_MAPPING = {
    "business_key": ["order_number"],
    "amount": "gross_amount",
    "refund_amount": "refunded",
    "currency": "currency",
    "occurred_at": "posted_at",
    "status": "status",
    "attributes": ["memo"],
}
FINANCE_METRIC = {
    "name": "Finance net revenue (demo)",
    "aggregation": "sum",
    "refund_policy": "net_of_refunds",
}
SALES_METRIC = {"name": "Sales gross revenue (demo)", "aggregation": "sum", "refund_policy": "gross"}
GROSS_METRIC = {"name": "Gross revenue (demo)", "aggregation": "sum", "refund_policy": "gross"}
MANUAL_ADJUSTMENTS = [
    (Decimal("1250.00"), "2026-03-31T21:00:00+00:00", "manual adjustment: accrual"),
    (Decimal("-310.40"), "2026-03-15T09:00:00+00:00", "manual adjustment: write-off"),
    (Decimal("75.25"), "2026-03-02T10:30:00+00:00", "manual adjustment: fx rounding"),
    (Decimal("-18.10"), "2026-03-20T16:45:00+00:00", "manual adjustment: fee"),
]


def money(d: Decimal) -> str:
    return f"{d:.2f}"


def _in(o: Order, bounds) -> bool:
    return bounds[0] <= o.ordered_at < bounds[1]


def finance_orders(orders: list[Order]) -> list[Order]:
    ny = local_month_bounds(2026, 3, NY)
    return sorted((o for o in orders if o.status != "cancelled" and _in(o, ny)), key=lambda o: o.order_number)


def net(o: Order) -> Decimal:
    return o.total_amount - (o.refund or ZERO)


def scenario_a(orders: list[Order]) -> dict:
    fin = finance_orders(orders)
    utc = local_month_bounds(2026, 3, UTC_ZONE)
    sales = [o for o in orders if o.payments and _in(o, utc)]
    fin_ids, sales_ids = {o.id for o in fin}, {o.id for o in sales}
    refunded = [o for o in fin if o.refund]
    split = [o for o in sales if len(o.payments) > 1]
    fin_only = [o for o in fin if o.id not in sales_ids]
    sales_only = [o for o in sales if o.id not in fin_ids]
    left_total = sum((net(o) for o in fin), ZERO)
    right_total = sum((o.total_amount * len(o.payments) for o in sales), ZERO)
    issues = [
        {
            "id": "A1",
            "category": "refund_policy",
            "expected_status": "confirmed",
            "rule_id": "definition.refund_policy",
            "description": "Finance reports revenue net of refunds; Sales reports gross. Contribution is the refunds on finance's orders.",
            "affected": [o.order_number for o in refunded],
            "expected_contribution": money(sum((o.refund for o in refunded), ZERO)),
        },
        {
            "id": "A2",
            "category": "join_amplification",
            "expected_status": "confirmed",
            "rule_id": "data.join_amplification",
            "description": "The sales query joins orders to payments (1:N); orders paid in two parts appear twice with the full total.",
            "affected": [o.order_number for o in split],
            "expected_contribution": money(
                sum((o.total_amount * (len(o.payments) - 1) for o in split), ZERO)
            ),
        },
        {
            "id": "A3",
            "category": "date_cutoff",
            "expected_status": "confirmed",
            "rule_id": "data.query_window",
            "description": "Orders inside March in New York time but outside March in UTC (first hours of 1 March UTC).",
            "affected": [o.order_number for o in fin_only],
            "expected_contribution": money(-sum((o.total_amount for o in fin_only), ZERO)),
        },
        {
            "id": "A4",
            "category": "date_cutoff",
            "expected_status": "confirmed",
            "rule_id": "data.query_window",
            "description": "Orders inside March in UTC but after March in New York time (last hours of 31 March New York).",
            "affected": [o.order_number for o in sales_only],
            "expected_contribution": money(sum((o.total_amount for o in sales_only), ZERO)),
        },
    ]
    groups = {"A1": refunded, "A2": split, "A3": fin_only, "A4": sales_only}
    members = {name: {o.order_number for o in group} for name, group in groups.items()}
    overlaps = []
    for number in sorted(set().union(*members.values())):
        causes = [name for name, numbers in members.items() if number in numbers]
        if len(causes) > 1:
            overlaps.append({"order_number": number, "issues": causes})
    return {
        "id": "A",
        "title": "Demo A: March revenue, Finance vs Sales (fully explained)",
        "left": {"query": "finance_order_revenue", "params": MARCH, "metric": FINANCE_METRIC},
        "right": {"query": "sales_order_payments", "params": MARCH, "metric": SALES_METRIC},
        "expected": {
            "left_total": money(left_total),
            "right_total": money(right_total),
            "delta": money(right_total - left_total),
            "unexplained": "0.00",
        },
        "issues": issues,
        "overlaps": overlaps,
        "overlap_rule": (
            "Refunds are attributed in the definition replay on the left (finance) side, before record matching. "
            "Join copies are removed on the right (sales) side. A cutoff order therefore enters A3/A4 at its gross "
            "value, and a refunded or split order is counted once in each category it belongs to, never twice in one."
        ),
    }


def _ledger_plan(fin: list[Order]) -> dict[str, list[Order]]:
    plan = {
        "missing": [o for i, o in enumerate(fin) if i % 240 == 7],
        "typo": [o for i, o in enumerate(fin) if i % 300 == 50],
        "duplicate": [o for i, o in enumerate(fin) if i % 500 == 123],
        "fan_out": [o for i, o in enumerate(fin) if i % 410 == 200],
    }
    counts = Counter(o.id for group in plan.values() for o in group)
    if any(n > 1 for n in counts.values()):
        raise ValueError("planted problems must not overlap")
    return plan


def _typo_delta(k: int) -> Decimal:
    return Decimal("90.00") if k % 2 == 0 else Decimal("-9.00")


def ledger_rows(orders: list[Order]) -> list[list[str]]:
    fin = finance_orders(orders)
    plan = _ledger_plan(fin)
    missing = {o.id for o in plan["missing"]}
    typos = {o.id: _typo_delta(k) for k, o in enumerate(plan["typo"])}
    dups = {o.id for o in plan["duplicate"]}
    fans = {o.id for o in plan["fan_out"]}
    rows = []
    for o in fin:
        if o.id in missing:
            continue
        gross = o.total_amount + typos.get(o.id, ZERO)
        base = [
            o.order_number,
            o.ordered_at.isoformat(),
            money(gross),
            money(o.refund or ZERO),
            "USD",
            o.status,
        ]
        rows.append([*base, "card payment"])
        if o.id in dups:
            rows.append([*base, "card payment"])
        if o.id in fans:
            rows.append([*base, "gift card payment"])
    for amount, at, memo in MANUAL_ADJUSTMENTS:
        rows.append(["", at, money(amount), "", "USD", "posted", memo])
    return rows


def ledger_csv(orders: list[Order]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(LEDGER_COLUMNS)
    w.writerows(ledger_rows(orders))
    return buf.getvalue().encode()


def scenario_b(orders: list[Order]) -> dict:
    fin = finance_orders(orders)
    plan = _ledger_plan(fin)
    typos = [(o, _typo_delta(k)) for k, o in enumerate(plan["typo"])]
    for o, d in typos:  # the refund-identity rule must not mistake a typo for a refund
        if o.refund is not None and abs(d) == o.refund:
            raise ValueError(f"keying error on {o.order_number} would be indistinguishable from its refund")
    left_total = sum((net(o) for o in fin), ZERO)
    right_total = (
        left_total
        - sum((net(o) for o in plan["missing"]), ZERO)
        + sum((d for _, d in typos), ZERO)
        + sum((net(o) for o in plan["duplicate"]), ZERO)
        + sum((net(o) for o in plan["fan_out"]), ZERO)
        + sum((a for a, _, _ in MANUAL_ADJUSTMENTS), ZERO)
    )
    fan_excess = sum((net(o) for o in plan["fan_out"]), ZERO)
    typo_total = sum((d for _, d in typos), ZERO)
    issues = [
        {
            "id": "B1",
            "category": "missing_records",
            "expected_status": "confirmed",
            "rule_id": "data.missing_keys",
            "description": "Orders not loaded into the ledger export.",
            "affected": [o.order_number for o in plan["missing"]],
            "expected_contribution": money(-sum((net(o) for o in plan["missing"]), ZERO)),
        },
        {
            "id": "B2",
            "category": "amount_mismatch",
            "expected_status": "confirmed",
            "rule_id": "data.value_mismatch",
            "description": "Keying errors in the ledger's gross amount (+90.00 / -9.00 alternately).",
            "affected": [o.order_number for o, _ in typos],
            "expected_contribution": money(typo_total),
        },
        {
            "id": "B3",
            "category": "duplicate_records",
            "expected_status": "confirmed",
            "rule_id": "data.exact_duplicates",
            "description": "Ledger lines exported twice, identical in every column.",
            "affected": [o.order_number for o in plan["duplicate"]],
            "expected_contribution": money(sum((net(o) for o in plan["duplicate"]), ZERO)),
        },
        {
            "id": "B4",
            "category": "join_amplification",
            "expected_status": "suspected",
            "rule_id": "data.join_amplification",
            "description": (
                "Orders repeated once per payment memo with the full amount. A file carries no join metadata, so this "
                "is only suspected and its amount stays in the record-level mismatch (B2's finding)."
            ),
            "affected": [o.order_number for o in plan["fan_out"]],
            "expected_contribution": None,
            "indicative_amount": money(fan_excess),
        },
        {
            "id": "B5",
            "category": "unexplained",
            "expected_status": "unexplained",
            "rule_id": "data.unmatchable_rows",
            "description": "Manual journal lines with no order number; nothing on the finance side to match them to.",
            "affected": [memo for _, _, memo in MANUAL_ADJUSTMENTS],
            "expected_contribution": money(sum((a for a, _, _ in MANUAL_ADJUSTMENTS), ZERO)),
        },
    ]
    return {
        "id": "B",
        "title": "Demo B: March revenue, finance system vs ledger export (unexplained remainder)",
        "left": {"query": "finance_order_revenue", "params": MARCH, "metric": FINANCE_METRIC},
        "right": {"file": LEDGER_FILE, "mapping": LEDGER_MAPPING, "metric": FINANCE_METRIC},
        "expected": {
            "left_total": money(left_total),
            "right_total": money(right_total),
            "delta": money(right_total - left_total),
            "unexplained": money(sum((a for a, _, _ in MANUAL_ADJUSTMENTS), ZERO)),
            # The record-level mismatch finding holds the keying errors plus the unattributed fan-out copies.
            "amount_mismatch_finding": money(typo_total + fan_excess),
            "right_duplicate_keys": len(plan["duplicate"]) + len(plan["fan_out"]),
        },
        "issues": issues,
        "overlaps": [],
    }


def sales_orders(orders: list[Order]) -> list[Order]:
    utc = local_month_bounds(2026, 3, UTC_ZONE)
    return sorted((o for o in orders if o.payments and _in(o, utc)), key=lambda o: o.order_number)


def _settlement_plan(sales: list[Order]) -> dict[str, dict[int, Decimal]]:
    refunded = [o for o in sales if o.refund]
    plain = [o for o in sales if not o.refund]
    plan = {
        # a keying error on a refunded order: the difference is no longer exactly the refund
        "refund_plus_error": {o.id: Decimal("1.00") for i, o in enumerate(refunded) if i % 40 == 5},
        "plain_error": {o.id: Decimal("12.50") for i, o in enumerate(plain) if i % 600 == 77},
    }
    for o in refunded:
        error = plan["refund_plus_error"].get(o.id)
        if error is not None and abs(-o.refund + error) == o.refund:
            raise ValueError(f"keying error on {o.order_number} would be indistinguishable from its refund")
    return plan


def settlement_csv(orders: list[Order]) -> bytes:
    sales = sales_orders(orders)
    plan = _settlement_plan(sales)
    errors = {**plan["refund_plus_error"], **plan["plain_error"]}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(SETTLEMENT_COLUMNS)
    for o in sales:
        settled = net(o) + errors.get(o.id, ZERO)
        w.writerow(
            [
                o.order_number,
                o.ordered_at.isoformat(),
                money(o.total_amount),
                money(settled),
                money(o.refund or ZERO),
                "USD",
                o.status,
                f"PR-{o.id:06d}",
            ]
        )
    return buf.getvalue().encode()


def scenario_c(orders: list[Order]) -> dict:
    sales = sales_orders(orders)
    plan = _settlement_plan(sales)
    split = [o for o in sales if len(o.payments) > 1]
    refunded = [o for o in sales if o.refund]
    identity = [o for o in refunded if o.id not in plan["refund_plus_error"]]
    errors = [o for o in sales if o.id in plan["refund_plus_error"] or o.id in plan["plain_error"]]
    left_total = sum((o.total_amount * len(o.payments) for o in sales), ZERO)
    right_total = sum((net(o) for o in sales), ZERO) + sum((sum(plan[k].values(), ZERO) for k in plan), ZERO)
    mismatch = sum(
        (
            -(o.refund or ZERO) + plan["refund_plus_error"][o.id]
            for o in errors
            if o.id in plan["refund_plus_error"]
        ),
        ZERO,
    ) + sum(plan["plain_error"].values(), ZERO)
    issues = [
        {
            "id": "C1",
            "category": "join_amplification",
            "expected_status": "confirmed",
            "rule_id": "data.join_amplification",
            "description": "Left is the sales query (orders joined to payments, 1:N declared); split-payment orders appear twice.",
            "affected": [o.order_number for o in split],
            "expected_contribution": money(
                -sum((o.total_amount * (len(o.payments) - 1) for o in split), ZERO)
            ),
        },
        {
            "id": "C2",
            "category": "refund_policy",
            "expected_status": "confirmed",
            "rule_id": "data.refund_identity",
            "description": (
                "The settlement export reports each order net of its refund and records the refund. Both metrics are "
                "gross, so the definitions agree; the difference on these orders is exactly the recorded refund."
            ),
            "affected": [o.order_number for o in identity],
            "expected_contribution": money(-sum((o.refund for o in identity), ZERO)),
        },
        {
            "id": "C3",
            "category": "amount_mismatch",
            "expected_status": "confirmed",
            "rule_id": "data.value_mismatch",
            "description": (
                "Settlement amounts with a keying error. On refunded orders the difference is refund + error, which is "
                "not exactly the refund, so the whole difference stays a mismatch rather than being split by guesswork."
            ),
            "affected": [o.order_number for o in errors],
            "expected_contribution": money(mismatch),
        },
    ]
    members = {
        "C1": {o.order_number for o in split},
        "C2": {o.order_number for o in identity},
        "C3": {o.order_number for o in errors},
    }
    overlaps = [
        {"order_number": n, "issues": [k for k, v in members.items() if n in v]}
        for n in sorted(set().union(*members.values()))
        if sum(n in v for v in members.values()) > 1
    ]
    return {
        "id": "C",
        "title": "Demo C: March gross revenue, sales query vs settlement export (refunds netted at source)",
        "left": {"query": "sales_order_payments", "params": MARCH, "metric": GROSS_METRIC},
        "right": {"file": SETTLEMENT_FILE, "mapping": SETTLEMENT_MAPPING, "metric": GROSS_METRIC},
        "expected": {
            "left_total": money(left_total),
            "right_total": money(right_total),
            "delta": money(right_total - left_total),
            "unexplained": "0.00",
        },
        "issues": issues,
        "overlaps": overlaps,
        "overlap_rule": (
            "Join copies are removed from the left before record matching; the refund identity is then tested on the "
            "de-duplicated order, so a split and refunded order contributes once to C1 (its extra copy) and once to C2 "
            "(its refund)."
        ),
    }


FILE_BUILDERS = {LEDGER_FILE: ledger_csv, SETTLEMENT_FILE: settlement_csv}


def manifest() -> dict:
    orders = build_orders()
    return {
        "description": "Oracle for the TruthSplit demo scenarios. Values are computed from app/seed/shop.py, a pure-Python model of docker/source-db/02-shop.sh.",
        "source": {
            "orders": len(orders),
            "order_items": sum(len(o.lines) for o in orders),
            "payments": sum(len(o.payments) for o in orders),
            "refunds": sum(1 for o in orders if o.refund),
            "cancelled": sum(1 for o in orders if o.status == "cancelled"),
        },
        "delta_convention": "delta = right - left",
        "scenarios": [scenario_a(orders), scenario_b(orders), scenario_c(orders)],
    }


def write(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    orders = build_orders()
    for name, build in FILE_BUILDERS.items():
        (out_dir / name).write_bytes(build(orders))
    (out_dir / "manifest.json").write_text(json.dumps(manifest(), indent=2) + "\n")


if __name__ == "__main__":
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3] / "sample_data")
