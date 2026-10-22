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

## Milestone 5 follow-up: refund identity (2026-10-21)

**Investigation.** The reported "failure" of the refund-identity unit test came from the deliberate mutation check in the Milestone 5 report: the rule was disabled on purpose and the test correctly failed. The committed code passes all four refund tests, and the implementation, the test expectations and the rule's assumptions were reviewed. The rule was kept (decision 27). The real gap was demo coverage.

**Added.**
- Demo scenario C (`settlement_march.csv`).
- Six boundary unit tests: a non-exact difference stays a mismatch; the identity is tested after join copies are removed; exact duplicates and a netted refund on one key; double deduction; refund on either side and either sign; a zero refund never matches.
- A per-key no-double-counting test for all three scenarios, computing each key's actual difference in SQL over the stored records.

**Results.**
- **Tests:** 294 passed, 0 failed, 0 skipped, 1 warning.
- **Scenario C:** the engine matches the manifest (delta −159,249.32; C1 −56,224.00, C2 −100,479.19, C3 −2,546.13; unexplained 0.00). The affected order sets match per finding. A SQL re-implementation of the rule over the stored records returns the same three amounts.
- **No double counting:** in A, B and C, every key's attributed evidence sums to its actual difference, including the 12 overlapping orders in C.
- **Mutation check repeated:** disabling the rule now fails 6 tests, including `test_scenario_c_engine_matches_manifest`. The code was restored and is identical to the commit.
- **Live stack:** `make demo` loaded scenario C with the same totals and findings as the manifest.

## Milestone 6 verification (2026-10-21)

Same environment as Milestone 3.

**Automated tests.** 312 passed, 0 failed, 0 skipped, 1 warning (the Starlette test-client deprecation notice), on Python 3.14.6 and 3.12.15. No end-to-end tests exist yet. `bandit` reports 0 issues; this follows two fixes made during verification: a `nosec` annotation the formatter had moved, and three generator `assert`s replaced with explicit errors.

**Mutation checks.** Computing `resolved_amount` as a signed difference failed 2 unit tests. Letting a simulation modify its input records in place failed 2 (`test_inputs_are_never_modified`, `test_simulations_are_deterministic`). The code was restored after each.

**Live checks.** All 30 passed, against the compose stack with the three demo investigations. The first run had 1 failure, and a second run exposed a check error:

- *Defect, fixed:* investigations created before Milestone 6 had no `investigation.created` audit event. Migration `0004` backfills it, marked `backfilled: true`.
- *Check error, fixed in the script:* the B similarity check assumed only demo investigations existed. The verification's own 100k investigation correctly scored 10 against B (3 shared confirmed rules + 1 suspected), so the check now compares demo investigations only.

Simulation results against the manifest:

| Simulation | Expected after-delta | Result | Affected rows |
|---|---|---|---|
| A: align left definition to right | 155,997.39 − 102,360.14 = 53,637.25 | exact | 393 (one finance row per refunded order) |
| A: left refund policy → gross | 53,637.25 | exact | 393 |
| A: remove join copies, right | 155,997.39 − 56,224.00 = 99,773.39 | exact | 244 |
| B: remove exact duplicates, right | 3,255.50 − 3,166.56 = 88.94 | exact | 6 |
| B: remove join copies, right (the suspected fan-out) | 3,255.50 − 5,113.00 = −1,857.50 | exact | 7 |
| C: remove join copies, left | −159,249.32 + 56,224.00 = −103,025.32 | exact | 244 |
| C: left refund policy → net (no refund column) | 422 | 422, naming `refund_amount` | |
| B: align window (neither side has one) | no change | no change | 0 |

