"""Pure-Python model of the demo `shop` source data.

It reproduces docker/source-db/02-shop.sh row for row from the same integer arithmetic, without touching a
database. The demo manifest's expected values are computed from this model, so they are independent of both
the source database and the investigation engine. A test asserts the model matches the seeded database.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
BASE = datetime(2026, 3, 1, tzinfo=UTC)
N_ORDERS = 6000


def pg_round(value: Decimal) -> Decimal:
    """PostgreSQL round(numeric, 2): half away from zero."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Order:
    id: int
    order_number: str
    customer_id: int
    status: str
    ordered_at: datetime
    total_amount: Decimal = Decimal("0.00")
    lines: list[tuple[int, int, int, Decimal, Decimal]] = field(
        default_factory=list
    )  # line, product, qty, price, amount
    payments: list[tuple[str, Decimal, datetime]] = field(default_factory=list)  # method, amount, paid_at
    refund: Decimal | None = None
    refund_reason: str | None = None


def unit_price(product_id: int) -> Decimal:
    return Decimal(5 + (product_id * 37) % 200) + Decimal(product_id % 4) * Decimal("0.25")


def region(customer_id: int) -> str:
    return ["NA", "EU", "APAC", "LATAM"][customer_id % 4]


def build_orders() -> list[Order]:
    orders = []
    for i in range(1, N_ORDERS + 1):
        o = Order(
            id=i,
            order_number=f"SO-{i:05d}",
            customer_id=1 + (i * 31) % 200,
            status="cancelled" if 85 <= (i * 13) % 100 <= 89 else "completed",
            ordered_at=BASE + timedelta(minutes=(i * 7919) % (61 * 24 * 60)),
        )
        for line in range(1, 2 + (i * 17) % 4):
            product = 1 + (i * 11 + line * 7) % 40
            qty = 1 + (i + line) % 3
            price = unit_price(product)
            o.lines.append((line, product, qty, price, qty * price))
        o.total_amount = sum((amount for *_, amount in o.lines), Decimal("0.00"))
        if o.status == "completed":
            paid_at = o.ordered_at + timedelta(minutes=i % 90)
            if i % 12 == 0:
                first = pg_round(o.total_amount * Decimal("0.6"))
                o.payments = [("card", first, paid_at), ("gift_card", o.total_amount - first, paid_at)]
            else:
                o.payments = [("card", o.total_amount, paid_at)]
            if i % 25 == 7:
                o.refund, o.refund_reason = o.total_amount, "full"
            elif i % 10 == 3:
                o.refund, o.refund_reason = pg_round(o.total_amount * Decimal("0.25")), "partial"
        orders.append(o)
    return orders


def local_month_bounds(year: int, month: int, tz) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=tz).astimezone(UTC)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=tz).astimezone(UTC)
    return start, end
