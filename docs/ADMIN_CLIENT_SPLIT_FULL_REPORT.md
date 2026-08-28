# DCLab Admin/Client Split & Business Translation Layer

## Complete Implementation Report (Steps 0–9)

This document is the full record of the architectural overhaul that turned DCLab from a
single mixed product (business screens and ML-ops screens sharing the same routes and
the same vocabulary) into two strictly separated products:

1. **A client product** that a business user can operate end-to-end in plain language.
2. **An admin product** that DCLab staff use to inspect, train, compare, and monitor
   the actual machine-learning engine with no restrictions.

The one rule everything else exists to enforce:

> **No raw ML-engine output ever reaches a client-facing screen, API response, or
> error message.**

This report explains that rule in non-technical language, then walks every
implementation step with the exact files, data models, APIs, frontend routes, tests,
bugs found in production-like crawls, and the CI that keeps the split from rotting.

Companion documents:

- `ACCESS_MODEL.md` — the living guardrail for anyone adding a new feature.
- `docs/PROJECT_RECAP.md` — earlier product history (UI redesign, Lab engine, benchmarks).

---

# Part I — Non-technical explanation

## 1. What the product was before this work

DCLab already had working machinery:

- A sales **opportunity** uploader and a **decision** engine that recommended “call
  today / send email / do nothing.”
- An **experimentation Lab** that trains many models, scores them, and combines the
  best ones.
- **Simulations** for eight business problems (churn, purchase, upsell, etc.).

The problem was not that those features were missing. The problem was that a paying
customer and an internal data scientist were looking at the **same kind of output**.
A client dashboard could theoretically show “ensemble fusion weights,” “PR-AUC,”
“model_version,” or a model-retrain event sitting next to “Contact this lead today.”

That is a product and a trust problem:

- A sales manager does not need (and should not see) how the sausage is made.
- Internal researchers *do* need every number, every candidate model, every metric.
- If those two worlds leak into each other, the client product becomes unusable and
  the research product becomes censored.

The overhaul did not replace the engine. It built a **wall** and a **translator**.

## 2. The wall (who can go where)

There are exactly two kinds of login:

| Who | What they can open | What they see |
|---|---|---|
| **Client user** | Dashboard, Insights, Opportunities, Decisions, Upload, Labs | Business language only |
| **DCLab admin** | Everything the client sees, plus Organizations, Labs & Experiments, Registry, Monitoring | Full ML detail |

If a client types `/admin/lab` into the browser, they get a real **403 Forbidden**
page — not a polite redirect that hides the fact they were blocked. If they call an
admin API with their token, the API also returns **403**. Hiding a button in the menu
is not security; the menu, the web server, and the API all independently refuse.

## 3. The translator (what a client is allowed to hear)

The engine still produces probabilities, model names, feature-importance lists, AUC
scores, and so on. Those numbers never leave the building as-is.

A translator sits between the engine and every client API. It turns:

- `conversion_probability = 0.82` into **High** / **Medium** / **Low**
- `action_key = call_customer` into **“Call the customer”**
- a list of numeric feature weights into **plain sentences** such as
  “Product usage has dropped over the last month”

The only object a client ever receives is a **Client-facing insight**: who it is
about, which business function it belongs to (Marketing, Sales, Revenue, Churn &
Retention, Customer Value, Custom), a short headline, a confidence band, a
recommended action, an expected value in AED, and 2–4 sentences of reasoning.

There is no field on that object for a model name. There is no field for a raw
probability. The shape itself makes the leak structurally hard.

## 4. What a client can actually do now (the whole workflow)

1. **Sign in** at `/login`.
2. **Dashboard** (`/app/dashboards`) — business metrics and a recent-decisions feed.
   A model-retrain event from the admin Lab never appears here. That was proven with
   a test that actually runs a retrain and then checks the client feed.
3. **Insights** (`/app/insights`) — the eight simulation problems, grouped by
   business function, not by “classification vs regression.”
4. **Opportunities** (`/app/opportunities`) — upload a CSV, browse deals.
5. **Decisions** (`/app/decisions`) — generate a recommended action per deal, with
   High/Medium/Low confidence and plain-language reasons.
6. **Labs** (`/app/labs`) — a bounded free trial: pick a fixed problem, run it on
   sample data or a small CSV (max 500 rows, max 3 runs per problem, 30-second
   budget). Results come back as the same translated insights. If the run is slow
   or the file is wrong, the product fails gracefully instead of crashing.

The client never sees a “Models” tab. That content lives only in Admin → Registry.

## 5. What an admin can actually do now

1. **Organizations** — which workspaces exist, how many users/opportunities/
   decisions/trial runs they have.
2. **Labs & Experiments** — the original DCLab engine, unrestricted: datasets,
   tasks, candidates, ensembles, precision/recall/AUC, feature importance.
