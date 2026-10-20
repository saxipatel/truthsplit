# Verification report

Measured results only. Each entry records what was run, on what, and the outcome.

## Import throughput (Milestone 2, 2026-10-20)

Single runs, not a benchmark. Measured end to end through the HTTP API of the Docker Compose stack (`curl` wall time).

Machine: Apple M4, 16 GB RAM, Docker Desktop. Data: synthetic, 100,000 rows × 8 columns, generated with a fixed seed. Mapping: composite business key (2 columns), amount, refund, currency, ISO timestamp, status, 1 attribute.

| File | Size | Upload + preview | Import (parse, validate, COPY) |
|---|---|---|---|
| CSV | 5.3 MB | 0.10 s | 2.02 s |
| XLSX | 3.5 MB | 3.47 s | 7.10 s |

- Importing the same CSV again returned the existing dataset in 0.01 s.
- The CSV and XLSX copies of the same data produced identical normalized records. The check was a row-by-row SQL comparison of key, amount, refund, timestamp, status and attributes: 0 rows differed, and totals matched (49,871,923.48).
- Backend container memory afterwards: 81 MiB. Peak memory during the import was not measured.

## Investigation engine (Milestone 3, 2026-10-20)

Same machine and setup. Two synthetic 100,000-row CSV datasets (fixed seed): the right side has about 1% of keys removed, about 1% with the amount changed by exactly +1.00, about 0.5% duplicated as identical rows, and 500 extra keys at 10.00 each.

- `POST /api/investigations` (load both datasets, evaluate, reconcile, store 3,052 evidence rows): **1.68 s**. Evidence CSV export (3,052 rows plus header): 0.04 s.
- Engine result: delta −300,664.04; missing −542,695.85 (1,085 keys); extra 5,000.00 (500 keys); duplicates 236,039.81 (475 keys); mismatch 992.00 (992 keys); unexplained 0.00. The categories sum exactly to delta.
- **Independent check:** a hand-written SQL query (anti-joins and `GROUP BY` over `dataset_records`, not using engine code) returned identical totals and category amounts.
- Before `ANALYZE`, that query ran for over 2 minutes and was cancelled. After `ANALYZE` it took 0.9 s. Imports now analyze the table; 100k-row CSV import time with it was 1.81 s.

## Milestone 3 verification (2026-10-20)

Environment: Apple M4 (10 cores), 16 GB RAM, macOS 26.6.2, Docker 29.7.2 (VM: 10 cores, 7.7 GiB), PostgreSQL 16.15, Python 3.12.15 in the backend container. Tests also run on Python 3.14.6 on the host.

**Automated tests.** 202 passed, 0 failed, 0 skipped, 1 warning (a Starlette deprecation notice about its test client), with the same result on 3.12 and 3.14. No end-to-end (Playwright) tests exist yet; they arrive with the UI.

**Live API checks.** 55 of 55 passed, against the compose stack. Expected values were computed by the test script at generation time or by brute force over the generated rows, never by engine code:

- **Ground truth:** 3,000 orders with composite keys (order, line), seed 20261020, including negative amounts. Missing, extra, 4-decimal-place mismatches, exact duplicates on both sides, and rows without a key were each introduced with their effect recorded. All five categories, both totals and delta (59,616.1022) matched exactly, and explained + unexplained = delta.
- **Sign convention:** swapping left and right negates delta and every category.
- **Composite keys:** (COMP, 1) and (COMP, 2) are not matched.
- **Independent SQL:** a hand-written query (not engine code) reproduced both totals and every category amount.
- **Definitions vs brute force:** 12 definitions over 600 generated rows matched a brute-force calculation, covering sum, count, count_distinct, filters (`ne`, `in` with nulls, numeric `lt` on negatives), UTC and New York March windows (spanning the DST change), an inclusive end bound, all three refund policies and business-key grain.
- **Persistence:** stored totals equal the API response. Inputs keep the dataset fingerprints. Each finding's evidence contributions sum to its amount, all evidence sums to delta, and the CSV export sums to delta. A later `GET` returns exactly what the create request returned.
- **Source unchanged:** an md5 over all `dataset_records` columns was identical before and after repeated investigations, and the fingerprints were unchanged. Re-running gave identical findings and proof hashes.

**Defects found by this verification and fixed:**

1. *Per-key evidence overflow (critical).* `evidence_records.left_amount/right_amount` were numeric(18,4). A key whose rows add up past 10^14 caused a 500 error and stored nothing. Migration `0002` widens both to numeric(24,4), and a regression check now stores 2 × 99,999,999,999,999.9999 exactly.
2. *Definitions using columns the dataset doesn't have.* Net-of-refunds over a dataset with no refund column silently equalled gross, a date window without timestamps excluded everything, and a filter on an unmapped attribute excluded everything. These now fail with 422 and name the missing columns.
3. *Inconsistent decimal formatting.* The create response showed `0` while a later `GET` showed `0.0000`. The response is now read back from the database.
4. *Alembic couldn't import the app from a plain checkout* (it depended on how the package was installed). Fixed with `prepend_sys_path` in `alembic.ini`.

**Security.**
- `bandit` reported 2 medium-severity, low-confidence warnings (B608, SQL built from strings) in `app/comparison/sql.py`. The interpolated parts are fixed fragments: column names now come from an allowlist inside that module, and all input values are bind parameters. Both lines are annotated with this reason. No other findings.
- An integration test runs the generated SQL against a dataset whose attribute column is named `region') OR 1=1; DROP TABLE investigations; --` and filters on the value `x' OR '1'='1`. Only the literal match is counted, and the table is intact.

**Performance, 100,000 vs 100,000 rows.** Same synthetic data as before: about 1% missing, about 1% changed by +1.00, about 0.5% exact duplicates, and 500 extra rows.

| Step | Time |
|---|---|
| Import left / right CSV (parse, validate, COPY, ANALYZE) | 2.05 s / 1.87 s |
| `POST /api/investigations`, runs 1–3 | 1.57 s, 1.51 s, 1.50 s |

The three runs produced identical findings and proof hashes, and the findings sum to delta (−300,664.04). Backend peak RSS (`VmHWM`) was 359 MiB, covering both imports and all three runs; RSS afterwards was 182 MiB.
