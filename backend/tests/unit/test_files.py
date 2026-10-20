import zipfile
from datetime import datetime

import pytest

from app.adapters.files import (
    IngestionError,
    UnsupportedFileError,
    check_xlsx_container,
    detect_file_type,
    list_sheets,
    read_table,
)
from app.models.sources import FileType
from tests.fixtures import write_csv, write_xls, write_xlsx

LIMIT = 1000


class TestDetectFileType:
    def test_by_extension_and_signature(self):
        assert detect_file_type("Report.CSV", b"id,amount\n") is FileType.CSV
        assert detect_file_type("r.xlsx", b"PK\x03\x04rest") is FileType.XLSX
        assert detect_file_type("r.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest") is FileType.XLS

    @pytest.mark.parametrize(
        ("name", "head"),
        [
            ("r.xlsx", b"id,amount\n"),  # CSV renamed to .xlsx
            ("r.csv", b"PK\x03\x04"),  # workbook renamed to .csv
            ("r.csv", b"id\x00,amount"),  # binary content
            ("r.xls", b"PK\x03\x04"),
        ],
    )
    def test_signature_must_match_extension(self, name, head):
        with pytest.raises(UnsupportedFileError):
            detect_file_type(name, head)

    @pytest.mark.parametrize("name", ["r.json", "r.xlsm", "r.exe", "noext"])
    def test_unsupported_extensions(self, name):
        with pytest.raises(UnsupportedFileError, match="Unsupported file type"):
            detect_file_type(name, b"anything")


class TestCsv:
    def test_reads_header_rows_and_row_numbers(self, tmp_path):
        path = write_csv(tmp_path / "a.csv", 'id,amount,note\n1,10.50,"a, b"\n\n2,3,"line\nbreak"\n')
        table = read_table(path, FileType.CSV, max_rows=LIMIT)
        assert table.header == ["id", "amount", "note"]
        assert table.rows == [["1", "10.50", "a, b"], ["2", "3", "line\nbreak"]]
        assert table.row_numbers == [2, 4]
        assert table.blank_rows_skipped == 1

    def test_header_may_follow_blank_rows(self, tmp_path):
        table = read_table(write_csv(tmp_path / "a.csv", "\n,\nid\n7\n"), FileType.CSV, max_rows=LIMIT)
        assert (table.header, table.row_numbers) == (["id"], [4])

    def test_strips_utf8_bom(self, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_bytes(b"\xef\xbb\xbfid,amount\n1,2\n")
        assert read_table(path, FileType.CSV, max_rows=LIMIT).header == ["id", "amount"]

    @pytest.mark.parametrize("delimiter", [";", "\t", "|"])
    def test_sniffs_delimiter(self, tmp_path, delimiter):
        text = f"id{delimiter}amount\n1{delimiter}2.5\n2{delimiter}3\n"
        table = read_table(write_csv(tmp_path / "d.csv", text), FileType.CSV, max_rows=LIMIT)
        assert table.rows == [["1", "2.5"], ["2", "3"]]

    def test_invalid_utf8_reports_encoding_problem(self, tmp_path):
        path = write_csv(tmp_path / "l1.csv", "id,name\n1,Café\n", encoding="cp1252")
        with pytest.raises(IngestionError, match="not valid utf-8"):
            read_table(path, FileType.CSV, max_rows=LIMIT)
        assert read_table(path, FileType.CSV, encoding="cp1252", max_rows=LIMIT).rows == [["1", "Café"]]

    def test_rejects_unknown_encoding(self, tmp_path):
        path = write_csv(tmp_path / "a.csv", "id\n1\n")
        with pytest.raises(IngestionError, match="Unsupported encoding"):
            read_table(path, FileType.CSV, encoding="utf-32", max_rows=LIMIT)

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "empty"),
            ("\n\n", "empty"),
            ("id,amount\n", "no data rows"),
            ("id,,amount\n1,2,3\n", "Column 2 has an empty header"),
            ("id,amount,id\n1,2,3\n", "Duplicate column header 'id'"),
            ('id,amount\n1,"unterminated\n', "Malformed CSV"),
        ],
    )
    def test_structural_errors(self, tmp_path, text, message):
        with pytest.raises(IngestionError, match=message):
            read_table(write_csv(tmp_path / "bad.csv", text), FileType.CSV, max_rows=LIMIT)

    def test_row_limit_is_an_error_not_a_truncation(self, tmp_path):
        path = write_csv(tmp_path / "big.csv", "id\n" + "".join(f"{i}\n" for i in range(11)))
        with pytest.raises(IngestionError, match="more than 10 data rows"):
            read_table(path, FileType.CSV, max_rows=10)
        preview = read_table(path, FileType.CSV, max_rows=10, preview=True)
        assert preview.truncated and len(preview.rows) == 10


