import csv
import io
import os
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.adapters import registered_queries
from app.api.deps import get_settings
from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from tests.fixtures import write_xlsx

SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")
ADMIN_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_ADMIN_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not SOURCE_URL, reason="TRUTHSPLIT_TEST_SOURCE_URL is not set"),
]
MARCH = {"start_date": "2026-03-01", "end_date": "2026-04-01"}


def settings_for(tmp_path, **kw):
    return Settings(
        database_url="unused",
        storage_dir=tmp_path,
        allowed_source_hosts="localhost,127.0.0.1,host.docker.internal",
        **kw,
    )


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    for key in [k for k in os.environ if k.startswith("TRUTHSPLIT_SOURCE_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", SOURCE_URL)
    settings = settings_for(tmp_path)
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def reader_sql(sql, params=None):
    """Independent read of the source, not through TruthSplit's adapter."""
    engine = create_engine(SOURCE_URL)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params or {}).all()
    engine.dispose()
    return rows


def test_connection_listing_never_exposes_credentials(client):
    resp = client.get("/api/connections")
    url = make_url(SOURCE_URL)
    assert resp.json() == [
        {"name": "demo", "host": url.host, "port": url.port, "database": url.database, "host_allowed": True}
    ]
    assert url.password not in resp.text and url.username not in resp.text


def test_reader_role_passes_the_safety_check(client):
    body = client.post("/api/connections/demo/test").json()
    assert body["ok"] is True and body["problems"] == []
    assert body["user"] == "ts_reader" and body["role_read_only_default"] is True


@pytest.mark.skipif(not ADMIN_URL, reason="TRUTHSPLIT_TEST_SOURCE_ADMIN_URL is not set")
def test_privileged_role_is_reported_and_never_queried(client, monkeypatch):
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", ADMIN_URL)
    body = client.post("/api/connections/demo/test").json()
    assert body["ok"] is False
    assert "role is a superuser" in body["problems"] and any("write to" in p for p in body["problems"])
    resp = client.post("/api/registered-queries/order_lines/preview", json={"params": MARCH})
    assert resp.status_code == 502 and "not a read-only login" in resp.json()["detail"]


def test_disallowed_host_is_refused_before_connecting(client, monkeypatch):
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", "postgresql+psycopg://u:secret@10.255.255.1:5432/x")
    resp = client.post("/api/connections/demo/test")
    assert resp.status_code == 502 and "not in the allowed source hosts" in resp.json()["detail"]
    assert client.get("/api/connections").json()[0]["host_allowed"] is False


def test_bad_credentials_give_a_sanitised_error(client, monkeypatch):
    bad = SOURCE_URL.replace(SOURCE_URL.split(":")[2].split("@")[0], "wrong-password-123")
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", bad)
    resp = client.post("/api/connections/demo/test")
    assert resp.status_code == 502
    assert "wrong-password-123" not in resp.text and "ts_reader" not in resp.text
    assert "Could not connect to source 'demo'" in resp.json()["detail"]


def test_unknown_connection_is_404(client):
    assert client.post("/api/connections/nope/test").status_code == 404


def test_catalog_lists_only_selectable_tables(client):
    tables = {
        t["table"]: [c["name"] for c in t["columns"]]
        for t in client.get("/api/connections/demo/catalog").json()
    }
    assert set(tables) == {"customers", "products", "orders", "order_items", "payments", "refunds"}
    assert tables["orders"][:3] == ["id", "order_number", "customer_id"]


def test_registered_query_metadata(client):
    queries = {q["key"]: q for q in client.get("/api/registered-queries").json()}
    sales = queries["sales_order_payments"]
    assert sales["joins"] == [{"left": "orders.id", "right": "payments.order_id", "cardinality": "1:N"}]
    assert sales["grain"] == "payment" and len(sales["sql_sha256"]) == 64
    assert [p["name"] for p in sales["params"]] == ["start_date", "end_date"]
    assert client.get("/api/registered-queries/nope").status_code == 404


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"start_date": "2026-03-01"}, "missing parameters: end_date"),
        ({**MARCH, "extra": "1"}, "unknown parameters: extra"),
        ({"start_date": "March", "end_date": "2026-04-01"}, "must be a date"),
        ({"start_date": "2026-03-01'; DROP TABLE orders; --", "end_date": "2026-04-01"}, "must be a date"),
    ],
)
def test_parameter_validation(client, params, message):
    resp = client.post("/api/registered-queries/order_lines/preview", json={"params": params})
    assert resp.status_code == 422 and message in resp.json()["detail"]