- **Repeatability:** repeating a simulation returned an identical result.
- **Nothing modified:** the md5 over investigations, findings, evidence, datasets and records, and the md5 over the source tables, were unchanged after all simulations and workflow actions.
- **Workflow:** resolving without a note → 422; open → in_review → resolved (with note) → open worked; resolved → in_review → 422. Accepting the unexplained finding left its evidence status `unexplained`. History listed every event in order.
- **Similarity among demo investigations:** A ranks C (5), then B (4); B ranks A (4), then C (3). Every point has a reason.
- **Patterns:** join amplification recurs in 3 investigations (2 confirmed, 1 suspected).
- **Secrets:** no password in 31 API responses or in the logs.

**Performance, 100k × 100k** (same synthetic data as Milestone 3). Investigation 1.70 s; simulation (`remove_duplicates`, right) 2.29 s, removing exactly the 475 duplicate rows' amount.

## Milestone 6 final verification (2026-10-21)

**Automated tests.** 324 passed, 0 failed, 0 skipped, 1 warning (Starlette test-client deprecation), on Python 3.14.6 and 3.12.15. `alembic check` finds no drift, the head is `0004`, and `bandit` reports 0 issues.

**All five corrections on demo data** (`tests/integration/test_simulations_demo.py`). Expected before and after values for left, right and delta are computed from the pure-Python source model, not the engine. Every value matched exactly:

| Correction | Demo case | Expected after-delta (model) | Affected rows |
|---|---|---|---|
| `remove_duplicates`, join copies | A, right | sales one copy per order − finance net | 244 (one dropped copy per split order) |
| `remove_duplicates`, exact | B, right | ledger total − net of the 6 duplicated orders − finance net | 6 |
| `refund_policy` → gross | A, left | sales rows − finance gross | 393 (refunded finance orders) |
| `align_definition` | A, left | same as above | 393 |
| `adjust_mapping` net → `gross_amount` | C, right | −56,224.00, i.e. only C1's join copies remain | 399 (384 refunded + 15 keying errors) |
| `align_window` NY → UTC | settlement export compared with itself | 0, from Σ settled amount of orders before 05:00 UTC on 1 March | the orders in that gap |

`resolved_amount = |before| − |after|` and `delta_change = after − before` were checked on every simulation.

**Nothing modified.** Uploaded files on disk still hash to their recorded SHA-256. The md5 over investigations, findings, evidence, datasets and records, and the md5 over the source tables, were identical after every simulation.

**Persistence, repeats and errors.**
- A repeated simulation returns an identical result under a new id, `GET /api/simulations/{id}` returns what was stored, and each run adds a `simulation.run` audit event.
- Seven invalid corrections return 422 and store nothing: an unknown kind or policy, a missing side, `adjust_mapping` on a query snapshot or to a missing column, a refund policy needing an unmapped column, and a mapping that rejects every row.

**Defect found and fixed.** `adjust_mapping` to a column whose values all fail validation (amount → `processor_ref`) previously returned 201 with right = 0. It now returns 422 ("the new mapping rejects every row…"). A partially rejected remap is accepted and reports `rows_read`, `rows_accepted`, `rows_rejected` and the first issues in `result_json.remap`; a test covers 3 rows read, 2 accepted, 1 rejected.

**Workflow and notes.** Status transitions, notes required to resolve, and accepting and rejecting a finding (the finding is unchanged, the audit event names the actor, decision and rule) all work, with complete ordered history. Blank notes and findings from other investigations return 422.

**Similarity.** Point scores with listed reasons. Among the demo investigations, A ranks C (5) then B (4); B ranks A (4) then C (3); C ranks A (5) then B (3). An accepted diagnosis appears as a suggestion and never changes another investigation.

**Refund identity (Milestone 5 follow-up).** Still supported (decision 27), with passing regression tests: four `TestRefundPolicy` tests, six boundary tests, and the scenario C manifest and SQL tests.

**Live stack.** All 31 live checks passed. Services healthy, migrations at `0004 (head)`, no password in 32 API responses or the logs.

