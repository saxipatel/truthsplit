# Architecture decisions

Each entry records a decision made while building TruthSplit, the context for it, and what it costs. Entries are added when a decision is made, not reconstructed afterwards.

---

## 1. Modular monolith

**Decision.** One FastAPI service, with modules for each stage (`adapters`, `normalization`, later `comparison`, `diagnosis`, `simulation`, `reporting`), plus one React app.

**Context.** The workload is interactive investigations on datasets of up to a few hundred thousand rows on one machine.

**Alternatives.** Separate ingestion and investigation services; a queue between them.

**Reason.** Nothing in the workload needs independent scaling or deployment, and a single process keeps transactions simple: an import or an investigation either fully commits or leaves nothing.

**Trade-offs.** Long imports block an API worker. With a 250k-row limit this has been a few seconds (see `docs/verification-report.md`).

**Consequences.** Module boundaries are kept by import discipline, not network calls. If imports ever need to run in the background, the import function can move behind a job table without changing its interface.

## 2. PostgreSQL for both metadata and the demo source, as separate instances

**Decision.** The app's metadata lives in one PostgreSQL instance. The "external" demo source is a second instance that the backend reaches only as a read-only role.

**Context.** The product must demonstrate reading an authorized source database without ever being able to modify it.

**Alternatives.** One instance with two databases; SQLite for metadata.

**Reason.** Separate instances make the trust boundary explicit: different credentials, and no shared superuser connection. PostgreSQL gives `numeric`, `timestamptz`, JSONB and `COPY`, which the reconciliation work relies on.

**Trade-offs.** A second container to run.

**Consequences.** The read-only guarantee is enforced by the database: no CREATE on the schema, no TEMP on the database, SELECT-only grants, and `default_transaction_read_only`. Application-side SQL checks are a second layer, not the guarantee. This is tested by attempting writes as the reader role, including after `SET TRANSACTION READ WRITE`.

## 3. Source credentials only in environment variables

**Decision.** Connection URLs for source databases come from environment profiles (`TRUTHSPLIT_SOURCE_<NAME>_URL`). The database stores only the profile name.

**Context.** The blueprint forbids plaintext credentials in the metadata database. A local-development tool needs a simple secret mechanism.

**Alternatives.** Encrypt credentials in the database with a key from the environment.

**Reason.** Encryption at rest would still need a key in the environment, and it would add key-rotation and leakage paths (backups, exports) for little benefit in a local tool.

**Trade-offs.** Adding a source means editing `.env` and restarting, not filling in a form.

**Consequences.** API responses and logs must never include a connection URL; the PostgreSQL source mode (Milestone 4) is tested for this. `.env` is git-ignored, and Compose refuses to start without it.

## 4. Metric definitions are independent of datasets

**Decision.** `metric_definitions` hold the logic (aggregation, filters, window, timezone, refund and null policies). `investigation_inputs` bind a definition run to a specific dataset snapshot.

**Context.** The same definition ("Finance net revenue v2") should be reusable against next month's export or a fresh query result.

**Alternatives.** A definition row per dataset.

**Reason.** It separates *what the metric means* from *which data it was computed on*. That is exactly the distinction investigations need to make when they separate definition differences from data differences.

**Trade-offs.** One more join to reconstruct a result.

**Consequences.** Definitions are immutable and versioned (`unique(name, version)`); an edit creates a new version. Query results will be snapshotted into `datasets` as well, so every investigation can be replayed from stored data.

## 5. Ingestion with the `csv` module, openpyxl and xlrd, not pandas

**Decision.** CSV is read with Python's `csv` module, XLSX with openpyxl (read-only mode), XLS with xlrd. Every cell is passed to strict parsers in `app/normalization/values.py`.

**Context.** Values must not be silently changed, money must be exact, and every record must trace back to a source row.

**Alternatives.** `pandas.read_csv` / `read_excel`.

**Reason.** pandas infers dtypes by default and turns amounts into float64 and blanks into NaN. Avoiding that means `dtype=str`, `keep_default_na=False` and per-column conversion anyway, which is what the custom parsers already do, with exact control over row numbers and error reporting. The standard library reader also gives precise malformed-quote errors.

