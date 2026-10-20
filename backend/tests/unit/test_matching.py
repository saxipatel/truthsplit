from decimal import Decimal

import pytest

from app.comparison.findings import build_findings
from app.comparison.matching import ComparisonError, reconcile
from app.comparison.metrics import evaluate
from app.models.investigations import EvidenceStatus, FindingType
from tests.unit.test_metrics import rec, spec

D = Decimal


def run(left_rows, right_rows, left_spec=None, right_spec=None):
    left = evaluate(left_rows, left_spec or spec())
    right = evaluate(right_rows, right_spec or spec())
    r = reconcile(left, right)
    findings = build_findings(
        left,
        right,
        r,
        {x.business_key for x in left_rows if x.business_key},
        {x.business_key for x in right_rows if x.business_key},
    )
    return r, findings


def by_type(findings):
    return {f.type: f for f in findings}


def test_equal_inputs_give_zero_delta_and_no_findings():
    rows = [rec("A", "10"), rec("B", "20")]
    r, findings = run(rows, rows)
    assert r.delta == 0 and findings == []
    assert r.matched_keys == 2


def test_each_category_and_exact_partition():
    left = [rec("A", "100"), rec("B", "50"), rec("C", "10"), rec("D", "7", raw="d")]
    right = [
        rec("A", "100"),
        rec("B", "55.25"),
        rec("E", "30"),
        rec("D", "7", raw="d"),
        rec("D", "7", raw="d"),
        rec(None, "1"),
    ]
    r, findings = run(left, right)
    assert r.delta == D("33.25")
    assert r.totals() == {
        "extra_records": D(30),  # E
        "missing_records": D(-10),  # C
        "duplicate_records": D(7),  # second copy of D
        "amount_mismatch": D("5.25"),  # B
        "no_key": D(1),
    }
    assert sum(r.totals().values()) == r.delta
    f = by_type(findings)
    assert f[FindingType.EXTRA_RECORDS].evidence[0].business_key == "E"
    assert f[FindingType.MISSING_RECORDS].amount_impact == D(-10)
    assert f[FindingType.UNEXPLAINED].evidence_status is EvidenceStatus.UNEXPLAINED
    assert f[FindingType.KEY_QUALITY].amount_impact is None
    assert [x.step_order for x in findings if x.amount_impact is not None] == [1, 2, 3, 4, 5]


def test_duplicate_key_with_different_content_is_a_mismatch_not_a_duplicate():
    # Two order lines under one key are not copies; the per-key total differs.
    r, findings = run([rec("A", "10")], [rec("A", "10", raw="x"), rec("A", "4", raw="y")])
    assert r.totals()["duplicate_records"] == 0
    assert r.totals()["amount_mismatch"] == D(4)
    assert by_type(findings)[FindingType.KEY_QUALITY].calculation == {"left": 0, "right": 1}


def test_duplicates_and_mismatch_on_the_same_key_split_without_double_counting():
    left = [rec("A", "10", raw="a")]
    right = [rec("A", "12", raw="b"), rec("A", "12", raw="b")]
    r, _ = run(left, right)
    assert r.totals()["duplicate_records"] == D(12)
    assert r.totals()["amount_mismatch"] == D(2)
    assert r.delta == D(14)


def test_missing_key_reports_whether_definition_excluded_it():
    left = [rec("A", "10"), rec("B", "5")]
    right = [rec("A", "10", status="void")]  # B absent from data; A present but filtered out
    r, findings = run(
        left, right, right_spec=spec(filters=[{"field": "status", "op": "eq", "value": "paid"}])
    )
    missing = by_type(findings)[FindingType.MISSING_RECORDS]
    assert {e.business_key: e.detail["other_side"] for e in missing.evidence} == {
        "A": "excluded:filtered",
        "B": "absent",
    }
    assert "1 of these 2 keys" in missing.overlap_note


def test_counts_reconcile_too():
    r, findings = run(
        [rec("A"), rec("B")],
        [rec("A"), rec("A"), rec("C")],
        spec(aggregation="count"),
        spec(aggregation="count"),
    )
    assert r.delta == 1 and sum(r.totals().values()) == 1


def test_incomparable_metrics():
    with pytest.raises(ComparisonError, match="cannot compare a sum metric with a count"):
        run([rec()], [rec()], spec(), spec(aggregation="count"))
    with pytest.raises(ComparisonError, match="different currencies"):
        run([rec(currency="USD")], [rec(currency="EUR")])


def test_reconciliation_is_deterministic():
    left = [rec(k, str(i)) for i, k in enumerate("QWERTY")]
    right = [rec(k, str(i * 2)) for i, k in enumerate("YTREWQ")]
    a = run(left, right)[1]
    b = run(list(reversed(left)), list(reversed(right)))[1]
    assert [f.proof_hash for f in a] == [f.proof_hash for f in b]
