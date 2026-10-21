"""Simulations and investigation memory over the demo investigations (expected values from the manifest)."""

import io
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_settings
from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from app.seed.demo import manifest
from app.seed.load import load_demo

SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not SOURCE_URL, reason="TRUTHSPLIT_TEST_SOURCE_URL is not set"),
]
D = Decimal


@pytest.fixture
def env(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", SOURCE_URL)
    settings = Settings(
        database_url="unused",
        storage_dir=tmp_path,
        allowed_source_hosts="localhost,127.0.0.1,host.docker.internal",
    )
    ids = {k: v["investigation_id"] for k, v in load_demo(db_session, settings).items()}
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app), ids, db_session
    app.dependency_overrides.clear()


def issue(sid, iid):
    sc = next(s for s in manifest()["scenarios"] if s["id"] == sid)
    return next(i for i in sc["issues"] if i["id"] == iid), sc["expected"]


def simulate(client, inv_id, **rule):
    resp = client.post(f"/api/investigations/{inv_id}/simulations", json=rule)
    assert resp.status_code == 201, resp.text
    return resp.json()


STORED_STATE = """
SELECT md5(string_agg(x, ',' ORDER BY x)) FROM (
  SELECT concat_ws('|', id, baseline_left, baseline_right, baseline_delta, explained_delta, unexplained_delta, status) x FROM investigations
  UNION ALL SELECT concat_ws('|', id, type, evidence_status, amount_impact, proof_hash) FROM findings
  UNION ALL SELECT concat_ws('|', id, finding_id, contribution, detail::text) FROM evidence_records
  UNION ALL SELECT concat_ws('|', id, dataset_id, row_number, amount, refund_amount, raw_payload_hash) FROM dataset_records
  UNION ALL SELECT concat_ws('|', id, fingerprint, row_count) FROM datasets
) t
"""


def test_simulations_report_exact_changes_and_never_modify_stored_results(env):
    client, ids, session = env
    before = session.execute(text(STORED_STATE)).scalar()
    a1, a_exp = issue("A", "A1")
    a2, _ = issue("A", "A2")
    delta_a = D(a_exp["delta"])

    sim = simulate(client, ids["A"], rule_type="align_definition", side="left")
    assert (D(sim["before_delta"]), D(sim["after_delta"])) == (
        delta_a,
        delta_a - D(a1["expected_contribution"]),
    )
    assert D(sim["resolved_amount"]) == D(a1["expected_contribution"])
    assert D(sim["result_json"]["delta_change"]) == -D(a1["expected_contribution"])
    assert all(
        f["rule_id"] != "definition.refund_policy" for f in sim["result_json"]["simulated"]["findings"]
    )
    assert sim["affected_rows"] == len(a1["affected"])  # one finance row per refunded order changes value

    sim = simulate(client, ids["A"], rule_type="remove_duplicates", side="right", kind="join_copies")
    assert D(sim["after_delta"]) == delta_a - D(a2["expected_contribution"])
    assert sim["affected_rows"] == len(a2["affected"])  # one dropped copy per split order

    b3, b_exp = issue("B", "B3")
    sim = simulate(client, ids["B"], rule_type="remove_duplicates", side="right")
    assert D(sim["after_delta"]) == D(b_exp["delta"]) - D(b3["expected_contribution"])
    assert D(sim["result_json"]["simulated"]["unexplained"]) == D(b_exp["unexplained"])  # manual lines remain

    again = simulate(client, ids["B"], rule_type="remove_duplicates", side="right")
    assert (again["after_delta"], again["result_json"]["simulated"]) == (
        sim["after_delta"],
        sim["result_json"]["simulated"],
    )
    assert len(client.get(f"/api/investigations/{ids['B']}/simulations").json()) == 2
    assert session.execute(text(STORED_STATE)).scalar() == before