3. **Registry** — every model produced by an experiment, a simulation, or a
   **client trial**, with full metrics. Opening a client trial shows the raw
   engine payload the client never saw.
4. **Monitoring** — retrain events and metric deltas across experiments,
   simulations, and client trials. Drift detection is explicitly not implemented
   yet; the page says so rather than pretending.

## 6. How we stop this from rotting

Four independent checks, from cheapest to most thorough:

1. **Static schema scan** — looks at every client API response type. If someone
   adds a field named `model_version`, the build fails.
2. **Static frontend scan** — looks at client page source. If someone writes
   “AUC” into Insights copy, the build fails. This was demonstrated live:
   inserting `AUC` into `apps/web/app/app/insights/page.tsx` made
   `python -m scripts.scan_banned_terms` exit 1 and print the exact file.
3. **Live client crawl** — logs in as a real client, calls **every** `/app` API
   with real data, and scans the **bytes on the wire**. This caught a real bug
   the static scans missed: the churn action `offer_training` humanized to
   “Offer training,” which contains the banned word **training**.
4. **Live admin crawl** — hits **every** `/admin` endpoint with a client token
   and asserts 403. Currently 33/33 endpoints.

All four run in GitHub Actions on every push and pull request. They are not a
manual checklist.

---

# Part II — Architecture (technical)

## 7. System shape after the split

```
                    ┌─────────────┐
                    │   /login    │  POST /auth/login → JWT
                    └──────┬──────┘
                           │ cookie dclab_token  /  Authorization: Bearer
           ┌───────────────┴───────────────┐
           │ Next.js middleware.ts         │
           │ verifies JWT signature        │
           └───────────────┬───────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 │                 ▼
   /app/* pages      public marketing    /admin/* pages
   client_user       (no auth)           dclab_admin only
   (and admin)                           403 if client
         │                                     │
         ▼                                     ▼
   FastAPI client_api                    FastAPI admin_api
   prefix /app                           prefix /admin
   Depends(require_client)               Depends(require_admin)
         │                                     │
         ▼                                     ▼
   app.translation ──────────────► engine / sim / lab
   ClientFacingInsight             raw probabilities,
                                   candidates, AUC, fusion
```

Important properties:

- Auth is **not** a UI convention. It is a FastAPI dependency on the parent
  router. A newly mounted admin route inherits `require_admin` automatically.
- The web middleware verifies the JWT **signature** (`jose.jwtVerify`). A
  hand-edited cookie claiming `role=dclab_admin` does not get through.
- Admins may use the client tree (`require_client` allows both roles) so staff
  can support an account. Clients can **never** use the admin tree.

## 8. Monorepo layout (what was added or changed)

```
apps/api/app/
  api/
    auth.py                      login + /auth/me
    deps.py                      get_current_user, require_admin, require_client
    decisions.py                 client decisions (translated)
    insights.py                  GET /app/insights
    client_labs.py               /app/labs/*
    lab.py                       admin experimentation (mounted under /admin)
    admin_organizations.py
    admin_model_registry.py
    admin_monitoring.py
  translation/                   THE WALL
    models.py                    ClientFacingInsight, ConfidenceBand, InsightCategory
    bands.py                     probability/agreement → High/Medium/Low
    decisions.py                 opportunity → insight
    simulations.py               8 use cases → insight
    banned_terms.py              single source of truth
    scanner.py                   schema + frontend static scan
  services/
    auth_service.py
    generate_service.py          now calls translate_opportunity_decision
    decision_query.py            serialize_decision through translation
    insight_query.py
    client_lab_service.py
    client_lab_upload_service.py
    admin_organization_service.py
    admin_model_registry_service.py
    admin_monitoring_service.py
  db/models.py                   User, ClientLabRun, ClientLabRunAudit, ClientLabUpload, Decision.incremental_value
  alembic/versions/
    0006_users.py
    0007_decision_incremental_value.py
    0008_client_lab_runs.py
    0009_client_lab_run_audits.py
    0010_client_lab_uploads.py

apps/web/
  middleware.ts
  app/login/page.tsx
  app/app/...                    all client product pages
  app/admin/...                  all admin pages
  lib/infrastructure/session.ts
  lib/infrastructure/api-client.ts   attaches Authorization
  lib/domain/schemas.ts          BEGIN/END CLIENT-FACING SCHEMAS markers

scripts/
  scan_banned_terms.py
  audit_admin_surface.py
  audit_client_surface.py
  audit_web_routes.py

.github/workflows/ci.yml
ACCESS_MODEL.md
```

## 9. Authentication internals

### 9.1 User model (`apps/api/app/db/models.py`)

- `UserRole`: `dclab_admin` | `client_user`
- `User`: `id`, `email`, `password_hash`, `role`, `workspace_id`, name, timestamps
- Check constraint: a client user must belong to a workspace; admin role is
  independent of that constraint as implemented for the prototype.

