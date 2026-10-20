import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_settings
from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from app.models import DatasetRecord, ImportIssue
from tests.fixtures import write_xls, write_xlsx

pytestmark = pytest.mark.db

CSV = b"order_id,amount,refund,currency,placed_at,status\nA1,100.00,,USD,2026-03-01T10:00:00Z,paid\nA2,25.5,5,USD,2026-03-02T10:00:00Z,refunded\nA3,oops,,USD,2026-03-03T10:00:00Z,paid\n"
MAPPING = {
    "source_record_id": "order_id",
    "business_key": ["order_id"],
    "amount": "amount",
    "refund_amount": "refund",
    "currency": "currency",
    "occurred_at": "placed_at",
    "status": "status",
}


@pytest.fixture
def client(db_session, tmp_path):
    settings = Settings(database_url="unused", storage_dir=tmp_path, max_upload_mb=1, max_rows=100)
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, name, data):
    return client.post("/api/imports/file", files={"file": (name, io.BytesIO(data))})


def test_csv_upload_preview_and_import(client, db_session):
    resp = upload(client, "orders.csv", CSV)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["already_uploaded"] is False
    preview = body["preview"]
    assert preview["header"] == ["order_id", "amount", "refund", "currency", "placed_at", "status"]
    assert preview["rows"][0] == ["A1", "100.00", "", "USD", "2026-03-01T10:00:00Z", "paid"]
    types = {c["name"]: c["inferred_type"] for c in preview["columns"]}
    assert types["amount"] == "text" and types["refund"] == "integer" and types["placed_at"] == "datetime"

    resp = client.post("/api/datasets", json={"upload_id": body["upload"]["id"], "mapping": MAPPING})
    assert resp.status_code == 201, resp.text
    dataset = resp.json()["dataset"]
    assert (dataset["row_count"], dataset["rejected_row_count"]) == (2, 1)

    records = client.get(f"/api/datasets/{dataset['id']}/records").json()
    assert records["total"] == 2
    assert [(r["row_number"], Decimal(r["amount"])) for r in records["items"]] == [
        (2, Decimal("100.00")),
        (3, Decimal("25.5")),
    ]
    stored = db_session.scalars(
        select(DatasetRecord.amount).where(DatasetRecord.dataset_id == dataset["id"])
    ).all()
    assert sorted(stored) == [Decimal("25.5000"), Decimal("100.0000")]  # exact numeric, no float

    issues = client.get(f"/api/datasets/{dataset['id']}/issues").json()
    assert issues["items"] == [
        {
            "row_number": 4,
            "column_name": "amount",
            "raw_value": "oops",
            "message": issues["items"][0]["message"],
        }
    ]


def test_upload_and_import_are_idempotent(client, db_session):
    first = upload(client, "orders.csv", CSV).json()
    again = upload(client, "renamed.csv", CSV)
    assert again.status_code == 200
    assert again.json()["already_uploaded"] is True
    assert again.json()["upload"]["id"] == first["upload"]["id"]

    payload = {"upload_id": first["upload"]["id"], "mapping": MAPPING}
    d1 = client.post("/api/datasets", json=payload)
    d2 = client.post("/api/datasets", json=payload)
    assert (d1.status_code, d2.status_code) == (201, 200)
    assert d1.json()["dataset"]["id"] == d2.json()["dataset"]["id"]
    count = db_session.scalar(
        select(func.count()).where(DatasetRecord.dataset_id == d1.json()["dataset"]["id"])
    )
    assert count == 2
    issues = db_session.scalar(
        select(func.count()).where(ImportIssue.dataset_id == d1.json()["dataset"]["id"])
    )
    assert issues == 1

    changed = client.post("/api/datasets", json={**payload, "mapping": {**MAPPING, "status": None}})
    assert changed.status_code == 201  # a different mapping is a different dataset


def test_xlsx_sheet_selection(client, tmp_path):
    path = write_xlsx(
        tmp_path / "book.xlsx",
        {"Notes": [["text"], ["hello"]], "Q1": [["id", "amt"], ["X", 1.25], ["Y", 2]]},
    )
    resp = upload(client, "book.xlsx", path.read_bytes())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["upload"]["sheet_names"] == ["Notes", "Q1"]
    assert body["preview"]["sheet"] == "Notes"

    preview = client.get(f"/api/imports/{body['upload']['id']}/preview", params={"sheet": "Q1"}).json()
    assert preview["rows"] == [["X", "1.25"], ["Y", "2"]]

    resp = client.post(
        "/api/datasets",
        json={
            "upload_id": body["upload"]["id"],
            "sheet": "Q1",
            "mapping": {"business_key": ["id"], "amount": "amt"},
        },
    )
    assert resp.json()["dataset"]["row_count"] == 2
    assert resp.json()["dataset"]["sheet_name"] == "Q1"

    missing = client.get(f"/api/imports/{body['upload']['id']}/preview", params={"sheet": "Q9"})
    assert missing.status_code == 422


