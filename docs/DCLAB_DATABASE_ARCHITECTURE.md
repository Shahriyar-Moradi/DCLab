# DCLab Database Architecture

**Physical freeze head:** `0047_personal_dev_identity`  
**Evidence date:** 2026-09-09  
**Catalog:** empty PostgreSQL → `alembic upgrade head` → `alembic current` / `alembic check` / SQLAlchemy `compare_metadata()`.

This document describes the database that exists at that head. It is not a wishlist.

## Scope and evidence

| Check | Result |
| --- | --- |
| `alembic current` | `0047_personal_dev_identity (head)` |
| `alembic check` | No new upgrade operations |
| `compare_metadata()` | `DIFF_COUNT 0` |
| Tables (public heaps) | 61 including `alembic_version` |
| Foreign keys | 280 |
| Unique constraints | 76 |
| Secondary + PK indexes | 321 |
| User triggers | 27 |
| Row-level security | none |
| Partitioned tables | none |

Notation: **NO ACTION** is PostgreSQL’s default FK delete action. Composite tenant FKs are `FOREIGN KEY (workspace_id, child_id) REFERENCES parent (workspace_id, id)`. Column-specific `ON DELETE SET NULL ("col")` clears only that column so `workspace_id` stays.

Pre-redesign databases start at `0028_semantic_leakage_purpose` and upgrade to this head. Historical revisions under `apps/api/alembic/versions/` do not import live `app.domain` / `app.services` / integrity modules; frozen copies live in `apps/api/alembic_frozen/`.

## Canonical object map

Physical PipelineRun is `experiments`. API `pipeline_run_id` is `experiments.id`. Labs CSV ingest is a compatibility adapter (`client_lab_uploads`), not a second run table.

| Object | Table | Role |
| --- | --- | --- |
| Workspace | `workspaces` | Tenant root |
| Project | `projects` | Customer case / ML workspace folder |
| DataSource | `data_sources` | How a project obtains data (no secrets in JSON) |
| IngestionRun | `ingestion_runs` | One pull of a DataSource |
| DatasetAsset / Dataset | `dataset_assets` / `datasets` | Logical dataset + immutable physical version |
| Artifact | `artifacts` | Object-storage registry metadata only |
| Workflow | `ml_workflows` | Reusable business objective |
| WorkflowRun | `workflow_runs` | One invocation |
| PipelineRun | `experiments` | One training/execution graph |
| ScientificPlan | `pipeline_scientific_plans` | Locked holdout/validation/metric plan |
| Candidate | `experiment_candidates` | Physical search candidate |
| ModelVersion | `model_versions` | Published winner release |
| MlJob | `ml_jobs` | Durable worker queue |

Legacy, still operational: `environments`, `prediction_tasks`, `opportunities`, `predictions`, `decisions`, `client_lab_runs`, `client_lab_run_audits`. Do not invent Project FKs on those opportunity/scoring tables.

## Tenant integrity

Authorization in services is not the boundary. Cross-workspace links are rejected by PostgreSQL composite FKs. Representative parent keys are `UNIQUE (workspace_id, id)` on the parent, then `FOREIGN KEY (workspace_id, child_id)` on the child.

Direct SQL that must fail (and does at this head):

- Workspace A `IngestionRun.data_source_id` → Workspace B `DataSource`
- Workspace A `Dataset.ingestion_run_id` → Workspace B `IngestionRun`
- Workspace A Labs upload → Workspace B DataSource / IngestionRun
- Workspace A `MlJob` → Workspace B upload / project
- Workspace A Project / Workflow / WorkflowRun / PipelineRun / Candidate / ModelVersion / Artifact / ScientificPlan pointers at Workspace B parents

Same-workspace relationships remain valid.

## Major foreign keys and delete actions

Counts at head: **86 CASCADE**, **66 SET NULL**, **128 NO ACTION**. Important tenant/lineage actions:

