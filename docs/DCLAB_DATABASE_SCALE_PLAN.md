# DCLab database scale plan

This is the Prompt 8 scale and integrity plan. It records expected growth, when
to partition, how blobs leave PostgreSQL, and which JSONB columns stay JSONB.
It does **not** partition MVP tables.

Application workspace authorization (`authorization_service` + request
dependencies) remains the authoritative tenant gate. PostgreSQL now also
rejects cross-workspace FKs on the canonical hierarchy and freezes locked /
published scientific rows. Those are integrity controls, not a substitute for
request-level authorization.

## Expected cardinalities (MVP → first serious customer load)

Estimates, not observed production counts. “Per workspace” assumes one active
ML project unless noted.

| Table / class | MVP (single tenant dogfood) | 12-month serious load | Growth driver |
| --- | --- | --- | --- |
| `workspaces` | 1–10 | tens–low thousands | customer count |
| `projects` / `problem_specs` | tens | low thousands | projects per tenant |
| `ml_workflows` / `pipelines` / versions | tens | thousands | workflow catalog; versions stay small (lock, then new row) |
| `workflow_runs` | tens–hundreds | 10⁴–10⁵ | every Labs/API invocation |
| `experiments` (PipelineRun) | tens–hundreds | 10⁴–10⁵ | one or more pipelines per workflow run |
| `pipeline_stage_runs` | hundreds | 10⁶ | ~10–30 stages × pipeline runs |
| `datasets` / `dataset_columns` | tens / hundreds | 10⁴ datasets; 10⁵–10⁶ columns | ingest + new physical versions |
| `artifacts` metadata | hundreds | 10⁵–10⁶ | every blob; bytes are **not** in Postgres |
| `experiment_candidates` | hundreds | 10⁶ | search width × completed runs |
| `cv_fold_runs` | hundreds | 10⁶–10⁷ | candidates × folds (typically 5) |
| `evaluation_metrics` | hundreds | 10⁶ | evaluations × named metrics |
| `model_versions` | tens | 10⁴ | published winners only |
| `ml_run_events` | thousands | 10⁷–10⁸ | stage timeline; highest write rate today |
| `llm_invocations` | hundreds | 10⁵–10⁶ | only when a decision agent actually runs |
| `client_lab_run_audits` / lab decision records | hundreds | 10⁵ | Labs uploads |
| prediction / scoring events | not a first-class table yet | future 10⁷+ | online scoring (see partition candidates) |

## Growth drivers

1. **Observability volume** — `ml_run_events` is append-only and grows with every
   real stage. This is the first table that can outrun a single heap.
2. **Search width** — candidates, hyperparameters, CV folds, and evaluation
   metrics grow with AutoML breadth, not with customer count.
3. **Ingest versions** — each physical `datasets` row is immutable; reruns add
   versions, columns, and artifacts rather than updating in place.
4. **Object store** — model binaries, source zips, lockfiles, CSVs. Postgres
   only stores `artifacts` registry rows (digest, key, size).
5. **Future scoring** — per-row prediction events are not modeled as a hot
   table yet. Do not invent that table until a product path needs it.

## Partition threshold (do not partition yet)

Partition a table only when **all** of the following are true:

- Sustained size **≥ 50 million rows** or **≥ 20 GB** heap+TOAST, **and**
- A clear partition key that matches the dominant delete/retention axis
  (`created_at` month or `workspace_id` hash), **and**
- EXPLAIN on the live list/detail queries shows sequential or bitmap scans
  that composite indexes cannot fix, **or** VACUUM/autovacuum cannot keep up.

Until then: composite btree indexes (this revision) plus retention jobs.

**Do not** partition `projects`, `problem_specs`, workflow/pipeline definition
tables, `model_versions`, or other small catalog tables because future volume
*might* be large.

### Partition candidates (future)

