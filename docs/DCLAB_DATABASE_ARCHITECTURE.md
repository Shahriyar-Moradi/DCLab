# DCLab Database Architecture

**Physical freeze head:** `0053_pipeline_run_branch`  
**Evidence date:** 2026-09-09  
**Catalog:** empty PostgreSQL → `alembic upgrade head` → `alembic current` / `alembic check` / SQLAlchemy `compare_metadata()`.

This document describes the database that exists at that head. It is not a wishlist.

## Scope and evidence

| Check | Result |
| --- | --- |
| `alembic current` | `0053_pipeline_run_branch (head)` |
| `alembic check` | No new upgrade operations |
| `compare_metadata()` | `DIFF_COUNT 0` |
| Tables (public heaps) | 65 including `alembic_version` |
| Foreign keys | 332 |
| Unique constraints | 80 |
| Secondary + PK indexes | 350 |
| User triggers | 29 |
| Row-level security | none |
| Partitioned tables | none |

Notation: **NO ACTION** is PostgreSQL’s default FK delete action. Composite tenant FKs are `FOREIGN KEY (workspace_id, child_id) REFERENCES parent (workspace_id, id)`. Column-specific `ON DELETE SET NULL ("col")` clears only that column so `workspace_id` stays.

Pre-redesign databases start at `0028_semantic_leakage_purpose` and upgrade to this head. Historical revisions under `apps/api/alembic/versions/` do not import live `app.domain` / `app.services` / integrity modules; frozen copies live in `apps/api/alembic_frozen/`.

## Canonical object map

Physical PipelineRun is `experiments`. API `pipeline_run_id` is `experiments.id`. Labs CSV ingest is a compatibility adapter (`client_lab_uploads`), not a second run table. Canonical ingest is `DataSource` → `DataAccess` → `IngestionRun` → `Dataset`. `Dataset` remains the immutable DatasetVersion; there is no `dataset_snapshots` table.

| Object | Table | Role |
| --- | --- | --- |
| Workspace | `workspaces` | Tenant root |
| Project | `projects` | Customer case / ML workspace folder |
| DataSource | `data_sources` | Logical external/local source (no secrets in JSON) |
| DataAccess | `data_accesses` | Authorized, executable way to reach a DataSource |
| IngestionRun | `ingestion_runs` | One pull through a DataSource (and optional DataAccess) |
| DatasetAsset / Dataset | `dataset_assets` / `datasets` | Logical dataset + immutable physical version |
| DatasetColumn | `dataset_columns` | Searchable column facts; nullable policy metadata, not a classifier |
| Artifact | `artifacts` | Object-storage registry metadata only |
| Workflow | `ml_workflows` | Reusable business objective |
| WorkflowRun | `workflow_runs` | One invocation |
| PipelineRun | `experiments` | One training/execution graph; optional scientific parent pointer |
| ExecutionRequest | `execution_requests` | Protocol-neutral control-plane intent |
| ScientificPlan | `pipeline_scientific_plans` | Locked holdout/validation/metric plan |
| Candidate | `experiment_candidates` | Physical search candidate |
| ModelVersion | `model_versions` | Published winner release |
| MlJob | `ml_jobs` | Durable worker queue (handler registry; auto_train is one handler) |
| DataAccessEvent | `data_access_events` | Append-only access ledger (summaries only; no raw rows) |
| Visualization | `visualizations` | Canonical visual-result description (small spec; series in artifacts) |

Legacy, still operational: `environments`, `prediction_tasks`, `opportunities`, `predictions`, `decisions`, `client_lab_runs`, `client_lab_run_audits`. Do not invent Project FKs on those opportunity/scoring tables.

## Tenant integrity

Authorization in services is not the boundary. Cross-workspace links are rejected by PostgreSQL composite FKs. Representative parent keys are `UNIQUE (workspace_id, id)` on the parent, then `FOREIGN KEY (workspace_id, child_id)` on the child.

Direct SQL that must fail (and does at this head):

