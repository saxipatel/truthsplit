# Benchmarks

Measured on 2026-10-22 against a freshly cloned, freshly built stack (new volumes, `docker compose build --no-cache`), through nginx on http://localhost:8080. These are the same HTTP endpoints the UI uses. Raw output: [benchmarks/results.json](benchmarks/results.json).

```bash
python3 scripts/benchmark.py --sizes 10000,100000,250000 --out docs/benchmarks/results.json
```

## Environment

| | |
|---|---|
| Machine | Apple M4 (arm64), macOS 26.6 |
| Docker | Docker Desktop, engine 29.7.2; VM with 10 CPUs and 7.7 GiB memory |
| Backend | Python 3.12 (container), one Uvicorn worker |
| Database | PostgreSQL 16 (container) |
| Client | `scripts/benchmark.py`, standard library only, run on the host (Python 3.14) |

## Data

Each size N generates a left CSV with N orders and a right CSV derived from it with a fixed seed (`20261022 + N`). The planted differences are:
- about 1% of keys removed;
- about 1% of amounts changed by +1.00;
- about 0.5% of rows duplicated exactly;
- 500 extra keys.

**Every investigation run is checked against the planted differences** (missing, extra, duplicate and mismatch amounts, unexplained remainder 0), and every simulation result is checked against the expected after-difference. A run that is fast but wrong fails the script. All runs below passed these checks.

| N | Right rows | Left file | Right file | Difference (right − left) |
|---:|---:|---:|---:|---:|
| 10,000 | 10,460 | 0.65 MB | 0.68 MB | −9,932.04 |
| 100,000 | 100,055 | 6.6 MB | 6.6 MB | −211,259.19 |
| 250,000 | 249,241 | 16.7 MB | 16.7 MB | −637,826.93 |

250,000 rows is the configured per-dataset limit (`TRUTHSPLIT_MAX_ROWS`).

## Results

Wall-clock seconds per HTTP request. Investigation and simulation were run 3 times each; the table shows the median, with all three runs in parentheses.

| Operation | 10,000 rows | 100,000 rows | 250,000 rows |
|---|---:|---:|---:|
| Upload (left / right) | 0.09 / 0.01 | 0.06 / 0.06 | 0.10 / 0.08 |
| Import: parse, normalize, `COPY` (left / right) | 0.39 / 0.32 | 2.23 / 2.15 | 5.37 / 6.47 |
| Investigation: evaluate both sides, reconcile, autopsy, store findings and evidence | **0.17** (0.19, 0.17, 0.14) | **1.74** (1.75, 1.74, 1.65) | **4.56** (4.61, 4.39, 4.56) |
| What-if simulation (remove exact duplicates) | **0.20** (0.21, 0.19, 0.20) | **2.31** (2.31, 2.35, 2.24) | **6.00** (6.00, 6.21, 5.89) |
| Dataset profile (aggregate SQL) | 0.05 | 0.25 | 0.52 |
| HTML report export | 0.01 | 0.01 | 0.03 |

**Backend peak resident memory:** 824 MiB after the whole run (VmHWM of the Uvicorn process, reset by restarting the backend just before the run; 90 MiB at idle). Investigations and simulations hold both datasets in memory as Python objects, so this is the dominant cost at 250,000 rows per side.

## Reading these numbers

- Time grows roughly linearly with rows: about 18 µs per row of N for an investigation (both sides together), and about 22–26 µs per row for import.
- A simulation costs more than an investigation, because it re-evaluates both sides and runs the full autopsy on the corrected copy. It first checks that the copies reproduce the stored baseline.
- Report export is constant: it reads stored findings and samples of the evidence, and never re-runs the engine.
- A first run on the same stack a few minutes earlier gave medians within about 20% of these (for example, investigation at 250,000 rows: 4.54 s, simulation 6.21 s). Its memory figure was discarded: the probe was measuring its own shell, and it was fixed before this run.

## Limits of this measurement

- **One machine.** Docker Desktop on a laptop, one run of each size, three repetitions of the timed engine operations. Not a capacity guarantee.
- **Synthetic data.** It has one currency, short keys and one attribute column. Wider rows with many attributes were not measured.
- **One request at a time.** There is no concurrency test; the backend runs a single Uvicorn worker.
- **Database sources are not benchmarked.** Snapshotting a registered query is not part of this run; its speed depends on the source database.
