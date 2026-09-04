# DCLab Database Architecture Verification

## Scope and evidence

This document covers the 25 mission tables in PostgreSQL and cross-checks them against `apps/api/app/db/models.py`.

Evidence collected on 2026-09-04 from `postgresql://localhost:55432/dclab_verify_empty`:

- The database initially had zero tables and upgraded successfully through repository head `0027_repair_tenant_lineage`.
- The forward 0027 repair corrects unambiguous legacy dataset, dataset-asset, and experiment workspace ownership inferred from `client_lab_uploads`; unlinked or ambiguous rows remain assigned to the legacy default workspace.
- Catalog evidence came from `information_schema.columns`, `pg_constraint`, `pg_indexes`, `pg_class`, `pg_partitioned_table`, `pg_trigger`, and `pg_stat_user_tables`.
- A direct Alembic `compare_metadata()` comparison between this physical schema and SQLAlchemy `Base.metadata` reported `DIFF_COUNT 0`.
- `alembic current` reports `0027_repair_tenant_lineage (head)` and `alembic check` reports no new upgrade operations.
- All requested tables have PostgreSQL row-level security disabled and are ordinary, non-partitioned heap tables. The only non-internal trigger is the append-only trigger on `ml_run_events`.
- The database is nearly empty, so plain `EXPLAIN` confirms index eligibility, not production selectivity, latency, cache behavior, or runtime PASS.

Notation below: **NO ACTION** is PostgreSQL's default FK delete action; “NN” means `NOT NULL`; listed secondary indexes exclude the PK index. Expected cardinality is a design estimate, not an observed production count.

## Identity, tenant, and domain tables

### `workspaces`

- **Purpose / ownership / cardinality:** Canonical tenant root. One row per customer workspace; expected tens to thousands.
- **Key and relationships:** PK `id uuid`; no FKs. Unique `slug`.
- **Indexes:** unique `ix_workspaces_slug(slug)`.
- **Nullability:** all columns NN: `id`, `slug`, `name`, `created_at`.
- **Mutation / volume:** Mutable identity row; no database immutability guard. `created_at` is the main list/sort column; the table itself should remain comparatively small.

### `business_profiles`

- **Purpose / ownership / cardinality:** Optional one-to-one business metadata for a workspace; zero or one row per workspace.
- **Key and relationships:** PK and FK `workspace_id -> workspaces.id ON DELETE CASCADE`; the PK enforces one profile per tenant.
- **Indexes:** PK only.
- **Nullability:** `legal_name`, `industry` nullable; `workspace_id`, `profile_data`, `created_at`, `updated_at` NN.
- **Mutation / volume:** Mutable. `profile_data jsonb` may become wide; updates rewrite the row/TOAST value. ORM supplies `{}` in Python, but the physical column has no server default.

### `users`

- **Purpose / ownership / cardinality:** Login identity plus legacy role/workspace scope. Expected tens to hundreds per workspace and a small platform-wide administrator set.
- **Key and relationships:** PK `id`; nullable FK `workspace_id -> workspaces.id NO ACTION`. Unique email is implemented as unique index `ix_users_email(email)`. Checks constrain `role` and require a workspace only for legacy `client_user`.
- **Indexes:** unique email; `role`; `workspace_id`.
- **Nullability:** only `workspace_id` nullable; all other columns NN.
- **Mutation / volume:** Mutable credentials and activation state. Tenant authorization is now membership-based, so `users.workspace_id` is compatibility state and can disagree with memberships unless application logic prevents it.

### `platform_memberships`

- **Purpose / ownership / cardinality:** Platform-level DCLab role, independent of a tenant; at most one per user and expected to be very small.
- **Key and relationships:** PK `id`; FK `user_id -> users.id ON DELETE CASCADE`; unique `user_id`; check allows only `dclab_admin` and `dclab_developer`.
- **Indexes:** unique `uq_platform_memberships_user_id(user_id)`; `role`.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable role assignment; low volume.

### `workspace_memberships`