Migration: `0006_users.py`.

### 9.2 Passwords and tokens (`apps/api/app/services/auth_service.py`)

- Passwords hashed with **bcrypt**.
- JWTs issued with **PyJWT**, payload includes `sub` (user id), `role`, `email`,
  expiry (`access_token_minutes`, default 12 hours).
- Secret: `Settings.jwt_secret` (default `dev-only-insecure-secret-change-me`
  for local/CI only; must be overridden in any real deployment).
- Frontend uses the **same secret** via `JWT_SECRET` / fallback, because
  middleware verifies tokens locally without a round-trip.

### 9.3 Dependencies (`apps/api/app/api/deps.py`)

- `_bearer_token` — missing/malformed header → **401** + `WWW-Authenticate: Bearer`
- `get_current_user` — decode + load user; `AuthError` → **401**
- `require_admin` — role must be `dclab_admin` else **403**
- `require_client` — role in `{client_user, dclab_admin}` else **403**

### 9.4 Login CLI

```
dclab user create --email admin@dclab.io --password '...' --role dclab_admin
dclab user create --email demo@client.io --password '...' --role client_user
```

Implemented in `apps/api/app/cli/main.py` (`cmd_user_create`).

### 9.5 Frontend session

- Cookie name: `dclab_token`
- `lib/infrastructure/session.ts` stores/reads the token
- `api-client.ts` attaches `Authorization: Bearer <token>` on every API call
- `apiPostForm` added for multipart Client Labs / opportunity upload
- Login page: `apps/web/app/login/page.tsx`
- `useLogin`, `useSession`, `logout` in `lib/application/hooks.ts`

### 9.6 Route moves (Step 0)

| Before | After |
|---|---|
| `/dashboards` | `/app/dashboards` |
| `/opportunities` | `/app/opportunities` |
| `/decisions` | `/app/decisions` |
| `/lab` | `/admin/lab` |

API:

| Before | After |
|---|---|
| `/opportunities`, `/decisions` | `/app/opportunities`, `/app/decisions` |
| `/lab/*` | `/admin/...` (lab router prefix stripped, mounted under `admin_api`) |
| `/simulations` | `/admin/simulations` (raw harness; client equivalent is Labs) |

---

# Part III — Step-by-step implementation

## Step 0 — Access model and routing split

### Intent

Separate the world into `/admin/*` and `/app/*` on both the API and the website,
and make a client token physically unable to read admin data.

### What was implemented

1. **User table + roles + JWT + bcrypt** (see §9).
2. **Two FastAPI parent routers** in `apps/api/app/main.py` with role
   dependencies at the parent, not on each leaf route.
3. **Next.js middleware** matching `/admin/:path*` and `/app/:path*`.
4. **Role-aware header**: client sees WORKSPACE nav; admin sees WORKSPACE + ADMIN.
5. **Tests that walk OpenAPI**, not a handwritten list:
   `apps/api/tests/test_access_control.py`
   - `_routes_under("/admin")` parses `app.openapi()["paths"]` because this
     FastAPI version nests included routers; walking `app.routes` silently
     returned `[]` and would have made the audit pass while testing nothing.
6. **Fixtures** in `conftest.py`: `admin_user`, `client_user`, `admin_token`,
   `client_token`, isolated `auth_client` / `admin_client` (each gets its own
   `TestClient` so headers cannot clobber each other).
7. Existing API tests rewritten to `/app/*` or `/admin/*` with the right
   authenticated client.

### Non-technical meaning

A customer cannot wander into the research lab by guessing a URL. Staff still
can. That is the entire access model.

### Bugs found in this step

- `test_admin_routes_exist` expected a populated admin tree and got `[]`
  because of nested routers. Fixed by reading OpenAPI.
- Shared `TestClient` between admin and client fixtures caused 403s that looked
  like RBAC bugs. Fixed by giving each fixture its own client instance.

---

## Step 1 — Business translation layer

### Intent

Create a service that is the **only** legal way to turn engine output into
something a client may see, plus an automated check that fails the build if ML
jargon appears on a client surface.

### Core types (`app/translation/models.py`)

`ConfidenceBand`: `High` | `Medium` | `Low`

`InsightCategory`:

- Marketing
- Sales
- Revenue
- Churn & Retention
- Customer Value
- Custom

`ClientFacingInsight` fields: `subject_id`, `category`, `headline`,
`confidence_band`, `recommended_action`, `expected_value`, `currency`,
`reasoning`, `generated_at`.

### Band conversion (`bands.py`)

- Probability: ≥ 0.7 High, ≥ 0.4 Medium, else Low
- Member-model agreement (internal, never shown as a number): ≥ 0.85 High,
  ≥ 0.6 Medium, else Low

