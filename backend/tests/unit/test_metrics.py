from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.comparison.metrics import MetricEvaluationError, MetricSpec, Record, evaluate, unmapped_fields

D = Decimal
_ids = iter(range(1, 10_000))


def rec(
    key="K",
    amount="10",
    *,
    row=None,
    at=None,
    refund=None,
    currency="USD",
    status="paid",
    attrs=None,
    raw=None,
):
    n = next(_ids)
    return Record(
        id=n,
        row_number=row or n,
        source_record_id=f"r{n}",
        business_key=key,
        occurred_at=at,
        amount=None if amount is None else D(amount),
        currency=currency,
        status=status,
        refund_amount=None if refund is None else D(refund),
        attributes=attrs or {},
        raw_payload_hash=raw or f"h{n}",
    )


def spec(**kw):
    return MetricSpec(aggregation=kw.pop("aggregation", "sum"), **kw)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_sum_count_count_distinct():
    rows = [rec("A", "10.10"), rec("A", "5"), rec("B", "0.0001")]
    assert evaluate(rows, spec()).value == D("15.1001")
    assert evaluate(rows, spec(aggregation="count")).value == 3
    assert evaluate(rows, spec(aggregation="count_distinct")).value == 2


def test_metric_equals_sum_of_contributions():
    rows = [rec("A", "1.1"), rec("B", "2.2"), rec(None, "3.3")]
    e = evaluate(rows, spec())
    assert e.value == sum(c.value for c in e.contributions) == D("6.6")


class TestWindow:
    def test_end_exclusive_by_default_and_inclusive_when_asked(self):
        rows = [rec(at=utc(2026, 3, 31, 23, 59, 59)), rec(at=utc(2026, 4, 1))]
        march = dict(window_start=datetime(2026, 3, 1), window_end=datetime(2026, 4, 1))
        assert evaluate(rows, spec(**march)).value == D(10)
        assert evaluate(rows, spec(**march, window_end_inclusive=True)).value == D(20)

    def test_bounds_are_local_time_in_the_metric_zone(self):
        # 2026-04-01 02:00 UTC is still March 31 in New York (EDT, UTC-4).
        row = rec(at=utc(2026, 4, 1, 2, 0))
        march = dict(window_start=datetime(2026, 3, 1), window_end=datetime(2026, 4, 1))
        assert evaluate([row], spec(**march)).value == 0
        assert evaluate([row], spec(**march, timezone="America/New_York")).value == D(10)

    def test_window_across_dst_change(self):
        # March 8 2026 has 23 hours in New York; a day window must still cover 23 real hours only.
        day = dict(
            window_start=datetime(2026, 3, 8), window_end=datetime(2026, 3, 9), timezone="America/New_York"
        )
        inside = rec(at=utc(2026, 3, 9, 3, 59))  # 23:59 EDT on the 8th
        outside = rec(at=utc(2026, 3, 9, 4, 0))  # 00:00 EDT on the 9th
        e = evaluate([inside, outside], spec(**day))
        assert [c.record for c in e.contributions] == [inside]
        assert e.excluded["outside_window"] == [outside]

    def test_open_ended_windows(self):
        rows = [rec(at=utc(2026, 1, 1)), rec(at=utc(2026, 6, 1))]
        assert evaluate(rows, spec(window_start=datetime(2026, 3, 1))).value == D(10)
        assert evaluate(rows, spec(window_end=datetime(2026, 3, 1))).value == D(10)

    def test_missing_timestamp_is_excluded_with_reason(self):
        e = evaluate([rec(at=None)], spec(window_start=datetime(2026, 1, 1)))
        assert e.value == 0 and e.excluded_counts == {"no_timestamp": 1}

    def test_window_bound_in_dst_gap_is_an_error(self):
        with pytest.raises(MetricEvaluationError, match="does not exist"):
            evaluate([rec()], spec(window_start=datetime(2026, 3, 8, 2, 30), timezone="America/New_York"))

    def test_bounds_must_be_wall_clock(self):
        with pytest.raises(ValidationError, match="wall-clock"):
            spec(window_start=utc(2026, 1, 1))


class TestFilters:
    def test_text_and_attribute_filters(self):
        rows = [
            rec(status="paid", attrs={"region": "EU"}),
            rec(status="void", attrs={"region": "EU"}),
            rec(status="paid", attrs={"region": "US"}),
        ]
        s = spec(
            filters=[
                {"field": "status", "op": "ne", "value": "void"},
                {"field": "attributes.region", "op": "in", "value": ["EU"]},
            ]
        )
        assert evaluate(rows, s).value == D(10)

    def test_null_fields_never_match_comparisons(self):
        rows = [rec(status=None), rec(status="paid")]
        assert evaluate(rows, spec(filters=[{"field": "status", "op": "ne", "value": "void"}])).value == D(10)
        assert evaluate(
            rows, spec(filters=[{"field": "status", "op": "not_in", "value": ["void"]}])
        ).value == D(10)
        assert evaluate(rows, spec(filters=[{"field": "status", "op": "is_null"}])).value == D(10)

    def test_numeric_filters_use_decimal(self):
        rows = [rec(amount="0.1"), rec(amount="0.2"), rec(amount="0.3")]
        assert evaluate(rows, spec(filters=[{"field": "amount", "op": "gte", "value": "0.2"}])).value == D(
            "0.5"
        )

    @pytest.mark.parametrize(
        "bad",
        [
            {"field": "nope", "op": "eq", "value": "x"},
            {"field": "status", "op": "gt", "value": "a"},
            {"field": "status", "op": "in", "value": []},
            {"field": "status", "op": "is_null", "value": "x"},
            {"field": "amount", "op": "eq", "value": "ten"},
            {"field": "status", "op": "eq", "value": None},
        ],
    )
    def test_invalid_filters(self, bad):
        with pytest.raises(ValidationError):
            spec(filters=[bad])


