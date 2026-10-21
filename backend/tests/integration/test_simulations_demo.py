"""All five what-if corrections on the deterministic demo data. Expected before/after values are computed from
the pure-Python source model (app/seed/shop.py), never from the engine."""

import hashlib
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from app.api.deps import get_settings
from app.core.config import Settings
from app.db.imports import upload_path
from app.db.session import get_session
from app.main import app
from app.models import FileUpload
from app.seed.demo import _ledger_plan, _settlement_plan, finance_orders, net, sales_orders
from app.seed.load import load_demo
from app.seed.shop import build_orders

SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not SOURCE_URL, reason="TRUTHSPLIT_TEST_SOURCE_URL is not set"),
]
D = Decimal
ZERO = D(0)

ORDERS = build_orders()
FIN = finance_orders(ORDERS)
SALES = sales_orders(ORDERS)
FIN_NET = sum((net(o) for o in FIN), ZERO)
FIN_GROSS = sum((o.total_amount for o in FIN), ZERO)
SALES_ROWS = sum((o.total_amount * len(o.payments) for o in SALES), ZERO)
SALES_ONE_COPY = sum((o.total_amount for o in SALES), ZERO)
_plan = _settlement_plan(SALES)
_errors = {**_plan["refund_plus_error"], **_plan["plain_error"]}
SETTLED = {o.order_number: net(o) + _errors.get(o.id, ZERO) for o in SALES}

STORED_STATE = """
SELECT md5(string_agg(x, ',' ORDER BY x)) FROM (
  SELECT concat_ws('|', id, baseline_left, baseline_right, baseline_delta, explained_delta, unexplained_delta, status, config_json::text) x FROM investigations
  UNION ALL SELECT concat_ws('|', id, type, evidence_status, amount_impact, proof_hash) FROM findings
  UNION ALL SELECT concat_ws('|', id, finding_id, contribution, detail::text) FROM evidence_records
  UNION ALL SELECT concat_ws('|', id, dataset_id, row_number, amount, refund_amount, occurred_at, attributes::text, raw_payload_hash) FROM dataset_records
  UNION ALL SELECT concat_ws('|', id, fingerprint, row_count, column_mapping::text, schema_json::text) FROM datasets
) t
"""


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
    yield TestClient(app), ids, db_session, settings
    app.dependency_overrides.clear()


def fingerprints(session, settings):
    """Stored state of results, datasets and records, uploaded files on disk, and the source database."""
    files = {
        u.sha256: hashlib.sha256(upload_path(settings.storage_dir, u).read_bytes()).hexdigest()
        for u in session.scalars(select(FileUpload))
    }
    engine = create_engine(SOURCE_URL)
    with engine.connect() as conn:
        source = conn.execute(
            text(
                "SELECT md5((SELECT string_agg(t::text, ',' ORDER BY t::text) FROM orders t) || (SELECT string_agg(t::text, ',' ORDER BY t::text) FROM payments t)"
                " || (SELECT string_agg(t::text, ',' ORDER BY t::text) FROM refunds t) || (SELECT string_agg(t::text, ',' ORDER BY t::text) FROM order_items t))"
            )
        ).scalar()
    engine.dispose()
    return session.execute(text(STORED_STATE)).scalar(), files, source


def run(client, inv, **rule):
    resp = client.post(f"/api/investigations/{inv}/simulations", json=rule)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert D(body["resolved_amount"]) == abs(D(body["before_delta"])) - abs(D(body["after_delta"]))
    assert D(body["result_json"]["delta_change"]) == D(body["after_delta"]) - D(body["before_delta"])
    return body


def values(body):
    return tuple(
        D(body[k])
        for k in ("before_left", "before_right", "before_delta", "after_left", "after_right", "after_delta")
    )