**Remaining limitations.**
- Simulations recompute both the baseline and the simulated autopsy, taking about 2.3 s on 100k × 100k rows, and results are not cached.
- `adjust_mapping` is limited to file datasets.
- Similarity weights are fixed judgement calls.
- Audit actors are free-text names, because there is no authentication.

## Milestone 7: frontend (2026-10-21)

**Automated tests.**
- Backend: 329 passed, 0 failed, 0 skipped, 1 warning (Starlette test-client deprecation). This includes 5 new report, settings and demo-endpoint tests.
- End-to-end (`make e2e`, Playwright 1.49.1, Chromium, against the compose stack): 11 passed, 0 failed.
- Frontend type check and production build: pass. The largest chunk is the lazily loaded chart bundle (388 kB, 107 kB gzipped); the initial bundle is 187 kB (61 kB gzipped).

**What the end-to-end tests exercise.**
- **Every page:** the seven pages render their heading with no console errors and no error alerts. At 375 × 812, every page and all five investigation tabs have no horizontal overflow.
- **File investigation:**
  1. Submitting empty shows the required-field messages.
  2. Two CSVs are uploaded and previewed, the key column is guessed, and both import.
  3. A definition is created inline; with the same definition on both sides, Metric DNA reports them identical.
  4. The run shows a difference of +30.00, as the fixture implies.
  5. The missing-keys finding expands to evidence naming K5.
  6. Removing the exact duplicates shows a difference after of +10.00 and a gap closed of +20.00.
  7. After a reload the stored difference is still +30.00 and the simulation is listed.
  8. The HTML report downloads, and the JSON report has `delta` 30.0000 and one simulation.
- **Database mode:** finance vs sales query snapshots for March show +155,997.39 (the manifest value) with an unexplained remainder of 0.00.
- **Review:** a note persists across a reload.

**Manual checks in the browser** (desktop, and a 375px viewport).
- Demo A's breakdown shows +102,360.14, +56,224.00, −9,604.00 and +7,017.25.
- A UI simulation (remove join copies, right side) on demo A showed 155,997.39 → 99,773.39, a gap closed of 56,224.00 and 244 rows, matching the manifest. The per-rule comparison table summed A's two cutoff findings to −2,586.75.

**Defects found and fixed during this milestone.**
- **Wrong totals in the What-if table:** two findings from the same rule were summed only when their amounts differed. Fixed with exact decimal addition.
- **Ambiguous chart labels:** the two cutoff bars on demo A were both labelled "Query window"; they now use each finding's title.
- **Overlapping labels at 375px:** a negative bar's label overlapped the axis labels and a positive one was clipped. Labels now sit at each bar's data end, padded axes use clean ticks, and narrow screens drop in-chart labels in favour of the table.
- **Misleading legend:** "Unexplained remainder" appeared in the legend when there was none; legends now list only categories present.
- **Large bundle:** the build warned about a 644 kB bundle; pages are now lazily loaded.
- **Ambiguous stat tiles:** stat tiles had no accessible name, which also made them ambiguous for the tests; they are now labelled groups.

**Limitations.**
- No dark theme.
- The compose stack serves the Vite dev server, not a production build.
- The blueprint's "Simulations" and "Reports" are tabs inside an investigation rather than top-level pages, because both belong to one investigation.
- No README screenshots yet (Milestone 8).
- No CI workflow yet (Milestone 8).

## Milestone 7: results-experience pass (2026-10-21)

**Scope.** Reconciliation waterfall, evidence breakdown by status, before/after chart of saved simulations, Overview KPIs and recurring discrepancies, plain-language Metric DNA differences, dataset profiling, and separate evidence and review status. One backend addition: `GET /api/datasets/{id}/profile`.

**Automated tests.**
- Backend: 330 passed, 0 failed, 0 skipped, 1 warning (Starlette test-client deprecation), including a new profile test whose expected values were derived by hand from a fixture with duplicates, a negative amount, a zero, a missing amount, a row without a key and a rejected value.
- Frontend: type check and production build pass.
- End-to-end: 20 passed, 0 failed, 1 skipped (the screenshot capture, which runs only via `make screenshots`). The suite was run twice in a row with the same result.