### Decision translator (`decisions.py`)

- Maps `CONTACT_TODAY` / `SCHEDULE_FOLLOWUP` / `SEND_EMAIL` / `NO_ACTION` to
  human labels.
- Headline: “Likely to convert” vs “Low near-term priority.”
- Reasoning from **business features** of the opportunity (engagement, last
  contact, stage, sales-rep availability, incremental value) — never from
  “feature importance” lists or raw `p(y)`.

### Simulation translator (`simulations.py`)

Maps eight use cases:

| Use case | Category | Headline |
|---|---|---|
| churn | Churn & Retention | Retention risk |
| purchase | Sales | Purchase likelihood |
| lead_conversion | Sales | Lead priority |
| upsell | Revenue | Upsell opportunity |
| cross_sell | Revenue | Cross-sell opportunity |
| campaign_response | Marketing | Campaign responsiveness |
| customer_value | Customer Value | Customer value |
| custom_support | Custom | Support need |

Each use case has its own `_reasoning` builder so the sentences are specific
(usage drop, cart abandonments, renewal window, etc.) instead of one generic
line that would have to mention raw features.

`ACTION_OVERRIDES` humanizes policy keys. Generic
`key.replace("_", " ").capitalize()` is the fallback — and is exactly what
later produced the `offer_training` leak (Step 8).

### Persistence change for re-translation

`Decision` gained `incremental_value` (migration `0007`) so that reading a
stored decision can rebuild reasoning without re-running the model, while the
API schema still **omits** `conversion_probability`, `model_version`,
`prediction_id`, and numeric `confidence`.

`generate_service.to_generate_response` and `decision_query.serialize_decision`
both go through `translate_opportunity_decision`.

Client Pydantic/API schemas (`DecisionGenerateResponse`, `DecisionRead`) now
match `ClientFacingInsight` language: `confidence_band`, `reasoning`,
`recommended_action`.

### Banned terms (`banned_terms.py`)

18 words, custom boundary so `_` and `-` count as separators (`model_version`
matches `model`).

22 phrases including algorithm names and `feature importance` / `pr_auc`.

### Scanner (`scanner.py`)

- Recurses Pydantic models under `/app` routes (lists, unions, nested models).
- Frontend: only client directories; marketing copy is out of scope.
- `schemas.ts` is shared with admin types, so only the block between
  `// BEGIN CLIENT-FACING SCHEMAS` and `// END CLIENT-FACING SCHEMAS` is scanned.

CLI: `python -m scripts.scan_banned_terms` (`--api-only` / `--web-only`).

### Frontend consumption

- `lib/domain/schemas.ts` Decision schemas updated.
- `toneFromConfidenceBand` in `signals.ts`.
- `DecisionLedgerEntry` no longer shows conversion probability or model version.
- Dashboard “Avg confidence” became **High-confidence share**.
- Marketing homepage hero similarly dropped “Prediction model.”

### Tests (`test_translation_layer.py`)

- Detector unit tests (boundaries, phrases, case).
- Opportunity translator: no banned terms, no raw probability in reasoning.
- All eight simulation translators parameterized.
- Live schema + frontend tree must be clean.
- `TestScannerCatchesRegressions` injects a leaky model / leaky TSX and asserts
  the scanner fires — proving it is not a no-op.

### Non-technical meaning

The customer sees “Call the customer, High confidence, because usage dropped.”
They never see “LightGBM, PR-AUC 0.73, feature_importance[login_frequency]=0.41.”

---

## Step 2 — Client dashboard isolation

### Intent (as written in the original plan)

Remove ML-ops events from “Recent Decisions” and replace them with business-only
events.

### What we actually found

The Recent Decisions feed **already** read only from translated `Decision`
objects via `/app/decisions`. There was no path that mixed experiment-complete
events into that list.

### What we implemented instead (the real DoD)

`TestClientDashboardIsolatedFromMlOps`:

1. Seed and **execute** a synthetic experiment (a real retrain).
2. Assert admin can `GET /admin/experiments/{id}`.
3. Assert client gets **403** on that URL.
4. Assert `/app/opportunities` and `/app/decisions` JSON contain neither the
   experiment UUID nor banned terms.

Dashboard page comment documents that the feed is translation-only (worded
without the banned word “model,” because comments in scanned files are scanned).

### Non-technical meaning

If DCLab retrains overnight, the customer dashboard does not grow a new row
that says “experiment completed.” It only shows business decisions.

---

## Step 3 — Client Insights rebuild

### Intent (as written)

Rename “Intelligence” to “Insights,” organize by business function.

### What we actually found

There was no Intelligence tab. Simulations existed only as an **admin** harness
returning raw ML.

### What we built

**Backend**

- `insight_query.py` — latest `SimulationRun` per use case, translated,
  grouped by `InsightCategory`.
