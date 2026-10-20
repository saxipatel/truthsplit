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
