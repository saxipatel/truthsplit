# TruthSplit

TruthSplit investigates why two reports of the same business metric disagree. It explains the gap between them with record-level evidence and reports whatever it cannot explain as an unexplained remainder.

**Status: in development.** In place so far: the Docker Compose stack, PostgreSQL schema and migrations, health checks, the file import API (CSV / XLSX / XLS upload, preview, column mapping, normalization), the comparison engine (metric definitions, record-level reconciliation, investigations with evidence), and read-only PostgreSQL sources (registered queries snapshotted into datasets, so files and queries can be compared in any combination). Root-cause diagnosis, simulation, reports and most of the UI are not built yet.

## Running locally

Requires Docker with Compose v2.

```bash
scripts/generate_env.sh   # writes .env with random database passwords
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

## Database sources

TruthSplit reads source databases only through **registered queries**: SQL defined in `backend/app/adapters/registered_queries.py`, with typed parameters. Users choose a query and supply parameter values; they can't submit SQL. Connections come from `TRUTHSPLIT_SOURCE_<NAME>_URL` environment variables, and the compose file defines `demo`, which points at the seeded `shop` database as `ts_reader`. Controls are described in [docs/security.md](docs/security.md).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/connections` | Configured sources (host and database only, never credentials). |
| `POST` | `/api/connections/{name}/test` | Connect and report the role's privileges. `ok` is false for any role that could write. |
| `GET` | `/api/connections/{name}/catalog` | Tables and columns the role may read. |
| `GET` | `/api/registered-queries` | Queries with their SQL, hash, parameters, tables, joins and grain. |
| `POST` | `/api/registered-queries/{key}/preview` | First 50 rows for the given parameters. |
| `POST` | `/api/registered-queries/{key}/snapshots` | Run the query and store its result as a dataset. |
| `POST` | `/api/datasets/{id}/freshness` | Re-run a snapshot's query and report whether the source data or the registered SQL has changed. |

Snapshots are ordinary datasets, so `POST /api/investigations` compares any two of CSV, XLSX, XLS and query snapshots. To reset the demo source data:

```bash
make seed-source
```

Parsing and validation rules are in [docs/metric-contract.md](docs/metric-contract.md). Measured import times are in [docs/verification-report.md](docs/verification-report.md).

## Backend tests

Requires Python 3.12+ and the compose databases running.

```bash
make backend-venv
make test
make lint
```

Tests that need a database are skipped unless `TRUTHSPLIT_TEST_DATABASE_URL` is set; `make test` sets it. The test database (`truthsplit_test`) is created on the compose `db` service and is reset by the test run.