| Table | Likely key | Why wait |
| --- | --- | --- |
| `ml_run_events` | `RANGE (created_at)` monthly, or `HASH (workspace_id)` | Highest write rate; unique `(experiment_id, sequence)` must stay inside a partition scheme |
| `llm_invocations` | `RANGE (created_at)` | Lower than events; partition only after events is proven |
| `cv_fold_runs` / `evaluation_metrics` | `HASH (workspace_id)` or parent id | Large but still index-friendly at MVP |
| prediction events (future) | time + workspace | Do not create or partition until the product writes them |
| audit / lab decision ledgers | `RANGE (created_at)` | After a documented retention policy exists |

## Retention strategy

- **Canonical scientific rows** (`datasets`, locked versions, published
  `model_versions`, winner `model_selection_decisions`): retain for the life of
  the workspace. Do not expire; they are the audit trail.
- **`ml_run_events`**: keep ≥ 90 days hot. After that, either archive to object
  storage (JSON/Parquet keyed by `experiment_id`) or drop old partitions once
  partitioned. Do not silently DELETE while the append-only trigger exists —
  retention must use `TRUNCATE` of a detached partition or a superuser-held
  procedure, never row `DELETE`.
- **`llm_invocations`**: same window as events unless compliance requires longer.
- **Labs audit JSON**: keep with the upload; do not duplicate into a second
  unbounded table.
- **Workspace offboarding**: `TRUNCATE … CASCADE` (or drop the tenant database
  if you isolate that far). Row `DELETE` of a workspace will fail on immutable
  children; that is intentional.

## Object storage strategy

- Bytes live in the local/S3/GCS provider behind `apps/api/app/storage/`.
- `artifacts` is the registry: `workspace_id`, `object_key`, `content_digest`,
  `size_bytes`, `artifact_type`.
- Large payloads (CSV, model pickle/joblib, engine zip, lockfile, reports)
  **must** be artifacts. Do not put file bodies in JSONB or BYTEA.
- Lifecycle: hot bucket/prefix for 30–90 days, then storage-class transition;
  Postgres metadata stays. Deleting an artifact row without deleting the object
  is an orphan; deleting the object without the row is a broken lineage pointer.
  Retention jobs must do both, workspace-scoped.

## Read replica strategy

- **MVP:** one primary. Explorer and admin list queries are tenant-scoped and
  use the new `(workspace_id, created_at DESC)` / status composites.
- **When to add a replica:** read QPS on explorer/list endpoints saturates
  primary I/O, or reporting jobs conflict with ingest/train writes.
- Replicas are for **read-only** explorer, dashboards, and `alembic` is never
  pointed at a replica.
- Replication lag: do not serve “run just completed” detail off a replica
  without a primary fallback. Completions are read-your-writes.

## PgBouncer recommendation

- Use PgBouncer in **transaction** pooling for the API (`pool_mode=transaction`).
- Session pooling only for the small set of connections that need session
  state (admin `psql`, one-off migrations).
- Default pool: `default_pool_size` sized to `(CPU cores on Postgres) * 2` from
  the app, with PgBouncer `max_client_conn` much higher. Do not open one
  SQLAlchemy pool per uvicorn worker at 20 without a bouncer in front.
- SQLAlchemy: `pool_pre_ping=True` (already used in tests); `pool_reset_on_return=rollback`.
- **Do not enable PostgreSQL RLS** while the API uses transaction pooling unless
  `SET` of `app.workspace_id` (or equivalent) is done **inside the same
  transaction** as the queries and is cleared on release. Transaction pooling
  reuses backends across tenants; a leftover GUC would be a class of
  cross-tenant bugs. That is why RLS is not in this revision.

## Backup / restore expectations

- **Primary:** daily full backup + continuous WAL archiving (or managed
  equivalent: PITR window ≥ 7 days).
- Restore drill: restore to a scratch instance, `alembic current` equals
  production head, `alembic check` clean, one tenant explorer query matches.
- Object store is **not** in the Postgres dump. Backup the bucket with the same
  PITR window, or accept that artifact bytes can be older/newer than metadata.
- `decisionai_test` / ephemeral integrity databases are not backed up.
- Immutable triggers do not affect `pg_dump` / restore.

## JSONB policy audit

Rule:

