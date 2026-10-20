import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.adapters.files import IngestionError, RawTable
from app.normalization.mapping import ColumnMapping, normalize


def table(header, *rows, strict=True):
    return RawTable(
        header=header,
        rows=[list(r) for r in rows],
        row_numbers=list(range(2, len(rows) + 2)),
        strict_width=strict,
    )


ORDERS = ["Order ID", "Line", "Amount", "Refund", "Ccy", "Placed", "State", "Region"]


def test_maps_differently_named_columns_onto_the_contract():
    t = table(ORDERS, ["A-1", "1", "100.00", "20", "usd", "2026-03-31T22:00:00-04:00", "paid", "EU"])
    m = ColumnMapping(
        source_record_id="Order ID",
        business_key=["Order ID"],
        amount="Amount",
        refund_amount="Refund",
        currency="Ccy",
        occurred_at="Placed",
        status="State",
        attributes=["Region"],
    )
    result = normalize(t, m)
    assert result.issues == []
    (r,) = result.records
    assert (r.row_number, r.source_record_id, r.business_key) == (2, "A-1", "A-1")
    assert (r.amount, r.refund_amount, r.currency) == (Decimal("100.00"), Decimal("20"), "USD")
    assert r.occurred_at == datetime(2026, 4, 1, 2, 0, tzinfo=UTC)
    assert r.status == "paid" and r.attributes == {"Region": "EU"}
    assert len(r.raw_payload_hash) == 64


def test_composite_key_is_unambiguous():
    t = table(["a", "b", "amt"], ["x|y", "z", "1"], ["x", "y|z", "2"])
    result = normalize(t, ColumnMapping(business_key=["a", "b"], amount="amt"))
    keys = [r.business_key for r in result.records]
    assert keys == [json.dumps(["x|y", "z"]), json.dumps(["x", "y|z"])]
    assert keys[0] != keys[1]


def test_missing_key_part_gives_null_key_and_is_counted():
    t = table(["a", "b", "amt"], ["x", "", "1"], ["x", "y", "2"])
    result = normalize(t, ColumnMapping(business_key=["a", "b"], amount="amt"))
    assert [r.business_key for r in result.records] == [None, json.dumps(["x", "y"])]
    assert result.null_business_keys == 1


def test_invalid_values_reject_the_whole_row_with_one_issue_per_cell():
    t = table(
        ["id", "amt", "refund", "when"],
        ["1", "10.00", "", "2026-01-01"],
        ["2", "$10", "-1", "01/02/2026"],
        ["3", "", "", ""],
    )
    result = normalize(
        t, ColumnMapping(business_key=["id"], amount="amt", refund_amount="refund", occurred_at="when")
    )
    assert [r.business_key for r in result.records] == ["1", "3"]
    assert result.records[1].amount is None  # empty stays unknown
    assert result.rejected_rows == 1
    assert sorted((i.row_number, i.column_name, i.raw_value) for i in result.issues) == [
        (3, "amt", "$10"),
        (3, "refund", "-1"),
        (3, "when", "01/02/2026"),
    ]


def test_ragged_csv_rows_are_rejected():
    t = table(["id", "amt"], ["1", "5"], ["2"], ["3", "4", "extra"])
    result = normalize(t, ColumnMapping(business_key=["id"], amount="amt"))
    assert [r.business_key for r in result.records] == ["1"]
    assert {i.row_number for i in result.issues} == {3, 4}


def test_short_spreadsheet_rows_are_padded():
    t = table(["id", "amt", "note"], ["1", 5], strict=False)
    assert normalize(t, ColumnMapping(business_key=["id"], amount="amt")).records[0].amount == Decimal(5)


def test_default_currency_and_row_number_record_id():
    t = table(["id", "amt"], ["1", "5"])
    (r,) = normalize(t, ColumnMapping(business_key=["id"], amount="amt", default_currency="eur")).records
    assert (r.currency, r.source_record_id) == ("EUR", "row:2")


def test_mapped_columns_must_exist():
    with pytest.raises(IngestionError, match="not found in the file: Amount, Missing"):
        normalize(table(["id"], ["1"]), ColumnMapping(business_key=["id", "Missing"], amount="Amount"))


def test_identical_rows_get_identical_hashes():
    t = table(["id", "amt"], ["1", "5"], ["1", "5"], ["1", "6"])
    hashes = [
        r.raw_payload_hash for r in normalize(t, ColumnMapping(business_key=["id"], amount="amt")).records
    ]
    assert hashes[0] == hashes[1] != hashes[2]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"amount": "a", "timezone": "Mars/Base"}, "unknown time zone"),
        ({"amount": "a", "currency": "c", "default_currency": "USD"}, "not both"),
        ({"business_key": ["a", "a"]}, "distinct"),
        ({}, "at least"),
        ({"amount": "a", "default_currency": "dollars"}, "ISO 4217"),
    ],
)
def test_mapping_validation(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        ColumnMapping(**kwargs)