- **Purpose / ownership / cardinality:** Many-to-many user-to-workspace authorization with a business role; typically one to a few memberships per user and tens to hundreds per workspace.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id CASCADE`, `user_id -> users.id CASCADE`; unique `(workspace_id, user_id)`; role check allows business admin/developer.
- **Indexes:** unique `(workspace_id,user_id)` plus separate `workspace_id`, `user_id`, and `role`.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable authorization state. Existing indexes support membership lookup in either direction; the standalone `workspace_id` index partly duplicates the unique index's left prefix.

### `workspace_capabilities`

- **Purpose / ownership / cardinality:** Per-tenant feature flags and configuration; expected zero to dozens per workspace.
- **Key and relationships:** PK `id`; FK `workspace_id -> workspaces.id CASCADE`; unique `(workspace_id, capability)`.
- **Indexes:** unique `(workspace_id,capability)` plus `workspace_id` and `capability`.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable. `configuration jsonb` is potentially wide and has no GIN index; querying inside it will scan/filter unless a targeted expression index is added. ORM's `{}` is Python-side, not a server default.

### `business_domains`

- **Purpose / ownership / cardinality:** Global catalog of domains such as labs, marketing, or sales; expected single digits to low hundreds.
- **Key and relationships:** PK `id`; no FK. Unique slug is implemented by `ix_business_domains_slug(slug)`.
- **Indexes:** unique slug.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable global reference data. `default_config jsonb` can grow but cardinality is low; no JSONB index is justified unless content predicates become common.

### `workspace_domains`

- **Purpose / ownership / cardinality:** Enables/configures a global business domain for one workspace; bounded by workspaces × domain catalog.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id NO ACTION`, `business_domain_id -> business_domains.id NO ACTION`; unique `(workspace_id,business_domain_id)`.
- **Indexes:** unique pair plus separate indexes on both FKs.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable. Tenant-owned directly by `workspace_id`; `config jsonb` is unindexed. NO ACTION means deleting a workspace/domain is blocked while links exist, unlike membership/capability cleanup.

## Data ingestion and dataset lineage

### `client_lab_uploads`

- **Purpose / ownership / cardinality:** Tenant upload and coarse/fine pipeline state; expected unbounded growth, potentially thousands to millions per workspace.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id NO ACTION`, nullable `requested_by -> users.id NO ACTION`, nullable `experiment_id -> experiments.id NO ACTION`, nullable `dataset_id -> datasets.id SET NULL`. `run_id` has a unique index. `client_status` is checked to queued/processing/completed/failed.
- **Indexes:** unique `run_id`; `workspace_id`, `category`, `pipeline_status`, `client_status`.
- **Nullability:** nullable `requested_by`, `pipeline_log`, `experiment_id`, `dataset_id`, `explicit_target_column`; all other columns NN.
- **Mutation / volume:** Mutable execution shell. High-volume/wide fields are `fields_noticed jsonb`, `pipeline_log jsonb`, path/name strings, statuses, and `created_at`. ORM events force `run_id=id` and synchronize `client_status`, but PostgreSQL has no equivalent trigger/check; direct SQL can violate both invariants. Tenant-scoped recent/status queries lack a composite `(workspace_id, status, created_at)` index.

### `dataset_assets`

- **Purpose / ownership / cardinality:** Logical tenant dataset; immutable physical versions are child `datasets`. Expected hundreds to thousands per workspace.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id NO ACTION`, nullable `created_by -> users.id SET NULL`; unique `(workspace_id,slug)`.
- **Indexes:** unique `(workspace_id,slug)` plus `workspace_id`.
- **Nullability:** only `created_by` nullable.
- **Mutation / volume:** Mutable catalog row. `updated_at` is maintained by SQLAlchemy `onupdate`, not by a database trigger. The separate workspace index duplicates the unique index's left prefix for many plans.

### `datasets`

