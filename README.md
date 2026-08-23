# Decision.ai — Milestone 1: Decision Engine Foundation

An AI-native decision layer. This milestone is the smallest complete slice: ingest
historical sales-opportunity data, predict conversion probability, compute expected
value, recommend a next action, and persist a traceable audit trail of *why*.

API only. One model. No UI.

## Local development (no Docker)

The API runs with uvicorn in a venv. Postgres is a native Homebrew service on
`localhost:5432`. Docker files stay in the repo for a later containerized deploy.

```bash
# one-time
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
brew install postgresql@16   # already done on this machine
make db
make migrate
make train

# every day
make run
```

In another terminal (venv activated, or use the Makefile which prefers `.venv/bin`):

```bash
make seed
curl -s localhost:8000/health
curl -s -X POST localhost:8000/decisions/generate \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id": "opp_1"}'
```

`.env` / `.env.example`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/decisionai
```

## Example commands

```bash
curl -F "file=@data/sample/opportunities.csv" localhost:8000/opportunities/upload
curl -s "localhost:8000/opportunities?limit=5"
curl -s -X POST localhost:8000/decisions/generate \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id": "opp_1"}'
curl -s "localhost:8000/decisions?limit=10"
make test
```

## Make targets

| Target         | Purpose                                                |
| -------------- | ------------------------------------------------------ |
| `make db`      | Start native Postgres and create local databases       |
| `make migrate` | Apply Alembic migrations                               |
| `make run`     | Run the API locally with autoreload                    |
| `make train`   | Train the conversion model                             |
| `make seed`    | Upload `data/sample/opportunities.csv`                 |
| `make test`    | Run pytest with coverage                               |
| `make up`      | Future: Docker Compose stack (`--profile docker`)      |
| `make down`    | Future: stop the Docker stack                          |

## Docker (later)

`Dockerfile` and `docker-compose.yml` are kept for a future one-command deploy.
They are behind Compose profile `docker`, so they will not start during normal
local work and will not steal port 5432 from Homebrew Postgres.

When you want them:

```bash
brew services stop postgresql@16   # free port 5432
make up
```

## What's not in this milestone

- Human approve/reject workflow
- Action execution / CRM write-back
- Outcome tracking / business-impact measurement
- Experimentation / control vs treatment
- CRM or marketing-platform integration
- Multi-model ensembles (exactly one model per prediction)
- LLM or agent usage
- A frontend
