# Test URLs point at the docker-compose databases, using the credentials in .env.
-include .env
TEST_DB ?= postgresql+psycopg://$(APP_DB_USER):$(APP_DB_PASSWORD)@localhost:$(APP_DB_PORT)/truthsplit_test
TEST_SOURCE ?= postgresql+psycopg://$(SOURCE_READER_USER):$(SOURCE_READER_PASSWORD)@localhost:$(SOURCE_DB_PORT)/$(SOURCE_DB_NAME)
# Used only to check that a privileged login is refused.
TEST_SOURCE_ADMIN ?= postgresql+psycopg://source_admin:$(SOURCE_DB_ADMIN_PASSWORD)@localhost:$(SOURCE_DB_PORT)/$(SOURCE_DB_NAME)

.PHONY: env up down logs backend-venv test-db seed-source demo demo-files test e2e screenshots lint frontend-check

env:
	@test -f .env || scripts/generate_env.sh

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

seed-source:
	docker compose exec -T source-db sh /docker-entrypoint-initdb.d/02-shop.sh

demo:
	docker compose exec -T backend python -m app.seed.load

demo-files:
	cd backend && .venv/bin/python -m app.seed.demo ../sample_data

test: env test-db
	cd backend && TRUTHSPLIT_TEST_DATABASE_URL=$(TEST_DB) TRUTHSPLIT_TEST_SOURCE_URL=$(TEST_SOURCE) TRUTHSPLIT_TEST_SOURCE_ADMIN_URL=$(TEST_SOURCE_ADMIN) .venv/bin/pytest

# End-to-end tests in the official Playwright image, against the running compose stack.
e2e:
	docker run --rm --network truthsplit_default -v "$(CURDIR)":/repo -v /repo/e2e/node_modules -w /repo/e2e -e E2E_BASE_URL=http://frontend:5173 \
		mcr.microsoft.com/playwright:v1.49.1-jammy sh -c "npm ci --no-audit --no-fund && npx playwright test"

screenshots:
	docker run --rm --network truthsplit_default -v "$(CURDIR)":/repo -v /repo/e2e/node_modules -w /repo/e2e -e E2E_BASE_URL=http://frontend:5173 -e SCREENSHOTS=1 \
		mcr.microsoft.com/playwright:v1.49.1-jammy sh -c "npm ci --no-audit --no-fund && npx playwright test screenshots"

frontend-check:
	docker run --rm -v "$(CURDIR)/frontend":/app -v /app/node_modules -w /app node:22-alpine sh -c "npm ci --no-audit --no-fund && npm run build && rm -rf dist"

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
