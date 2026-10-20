from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.normalization.values import infer_type, parse_amount, parse_currency, parse_timestamp

NY = ZoneInfo("America/New_York")


class TestParseAmount:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("10", Decimal("10")),
            (" -12.34 ", Decimal("-12.34")),
            ("+5.5", Decimal("5.5")),
            ("1,234,567.89", Decimal("1234567.89")),
            ("1.50000", Decimal("1.5000")),  # trailing zeros beyond the scale lose nothing
            (19.99, Decimal("19.99")),  # float from a spreadsheet keeps its displayed value
            (7, Decimal("7")),
            (Decimal("0.0001"), Decimal("0.0001")),
        ],
    )
    def test_accepts(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_is_unknown_not_zero(self, raw):
        assert parse_amount(raw) is None

    def test_zero_is_zero(self):
        assert parse_amount("0") == Decimal("0")

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("$5.00", "plain decimal"),
            ("(5.00)", "plain decimal"),
            ("1 000", "plain decimal"),
            ("12,34", "plain decimal"),  # comma decimal separator is ambiguous; not guessed
            ("1e5", "plain decimal"),
            ("abc", "plain decimal"),
            ("0.00001", "more than 4 decimal places"),
            (0.1 + 0.2, "more than 4 decimal places"),
            ("100000000000000", "too large"),
            (True, "boolean"),
            (float("nan"), "finite"),
        ],
    )
    def test_rejects(self, raw, message):
        with pytest.raises(ValueError, match=message):
            parse_amount(raw)


class TestParseTimestamp:
    def test_offset_and_z_are_respected(self):
        assert parse_timestamp("2026-03-31T23:30:00-04:00", NY) == datetime(2026, 4, 1, 3, 30, tzinfo=UTC)
        assert parse_timestamp("2026-03-31T23:30:00Z", NY) == datetime(2026, 3, 31, 23, 30, tzinfo=UTC)

    def test_naive_values_use_the_mapping_zone(self):
        assert parse_timestamp("2026-01-15 10:00", NY) == datetime(2026, 1, 15, 15, 0, tzinfo=UTC)  # EST
        assert parse_timestamp("2026-07-15 10:00", NY) == datetime(2026, 7, 15, 14, 0, tzinfo=UTC)  # EDT

    def test_date_only_is_local_midnight(self):
        assert parse_timestamp("2026-04-01", NY) == datetime(2026, 4, 1, 4, 0, tzinfo=UTC)
        assert parse_timestamp(date(2026, 4, 1), ZoneInfo("UTC")) == datetime(2026, 4, 1, tzinfo=UTC)

    def test_spreadsheet_datetime(self):
        assert parse_timestamp(datetime(2026, 2, 1, 12, 0), ZoneInfo("Europe/London")) == datetime(
            2026, 2, 1, 12, 0, tzinfo=UTC
        )

    def test_dst_gap_is_rejected(self):
        with pytest.raises(ValueError, match="does not exist"):
            parse_timestamp("2026-03-08 02:30", NY)

    def test_dst_overlap_is_rejected(self):
        with pytest.raises(ValueError, match="ambiguous"):
            parse_timestamp("2026-11-01 01:30", NY)

    def test_overlap_with_explicit_offset_is_fine(self):
        assert parse_timestamp("2026-11-01T01:30:00-05:00", NY) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)

    def test_custom_format(self):
        assert parse_timestamp("31/03/2026", ZoneInfo("UTC"), "%d/%m/%Y") == datetime(2026, 3, 31, tzinfo=UTC)
        with pytest.raises(ValueError, match="format '%d/%m/%Y'"):
            parse_timestamp("2026-03-31", ZoneInfo("UTC"), "%d/%m/%Y")

    @pytest.mark.parametrize("raw", ["03/31/2026", "yesterday", "2026-13-01"])
    def test_rejects_non_iso(self, raw):
        with pytest.raises(ValueError, match="ISO 8601"):
            parse_timestamp(raw, NY)


def test_currency():
    assert parse_currency(" usd ") == "USD"
    assert parse_currency("") is None
    for bad in ("US", "US$", "1234"):
        with pytest.raises(ValueError):
            parse_currency(bad)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "2", None], "integer"),
        (["1.5", "2"], "decimal"),
        ([19.99, 3], "decimal"),
        (["2026-01-01", ""], "date"),
        (["2026-01-01T10:00:00Z"], "datetime"),
        ([datetime(2026, 1, 1)], "date"),
        ([datetime(2026, 1, 1, 9)], "datetime"),
        (["1", "x"], "text"),
        ([None, " "], "empty"),
        ([True, False], "boolean"),
    ],
)
def test_infer_type(values, expected):
    assert infer_type(values) == expected
