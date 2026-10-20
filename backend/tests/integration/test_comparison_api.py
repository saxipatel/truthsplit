import csv
import io
import random
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_settings
from app.comparison.metrics import MetricSpec, evaluate
from app.comparison.sql import run_metric_sql
from app.core.config import Settings
from app.db.investigations import load_records
from app.db.session import get_session
from app.main import app

pytestmark = pytest.mark.db

MAPPING = {
    "source_record_id": "id",
    "business_key": ["order"],
    "amount": "amount",
    "refund_amount": "refund",
    "currency": "ccy",
    "occurred_at": "at",
    "status": "status",
    "attributes": ["region"],
}
HEADER = ["id", "order", "amount", "refund", "ccy", "at", "status", "region"]


@pytest.fixture
def client(db_session, tmp_path):
    settings = Settings(database_url="unused", storage_dir=tmp_path, max_upload_mb=5, max_rows=10_000)
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def import_rows(client, name, rows, mapping=MAPPING):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    up = client.post("/api/imports/file", files={"file": (name, buf.getvalue().encode())})
    assert up.status_code in (200, 201), up.text
    ds = client.post("/api/datasets", json={"upload_id": up.json()["upload"]["id"], "mapping": mapping})
    assert ds.status_code in (200, 201), ds.text
    return ds.json()["dataset"]["id"]


def define(client, name, **spec):
    resp = client.post("/api/metric-definitions", json={"name": name, "aggregation": "sum", **spec})
    assert resp.status_code == 201, resp.text
    return resp.json()


def varied_rows(seed=11, n=400):
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        order = f"O{rnd.randint(1, n // 2)}" if rnd.random() > 0.05 else ""
        amount = "" if rnd.random() < 0.03 else f"{rnd.randint(-500, 50000) / 100:.2f}"
        refund = rnd.choice(["", "", "0", f"{rnd.randint(1, 900) / 100:.2f}"])
        day = rnd.randint(1, 30)
        hour = rnd.randint(0, 23)
        at = (
            ""
            if rnd.random() < 0.02
            else f"2026-{rnd.choice(['03', '04'])}-{day:02d}T{hour:02d}:{rnd.randint(0, 59):02d}:00Z"
        )
        status = rnd.choice(["paid", "paid", "refunded", "void", ""])
        rows.append([f"r{i}", order, amount, refund, "USD", at, status, rnd.choice(["EU", "US", ""])])
    return rows


PARITY_SPECS = [
    {"aggregation": "sum"},
    {"aggregation": "count"},
    {"aggregation": "count_distinct"},
    {"aggregation": "sum", "refund_policy": "net_of_refunds"},
    {"aggregation": "sum", "refund_policy": "exclude_refunded", "grain": "business_key"},
    {"aggregation": "count", "refund_policy": "exclude_refunded"},
    {"aggregation": "sum", "window_start": "2026-03-01T00:00:00", "window_end": "2026-04-01T00:00:00"},
    {
        "aggregation": "sum",
        "window_start": "2026-03-01T00:00:00",
        "window_end": "2026-04-01T00:00:00",
        "timezone": "America/New_York",
        "window_end_inclusive": True,
    },
    {"aggregation": "sum", "window_end": "2026-03-15T12:00:00", "timezone": "Asia/Kolkata"},
    {"aggregation": "sum", "filters": [{"field": "status", "op": "ne", "value": "void"}]},
    {"aggregation": "sum", "filters": [{"field": "status", "op": "not_in", "value": ["void", "refunded"]}]},
    {"aggregation": "sum", "filters": [{"field": "status", "op": "is_null"}]},
    {"aggregation": "sum", "filters": [{"field": "attributes.region", "op": "in", "value": ["EU", "US"]}]},
    {
        "aggregation": "sum",
        "filters": [
            {"field": "attributes.region", "op": "not_null"},
            {"field": "amount", "op": "gt", "value": "100"},
        ],
    },
    {"aggregation": "sum", "filters": [{"field": "refund_amount", "op": "in", "value": ["0", "1.5"]}]},
    {"aggregation": "sum", "value_field": "refund_amount"},
    {
        "aggregation": "count_distinct",
        "grain": "business_key",
        "filters": [{"field": "amount", "op": "lte", "value": 0}],
    },
    {
        "aggregation": "sum",
        "grain": "business_key",
        "refund_policy": "net_of_refunds",
        "timezone": "Europe/London",
        "window_start": "2026-03-29T03:00:00",
    },
]


