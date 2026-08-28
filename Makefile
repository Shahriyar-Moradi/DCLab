.PHONY: db migrate train seed test run web up down sim users

# Local toolchain (no Docker). Uses the project venv when present.
PYTHON ?= $(wildcard .venv/bin/python)
ifeq ($(PYTHON),)
PYTHON := python3
endif
UVICORN := $(dir $(PYTHON))uvicorn
ALEMBIC := $(dir $(PYTHON))alembic
PYTEST := $(dir $(PYTHON))pytest
PG_BIN := /opt/homebrew/opt/postgresql@16/bin
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

test:
	$(PYTEST) --cov=app --cov-report=term-missing

run:
	$(UVICORN) app.main:app --reload --app-dir apps/api --host 127.0.0.1 --port $(API_PORT)

# Future: full containerized stack. Not used for day-to-day local development.
# Stop native Postgres first if port 5432 is already taken: brew services stop postgresql@16
up:
	docker compose --profile docker up -d

down:
	docker compose --profile docker down
