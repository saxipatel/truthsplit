# Normalized record contract and import rules

Every source, whether a CSV, a worksheet or (later) a registered PostgreSQL query, is converted into rows of one shape before any comparison happens. The comparison and diagnosis code only ever sees this shape.

## Record fields

| Field | Type | Notes |
|---|---|---|
| `row_number` | int | Row in the original file; the file's first line is row 1. For CSV this equals the line number unless quoted fields contain line breaks. |
| `source_record_id` | text | From the mapped column, or `row:<row_number>` when none is mapped. |
| `business_key` | text, nullable | Matching key. A single column is used as is; a composite key is stored as a JSON array (`["A-1","2"]`) so separator characters inside values cannot make two keys collide. Null if any key part is empty. |
| `occurred_at` | timestamptz (UTC), nullable | See *Timestamps*. |
| `amount` | numeric(18,4), nullable | See *Amounts*. |
| `refund_amount` | numeric(18,4), nullable | Must be zero or positive. |
| `currency` | ISO 4217 code, nullable | From a column, or a constant `default_currency`. |
| `status` | text, nullable | Free text, up to 64 characters. |
| `attributes` | JSON object | Extra mapped columns kept as text for filters. |
| `raw_payload_hash` | sha256 | Hash of the row's raw cell values in header order. |

**Empty is not zero.** An empty cell becomes null and is treated as unknown. A metric's null policy (Milestone 3) decides whether such rows are excluded or make the evaluation fail.

## Validation policy

- A row with any invalid mapped value is **rejected as a whole**. Each bad cell is recorded in `import_issues` with its row number, column and raw value. Rejected rows are never partially imported, and nothing is coerced.
- Valid rows are still imported. The dataset's `rejected_row_count` shows how many rows were left out, and investigations must surface it.
- Structural problems fail the whole import: unknown file type, a signature that doesn't match the extension, an empty file, a header with blank or duplicate names, malformed CSV quoting, mapped columns missing from the header, or more rows than `TRUTHSPLIT_MAX_ROWS`. Rows are never silently truncated.

### Amounts

Accepted: plain decimals with an optional sign and optional comma thousands separators in groups of three, e.g. `1234.5`, `-12`, `1,234,567.89`.

Rejected: currency symbols, parentheses for negatives, spaces, comma decimal separators (`12,34` is ambiguous), scientific notation, more than 4 decimal places (unless the extra digits are zeros), and values too large for numeric(18,4).

Spreadsheet numbers arrive as binary floats. They are converted using the shortest decimal string that round-trips (`19.99`, not `19.989999999999998`), so the value matches what the spreadsheet displayed. Arithmetic is done in `Decimal` and stored as PostgreSQL `numeric`. Floats are never used for totals.

### Timestamps

- ISO 8601 by default (`2026-03-31`, `2026-03-31 14:05`, `2026-03-31T14:05:00Z`, `…-04:00`), or an explicit `strptime` `date_format`.
- A value with a UTC offset keeps that offset. A value without one is read as wall-clock time in the mapping's `timezone`. A date alone means local midnight.
- Wall-clock times that fall in a DST gap (they never existed) or a DST overlap (they happened twice) are rejected rather than resolved by guessing.
- All timestamps are stored in UTC.

## Files

| Type | Detection | Parser | Notes |
|---|---|---|---|
| CSV | `.csv`, no binary bytes | Python `csv`, delimiter detected from `, ; tab \|` | Encodings: UTF-8 (BOM stripped), cp1252, latin-1. Ragged rows are rejected. |
| XLSX | `.xlsx`, zip signature, `xl/workbook.xml` present | openpyxl, read-only, cached values | XML parsed through defusedxml. Macro-enabled archives and archives that expand past 1 GB or 10,000 members are refused. Formulas contribute their cached value; a formula never calculated by Excel reads as empty. |
| XLS | `.xls`, OLE2 signature | xlrd | Date cells converted using the workbook's date system. |

The header is the first non-blank row. Blank rows are skipped and counted, and trailing empty header cells are ignored.

## Idempotency

- **Uploads** are stored once per SHA-256 of their bytes (`uploads/<sha256>.<ext>`). Uploading the same bytes again, under any filename, returns the existing upload.
- **Datasets** are keyed by a fingerprint of the upload hash, sheet, CSV encoding, column mapping and normalizer version. Repeating an import returns the existing dataset, and changing any of those inputs creates a new one.
- Records are bulk-loaded with `COPY` inside the same transaction as the dataset row, so a failed import leaves nothing behind.