- **Frequently filtered/joined scientific facts → columns or normalized tables.**
- **Variable provider/algorithm evidence → JSONB.**
- **Large payload/file → Artifact / object storage.**

Do not normalize low-value fields merely to increase table count.
`workflow_run_inputs`, `feature_lineage`, `model_hyperparameters`, and
`evaluation_metrics` stay without a redundant `workspace_id` because they hang
off already tenant-checked parents.

| Location | Policy |
| --- | --- |
| `evaluation_metrics.metric_name` / `metric_value` | Column. Named scores are listed and compared. |
| `model_hyperparameters.parameter_name` / `value_json` | Name is a column; **value** stays JSONB (type varies by algorithm). |
| `cv_fold_runs` fold identity | Columns (`fold_number`, row counts, status). |
| `dataset_columns` stats | Searchable facts are columns; leftover histogram blobs stay `stats` JSONB. |
| `data_quality_findings.finding_type` / `severity` | Columns; `evidence` JSONB. |
| `data_preparation_decisions.strategy` / `decision_type` | Columns; `parameter_value` / `evidence` JSONB. |
| `experiment_candidates.payload` | Compatibility dump of the search row. Authoritative fields are columns (`model_family`, `fingerprint`, …). |
| `model_versions.metrics` | Compatibility blob. Prefer `model_evaluations` + `evaluation_metrics` for query. |
| `experiments.config` / `result` | Execution envelope. Stage/candidate facts are normalized elsewhere. |
| `workflow_versions.definition`, `pipeline_versions.graph_definition` | Version snapshot JSON; frozen after lock. |
| `ml_run_events.payload` | Bounded event detail; not a file. |
| `llm_invocations.safe_output` / `final_decision` | Provider-shaped evidence. |
| `artifacts.metadata` | Small registry extras. Bytes are in object storage. |
| `problem_specs.constraints` / `success_criteria` | Variable intent documents; status/version/task_type are columns. |
| `runtime_environments.hardware` | Heterogeneous machine facts. RuntimeEnvironment is a global fingerprint; dependency-lock Artifact ids are workspace-scoped on CodeSnapshot. |

GIN indexes on JSONB are **not** justified at MVP. Add an expression GIN only
after a concrete predicate (e.g. `payload ->> 'event_type'`) shows up in
EXPLAIN as a filter on a large table.

## Indexes added or kept (Prompt 8)

Measured for tenant list/detail. PostgreSQL can scan btree backwards, but
`created_at DESC` is declared where list pages always sort newest-first.

Added:

- `experiments`: `(workspace_id, created_at DESC)`, `(workspace_id, status, created_at DESC)`, `(project_id, created_at DESC)`, `(workflow_run_id, created_at)`
- `workflow_runs`: `(workspace_id, created_at DESC)`, `(workspace_id, status, created_at DESC)`, `(project_id, created_at DESC)`
- `datasets`: `(workspace_id, created_at DESC)`, `(project_id, created_at DESC)`
- `ml_run_events`: `(workspace_id, created_at DESC)`, `(workflow_run_id, created_at)`
- `llm_invocations`: `(workspace_id, created_at DESC)`
- `client_lab_uploads`: `(workspace_id, created_at DESC)`, `(workspace_id, pipeline_status, created_at DESC)`

Already sufficient (unique, not duplicated):

- `(pipeline_run_id, sequence)` → `uq_pipeline_stage_runs_run_sequence`
- `(candidate_id, fold_number)` → `uq_cv_fold_runs_candidate_fold`
- `(model_evaluation_id, metric_name)` → `uq_evaluation_metrics_evaluation_name`
- `(model_asset_id, version)` → `uq_model_versions_asset_version`
- `(experiment_id, sequence)` → `uq_ml_run_events_experiment_sequence` (events by pipeline run)

Removed as prefix-redundant after EXPLAIN (unique or composite already covers
the leading column): standalone `workspace_id` on several of the tables above,
`ix_pipeline_stage_runs_pipeline_run_id`, `ix_cv_fold_runs_candidate_id`,
`ix_evaluation_metrics_evaluation_name`, `ix_model_hyperparameters_candidate_id`,
`ix_ml_run_events_experiment_id`.