- Workspace A `IngestionRun.data_source_id` → Workspace B `DataSource`
- Workspace A `IngestionRun.data_access_id` → Workspace B `DataAccess`
- Workspace A `DataAccess.data_source_id` → Workspace B `DataSource`
- Workspace A `IngestionRun.execution_request_id` → Workspace B `ExecutionRequest`
- Workspace A `Dataset.ingestion_run_id` → Workspace B `IngestionRun`
- Workspace A Labs upload → Workspace B DataSource / IngestionRun
- Workspace A `Visualization` → Workspace B pipeline run / artifact / candidate
- Workspace A PipelineRun `parent_pipeline_run_id` → Workspace B PipelineRun
- Workspace A `ExecutionRequest` → Workspace B project / parent / workflow run / pipeline run
- Workspace A Project / Workflow / WorkflowRun / PipelineRun / Candidate / ModelVersion / Artifact / ScientificPlan pointers at Workspace B parents

Same-workspace relationships remain valid.

## Major foreign keys and delete actions

Counts at head: **92 CASCADE**, **101 SET NULL**, **139 NO ACTION**. Important tenant/lineage actions:

| Child | Parent | Composite (if any) | Delete |
| --- | --- | --- | --- |
| `ingestion_runs.data_source_id` | `data_sources` | `fk_ingestion_runs_workspace_data_source` | **CASCADE** |
| `data_accesses.data_source_id` | `data_sources` | `fk_data_accesses_workspace_data_source` | **CASCADE** |
| `data_accesses.project_id` | `projects` | `fk_data_accesses_workspace_project` | **SET NULL (`project_id` only)** |
| `ingestion_runs.data_access_id` | `data_accesses` | `fk_ingestion_runs_workspace_data_access` | **SET NULL (`data_access_id` only)** |
| `ingestion_runs.data_access_id` | `data_accesses` | `fk_ingestion_runs_data_source_data_access` | **SET NULL (`data_access_id` only)** |
| `ingestion_runs.execution_request_id` | `execution_requests` | `fk_ingestion_runs_workspace_execution_request` | **SET NULL (`execution_request_id` only)** |
| `datasets.ingestion_run_id` | `ingestion_runs` | `fk_datasets_workspace_ingestion_run` | **NO ACTION** (datasets are immutable; SET NULL/CASCADE cannot succeed) |
| `client_lab_uploads.data_source_id` | `data_sources` | `fk_client_lab_uploads_workspace_data_source` | **SET NULL (`data_source_id` only)** |
| `client_lab_uploads.ingestion_run_id` | `ingestion_runs` | `fk_client_lab_uploads_workspace_ingestion_run` | **SET NULL (`ingestion_run_id` only)** |
| `ml_jobs.project_id` | `projects` | `fk_ml_jobs_workspace_project` | **SET NULL (`project_id` only)** |
| `ml_jobs.upload_id` | `client_lab_uploads` | `fk_ml_jobs_workspace_upload` | **CASCADE** |
| `ml_jobs.execution_request_id` | `execution_requests` | `fk_ml_jobs_workspace_execution_request` | **SET NULL (`execution_request_id` only)** |
| `ml_jobs.workflow_run_id` | `workflow_runs` | `fk_ml_jobs_workspace_workflow_run` | **SET NULL (`workflow_run_id` only)** |
| `ml_jobs.pipeline_run_id` | `experiments` | `fk_ml_jobs_workspace_pipeline_run` | **SET NULL (`pipeline_run_id` only)** |
| `data_access_events.data_access_id` | `data_accesses` | `fk_data_access_events_workspace_data_access` | **NO ACTION** |
| `data_access_events.execution_request_id` | `execution_requests` | `fk_data_access_events_workspace_execution_request` | **SET NULL (`execution_request_id` only)** |
| `data_access_events.ingestion_run_id` | `ingestion_runs` | `fk_data_access_events_workspace_ingestion_run` | **SET NULL (`ingestion_run_id` only)** |
| `execution_requests.project_id` | `projects` | `fk_execution_requests_workspace_project` | **SET NULL (`project_id` only)** |
| `execution_requests.parent_request_id` | `execution_requests` | `fk_execution_requests_workspace_parent` | **SET NULL (`parent_request_id` only)** |
| `execution_requests.workflow_run_id` | `workflow_runs` | `fk_execution_requests_workspace_workflow_run` | **SET NULL (`workflow_run_id` only)** |
| `execution_requests.pipeline_run_id` | `experiments` | `fk_execution_requests_workspace_pipeline_run` | **SET NULL (`pipeline_run_id` only)** |
| `workflow_runs.source_upload_id` | `client_lab_uploads` | `fk_workflow_runs_workspace_source_upload` | **SET NULL (`source_upload_id` only)** |
| `artifacts.pipeline_run_id` | `experiments` | `fk_artifacts_workspace_pipeline_run` | **SET NULL (`pipeline_run_id` only)** |
| `pipeline_scientific_plans.pipeline_run_id` | `experiments` | `fk_pipeline_scientific_plans_workspace_pipeline_run` | **CASCADE** |
| `experiment_candidates.experiment_id` | `experiments` | `fk_experiment_candidates_workspace_pipeline_run` | **CASCADE** |
| `code_snapshots.pipeline_run_id` | `experiments` | `fk_code_snapshots_workspace_pipeline_run` | **CASCADE** |
| `dataset_columns.dataset_id` | `datasets` | `fk_dataset_columns_workspace_dataset` | **CASCADE** |
| `visualizations.pipeline_run_id` | `experiments` | `fk_visualizations_workspace_pipeline_run` | **NO ACTION** (identity; SET NULL/CASCADE cannot succeed) |
| `visualizations.project_id` | `projects` | `fk_visualizations_workspace_project` | **SET NULL (`project_id` only)** |
| `visualizations.pipeline_stage_run_id` | `pipeline_stage_runs` | `fk_visualizations_workspace_stage` | **SET NULL (`pipeline_stage_run_id` only)** |
| `visualizations.candidate_id` | `experiment_candidates` | `fk_visualizations_workspace_candidate` | **SET NULL (`candidate_id` only)** |
| `visualizations.model_evaluation_id` | `model_evaluations` | `fk_visualizations_workspace_evaluation` | **SET NULL (`model_evaluation_id` only)** |
| `visualizations.data_artifact_id` | `artifacts` | `fk_visualizations_workspace_data_artifact` | **NO ACTION** |
| `visualizations.image_artifact_id` | `artifacts` | `fk_visualizations_workspace_image_artifact` | **NO ACTION** |
| `experiments.parent_pipeline_run_id` | `experiments` | `fk_experiments_workspace_parent_pipeline_run` | **NO ACTION** (child stays; parent delete is refused) |
| `ingestion_runs.project_id` | `projects` | (simple FK) | **CASCADE** |

