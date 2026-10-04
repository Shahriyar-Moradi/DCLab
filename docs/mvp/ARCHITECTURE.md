# DCLab MVP architecture

Status: CURRENT design authority (2026-10-01). Supersedes the delivery order of
`docs/agentic-program/`; reuses its invariants where cited.

## 1. Product

**DCLab is an ML lab whose unit of work is the versioned ML state graph, not
code.** Data scientists, ML engineers and AI agents change datasets, features,
splits, models, hyperparameters, metrics and experiments as typed, versioned
nodes. DCLab executes every change as a reproducible experiment, verifies it
scientifically, and remembers why each decision was made.

- **Primary users:** data scientists and ML engineers working on structured data
  (classification, regression; forecasting later).
- **Interfaces, in delivery order:** `/v1` API → Python SDK/CLI → **MCP server**
  (Claude Code / Cursor / Codex drive DCLab) → **Studio** (web) with the **in-app
  assistant** (chat panel; same tool catalog as MCP, read tools run, write tools
  become confirm cards — ADR 0009, `prompts/ASSISTANT.md`) → internal agents
  (NOOA proposal classes) → notebook/IDE plugin (later). Every capability ships
  its Studio screen in the phase that builds it (`UI_COVERAGE.md`).
- **Three views of one truth** (from `docs/agentic-program/DCLAB_CORE_CONCEPT.md:20-38`):
  Level 1 conversation/actions, Level 2 ML workflow graph, Level 3 code/infra.
  Every view resolves to the same node IDs.
- **Differentiators:** (1) the state graph with staleness and branching;
  (2) verify — automatic scientific investigation; (3) improve — a bounded,
  constraint-driven experiment loop where every iteration is an auditable node;
  (4) project memory as immutable decision records; (5) an R&D benchmark that
  proves engine quality publicly.
- **Not in the MVP:** online serving, autonomous retraining, arbitrary connectors,
  stateful Jupyter runtime, dual-cloud parity, external GPU marketplace, SLM
  fine-tuning, business decision/action layer. All have a place in Phase 9.

## 2. System architecture

```
 Claude Code / Cursor ─MCP─┐   Studio (Next.js 15, BFF) ─┐   SDK / CLI ─┐
                           ▼                             ▼              ▼
              ┌──────────────── FastAPI  (/v1 + internal routes) ───────────────┐
              │ Auth: HttpOnly session (browser) · service tokens (SDK/MCP)     │
              │ Workspace authorization on every request (ADR 0001–0004)        │
              │                                                                 │
              │  Graph service     Command service      Read models (Studio)    │
              │  (nodes, refs,     (idempotent intent → (explorers, compare,    │
              │   staleness)        ExecutionRequest)    timeline)              │
              │  Decision records  Agent/proposal svc   LLM gateway (+ledger)   │
              │  Assistant svc (tool catalog shared with MCP, SSE to Studio)    │
              └──────────┬──────────────────┬──────────────────────┬────────────┘
                         │                  │                      │
                 PostgreSQL 16         MlJob queue           Object storage
                 (graph, ledger,       (SKIP LOCKED,         (local / S3 / GCS;
                  evidence locks)       leases)               datasets, models,
                         ▲                  │                 artifacts)
                         │                  ▼                      ▲
              ┌──────────┴──────── Worker process(es) ─────────────┴───────────┐
              │ Engine: profile → plan (problem/split/metric/leakage) → prepare │
              │ → fold-local Pipeline → candidates (LR/RF/XGB/LGBM/CatBoost/     │
              │ dummy, Optuna) → CV select → lock → holdout once → package       │
              │ Verify checks · Improve loop · Batch prediction · Monitoring     │
              │ Agent runner (NOOA Predict-only) · Jev client (shadow)           │
              └─────────────────────────────────────────────────────────────────┘
```

Rules of the topology:

1. The API never trains, never loads model packages and never runs generated
   code. All heavy work is an `MlJob` claimed by a worker
   (`services/ml_job_service.py:292`). `ML_JOB_DISPATCHER=thread` is dev-only.
2. One durable queue: PostgreSQL `ml_jobs` with `FOR UPDATE SKIP LOCKED`, leases
   and heartbeats. No broker until measured need
   (`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md` §3 rule 7).
3. Every external call (LLM, Jev, object store, compute provider) happens outside
   DB transactions; intent is persisted first, results reconciled after.
4. Interfaces are thin: MCP → SDK → `/v1` → services. No interface has logic or
   authority the others lack (MASTER inv. 11).
5. Code that will become open source lives behind package boundaries:
   `app/engine` (ML engine, no DB imports), `packages/dclab_client` (SDK),
   `packages/dclab_mcp` (MCP server). Services/DB/Studio stay product code.

## 3. Fundamentals

### 3.1 Software

- **One owner per concern.** One training path (open-ingest runner), one queue,
  one LLM gateway, one storage port, one decision ledger, one progress ledger
  (`docs/mvp/STATUS.md`). Search before creating.
