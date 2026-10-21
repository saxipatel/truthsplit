import random
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.comparison.metrics import MetricSpec
from app.diagnosis.autopsy import SideInput, run_autopsy
from app.models.investigations import EvidenceStatus, FindingType
from tests.unit.test_metrics import rec, utc

D = Decimal
FULL_MAP = {
    "amount": "a",
    "refund_amount": "r",
    "occurred_at": "t",
    "status": "s",
    "currency": "c",
    "business_key": ["k"],
    "attributes": ["region", "method"],
}
FAN_OUT = {
    "amplifying_joins": [{"left": "orders.id", "right": "payments.order_id", "cardinality": "1:N"}],
    "measure_table": "orders",
    "grain": "payment",
}


def side(records, mapping=FULL_MAP, provenance=None, **spec):
    return SideInput(
        records=records,
        spec=MetricSpec(aggregation=spec.pop("aggregation", "sum"), **spec),
        mapping=mapping,
        provenance=provenance or {},
    )


def autopsy(left, right):
    result = run_autopsy(left, right)
    assert (
        sum((f.amount_impact for f in result.findings if f.amount_impact is not None), D(0)) == result.delta
    )
    return result


def by_type(result):
    return {f.type: f for f in result.findings}


def test_equal_inputs_have_no_findings():
    rows = [rec("A", "10", at=utc(2026, 3, 2)), rec("B", "5", at=utc(2026, 3, 3))]
    r = autopsy(side(rows), side(rows))
    assert r.delta == 0 and r.findings == [] and r.replay == []


class TestRefundPolicy:
    rows = staticmethod(
        lambda: [
            rec("A", "100", refund="25"),
            rec("B", "40", refund="40"),
            rec("C", "60", refund="0"),
            rec("D", "10"),
        ]
    )

    def test_net_vs_gross_attributes_exact_signed_refunds(self):
        data = self.rows()
        r = autopsy(side(data, refund_policy="net_of_refunds"), side(data, refund_policy="gross"))
        (f,) = r.findings
        assert f.type is FindingType.REFUND_POLICY and f.evidence_status is EvidenceStatus.CONFIRMED
        assert f.amount_impact == D(65) == r.delta  # gross is higher by the refunds
        assert sorted(e.business_key for e in f.evidence) == ["A", "B"]  # zero and missing refunds unaffected
        assert {e.business_key: e.contribution for e in f.evidence} == {"A": D(25), "B": D(40)}

    def test_reverse_direction_has_opposite_sign(self):
        data = self.rows()
        r = autopsy(side(data, refund_policy="gross"), side(data, refund_policy="net_of_refunds"))
        assert by_type(r)[FindingType.REFUND_POLICY].amount_impact == D(-65)

    def test_exclude_refunded_counts_full_and_partial(self):
        data = self.rows()
        r = autopsy(side(data, refund_policy="gross"), side(data, refund_policy="exclude_refunded"))
        assert by_type(r)[FindingType.REFUND_POLICY].amount_impact == D(-140)

    def test_refunds_baked_into_source_amounts_are_found_by_identity(self):
        left = [rec("A", "75", refund="25"), rec("B", "0", refund="40"), rec("C", "60")]
        right = [rec("A", "100"), rec("B", "40"), rec("C", "61")]
        r = autopsy(side(left), side(right))
        f = by_type(r)
        assert f[FindingType.REFUND_POLICY].rule_id == "data.refund_identity"
        assert f[FindingType.REFUND_POLICY].amount_impact == D(65)
        assert f[FindingType.AMOUNT_MISMATCH].amount_impact == D(1)  # C is not a refund