Unqualified composite `ON DELETE SET NULL` is forbidden: it would null `workspace_id`. Revision `0044_tenant_set_null_columns` and later FKs name the optional child column.

## Immutability triggers

Installed by 0035 / 0042 / 0044 (code-snapshot SET NULL exception). Direct SQL is in scope.

**Always immutable** (UPDATE or DELETE raises): `datasets`, `model_versions`, `model_selection_decisions`, `runtime_environments`. `code_snapshots` uses `prevent_code_snapshot_mutation()` so PostgreSQL may clear `pipeline_stage_run_id` on parent DELETE; every other write is refused.

**Visualization identity/spec immutable:** `visualizations` (`visualizations_immutable`). DELETE is refused. UPDATE may only clear association columns (`project_id`, `pipeline_stage_run_id`, `candidate_id`, `model_evaluation_id`) so parent SET NULL can succeed. `spec`, `visualization_type`, `spec_version`, `renderer_hint`, `content_digest`, artifact ids, and `pipeline_run_id` cannot change.

**Locked immutable** (`locked_at IS NOT NULL`): `workflow_versions`, `pipeline_versions`, `feature_set_versions`, `problem_specs`, `pipeline_scientific_plans`.

**Column-immutable:** `artifacts` identity of stored bytes (`provider`, `bucket`, `object_key`, `content_digest`, `size_bytes`). Association and retention columns stay writable; DELETE is allowed.

**Append-only:** `ml_run_events` (`ml_run_events_append_only`); `data_access_events` (`data_access_events_append_only`).

PipelineRun `experiments.status` / `result` stay mutable. That is execution state, not scientific evidence.

