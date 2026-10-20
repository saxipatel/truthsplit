# TruthSplit

TruthSplit investigates why two reports of the same business metric disagree. It explains the gap between them with record-level evidence and reports whatever it cannot explain as an unexplained remainder.

**Status: in development.** In place so far: the Docker Compose stack, PostgreSQL schema and migrations, health checks, the file import API (CSV / XLSX / XLS upload, preview, column mapping, normalization), and the comparison engine (metric definitions, record-level reconciliation, investigations with evidence). Root-cause diagnosis, the database source mode, simulation, reports and most of the UI are not built yet.

## Running locally

Requires Docker with Compose v2.

```bash
cp .env.example .env      # then change the passwords
docker compose up -d --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| API | http://localhost:8000 (`/health/live`, `/health/ready`, `/docs`) |
| Metadata database | `localhost:5433` |
| Demo source database | `localhost:5434`; the backend connects only as the read-only `ts_reader` role |

The backend container runs `alembic upgrade head` before it starts.

## File import API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/imports/file` | Multipart upload. Validates type, signature and size; returns sheet names and a preview with inferred column types. |
| `GET` | `/api/imports/{id}/preview?sheet=&encoding=` | Preview another worksheet or a CSV encoding. |
| `POST` | `/api/datasets` | Apply a column mapping and import normalized records. |
| `GET` | `/api/datasets`, `/api/datasets/{id}` | Datasets and their import summary. |
| `GET` | `/api/datasets/{id}/records`, `/api/datasets/{id}/issues` | Normalized records and rejected values, paginated. |

## Comparison API

| Method | Path | Purpose |
|---|---|---|
| `POST` / `GET` | `/api/metric-definitions` | Create the next version of a named metric definition; list definitions. |
| `POST` | `/api/investigations` | Evaluate two (dataset, definition) inputs, reconcile them and store the result. |
| `GET` | `/api/investigations`, `/api/investigations/{id}` | Stored investigations with totals, key statistics and findings. |
| `GET` | `/api/investigations/{id}/findings/{finding_id}/evidence` | Per-key evidence with source row numbers, paginated. |
| `GET` | `/api/investigations/{id}/evidence.csv` | All evidence rows as CSV. |

Parsing and validation rules are in [docs/metric-contract.md](docs/metric-contract.md). Measured import times are in [docs/verification-report.md](docs/verification-report.md).

## Backend tests

Requires Python 3.12+ and the compose databases running.

```bash
make backend-venv
make test
make lint
```

Tests that need a database are skipped unless `TRUTHSPLIT_TEST_DATABASE_URL` is set; `make test` sets it. The test database (`truthsplit_test`) is created on the compose `db` service and is reset by the test run.
