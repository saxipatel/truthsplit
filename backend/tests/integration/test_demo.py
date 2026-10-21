"""Demo scenarios checked three ways: the manifest (pure-Python model), SQL written for these tests (not engine
code), and the investigation engine. The demo source database must be seeded (compose does this)."""

import json
import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select, text

from app.core.config import Settings
from app.db.investigations import InvestigationSide, run_investigation
from app.models import EvidenceRecord, Finding, Investigation
from app.seed.demo import LEDGER_FILE, finance_orders, ledger_csv, manifest, net
from app.seed.load import load_demo
from app.seed.shop import build_orders, local_month_bounds

SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not SOURCE_URL, reason="TRUTHSPLIT_TEST_SOURCE_URL is not set"),
]
SAMPLE_DATA = Path(__file__).resolve().parents[3] / "sample_data"
D = Decimal


@pytest.fixture(scope="module")
def oracle():
    return manifest()


@pytest.fixture
def loaded(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", SOURCE_URL)
    settings = Settings(
        database_url="unused",
        storage_dir=tmp_path,
        allowed_source_hosts="localhost,127.0.0.1,host.docker.internal",
    )
    ids = load_demo(db_session, settings)
    return {k: db_session.get(Investigation, v["investigation_id"]) for k, v in ids.items()}


def source_sql(sql: str):
    engine = create_engine(SOURCE_URL)
    with engine.connect() as conn:
        row = conn.execute(text(sql)).one()
    engine.dispose()
    return row


def scenario(oracle, sid):
    return next(s for s in oracle["scenarios"] if s["id"] == sid)


def findings_by_rule(inv):
    out = defaultdict(list)
    for f in inv.findings:
        out[(f.type.value, f.rule_id)].append(f)
    return out


def evidence(db_session, finding):
    return db_session.scalars(select(EvidenceRecord).where(EvidenceRecord.finding_id == finding.id)).all()


# ---------------------------------------------------------------------------- the oracle itself


def test_python_model_matches_the_seeded_source():
    orders = build_orders()
    count, total, refunds, payments = source_sql(
        "SELECT (SELECT count(*) FROM orders), (SELECT sum(total_amount) FROM orders),"
        " (SELECT sum(amount) FROM refunds), (SELECT count(*) FROM payments)"
    )
    assert count == len(orders)
    assert total == sum(o.total_amount for o in orders)
    assert refunds == sum(o.refund for o in orders if o.refund)
    assert payments == sum(len(o.payments) for o in orders)


def test_committed_sample_files_are_reproducible(oracle):
    assert (SAMPLE_DATA / LEDGER_FILE).read_bytes() == ledger_csv(build_orders())
    assert json.loads((SAMPLE_DATA / "manifest.json").read_text()) == json.loads(json.dumps(oracle))


# ---------------------------------------------------------------------------- scenario A


def test_scenario_a_matches_independent_sql(oracle):
    exp = scenario(oracle, "A")["expected"]
    ny = "timestamp '{d}' AT TIME ZONE 'America/New_York'"
    fin = f"o.status <> 'cancelled' AND o.ordered_at >= {ny.format(d='2026-03-01')} AND o.ordered_at < {ny.format(d='2026-04-01')}"
    utc = "o.ordered_at >= '2026-03-01T00:00:00Z' AND o.ordered_at < '2026-04-01T00:00:00Z'"
    row = source_sql(
        f"""
        WITH f AS (SELECT o.id, o.total_amount t, COALESCE((SELECT sum(amount) FROM refunds r WHERE r.order_id = o.id), 0) refunded
                   FROM orders o WHERE {fin}),
             s AS (SELECT o.id, o.total_amount t, count(*) n FROM orders o JOIN payments p ON p.order_id = o.id WHERE {utc} GROUP BY o.id)
        SELECT (SELECT sum(t - refunded) FROM f), (SELECT sum(t * n) FROM s), (SELECT sum(refunded) FROM f),
               (SELECT sum(t * (n - 1)) FROM s), (SELECT -sum(t) FROM f WHERE id NOT IN (SELECT id FROM s)),
               (SELECT sum(t) FROM s WHERE id NOT IN (SELECT id FROM f))
        """
    )
    issues = {i["id"]: D(i["expected_contribution"]) for i in scenario(oracle, "A")["issues"]}
    assert tuple(row) == (
        D(exp["left_total"]),
        D(exp["right_total"]),
        issues["A1"],
        issues["A2"],
        issues["A3"],
        issues["A4"],
    )


def test_scenario_a_engine_matches_manifest(loaded, oracle, db_session):
    inv, sc = loaded["A"], scenario(oracle, "A")
    assert (inv.baseline_left, inv.baseline_right, inv.baseline_delta) == tuple(
        D(sc["expected"][k]) for k in ("left_total", "right_total", "delta")
    )
    assert inv.unexplained_delta == 0 and inv.explained_delta == inv.baseline_delta
    found = findings_by_rule(inv)
    attributed = [f for f in inv.findings if f.amount_impact is not None]
    assert len(attributed) == 4
    for issue in sc["issues"]:
        candidates = found[(issue["category"], issue["rule_id"])]
        match = [f for f in candidates if f.amount_impact == D(issue["expected_contribution"])]
        assert len(match) == 1, issue["id"]
        f = match[0]
        assert f.evidence_status.value == issue["expected_status"]
        keys = {e.business_key for e in evidence(db_session, f)}
        assert keys == set(issue["affected"]), issue["id"]


def test_scenario_a_overlaps_are_not_double_counted(loaded, oracle, db_session):
    """Per order: the evidence contributions of all attributed findings must add up to that order's actual
    difference (sales gross x payment rows minus finance net), so no part of an order is counted twice."""
    inv = loaded["A"]
    per_key = defaultdict(Decimal)
    for f in inv.findings:
        if f.amount_impact is None:
            continue
        for e in evidence(db_session, f):
            per_key[e.business_key] += e.contribution
    orders = {o.order_number: o for o in build_orders()}
    exp_keys = {o for i in scenario(oracle, "A")["issues"] for o in i["affected"]}
    assert set(per_key) == exp_keys

    fin = {o.order_number for o in finance_orders(list(orders.values()))}
    lo, hi = local_month_bounds(2026, 3, ZoneInfo("UTC"))
    for key, attributed in per_key.items():
        o = orders[key]
        left = net(o) if key in fin else D(0)
        right = o.total_amount * len(o.payments) if (o.payments and lo <= o.ordered_at < hi) else D(0)
        assert attributed == right - left, key
    for overlap in scenario(oracle, "A")["overlaps"]:
        assert overlap["order_number"] in per_key


# ---------------------------------------------------------------------------- scenario B


def test_scenario_b_engine_matches_manifest(loaded, oracle, db_session):
    inv, sc = loaded["B"], scenario(oracle, "B")
    exp = sc["expected"]
    assert (inv.baseline_left, inv.baseline_right, inv.baseline_delta) == tuple(
        D(exp[k]) for k in ("left_total", "right_total", "delta")
    )
    assert inv.unexplained_delta == D(exp["unexplained"])
    found = findings_by_rule(inv)
    for issue in sc["issues"]:
        (f,) = found[(issue["category"], issue["rule_id"])]
        assert f.evidence_status.value == issue["expected_status"], issue["id"]
        if issue["expected_contribution"] is None:
            assert f.amount_impact is None
            assert D(f.calculation_json["excess"]) == D(issue["indicative_amount"])
            assert {e.business_key for e in evidence(db_session, f)} == set(issue["affected"])
            assert all(e.contribution is None for e in evidence(db_session, f))
        elif issue["category"] == "amount_mismatch":
            assert f.amount_impact == D(
                exp["amount_mismatch_finding"]
            )  # keying errors + unattributed fan-out copies
            assert set(issue["affected"]) <= {e.business_key for e in evidence(db_session, f)}
        elif issue["category"] == "unexplained":
            assert f.amount_impact == D(issue["expected_contribution"])
            assert len(evidence(db_session, f)) == len(issue["affected"])
        else:
            assert f.amount_impact == D(issue["expected_contribution"])
            assert {e.business_key for e in evidence(db_session, f)} == set(issue["affected"])
    (kq,) = found[("key_quality", "data.non_unique_keys")]
    assert kq.evidence_status.value == "suspected" and kq.amount_impact is None
    assert kq.calculation_json == {"left": 0, "right": exp["right_duplicate_keys"]}


def test_scenario_b_matches_independent_sql_over_stored_records(loaded, oracle, db_session):
    inv = loaded["B"]
    left, right = sorted(inv.inputs, key=lambda i: i.side.value)
    row = db_session.execute(
        text(
            """
            WITH l AS (SELECT business_key k, sum(amount - COALESCE(refund_amount, 0)) v FROM dataset_records WHERE dataset_id = :l GROUP BY 1),
                 r AS (SELECT business_key k, sum(amount - COALESCE(refund_amount, 0)) v, count(*) n, count(DISTINCT raw_payload_hash) h
                       FROM dataset_records WHERE dataset_id = :r AND business_key IS NOT NULL GROUP BY 1)
            SELECT (SELECT sum(v) FROM l),
                   (SELECT sum(amount - COALESCE(refund_amount, 0)) FROM dataset_records WHERE dataset_id = :r),
                   (SELECT -sum(l.v) FROM l LEFT JOIN r USING (k) WHERE r.k IS NULL),
                   (SELECT sum(r.v / r.n * (r.n - r.h)) FROM r WHERE r.h < r.n),
                   (SELECT sum(amount) FROM dataset_records WHERE dataset_id = :r AND business_key IS NULL)
            """
        ),
        {"l": left.dataset_id, "r": right.dataset_id},
    ).one()
    exp = scenario(oracle, "B")["expected"]
    issues = {i["id"]: i for i in scenario(oracle, "B")["issues"]}
    assert (row[0], row[1]) == (D(exp["left_total"]), D(exp["right_total"]))
    assert row[2] == D(issues["B1"]["expected_contribution"])
    assert row[3] == D(issues["B3"]["expected_contribution"])
    assert row[4] == D(issues["B5"]["expected_contribution"])


# ---------------------------------------------------------------------------- persistence and reproducibility


def _signature(findings):
    return sorted(
        (f.type.value, str(f.amount_impact), f.evidence_status.value, f.proof_hash) for f in findings
    )


def test_results_persist_and_rerun_identically(loaded, db_session):
    for inv in loaded.values():
        stored = db_session.scalars(select(Finding).where(Finding.investigation_id == inv.id)).all()
        assert (
            sum((f.amount_impact for f in stored if f.amount_impact is not None), D(0)) == inv.baseline_delta
        )
        for f in stored:
            if f.amount_impact is not None:
                assert sum((e.contribution for e in evidence(db_session, f)), D(0)) == f.amount_impact
        left, right = sorted(inv.inputs, key=lambda i: i.side.value)
        again = run_investigation(
            db_session,
            "rerun",
            InvestigationSide(left.dataset, inv.left_metric),
            InvestigationSide(right.dataset, inv.right_metric),
        )
        assert _signature(again.findings) == _signature(stored)