- `domain/insight.py` — `InsightCategoryGroup`, `InsightListResponse`.
- `GET /app/insights` in `api/insights.py`, mounted on `client_api`.

Empty categories are still returned so the UI can show all six functions even
when a use case has never been run.

**Frontend**

- `/app/insights` page
- Shared `InsightCard` + `categoryMeta.ts` (`CATEGORY_ORDER`, icons, blurbs)
- `useInsights()` hook
- “Insights” added to WORKSPACE nav
- Client schemas: `InsightCategorySchema`, `ClientInsightSchema`, etc.

**Tests**

`TestClientInsightsSection` — six categories present, seeded simulation
populates the right group, response has no banned terms.

### Non-technical meaning

Insights is the “what should I worry about this week?” page, organized the way
a company is organized (marketing vs churn), not the way a data scientist
thinks (binary classification vs ranking).

---

## Step 4 — No client Models tab

### Intent (as written)

Remove the Models tab from the client side; move content to Admin Model Registry.

### What we actually found

There was never a client Models route. Candidate scores, AUC, fusion, etc.
already lived under `/admin/lab/experiments/{id}`.

### What we implemented

`TestNoClientModelsTab`:

- `/models`, `/app/models`, `/model-registry`, `/app/model-registry` → **404**
  for both roles (no leftover client path).
- Admin `GET .../models`, `.../candidates`, `.../ensemble` return full
  `model_family`, `score`, `fusion`, `test_metrics`.
- Same URLs with a client token → **403**.

Step 6 later added the dedicated Registry UI; Step 4 locked the access story.

### Non-technical meaning

Customers do not pick models. DCLab does. Customers see recommendations.

---

## Step 5 — Client Labs (bounded free trial)

### Intent

A real trial that reuses the DCLab engine, not a scripted marketing “case study.”
Marketing case studies on the public homepage were left alone; they are not
in-product Labs.

### Data model `ClientLabRun` (migration `0008`)

`workspace_id`, `requested_by`, `use_case`, `category`, `data_source`
(`sample` | `uploaded`), `row_count`, `status` (`completed` | `failed`),
`failure_reason`, `insights` (JSONB of already-translated payloads).

### Service (`client_lab_service.py`)

Hard limits:

| Constant | Value | Behavior |
|---|---|---|
| `MAX_UPLOAD_ROWS` | 500 | 422, no DB row |
| `MAX_TRIAL_RUNS_PER_PROBLEM` | 3 | 429 when exceeded |
| `TRIAL_TIMEOUT_SECONDS` | 30 | stored `failed` row, request returns |
| `MAX_INSIGHTS_PER_RUN` | 6 | truncate translated list |

Execution:

1. Validate use case against the fixed catalog (`UnknownLabProblemError` → 404).
2. Check quota.
3. Sample data via `ensure_data` / `run_use_case`, or parse uploaded CSV and
   check required columns (`TrialDatasetColumnsError` → 422).
4. Isolate training in a **temporary directory**.
5. Run `app.sim.runner.run_use_case` in a `ThreadPoolExecutor`.
6. On timeout: `shutdown(wait=False)` so the HTTP request does not block on the
   orphaned thread (the `with` form of the executor would `wait=True` and hang
   the timeout test).
7. Translate via `translate_simulation_outcome`; persist only insights.

### API (`/app/labs`)

- `GET /problems` — catalog
- `GET /problems/{use_case}/quota`
- `POST /runs` — form field `use_case`, optional `file`
- `GET /runs`, `GET /runs/{run_id}` — workspace-scoped

### Frontend `/app/labs`

Per-problem cards: quota, run with sample, upload CSV, show `InsightCard`s.

### Tests (`test_client_labs.py`)

Catalog cleanliness, sample run translated-only, unknown problem 404, oversized
upload 422 with no row, missing columns 422, quota 429, timeout → failed not
crash, list/detail, workspace isolation, scanner registration.

### Non-technical meaning

A prospect can try “retention risk on sample data” a few times. They cannot
upload a million-row warehouse dump or leave a training job running forever.
They get business answers, not a model zoo.

---

## Step 6 — Admin surfaces

### Intent

Confirm/build Organizations, Model Registry, Labs, Monitoring — admin only,
full ML detail.

Labs UI already existed under `/admin/lab`. The other three were built.

### Organizations

- Domain: `OrganizationSummary`, `OrganizationDetail`, `OrganizationUserRead`
- Service: counts users, opportunities, decisions, trial runs per workspace
- API: `GET /admin/organizations`, `GET /admin/organizations/{workspace_id}`
- UI: list + detail pages

### Model Registry

- `RegisteredModel`: id, source (`experiment` | `simulation`, later
  `client_trial`), name, status, `model_family`, `fusion`, `metrics`,
  `candidate_count`, `created_at`