| Child | Parent | Composite (if any) | Delete |
| --- | --- | --- | --- |
| `ingestion_runs.data_source_id` | `data_sources` | `fk_ingestion_runs_workspace_data_source` | **CASCADE** |
| `datasets.ingestion_run_id` | `ingestion_runs` | `fk_datasets_workspace_ingestion_run` | **NO ACTION** (datasets are immutable; SET NULL/CASCADE cannot succeed) |
| `client_lab_uploads.data_source_id` | `data_sources` | `fk_client_lab_uploads_workspace_data_source` | **SET NULL (`data_source_id` only)** |
| `client_lab_uploads.ingestion_run_id` | `ingestion_runs` | `fk_client_lab_uploads_workspace_ingestion_run` | **SET NULL (`ingestion_run_id` only)** |
| `ml_jobs.project_id` | `projects` | `fk_ml_jobs_workspace_project` | **SET NULL (`project_id` only)** |
| `ml_jobs.upload_id` | `client_lab_uploads` | `fk_ml_jobs_workspace_upload` | **CASCADE** |
| `workflow_runs.source_upload_id` | `client_lab_uploads` | `fk_workflow_runs_workspace_source_upload` | **SET NULL (`source_upload_id` only)** |
| `artifacts.pipeline_run_id` | `experiments` | `fk_artifacts_workspace_pipeline_run` | **SET NULL (`pipeline_run_id` only)** |
| `pipeline_scientific_plans.pipeline_run_id` | `experiments` | `fk_pipeline_scientific_plans_workspace_pipeline_run` | **CASCADE** |
| `experiment_candidates.experiment_id` | `experiments` | `fk_experiment_candidates_workspace_pipeline_run` | **CASCADE** |
| `code_snapshots.pipeline_run_id` | `experiments` | `fk_code_snapshots_workspace_pipeline_run` | **CASCADE** |
| `dataset_columns.dataset_id` | `datasets` | `fk_dataset_columns_workspace_dataset` | **CASCADE** |
| `ingestion_runs.project_id` | `projects` | (simple FK) | **CASCADE** |

Unqualified composite `ON DELETE SET NULL` is forbidden: it would null `workspace_id`. Revision `0044_tenant_set_null_columns` and later FKs name the optional child column.

## Immutability triggers

Installed by 0035 / 0042 / 0044 (code-snapshot SET NULL exception). Direct SQL is in scope.

**Always immutable** (UPDATE or DELETE raises): `datasets`, `model_versions`, `model_selection_decisions`, `runtime_environments`. `code_snapshots` uses `prevent_code_snapshot_mutation()` so PostgreSQL may clear `pipeline_stage_run_id` on parent DELETE; every other write is refused.

**Locked immutable** (`locked_at IS NOT NULL`): `workflow_versions`, `pipeline_versions`, `feature_set_versions`, `problem_specs`, `pipeline_scientific_plans`.

**Column-immutable:** `artifacts` identity of stored bytes (`provider`, `bucket`, `object_key`, `content_digest`, `size_bytes`). Association and retention columns stay writable; DELETE is allowed.

**Append-only:** `ml_run_events` (`ml_run_events_append_only`).

PipelineRun `experiments.status` / `result` stay mutable. That is execution state, not scientific evidence.

## Evidence lock

Revision `0043_evidence_lock` adds `experiments.scientific_evidence_locked_at`. The application stamps it only when `pipeline_run_scientific_evidence_complete(run_id)` is true. The stamp is one-way.

After the stamp, PostgreSQL rejects INSERT/UPDATE/DELETE on the run’s canonical evidence:

`data_quality_findings`, `data_preparation_decisions`, `preprocessing_steps`, `model_selection_decisions`, `experiment_test_predictions`, `experiment_candidates`, `model_hyperparameters`, `cv_fold_runs`, `evaluation_metrics`, `features`, `feature_lineage`, `feature_transformations`, `model_evaluations`, and finalized `pipeline_stage_runs`.

Still writable on purpose: `ml_run_events`, `ml_run_verifications`, `llm_invocations`, `artifacts`, and `experiments.status`/`result`. Post-run stages (`deterministic_verification`, `report`, `openai_audit`) may still close out.

ScientificPlan / RuntimeEnvironment / CodeSnapshot / ModelVersion are frozen by the immutability triggers above, not by the evidence-lock stamp. A locked Labs run cannot rewrite any of them through `psql`.

## Queue model

`ml_jobs` is the durable worker queue. The API inserts a queued row in the same transaction as the Labs upload. A worker claims with `SELECT … WHERE status = 'queued' ORDER BY queued_at FOR UPDATE SKIP LOCKED`. Heartbeats use a short-lived session so training transactions do not hold the lease row. Abandoned running jobs are requeued or failed from heartbeat expiry. `UNIQUE (job_type, target_id)` and `UNIQUE (upload_id)` prevent duplicate auto-train jobs. Tenant FKs: workspace+project (nullable, SET NULL project only) and workspace+upload (CASCADE).