class TestJoinAmplification:
    def orders(self):
        return [rec("O1", "100", attrs={"method": None}), rec("O2", "50", attrs={"method": None})]

    def payments(self, fan_out=3):
        return [rec("O1", "100", attrs={"method": f"m{i}"}) for i in range(fan_out)] + [
            rec("O2", "50", attrs={"method": "card"})
        ]

    def test_confirmed_with_join_metadata(self):
        r = autopsy(side(self.orders()), side(self.payments(), provenance=FAN_OUT))
        (f,) = r.findings
        assert f.type is FindingType.JOIN_AMPLIFICATION and f.evidence_status is EvidenceStatus.CONFIRMED
        assert f.amount_impact == D(200) == r.delta
        assert f.calculation["keys_where_other_side_equals_one_copy"] == 1
        assert r.comparison.right_duplicate_keys == 0

    def test_suspected_without_metadata_and_not_attributed(self):
        r = autopsy(side(self.orders()), side(self.payments()))
        f = by_type(r)
        assert f[FindingType.JOIN_AMPLIFICATION].evidence_status is EvidenceStatus.SUSPECTED
        assert f[FindingType.JOIN_AMPLIFICATION].amount_impact is None
        assert f[FindingType.AMOUNT_MISMATCH].amount_impact == D(200)  # stays a record-level difference

    def test_legitimate_child_rows_are_not_amplification(self):
        lines = [
            rec("O1", "60", attrs={"method": "x"}),
            rec("O1", "40", attrs={"method": "y"}),
            rec("O2", "50"),
        ]
        r = autopsy(side(self.orders()), side(lines, provenance=FAN_OUT))
        assert FindingType.JOIN_AMPLIFICATION not in by_type(r) and r.delta == 0

    def test_business_key_grain_already_deduplicates(self):
        r = autopsy(side(self.orders()), side(self.payments(), provenance=FAN_OUT, grain="business_key"))
        assert FindingType.JOIN_AMPLIFICATION not in by_type(r)

    def test_left_side_amplification_has_negative_sign(self):
        r = autopsy(side(self.payments(), provenance=FAN_OUT), side(self.orders()))
        assert by_type(r)[FindingType.JOIN_AMPLIFICATION].amount_impact == D(-200)


class TestDateCutoff:
    MARCH = {"window_start": datetime(2026, 3, 1), "window_end": datetime(2026, 4, 1)}

    def test_timezone_difference_at_month_end(self):
        rows = [
            rec("A", "10", at=utc(2026, 3, 31, 23, 30)),
            rec("B", "20", at=utc(2026, 4, 1, 2, 0)),
            rec("C", "5", at=utc(2026, 3, 15)),
        ]
        r = autopsy(side(rows, **self.MARCH, timezone="America/New_York"), side(rows, **self.MARCH))
        (f,) = r.findings
        assert f.type is FindingType.DATE_CUTOFF and f.amount_impact == D(-20)
        assert [e.business_key for e in f.evidence] == ["B"]
        assert f.evidence[0].detail["excluded_after"] == "outside_window"

    def test_inclusive_versus_exclusive_end(self):
        rows = [rec("A", "10", at=utc(2026, 4, 1)), rec("B", "1", at=utc(2026, 3, 9))]
        r = autopsy(side(rows, **self.MARCH, window_end_inclusive=True), side(rows, **self.MARCH))
        assert by_type(r)[FindingType.DATE_CUTOFF].amount_impact == D(-10)

    def test_dst_transition_day(self):
        # New York's March 8 2026 is 23 hours long; a UTC day window is 24 hours.
        day = {"window_start": datetime(2026, 3, 8), "window_end": datetime(2026, 3, 9)}
        rows = [rec(str(h), "1", at=utc(2026, 3, 8, 0) + timedelta(hours=h)) for h in range(30)]
        r = autopsy(side(rows, **day, timezone="America/New_York"), side(rows, **day))
        assert r.left_value == 23 and r.right_value == 24
        f = by_type(r)[FindingType.DATE_CUTOFF]
        # NY day = 05:00..04:00(+1) UTC. Switching to the UTC day adds hours 0-4 and drops hours 24-27.
        entered = sorted(int(e.business_key) for e in f.evidence if e.contribution > 0)
        left_ = sorted(int(e.business_key) for e in f.evidence if e.contribution < 0)
        assert (entered, left_, f.amount_impact) == ([0, 1, 2, 3, 4], [24, 25, 26, 27], D(1))

    def test_same_instants_written_in_different_zones_are_identical(self):
        rows = [rec("A", "1", at=utc(2026, 3, 1, 5))]
        ny = {"window_start": datetime(2026, 3, 1), "timezone": "America/New_York"}
        r = autopsy(side(rows, **ny), side(rows, window_start=datetime(2026, 3, 1, 5)))
        assert r.findings == [] and r.replay[0].status == "identical"

    def test_query_window_explains_one_sided_records(self):
        window = {
            "window": {
                "start": "2026-03-01T05:00:00+00:00",
                "end": "2026-04-01T04:00:00+00:00",
                "timezone": "America/New_York",
            }
        }
        left = [rec("A", "10", at=utc(2026, 3, 1, 1)), rec("B", "7", at=utc(2026, 3, 10))]
        right = [rec("B", "7", at=utc(2026, 3, 10))]
        r = autopsy(side(left), side(right, provenance=window))
        (f,) = r.findings
        assert (
            f.type is FindingType.DATE_CUTOFF
            and f.rule_id == "data.query_window"
            and f.amount_impact == D(-10)
        )

    def test_near_boundary_without_window_is_only_suspected(self):
        left = [rec("A", "10", at=utc(2026, 4, 1, 2)), rec("B", "7", at=utc(2026, 3, 31, 20))]
        right = [rec("B", "7", at=utc(2026, 3, 31, 20))]
        f = by_type(autopsy(side(left), side(right)))
        assert (
            f[FindingType.DATE_CUTOFF].evidence_status is EvidenceStatus.SUSPECTED
            and f[FindingType.DATE_CUTOFF].amount_impact is None
        )
        assert f[FindingType.MISSING_RECORDS].amount_impact == D(-10)