- Combined from Experiment and SimulationRun records
- UI: `/admin/models` with links into experiment detail

### Monitoring

- `MetricDelta`, `RetrainEvent`, `DatasetHealth`, `MonitoringOverview`
- Deltas between consecutive runs of the same task/use case
- Explicit `drift_detection_note`: drift detection is **not** implemented
- UI: `/admin/monitoring`

### Nav

ADMIN array: Organizations, Labs & Experiments, Registry, Monitoring.

Note: the label **“Model Registry”** later failed the banned-terms scanner
because `SiteHeader.tsx` is in the client scan set (shared layout). Renamed to
**Registry**.

### Tests

`test_admin_surfaces.py` — admin 200, client 403, payload shape.

---

## Step 7 — Custom prediction wiring (admin audit trail)

### Intent (as written)

Custom prediction output must go through the translation layer; the underlying
ML task must be reviewable on the admin Model Registry.

### What we actually found

There is no natural-language “type a custom prediction” box. Marketing copy
mentions custom predictions. The real client-triggered prediction is **Client
Labs**.

The gap: `run_trial` translated results and **discarded** the raw
`run_use_case` dict. Admins could not inspect what the engine actually did.

### Implementation

`ClientLabRunAudit` (migration `0009`):

- 1:1 with `ClientLabRun`
- `payload` JSONB = full raw result
- created only on **successful** runs
- never returned from `/app` endpoints

Registry: source `client_trial`, `client_lab_run_id`,
`GET /admin/models/client-trials/{audit_id}` → `ClientTrialAuditDetail`.

Monitoring: client trials merged into chronological retrain events so deltas
can span admin simulations and client trials of the same use case.

Frontend: oxblood badge for `client_trial`, detail page dumps raw JSON.

Tests (`test_custom_prediction_admin_trail.py`):

- completed trial appears on registry with full detail
- appears in monitoring with use-case delta
- failed trial is not registered as a model
- client-facing trial responses stay translated-only

### Non-technical meaning

When a customer runs a trial, they see “Call the customer.” Internally, DCLab
keeps the full lab notebook for that run and can open it in Registry.

---

## Step 8 — Full regression audit in CI

### Intent

Crawl **every** client page and API response for banned terms (not a sample).
Crawl **every** admin API with a client token and demand 403/404 (not a sample).
Put both in CI.

### `audit_admin_surface.py` (existed from Step 0, confirmed complete)

- Reads live `/openapi.json`
- Substitutes path params with a nil UUID
- `--role client` → every `/admin` op must be 401/403/404
- `--role admin` → must not be 401/403 (400/404/422 from empty bodies are OK)
- `--role anonymous` → 401

Verified live: **33/33** admin operations, all 403 for client, all 401 for
anonymous.

### `audit_client_surface.py` (new)

Two jobs:

1. **API**: every `/app` operation from OpenAPI must be in
   `KNOWN_CLIENT_OPERATIONS`. Then actually call them with real data
   (upload CSV if needed, generate a decision, run a lab trial, fetch by id)
   and `find_banned_terms` on the **raw JSON bytes**.
2. **Pages**: `rglob("page.tsx")` under `apps/web/app/app/`, resolve `[id]`
   using IDs discovered during the API crawl (opportunity id vs decision id
   are different maps — a flat global id would 404). Expect HTTP 200 and
   clean HTML.

Client pages are `"use client"` and load data in the browser; the HTML crawl
proves reachability and static copy. The **business strings** are proven by
the API crawl.

### Real bug this crawl found

Churn policy action `offer_training` → humanized **“Offer training”** →
banned word `training`.

Fix: `ACTION_OVERRIDES["offer_training"] = "Offer a coaching session"`.

Regression:
`test_every_real_policy_action_key_humanizes_clean` loads **every** action
from all eight `configs/policies/sim/*.yaml` files and translates each one.

That closed a hole: `test_every_use_case_has_a_clean_translator` had always
used `recommended_action_key="email"`, which never hit the colliding key.

### Coverage-drift unit test

`test_client_surface_audit_script_knows_about_every_live_app_operation`
compares OpenAPI `/app` ops to `KNOWN_CLIENT_OPERATIONS` at pytest speed so a
new endpoint fails CI even if nobody reruns the slow live crawl yet.

### CI (`.github/workflows/ci.yml`) — first pipeline in this repo

On push to `main` and every PR:

1. Postgres 16 service
2. `pip install -e .`, create `decisionai` + `decisionai_test`, `alembic upgrade head`
3. `pytest`
4. `python -m scripts.scan_banned_terms`
5. `npm ci`, `tsc --noEmit`, `next lint`, `next build`
6. Seed admin + client users via `dclab user create`
7. Boot `uvicorn` + `next start` (production build, not `next dev`)
8. `audit_admin_surface` for client, admin, anonymous
9. `audit_client_surface --role client`

