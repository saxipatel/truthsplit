# Test URLs point at the docker-compose databases, using the credentials in .env.
-include .env
TEST_DB ?= postgresql+psycopg://$(APP_DB_USER):$(APP_DB_PASSWORD)@localhost:$(APP_DB_PORT)/truthsplit_test
TEST_SOURCE ?= postgresql+psycopg://$(SOURCE_READER_USER):$(SOURCE_READER_PASSWORD)@localhost:$(SOURCE_DB_PORT)/$(SOURCE_DB_NAME)

.PHONY: env up down logs backend-venv test-db test lint

env:
	@test -f .env || cp .env.example .env

up: env
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend frontend

backend-venv:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

test-db:
	docker compose exec -T db sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB -tc "SELECT 1 FROM pg_database WHERE datname = '"'"'truthsplit_test'"'"'" | grep -q 1 || psql -U $$POSTGRES_USER -d $$POSTGRES_DB -c "CREATE DATABASE truthsplit_test"'

test: env test-db
	cd backend && TRUTHSPLIT_TEST_DATABASE_URL=$(TEST_DB) TRUTHSPLIT_TEST_SOURCE_URL=$(TEST_SOURCE) .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
