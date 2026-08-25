# DCLab — Decision layer + Experimentation Platform v0.1

DCLab is two slices in one monorepo:

1. **Milestone 1 decision ledger** — ingest sales opportunities, score conversion, recommend an action, persist why.
2. **Lab experiment engine** — ingest a tabular dataset, define a prediction task, search feature-group × model candidates, detect leakage, validate temporally, select a diverse set, compare ensemble vs best single vs baselines, and write a report.

It is **not** AutoML-for-its-own-sake, not a CRM, and not the Horizontal Intelligence layer (out of scope).

`action_uplift` in the M1 policy is still a **placeholder**, not a causal treatment effect.

## Local development (no Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
brew install postgresql@16
make db
make migrate
make train
cd apps/web && npm install && cp .env.example .env.local && cd ../..

make run    # API  http://127.0.0.1:8000
make web    # UI   http://localhost:3000
```

M1 UI: opportunities, decisions, upload.
Lab UI: http://localhost:3000/lab

```bash
make seed
dclab env seed-dogfood
dclab experiment run --dataset synthetic --task purchase_prediction
```

Olist (manual benchmark, not CI):

```bash
python scripts/fetch_olist.py
dclab experiment run --dataset olist --task purchase_prediction
dclab experiment run --dataset olist --task revenue_prediction
dclab experiment run --dataset olist --task customer_value
dclab experiment run --dataset olist --task next_purchase
dclab experiment run --dataset olist --task marketing_response
```

`.env`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/decisionai
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

## Layout

```
apps/api/app/engine/   generic experiment engine (no dataset if-branches)
apps/api/app/ml/       M1 factory facade over the engine
apps/api/app/sim/      isolated case-study pack
apps/api/app/cli/      dclab CLI
apps/web/app/lab/      internal Lab UI
configs/tasks/         purchase, revenue, customer_value, next_purchase, marketing_response
```

## Tests

```bash
make test
```

CI uses synthetic data only. Olist is optional and gitignored under `data/olist/raw/`.

## Out of scope

Horizontal Intelligence, CRM integrations, SSO, billing, Kubernetes, causal platform, distributed GPU training.

See `docs/` for architecture, experimentation, leakage, and reporting.
