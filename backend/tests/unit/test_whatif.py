from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import pytest

from app.memory.similarity import Profile, rank
from app.simulation.whatif import (
    AdjustMapping,
    AlignDefinition,
    AlignWindow,
    RemoveDuplicates,
    SetRefundPolicy,
    SimulationError,
    simulate,
)
from tests.unit.test_autopsy import FAN_OUT, side
from tests.unit.test_metrics import rec, utc

D = Decimal


def data():
    left = [rec("A", "100", refund="20", at=utc(2026, 3, 5)), rec("B", "50", at=utc(2026, 3, 31, 23))]
    right = [
        rec("A", "100", refund="20", at=utc(2026, 3, 5), raw="a"),
        rec("A", "100", refund="20", at=utc(2026, 3, 5), raw="a"),
        rec("B", "50", at=utc(2026, 3, 31, 23), attrs={"method": "card"}),
        rec("B", "50", at=utc(2026, 3, 31, 23), attrs={"method": "gift"}),
    ]
    return left, right


def test_remove_exact_duplicates_resolves_exactly_their_value():
    left, right = data()
    out = simulate(RemoveDuplicates(rule_type="remove_duplicates", side="right"), side(left), side(right))
    assert out.baseline.delta == D(150) and out.simulated.delta == D(50)
    assert out.resolved_amount == D(100) and out.affected_rows == 1


def test_remove_join_copies_only_drops_rows_that_differ_elsewhere():
    left, right = data()
    out = simulate(
        RemoveDuplicates(rule_type="remove_duplicates", side="right", kind="join_copies"),
        side(left),
        side(right),
    )
    assert out.simulated.delta == D(100)  # B's second payment row removed; A's exact copy kept


def test_refund_policy_and_align_definition():
    left, right = data()
    lhs, rhs = side(left, refund_policy="net_of_refunds"), side(right[:1] + right[2:3])
    out = simulate(SetRefundPolicy(rule_type="refund_policy", side="left", policy="gross"), lhs, rhs)
    assert (out.baseline.delta, out.simulated.delta, out.resolved_amount) == (D(20), D(0), D(20))
    out = simulate(AlignDefinition(rule_type="align_definition", side="right"), lhs, rhs)
    assert out.simulated.delta == 0 and out.simulated.findings == []


def test_align_window_reports_signed_change():
    left, right = data()
    march_ny = {
        "window_start": datetime(2026, 3, 1),
        "window_end": datetime(2026, 4, 1),
        "timezone": "America/New_York",
    }
    march_utc = {"window_start": datetime(2026, 3, 1), "window_end": datetime(2026, 4, 1)}
    lhs, rhs = side(left, **march_ny), side(left, **march_utc)  # B (23:00 UTC 31 Mar) is March in both
    out = simulate(AlignWindow(rule_type="align_window", side="right"), lhs, rhs)
    assert out.simulated.delta == out.baseline.delta == 0
    late = [rec("C", "7", at=utc(2026, 4, 1, 2))]
    out = simulate(
        AlignWindow(rule_type="align_window", side="right"), side(late, **march_ny), side(late, **march_utc)
    )
    assert (out.baseline.delta, out.simulated.delta, out.resolved_amount) == (D(-7), D(0), D(7))


def test_change_that_widens_the_gap_has_negative_resolved_amount():
    left, right = data()
    out = simulate(
        SetRefundPolicy(rule_type="refund_policy", side="right", policy="net_of_refunds"),
        side(left),
        side(left),
    )
    assert out.simulated.delta == D(-20) and out.resolved_amount == D(-20)


def test_inputs_are_never_modified():
    left, right = data()
    lhs, rhs = side(left, refund_policy="net_of_refunds"), side(right, provenance=FAN_OUT)
    before = deepcopy((lhs.records, lhs.spec, lhs.mapping, rhs.records, rhs.spec, rhs.mapping))
    for rule in [
        RemoveDuplicates(rule_type="remove_duplicates"),
        RemoveDuplicates(rule_type="remove_duplicates", kind="join_copies"),
        SetRefundPolicy(rule_type="refund_policy", side="left", policy="gross"),
        AlignDefinition(rule_type="align_definition", side="left"),
    ]:
        simulate(rule, lhs, rhs)
    assert (lhs.records, lhs.spec, lhs.mapping, rhs.records, rhs.spec, rhs.mapping) == before


def test_simulations_are_deterministic():
    left, right = data()
    rule = RemoveDuplicates(rule_type="remove_duplicates")
    a = simulate(rule, side(left), side(right))
    b = simulate(rule, side(left), side(right))
    assert [f.proof_hash for f in a.simulated.findings] == [f.proof_hash for f in b.simulated.findings]
    assert (a.simulated.delta, a.affected_rows) == (b.simulated.delta, b.affected_rows)


def test_rule_that_needs_an_unmapped_field_is_refused():
    left, _ = data()
    bare = {"amount": "a", "currency": "c", "business_key": ["k"]}
    with pytest.raises(SimulationError, match="refund_amount"):
        simulate(
            SetRefundPolicy(rule_type="refund_policy", side="left", policy="net_of_refunds"),
            side(left, mapping=bare),
            side(left),
        )


def test_adjust_mapping_needs_records():
    left, _ = data()
    with pytest.raises(SimulationError, match="re-normalized"):
        simulate(
            AdjustMapping(rule_type="adjust_mapping", side="left", mapping={"amount": "a"}),
            side(left),
            side(left),
        )


def test_similarity_scores_are_explained_and_ranked():
    a = Profile(
        "a",
        "A",
        confirmed_rules={"r1", "r2"},
        metrics={"m"},
        sources={"q"},
        dna_differences={"refund_policy"},
    )
    b = Profile("b", "B", confirmed_rules={"r1"}, suspected_rules={"r2"}, metrics={"m"})
    c = Profile("c", "C", confirmed_rules={"r1", "r2"}, sources={"q"}, dna_differences={"refund_policy"})
    d = Profile("d", "D", confirmed_rules={"zzz"})
    ranked = rank(a, [b, c, d, a])
    assert [(m.investigation_id, m.score) for m in ranked] == [("c", 3 + 3 + 2 + 1), ("b", 3 + 2)]
    assert ranked[1].reasons == [
        "both have a confirmed finding from rule r1 (+3)",
        "both use metric definition m (+2)",
    ]
