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

## 10. Python reference evaluator, with SQL as an independent proof

**Decision.** Metric definitions are evaluated in Python over in-memory records (`app/comparison/metrics.py`). Each is also rendered as one SQL query over `dataset_records` (`app/comparison/sql.py`), which is stored with the investigation and tested to return the same value.

**Context.** Findings need per-record contributions as evidence, and every number must be reproducible by someone who doesn't trust the engine.

**Alternatives.** Only SQL (aggregate in PostgreSQL); only Python.

**Reason.** The Python evaluator keeps, for each record, the value it contributes or the reason it was excluded, which the per-key reconciliation and the evidence need. The SQL form is what a reviewer can run, and agreement between two independent implementations is a meaningful test. It is checked on 18 definitions covering every filter operator, refund policy, grain, window, timezone and aggregation.

**Trade-offs.** Two implementations of the same rules must change together; the parity test fails if they drift. Investigations load both datasets into memory (bounded by the 250k row limit).

**Consequences.** Filter semantics follow SQL: a comparison against a null field is never true, including `ne` and `not_in`.

## 11. Exact partition of the difference by business key, in a fixed order

**Decision.** With delta = right − left, contributions are grouped by business key and each key's difference is assigned, in order, to: extra (key only on the right), missing (only on the left), exact duplicates (identical raw rows beyond the first, on matched keys), and amount mismatch (the rest of a matched key). Rows without a key form a separate, unexplained part.

**Context.** The sum of explanations must equal the observed difference, with nothing counted twice.

**Alternatives.** Compute each check independently and report the overlaps; pair rows within a key one to one.

**Reason.** Since each metric equals the sum of its contributions, grouping by key splits delta exactly in `Decimal` arithmetic. The engine asserts `Σ categories == delta` on every run. Pairing rows within a key would have to guess which row corresponds to which, so totals are compared per key instead.

**Trade-offs.** The order matters: a missing key that was also duplicated is reported only as missing. Repeated keys that aren't byte-identical (several order lines, or join fan-out) fall into amount mismatch at this level. The Difference Autopsy is responsible for recognising join amplification.

**Consequences.** These are data-level findings. They say which records differ, not why. A missing key's evidence records whether the key is absent from the other dataset or present but excluded by that side's definition (`other_side: excluded:filtered`), so definition differences aren't reported as lost data.

## 12. Evidence status of data-level findings

**Decision.** Missing, extra, duplicate and mismatch findings are `confirmed` because their amounts are recomputed exactly from listed records. Non-unique keys are `suspected`, with no amount. Rows without a business key are `unexplained`.

**Context.** The blueprint reserves "confirmed" for reproducible proof, and forbids invented confidence scores.

**Reason.** What these findings claim (these records differ by this much) is fully proven. Each finding's `classification_reason` states that the cause is not established, so "confirmed" never overstates the claim.

**Consequences.** `explained_delta + unexplained_delta = baseline_delta` always. Root-cause findings come later and add their own rules.

## 13. ANALYZE after each import

**Decision.** `ANALYZE dataset_records` runs after every dataset import commits.

**Context.** A cross-check query joining two freshly COPY-loaded 100k-row datasets ran for over 2 minutes with stale statistics, and in 0.9 s after `ANALYZE`.

**Alternatives.** Rely on autovacuum (it runs, but not necessarily before the next query); analyze only before investigations.

**Reason.** Bulk loads are exactly when statistics go stale, and the import is the one place that knows a large change just happened. The measured import time was unchanged, at about 1.8 s per 100k CSV rows.

**Trade-offs.** It analyzes the whole table, not just the new dataset. On a much larger table it would be worth sampling or moving off the request path.

## 14. Refuse definitions that read columns the dataset doesn't map

**Decision.** Before evaluating, each side's definition is checked against its dataset's column mapping (`unmapped_fields`). A definition that needs an unmapped column fails with a message naming the column. That covers a sum's value column, a refund column for any non-gross refund policy, a timestamp for a date window, a business key for key grain or count_distinct, and any filtered field.

**Context.** Found during Milestone 3 verification. Unmapped columns are null in every record, and the evaluator's null semantics then produce plausible but wrong numbers: net-of-refunds equals gross, and a filtered or windowed metric is zero.

**Alternatives.** Evaluate anyway and add a warning to the investigation.

**Reason.** A warning beside a wrong total is easy to miss, and the total would flow into findings and reports. Refusing is cheap, and the fix (map the column, or use a different definition) is clear.

**Consequences.** A shared definition can't be applied to a dataset that lacks one of its columns. A definition variant without that dependency has to be created explicitly, which makes the difference in Metric DNA visible.

