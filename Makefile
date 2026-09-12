.DEFAULT_GOAL := help
PYTHON := .venv/bin/python
TEST_PATHS := core/tests backend/tests tests

help: ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtualenv (Python 3.13)
	uv venv --python 3.13 .venv
	$(MAKE) install

install: ## Install all deps incl. dev
	uv pip install --python .venv/bin/python -e ".[dev,ingest,cv,geo,exportlibs,s3]"

lint: ## ruff + mypy
	.venv/bin/ruff check .
	.venv/bin/mypy core ingestion takeoff classification provenance review boq catalog pricing exports backend

arch: ## import-linter boundary check
	.venv/bin/lint-imports

guards: ## license + reference-leak guards
	$(PYTHON) tools/guards/reference_leak.py

test: ## Run all non-integration Python tests
	.venv/bin/pytest $(TEST_PATHS) -m "not integration"

golden-update: ## Regenerate golden-run files (tests/golden/data) — deliberate drift ONLY: bump ENGINE_VERSION first (T121)
	$(PYTHON) tests/golden/golden_run.py --update

test-integration: ## Run integration tests (needs Postgres)
	.venv/bin/pytest $(TEST_PATHS) -m integration

test-all: ## Everything
	.venv/bin/pytest $(TEST_PATHS)

db-up: ## Start dev Postgres + MinIO via Docker
	docker compose up -d postgres minio

db-migrate: ## Run Alembic migrations
	.venv/bin/alembic -c backend/alembic.ini upgrade head

db-revision: ## Create migration (usage: make db-revision m="msg")
	.venv/bin/alembic revision --autogenerate -m "$(m)" -c backend/alembic.ini

api: ## Run API dev server
	.venv/bin/uvicorn backend.app.main:app --reload --port 8000

worker: ## Run background worker
	$(PYTHON) -m backend.app.jobs.worker

e2e: ## Browser E2E (needs api :8099 + worker + npm dev :5173 + Postgres; CI runs the same spec in the e2e job)
	cd frontend && npm run e2e

deploy-build: ## Build the prod backend image (docker-compose.prod.yml)
	docker compose -f docker-compose.prod.yml build

deploy-up: ## Start prod composition (Postgres + MinIO + api + worker; build first)
	docker compose -f docker-compose.prod.yml up -d --build

deploy-down: ## Stop the prod composition (named volumes survive)
	docker compose -f docker-compose.prod.yml down

.PHONY: help venv install lint arch guards test golden-update test-integration test-all db-up db-migrate db-revision api worker e2e deploy-build deploy-up deploy-down
