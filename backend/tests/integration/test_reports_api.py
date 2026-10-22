import json
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings
from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from app.seed.demo import manifest

SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not SOURCE_URL, reason="TRUTHSPLIT_TEST_SOURCE_URL is not set"),
]


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUTHSPLIT_SOURCE_DEMO_URL", SOURCE_URL)
    settings = Settings(
        database_url="unused",
        storage_dir=tmp_path,
        allowed_source_hosts="localhost,127.0.0.1,host.docker.internal",
    )
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_demo_endpoint_is_idempotent(client):
    first = client.post("/api/demo")
    assert first.status_code == 200 and set(first.json()) == {"A", "B", "C"}
    assert client.post("/api/demo").json() == first.json()


def test_json_report_contains_accurate_stored_results(client):
    ids = client.post("/api/demo").json()
    report = client.get(f"/api/investigations/{ids['B']['investigation_id']}/export").json()
    expected = next(s for s in manifest()["scenarios"] if s["id"] == "B")["expected"]
    assert Decimal(report["totals"]["delta"]) == Decimal(expected["delta"])
    assert Decimal(report["totals"]["unexplained"]) == Decimal(expected["unexplained"])
    assert report["synthetic_demo_data"] is True
    statuses = {f["evidence_status"] for f in report["findings"]}
    assert statuses == {"confirmed", "suspected", "unexplained"}
    attributed = sum(
        Decimal(f["amount_impact"]) for f in report["findings"] if f["amount_impact"] is not None
    )
    assert attributed == Decimal(report["totals"]["delta"])
    assert all(len(i["fingerprint"]) == 64 for i in report["inputs"])
    assert {s["rule_id"] for s in report["recommended_next_steps"]} <= {
        f["rule_id"] for f in report["findings"]
    }
    assert report["history"][0]["action"] == "investigation.created"


def test_html_report_is_escaped_and_scriptless(client):
    ids = client.post("/api/demo").json()
    inv = ids["A"]["investigation_id"]
    client.post(
        f"/api/investigations/{inv}/notes",
        json={"decision": "comment", "note": "<script>alert(1)</script> & <b>bold</b>"},
    )
    resp = client.get(f"/api/investigations/{inv}/export", params={"format": "html", "download": True})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["content-disposition"].endswith('.html"')
    body = resp.text
    assert "<script" not in body.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in body
    assert "synthetic demo data" in body and "Confirmed findings" in body and "102360.1400" in body


def test_report_errors(client):
    assert client.get("/api/investigations/00000000-0000-0000-0000-000000000000/export").status_code == 404
    ids = client.post("/api/demo").json()
    assert (
        client.get(
            f"/api/investigations/{ids['A']['investigation_id']}/export", params={"format": "pdf"}
        ).status_code
        == 422
    )


def test_settings_never_expose_credentials(client):
    body = client.get("/api/settings").json()
    assert body["source_connections"] == ["demo"] and body["max_rows"] > 0
    text = json.dumps(body)
    assert "postgresql" not in text and SOURCE_URL.split("@")[0].split(":")[-1] not in text