**Trade-offs.** No vectorized parsing. Measured throughput is about 2 s per 100k CSV rows and 7 s per 100k XLSX rows, which is acceptable at the 250k row limit. pandas is listed in the original stack and remains an option for later analysis code, but it is not a dependency today.

**Consequences.** A parsing rule lives in one place and has a unit test. Changing one means bumping `NORMALIZER_VERSION`.

## 6. Reject invalid rows whole and record why; never coerce

**Decision.** A row with any invalid mapped value is left out entirely and logged in `import_issues` (row, column, raw value, reason). Valid rows are imported. `datasets.rejected_row_count` is always visible. Structural problems (bad header, malformed quoting, missing mapped columns, row limit exceeded) fail the whole import.

**Context.** Reconciliation results are only meaningful if every included value is exactly what the source said.

**Alternatives.** Fail the whole import on the first bad value; coerce bad values to null or zero.

**Reason.** Failing on the first bad value makes large real-world exports unusable over one bad cell. Coercion hides data quality problems that might be the very cause of a discrepancy.

**Trade-offs.** An imported dataset can be incomplete. Later milestones must surface rejected rows in investigations and must not present such a dataset as complete.

**Consequences.** Empty cells are null (unknown), not zero. Amounts with more than 4 decimal places, currency symbols or ambiguous separators are rejected, not rounded or guessed. Wall-clock times inside a DST gap or overlap are rejected rather than resolved by guessing.

## 7. Content-addressed uploads and fingerprinted datasets

**Decision.** Uploaded files are stored once per SHA-256 (`uploads/<sha256>.<ext>`). Datasets are unique on a fingerprint of upload hash + sheet + encoding + mapping + normalizer version.

**Context.** Imports must be idempotent and reproducible, and source files must stay byte-identical to what the user provided.

**Alternatives.** Store each upload under a random id; store files in PostgreSQL.

**Reason.** Hash addressing makes retries and repeated uploads harmless, and it proves the stored file is unchanged: its name is its checksum. Files stay on a volume rather than in the database to keep the database small. Normalized records do go in the database, because investigations query them.

**Trade-offs.** Deleting an upload needs care if several datasets reference it (`ON DELETE RESTRICT` on `datasets.upload_id`).

**Consequences.** CSV structure is validated before the file is stored, so a broken CSV is never kept. A CSV that only fails to decode is kept so another encoding can be tried. A workbook whose first sheet cannot be previewed (e.g. a cover sheet) is still accepted, and the problem is reported as `preview_error`.

## 8. Bulk load with COPY inside the import transaction

**Decision.** Normalized records are written with PostgreSQL `COPY` on the same connection and transaction as the dataset row.

**Context.** Imports can reach 250k rows, and a failure must not leave a partial dataset.

**Alternatives.** ORM `add_all`; batched `INSERT … VALUES`.

**Reason.** `COPY` is the standard bulk-load path and is much faster than per-row inserts. Sharing the transaction keeps the import atomic.

**Trade-offs.** It bypasses the ORM, so the column list and types are declared explicitly in `_copy_records`.

**Consequences.** Schema changes to `dataset_records` must update that function. The integration tests exercise it.

## 9. Spreadsheet floats converted via the shortest round-trip string

**Decision.** Numeric spreadsheet cells, which openpyxl and xlrd return as binary floats, are converted with `Decimal(repr(value))`.

**Context.** Excel stores numbers as IEEE doubles. `Decimal(19.99)` would be `19.989999999999998436805981327779591083526611328125`.

**Alternatives.** `Decimal(float)` (exposes binary noise); rounding to 2 places (loses legitimate 4-place values and assumes a currency).

**Reason.** `repr` yields the shortest string that round-trips to the same double, which is what the spreadsheet displays. Anything still beyond 4 decimal places is then rejected by the normal amount rule.

**Trade-offs.** A value whose stored double is not a short decimal (for example the result of floating-point arithmetic, `0.30000000000000004`) is rejected as having too many decimal places, even if the spreadsheet displays it rounded. This is visible and explained rather than silently rounded.

**Consequences.** CSV and XLSX copies of the same data normalize to identical records. This was verified on 100k rows.
