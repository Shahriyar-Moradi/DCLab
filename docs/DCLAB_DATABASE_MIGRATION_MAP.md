# DCLab database migration map

Head revision: `0040_ml_jobs`.

This map freezes how pre-redesign objects relate to the canonical model. Nothing
in this redesign deletes working product paths. Do not infer that two historical
Workflows were the same case study.

## Revision spine (redesign window)

| Revision | Role |
| --- | --- |
| `0028_semantic_leakage_purpose` | Last pre-redesign head |
| `0029_workspace_identity` | Workspace kinds, entitlements, Project, ProblemSpec |
| `0030_object_storage_lineage` | Artifact, DataSource, IngestionRun, DatasetColumn |
| `0031_execution_hierarchy` | WorkflowVersion, Pipeline, PipelineVersion, stage runs |
| `0032_scientific_lineage` | Data decisions, features, preprocessing |
| `0033_candidate_modeling` | Hyperparameters, folds, evaluations, selection |
| `0034_reproducible_code` | CodeSnapshot, RuntimeEnvironment |
| `0035_database_integrity` | Composite tenant FKs, immutability triggers, list indexes |
| `0036_legacy_import_projects` | Compatibility Project backfill |
| `0037_ml_engineer_seats` | Business `max_ml_engineer_seats = 5`; stop using `max_members` as that cap |
| `0038_runtime_env_lock_scope` | RuntimeEnvironment is global facts; lock Artifacts stay on CodeSnapshot |
| `0039_scientific_plans` | One `pipeline_scientific_plans` row per PipelineRun; explorer reads columns first |
| `0040_ml_jobs` | Durable ML job queue (`ml_jobs`); not a 0036 backfill target |

## Object classification

| Object | Classification | Keep because | Do not |
| --- | --- | --- | --- |
| `experiments` | **Canonical compatibility table** | Physical PipelineRun | Create a second run table or trainer |
| `experiment_candidates` | **Canonical compatibility table** | Physical candidate rows | Duplicate search results only in JSON |
| `client_lab_uploads` | **Compatibility adapter** | Labs CSV → DataSource / Ingestion / Dataset / PipelineRun | Bypass auto-train |
| `opportunities` | **Legacy, still operational** | `/app` scoring upload path | Attach guessed Project FKs |
| `predictions` | **Legacy, still operational** | Written with opportunities | Treat as ModelVersion |
| `decisions` | **Legacy, still operational** | Translated actions | Merge into ModelSelectionDecision |
| `client_lab_runs` | **Legacy read-capable** | Catalog trial history | Use as the CSV auto-train ledger |
| `client_lab_run_audits` | **Legacy read-capable** | Admin raw trial payload | Feed the canonical pipeline |
| `environments` | **Legacy, still used by ingest/train** | Lab execution container | Expose as a customer tenant |
| `prediction_tasks` | **Legacy, still used by ingest/train** | Physical TaskSpec | Replace ProblemSpec |

Future migration candidates (not in this freeze): collapsing `environments` once
every writer can key off Workspace/Project only; retiring catalog `client_lab_runs`
after product replacement; moving scoring events out of opportunity/prediction.

## Backfill rules (`0036_legacy_import_projects`)

Rule 1. **One compatibility project per workspace, never per workflow.**
Slug `legacy-import`, name `Legacy import`. Description states that sharing this
project does not mean the attached Workflows were the same case study.

Rule 2. **Create that project when the workspace has unambiguous orphans.**
At least one row on the frozen 0036 table list has `workspace_id` set and
`project_id` NULL. The table list is explicit in `app.db.legacy_import`; 0036
does not discover tables from `information_schema`.

Rule 3. **Actorless workspaces still get a compatibility Project.** If a
membership/user actor exists (first `workspace_memberships` row by `created_at`,
else first `users` row with that `workspace_id`), store it on `created_by`. If
none exists, leave `created_by` NULL. Do not invent a user. These projects use
`provenance = system_legacy_import`. User-created projects use `provenance = user`
and require `created_by`.

Rule 4. **Attach only unambiguous orphans.** `UPDATE … SET project_id = legacy-import.id`
where `project_id IS NULL` and `workspace_id` matches. Rows that already have a
Project (including the Labs project `labs`) are left alone.

Rule 5. **Do not merge Workflow definitions.** Two historical `ml_workflows` in
the same workspace both attach to `legacy-import` as a bucket. They remain two
rows with two slugs.

Rule 6. **Do not invent Project FKs** on `opportunities`, `predictions`,
`decisions`, `client_lab_runs`, `environments`, or `prediction_tasks`.

Rule 7. **Idempotent.** Re-running the data fix does not create a second
`legacy-import` slug in a workspace. Remaining NULL `project_id` rows still attach.

Rule 8. **Downgrade.** Nulls `project_id` on rows pointing at `legacy-import`,
then deletes those project rows and restores `projects.created_by` NOT NULL.
The slug is reserved for this compatibility bucket. Uses the same named-trigger
disable as upgrade.

Rule 9. **Immutability.** `0035` freezes `datasets` (and published model rows).
`0036` disables only those named DCLab immutability triggers for the backfill
transaction, then enables them again. It does not set `session_replication_role`
and does not disable constraint triggers. Application writers still cannot UPDATE
datasets afterward; new ingest must set `project_id` on INSERT.

Rule 10. **Prepared Labs CSV.** Auto-train writes a second immutable `datasets`
row for the prepared file and reuses the upload `IngestionRun`. That is not a
second ingest engine. Historical `client_lab_uploads` keep their original
dataset/experiment ids.

## Entitlement backfill (`0029` / `0037`)

`0029` seeded `max_members = 5` on every existing workspace. `0037` gives
Business workspaces `max_ml_engineer_seats = 5` and removes the Business
`max_members` system default so that key is not the technical-seat limit.
Personal system-default `max_members` rows are set to `1`. New personal
workspaces still seed `max_members = 1`. New business workspaces seed
`max_ml_engineer_seats = 5`. `max_members` remains an optional overall
safety cap when set.

## Runtime environment lock scope (`0038`)

`0034` stored `runtime_environments.dependency_lock_artifact_id`. That Artifact
is workspace-owned, so two tenants sharing an `environment_digest` could point
at the first tenant's lockfile. `0038` moves the Artifact FK onto
`code_snapshots` with `(workspace_id, dependency_lock_artifact_id)` and copies
only same-workspace locks. Remaining snapshots attach a lock Artifact from the
same PipelineRun when one exists. `RuntimeEnvironment` keeps the digest facts
only.

## Fresh vs existing databases

- **Fresh:** empty PostgreSQL → `alembic upgrade head` → `alembic check` → seed →
  identity E2E. No `legacy-import` projects unless orphan rows exist.
- **Existing:** pre-redesign (`0028`) rows survive upgrade. Compatibility routes
  that read `client_lab_uploads`, `experiments`, and `opportunities` keep working.
  Canonical queries that filter `project_id` see attached orphans on `legacy-import`.