## Object-storage architecture

Bytes are never stored in PostgreSQL.

1. `store_artifact` writes to `ObjectStorage` (`local` / `s3` / `gcs`) and inserts `artifacts` metadata (`object_key`, `content_digest`, `size_bytes`, provider/bucket).
2. Canonical Dataset loading uses `Dataset.artifact_id` → `Artifact` → `materialize_dataset` / `materialize_object`. Local providers may reuse `local_path` without deleting it. Remote providers stream to a worker-local temp file, verify digest, and delete on exit.
3. `Dataset.location` and `ClientLabUpload.stored_path` are legacy path/URI fields. Production loading prefers Artifact.
4. Workers must not require the API node’s filesystem.

## Measured indexes (hot paths)

`EXPLAIN` with `enable_seqscan = off` on the pytest database (eligibility, not production selectivity):

| Query | Index |
| --- | --- |
| Recent projects | `ix_projects_workspace_created_at` |
| Recent pipeline runs | `ix_experiments_workspace_created_at` |
| Pipeline runs by status | `ix_experiments_workspace_status_created_at` (present; plans may also use the created_at prefix) |
| Queued jobs / claim | `ix_ml_jobs_status_queued_at` |
| Stage timeline | `uq_pipeline_stage_runs_run_sequence` |
| Events by workflow run | `ix_ml_run_events_workflow_run_created_at` |
| Candidates by pipeline run | `ix_experiment_candidates_experiment_id` |
| CV folds | `uq_cv_fold_runs_candidate_fold` |
| Artifacts by run | `ix_artifacts_workspace_pipeline_run_id` |
| Model registry list | `ix_model_versions_workspace_id` |
| Ingestion runs by DataSource | `ix_ingestion_runs_data_source_id` |
| Event sequence / holdout paging | `uq_ml_run_events_experiment_sequence`; `uq_experiment_test_predictions_experiment_row` |

Also present: `ix_ml_jobs_workspace_created_at`, `ix_client_lab_uploads_workspace_status_created_at`, `ix_workflow_runs_workspace_created_at`, `ix_datasets_workspace_created_at`.

Empty-database `EXPLAIN` does not prove production cache or cardinality. Do not drop overlapping left-prefix indexes without measured `EXPLAIN ANALYZE`.

## Known high-volume tables

| Table | Why it grows |
| --- | --- |
| `experiment_test_predictions` | One row per holdout row per run |
| `ml_run_events` | Dozens to hundreds of events per pipeline |
| `llm_invocations` | Optional per-stage LLM ledger |
| `lab_decision_records` | One missing-value decision per column per upload |
| `cv_fold_runs` / `evaluation_metrics` | Candidates × folds × metrics |
| `workflow_runs` / `experiments` / `client_lab_uploads` / `ml_jobs` | Operational history |
| `ingestion_runs` | One row per pull |

Wide JSONB (`payload`, `result`, `schema_json`, `full_plan`, `pipeline_log`, LLM reports) is unindexed. List endpoints must not `SELECT` those columns. No GIN indexes exist; add them only for measured containment queries.

No table is partitioned. Partition only after retention and query-key evidence. Strongest future candidates remain `experiment_test_predictions` and `ml_run_events`.

## Behavioral boundaries that remain

1. **No RLS.** Tenant isolation is composite FKs plus application workspace filters. A missed filter is still a leak for tables that only have `workspace_id` without a composite parent key.
2. **Legacy slug collision in 0023** is unchanged. Historical migrations are not rewritten.
3. **ORM-only defaults.** `client_lab_uploads.run_id = id` and `client_status` derivation are SQLAlchemy events, not PostgreSQL checks.
4. **Dataset immutability vs SET NULL.** The simple `datasets.ingestion_run_id` FK is still `ON DELETE SET NULL`; the composite tenant FK is NO ACTION. Deleting an ingestion run that a dataset points at is refused (immutability and/or NO ACTION).
5. **`compare_metadata()` is zero at this head.** New vocabulary belongs in a new revision, not in frozen historical files.

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