## Evidence lock

Revision `0043_evidence_lock` adds `experiments.scientific_evidence_locked_at`. The application stamps it only when `pipeline_run_scientific_evidence_complete(run_id)` is true. The stamp is one-way.

After the stamp, PostgreSQL rejects INSERT/UPDATE/DELETE on the run’s canonical evidence:

`data_quality_findings`, `data_preparation_decisions`, `preprocessing_steps`, `model_selection_decisions`, `experiment_test_predictions`, `experiment_candidates`, `model_hyperparameters`, `cv_fold_runs`, `evaluation_metrics`, `features`, `feature_lineage`, `feature_transformations`, `model_evaluations`, and finalized `pipeline_stage_runs`.

Still writable on purpose: `ml_run_events`, `ml_run_verifications`, `llm_invocations`, `artifacts`, and `experiments.status`/`result`. Post-run stages (`deterministic_verification`, `report`, `openai_audit`) may still close out.

ScientificPlan / RuntimeEnvironment / CodeSnapshot / ModelVersion are frozen by the immutability triggers above, not by the evidence-lock stamp. A locked Labs run cannot rewrite any of them through `psql`.

## Queue model

`execution_requests` is the control-plane request: user/system intent independent of MCP, CLI, studio, or HTTP. It is not a worker row. `request_spec` / `result_summary` are bounded JSON objects and must not store dataset rows or credentials. Labs auto-train optionally inserts one `legacy_labs` / `model_build` request in the same transaction as the upload; `IngestionRun.execution_request_id` may point at it. `ClientLabUpload` remains the compatibility adapter and is not the canonical request. `execution_requests.parent_request_id` is control-plane lineage (SET NULL that column only). `experiments.parent_pipeline_run_id` is scientific branch lineage (NO ACTION). A child PipelineRun is a new execution with its own evidence lock; creating it does not rewrite the parent. This head does not fork runs or copy evidence.

`data_accesses` is the executable path to a `DataSource`. `resource_locator` is non-secret JSON (object keys, artifact ids). `credential_reference` is an opaque secret-manager pointer only — never a connection string or password. `execution_mode` is architectural (`copy`, `query`, `pushdown`, `customer_runner`); only local/upload `copy` is operational. This head does not add Salesforce, Airbyte, MCP connectors, Snowflake, or external OAuth.

`ml_jobs` is the durable worker queue. One table, not a second queue. `job_type` and `handler_key` are slugs so a new worker capability does not need a schema redesign; PostgreSQL is not frozen to `job_type = auto_train`. The shipped handler is `labs.auto_train`. `upload_id` is compatibility-only and nullable; Labs auto-train still inserts one job per upload (`UNIQUE (upload_id)`). Optional tenant links: `execution_request_id`, `workflow_run_id`, `pipeline_run_id` (SET NULL those columns only). One ExecutionRequest may own multiple jobs later; this head does not enqueue those extra jobs and does not add MCP jobs.

The API inserts a queued row in the same transaction as the Labs upload. A worker claims with `SELECT … WHERE status = 'queued' AND available_at <= now() ORDER BY priority DESC, available_at, queued_at FOR UPDATE SKIP LOCKED`. Claim sets `claimed_by` and `lease_expires_at`. Heartbeats use a short-lived session so training transactions do not hold the lease row. Abandoned running jobs are requeued or failed from expired lease (heartbeat fallback when lease is null). `payload` is bounded JSON and must not store rows or credentials.

## Object-storage architecture

Bytes are never stored in PostgreSQL.

1. `store_artifact` writes to `ObjectStorage` (`local` / `s3` / `gcs`) and inserts `artifacts` metadata (`object_key`, `content_digest`, `size_bytes`, provider/bucket).
2. Canonical Dataset loading uses `Dataset.artifact_id` → `Artifact` → `materialize_dataset` / `materialize_object`. Local providers may reuse `local_path` without deleting it. Remote providers stream to a worker-local temp file, verify digest, and delete on exit.
3. `Dataset.location` and `ClientLabUpload.stored_path` are legacy path/URI fields. Production loading prefers Artifact.
4. Workers must not require the API node’s filesystem.