def test_preview_is_limited(client):
    body = client.post("/api/registered-queries/order_lines/preview", json={"params": MARCH}).json()
    assert body["columns"] == [
        "order_number",
        "line_no",
        "ordered_at",
        "line_amount",
        "currency",
        "status",
        "category",
    ]
    assert len(body["rows"]) == 50 and body["truncated"] is True


def test_snapshot_matches_an_independent_read_and_is_idempotent(client):
    resp = client.post("/api/registered-queries/sales_order_payments/snapshots", json={"params": MARCH})
    assert resp.status_code == 201, resp.text
    ds = resp.json()["dataset"]
    count, total = reader_sql(
        "SELECT count(*), sum(o.total_amount) FROM orders o JOIN payments p ON p.order_id = o.id"
        " WHERE o.ordered_at >= '2026-03-01T00:00:00Z' AND o.ordered_at < '2026-04-01T00:00:00Z'"
    )[0]
    assert ds["row_count"] == count and ds["rejected_row_count"] == 0
    assert ds["source_type"] == "postgres_query"
    assert ds["source_schema"]["grain"] == "payment" and ds["source_schema"]["role"] == "ts_reader"
    records = client.get(f"/api/datasets/{ds['id']}/records", params={"limit": 1000}).json()
    assert records["total"] == count
    again = client.post("/api/registered-queries/sales_order_payments/snapshots", json={"params": MARCH})
    assert again.status_code == 200 and again.json()["dataset"]["id"] == ds["id"]
    other = client.post(
        "/api/registered-queries/sales_order_payments/snapshots",
        json={"params": {"start_date": "2026-04-01", "end_date": "2026-05-01"}},
    )
    assert other.status_code == 201 and other.json()["dataset"]["id"] != ds["id"]
    assert Decimal(total) > 0


def test_statement_timeout(client, monkeypatch, tmp_path):
    slow = registered_queries.RegisteredQuery(
        key="slow",
        title="slow",
        description="",
        connection="demo",
        sql="SELECT 1 AS x FROM pg_sleep(2)",
        params=(),
        mapping={"amount": "x"},
        tables=(),
    )
    monkeypatch.setitem(registered_queries.REGISTRY, "slow", slow)
    app.dependency_overrides[get_settings] = lambda: settings_for(tmp_path, statement_timeout_ms=200)
    resp = client.post("/api/registered-queries/slow/preview", json={"params": {}})
    assert resp.status_code == 502 and "200 ms statement timeout" in resp.json()["detail"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders FOR UPDATE",  # row locks need a read-write transaction
        "WITH d AS (DELETE FROM refunds RETURNING id) SELECT id FROM d",  # not allowed inside the wrapper subquery
        "SELECT 1 AS x; DELETE FROM refunds",  # second statement can't escape the wrapper
    ],
)
def test_registered_sql_cannot_write_even_if_it_tries(client, monkeypatch, sql):
    bad = registered_queries.RegisteredQuery(
        key="bad",
        title="bad",
        description="",
        connection="demo",
        sql=sql,
        params=(),
        mapping={"amount": "id"},
        tables=(),
    )
    monkeypatch.setitem(registered_queries.REGISTRY, "bad", bad)
    before = reader_sql("SELECT count(*) FROM refunds")[0][0]
    resp = client.post("/api/registered-queries/bad/preview", json={"params": {}})
    assert resp.status_code == 502
    assert reader_sql("SELECT count(*) FROM refunds")[0][0] == before


