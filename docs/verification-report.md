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

## Milestone 4 verification (2026-10-20)

Same environment as Milestone 3.

**Automated tests.** 230 passed, 0 failed, 0 skipped, 1 warning (the Starlette test-client deprecation notice). Same result on Python 3.14.6 (host) and 3.12.15 (container). The first 3.12 run failed one test that hardcoded `localhost` as the source host; the test now reads the host from its URL.

**Live checks.** The 72 live checks found one failure, a race: Docker had not yet marked the just-rebuilt backend "healthy". It was healthy when checked again. The checks ran against the compose stack, with the backend reaching `source-db` as `ts_reader`.

- **Connections and queries:** the connection test reports `ts_reader` with no problems, and the catalog shows the 6 readable tables. The 3 registered queries carry SQL hash, parameters, joins and grain. Six bad parameter sets were rejected with 422: missing, unknown, `2026-02-30`, `' OR '1'='1`, `); DROP TABLE orders; --`, and a list in place of a date. Preview is capped at 50 rows.
- **Snapshots:** the finance March snapshot has 2,882 rows, the same as an independent `ts_reader` read. The same query, parameters and data reuse the dataset (200, same id); different parameters create a new one. Provenance holds the SQL hash, content hash, executed_at, role and server version.
- **File vs query:** expected values came from an independent read plus the edits the script made to the file copy.

  | Comparison | Edits to the file copy | Result |
  |---|---|---|
  | CSV vs PostgreSQL | dropped 7 orders, changed 5 amounts, added 3 orders, duplicated 2 rows | delta 1,684.87 exact; extra 4,008.50, missing −1,049.06, duplicates −511.75, mismatch −762.82; unexplained 0 |
  | XLSX vs PostgreSQL, net of refunds | dropped 2 orders, changed 1 amount | delta 1,358.19 exact; mismatch −5.25 |
  | Unmodified XLSX copy vs PostgreSQL | none | zero delta, no findings |
  | PostgreSQL vs PostgreSQL | none | both totals equal independent SUMs |

- **Failure modes:**
  - A wrong password returned a sanitized message, and the old placeholder password is rejected.
  - Hosts 10.0.0.5, 169.254.169.254, metadata.google.internal and `db` were refused before any connection attempt.
  - Registered SQL with two statements (42601), a data-modifying CTE (0A000), `FOR UPDATE` (25006) or `set_config('transaction_read_only','off')` (25001) did not execute, and the source was unchanged.
  - A 300 ms statement timeout cancelled `pg_sleep(3)` after 0.31 s.
  - With `source-db` stopped, the connection test and snapshot returned 502 immediately and nothing was stored. App health was unaffected.
- **Source unchanged:** an md5 over every row of all 6 source tables was identical before and after all of the above.
- **Staleness:** a fresh snapshot is `current`. After an admin changed one order by +10.00 it became `source_changed`. Re-snapshotting created a new dataset, and comparing the old and new snapshots showed exactly one +10.00 mismatch, with the old snapshot's records untouched. After re-running the seed script the old snapshot was `current` again, and the source was byte-identical to the original.
- **Secrets:** no password appeared in any of the 43 API responses, in any container log, in git history or in tracked files. `.env` is git-ignored with mode 600.

**Read-only enforcement through privileges alone.** With the session switched to `default_transaction_read_only = off`, `ts_reader` attempted 14 writes or escalations. These 12 failed with 42501 (insufficient privilege): INSERT, UPDATE, DELETE, TRUNCATE, CREATE TABLE, CREATE TEMP TABLE, ALTER TABLE, DROP TABLE, CREATE SCHEMA, CREATE FUNCTION, SELECT FOR UPDATE, and COPY TO file. The other two did not error:

- `GRANT INSERT ON orders TO ts_reader` returns a warning and grants nothing. Afterwards the role still had only SELECT and write privileges on 0 tables.
- `ALTER ROLE ts_reader SET default_transaction_read_only = off` **succeeds**. PostgreSQL lets any role change its own session defaults. It grants no write access, as the 42501 results above show, and TruthSplit cannot issue it because its queries run in a read-only transaction. The setting was restored. See the limitations in `docs/security.md`.

**Credential rotation.** The local `.env` had been copied from `.env.example`, so the real database passwords were the committed placeholder strings. All three database passwords were rotated to random values with `ALTER ROLE` and written to `.env`. `scripts/generate_env.sh` now creates `.env` with random passwords for new setups.

## Milestone 5 verification (2026-10-21)

Same environment as Milestone 3.

**Automated tests.** 284 passed, 0 failed, 0 skipped, 1 warning (the Starlette test-client deprecation notice), on both Python 3.14.6 and 3.12.15. No end-to-end tests exist yet.

**Oracle independence.** `app/seed/shop.py` reproduced the seeded source row for row: 6,000 orders, 15,000 lines, 6,180 payments and 780 refunds all identical. The manifest is computed from it.

**Scenario A (fully explained).** Model, independent SQL over the source, and the engine all agree:

| | Expected (manifest) | Engine |
|---|---|---|
| Left (finance net) | 1,397,648.86 | 1,397,648.86 |
| Right (sales) | 1,553,646.25 | 1,553,646.25 |
| A1 refund treatment, confirmed | +102,360.14 | +102,360.14 |
| A2 join amplification, confirmed | +56,224.00 | +56,224.00 |
| A3 cutoff, finance-only, confirmed | −9,604.00 | −9,604.00 |
| A4 cutoff, sales-only, confirmed | +7,017.25 | +7,017.25 |
| Unexplained | 0.00 | 0.00 |

For each finding, the set of affected order numbers in its evidence equals the manifest's set.

**No double counting.** For every order with an attributed contribution, the sum of its evidence contributions across all findings equals its actual difference (sales gross × payment rows − finance net). This covers the 15 orders that belong to two causes.

**Scenario B (unexplained remainder).** The expected delta of 3,255.50 matches.

| Issue | Status | Expected | Engine |
|---|---|---|---|
| B1 missing orders | confirmed | −6,425.81 | −6,425.81 |
| B3 exact duplicates | confirmed | +3,166.56 | +3,166.56 |
| Record-level mismatch | confirmed | +5,518.00 (405.00 keying errors + 5,113.00 unattributed fan-out) | +5,518.00 |
| B4 join-like fan-out | suspected | 5,113.00 indicative, not attributed | no amount; indicative 5,113.00 |
| B5 unexplained | unexplained | 996.75 | 996.75 |
| Non-unique keys | suspected | 13 right-side keys | 13 right-side keys |

SQL over the stored records independently reproduced both totals, the missing amount, the exact-duplicate amount and the unkeyed sum.

**Persistence and reproducibility.** For each demo investigation, attributed findings sum to delta and each finding's evidence sums to its amount. Re-running an investigation on the same snapshots produced identical findings, statuses and proof hashes. Loading the demo twice reused every dataset, definition and investigation.

**Source unchanged.** An md5 over all rows of the six source tables was identical before and after loading the demo.

**Mutation checks** (the tests can fail):
- Making join amplification remove one copy too many caused the engine's sum-to-delta check to fail while loading, so 5 demo tests errored.
- Disabling the refund-identity rule failed its unit test. The demo scenarios don't exercise that rule.

**Live stack.** `make demo` against the compose stack produced the same totals and findings as the manifest. No passwords appeared in the backend logs, and all four services were healthy.

**Limitations found.** The refund-identity rule has unit tests only. Suspected join amplification previously wrote an indicative amount into evidence `contribution`; it now leaves the column empty (decision 26).