@pytest.mark.parametrize("spec", PARITY_SPECS, ids=[str(i) for i in range(len(PARITY_SPECS))])
def test_sql_and_python_evaluators_agree(client, db_session, spec):
    dataset_id = import_rows(client, "varied.csv", varied_rows())
    parsed = MetricSpec.model_validate(spec)
    python = evaluate(load_records(db_session, dataset_id), parsed)
    sql_value, sql_nulls = run_metric_sql(db_session, parsed, dataset_id)
    assert sql_value == python.value
    if parsed.aggregation.value == "sum":
        assert sql_nulls == python.excluded_counts.get("null_value", 0)


def test_metric_definitions_are_versioned_and_immutable(client):
    v1 = define(client, "Net revenue", refund_policy="net_of_refunds")
    v2 = define(client, "Net revenue", refund_policy="gross", description="Finance switched to gross")
    assert (v1["version"], v2["version"]) == (1, 2)
    listed = client.get("/api/metric-definitions", params={"name": "Net revenue"}).json()
    assert [(d["version"], d["refund_policy"]) for d in listed] == [(2, "gross"), (1, "net_of_refunds")]
    assert client.get(f"/api/metric-definitions/{v1['id']}").json()["refund_policy"] == "net_of_refunds"


def rows_for(orders: dict[str, str], **overrides):
    return [
        [f"{k}-1", k, v, overrides.get("refund", ""), "USD", "2026-03-10T10:00:00Z", "paid", "EU"]
        for k, v in orders.items()
    ]


def test_investigation_end_to_end(client):
    finance = rows_for({"A": "100.00", "B": "50.00", "C": "10.00"})
    sales = rows_for({"A": "100.00", "B": "55.25", "E": "30.00"}) + [
        ["n1", "", "1.00", "", "USD", "2026-03-10T10:00:00Z", "paid", "EU"]
    ]
    left = import_rows(client, "finance.csv", finance)
    right = import_rows(client, "sales.csv", sales)
    metric = define(client, "Revenue")
    resp = client.post(
        "/api/investigations",
        json={
            "title": "March revenue",
            "left": {"dataset_id": left, "metric_definition_id": metric["id"]},
            "right": {"dataset_id": right, "metric_definition_id": metric["id"]},
        },
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert (
        Decimal(inv["baseline_left"]),
        Decimal(inv["baseline_right"]),
        Decimal(inv["baseline_delta"]),
    ) == (D("160"), D("186.25"), D("26.25"))
    assert Decimal(inv["explained_delta"]) + Decimal(inv["unexplained_delta"]) == Decimal(
        inv["baseline_delta"]
    )
    assert Decimal(inv["unexplained_delta"]) == D("1.00")
    impacts = {
        f["type"]: Decimal(f["amount_impact"]) for f in inv["findings"] if f["amount_impact"] is not None
    }
    assert impacts == {
        "missing_records": D(-10),
        "extra_records": D(30),
        "amount_mismatch": D("5.25"),
        "unexplained": D(1),
    }
    assert {f["type"]: f["evidence_status"] for f in inv["findings"]}["unexplained"] == "unexplained"
    assert inv["comparison"]["matched_keys"] == 2
    assert inv["config_json"]["proof_queries"]["left"]["query_hash"]

    found = client.get(f"/api/investigations/{inv['id']}").json()
    assert found["id"] == inv["id"] and len(found["findings"]) == len(inv["findings"])
    assert client.get("/api/investigations").json()[0]["id"] == inv["id"]

    mismatch = next(f for f in inv["findings"] if f["type"] == "amount_mismatch")
    ev = client.get(f"/api/investigations/{inv['id']}/findings/{mismatch['id']}/evidence").json()
    assert ev["total"] == 1
    item = ev["items"][0]
    assert (
        item["business_key"],
        Decimal(item["left_amount"]),
        Decimal(item["right_amount"]),
        Decimal(item["contribution"]),
    ) == ("B", D(50), D("55.25"), D("5.25"))
    assert item["detail"]["left_rows"][0]["row_number"] == 3

    export = client.get(f"/api/investigations/{inv['id']}/evidence.csv")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    lines = list(csv.DictReader(io.StringIO(export.text)))
    assert sum(Decimal(r["contribution"]) for r in lines) == Decimal(inv["baseline_delta"])
    assert {r["finding_type"] for r in lines} == {
        "missing_records",
        "extra_records",
        "amount_mismatch",
        "unexplained",
    }


def test_equal_datasets_produce_no_findings(client):
    rows = rows_for({"A": "1.00", "B": "2.00"})
    ds = import_rows(client, "same.csv", rows)
    metric = define(client, "Rev")
    inv = client.post(
        "/api/investigations",
        json={
            "title": "same",
            "left": {"dataset_id": ds, "metric_definition_id": metric["id"]},
            "right": {"dataset_id": ds, "metric_definition_id": metric["id"]},
        },
    ).json()
    assert Decimal(inv["baseline_delta"]) == 0 and inv["findings"] == []


def test_incomparable_inputs_are_422(client):
    usd = import_rows(client, "usd.csv", rows_for({"A": "1"}))
    eur_rows = [[*r[:4], "EUR", *r[5:]] for r in rows_for({"A": "1"})]
    eur = import_rows(client, "eur.csv", eur_rows)
    s = define(client, "Rev")
    c = define(client, "Orders", aggregation="count")

    def investigate(l_ds, l_m, r_ds, r_m):
        return client.post(
            "/api/investigations",
            json={
                "title": "x",
                "left": {"dataset_id": l_ds, "metric_definition_id": l_m},
                "right": {"dataset_id": r_ds, "metric_definition_id": r_m},
            },
        )

    resp = investigate(usd, s["id"], eur, s["id"])
    assert resp.status_code == 422 and "different currencies" in resp.json()["detail"]
    resp = investigate(usd, s["id"], usd, c["id"])
    assert resp.status_code == 422 and "cannot compare" in resp.json()["detail"]
    strict = define(client, "Strict", null_policy="reject")
    nulls = import_rows(
        client, "nulls.csv", [["x", "A", "", "", "USD", "2026-03-10T10:00:00Z", "paid", "EU"]]
    )
    resp = investigate(nulls, strict["id"], usd, strict["id"])
    assert resp.status_code == 422 and "reject" in resp.json()["detail"]
    assert client.get("/api/investigations").json() == []  # nothing persisted for failed runs


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "aggregation": "median"},
        {"name": "", "aggregation": "sum"},
        {"name": "x", "aggregation": "sum", "timezone": "Mars/Base"},
        {
            "name": "x",
            "aggregation": "sum",
            "window_start": "2026-04-01T00:00:00",
            "window_end": "2026-03-01T00:00:00",
        },
        {"name": "x", "aggregation": "sum", "window_start": "2026-04-01T00:00:00+02:00"},
        {"name": "x", "aggregation": "sum", "filters": [{"field": "secret", "op": "eq", "value": "1"}]},
        {"name": "x", "aggregation": "sum", "currency": "dollars"},
    ],
)
def test_invalid_metric_definitions(client, body):
    assert client.post("/api/metric-definitions", json=body).status_code == 422