## PostgreSQL RLS (optional future)

Row-level security is **not** enabled in this revision.

Current pooling (SQLAlchemy pool today, PgBouncer transaction pooling when
introduced) does not safely carry a per-tenant session GUC across checkouts.
A forgotten `SET app.current_workspace_id` would leak rows or hide them.

When RLS is considered later:

1. Switch the API to **session** pooling **or** set and `RESET` the tenant GUC
   inside every request transaction (never at connect time only).
2. `FORCE ROW LEVEL SECURITY` on tenant tables with
   `workspace_id = current_setting('app.current_workspace_id')::uuid`.
3. Keep application authorization as the primary control; RLS is
   defense-in-depth against a missed filter in a new query.
4. Superuser/migration roles bypass RLS; backups and `alembic` need an explicit
   policy.

Until that pooling model exists, do not add RLS.

## Entitlement semantics

`workspace_entitlements.max_ml_engineer_seats` is the Business technical-seat
cap (default 5). It counts canonical `ml_engineer` memberships only. Owner,
admin, and viewer rows do not consume it.

`workspace_entitlements.max_members` is a separate overall membership cap when
present. Personal default is 1. Business does not seed `max_members`.

## Prompt 9 query benchmark

Generator: `scripts/benchmark_canonical_queries.py`. Not invoked by CI.
`--profile large` is ~100k users; `--profile xl` is ~1M users.

Measured statements:

1. Workspace project list: `projects` by `workspace_id` ordered by `created_at DESC`
2. Recent pipeline runs: `experiments` by `workspace_id` ordered by `created_at DESC`
3. Pipeline detail: `experiments` by `id` + `workspace_id`
4. Candidate comparison: `experiment_candidates` by `experiment_id` + `workspace_id`
5. Model registry: `model_versions` by `workspace_id` ordered by `created_at DESC`
6. Admin cross-tenant recent failures: `experiments` where `status = 'FAILED'` ordered by `created_at DESC`

Expected index use (from 0035):

| Query | Expected plan |
| --- | --- |
| project list | `ix_projects_workspace_created_at` |
| recent pipeline runs | `ix_experiments_workspace_created_at` |
| pipeline detail | PK `experiments_pkey` |
| candidate comparison | `uq_experiment_candidates_experiment_fingerprint` leading `experiment_id`, or workspace unique |
| model registry | sequential until volume; add `(workspace_id, created_at DESC)` only after EXPLAIN shows a seq scan at serious load |
| recent failures | `ix_experiments_workspace_status_created_at` is workspace-prefixed, so a global failure list may still seq-scan until partitioned or a `(status, created_at DESC)` index is justified |

Smoke-profile `EXPLAIN ANALYZE` (2 workspaces, 8 users, 4 projects, 8 runs)
was captured on an isolated Alembic-head database. PostgreSQL chose sequential
scans for every measured statement because the heaps are tiny (planner cost of
an index lookup exceeds a 4–24 row seq scan). That is expected. Do not add
indexes to “fix” smoke seq scans.

| Query | Smoke plan | Index that should win at serious load |
| --- | --- | --- |
| project list | Seq Scan + sort on `projects` | `ix_projects_workspace_created_at` |
| recent pipeline runs | Seq Scan + sort on `experiments` | `ix_experiments_workspace_created_at` |
| pipeline detail | Seq Scan on `experiments` PK filter | `experiments_pkey` |
| candidate comparison | Seq Scan + sort on `experiment_candidates` | `uq_experiment_candidates_experiment_fingerprint` |
| model registry | Seq Scan + sort on `model_versions` | none yet; add `(workspace_id, created_at DESC)` only after volume |
| recent failures | Seq Scan filter `status = FAILED` | global `(status, created_at DESC)` still not justified; workspace-prefixed `ix_experiments_workspace_status_created_at` does not serve this admin query |

Do not treat smoke timings as production SLOs. `--profile large` / `xl` remain
optional and are not CI.