- **Purpose / ownership / cardinality:** Physical dataset version and lineage metadata; expected one to tens/hundreds of versions per asset and potentially large total counts.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id`, `dataset_asset_id -> dataset_assets.id`, `environment_id -> environments.id`, all NO ACTION. Unique `(dataset_asset_id,version)` and `(dataset_asset_id,content_digest)`.
- **Indexes:** both unique pairs plus `workspace_id`, `dataset_asset_id`, `environment_id`, and `content_digest`.
- **Nullability:** `content_digest` and `schema_json` nullable; all others NN. PostgreSQL permits multiple NULL digests under the unique pair.
- **Mutation / volume:** SQLAlchemy rejects update/delete, but PostgreSQL has no trigger; immutability is bypassable through SQL, bulk operations, or another client. Wide/high-volume fields are `schema_json`, `location`, row/column counts, and digest. Direct `workspace_id` is not constrained to match the parent asset's workspace. The standalone asset/digest indexes overlap composite indexes.

### `dataset_profiles`

- **Purpose / ownership / cardinality:** Profiling snapshot for a dataset; expected one or a small number per version, but no uniqueness rule limits this.
- **Key and relationships:** PK `id`; FK `dataset_id -> datasets.id NO ACTION`.
- **Indexes:** `dataset_id`.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable and indirectly tenant-owned through dataset. `stats jsonb` is likely large/TOASTed; selecting profiles in list views can cause heavy I/O. Add a version/profile-kind uniqueness rule if only one current profile is intended.

### `prediction_tasks`

- **Purpose / ownership / cardinality:** Prediction task specification attached to a legacy/global environment; expected tens to hundreds per environment.
- **Key and relationships:** PK `id`; FK `environment_id -> environments.id NO ACTION`; no unique constraint.
- **Indexes:** `environment_id`, non-unique `slug`.
- **Nullability:** only `config_path` nullable.
- **Mutation / volume:** Mutable; ownership is indirect through `environments.org_id`, not a workspace FK. `spec jsonb` may be wide. If slugs identify tasks within an environment, missing unique `(environment_id,slug)` permits ambiguity.

## Workflow and experiment execution

### `ml_workflows`

- **Purpose / ownership / cardinality:** Reusable tenant business objective/configuration, not an execution; expected tens to hundreds per workspace.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id`, `workspace_domain_id -> workspace_domains.id` (both NO ACTION), nullable `created_by -> users.id SET NULL`; unique `(workspace_id,slug)`.
- **Indexes:** unique pair plus `workspace_id`, `workspace_domain_id`, and `status`.
- **Nullability:** only `created_by` nullable.
- **Mutation / volume:** Mutable. `config jsonb`, descriptions, and objectives are wide; `updated_at` is ORM-maintained only. Nothing enforces that `workspace_domain_id` belongs to the same `workspace_id`.

### `workflow_runs`

- **Purpose / ownership / cardinality:** One workflow invocation; append-like operational history with potentially millions of rows.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id NO ACTION`, `workflow_id -> ml_workflows.id NO ACTION`, nullable `requested_by -> users.id SET NULL`, nullable `source_upload_id -> client_lab_uploads.id SET NULL`.
- **Indexes:** `workspace_id`, `workflow_id`, `source_upload_id`, `status`.
- **Nullability:** nullable `requested_by`, `source_upload_id`, `explicit_target`, `resolved_target`, `task_type`, `failure_reason`, `started_at`, `completed_at`; remaining columns NN.
- **Mutation / volume:** Mutable state machine. High-volume columns are statuses/timestamps and failure text. Nothing enforces tenant consistency across workflow/upload/workspace. `EXPLAIN` for workspace + status + recent-first used the status index, filtered workspace, then sorted; add `(workspace_id,status,created_at DESC)` for the common queue/history path.

### `workflow_run_inputs`

- **Purpose / ownership / cardinality:** Ordered dataset inputs for a run; usually one to a small handful per run.
- **Key and relationships:** PK `id`; FKs `workflow_run_id -> workflow_runs.id CASCADE`, `dataset_id -> datasets.id NO ACTION`; unique `(workflow_run_id,dataset_id,input_role)`.
- **Indexes:** unique triple plus separate `workflow_run_id`, `dataset_id`, `input_role`.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable child rows, indirectly tenant-owned through both parents. No cross-table rule ensures both parents share a workspace. The run index duplicates the unique index's left prefix.

### `experiments`

- **Purpose / ownership / cardinality:** One pipeline/training execution; normally one to a few per workflow run, with unbounded historical growth.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id`, nullable `workflow_run_id -> workflow_runs.id`, `environment_id -> environments.id`, nullable `task_id -> prediction_tasks.id`, `dataset_id -> datasets.id`, all NO ACTION. Unique `(workflow_run_id,pipeline_index)`; multiple NULL workflow runs are allowed.
- **Indexes:** unique pair plus `workspace_id`, `workflow_run_id`, `environment_id`, `task_id`, `dataset_id`, and `status`.
- **Nullability:** nullable `workflow_run_id`, `task_id`, `failure_reason`, `result`, `artifact_dir`, `git_commit`, `started_at`, `ended_at`; all others NN.
- **Mutation / volume:** Mutable execution state. `config` and `result` JSONB and failure text are wide. Direct tenant ownership is not checked against workflow run or dataset. Tenant/status/time dashboards need composites such as `(workspace_id,status,created_at DESC)`.

