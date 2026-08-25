.PHONY: db migrate train seed test run web up down sim

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

# Create/start native Postgres (Homebrew) and the decisionai databases.
db:
	@command -v psql >/dev/null || { echo "Install Postgres: brew install postgresql@16"; exit 1; }
	@brew services start postgresql@16 >/dev/null
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
	npm --prefix apps/web run dev

seed:
	curl -s -F "file=@data/sample/opportunities.csv" http://localhost:8000/opportunities/upload

test:
	$(PYTEST) --cov=app --cov-report=term-missing

run:
	$(UVICORN) app.main:app --reload --app-dir apps/api --host 127.0.0.1 --port 8000

# Future: full containerized stack. Not used for day-to-day local development.
# Stop native Postgres first if port 5432 is already taken: brew services stop postgresql@16
up:
	docker compose --profile docker up -d

down:
	docker compose --profile docker down