`visualizations` is the canonical description of a visual result for one PipelineRun. `spec` is a small declarative JSON object (bounded, no bulk series keys). Large underlying data and optional PNG/SVG live in Artifact/ObjectStorage (`data_artifact_id`, `image_artifact_id`). This head does not generate charts or add a visualization UI. Studio, MCP, notebook, and report are meant to read the same row later.

## Measured indexes (hot paths)

`EXPLAIN` with `enable_seqscan = off` on the pytest database (eligibility, not production selectivity):

| Query | Index |
| --- | --- |
| Recent projects | `ix_projects_workspace_created_at` |
| Recent pipeline runs | `ix_experiments_workspace_created_at` |
| Pipeline runs by status | `ix_experiments_workspace_status_created_at` (present; plans may also use the created_at prefix) |
| Queued jobs / claim | `ix_ml_jobs_status_queued_at`; due jobs also `ix_ml_jobs_queued_claim` (`priority`, `available_at`, `queued_at`) WHERE `status = 'queued'` |
| Stage timeline | `uq_pipeline_stage_runs_run_sequence` |
| Events by workflow run | `ix_ml_run_events_workflow_run_created_at` |
| Candidates by pipeline run | `ix_experiment_candidates_experiment_id` |
| CV folds | `uq_cv_fold_runs_candidate_fold` |
| Artifacts by run | `ix_artifacts_workspace_pipeline_run_id` |
| Model registry list | `ix_model_versions_workspace_id` |
| Ingestion runs by DataSource | `ix_ingestion_runs_data_source_id` |
| DataAccess by workspace/status | `ix_data_accesses_workspace_status_created_at` |
| Execution requests by workspace/status | `ix_execution_requests_workspace_status_created_at` |
| Data-access audit by workspace | `ix_data_access_events_workspace_created_at` |
| Classified columns (when set) | `ix_dataset_columns_workspace_sensitivity_class` |
| Visualizations by workspace | `ix_visualizations_workspace_created_at` |
| Visualizations by pipeline run | `ix_visualizations_pipeline_run_id` |
| PipelineRun scientific parent | `ix_experiments_parent_pipeline_run_id` |
| Event sequence / holdout paging | `uq_ml_run_events_experiment_sequence`; `uq_experiment_test_predictions_experiment_row` |

Also present: `ix_ml_jobs_workspace_created_at`, `ix_client_lab_uploads_workspace_status_created_at`, `ix_workflow_runs_workspace_created_at`, `ix_datasets_workspace_created_at`.

Empty-database `EXPLAIN` does not prove production cache or cardinality. Do not drop overlapping left-prefix indexes without measured `EXPLAIN ANALYZE`.

## Known high-volume tables

| Table | Why it grows |
| --- | --- |
| `experiment_test_predictions` | One row per holdout row per run |
| `ml_run_events` | Dozens to hundreds of events per pipeline |
| `data_access_events` | One summary row per authorized access (Labs copy today) |
| `llm_invocations` | Optional per-stage LLM ledger |
| `lab_decision_records` | One missing-value decision per column per upload |
| `cv_fold_runs` / `evaluation_metrics` | Candidates × folds × metrics |
| `workflow_runs` / `experiments` / `client_lab_uploads` / `ml_jobs` / `execution_requests` | Operational history |
| `ingestion_runs` | One row per pull |
| `data_accesses` | One authorized path per source (upload copy today) |
| `visualizations` | One small spec row per visual result (series/PNG in artifacts, not JSONB) |

Wide JSONB (`payload`, `result`, `schema_json`, `full_plan`, `pipeline_log`, LLM reports) is unindexed. List endpoints must not `SELECT` those columns. No GIN indexes exist; add them only for measured containment queries.

No table is partitioned. Partition only after retention and query-key evidence. Strongest future candidates remain `experiment_test_predictions` and `ml_run_events`.

## Behavioral boundaries that remain