class TestXlsx:
    def test_sheets_and_typed_cells(self, tmp_path):
        path = write_xlsx(
            tmp_path / "b.xlsx",
            {
                "Summary": [["note"], ["ignore me"]],
                "Orders": [["id", "amount", "when", None], ["A1", 19.99, datetime(2026, 3, 1, 9, 30), None]],
            },
        )
        check_xlsx_container(path)
        assert list_sheets(path, FileType.XLSX) == ["Summary", "Orders"]
        table = read_table(path, FileType.XLSX, sheet="Orders", max_rows=LIMIT)
        assert table.header == ["id", "amount", "when"]  # trailing empty header cell ignored
        assert table.rows[0][:3] == ["A1", 19.99, datetime(2026, 3, 1, 9, 30)]
        assert read_table(path, FileType.XLSX, max_rows=LIMIT).header == ["note"]  # first sheet by default

    def test_unknown_sheet(self, tmp_path):
        path = write_xlsx(tmp_path / "b.xlsx", {"Orders": [["id"], [1]]})
        with pytest.raises(IngestionError, match="Worksheet 'Nope' not found"):
            read_table(path, FileType.XLSX, sheet="Nope", max_rows=LIMIT)

    def test_rejects_zip_that_is_not_a_workbook(self, tmp_path):
        path = tmp_path / "fake.xlsx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "hi")
        with pytest.raises(IngestionError, match="does not contain a workbook"):
            check_xlsx_container(path)

    def test_rejects_macro_enabled_workbook(self, tmp_path):
        path = write_xlsx(tmp_path / "m.xlsx", {"S": [["id"], [1]]})
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("xl/vbaProject.bin", b"\x00")
        with pytest.raises(IngestionError, match="Macro-enabled"):
            check_xlsx_container(path)

    def test_corrupt_archive(self, tmp_path):
        path = tmp_path / "c.xlsx"
        path.write_bytes(b"PK\x03\x04garbage")
        with pytest.raises(IngestionError, match="corrupt"):
            check_xlsx_container(path)


class TestXls:
    def test_reads_values_and_dates(self, tmp_path):
        path = write_xls(tmp_path / "c.xls", [["id", "amount", "day"], ["X1", 12.5, datetime(2026, 1, 31)]])
        assert list_sheets(path, FileType.XLS) == ["Sheet1"]
        table = read_table(path, FileType.XLS, max_rows=LIMIT)
        assert table.header == ["id", "amount", "day"]
        assert table.rows == [["X1", 12.5, datetime(2026, 1, 31)]]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.csv", "passwd.csv"),
        ("C:\\Users\\me\\Q1.xlsx", "Q1.xlsx"),
        ("bell\x07\x00.csv", "bell.csv"),
        ("", "upload"),
        (None, "upload"),
        ("x" * 300 + ".csv", "x" * 255),
    ],
)
def test_sanitize_filename(raw, expected):
    from app.db.imports import sanitize_filename

    assert sanitize_filename(raw) == expected