def test_filter_difference():
    rows = [rec("A", "10", status="paid"), rec("B", "4", status="void")]
    r = autopsy(side(rows), side(rows, filters=[{"field": "status", "op": "ne", "value": "void"}]))
    (f,) = r.findings
    assert f.type is FindingType.FILTER_DIFFERENCE and f.amount_impact == D(-4)


def test_unreplayable_rule_is_suspected_and_left_to_record_level():
    left_map = {"amount": "a", "currency": "c", "business_key": ["k"]}
    left = [rec("A", "10", status=None), rec("B", "4", status=None)]
    right = [rec("A", "10", status="paid")]
    r = autopsy(
        side(left, mapping=left_map), side(right, filters=[{"field": "status", "op": "eq", "value": "paid"}])
    )
    f = by_type(r)
    assert f[FindingType.FILTER_DIFFERENCE].evidence_status is EvidenceStatus.SUSPECTED
    assert r.replay[0].status == "not_replayable"
    assert f[FindingType.MISSING_RECORDS].amount_impact == D(-4)


def test_overlapping_causes_reconcile_in_order_and_disclose_overlap():
    # One order is both refunded and repeated by a payments join; another record sits outside the window.
    left = [
        rec("O1", "100", refund="30", at=utc(2026, 3, 5)),
        rec("O2", "50", at=utc(2026, 3, 6)),
        rec("O3", "80", refund="80", at=utc(2026, 4, 1, 2)),
    ]
    right = [
        rec("O1", "100", refund="30", at=utc(2026, 3, 5), attrs={"method": "card"}),
        rec("O1", "100", refund="30", at=utc(2026, 3, 5), attrs={"method": "gift"}),
        rec("O2", "50", at=utc(2026, 3, 6), attrs={"method": "card"}),
    ]
    march = {"window_start": datetime(2026, 3, 1), "window_end": datetime(2026, 4, 1)}
    r = autopsy(
        side(left, refund_policy="net_of_refunds", timezone="America/New_York", **march),
        side(right, provenance=FAN_OUT, refund_policy="gross", **march),
    )
    f = by_type(r)
    assert r.delta == D(250) - D(120)
    assert f[FindingType.DATE_CUTOFF].amount_impact == D(0)  # O3 leaves, but it was fully refunded (net 0)
    assert f[FindingType.REFUND_POLICY].amount_impact == D(30)
    assert f[FindingType.JOIN_AMPLIFICATION].amount_impact == D(100)
    assert "Applied alone" in f[FindingType.REFUND_POLICY].overlap_note  # alone it would also add O3's 80
    assert [x.step for x in r.replay] == ["date_window", "refund_policy"]