1. **No RLS.** Tenant isolation is composite FKs plus application workspace filters. A missed filter is still a leak for tables that only have `workspace_id` without a composite parent key.
2. **Legacy slug collision in 0023** is unchanged. Historical migrations are not rewritten.
3. **ORM-only defaults.** `client_lab_uploads.run_id = id` and `client_status` derivation are SQLAlchemy events, not PostgreSQL checks.
4. **Dataset immutability vs SET NULL.** The simple `datasets.ingestion_run_id` FK is still `ON DELETE SET NULL`; the composite tenant FK is NO ACTION. Deleting an ingestion run that a dataset points at is refused (immutability and/or NO ACTION).
5. **`compare_metadata()` is zero at this head.** New vocabulary belongs in a new revision, not in frozen historical files.
6. **SQLAlchemy table-sort cycle.** `ingestion_runs.execution_request_id`, `execution_requests.pipeline_run_id`, `experiments.dataset_id`, and `datasets.ingestion_run_id` form a metadata cycle. The PostgreSQL FKs are valid; Alembic still reports `DIFF_COUNT 0`. Autogenerate may warn while sorting those four tables.
7. **No PII classifier.** `dataset_columns` policy fields are nullable and stay NULL on ingest. `data_access_events` stores summaries only. There is no privacy UI at this head.
8. **No chart generation.** `visualizations` stores metadata only. No ROC, confusion matrix, SHAP, or other plot is produced at this head. There is no visualization UI.
9. **No branching behavior.** `experiments.parent_pipeline_run_id` / `branch_key` / `branch_reason` are nullable pointers. This head does not fork a run, copy scientific evidence, or mutate a locked parent.
10. **Stable `/v1` HTTP boundary.** Application resources live under `/v1`. Legacy `/app`, `/admin`, `/business`, `/workspaces`, and `/auth/me` remain adapters over the same services. This head does not add MCP, customer CLI, WebSocket, or SSE.
11. **Transport-neutral Python client.** `packages/dclab_client` talks only to `/v1` over HTTP. It does not import SQLAlchemy models, database sessions, `app.services`, or the ML engine. MCP and customer CLI would wrap this client later; neither wrapper exists at this head.
12. **Greenfield infrastructure E2E.** `apps/api/tests/test_mvp_infra_foundation_gate.py` walks User → Workspace → Project → DataSource → DataAccess → ExecutionRequest → IngestionRun → Dataset → MlJob → WorkflowRun → PipelineRun → scientific evidence → Visualization → Artifact without `ClientLabUpload`. Labs remains a compatibility adapter. This head does not add MCP, agents, or notebooks.

## Revision spine (redesign window through freeze)

| Revision | Role |
| --- | --- |
| `0028_semantic_leakage_purpose` | Last pre-redesign head |
| `0029`–`0034` | Project, object storage, execution, science, candidates, code |
| `0035_database_integrity` | Composite tenant FKs, immutability, list indexes |
| `0036_legacy_import_projects` | One `legacy-import` project per workspace |
| `0037`–`0039` | Seats, runtime lock scope, scientific plans |
| `0040_ml_jobs` | Durable queue |
| `0041_artifact_pipeline_run` | Indexed `artifacts.pipeline_run_id` |
| `0042_provenance_immutability` | Runtime / snapshot / plan / artifact identity |
| `0043_evidence_lock` | Scientific evidence stamp |
| `0044_tenant_set_null_columns` | Column-specific SET NULL |
| `0045_reproduction_artifacts` | Notebook/script artifact types |
| `0046_ingestion_job_tenant_fks` | DataSource / IngestionRun parent keys and remaining tenant FKs |
| `0047_personal_dev_identity` | `personal_developer` role + nullable `ml_workflows.workspace_domain_id` |
| `0048_execution_requests` | Protocol-neutral `execution_requests` control-plane |
| `0049_data_access` | `data_accesses`; nullable `ingestion_runs.data_access_id` / `execution_request_id` |
| `0050_privacy_audit` | Nullable DatasetColumn policy fields; append-only `data_access_events` |
| `0051_ml_job_queue` | Extensible `ml_jobs` handler queue; `upload_id` compatibility-only |
| `0052_visualizations` | Canonical visual-result metadata; identity/spec immutable |
| `0053_pipeline_run_branch` | Nullable scientific parent pointer on PipelineRun; NO ACTION delete |