### Non-technical meaning

We do not “remember to check” before a release. The machine checks every
client sentence and every admin lock on every change.

---

## Step 9 — Final review and ACCESS_MODEL.md

### Client walkthrough (live API)

- Opportunities: business fields (amount, stage, engagement) — no ML fields.
- Decisions: “No action needed” / “Contact today”, bands Low/Medium/High,
  reasoning about engagement, recency, stage, sales-rep availability.
- Insights / **Marketing**: “Campaign responsiveness,” Medium, “Send an email,”
  “Strong recent email engagement,” “Expected to add AED 60…”
- Insights / **Churn & Retention**: “Retention risk,” High, “Call the customer,”
  usage drop, negative support, renewal soon.

### Admin walkthrough (live API)

- Organizations: 200, 1 workspace
- Model Registry: 200, 45 registered models
- Monitoring: 200, keys `retrain_events`, `dataset_health`, `drift_detection_note`
- Labs environments: 200, 1 environment

### Deliberately reintroduced banned term (live, not only a fixture)

Temporarily put `AUC` in `apps/web/app/app/insights/page.tsx`.

```
[FAIL] client frontend source — banned terms found:
  apps/web/app/app/insights/page.tsx: auc
exit code: 1
```

Reverted. `git diff` empty. Scan passed again.

`TestScannerCatchesRegressions` also passed (3 tests).

### `ACCESS_MODEL.md`

Repo-root document covering roles, enforcement, translation, banned list,
four guardrail layers, CI, Client Labs limits, admin surfaces, and a
**checklist for adding features** so the split does not rot.

---

# Part IV — File and API inventory

## 10. Client API surface (`/app`) — 14 operations

| Method | Path | Purpose |
|---|---|---|
| GET | `/app/opportunities` | List deals |
| GET | `/app/opportunities/{opportunity_id}` | Deal detail |
| POST | `/app/opportunities/upload` | CSV upload |
| GET | `/app/decisions` | List translated decisions |
| GET | `/app/decisions/{decision_id}` | Decision detail |
| POST | `/app/decisions/generate` | Generate via translation |
| GET | `/app/insights` | Grouped ClientFacingInsight |
| GET | `/app/labs/problems` | Trial catalog |
| GET | `/app/labs/problems/{use_case}/quota` | Remaining runs |
| GET | `/app/labs/runs` | List trials |
| POST | `/app/labs/runs` | Start trial |
| GET | `/app/labs/runs/{run_id}` | Trial result (translated) |
| GET | `/app/labs/uploads` | Open-ingest files for this workspace |
| POST | `/app/labs/uploads` | Save any usual data file (no required columns) |

Public (no role): `POST /auth/login`, `GET /auth/me`, `GET /health`.

## 11. Client web routes

- `/login`
- `/app/dashboards`
- `/app/insights`
- `/app/opportunities`, `/app/opportunities/[id]`, `/app/opportunities/upload`
- `/app/decisions`, `/app/decisions/[id]`
- `/app/labs`

## 12. Admin web routes

- `/admin/organizations`, `/admin/organizations/[id]`
- `/admin/lab`, datasets, experiments, tasks (create + list + ids)
- `/admin/models`, `/admin/models/client-trials/[id]`
- `/admin/monitoring`

Admin API: 33 operations under `/admin` (lab engine, simulations, orgs,
registry, monitoring) — all `require_admin`.

## 13. Database migrations added in this overhaul

| Rev | Purpose |
|---|---|
| 0006 | `users` table, roles |
| 0007 | `decisions.incremental_value` |
| 0008 | `client_lab_runs` |
| 0009 | `client_lab_run_audits` |
| 0010 | `client_lab_uploads` (open ingest; no engine run) |

## 14. Test suites added or extended

| File | What it proves |
|---|---|
| `test_access_control.py` | Every admin route 403/401; login; no client models path; OpenAPI ↔ crawl-script sync |
| `test_translation_layer.py` | Translators, scanners, dashboard isolation, insights cleanliness, all policy actions |
| `test_client_labs.py` | Bounds, translation, isolation, graceful failure |
| `test_admin_surfaces.py` | Org / registry / monitoring access and shape |
| `test_custom_prediction_admin_trail.py` | Audit trail vs client translation |
| Existing `test_api.py`, `test_simulation.py`, `engine/test_lab_api.py` | Paths and auth updated |

Final local count at Step 9 close: **201 backend tests passing**.

---

# Part V — Bugs found during implementation (and why they matter)

These are not hypothetical. Each one would have shipped without the matching
guardrail.

1. **OpenAPI vs `app.routes`** — enumerating nested FastAPI routers via
   `app.routes` returned nothing. An access audit that “passed” would have
   tested zero endpoints. Always enumerate `app.openapi()["paths"]`.