def test_adjust_mapping_on_a_file_dataset(env):
    client, _, _ = env
    csv_bytes = b"id,gross,net,ccy\nA,100.00,80.00,USD\nB,50.00,50.00,USD\n"
    up = client.post("/api/imports/file", files={"file": ("remap.csv", io.BytesIO(csv_bytes))}).json()
    ds = client.post(
        "/api/datasets",
        json={
            "upload_id": up["upload"]["id"],
            "mapping": {"business_key": ["id"], "amount": "gross", "currency": "ccy"},
        },
    ).json()["dataset"]["id"]
    m = client.post("/api/metric-definitions", json={"name": "remap", "aggregation": "sum"}).json()["id"]
    inv = client.post(
        "/api/investigations",
        json={
            "title": "remap",
            "left": {"dataset_id": ds, "metric_definition_id": m},
            "right": {"dataset_id": ds, "metric_definition_id": m},
        },
    ).json()
    sim = simulate(
        client,
        inv["id"],
        rule_type="adjust_mapping",
        side="right",
        mapping={"business_key": ["id"], "amount": "net", "currency": "ccy"},
    )
    assert (D(sim["before_delta"]), D(sim["after_right"]), D(sim["after_delta"])) == (D(0), D(130), D(-20))
    assert sim["affected_rows"] == 1 and D(sim["resolved_amount"]) == D(-20)


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (
            {"rule_type": "adjust_mapping", "side": "left", "mapping": {"amount": "total_amount"}},
            "only available for file datasets",
        ),
        ({"rule_type": "refund_policy", "side": "right", "policy": "net_of_refunds"}, "refund_amount"),
    ],
)
def test_invalid_simulations_are_422(env, rule, message):
    client, ids, _ = env
    resp = client.post(f"/api/investigations/{ids['A']}/simulations", json=rule)
    assert resp.status_code == 422 and message in resp.json()["detail"]