def test_unknown_ids(client):
    z = "00000000-0000-0000-0000-000000000000"
    metric = define(client, "Rev")
    ds = import_rows(client, "a.csv", rows_for({"A": "1"}))
    assert client.get(f"/api/metric-definitions/{z}").status_code == 404
    assert client.get(f"/api/investigations/{z}").status_code == 404
    assert client.get(f"/api/investigations/{z}/evidence.csv").status_code == 404
    body = {
        "title": "x",
        "left": {"dataset_id": z, "metric_definition_id": metric["id"]},
        "right": {"dataset_id": ds, "metric_definition_id": metric["id"]},
    }
    assert client.post("/api/investigations", json=body).status_code == 404
    inv = client.post(
        "/api/investigations", json={**body, "left": {"dataset_id": ds, "metric_definition_id": metric["id"]}}
    ).json()
    assert client.get(f"/api/investigations/{inv['id']}/findings/{z}/evidence").status_code == 404


D = Decimal


def test_injection_shaped_attribute_names_and_values_are_bound_not_spliced(client, db_session):
    evil_column = "region') OR 1=1; DROP TABLE investigations; --"
    data = f"id,amount,ccy,\"{evil_column}\"\nr1,10,USD,EU\nr2,20,USD,x' OR '1'='1\n".encode()
    up = client.post("/api/imports/file", files={"file": ("evil.csv", data)}).json()
    mapping = {
        "source_record_id": "id",
        "business_key": ["id"],
        "amount": "amount",
        "currency": "ccy",
        "attributes": [evil_column],
    }
    ds = client.post("/api/datasets", json={"upload_id": up["upload"]["id"], "mapping": mapping}).json()[
        "dataset"
    ]["id"]
    spec = MetricSpec(
        aggregation="sum",
        filters=[{"field": f"attributes.{evil_column}", "op": "eq", "value": "x' OR '1'='1"}],
    )
    value, _ = run_metric_sql(db_session, spec, ds)
    assert value == Decimal(20)  # matched the literal value only
    assert evaluate(load_records(db_session, ds), spec).value == Decimal(20)
    assert (
        db_session.execute(text("SELECT count(*) FROM investigations")).scalar() is not None
    )  # table intact