def test_all_five_corrections_match_the_model_and_change_nothing(env):
    client, ids, session, settings = env
    before_state = fingerprints(session, settings)
    for uploaded_sha, on_disk in before_state[1].items():
        assert uploaded_sha == on_disk  # stored files are byte-identical to what was uploaded

    # 1. remove_duplicates (join copies), demo A right: sales rows collapse to one per order.
    body = run(client, ids["A"], rule_type="remove_duplicates", side="right", kind="join_copies")
    assert values(body) == (
        FIN_NET,
        SALES_ROWS,
        SALES_ROWS - FIN_NET,
        FIN_NET,
        SALES_ONE_COPY,
        SALES_ONE_COPY - FIN_NET,
    )
    assert body["affected_rows"] == sum(len(o.payments) - 1 for o in SALES)

    # 1b. remove_duplicates (exact), demo B right: the ledger's twice-exported lines.
    dups = _ledger_plan(FIN)["duplicate"]
    b_right = next(
        D(i["baseline_right"]) for i in client.get("/api/investigations").json() if i["id"] == ids["B"]
    )
    body = run(client, ids["B"], rule_type="remove_duplicates", side="right")
    assert D(body["after_right"]) == b_right - sum((net(o) for o in dups), ZERO)
    assert D(body["after_delta"]) == D(body["after_right"]) - FIN_NET and body["affected_rows"] == len(dups)

    # 2. refund_policy, demo A left -> gross.
    body = run(client, ids["A"], rule_type="refund_policy", side="left", policy="gross")
    assert values(body)[3:] == (FIN_GROSS, SALES_ROWS, SALES_ROWS - FIN_GROSS)
    assert body["affected_rows"] == sum(1 for o in FIN if o.refund)

    # 3. align_definition, demo A left takes the sales definition (which differs only in refund policy).
    assert values(run(client, ids["A"], rule_type="align_definition", side="left"))[3:] == (
        FIN_GROSS,
        SALES_ROWS,
        SALES_ROWS - FIN_GROSS,
    )

    # 4. adjust_mapping, demo C right: read the settlement's gross column instead of net.
    c = client.get(f"/api/investigations/{ids['C']}").json()
    right_ds = next(i["dataset_id"] for i in c["inputs"] if i["side"] == "right")
    mapping = client.get(f"/api/datasets/{right_ds}").json()["column_mapping"]
    body = run(
        client,
        ids["C"],
        rule_type="adjust_mapping",
        side="right",
        mapping={**mapping, "amount": "gross_amount"},
    )
    assert values(body) == (
        SALES_ROWS,
        sum(SETTLED.values(), ZERO),
        sum(SETTLED.values(), ZERO) - SALES_ROWS,
        SALES_ROWS,
        SALES_ONE_COPY,
        SALES_ONE_COPY - SALES_ROWS,  # only the join copies remain: exactly scenario C's C1
    )
    assert body["affected_rows"] == sum(1 for o in SALES if o.total_amount != SETTLED[o.order_number])
    simulated_rules = {
        f["rule_id"] for f in body["result_json"]["simulated"]["findings"] if f["amount"] is not None
    }
    assert simulated_rules == {"data.join_amplification"}

    # 5. align_window: the settlement export compared with itself under a New York and a UTC March window.
    ny = client.post(
        "/api/metric-definitions",
        json={
            "name": "Gross, March NY",
            "aggregation": "sum",
            "window_start": "2026-03-01T00:00:00",
            "window_end": "2026-04-01T00:00:00",
            "timezone": "America/New_York",
        },
    ).json()["id"]
    utc = client.post(
        "/api/metric-definitions",
        json={
            "name": "Gross, March UTC",
            "aggregation": "sum",
            "window_start": "2026-03-01T00:00:00",
            "window_end": "2026-04-01T00:00:00",
        },
    ).json()["id"]
    inv = client.post(
        "/api/investigations",
        json={
            "title": "window",
            "left": {"dataset_id": right_ds, "metric_definition_id": ny},
            "right": {"dataset_id": right_ds, "metric_definition_id": utc},
        },
    ).json()
    ny_start = datetime(2026, 3, 1, 5, tzinfo=UTC)  # midnight in New York (EST)
    early = sum((SETTLED[o.order_number] for o in SALES if o.ordered_at < ny_start), ZERO)
    total = sum(SETTLED.values(), ZERO)
    body = run(client, inv["id"], rule_type="align_window", side="left")
    assert values(body) == (total - early, total, early, total, total, ZERO)
    assert body["affected_rows"] == sum(1 for o in SALES if o.ordered_at < ny_start)
    body = run(client, inv["id"], rule_type="align_window", side="right")
    assert values(body)[3:] == (total - early, total - early, ZERO)

    after_state = fingerprints(session, settings)
    assert after_state[1] == before_state[1]  # uploaded files untouched
    assert after_state[2] == before_state[2]  # source database untouched
    # Stored results change only by the one investigation this test created for the window case.
    session.execute(text("DELETE FROM simulations"))
    assert len(client.get("/api/investigations").json()) == 4


