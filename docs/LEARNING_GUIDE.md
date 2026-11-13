# Learning guide

This guide is for someone who wants to understand how TruthSplit works, change it, or explain it in an interview. It follows one investigation through the system, then lists what to read and some exercises.

## 1. The problem in one example

Finance reports March revenue of 1,397,648.86 and Sales reports 1,553,646.25. Both are "right" for their own definition, but they differ by 155,997.39. A useful answer is:

| Cause | Amount | Evidence status |
|---|---:|---|
| Finance nets refunds; Sales reports gross | +102,360.14 | confirmed |
| The sales query joins orders to payments (1:N), so orders paid in two parts appear twice | +56,224.00 | confirmed |
| Orders in March in New York time but not in UTC (first hours of 1 March UTC) | −9,604.00 | confirmed |
| Orders in March in UTC but after March in New York time | +7,017.25 | confirmed |
| Unexplained remainder | 0.00 | |

Each amount is backed by the records that produce it, and the amounts plus the remainder equal the difference exactly, to the cent. That is demo A (`make demo`, then open "Demo A" in the UI). These expected numbers are in `sample_data/manifest.json`, computed by a separate model of the data (`backend/app/seed/shop.py`) rather than by the engine.

## 2. Vocabulary

| Term | Meaning | Where |
|---|---|---|
| Dataset | An immutable, normalized copy of a file or query result. Its fingerprint is a SHA-256 of the normalized records. | `db/imports.py`, `datasets` table |
| Metric definition (Metric DNA) | How a number is computed: aggregation, value field, grain, filters, date window and time zone, refund policy, null policy. Versioned, never edited in place. | `comparison/metrics.py` (`MetricSpec`) |
| Investigation | Two (dataset, definition) inputs plus everything derived from them. | `db/investigations.py` |
| Finding | One cause with an amount (or none), a rule id, an evidence status and supporting records. | `comparison/findings.py` |
| Evidence status | **confirmed**: amount proven from records. **suspected**: plausible, but no amount is claimed. **unexplained**: the remainder. | `diagnosis/autopsy.py` |
| Review status | open → in review → resolved → archived, set by a person. Separate from evidence status (see DECISIONS 39). | `memory/service.py` |

## 3. Follow one request through the code

1. **Upload.** `POST /api/imports/file` (`api/imports.py`). `adapters/files.py` streams the file to disk with a size cap, checks the extension against the file's signature, hashes it (content addressing), and returns a preview with inferred column types (`normalization/values.py: infer_type`).
2. **Import.** `POST /api/datasets` applies a column mapping (`normalization/mapping.py`). Every value goes through a parser in `normalization/values.py`:
   - `parse_amount` produces a `Decimal`, never a float, and rejects anything that doesn't fit `numeric(18,4)`;
   - `parse_timestamp` localizes to the mapping's time zone, then converts to UTC.

   Bad values become `import_issues` rows instead of being skipped silently. Records are bulk-loaded with PostgreSQL `COPY`.
3. **Define the metric.** `POST /api/metric-definitions` stores a `MetricSpec`. `comparison/metrics.py: evaluate` applies it in a fixed order: window → filters → refund policy → grain → null policy → aggregation. `comparison/sql.py` renders the same definition as SQL, and the tests check that the Python and SQL results are equal (`test_sql_and_python_evaluators_agree` in `tests/integration/test_comparison_api.py`). That SQL is the "proof query" shown in reports.
4. **Reconcile.** `POST /api/investigations` evaluates both sides. `comparison/matching.py: reconcile` groups contributions by business key and classifies every difference: missing, extra, amount mismatch, duplicate (identical raw rows) and records with no key.
5. **Autopsy.** `diagnosis/autopsy.py: run_autopsy`:
   - **Definition replay.** Starting from the left definition, switch one rule at a time to the right one, in the order of `REPLAY_STEPS` (`diagnosis/dna.py`), and re-evaluate. Each step's change in the total is attributed to that rule. Because the steps run in order, the step amounts add up to the definition effect exactly. The amount each rule would have caused on its own is stored next to it as the standalone amount, with an overlap note when the two differ.
   - **Data rules** on what is still different:
     - *Join amplification:* confirmed only when the query metadata declares a 1:N join; otherwise it is suspected, with an indicative amount kept out of the totals.
     - *Reporting cutoff:* records outside the other side's query window.
     - *Refund identity:* the per-key difference equals the refund exactly.
   - **The invariant.** Confirmed amounts plus the unexplained remainder must equal right − left. The code asserts it; an investigation that fails this check is never stored.