def test_row_limit_is_an_error(client, tmp_path):
    app.dependency_overrides[get_settings] = lambda: settings_for(tmp_path, max_rows=10)
    resp = client.post("/api/registered-queries/order_lines/snapshots", json={"params": MARCH})
    assert resp.status_code == 422 and "more than 10 rows" in resp.json()["detail"]


def _investigate(client, left_ds, right_ds, **metric):
    m = client.post("/api/metric-definitions", json={"name": "cross", "aggregation": "sum", **metric}).json()
    resp = client.post(
        "/api/investigations",
        json={
            "title": "cross",
            "left": {"dataset_id": left_ds, "metric_definition_id": m["id"]},
            "right": {"dataset_id": right_ds, "metric_definition_id": m["id"]},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


FINANCE_SQL = registered_queries.REGISTRY["finance_order_revenue"].sql


def _file_copy_of_finance(client, tmp_path, fmt):
    rows = reader_sql(FINANCE_SQL, {"start_date": "2026-03-01", "end_date": "2026-04-01"})
    header = ["order_number", "ordered_at", "total_amount", "currency", "status", "refunded", "region"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows([[r[0], r[1].isoformat(), r[2], r[3], r[4], r[5], r[6]] for r in rows])
        name, data = "finance_export.csv", buf.getvalue().encode()
    else:
        # Spreadsheets have no time zone; export as UTC wall-clock and say so in the mapping.
        path = write_xlsx(
            tmp_path / "finance.xlsx",
            {
                "Export": [header]
                + [
                    [r[0], r[1].replace(tzinfo=None), float(r[2]), r[3], r[4], float(r[5]), r[6]]
                    for r in rows
                ]
            },
        )
        name, data = "finance_export.xlsx", path.read_bytes()
    up = client.post("/api/imports/file", files={"file": (name, data)}).json()
    mapping = {**registered_queries.REGISTRY["finance_order_revenue"].mapping, "timezone": "UTC"}
    ds = client.post("/api/datasets", json={"upload_id": up["upload"]["id"], "mapping": mapping}).json()
    return ds["dataset"]["id"], len(rows)


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_file_versus_query_parity_on_the_same_records(client, tmp_path, fmt):
    snap = client.post(
        "/api/registered-queries/finance_order_revenue/snapshots", json={"params": MARCH}
    ).json()["dataset"]
    file_ds, n = _file_copy_of_finance(client, tmp_path, fmt)
    inv = _investigate(client, file_ds, snap["id"], refund_policy="net_of_refunds")
    assert snap["row_count"] == n
    assert Decimal(inv["baseline_delta"]) == 0 and inv["findings"] == []
    assert inv["comparison"]["matched_keys"] == n


def test_query_versus_query_reconciles_against_independent_sql(client):
    finance = client.post(
        "/api/registered-queries/finance_order_revenue/snapshots", json={"params": MARCH}
    ).json()["dataset"]
    sales = client.post(
        "/api/registered-queries/sales_order_payments/snapshots", json={"params": MARCH}
    ).json()["dataset"]
    inv = _investigate(client, finance["id"], sales["id"])
    (f_total,) = reader_sql(
        f"SELECT sum(total_amount) FROM ({FINANCE_SQL}) q",
        {"start_date": "2026-03-01", "end_date": "2026-04-01"},
    )[0]
    (s_total,) = reader_sql(
        f"SELECT sum(total_amount) FROM ({registered_queries.REGISTRY['sales_order_payments'].sql}) q",
        {"start_date": "2026-03-01", "end_date": "2026-04-01"},
    )[0]
    assert Decimal(inv["baseline_left"]) == f_total and Decimal(inv["baseline_right"]) == s_total
    assert Decimal(inv["explained_delta"]) + Decimal(inv["unexplained_delta"]) == s_total - f_total
    # Split payments repeat an order on the sales side; those keys are flagged as non-unique.
    assert inv["comparison"]["right_duplicate_keys"] > 0


def test_snapshot_provenance_is_traceable(client, db_session):
    ds = client.post("/api/registered-queries/order_lines/snapshots", json={"params": MARCH}).json()[
        "dataset"
    ]
    row = db_session.execute(
        text(
            "SELECT source_connection_id IS NOT NULL, query_key, query_params, query_hash FROM datasets WHERE id = :id"
        ),
        {"id": ds["id"]},
    ).one()
    assert row[0] and row[1] == "order_lines" and row[2] == MARCH and len(row[3]) == 64
    first = client.get(f"/api/datasets/{ds['id']}/records", params={"limit": 1}).json()["items"][0]
    assert first["row_number"] == 1 and first["business_key"].startswith('["SO-')


def test_registry_sql_hash_changes_with_sql():
    q = registered_queries.REGISTRY["order_lines"]
    assert replace(q, sql=q.sql + " ").sql_hash != q.sql_hash


def test_fresh_snapshot_is_current(client):
    ds = client.post("/api/registered-queries/order_lines/snapshots", json={"params": MARCH}).json()[
        "dataset"
    ]
    body = client.post(f"/api/datasets/{ds['id']}/freshness").json()
    assert body["status"] == "current" and body["current_content_sha256"] == body["snapshot_content_sha256"]


def test_changed_registered_sql_is_reported_without_running(client, monkeypatch):
    ds = client.post("/api/registered-queries/order_lines/snapshots", json={"params": MARCH}).json()[
        "dataset"
    ]
    q = registered_queries.REGISTRY["order_lines"]
    monkeypatch.setitem(
        registered_queries.REGISTRY, "order_lines", replace(q, sql=q.sql + "\n  AND i.quantity > 1")
    )
    body = client.post(f"/api/datasets/{ds['id']}/freshness").json()
    assert body["status"] == "query_changed" and body["current_content_sha256"] is None


def test_freshness_is_only_for_query_snapshots(client):
    up = client.post("/api/imports/file", files={"file": ("a.csv", b"id,amount\n1,2\n")}).json()
    ds = client.post(
        "/api/datasets",
        json={"upload_id": up["upload"]["id"], "mapping": {"business_key": ["id"], "amount": "amount"}},
    ).json()
    assert client.post(f"/api/datasets/{ds['dataset']['id']}/freshness").status_code == 422


@pytest.mark.skipif(not ADMIN_URL, reason="TRUTHSPLIT_TEST_SOURCE_ADMIN_URL is not set")
def test_source_change_is_detected_and_old_snapshot_is_kept(client, monkeypatch):
    admin = create_engine(ADMIN_URL)
    with admin.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS ts_freshness_probe"))
        conn.execute(text("CREATE TABLE ts_freshness_probe (k text, amount numeric(12,2))"))
        conn.execute(text("INSERT INTO ts_freshness_probe VALUES ('a', 1.00), ('b', 2.00)"))
        conn.execute(text("GRANT SELECT ON ts_freshness_probe TO ts_reader"))
    probe = registered_queries.RegisteredQuery(
        key="probe",
        title="probe",
        description="",
        connection="demo",
        sql="SELECT k, amount FROM ts_freshness_probe",
        params=(),
        mapping={"business_key": ["k"], "amount": "amount", "default_currency": "USD"},
        tables=("ts_freshness_probe",),
    )
    monkeypatch.setitem(registered_queries.REGISTRY, "probe", probe)
    try:
        first = client.post("/api/registered-queries/probe/snapshots", json={"params": {}}).json()["dataset"]
        with admin.begin() as conn:
            conn.execute(text("UPDATE ts_freshness_probe SET amount = 5.00 WHERE k = 'b'"))
        stale = client.post(f"/api/datasets/{first['id']}/freshness").json()
        assert stale["status"] == "source_changed"
        second = client.post("/api/registered-queries/probe/snapshots", json={"params": {}})
        assert second.status_code == 201 and second.json()["dataset"]["id"] != first["id"]
        old = client.get(f"/api/datasets/{first['id']}/records").json()["items"]
        assert sorted(Decimal(r["amount"]) for r in old) == [Decimal(1), Decimal(2)]  # untouched
        assert (
            client.post(f"/api/datasets/{second.json()['dataset']['id']}/freshness").json()["status"]
            == "current"
        )
    finally:
        with admin.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS ts_freshness_probe"))
        admin.dispose()
