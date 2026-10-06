.PHONY: db migrate train seed test test-ai run worker web up down sim users truth-check truth-drift truth-generate truth-idempotence lock lock-mcp lock-agents benchmark benchmark-quick test-db-clean

# Local toolchain (no Docker). Uses the project venv when present.
PYTHON ?= $(wildcard .venv/bin/python)
ifeq ($(PYTHON),)
PYTHON := python3
endif
UVICORN := $(dir $(PYTHON))uvicorn
ALEMBIC := $(dir $(PYTHON))alembic
PYTEST := $(dir $(PYTHON))pytest
# Override on Linux, e.g. `make db PG_BIN=/usr/lib/postgresql/16/bin`.
PG_BIN ?= /opt/homebrew/opt/postgresql@16/bin
export PATH := $(PG_BIN):$(PATH)

ifneq (,$(wildcard .env))
include .env
export
endif

API_PORT ?= 8001
WEB_PORT ?= 3001
API_URL ?= http://127.0.0.1:$(API_PORT)

# Create/start native Postgres (Homebrew) and the decisionai databases.
# brew services start fails with launchctl "Bootstrap failed: 5" when the
# agent is already loaded; skip it if port 5432 is already accepting connections.
db:
	@command -v psql >/dev/null || { echo "Install Postgres: brew install postgresql@16"; exit 1; }
	@if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then \
		echo "Postgres already running on 5432"; \
	else \
		brew services start postgresql@16; \
	fi
	@createuser -s postgres 2>/dev/null || true
	@psql -d postgres -c "ALTER USER postgres WITH PASSWORD 'postgres';" >/dev/null
	@createdb -U postgres decisionai 2>/dev/null || true
	@createdb -U postgres decisionai_test 2>/dev/null || true
	@echo "local Postgres ready → postgresql://postgres:postgres@localhost:5432/decisionai"

migrate:
	$(ALEMBIC) upgrade head

train:
	$(PYTHON) -m app.ml.train

sim:
	$(PYTHON) -m app.sim.generate
	$(PYTHON) -m app.sim.run all

web:
	cd apps/web && npm run dev -- -p $(WEB_PORT)

users:
	$(dir $(PYTHON))dclab user seed

seed:
	curl -s -F "file=@data/sample/opportunities.csv" $(API_URL)/app/opportunities/upload

# Parallel by default (one Postgres database per xdist worker); PYTEST_WORKERS=0 runs serially.
PYTEST_WORKERS ?= auto
test:
	$(PYTEST) -n $(PYTEST_WORKERS) --cov=app --cov-report=term-missing

# P6.10-B: the offline AI test harness (fake providers, recorded fixtures, goldens, property
# and chaos tests; no network). Regenerate fixtures + goldens: DCLAB_RECORD_AI=1 DCLAB_UPDATE_GOLDEN=1 make test-ai
test-ai:
	$(PYTEST) apps/api/tests/ai_harness -m ai_harness -n $(PYTEST_WORKERS) -q -p no:cacheprovider

truth-check:
	$(PYTHON) -m scripts.check_truth_drift

truth-drift: truth-check

truth-generate:
	$(PYTHON) -m scripts.generate_truth_artifacts

truth-idempotence:
	$(PYTHON) -m scripts.generate_truth_artifacts --verify-idempotent

# Local API. Default dispatcher is postgres (persist and return). That leaves
# CSV uploads on Queued unless a worker claims them — so `make run` uses the
# in-process thread adapter unless ML_JOB_DISPATCHER is already set.
run:
	ML_JOB_DISPATCHER=$(or $(ML_JOB_DISPATCHER),postgres) $(UVICORN) app.main:app --reload --app-dir apps/api --host 127.0.0.1 --port $(API_PORT)

worker:
	$(dir $(PYTHON))dclab worker run

# Future: full containerized stack. Not used for day-to-day local development.
# Stop native Postgres first if port 5432 is already taken: brew services stop postgresql@16
up:
	docker compose --profile docker up -d

down:
	docker compose --profile docker down

# Refresh the cross-platform dependency lock after editing pyproject.toml.
lock:
	uv pip compile pyproject.toml --extra boosting --extra tuning --extra dev --universal \
		--python-version 3.12 --no-header -o requirements.lock

# MCP server deps (packages/dclab_mcp) stay out of requirements.lock, which the
# API/worker images install whole; constrained by it so shared pins match.
lock-mcp:
	uv pip compile pyproject.toml --extra mcp -c requirements.lock --universal \
		--python-version 3.12 --no-header -o requirements-mcp.lock

# AI provider SDKs (the `agents` extra: typesafe-sdk) stay out of requirements.lock
# too; everything works without them (fake providers).
lock-agents:
	uv pip compile pyproject.toml --extra agents -c requirements.lock --universal \
		--python-version 3.12 --no-header -o requirements-agents.lock

# R1-A benchmark harness (engine only, no database). `benchmark` adds OpenML
# tasks (downloaded once into SCIKIT_LEARN_DATA); `benchmark-quick` needs no network.
benchmark:
	$(PYTHON) -m benchmarks.harness.run --suite full --out benchmarks/results/latest-full.json
	$(PYTHON) -m benchmarks.harness.compare benchmarks/results/latest-full.json

benchmark-quick:
	$(PYTHON) -m benchmarks.harness.run --suite quick --out benchmarks/results/latest-quick.json
	$(PYTHON) -m benchmarks.harness.compare benchmarks/results/latest-quick.json

# Drop test databases left behind by killed pytest runs (no other run active).
test-db-clean:
	$(PYTHON) -c "import os;from sqlalchemy import create_engine,text;u=os.environ.get('DATABASE_URL','postgresql://postgres:postgres@localhost:5432/decisionai').rsplit('/',1)[0]+'/postgres';e=create_engine(u,isolation_level='AUTOCOMMIT');c=e.connect();[c.execute(text(f'DROP DATABASE IF EXISTS \"{n}\" WITH (FORCE)')) for (n,) in c.execute(text(\"SELECT datname FROM pg_database WHERE datname LIKE 'decisionai\\_test%'\")).all()];print('dropped stale test databases')"