def test_simulation_request_validation(env):
    client, ids, _ = env
    assert (
        client.post(
            f"/api/investigations/{ids['A']}/simulations", json={"rule_type": "delete_everything"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/investigations/{ids['A']}/simulations",
            json={"rule_type": "refund_policy", "side": "middle", "policy": "gross"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/investigations/00000000-0000-0000-0000-000000000000/simulations",
            json={"rule_type": "align_window", "side": "left"},
        ).status_code
        == 404
    )


def test_status_workflow_notes_and_history(env):
    client, ids, _ = env
    inv = ids["A"]
    finding = next(
        f
        for f in client.get(f"/api/investigations/{inv}").json()["findings"]
        if f["rule_id"] == "data.join_amplification"
    )

    assert (
        client.patch(f"/api/investigations/{inv}/status", json={"status": "resolved"}).status_code == 422
    )  # needs a note
    assert (
        client.patch(
            f"/api/investigations/{inv}/status", json={"status": "in_review", "reviewer": "ana"}
        ).json()["status"]
        == "in_review"
    )
    resp = client.post(
        f"/api/investigations/{inv}/notes",
        json={
            "decision": "accepted",
            "note": "Sales query counts split payments twice.",
            "finding_id": finding["id"],
            "reviewer": "ana",
        },
    )
    assert resp.status_code == 201
    assert (
        client.post(
            f"/api/investigations/{inv}/notes", json={"decision": "accepted", "note": "x"}
        ).status_code
        == 422
    )  # no finding
    other = client.get(f"/api/investigations/{ids['B']}").json()["findings"][0]["id"]
    assert (
        client.post(
            f"/api/investigations/{inv}/notes",
            json={"decision": "rejected", "note": "x", "finding_id": other},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/investigations/{inv}/status",
            json={"status": "resolved", "note": "Definitions agreed with finance.", "reviewer": "ana"},
        ).status_code
        == 200
    )
    assert (
        client.patch(f"/api/investigations/{inv}/status", json={"status": "in_review"}).status_code == 422
    )  # resolved -> in_review not allowed

    detail = client.get(f"/api/investigations/{inv}").json()
    assert detail["status"] == "resolved"
    assert (
        next(f for f in detail["findings"] if f["id"] == finding["id"])["evidence_status"] == "confirmed"
    )  # unchanged by review
    actions = [e["action"] for e in client.get(f"/api/investigations/{inv}/history").json()]
    assert actions == [
        "investigation.created",
        "investigation.status_changed",
        "note.added",
        "investigation.status_changed",
    ]
    notes = client.get(f"/api/investigations/{inv}/notes").json()
    assert [n["decision"] for n in notes] == ["accepted", "comment"]


def test_rejecting_a_finding_is_recorded_without_changing_it(env):
    client, ids, _ = env
    inv = ids["B"]
    suspected = next(
        f
        for f in client.get(f"/api/investigations/{inv}").json()["findings"]
        if f["rule_id"] == "data.join_amplification"
    )
    resp = client.post(
        f"/api/investigations/{inv}/notes",
        json={
            "decision": "rejected",
            "note": "Two payment memos per order are legitimate here.",
            "finding_id": suspected["id"],
            "reviewer": "lee",
        },
    )
    assert (
        resp.status_code == 201
        and resp.json()["decision"] == "rejected"
        and resp.json()["finding_id"] == suspected["id"]
    )
    after = next(
        f for f in client.get(f"/api/investigations/{inv}").json()["findings"] if f["id"] == suspected["id"]
    )
    assert after == suspected  # the finding itself, including its status, is unchanged
    event = client.get(f"/api/investigations/{inv}/history").json()[-1]
    assert event["action"] == "note.added" and event["actor"] == "lee"
    assert (
        event["metadata_json"]["decision"] == "rejected"
        and event["metadata_json"]["finding_rule"] == "data.join_amplification"
    )
    assert (
        client.post(
            f"/api/investigations/{inv}/notes",
            json={"decision": "rejected", "note": "   ", "finding_id": suspected["id"]},
        ).status_code
        == 422
    )


def test_similar_investigations_are_explained_and_never_auto_confirm(env):
    client, ids, _ = env
    sim_a = client.get(f"/api/investigations/{ids['A']}/similar").json()["matches"]
    assert [(m["investigation_id"], m["score"]) for m in sim_a] == [(ids["C"], 5), (ids["B"], 4)]
    assert sim_a[0]["reasons"] == [
        "both have a confirmed finding from rule data.join_amplification (+3)",
        "both read source registered query sales_order_payments (+2)",
    ]
    finding = next(
        f
        for f in client.get(f"/api/investigations/{ids['A']}").json()["findings"]
        if f["rule_id"] == "data.join_amplification"
    )
    client.post(
        f"/api/investigations/{ids['A']}/notes",
        json={"decision": "accepted", "note": "Known payments fan-out.", "finding_id": finding["id"]},
    )
    before = client.get(f"/api/investigations/{ids['C']}").json()["findings"]
    sim_c = client.get(f"/api/investigations/{ids['C']}/similar").json()["matches"]
    assert [(m["investigation_id"], m["score"]) for m in sim_c] == [(ids["A"], 5), (ids["B"], 3)]
    (suggestion,) = sim_c[0]["accepted_diagnoses"]
    assert suggestion["rule_id"] == "data.join_amplification" and suggestion["applies_here"] is True
    assert client.get(f"/api/investigations/{ids['C']}").json()["findings"] == before  # nothing applied


def test_recurring_patterns_and_history_filters(env):
    client, ids, _ = env
    rows = {(r["finding_type"], r["rule_id"]): r for r in client.get("/api/memory/patterns").json()}
    amp = rows[("join_amplification", "data.join_amplification")]
    assert (amp["investigations"], amp["confirmed"], amp["suspected"], amp["recurring"]) == (3, 2, 1, True)
    assert D(amp["total_absolute_amount"]) == D("112448.00")  # 56,224.00 in A and in C; B's is not attributed
    assert rows[("refund_policy", "data.refund_identity")]["investigations"] == 1
    listed = client.get("/api/investigations", params={"finding_type": "unexplained"}).json()
    assert [i["id"] for i in listed] == [ids["B"]]
    assert {i["id"] for i in client.get("/api/investigations", params={"q": "demo c"}).json()} == {ids["C"]}
    assert client.get("/api/investigations", params={"q": "100%_"}).json() == []
    assert len(client.get("/api/investigations", params={"is_demo": True, "status": "open"}).json()) == 3
