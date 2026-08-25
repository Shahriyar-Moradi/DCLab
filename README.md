# Decision.ai — Milestone 1: Decision Engine Foundation

An AI-native **decision layer**, not AutoML, not a CRM, and not an agent platform.
Given historical sales-opportunity data it answers: which opportunities to act on,
what to do, expected value, and why — with a persisted audit trail.

This milestone is the smallest complete slice: CSV ingest → conversion probability →
policy-backed recommended action → ledger UI.

## What you get

- FastAPI in `apps/api`: upload opportunities, generate decisions, list the ledger
- Next.js 14 app in `apps/web`: overview, opportunities, decisions, CSV upload
- One served conversion probability (factory trains several sklearn candidates and
  keeps a blend only if it beats the best single model)
- YAML policy for action choice (`configs/policies/opportunity_prioritization.yaml`)
- Isolated case-study simulation pack (`apps/api/app/sim/`, `make sim`) — not part
  of the generate JSON contract

`action_uplift` in the policy is an explicit **placeholder**, not a causal treatment effect.

## Local development (no Docker)

Postgres is a native Homebrew service on `localhost:5432`. Docker files stay in the
repo for a later containerized deploy.

```bash
# one-time
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
brew install postgresql@16
make db
make migrate
make train
cd apps/web && npm install && cp .env.example .env.local && cd ../..

# every day — two terminals
make run    # API  http://127.0.0.1:8000
make web    # UI   http://localhost:3000
```

Then seed and open the UI:

```bash
make seed
open http://localhost:3000
```

Generate from an opportunity page, or:

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/decisions/generate \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id": "opp_1"}'
```

`.env` / `.env.example`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/decisionai
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

`apps/web/.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Layout

```
apps/api/          FastAPI — thin HTTP adapters over domain + services
  app/api/         routes
  app/domain/      DTOs and errors
  app/services/    ingest, query, generate, policy decide
  app/db/          SQLAlchemy + Alembic
  app/ml/          conversion factory / serve
  app/sim/         case-study simulations (isolated from generate)
apps/web/          Next.js App Router
  app/             pages (thin)
  app/components/  UI, including DecisionLedgerEntry
  lib/domain/      Zod schemas, formatters, action tones
  lib/application/ TanStack Query hooks
  lib/infrastructure/  API client
configs/           conversion layer + policies (+ configs/*/sim)
data/sample/       M1 opportunities CSV
data/sim/          simulation CSVs
```

The UI does not add Next.js API routes or a second database. Generate responses stay
exactly eight keys: `opportunity_id`, `conversion_probability`, `expected_revenue`,
`recommended_action`, `confidence`, `reasoning`, `model_version`, `policy_version`.

## Example commands

```bash
curl -F "file=@data/sample/opportunities.csv" localhost:8000/opportunities/upload
curl -s "localhost:8000/opportunities?limit=5&sort=amount&order=desc"
curl -s "localhost:8000/opportunities?stage=proposal"
curl -s -X POST localhost:8000/decisions/generate \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id": "opp_1"}'
curl -s "localhost:8000/decisions?limit=10&action=CONTACT_TODAY"
make test
```

## Make targets

| Target         | Purpose                                              |
| -------------- | ---------------------------------------------------- |
| `make db`      | Start native Postgres and create local databases     |
| `make migrate` | Apply Alembic migrations                             |
| `make train`   | Train the conversion model                           |
| `make run`     | Run the API locally with autoreload                  |
| `make web`     | Run the Next.js UI (`apps/web`, port 3000)           |
| `make seed`    | Upload `data/sample/opportunities.csv`               |
| `make sim`     | Generate simulation CSVs and run the case-study pack |
| `make test`    | Run pytest with coverage                             |
| `make up`      | Future: Docker Compose stack (`--profile docker`)    |
| `make down`    | Future: stop the Docker stack                        |

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
- Auth / login
- Action execution / CRM write-back
- Outcome tracking / business-impact measurement
- Experimentation / control vs treatment
- CRM or marketing-platform integration
- LLM or agent usage
- Causal uplift (policy uplifts are placeholders)
