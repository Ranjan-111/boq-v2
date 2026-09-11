.DEFAULT_GOAL := help
PYTHON := .venv/bin/python
TEST_PATHS := core/tests backend/tests tests

help: ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtualenv (Python 3.13)
	uv venv --python 3.13 .venv
	$(MAKE) install

install: ## Install all deps incl. dev
	uv pip install --python .venv/bin/python -e ".[dev,ingest,cv,geo]"

lint: ## ruff + mypy
	.venv/bin/ruff check .
	.venv/bin/mypy core ingestion takeoff classification provenance review boq catalog pricing exports backend

arch: ## import-linter boundary check
	.venv/bin/lint-imports

guards: ## license + reference-leak guards
	$(PYTHON) tools/guards/reference_leak.py

test: ## Run all non-integration Python tests
	.venv/bin/pytest $(TEST_PATHS) -m "not integration"

test-integration: ## Run integration tests (needs Postgres)
	.venv/bin/pytest $(TEST_PATHS) -m integration

test-all: ## Everything
	.venv/bin/pytest $(TEST_PATHS)

db-up: ## Start dev Postgres + MinIO via Docker
	docker compose up -d postgres minio

db-migrate: ## Run Alembic migrations
	.venv/bin/alembic upgrade head -c backend/alembic.ini

db-revision: ## Create migration (usage: make db-revision m="msg")
	.venv/bin/alembic revision --autogenerate -m "$(m)" -c backend/alembic.ini

api: ## Run API dev server
	.venv/bin/uvicorn backend.app.main:app --reload --port 8000

worker: ## Run background worker
	$(PYTHON) -m backend.app.jobs.worker

e2e: ## Browser E2E (needs api :8099 + worker + npm dev :5173 + Postgres)
	cd frontend && npm run e2e

.PHONY: help venv install lint arch guards test test-integration test-all db-up db-migrate db-revision api worker e2e