6. **What-if.** `POST /api/investigations/{id}/simulations` (`simulation/whatif.py`) applies one correction to in-memory copies of the records and runs the autopsy again. It first checks that the copies reproduce the stored baseline (same fingerprints, same totals), so a simulation cannot silently compare against different data.
7. **Report.** `reporting/report.py` renders JSON and a script-free, escaped HTML report from stored rows only.
8. **UI.** `frontend/src/pages/InvestigationDetail.tsx` and `features/investigation/*`. Money stays a string end to end. `lib/format.ts` formats it, and `lib/decimal.ts` adds it with BigInt where the UI must, for example the waterfall's running totals.

## 4. Database sources, safely

A user can never type SQL. `adapters/registered_queries.py` holds fixed SQL with typed parameters, and `adapters/postgres.py` applies these checks before and during every query:

1. Is the host in the allowlist?
2. Do the role's privileges allow anything beyond SELECT? Inspect them; refuse the query if so.
3. Open a `READ ONLY` transaction with statement and lock timeouts.
4. Wrap the registered SQL as a subquery with a deterministic `ORDER BY` and a row limit.

The result becomes an ordinary dataset (a snapshot), so a query and a file compare the same way. The threat model and the test that covers each control are in `docs/security.md`.

## 5. Reading order

1. `README.md`, then `docs/architecture.md` (components, modules, ER diagram).
2. `docs/metric-contract.md`: what a valid record is.
3. `backend/app/comparison/metrics.py` and `tests/unit/test_metrics.py`.
4. `backend/app/comparison/matching.py` and `tests/unit/test_matching.py`.
5. `docs/difference-autopsy.md`, then `backend/app/diagnosis/autopsy.py` and `tests/unit/test_autopsy.py` (50 tests; the hand-computed cases are the best documentation).
6. `backend/app/seed/shop.py` and `sample_data/README.md`: how the demo data and its expected answers are produced independently.
7. `docs/simulation-and-memory.md` and `simulation/whatif.py`.
8. `DECISIONS.md`: why things are the way they are, including the alternatives that were rejected.

## 6. Running things while you read

```bash
scripts/generate_env.sh && docker compose up -d --build --wait   # UI at http://localhost:8080
make demo                      # load demos A, B, C
make verify-demo               # check them against sample_data/manifest.json
make backend-venv && make test # backend tests
make e2e                       # Playwright against the running stack
```

The API is self-documenting at http://localhost:8000/docs.

## 7. Exercises

1. **Change the attribution order.** Swap two entries in `REPLAY_STEPS` and run `pytest tests/unit/test_autopsy.py`. Which tests fail? The total still reconciles; which per-step amounts change, and why? (Hint: `overlap_note`.)
2. **Break the invariant on purpose.** In `run_autopsy`, add one cent to a confirmed finding's amount. Which check fails, and why is nothing stored? (Look near the end of `run_autopsy`.)
3. **Add a filter operator** (for example `starts_with` for text fields) to `FilterOp` and `Filter` in `comparison/metrics.py`. Add it to the SQL renderer in `comparison/sql.py` as a bound `LIKE` pattern with `%` and `_` escaped, and add a case to `test_sql_and_python_evaluators_agree` showing that Python and SQL agree.
4. **Add a registered query** for the demo `shop` database in `adapters/registered_queries.py`, with a typed date parameter. Snapshot it from the Sources page, and compare it against `sample_data/settlement_march.csv`.
5. **Read a suspected finding.** In demo B, open a suspected finding. Why does it have no amount, and what metadata would make it confirmed?
6. **Benchmark a change.** Run `python3 scripts/benchmark.py --sizes 10000` before and after a change to `matching.py`. The script fails if the results change, not only if they get slower.