**Charts against independently calculated values.** Expected values come from `sample_data/manifest.json`, from the settlement CSV parsed in the test, or from the API, never from frontend code.

- **Waterfall, demos A/B/C:** every step amount equals the manifest's contribution (for B, the mismatch row equals keying errors + unattributed fan-out, 5,518.00). Each running total equals the previous one plus the change (BigInt), and the last equals the manifest difference: +155,997.39, +3,255.50 and −159,249.32. Demo B by hand: 0 − 6,425.81 + 3,166.56 + 5,518.00 + 996.75 = +3,255.50.
- **Evidence breakdown, demo B:** confirmed +2,258.75 (= difference − unexplained), suspected "not attributed", unexplained +996.75.
- **Before/after chart:** one row per saved simulation, and the last row's values equal the API's.
- **Dataset profile:** rows 2,882, amount sum 1,394,396.93, 394 refunded rows, 119 zero amounts and the first timestamp all match the settlement CSV.
- **Overview KPIs:** fully explained, with remainder and formally resolved equal counts computed from the API list. Finding counts were confirmed in the browser: 10 confirmed, 2 suspected, 1 unexplained.

**Edge cases seen in the browser.**
- Demo C has only negative steps; the bars run leftward and the labels sit on the outer side.
- Demo B has a positive remainder after a negative first step.
- A simulation that widens the gap (+155,997.39 → +260,607.00) shows a negative "gap closed" in red.
- A simulation with no change (align window) is drawn as a single point.
- A simulation needing an unmapped column was refused and does not appear in the chart.
- An investigation id that no longer exists shows "Investigation not found".

**Mutation check.** Removing the unexplained-remainder step from the waterfall failed the demo B waterfall test only (A and C have no remainder). The code was restored and is identical to the source.

**Defects found during this pass and fixed.**
- Dataset details rendered inside a table cell pushed half the profile off-screen; they now render below the table.
- The profile's time range was labelled UTC but shown in local time; it is now formatted in UTC.
- "Filters differs" became "Filters differ".
- Findings with no record-level evidence showed "0 rows"; the count is now omitted.
- The Overview's "Latest" and "Unresolved" tables duplicated each other; it now shows "Recently resolved or archived" only when there are some.
- `npm ci` on the macOS bind mount left duplicate `… 2` directories and failed every second run; `make e2e`, `make screenshots` and `make frontend-check` keep `node_modules` inside the container.

**Measured.** Profiling a 100,000-row dataset took 0.81 s, then 0.40 s and 0.34 s (Apple M4, Docker).

**Screenshots.** `docs/screenshots/01-10`, captured by Playwright from the running stack after resetting to the three demo investigations, two example metric definitions and three saved simulations on demo A.

**Limitations.**
- No dark theme.
- On screens under 640px the in-chart value labels are hidden; the tables next to the charts carry the values.
- The compose stack serves the Vite dev server.

## Milestone 8: release verification (2026-10-22)

**Fresh environment.** Commit `b9efa8e` was cloned into an empty directory and run as a separate Compose project (`truthsplit_fresh`). It had new volumes and new `.env` passwords from `scripts/generate_env.sh`, and the images were built with `docker compose build --no-cache`. Every check below ran against that clone; the original stack was stopped with its volumes kept.