### `experiment_candidates`

- **Purpose / ownership / cardinality:** Generated model/pipeline candidates; expected several to hundreds per experiment.
- **Key and relationships:** PK `id`; FK `experiment_id -> experiments.id NO ACTION`; no unique constraint on candidate key or fingerprint.
- **Indexes:** `experiment_id`, `fingerprint`.
- **Nullability:** all columns NN.
- **Mutation / volume:** Mutable and indirectly tenant-owned. `payload jsonb` may dominate storage. If retries can regenerate a candidate, absence of `(experiment_id,fingerprint)` uniqueness allows duplicates.

### `experiment_test_predictions`

- **Purpose / ownership / cardinality:** Holdout prediction per test row; the largest natural fact table, scaling with test rows across all experiments.
- **Key and relationships:** PK `id`; FK `experiment_id -> experiments.id CASCADE`; unique `(experiment_id,row_index)`.
- **Indexes:** unique pair plus `experiment_id` and global `source_row_index`.
- **Nullability:** `source_row_index`, `probability`, and `y_true` nullable; all others NN.
- **Mutation / volume:** Mutable physically; indirectly tenant-owned. High-volume fields are `record_id`, JSONB `predicted_value`/`y_true`, probability, and row indexes. `EXPLAIN` for experiment-ordered paging used the unique composite index. The separate experiment index is redundant for left-prefix lookups; global `source_row_index` may have weak selectivity and cross-tenant semantics.

## Model registry

### `model_assets`

- **Purpose / ownership / cardinality:** Logical managed model under a workflow; expected tens to hundreds per workspace.
- **Key and relationships:** PK `id`; FKs `workspace_id -> workspaces.id`, `workflow_id -> ml_workflows.id` (NO ACTION), nullable `created_by -> users.id SET NULL`; unique `(workspace_id,slug)`.
- **Indexes:** unique pair plus `workspace_id`, `workflow_id`, `status`.
- **Nullability:** only `created_by` nullable.
- **Mutation / volume:** Mutable catalog row; `updated_at` is ORM-maintained only. No constraint ensures the workflow belongs to the same workspace.

### `model_versions`

- **Purpose / ownership / cardinality:** Selected, append-only model release with complete lineage; expected one to many versions per asset, usually much smaller than run/event tables.
- **Key and relationships:** PK `id`; NO ACTION FKs to `model_assets`, `workspaces`, `ml_workflows`, `workflow_runs`, `experiments` via `pipeline_run_id`, `experiment_candidates`, and `datasets`. Unique `(model_asset_id,version)`, `pipeline_run_id`, and `selected_candidate_id`.
- **Indexes:** the three unique indexes plus `workspace_id`, `workflow_id`, `workflow_run_id`, `dataset_id`.
- **Nullability:** only `artifact_uri` nullable.
- **Mutation / volume:** SQLAlchemy rejects update/delete, but PostgreSQL has no immutability trigger. `metrics jsonb`, artifact URI, and digest are the wide fields. Numerous direct lineage FKs do not enforce that all referenced rows belong to the same tenant/run/experiment; consistency is application-owned.

## Audit and observability

### `lab_decision_records`

- **Purpose / ownership / cardinality:** Missing-value decision ledger, approximately one row per feature column per upload; potentially high volume for wide datasets.
- **Key and relationships:** PK `id`; FK `upload_id -> client_lab_uploads.id CASCADE`; nullable `llm_invocation_id -> llm_invocations.id SET NULL`, uniquely indexed when non-null. Check restricts `source` to rule/llm/fallback.
- **Indexes:** `upload_id`; unique `llm_invocation_id`.
- **Nullability:** `raw_llm_output`, `fill_value`, `llm_invocation_id` nullable; all others NN.
- **Mutation / volume:** No physical append-only guard. High-volume/wide fields are `evidence_snapshot`, `raw_llm_output`, `fill_value`, verdict text, and `created_at`. Tenant ownership is indirect through upload; the optional LLM invocation is not constrained to the same tenant/run.

### `ml_run_events`