def test_xls_upload(client, tmp_path):
    path = write_xls(tmp_path / "legacy.xls", [["id", "amt"], ["Z", 3.5]])
    body = upload(client, "legacy.xls", path.read_bytes())
    assert body.status_code == 201, body.text
    assert body.json()["preview"]["rows"] == [["Z", "3.5"]]


@pytest.mark.parametrize(
    ("name", "data", "status", "message"),
    [
        ("data.json", b'{"a": 1}', 415, "Unsupported file type"),
        ("fake.xlsx", b"id,amount\n1,2\n", 415, "not an Excel workbook"),
        ("bin.csv", b"\x00\x01\x02", 415, "binary"),
        ("empty.csv", b"", 422, "empty"),
        ("header.csv", b"id,amount\n", 422, "no data rows"),
        ("big.csv", b"id\n" + b"1\n" * 600_000, 413, "1 MB upload limit"),
    ],
)
def test_rejected_uploads(client, name, data, status, message):
    resp = upload(client, name, data)
    assert resp.status_code == status
    assert message in resp.json()["detail"]


def test_row_limit_on_import(client):
    data = b"id,amt\n" + b"".join(f"{i},1\n".encode() for i in range(101))
    body = upload(client, "rows.csv", data).json()
    assert body["preview"]["truncated"] is True
    resp = client.post(
        "/api/datasets",
        json={"upload_id": body["upload"]["id"], "mapping": {"business_key": ["id"], "amount": "amt"}},
    )
    assert resp.status_code == 422
    assert "more than 100 data rows" in resp.json()["detail"]


def test_mapping_errors(client):
    body = upload(client, "orders.csv", CSV).json()
    resp = client.post(
        "/api/datasets", json={"upload_id": body["upload"]["id"], "mapping": {"amount": "Amount"}}
    )
    assert resp.status_code == 422
    assert "not found in the file: Amount" in resp.json()["detail"]
    resp = client.post(
        "/api/datasets",
        json={"upload_id": body["upload"]["id"], "mapping": {"amount": "amount", "timezone": "Nowhere/X"}},
    )
    assert resp.status_code == 422


def test_unknown_ids_are_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/imports/{missing}/preview").status_code == 404
    assert client.get(f"/api/datasets/{missing}").status_code == 404
    assert client.post("/api/datasets", json={"upload_id": missing, "mapping": MAPPING}).status_code == 404


def test_path_components_are_dropped_from_filenames(client):
    body = upload(client, "../../etc/orders.csv", CSV).json()
    assert body["upload"]["original_filename"] == "orders.csv"


def test_workbook_whose_first_sheet_has_no_data_is_still_accepted(client, tmp_path):
    path = write_xlsx(
        tmp_path / "cover.xlsx", {"Cover": [["Prepared by finance"]], "Data": [["id", "amt"], ["A", 1]]}
    )
    resp = upload(client, "cover.xlsx", path.read_bytes())
    assert resp.status_code == 201
    body = resp.json()
    assert body["preview"] is None
    assert "no data rows" in body["preview_error"]
    preview = client.get(f"/api/imports/{body['upload']['id']}/preview", params={"sheet": "Data"})
    assert preview.status_code == 200 and preview.json()["rows"] == [["A", "1"]]


def test_wrong_encoding_keeps_upload_for_another_try(client):
    resp = upload(client, "latin.csv", "id,name\n1,Café\n".encode("cp1252"))
    assert resp.status_code == 201
    body = resp.json()
    assert body["preview"] is None and "not valid utf-8" in body["preview_error"]
    preview = client.get(f"/api/imports/{body['upload']['id']}/preview", params={"encoding": "cp1252"})
    assert preview.json()["rows"] == [["1", "Café"]]


def test_rejected_csv_is_not_stored(client, db_session, tmp_path):
    from app.models import FileUpload

    assert upload(client, "header.csv", b"id,amount\n").status_code == 422
    assert db_session.scalar(select(func.count()).select_from(FileUpload)) == 0
    assert list((tmp_path / "uploads").iterdir()) == []