def test_simulations_never_change_stored_results(env):
    client, ids, session, settings = env
    before = fingerprints(session, settings)
    for inv, rule in [
        (ids["A"], {"rule_type": "remove_duplicates", "side": "both", "kind": "join_copies"}),
        (ids["A"], {"rule_type": "align_definition", "side": "left"}),
        (ids["B"], {"rule_type": "remove_duplicates", "side": "both"}),
        (ids["B"], {"rule_type": "align_window", "side": "left"}),
        (ids["C"], {"rule_type": "refund_policy", "side": "right", "policy": "exclude_refunded"}),
    ]:
        run(client, inv, **rule)
    assert fingerprints(session, settings) == before


def test_persistence_and_repeat(env):
    client, ids, _, _ = env
    first = run(client, ids["B"], rule_type="remove_duplicates", side="right", kind="join_copies")
    again = run(client, ids["B"], rule_type="remove_duplicates", side="right", kind="join_copies")
    assert first["id"] != again["id"]
    strip = lambda b: {k: v for k, v in b.items() if k not in ("id", "created_at")}  # noqa: E731
    assert strip(first) == strip(again)
    stored = client.get(f"/api/simulations/{first['id']}").json()
    assert strip(stored) == strip(first)
    assert [s["id"] for s in client.get(f"/api/investigations/{ids['B']}/simulations").json()] == [
        first["id"],
        again["id"],
    ]
    assert client.get("/api/simulations/00000000-0000-0000-0000-000000000000").status_code == 404
    history = [e["action"] for e in client.get(f"/api/investigations/{ids['B']}/history").json()]
    assert history.count("simulation.run") == 2


@pytest.mark.parametrize(
    ("sid", "rule", "status", "message"),
    [
        (
            "A",
            {"rule_type": "adjust_mapping", "side": "right", "mapping": {"amount": "total_amount"}},
            422,
            "only available for file datasets",
        ),
        (
            "C",
            {
                "rule_type": "adjust_mapping",
                "side": "right",
                "mapping": {"business_key": ["order_number"], "amount": "no_such_column"},
            },
            422,
            "not found in the file",
        ),
        (
            "C",
            {
                "rule_type": "adjust_mapping",
                "side": "right",
                "mapping": {
                    "business_key": ["order_number"],
                    "amount": "processor_ref",
                    "currency": "currency",
                },
            },
            422,
            "rejects every row",
        ),
        (
            "C",
            {"rule_type": "refund_policy", "side": "left", "policy": "net_of_refunds"},
            422,
            "refund_amount",
        ),
        ("A", {"rule_type": "remove_duplicates", "side": "left", "kind": "fuzzy"}, 422, None),
        ("A", {"rule_type": "align_window"}, 422, None),
        ("A", {"rule_type": "refund_policy", "side": "left", "policy": "maybe"}, 422, None),
    ],
)
def test_invalid_corrections(env, sid, rule, status, message):
    client, ids, _, _ = env
    resp = client.post(f"/api/investigations/{ids[sid]}/simulations", json=rule)
    assert resp.status_code == status, resp.text
    if message:
        assert message in resp.json()["detail"]
    assert client.get(f"/api/investigations/{ids[sid]}/simulations").json() == []


def test_partial_rejections_on_remap_are_reported(env):
    client, _, _, _ = env
    data = b"id,gross,alt,ccy\nA,100.00,90.00,USD\nB,50.00,n/a,USD\nC,10.00,10.00,USD\n"
    up = client.post("/api/imports/file", files={"file": ("alt.csv", data)}).json()
    mapping = {"business_key": ["id"], "amount": "gross", "currency": "ccy"}
    ds = client.post("/api/datasets", json={"upload_id": up["upload"]["id"], "mapping": mapping}).json()[
        "dataset"
    ]["id"]
    m = client.post("/api/metric-definitions", json={"name": "alt", "aggregation": "sum"}).json()["id"]
    inv = client.post(
        "/api/investigations",
        json={
            "title": "alt",
            "left": {"dataset_id": ds, "metric_definition_id": m},
            "right": {"dataset_id": ds, "metric_definition_id": m},
        },
    ).json()
    body = run(
        client, inv["id"], rule_type="adjust_mapping", side="right", mapping={**mapping, "amount": "alt"}
    )
    remap = body["result_json"]["remap"]
    assert (remap["rows_read"], remap["rows_accepted"], remap["rows_rejected"]) == (3, 2, 1)
    assert remap["issues"][0]["raw_value"] == "n/a" and remap["issues"][0]["row_number"] == 3
    # B is rejected under the new mapping, so it no longer contributes on the right.
    assert (D(body["after_right"]), D(body["after_delta"])) == (D(100), D(-60))