- **Purpose / ownership / cardinality:** Ordered pipeline event stream; expected dozens to hundreds per experiment and one of the primary future high-volume tables.
- **Key and relationships:** PK `id`; FK `workspace_id -> workspaces.id NO ACTION`; FKs `workflow_run_id -> workflow_runs.id CASCADE`, `experiment_id -> experiments.id CASCADE`; unique `(experiment_id,sequence)`.
- **Indexes:** unique event sequence plus `workspace_id`, `workflow_run_id`, `experiment_id`, `stage`, `timestamp`.
- **Nullability:** only `duration_ms` nullable.
- **Mutation / volume:** Append-only is enforced twice: SQLAlchemy update/delete listeners and PostgreSQL trigger `ml_run_events_append_only`, which raises on UPDATE/DELETE. `payload jsonb`, timestamp, stage/status, and duration are high-volume. `EXPLAIN` for one experiment ordered by sequence used the unique composite index. Tenant-wide time queries need `(workspace_id,timestamp DESC)`; stage/time monitoring may need a selective composite/partial index.

### `llm_invocations`

- **Purpose / ownership / cardinality:** Generic safe LLM observability ledger; zero to many invocations per pipeline operation and potentially high volume.
- **Key and relationships:** PK `id`; FK `workspace_id -> workspaces.id NO ACTION`; FKs `workflow_run_id -> workflow_runs.id CASCADE`, `experiment_id -> experiments.id CASCADE`; purpose check enumerates five supported purposes.
- **Indexes:** `workspace_id`, `workflow_run_id`, `experiment_id`, `purpose`, `status`, `created_at`, `input_evidence_digest`.
- **Nullability:** nullable `provider`, `model`, `safe_output`, `final_decision`, `latency_ms`, token counts, `estimated_cost`, `completed_at`; all others NN.
- **Mutation / volume:** Mutable physically. JSONB redaction/output/decision values, reason/verdict text, token/cost metrics, and timestamps are high-volume. Tenant/run/experiment consistency is not enforced. `EXPLAIN` for tenant recent-first used the workspace index then sorted; `(workspace_id,created_at DESC)` is the key missing dashboard index. Purpose/status/time composites or partial indexes should follow measured query patterns.

### `ml_run_verifications`

- **Purpose / ownership / cardinality:** Multiple deterministic/OpenAI audit attempts for a client Labs run; normally a small number per run but unbounded over retries.
- **Key and relationships:** PK `id`; FK `run_id -> client_lab_uploads.id CASCADE`; nullable FKs `experiment_id -> experiments.id SET NULL`, `llm_invocation_id -> llm_invocations.id SET NULL`; `llm_invocation_id` is uniquely indexed. Audit mode check permits routine/deep.
- **Indexes:** `run_id`, `experiment_id`, unique `llm_invocation_id`, `input_digest`, `created_at`.
- **Nullability:** nullable `experiment_id`, `llm_report`, `error`, `duration_ms`, `completed_at`, `llm_invocation_id`; all others NN.
- **Mutation / volume:** Mutable physically. `deterministic_checks`, `redaction_summary`, and `llm_report` JSONB can be large. Tenant ownership is indirect through `run_id`, with no same-tenant enforcement for optional experiment/invocation. Recent attempts per run would benefit from `(run_id,created_at DESC)`.

## Physical schema versus ORM

At revision 0027, the catalog and SQLAlchemy metadata are structurally aligned (`compare_metadata`: zero differences). Important behavioral boundaries remain:

1. **Legacy slug collision remains.** Revision 0023 derives backfilled asset slugs from only the first 12 UUID hex characters. Two valid legacy dataset IDs with the same prefix make 0023 fail before any forward repair can run. This is a migration defect and remains unresolved because historical migrations were not rewritten.
2. **Immutability differs by access path.** `Dataset` and `ModelVersion` are protected only by SQLAlchemy listeners; direct SQL and bulk operations can update/delete them. `MlRunEvent` additionally has a PostgreSQL trigger and is physically append-only.
3. **Client-side defaults/on-update behavior is not a database invariant.** Many JSONB/config/status/description fields rely on Python defaults, and `updated_at` fields rely on SQLAlchemy `onupdate`. Direct inserts must provide NN values; direct updates do not automatically refresh `updated_at`.
4. **Upload synchronization is ORM-only.** The ORM assigns `client_lab_uploads.run_id=id` and derives `client_status` from `pipeline_status`; PostgreSQL enforces only run ID uniqueness and the coarse-status value set, not equality/derivation.
5. **Tenant consistency is denormalized but unenforced.** Direct `workspace_id` columns speed scoping, but there are no composite FKs/checks ensuring linked workflow, dataset, upload, experiment, candidate, model, event, and invocation rows share a workspace.
6. **No RLS.** Every tenant boundary depends on application predicates and authorization. A missed filter is a cross-tenant exposure risk.