- **Layering:** `api/` (transport, auth, schemas) → `services/` (transactions,
  authorization, orchestration) → `domain/` (Pydantic contracts, enums, pure
  rules) → `engine/` (pure ML, takes DataFrames + typed plans, returns typed
  results, no SQLAlchemy). `db/` is used only by services.
- **Typed boundaries.** Pydantic models for every service input/output and every
  JSON column schema. New code avoids `dict[str, Any]`; the 1,270-line
  `run_auto_train_job` is split into stage functions with typed results (P1.5).
- **Idempotent commands.** Mutations from SDK/MCP/agents carry an
  Idempotency-Key bound to a request digest (existing `execution_requests`).
- **Fail closed** on unsafe config, missing authorization, unknown LLM exposure,
  invalid agent output. Fail safe in ML: LLM/agent failure falls back to the
  deterministic path, never crashes the run.
- **Feature flags** (typed settings, default off) + kill switch for every
  optional integration (OpenAI, NOOA, Jev, MCP writes, external compute).
- **Testing pyramid:** engine unit tests (pure, fast) → service tests on real
  PostgreSQL → E2E lab suite (`test_e2e_lab_run.py`) → benchmark regression
  (Track R) → Playwright for Studio flows. Deterministic fakes for every LLM/agent.
- **Change budget** per PR ≈ 800 lines / 20 files / ≤ 1 migration
  (`prompts/EXECUTION_STANDARD.md` §4), evidence recorded once in STATUS.md.

### 3.2 Database

Kept exactly as built (verified in code, `db/integrity.py`, `db/evidence_lock.py`):

- Workspace is the tenant boundary; composite FKs `(workspace_id, x_id) →
  parent(workspace_id, id)` (129 today) make cross-tenant references impossible.
- Immutable rows: `datasets`, `model_versions`, `model_selection_decisions`;
  lock-immutable: `problem_specs`, `feature_set_versions`, `workflow_versions`,
  `pipeline_versions`, `pipeline_scientific_plans`; append-only:
  `ml_run_events`, `data_access_events`, `ingestion_publication_events`,
  `dataset_policy_revisions`; evidence lock freezes 13 run-evidence tables.
- Migration policy: discover live head (currently `0061_ingestion_publication`),
  additive expand → backfill → enforce → contract; never edit applied revisions;
  `alembic_frozen/` helpers keep old revisions stable; forward repair over
  downgrade (ADR 0004).
- Bodies in object storage, metadata + digests in PostgreSQL; tenant-prefixed
  immutable object keys; money as integer micro-units + currency.

New rules for the state graph:

- **Nodes are immutable versions; refs are the only mutable pointers.**
  Like git: a `project_refs` row says "current ProblemSpec / DatasetVersion /
  SplitPlan / FeatureRecipe / champion ModelVersion for project P". Changing a
  ref is a recorded decision; nodes are never edited.
- **Staleness is computed, not stored**: a node is stale when any upstream node
  it was built from is no longer the current ref (or has a newer locked version).
- **Experiments reference a locked SplitPlan.** All experiments in a lineage
  share the same holdout, so comparisons are valid and the improve loop can
  never peek at a fresh holdout.

Fix list (Phase 0): resolve the metadata cycle warning by marking the two
duplicate single-column FKs `ingestion_runs.execution_request_id` and
`execution_requests.pipeline_run_id` `use_alter=True` (or dropping them; the
composite FKs already cover them); add CFKs to `workflow_run_inputs`,
`experiment_test_predictions`, `ml_run_verifications`.

### 3.3 Infrastructure

| Stage | Topology |
| --- | --- |
| Local dev | Docker Compose: `postgres`, `migrate`, `api`, **`worker`** (new), `web`; local object storage dir |
| CI | GitHub Actions: Postgres service, locked deps, migrations, truth drift, pytest, web tsc/lint/build, Playwright; nightly benchmark |
| Hosted beta (Phase 8) | **One** cloud (choose GCP or AWS once a design partner needs it): managed PostgreSQL, object storage, secret manager, container service for api/web/worker; OpenTofu; OTel |
| Later | Kubernetes, second cloud, external GPU (SkyPilot), sandbox lane for generated code |

Provider-neutral ports stay (`storage/`, future `ComputePort`), so the second
cloud is an adapter, not a rewrite. Dual-cloud certification is not an MVP gate.

Dependency hygiene: pinned upper bounds for SQLAlchemy/pandas/sklearn, one
lockfile (`uv.lock`), `psycopg[binary]` v3 driver, test-only packages in a `dev`
extra, Node version unified.

### 3.4 Security (kept from Scope 0, verified locally)

HttpOnly opaque session + BFF (ADR 0001), CSRF HMAC + Origin + CSP + login
throttle (ADR 0002), `X-Workspace-Id` as selector re-checked per request, 403
bad selector / 404 foreign resource (ADR 0003), tenant-scoped legacy simulation
(ADR 0004). New in MVP: **service tokens** for SDK/MCP (hashed, scoped to
workspace + capability set, revocable, expiring); MCP read and write kill
switches; agent runs never receive DB sessions, storage clients or provider keys
beyond the trusted worker's own gateway.

## 4. The ML state graph