| Check | Result |
|---|---|
| `docker compose up -d --wait` | db, source-db, backend and frontend healthy; `/health/ready` reports database ok, schema revision 0004 |
| Migrations | Fresh volume upgraded to 0004; `alembic check`: no new upgrade operations. On a scratch database, upgrade head → downgrade base → upgrade head ran 4 + 4 + 4 steps, followed by a clean `alembic check` |
| Demo investigations (`make verify-demo`) | 36 of 36 checks pass: totals, difference, unexplained remainder, and every finding's amount and evidence status for demos A, B and C against `sample_data/manifest.json` |
| Backend lint (`make lint`) | ruff check and ruff format: clean (83 files) |
| Backend tests, Python 3.14 (`make test`) | **330 passed, 0 failed, 0 skipped, 1 warning** (Starlette test-client deprecation, third party) |
| Backend tests, Python 3.12 (CI version, `python:3.12-slim` container) | **330 passed, 0 failed, 0 skipped, 1 warning** |
| Frontend (`make frontend-check`) | `tsc` and `vite build` pass |
| End to end (`make e2e`, Playwright 1.56.1, against nginx) | **20 passed, 0 failed, 1 skipped** (the screenshot capture, which runs only via `make screenshots`) |
| Benchmarks | All sizes passed their correctness checks; results in [benchmarks.md](benchmarks.md) |

The Python 3.12 run first failed with 20 failures and 29 errors. Both causes were in how it was run, not in the code: the container lacked `sample_data/`, and it reached the databases by service name, while the tests' source allowlist contains `localhost`, `127.0.0.1` and `host.docker.internal`. Run with the repository layout and `host.docker.internal`, it passed in full. CI runs the tests on the runner host against published ports, as `make test` does.

**Production serving.**
- nginx 1.30 runs as uid 101 and the backend as uid 1000.
- SPA routes (e.g. `/investigations/<id>`) return `index.html`.
- `/assets/*` are served with `Cache-Control: public, max-age=31536000, immutable`.
- Responses carry a CSP, `nosniff`, `X-Frame-Options`, `Referrer-Policy` and `Permissions-Policy`, and `server_tokens` is off.

**Security checks.**
- gitleaks over all commits: no leaks. Over the working tree: only the generated passwords in the git-ignored `.env`.
- bandit: 0 issues. pip-audit: no known vulnerabilities.
- npm audit, frontend (including dev) and e2e: 0 vulnerabilities, after upgrading react-router-dom to 7.18.4 (two moderate advisories in v6) and Playwright to 1.56.1 (one high advisory before 1.55.1).
- Trivy, fixable HIGH/CRITICAL:
  - backend: 0;
  - frontend on nginx 1.27-alpine: 57, so it was moved to 1.30-alpine, which has 0;
  - `postgres:16-alpine`: 0 in OS packages, 26 in the upstream `gosu` binary (accepted; reasoning in [security.md](security.md)).
- Database access, SQL execution and uploads are covered by the backend suite: 28 tests in `test_sources_api.py`, 4 in `test_source_role.py`, 36 in `test_files.py` and 18 in `test_imports_api.py`, all passing.
- Through nginx:
  - a 32 MB upload is refused by nginx with a JSON 413;
  - a 26 MB upload is refused by the API with a JSON 413;
  - a binary file named `.csv` is refused with 415.

**Repository.**
- No generated artifacts, dependencies, caches or secrets are tracked (163 files).
- `.gitignore` also covers `.env.*` (except `.env.example`) and `*.log`.
- Every declared dependency is imported.

**Defects found during this milestone and fixed.**
- nginx's 413 page was HTML, so the UI could show only "HTTP 413"; it now returns `{"detail": ...}`.
- The Playwright default base URL still pointed at the dev server.
- `docs/security.md` listed rate limiting as planned for this milestone; it was not built and is now listed under "Not in place".
- The benchmark's memory probe matched its own shell and reported 1.5 MiB; it now takes the highest VmHWM in the container. The benchmark was re-run after the fix.
- The README and learning guide described demos A and C incorrectly; corrected from the manifest.

**Not verified.**
- The GitHub Actions workflow has not run on GitHub; the repository is not published. It passes actionlint 1.7.7, and every step was run locally.
- The rendered README on GitHub (the Mermaid diagrams were rendered locally with mermaid-cli 11.4.2).