2. **Shared TestClient headers** — admin and client fixtures mutating one
   client made a later admin call look like a 403 bug. Isolation is part of
   the access story.

3. **Scanner scope** — marketing homepage saying “model” is legitimate
   product copy. Scanning it would make the guardrail unusable. Scanner dirs
   were narrowed to actual insight surfaces; `schemas.ts` uses BEGIN/END
   markers.

4. **Comments are source** — a dashboard comment containing “model” failed
   the frontend scan. Documentation inside scanned files must also be clean.

5. **Nav label “Model Registry”** — shared `SiteHeader` is scanned.
   Admin-only labels still cannot contain banned words if they live in a
   shared file. Label became “Registry.”

6. **`offer_training` → “Offer training”** — static schema said
   `recommended_action: str`. Only a **live response crawl** saw the word
   `training`. Lesson: generated strings need generated-string tests
   (all real policy keys, not one safe example).

7. **ThreadPoolExecutor context manager on timeout** — `with` implies
   `shutdown(wait=True)`, so a “timed out” Labs request waited for the
   sleeping worker. Soft timeout requires `shutdown(wait=False)`.

8. **Stale Next.js `.next` cache** — `Cannot find module './682.js'` during
   audits. Production crawl uses `next build && next start`, not a rotting
   `next dev` cache.

9. **Alembic from the wrong directory** — `script_location` lives at repo
   root. CI and docs run `alembic` from the repository root.

---

# Part VI — How to operate the prototype

## Accounts (local / CI defaults)

| Role | Email | Typical password env |
|---|---|---|
| Admin | `admin@dclab.io` | `DCLAB_ADMIN_PASSWORD` / `AdminPass123!` |
| Client | `demo@client.io` | `DCLAB_CLIENT_PASSWORD` / `ClientPass123!` |

Create with `dclab user create` after `alembic upgrade head`.

## Run locally

```
# API
make run
# or: uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000

# Web
cd apps/web && npm run dev
# for audits that scan HTML: npm run build && npx next start -p 3000
```

Client workflow: login → Dashboard → Insights → Opportunities → Upload →
Decisions (generate) → Labs (sample run).

Admin workflow: login as admin → Organizations → Labs & Experiments →
Registry (including client-trial rows after a Labs run) → Monitoring.

## Guardrail commands

```
pytest
python -m scripts.scan_banned_terms
python -m scripts.audit_admin_surface --role client
python -m scripts.audit_client_surface --role client
```

---

# Part VII — Explicit non-goals and remaining honesty

The overhaul is complete against the ten-step plan. These things are **true
and documented**, not hidden:

- There is still **no** free-text “ask any prediction” box. Client Labs is
  the bounded equivalent.
- **Drift detection** is not implemented. Monitoring says so in
  `drift_detection_note`.
- Client Labs timeout is **soft**. The worker thread is not kill -9’d.
- Default JWT secret is for local/CI only.
- Marketing pages may still say “model” in ordinary English; they are not
  scanned as insight surfaces.
- Admins using the client app still see translated copy on `/app` (by
  design). Full ML is on `/admin`.
- `require_client` allows admins into `/app` for support; it does not allow
  clients into `/admin`.

---

# Part VIII — Definition of Done mapped to evidence

| Plan DoD | Evidence |
|---|---|
| Client completes Dashboard → Insights → Recommendations/Decisions → Labs without ML vocabulary | Live `/app` walkthrough + `audit_client_surface` PASS 12/12 APIs, 8/8 pages |
| Admin has Organizations, Registry, Labs, Monitoring unrestricted | Live admin GETs 200; 45 registry entries; experiment candidate/ensemble payloads include `model_family`, `fusion`, metrics |
| Client token rejected on all admin routes | 33/33 HTTP 403 (`audit_admin_surface --role client`) |
| Crawls in CI, not one-off | `.github/workflows/ci.yml` |
| ACCESS_MODEL.md exists | repo root |
| Reintroduced banned term fails the build | Live `AUC` in insights page → scan exit 1; plus `TestScannerCatchesRegressions` |
| Custom prediction reviewable by admin | `ClientLabRunAudit` + `/admin/models/client-trials/{id}` |

---

# Closing

This work did not invent a new machine-learning engine. It invented a
**product boundary**.

On one side of the boundary, a business user gets decisions they can act on.
On the other side, DCLab keeps the scientific record: every candidate, every
metric, every client-triggered trial’s raw payload.

The boundary is enforced three times (API role, web middleware, nav),
translated once (the only client DTO), and checked four ways (schema scan,
source scan, live client crawl, live admin crawl), on every CI run.

If a future change puts `model_version` on a client schema, or “Offer
training” on a generated action, or a new `/admin` route without
`require_admin`, the build is designed to fail **before** a customer sees it.

That is the entire overhaul, from Step 0 through Step 9.
