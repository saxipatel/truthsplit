# TruthSplit

TruthSplit investigates why two reports of the same business metric disagree. It explains the gap between them with record-level evidence and reports whatever it cannot explain as an unexplained remainder.

**Status: in development.** Only the foundation milestone is in place: Docker Compose, the PostgreSQL schema and migrations, health checks, and the frontend shell. File import, comparison, diagnosis, simulation and reports are not built yet.

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

## Backend tests

Requires Python 3.12+ and the compose databases running.

```bash
make backend-venv
make test
make lint
```

Tests that need a database are skipped unless `TRUTHSPLIT_TEST_DATABASE_URL` is set; `make test` sets it. The test database (`truthsplit_test`) is created on the compose `db` service and is reset by the test run.