## Query and index scalability assessment

- **Strong paths:** event timeline `(experiment_id,sequence)` and holdout paging `(experiment_id,row_index)` were index-driven in `EXPLAIN`. Natural-key lookups for workspace-scoped assets/workflows/models and membership lookup in either direction are covered.
- **Primary gap:** operational dashboards usually combine tenant, status/purpose/stage, and recent-first ordering. Current single-column indexes force filtering and/or sorting. Prioritize measured variants of:
  - `workflow_runs(workspace_id,status,created_at DESC)`
  - `experiments(workspace_id,status,created_at DESC)`
  - `client_lab_uploads(workspace_id,pipeline_status,created_at DESC)` and/or coarse status
  - `llm_invocations(workspace_id,created_at DESC)`
  - `ml_run_events(workspace_id,timestamp DESC)`
  - `ml_run_verifications(run_id,created_at DESC)`
- **Redundant/overlapping indexes:** separate indexes on the leftmost columns of unique composites (`workspace_memberships.workspace_id`, dataset asset/workflow/model workspace IDs, `datasets.dataset_asset_id`, `workflow_run_inputs.workflow_run_id`, `experiment_test_predictions.experiment_id`, `ml_run_events.experiment_id`) increase write amplification. Do not remove them without checking index usage and query plans under realistic data.
- **FK deletion and retention:** most lineage FKs are NO ACTION, preserving history but making parent cleanup fail. Cascades on event/invocation/test-prediction children can produce large delete transactions and lock/WAL spikes. Define retention and archival before tables become large.
- **Empty-database limitation:** planner row estimates were one row and no `EXPLAIN ANALYZE` production workload exists. Index recommendations are structural hypotheses requiring realistic seeded/production statistics and query traces.

## JSONB, ORM loading, and high-volume risks

- JSONB is used for configuration, schemas/profiles, experiment candidate/result data, predictions, evidence, decisions, events, and LLM reports. None of these columns has a GIN or expression index. This is appropriate for write/read-whole-document usage; containment/path filtering will not scale without targeted indexes.
- Wide JSONB values increase TOAST reads, vacuum work, replication/WAL volume, and update amplification. Keep list endpoints from selecting large payloads; project only summary columns and fetch detail lazily.
- Most SQLAlchemy relationships use default lazy loading. Iterating workspaces → workflows/runs, experiments → candidates/predictions/events/invocations, datasets → profiles/experiments, or model assets → versions can cause N+1 queries. Use explicit `selectinload`/`joinedload` where bounded, pagination for child collections, and avoid eager-loading multiple large collections in one join.
- The highest expected row counts are `experiment_test_predictions`, `ml_run_events`, `llm_invocations`, `lab_decision_records`, `workflow_runs`, and `experiments`. The widest likely rows are profiles, candidates, experiments, events, decisions, verifications, and LLM invocations.

## Future partition candidates

No table is currently partitioned. Partition only after workload/retention evidence, because UUID PK/FK uniqueness and migration complexity are material:

1. **`experiment_test_predictions`** — strongest candidate; partition by hash of `experiment_id` for parallel experiment-local access, or by parent lifecycle if whole-experiment deletion dominates. Time partitioning is less aligned with its main query key.
2. **`ml_run_events`** — range partition by `timestamp`/`created_at` for retention, possibly subpartition/hash by tenant at very large scale.
3. **`llm_invocations`** — range partition by `created_at` for cost/audit retention and time-window dashboards.
4. **`lab_decision_records` and `ml_run_verifications`** — range by `created_at` if audit retention drives bulk expiry.
5. **`workflow_runs`, `experiments`, and `client_lab_uploads`** — later range-partition candidates when historical execution volume and archival windows justify it.

Before partitioning, define tenant-aware retention, ensure all hot queries include the partition key, reconcile global uniqueness requirements, and test cascade behavior. The present evidence supports candidate identification only, not an immediate partition migration.