| Node (domain name) | Physical owner today | Status | Notes |
| --- | --- | --- | --- |
| Project | `projects` | exists | root of refs |
| ProblemSpec | `problem_specs` | exists | add `objective` block: primary metric override, constraints (e.g. precision ≥ 0.70), cost weights, prediction moment |
| DatasetVersion | `datasets` (+ `dataset_assets`, `dataset_columns`) | exists | immutable, digested |
| SplitPlan | `pipeline_scientific_plans` (per run) | **promote** | new project-scoped `split_plans` (strategy, seed, group/time cols, holdout row-assignment artifact digest); experiments reference it |
| FeatureRecipe | `feature_set_versions` + `features`/`feature_transformations`/`feature_lineage` | exists | each transform carries reason + code snippet |
| Experiment | `experiments` (old docs call it PipelineRun) | exists | **canonical name = Experiment.** Add `parent_experiment_id` use, `change_set` (typed diff vs parent), `split_plan_id`, `intent`. `workflow_runs` = execution envelope; `ml_workflows`/`workflow_versions` = templates |
| Candidate / Fold | `experiment_candidates`, `model_hyperparameters`, `cv_fold_runs` | exists | add dummy baseline candidate |
| Evaluation | `model_evaluations`, `evaluation_metrics`, `experiment_test_predictions` | exists | holdout scope only for locked winner |
| ModelSelection | `model_selection_decisions` | exists | CV-only, immutable |
| ModelVersion | `model_assets`, `model_versions` | exists | safe package format in Phase 7 |
| Finding (verify) | `data_quality_findings`, `ml_run_verifications` | exists | generalize to investigation findings (Phase 5) |
| ProjectDecisionRecord | — | **new** (Phase 2) | proposed/accepted/rejected/superseded; subject node, rationale, evidence refs, actor (human/agent/rule) |
| ProjectRef | — | **new** (Phase 2) | mutable pointer per node kind |
| AgentRun / AgentProposal | — | **new** (Phase 6, runs after Phase 4 Stage 1) | see AGENTS_NOOA_JEV.md |
| ModelRelease, BatchPrediction, MonitoringWindow | — | **new** (Phase 7) | batch-first operations |

Graph edges are the existing FKs; the lifecycle projection
(`GET /v1/projects/{id}/graph`) assembles nodes, edges, refs and stale flags.
Not a graph database (CORE_CONCEPT §3:101-118).

## 5. Scientific invariants (unchanged, enforced in code and tests)

From `DCLAB_ADAPTIVE_MODEL_BUILDER.md` and its correctness companion:
holdout plan first (stratified / random / group-disjoint / temporal-future;
group+temporal fails closed); profile, leakage audit and plans on the locked
train partition only; fit-dependent preprocessing inside per-fold Pipelines;
name or correlation alone never excludes a feature; CV-only winner lock, then a
single winner-only holdout evaluation; accuracy drops from removing leakage are
corrections; every run reports "LLM used: yes/no". New: a dummy baseline in
every portfolio; thresholds and constraints tuned on validation folds only;
ProblemSpec objective may override the default metric (PR-AUC/MAE) with a
recorded reason.

## 6. Keep / refactor / freeze map

| Area | Decision |
| --- | --- |
| Open-ingest runner (`runner.py:98-1215`), `engine/modeling/*`, `engine/lab/*`, profiler, search, metrics | **Keep** — core engine |
| Legacy runner branch (`runner.py:1258-1703`), `engine/experiments/factory.py`, `app/ml/*` | **Delete** after admin Lab moves to open-ingest (P1.1–P1.2); move `ml/ensemble`, `ml/selection` into `engine/` first |
| `app/sim/*`, opportunities/predictions/decisions/simulation/insights services + `/app` pages | **Freeze** (Decision.ai vertical): frozen now (2026-10-01) — no new features, mandatory tenant filtering, keep tests green, hide behind flag in Studio IA; removal-or-rework decision at the Phase 9 business-layer rework; hard deadline: removed or formally re-homed by 2027-03-31 (see [S0-P04D gate](../verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md)) |
| Lineage, scientific lineage, evidence lock, model_build*, reproducibility, pipeline_verifier, explorers | **Keep** — state-graph writers and Studio read models |
| `engine/serving/artifacts.py` and every `REPO_ROOT/data|artifacts` write | **Refactor** to `ObjectStorage` (P1.3) |
| `llm_client.py`, `openai_provider.py`, `llm_invocations` | **Refactor** into one LLM gateway with provider interface and nullable run FKs (P2.2, P6.2-B, which absorbs Track A's A2-A) — Phase 6 runs after Phase 4 Stage 1 |
| `/v1`, `packages/dclab_client`, `execution_requests`, `ml_jobs`, job handlers, worker CLI | **Keep and extend** — base of SDK/CLI/MCP |
| Auth/session/CSRF/BFF/workspace/capabilities | **Keep** |
| `lab_decision_records`, `dataset_profiles`, `prediction_tasks`, `environments`, `client_lab_runs*` | **Freeze**, drop in a later contract migration |
| `docs/agentic-program/**` | **Paused reference**; designs reused per prompt citation |