@pytest.mark.parametrize("seed", range(25))
def test_random_scenarios_always_reconcile(seed):
    rnd = random.Random(seed)

    def rows(n):
        out = []
        for _ in range(n):
            key = (
                rnd.choice([f"K{rnd.randint(1, n // 2)}", None])
                if rnd.random() < 0.1
                else f"K{rnd.randint(1, n // 2)}"
            )
            out.append(
                rec(
                    key,
                    str(D(rnd.randint(-1000, 100000)) / 100),
                    refund=rnd.choice([None, "0", "5.00"]),
                    at=utc(2026, 3, 1) + timedelta(hours=rnd.randint(-40, 800)),
                    status=rnd.choice(["paid", "void", None]),
                    attrs={"region": rnd.choice(["EU", "US", None]), "method": None},
                )
            )
        return out

    def spec():
        s = {
            "refund_policy": rnd.choice(["gross", "net_of_refunds", "exclude_refunded"]),
            "grain": rnd.choice(["record", "business_key"]),
        }
        if rnd.random() < 0.6:
            s |= {
                "window_start": datetime(2026, 3, rnd.randint(1, 5)),
                "window_end": datetime(2026, 3, rnd.randint(20, 31)),
                "timezone": rnd.choice(["UTC", "America/New_York", "Asia/Kolkata"]),
                "window_end_inclusive": rnd.random() < 0.5,
            }
        if rnd.random() < 0.5:
            s["filters"] = [{"field": "status", "op": rnd.choice(["eq", "ne"]), "value": "void"}]
        return s

    left_rows, right_rows = rows(rnd.randint(0, 120)), rows(rnd.randint(0, 120))
    provenance = FAN_OUT if rnd.random() < 0.5 else {}
    autopsy(side(left_rows, **spec()), side(right_rows, provenance=provenance, **spec()))


class TestRefundIdentityBoundaries:
    def test_difference_that_is_not_exactly_the_refund_stays_a_mismatch(self):
        left = [rec("A", "100")]
        right = [rec("A", "76", refund="25")]  # net would be 75; the extra 1.00 breaks the identity
        f = by_type(autopsy(side(left), side(right)))
        assert FindingType.REFUND_POLICY not in f and f[FindingType.AMOUNT_MISMATCH].amount_impact == D(-24)

    def test_identity_runs_after_join_copies_are_removed(self):
        left = [rec("A", "100", attrs={"method": "card"}), rec("A", "100", attrs={"method": "gift"})]
        right = [rec("A", "70", refund="30")]
        r = autopsy(side(left, provenance=FAN_OUT), side(right))
        f = by_type(r)
        assert f[FindingType.JOIN_AMPLIFICATION].amount_impact == D(-100)
        assert f[FindingType.REFUND_POLICY].amount_impact == D(-30)
        assert FindingType.AMOUNT_MISMATCH not in f and r.delta == D(-130)

    def test_exact_duplicate_and_netted_refund_on_one_key_split_cleanly(self):
        left = [rec("A", "100", raw="a"), rec("A", "100", raw="a")]
        right = [rec("A", "80", refund="20")]
        f = by_type(autopsy(side(left), side(right)))
        assert (
            f[FindingType.DUPLICATE_RECORDS].amount_impact,
            f[FindingType.REFUND_POLICY].amount_impact,
        ) == (D(-100), D(-20))

    def test_double_deduction_is_two_mechanisms_not_double_counting(self):
        # The left source already nets the refund AND its metric subtracts it again.
        left = [rec("A", "75", refund="25")]
        right = [rec("A", "100")]
        r = autopsy(side(left, refund_policy="net_of_refunds"), side(right))
        f = {x.rule_id: x.amount_impact for x in r.findings}
        assert f == {"definition.refund_policy": D(25), "data.refund_identity": D(25)}
        assert r.delta == D(50)  # 100 - (75 - 25): the refund really is deducted twice on the left

    def test_refund_on_either_side_and_either_sign(self):
        assert by_type(autopsy(side([rec("A", "75", refund="25")]), side([rec("A", "100")])))[
            FindingType.REFUND_POLICY
        ].amount_impact == D(25)
        assert by_type(autopsy(side([rec("A", "100")]), side([rec("A", "75", refund="25")])))[
            FindingType.REFUND_POLICY
        ].amount_impact == D(-25)

    def test_zero_refund_never_matches(self):
        f = by_type(autopsy(side([rec("A", "10", refund="0")]), side([rec("A", "10.00")])))
        assert f == {}