class TestRefunds:
    rows = staticmethod(
        lambda: [rec("A", "100", refund="30"), rec("B", "50", refund="0"), rec("C", "20", refund=None)]
    )

    def test_gross_ignores_refunds(self):
        assert evaluate(self.rows(), spec(refund_policy="gross")).value == D(170)

    def test_net_subtracts_partial_refunds_and_treats_missing_as_none(self):
        assert evaluate(self.rows(), spec(refund_policy="net_of_refunds")).value == D(140)

    def test_exclude_refunded_drops_only_positive_refunds(self):
        e = evaluate(self.rows(), spec(refund_policy="exclude_refunded"))
        assert e.value == D(70) and e.excluded_counts == {"refunded": 1}

    def test_net_only_for_sums(self):
        with pytest.raises(ValidationError, match="net_of_refunds"):
            spec(aggregation="count", refund_policy="net_of_refunds")


def test_business_key_grain_keeps_first_row_and_drops_null_keys():
    first, second, nokey = rec("A", "10", row=2), rec("A", "10", row=3), rec(None, "5", row=4)
    e = evaluate([second, nokey, first], spec(grain="business_key"))
    assert [c.record for c in e.contributions] == [first]
    assert e.excluded == {"duplicate_grain": [second], "null_key": [nokey]}


class TestNullPolicy:
    def test_exclude_counts_null_values(self):
        e = evaluate([rec(amount=None), rec(amount="0")], spec())
        assert e.value == 0 and len(e.contributions) == 1 and e.excluded_counts == {"null_value": 1}

    def test_reject_fails(self):
        with pytest.raises(MetricEvaluationError, match="null policy is 'reject'"):
            evaluate([rec(amount=None, row=7)], spec(null_policy="reject"))

    def test_counts_ignore_value_nulls(self):
        assert evaluate([rec(amount=None)], spec(aggregation="count", null_policy="reject")).value == 1


class TestCurrency:
    def test_mixed_currencies_are_refused(self):
        with pytest.raises(MetricEvaluationError, match="mix currencies"):
            evaluate([rec(currency="USD"), rec(currency="EUR")], spec())

    def test_declared_currency_must_match(self):
        with pytest.raises(MetricEvaluationError, match="defined in EUR"):
            evaluate([rec(currency="USD")], spec(currency="EUR"))

    def test_missing_currency_is_refused_for_sums(self):
        with pytest.raises(MetricEvaluationError, match="no currency"):
            evaluate([rec(currency=None)], spec())

    def test_filtered_out_currency_is_fine(self):
        rows = [rec(currency="USD"), rec(currency="EUR")]
        e = evaluate(rows, spec(filters=[{"field": "currency", "op": "eq", "value": "USD"}]))
        assert e.currency == "USD"


class TestUnmappedFields:
    FULL = {
        "amount": "a",
        "refund_amount": "r",
        "occurred_at": "t",
        "status": "s",
        "currency": "c",
        "business_key": ["k"],
        "attributes": ["region"],
    }

    def test_fully_mapped_dataset_supports_everything(self):
        s = spec(
            refund_policy="net_of_refunds",
            grain="business_key",
            window_start=datetime(2026, 1, 1),
            filters=[
                {"field": "attributes.region", "op": "eq", "value": "EU"},
                {"field": "status", "op": "is_null"},
            ],
        )
        assert unmapped_fields(s, self.FULL) == []

    @pytest.mark.parametrize(
        ("kw", "missing"),
        [
            ({"refund_policy": "net_of_refunds"}, ["refund_amount"]),
            ({"refund_policy": "exclude_refunded"}, ["refund_amount"]),
            ({"window_end": datetime(2026, 1, 1)}, ["occurred_at"]),
            ({"grain": "business_key"}, ["business_key"]),
            ({"aggregation": "count_distinct"}, ["business_key"]),
            ({"filters": [{"field": "attributes.region", "op": "eq", "value": "EU"}]}, ["attributes.region"]),
            ({"filters": [{"field": "status", "op": "eq", "value": "paid"}]}, ["status"]),
        ],
    )
    def test_reports_what_the_dataset_lacks(self, kw, missing):
        assert unmapped_fields(spec(**kw), {"amount": "a", "default_currency": "USD"}) == missing

    def test_sum_needs_its_value_column_but_count_does_not(self):
        assert unmapped_fields(spec(), {"business_key": ["k"]}) == ["amount"]
        assert unmapped_fields(spec(aggregation="count"), {"business_key": ["k"]}) == []
