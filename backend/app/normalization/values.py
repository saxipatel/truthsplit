"""Strict value parsers. Each returns None for an empty cell (unknown, not zero) or raises ValueError
with a message that explains why the raw value was rejected. Nothing is rounded or guessed."""

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.adapters.files import Cell

MONEY_SCALE = 4
MONEY_MAX = Decimal(10) ** (18 - MONEY_SCALE)  # numeric(18, 4)
_MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_SCALE)

# Plain decimal, optionally with comma thousands separators in the usual 3-digit groups.
_AMOUNT_RE = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def is_empty(value: Cell) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_amount(value: Cell) -> Decimal | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    if isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        # Spreadsheet numbers arrive as binary floats. repr() gives the shortest decimal string that
        # round-trips, which is what the spreadsheet displayed (e.g. 19.99, not 19.989999999999998).
        amount = Decimal(repr(value))
    elif isinstance(value, Decimal):
        amount = value
    elif isinstance(value, str):
        text = value.strip()
        if not _AMOUNT_RE.match(text):
            raise ValueError(
                "not a plain decimal number (currency symbols, spaces and parentheses are not accepted)"
            )
        amount = Decimal(text.replace(",", ""))
    else:
        raise ValueError(f"expected a number, got {type(value).__name__}")

    if not amount.is_finite():
        raise ValueError("not a finite number")
    if abs(amount) >= MONEY_MAX:
        raise ValueError("too large for numeric(18,4)")
    if amount.as_tuple().exponent < -MONEY_SCALE:
        # Trailing zeros beyond the scale (1.50000) are harmless; any other extra digit would be lost.
        quantized = amount.quantize(_MONEY_QUANTUM)
        if quantized != amount:
            raise ValueError(f"more than {MONEY_SCALE} decimal places")
        amount = quantized
    return amount


def localize(naive: datetime, tz: ZoneInfo) -> datetime:
    """Attach a zone to a wall-clock time, refusing times that DST makes ambiguous or impossible."""
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    if first.utcoffset() != second.utcoffset():
        round_trip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
        if round_trip != naive:
            raise ValueError(f"local time does not exist in {tz.key} (DST gap)")
        raise ValueError(f"local time is ambiguous in {tz.key} (DST overlap)")
    return first


def parse_timestamp(value: Cell, tz: ZoneInfo, fmt: str | None = None) -> datetime | None:
    """Return an aware UTC datetime. Values without an offset are read as wall-clock time in `tz`."""
    if is_empty(value):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time())
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.strptime(text, fmt) if fmt else datetime.fromisoformat(text)
        except ValueError:
            expected = f"format {fmt!r}" if fmt else "ISO 8601 (e.g. 2026-03-31 or 2026-03-31T14:05:00Z)"
            raise ValueError(f"not a date/time in {expected}") from None
    else:
        raise ValueError(f"expected a date/time, got {type(value).__name__}")

    if parsed.tzinfo is None:
        parsed = localize(parsed, tz)
    return parsed.astimezone(UTC)


def parse_currency(value: Cell) -> str | None:
    if is_empty(value):
        return None
    code = str(value).strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ValueError("not a 3-letter ISO 4217 currency code")
    return code


def parse_text(value: Cell) -> str | None:
    if is_empty(value):
        return None
    return format_cell(value).strip()


def format_cell(value: Cell) -> str | None:
    """Render a raw cell as text for previews, hashes and free-form attributes."""
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def infer_type(values: list[Cell]) -> str:
    """Best-effort label for the preview. It does not affect how values are parsed."""
    present = [v for v in values if not is_empty(v)]
    if not present:
        return "empty"

    def all_match(check) -> bool:
        for v in present:
            try:
                check(v)
            except (ValueError, TypeError):
                return False
        return True

    if all(isinstance(v, bool) for v in present):
        return "boolean"
    if all(_looks_integer(v) for v in present):
        return "integer"
    if all_match(parse_amount):
        return "decimal"
    if all(_looks_date(v) for v in present):
        return "date"
    if all_match(lambda v: parse_timestamp(v, ZoneInfo("UTC"))):
        return "datetime"
    return "text"


def _looks_integer(v: Cell) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, str) and re.fullmatch(r"[+-]?\d+", v.strip()) is not None)


def _looks_date(v: Cell) -> bool:
    if isinstance(v, datetime):
        return v.time() == time() and v.tzinfo is None
    return isinstance(v, date) or (
        isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()) is not None
    )