## 15. Per-key evidence amounts use the total type

**Decision.** `evidence_records.left_amount` and `right_amount` are numeric(24,4), like investigation totals (migration `0002`).

**Context.** They hold per-key totals, not single record values, so a key with several large rows overflowed numeric(18,4), as found in verification.

**Consequences.** Record-level columns stay numeric(18,4), matching the normalized record contract. Every column that holds a sum is numeric(24,4).

## 16. Registered queries only, not arbitrary SELECT

**Decision.** Source databases are read only through queries registered in code. Each has typed parameters, a column mapping, and metadata: tables, joins with cardinality, and output grain.

**Context.** The original brief allowed "controlled read-only query execution"; the blueprint says "no user-provided arbitrary SQL execution". A query starting with SELECT is not safe by construction: functions, locking clauses and data-modifying CTEs can all hide behind it.

**Alternatives.** Accept user SQL and validate it with a parser such as sqlglot.

**Reason.** A parser-based allowlist is hard to make complete (functions with side effects, dialect corners), and the blueprint chose the stricter option. Registered queries also give Metric DNA real metadata about joins and grain, rather than metadata inferred from arbitrary SQL.

**Trade-offs.** Adding a query is a code change and a review, not a form.

**Consequences.** Users choose a query and parameter values. The SQL text and its hash are shown, and stored with every snapshot.

## 17. Check the role's privileges before every query

**Decision.** Each source query first inspects the login role (`pg_roles`, `has_*_privilege`), and refuses to run if the role could write or escalate.

**Context.** Read-only transactions stop writes but not everything: a superuser can turn them off, `COPY ... TO PROGRAM` needs only superuser, and a mis-configured profile is an easy mistake.

**Alternatives.** Check only in the connection test; trust the operator.

**Reason.** The check is one small query per run, and it turns a configuration mistake into a clear error instead of a latent risk.

**Trade-offs.** Roles that hold write grants they never use are refused. That is deliberate.

**Consequences.** The compose superuser is refused in tests, and only a dedicated SELECT-only login works.

## 18. Snapshot query results into datasets

**Decision.** Running a registered query stores its result as a `datasets` row with normalized `dataset_records`, plus provenance: source connection, query key, parameters, the hash of the SQL plus parameters, the content hash, executed_at, server version and role.

**Context.** Investigations must be reproducible later, after the source data has changed.

**Alternatives.** Re-run the query whenever an investigation is opened.

**Reason.** A live query can return different data tomorrow, which would silently change stored findings. Snapshots also let file and query inputs share the whole comparison engine.

**Trade-offs.** Storage grows with each distinct result. Unchanged results are deduplicated by fingerprint (query + parameters + content).

**Consequences.** Results are ordered by the entire row (`ORDER BY registered_query`), so row numbers in a snapshot are stable for the same data. Query values go through the same strict normalizers as file cells.

## 19. Upstream failures are 502, limits are 422

**Decision.** Connection failures, refusals and query errors from a source return 502 with a sanitized message. Exceeding the row limit returns 422.

**Reason.** The first group is a fault of the upstream system or its configuration. A row limit is a property of the request (choose a narrower window).

## 20. Snapshots are immutable; staleness is checked, not hidden

**Decision.** A snapshot never changes after it is taken. `POST /api/datasets/{id}/freshness` re-runs the snapshot's registered query with its stored parameters and compares a hash of the fully sorted result. It reports `current`, `source_changed`, `query_changed` (the registered SQL's hash no longer matches, so the query is not re-run), or `query_removed`.

**Context.** Investigations must stay reproducible, yet users need to know when a snapshot no longer reflects the source.

**Alternatives.** Refresh snapshots in place; refresh on a schedule; re-query every time an investigation is opened.

**Reason.** Refreshing in place would silently change stored findings. An explicit check makes staleness visible, and keeps the decision to re-run with the user.

**Trade-offs.** A freshness check costs a full query run. Nothing warns automatically; the check must be requested. The planned UI will show the snapshot time and offer the check.

**Consequences.** Re-snapshotting changed data creates a new dataset. Comparing the old and new snapshots is itself an investigation, and verification used one to show a single +10.00 source edit.

## 21. Generated local credentials

**Decision.** `scripts/generate_env.sh` creates `.env` with random passwords. `.env.example` keeps obvious placeholders, and the README points to the script.

**Context.** Found during Milestone 4 verification: copying `.env.example` made the committed placeholders the real passwords.

**Consequences.** Passwords take effect when the database volumes are first created. Existing volumes need `ALTER ROLE` as well, which is what was done for this machine's stack.
