# ADR 0006 — The ML state graph

**Status:** Accepted (founder, 2026-10-01; decisions recorded below)  
**Date:** 2026-10-01  
**Prompt:** P2.1-A (design only; P2.2-A implements the schema)  
**Depends on:** [0003-workspace-selection.md](0003-workspace-selection.md),
[0004-simulation-insights-tenancy.md](0004-simulation-insights-tenancy.md),
[0005-upload-policy.md](0005-upload-policy.md); live Alembic head
`0062_run_artifact_types` (verified 2026-10-01)  
**Consumed by:** P2.2-A/B, P2.3-A, P2.4-A/B, P2.5-A, P3.1-B, P3.4-A, P4.2-A–P4.4-A

### Revision 1 (post-review, 2026-10-01)

Changes after the db-migration review; founder decisions above §Founder
decisions are unchanged except the Q10 refinement noted there.

- **Dataset identity (B1).** Auto-train rebinds `experiments.dataset_id` to a
  per-job prepared Dataset after the holdout lock. New write-once
  `experiments.source_dataset_id` (the published upload dataset) is the graph's
  DatasetVersion node, the `split_plans` partition target and the lineage-guard
  key; `dataset_id` stays the prepared dataset (edge `prepared_as`). Backfill from
  `client_lab_uploads.dataset_id`, NULL otherwise. `feature_recipe` is excluded
  from staleness until FeatureRecipe is a reusable node (Phase 5).
- **Same-project FKs (B2).** `UNIQUE(workspace_id, project_id, id)` is added to
  `problem_specs`, `datasets`, `feature_set_versions`, `model_versions`,
  `experiments`, `experiment_candidates` (and the new tables); every ref target,
  decision subject, `split_plans.dataset_id` and `experiments.split_plan_id` is
  a three-column FK (MATCH SIMPLE semantics stated).
- **Tenant backfill (B3).** `workspace_id` on the three tenant-less tables is
  always `SET NOT NULL`; the migration raises with counts if a NULL remains.
- Lineage guard specified precisely (column list, insert-only columns, lock
  rule, project equality, function name); ON DELETE action for every new FK;
  cross-workspace mismatch pre-checks; named-trigger disable procedure; migrations
  inline literal SQL; `actor_service_token_id`; `project_refs` column freeze,
  DELETE rejection and transactional move; JSONB CHECK semantics; graph-loader
  corrections; downgrade preconditions; migration split into `0063` + `0064`;
  nits (hex digest CHECKs, FK indexes, ordering tiebreak, non-empty CHECKs,
  `_append_only` trigger name, `provider_kind` values, verifier row-list note,
  why existing decision tables are not reused).

### Revision 2 (P2.2-B implementation, 2026-10-02)

Implementation decisions taken while building the write paths. They refine
§2/§3/§5 and do not change the schema.

- **Source dataset in plan identity.** `plan_digest` also includes
  `source_dataset_id` (besides its content digest). Every upload is its own
  Dataset row and `split_plans.dataset_id` must equal
  `experiments.source_dataset_id`, so a re-upload of identical bytes gets a new
  plan version, whose deterministic map has the **same** `assignment_digest`.
  The canonical identity is stored in `plan_evidence.identity` (bounded,
  no row lists) so the holdout part can be matched at the holdout lock.
- **Fold column.** In the assignment CSV, `fold` is the 1-based outer
  validation fold of a train row; `0` marks a train row that is never a
  validation row (TimeSeriesSplit warm-up). Expanding-window folds train fold
  k on every row with fold < k; every other strategy trains on all rows outside
  fold k.
- **Partition-by-map on reuse.** At the holdout lock the oldest plan with the
  same holdout identity (source dataset, target, task, holdout plan,
  holdout planner version, `structural_cleaning_digest`) is loaded, its bytes
  are verified against `assignment_digest`, and the frame is partitioned by the
  map (`split_holdout_by_assignment`); on reuse nothing is re-split, in the
  stage or in the runner (on plan creation the runner's own split must equal
  the new map, or `folds_for_pool` / `verify_run_against_plan` fail closed). A frame whose source rows differ from the map fails closed
  (`split_assignment_mismatch`); a logged cleaning change is a new identity and
  therefore a new plan. After the train-only validation plan, the full
  `plan_digest` selects the plan; a reused plan supplies the outer folds
  (the runner applies the stored fold map) and its stored
  HoldoutPlan/ValidationPlan (reuse copy rule). Outer folds are derived only
  when a plan is created. Because reuse never re-derives, no library version is
  part of the identity.
- **Bootstrap preconditions.** `refs.bootstrap.v1` runs only for a locked
  ModelVersion whose run satisfies `dataset`, `split_plan` and
  `champion_model` (with a `final_holdout` evaluation); otherwise bootstrap is
  deferred to a later run (no refs, no record). `problem_spec` (no locked spec)
  and `feature_recipe` (no locked FeatureSetVersion) may be unsatisfiable; they
  are listed in `details.skipped_refs`, and P2.5-A `move_ref` may INSERT such a
  missing kind (`ref_moves[].from = null`) under an accepted record.
- **Run cross-check, twice.** Write time: `verify_run_against_plan` runs before
  the evidence lock and fails closed unless the plan partitions the run's
  source dataset, the per-run `holdout_plan_digest` equals the plan's, and the
  holdout rows and every candidate's per-fold train/validation rows equal the
  map. Read time: the pipeline verifier's `split_plan_consistent` check compares
  `holdout_plan_digest`, `n_train`/`n_test`, the recorded `assignment_digest`
  and the source dataset with `split_plans` (FAIL on mismatch, NOT_VERIFIABLE
  when the plan row is missing; runs without a plan get no check).
- **Row lists.** `full_plan.split` drops `all/train/test_source_rows` only for
  runs with a plan (counts + `split_plan_id`/`assignment_digest` instead); runs
  without a plan keep them (their only immutable copy). The run result keeps
  its own `split` provenance for the verifier and reports.
- **Races.** Only SQLSTATE 23505 on the expected unique constraints is treated
  as a concurrent creator (plan: `uq_split_plans_project_plan_digest`,
  `uq_split_plans_project_version`, `uq_pdr_workspace_idempotency_key`;
  bootstrap: the latter and `uq_project_refs_project_ref_kind`); any other
  integrity error surfaces. An uploaded map whose row insert fails is deleted.
- Deferred: reproduction codegen still re-derives folds (P2.4-B).
- Carry-forward (P2.5-A, P4.4-A): a logged cleaning change or a new seed creates a
  new plan with a fresh holdout on the same source dataset, so champion and
  experiment comparisons require the same `split_plan_id`.

## Context

Phase 1 produced one correct engine: holdout planned first, train-only
decisions, fold-local Pipelines, CV-only winner lock, one holdout evaluation,
object-storage artifacts, typed auto-train stages. The evidence is already
immutable (`db/integrity.py`, `db/evidence_lock.py`), but the data model still
describes **runs**, not a **project state**:

- The holdout partition is per run (`pipeline_scientific_plans`, one row per
  `experiments` row). Two runs on the same dataset cannot prove they share a
  holdout, and the row lists live in `full_plan.split` JSONB
  (`train_source_rows`, `test_source_rows`), which is neither digest-addressed
  nor bounded.
- "Current" has no owner. Nothing records which ProblemSpec, DatasetVersion,
  split or model a project considers current, so staleness cannot be computed.
- Branch lineage exists as columns (`experiments.parent_pipeline_run_id`,
  `branch_key`, `branch_reason`) but a branch carries no typed description of
  what it changed.
- Decisions are scattered: `model_selection_decisions.reason`, `branch_reason`,
  `HoldoutPlan.reason`, upload policy events. There is no project memory an
  agent or a reviewer can query.
- Three evidence tables lack tenant keys entirely (`workflow_run_inputs`,
  `experiment_test_predictions`, `ml_run_verifications` have no `workspace_id`
  column), and `llm_invocations` cannot describe an invocation that has no run.

`docs/mvp/ARCHITECTURE.md` §3.2/§4 fixes the principles (immutable nodes,
mutable refs, computed staleness, shared SplitPlan per lineage, not a graph
database). This ADR turns them into names, tables, rules and a migration plan.

## Decision

### Summary (ten lines)

1. Canonical node name is **Experiment** = table `experiments`; WorkflowRun,
   ExecutionRequest and MlJob are execution envelopes, never graph nodes.
2. Three new tables: `split_plans` (immutable), `project_refs` (the only
   mutable pointers, optimistic `version`), `project_decision_records`
   (strictly append-only ledger; `superseded` is derived from `supersedes_id`).
3. A SplitPlan fixes the holdout **and** the outer CV fold assignment for one
   source DatasetVersion; the row→partition map is an object-storage artifact
   (`split_assignment`) identified by digest; Postgres keeps only counts and
   digests. Reuse is by `plan_digest` equality, verified by `assignment_digest`.
4. Experiment lineage reuses `parent_pipeline_run_id` (no new parent column);
   adds `split_plan_id`, `intent`, and a schema-versioned `change_set` JSONB
   validated by a Pydantic discriminated union of seven change kinds.
5. A branch may change hyperparameters, families, class weights, threshold
   objective, metric, allowlisted feature transforms. It may **not** change
   dataset, split plan, target, task or ProblemSpec: a DB trigger enforces
   source-dataset, split-plan and project equality with the parent; anything
   else is a new root.
6. Every ref move, winner lock, split-plan creation, spec lock and champion
   promotion writes a decision record automatically (actor `rule` or `human`);
   agent rationale is stored with `rationale_untrusted = true`.
7. Staleness is computed, never stored: a node is stale iff an upstream node of
   a ref's kind is not that ref's current target (`feature_recipe` excluded
   until Phase 5). Impact = downstream closure. Both come from one bounded
   in-memory graph loaded with ≤ 10 SQL statements.
8. Tenancy: `workspace_id` + composite FKs on every new table and column; seven
   existing tables gain `UNIQUE(workspace_id, project_id, id)` so every graph
   pointer is a three-column FK `(workspace_id, project_id, id)`.
9. Two expand-only migrations: `0063_state_graph_nodes` (P2.2-A) and
   `0064_tenant_backfill_llm_attribution` (P2.2-A2). Existing runs get honest
   NULLs (no synthesized split plans, no guessed refs); the only data writes
   are deterministic parent-derived backfills (`workspace_id`, `project_id`,
   `source_dataset_id`).
10. Non-goals: generic node/edge tables, stored stale flags, ref history table,
    FeatureRecipe authoring, agent/release/monitoring tables (Phases 6–7).

### 1. Canonical names and the public `/v1` vocabulary

Node kinds are singular snake_case strings. Ids are the table UUIDs; the
textual form `kind:uuid` is used in evidence references and MCP output. Path
form is `/v1/nodes/{kind}/{id}` (P2.3-A impact route).

| `/v1` node kind | Domain name | Physical table | Version / digest | Status source |
| --- | --- | --- | --- | --- |
| `project` | Project | `projects` | — | `status` |
| `problem_spec` | ProblemSpec | `problem_specs` | `version`, `content_digest` | `status` draft/locked |
| `dataset_version` | DatasetVersion | `datasets` | `version`, `content_digest` | publication state (ADR 0005); the node is the **source** (published) dataset, see below |
| `split_plan` | SplitPlan | `split_plans` (new) | `version`, `plan_digest`, `assignment_digest` | always locked |
| `feature_recipe` | FeatureRecipe | `feature_set_versions` | `version`, `content_digest` | `locked_at` |
| `experiment` | Experiment | `experiments` | fingerprint in `config` | `status`, `scientific_evidence_locked_at` |
| `candidate` | Candidate | `experiment_candidates` | `fingerprint` | `status` |
| `model_selection` | ModelSelection | `model_selection_decisions` | — | `locked_at` |
| `model_version` | ModelVersion | `model_versions` | `version`, `content_digest` | — |
| `decision_record` | ProjectDecisionRecord | `project_decision_records` (new) | — | `state` + derived `effective_state` |

Not nodes (shown as attributes of an experiment): `workflow_run`
(`workflow_runs`, execution envelope), `execution_request` (idempotent intent),
`ml_job` (worker claim). Evaluations, fold runs, hyperparameters, findings and
preprocessing steps are **children** of experiment/candidate and appear only in
the experiment inspector, never in the project graph.

Naming rules for new code: Pydantic read models and `/v1` say `experiment_id`
and `parent_experiment_id`; column names `pipeline_run_id` and
`parent_pipeline_run_id` stay (compatibility window, contract migration later).
`GET /v1/experiments/{id}` is canonical; the existing
`/v1/model-builds/{pipeline_run_id}` routes become aliases in P3.1-A.

Ref kinds are **roles**, not types: `problem_spec → problem_spec`,
`dataset → dataset_version`, `split_plan → split_plan`,
`feature_recipe → feature_recipe`, `champion_model → model_version`.

**Two datasets per run — the source dataset is the node.** Auto-train creates
the shell experiment with the published upload dataset
(`client_lab_upload_service.py:503`), locks the holdout on it, then
`run_preprocessing_setup` ingests a new derived Dataset for the prepared CSV
every job (`auto_train/preprocessing.py:111`, `lab_service.py:92-103`) and
`bind_pipeline_run` rebinds `experiments.dataset_id` to it
(`lineage_service.py:585`); `model_versions.dataset_id` follows. A per-job
derived dataset can never be shared, so it cannot be the graph's
DatasetVersion. Decision: new write-once `experiments.source_dataset_id`
(§4) names the source dataset; it is the DatasetVersion node, the
`split_plans` partition target and the lineage-guard key. `dataset_id` keeps
its meaning (prepared/derived dataset of this run) and is shown as the
`prepared_as` attribute edge, never used for staleness.

Graph edges (`GET /v1/projects/{id}/graph`) point from child to the upstream
node it was built from:

| `relation` | from → to | Source column |
| --- | --- | --- |
| `uses_problem_spec` | experiment → problem_spec | `workflow_runs.problem_spec_id` via `experiments.workflow_run_id` |
| `uses_dataset` | experiment → dataset_version | `experiments.source_dataset_id` |
| `prepared_as` | experiment → dataset_version (derived) | `experiments.dataset_id`; attribute edge, excluded from staleness and impact |
| `uses_split_plan` | experiment → split_plan | `experiments.split_plan_id` |
| `branch_of` | experiment → experiment | `experiments.parent_pipeline_run_id` |
| `partitions` | split_plan → dataset_version | `split_plans.dataset_id` (= the source dataset) |
| `produced_by` | feature_recipe → experiment | distinct `(feature_set_version_id, experiment_id)` from `experiment_candidates` |
| `produced_by` | model_version → experiment | `model_versions.pipeline_run_id` |
| `uses_feature_recipe` | model_version → feature_recipe | `model_versions.feature_set_version_id` |
| `uses_dataset` | model_version → dataset_version | via the owning experiment's `source_dataset_id` (`model_versions.dataset_id` is the prepared dataset) |

Graph response shape: `{project, refs:[…], nodes:[{kind,id,label,status,
created_at,version,digest,stale,stale_reasons:[{ref_kind,expected,actual}],
ref_kinds:[…]}], edges:[{from:{kind,id},to:{kind,id},relation}], truncated,
next_cursor}`.

### 2. `project_refs` — the only mutable pointers

One row per `(project, ref_kind)`. A ref says "this is the project's current X".
Nodes are never edited; refs move. Moving a ref **requires an accepted
decision record** written in the same transaction (the row carries its FK).

| Column | Type | Null | Constraints / notes |
| --- | --- | --- | --- |
| `id` | uuid | no | PK |
| `workspace_id` | uuid | no | FK `workspaces(id)` ON DELETE CASCADE; `UNIQUE(workspace_id, id)` |
| `project_id` | uuid | no | CFK `(workspace_id, project_id) → projects` ON DELETE CASCADE; `UNIQUE(workspace_id, project_id, id)` |
| `ref_kind` | varchar(32) | no | CHECK IN (`problem_spec`,`dataset`,`split_plan`,`feature_recipe`,`champion_model`); `UNIQUE(project_id, ref_kind)` |
| `problem_spec_id` | uuid | yes | FK `(workspace_id, project_id, problem_spec_id) → problem_specs(workspace_id, project_id, id)` NO ACTION; index |
| `dataset_id` | uuid | yes | FK `(workspace_id, project_id, dataset_id) → datasets(…)` NO ACTION; index; the source dataset |
| `split_plan_id` | uuid | yes | FK `(workspace_id, project_id, split_plan_id) → split_plans(…)` NO ACTION; index |
| `feature_set_version_id` | uuid | yes | FK `(workspace_id, project_id, feature_set_version_id) → feature_set_versions(…)` NO ACTION; index |
| `model_version_id` | uuid | yes | FK `(workspace_id, project_id, model_version_id) → model_versions(…)` NO ACTION; index |
| `version` | integer | no | default 1, CHECK ≥ 1; optimistic concurrency token |
| `decision_record_id` | uuid | no | FK `(workspace_id, project_id, decision_record_id) → project_decision_records(workspace_id, project_id, id)` NO ACTION; index |
| `moved_at` | timestamptz | no | time of the last move (= `created_at` for bootstrap) |
| `created_at`, `updated_at` | timestamptz | no | server defaults |

CHECK `ck_project_refs_target_matches_kind`:
`num_nonnulls(problem_spec_id, dataset_id, split_plan_id, feature_set_version_id, model_version_id) = 1`
AND the non-null column is the one named by `ref_kind`. No per-kind polymorphic
`target_id`: real composite FKs are what make cross-tenant pointers impossible
in SQL. Three-column FKs require the target row to carry the same
`project_id` (§7 adds `UNIQUE(workspace_id, project_id, id)` to the target
tables); because `project_refs.project_id` is NOT NULL, MATCH SIMPLE never
skips the check here.

Concurrency. The service moves a ref with
`UPDATE project_refs SET …, version = version + 1, moved_at = now() WHERE id = :id AND version = :expected`;
zero rows → `409 ref_version_conflict` carrying the current version. Bootstrap
inserts race on `UNIQUE(project_id, ref_kind)`; the loser re-reads and returns
the winner. `/v1` (P3.1-A) maps `If-Match: "<version>"` to `expected_version`
and returns `ETag` on every ref read.

Integrity of the pointer row. Trigger `project_refs_columns_immutable`
(BEFORE UPDATE → existing `prevent_canonical_column_mutation`) freezes `id`,
`workspace_id`, `project_id`, `ref_kind`, `created_at`; only the target
columns, `version`, `decision_record_id`, `moved_at`, `updated_at` may change.
Trigger `project_refs_no_delete` (BEFORE DELETE) raises: projects are archived,
never deleted, and a current pointer is never dropped. `move_ref()` is one
transaction: INSERT the accepted record → versioned UPDATE of every ref in
`details.ref_moves` → if any UPDATE affects 0 rows, raise and roll back the
whole transaction, so an accepted record can never exist without its move.

Semantic rules checked by the service before a move (all 404-safe across
tenants):

| `ref_kind` | Target must satisfy |
| --- | --- |
| `problem_spec` | `status = 'locked'`, same project |
| `dataset` | `datasets.project_id = project` (legacy NULL-project datasets are not referenceable) |
| `split_plan` | `split_plans.dataset_id` equals the current `dataset` ref target, or the same decision moves both (`details.ref_moves`) |
| `feature_recipe` | `locked_at IS NOT NULL`, same project |
| `champion_model` | same project; owning experiment has `scientific_evidence_locked_at IS NOT NULL`; the record cites at least one `final_holdout` evidence ref |

One decision record may move several refs at once (`details.ref_moves`); every
moved row points at that record. A champion promotion moves `champion_model`
and `feature_recipe` together (the recipe of the promoted model).

Bootstrap rule (`refs.bootstrap.v1`, P2.2-B): the **first** run in a project
that reaches a locked ModelVersion initializes all five refs from its own
lineage and writes one `ref_initialized` record (actor `rule`, rationale "first
locked model; not a comparison"). Later runs never move refs automatically.
There is no ref history table: the sequence of `ref_moved` /
`champion_promoted` records with `details.ref_moves[].from/to` is the history.

### 3. `split_plans` — one holdout and fold assignment per lineage

A SplitPlan is the project-scoped, immutable partition of one **source**
DatasetVersion into `holdout` and `train`, plus the outer CV fold of every
train row (the holdout is locked before the prepared dataset exists). Every
experiment references exactly one; all experiments in a lineage share it, so
their CV and holdout numbers are comparable and the improve loop can never
touch a fresh holdout.

| Column | Type | Null | Constraints / notes |
| --- | --- | --- | --- |
| `id` | uuid | no | PK |
| `workspace_id` | uuid | no | FK `workspaces(id)` ON DELETE CASCADE; `UNIQUE(workspace_id, id)` |
| `project_id` | uuid | no | CFK → `projects` ON DELETE CASCADE; `UNIQUE(workspace_id, project_id, id)` |
| `dataset_id` | uuid | no | FK `(workspace_id, project_id, dataset_id) → datasets(workspace_id, project_id, id)` NO ACTION; the **source** DatasetVersion partitioned (= `experiments.source_dataset_id`), never a prepared dataset; the service requires `datasets.content_digest IS NOT NULL` (plan identity needs it) |
| `version` | integer | no | per project, CHECK ≥ 1; `UNIQUE(project_id, version)` |
| `task_type` | varchar(64) | no | resolved task (`binary`/`multiclass`/`regression`) |
| `target_column` | varchar(256) | no | stratification depends on it, so it is part of identity |
| `holdout_strategy` | varchar(64) | no | CHECK IN (`stratified_random`,`random`,`group_disjoint`,`temporal_future`) (`unsupported` is never persisted) |
| `holdout_test_size` | double | no | requested; CHECK `> 0 AND < 1` |
| `holdout_seed` | integer | no | `HoldoutPlan.random_state` |
| `stratified` | boolean | no | |
| `group_column` | varchar(256) | yes | |
| `time_column` | varchar(256) | yes | |
| `validation_strategy` | varchar(64) | no | `ValidationPlan.strategy` |
| `validation_folds` | integer | no | actual outer folds in the map; CHECK ≥ 2 |
| `validation_seed` | integer | no | |
| `row_count` | integer | no | modeling rows after structural cleaning |
| `train_row_count` | integer | no | |
| `holdout_row_count` | integer | no | CHECK `train_row_count + holdout_row_count = row_count` |
| `plan_digest` | varchar(64) | no | sha256 hex of the canonical plan identity (below); CHECK `~ '^[0-9a-f]{64}$'`; `UNIQUE(project_id, plan_digest)` |
| `assignment_artifact_id` | uuid | no | CFK `(workspace_id, assignment_artifact_id) → artifacts` NO ACTION (`artifact_type = 'split_assignment'`) |
| `assignment_digest` | varchar(64) | no | = `artifacts.content_digest` of the map (denormalized; service asserts equality); hex CHECK |
| `holdout_plan_digest` | varchar(64) | no | `content_digest(HoldoutPlan.to_dict())`; hex CHECK; equals `pipeline_scientific_plans.holdout_plan_digest` of every experiment on this plan by construction (reuse copy rule below) |
| `holdout_planner_version` | varchar(64) | no | `HOLDOUT_PLAN_VERSION` |
| `validation_planner_version` | varchar(64) | no | `VALIDATION_PLAN_VERSION` |
| `plan_evidence` | jsonb | no | `{holdout_plan: HoldoutPlan.to_dict(), validation_plan: ValidationPlan.to_dict()}`; CHECK object, `octet_length ≤ 32768`; **no row lists** |
| `reason` | varchar(2048) | no | `HoldoutPlan.reason` (why this strategy) |
| `created_by` | uuid | yes | FK `users(id)` ON DELETE NO ACTION; NULL for worker-created |
| `created_at`, `locked_at` | timestamptz | no | both `now()`; the row is locked from insert |

Also `UNIQUE(workspace_id, project_id, id)` (three-column FK target for
`experiments.split_plan_id`, refs and decision subjects).

Indexes: `(project_id, created_at DESC)`, `(dataset_id)`, `(assignment_artifact_id)`.
Trigger `split_plans_immutable` BEFORE UPDATE OR DELETE →
`prevent_canonical_row_mutation()` (new tuple `STATE_GRAPH_IMMUTABLE_TABLES`
in `db/integrity.py`, same pattern as `PROVENANCE_IMMUTABLE_TABLES`; the
0035 tuples stay untouched).

**Plan identity** (`plan_digest`) = sha256 of canonical JSON of:
`{source_dataset_content_digest, target_column, task_type, holdout:{strategy,
test_size, seed, stratified, group_column, time_column}, validation:{strategy,
requested_folds, shuffle, seed, group_column, time_column},
holdout_planner_version, validation_planner_version,
structural_cleaning_digest}`. The last item is the digest of the deterministic
cleaning log's row-removal rules (the only pre-split step that changes the row
set). Reason, evidence and timestamps are excluded on purpose.

**Row-assignment artifact.** Canonical CSV, UTF-8, LF, header
`source_row,partition,fold`, one line per modeling row sorted by `source_row`
ascending; `partition ∈ {train, holdout}`; `fold` is the outer fold index for
train rows and empty for holdout rows. `source_row` is the engine's existing
`__dclab_source_row__` (0-based position in the **source** DatasetVersion's
stored table, attached by the cleaning stage before the holdout lock); rows
removed by structural cleaning are absent, so the map also fixes
the modeling row set. `content_digest` = sha256 of the exact bytes. Stored via
`app/storage` at a tenant-prefixed immutable key
`ws/{workspace_id}/projects/{project_id}/split_plans/{split_plan_id}/assignment.csv`,
`artifact_type = 'split_assignment'` (added to `ARTIFACT_TYPES` in
`domain/data_plane.py`; CHECK widened as 0062 did). Bodies never go to
Postgres: P2.2-B stops writing `all_source_rows`/`train_source_rows`/
`test_source_rows` into `pipeline_scientific_plans.full_plan.split` and writes
counts plus `assignment_digest` instead (existing locked rows keep their lists).

**Sharing and reuse.** The engine computes the plan after `run_holdout_lock`
and the outer-fold plan from `run_train_only_decisions`, before any candidate
trains. It then looks up `split_plans` by `(project_id, plan_digest)`:

- Not found → upload the map, insert the row (next `version`), write a
  `split_plan_created` record (actor `rule`, `holdout.planner.v<version>`).
- Found → **do not re-plan**: download the stored map, verify its digest
  against `assignment_digest`, partition the frame by the map. If the engine
  re-derives the split anyway (reproduction, verifier) and its digest differs,
  fail closed with `split_assignment_mismatch` (nondeterminism is a bug, never a
  second plan).
- A branch (P2.4-A) never computes a plan: it copies the parent's
  `split_plan_id` at creation and the worker applies the stored map.

**Reuse copy rule.** On reuse the run does not re-plan: its `HoldoutPlan`
(including `reason` and `evidence`) and `ValidationPlan` are loaded from
`split_plans.plan_evidence`, and `persist_scientific_plan` writes them to the
per-run `pipeline_scientific_plans` row unchanged, so
`pipeline_scientific_plans.holdout_plan_digest = split_plans.holdout_plan_digest`
by construction (the verifier asserts it; a difference is a bug). Note for
P2.2-B: `pipeline_verifier.py:46` (`_HOLDOUT_KEYS`) still reads
`train_source_rows` / `test_source_rows` / `all_source_rows` from the per-run
evidence; it must be switched to the assignment artifact (digest + counts, or
the downloaded map) **before** the write path stops storing row lists, in the
same prompt, with a test on a reused plan.

"Two runs may share a split plan" therefore means: same source DatasetVersion, target,
task, holdout strategy/size/seed/columns, validation strategy/folds/seed and
planner versions. A planner-code upgrade changes `plan_digest`, so a new root
experiment gets a new SplitPlan version and the project ref must be moved
explicitly; old lineages stay comparable among themselves.

Relation to existing evidence: `pipeline_scientific_plans` stays as the
per-run locked evidence (holdout, validation, metric and development plan).
`experiments.split_plan_id` is the canonical edge; the per-run row is
cross-checked against it (`holdout_plan_digest`, counts) by the pipeline
verifier. No column is added to `pipeline_scientific_plans`.

### 4. Experiment lineage and typed change sets

Columns added to `experiments` (all nullable, write-once):

| Column | Type | Null | Constraints / notes |
| --- | --- | --- | --- |
| `source_dataset_id` | uuid | yes | CFK `(workspace_id, source_dataset_id) → datasets(workspace_id, id)` NO ACTION; index; write-once; the published source dataset (§1); set at creation by the upload path; backfilled from `client_lab_uploads.dataset_id` via `client_lab_uploads.experiment_id`, NULL otherwise (admin-Lab and legacy runs; honest) |
| `split_plan_id` | uuid | yes | FK `(workspace_id, project_id, split_plan_id) → split_plans(workspace_id, project_id, id)` NO ACTION; index; CHECK `split_plan_id IS NULL OR project_id IS NOT NULL` (MATCH SIMPLE would skip the FK when `project_id` is NULL); set once (root: by the worker after planning; branch: at creation) |
| `intent` | varchar(2000) | yes | free text for root and branch runs ("why this run"); CHECK `intent IS NULL OR char_length(btrim(intent)) > 0` |
| `change_set` | jsonb | yes | CHECK `jsonb_typeof(change_set) = 'object'`, `(change_set->>'schema_version') ~ '^[0-9]+$'`, `CASE WHEN jsonb_typeof(change_set->'changes') = 'array' THEN jsonb_array_length(change_set->'changes') BETWEEN 1 AND 32 ELSE false END`, `octet_length(change_set::text) ≤ 16384`; CHECK `change_set IS NULL OR parent_pipeline_run_id IS NOT NULL` |

JSONB forbidden-key policy (applies to `change_set`, `plan_evidence`, `facts`,
`evidence_refs`, `details`): the DB CHECK reuses the existing top-level
`sql_no_forbidden_keys` pattern (`execution_requests`) as a backstop only; the
Pydantic schema that validates every write is the real gate (recursive). No
recursive `jsonb_path_exists` CHECK is attempted in Phase 2.

`parent_pipeline_run_id` is reused as the parent edge (it already has a CFK,
self-parent CHECK and index); no `parent_experiment_id` column is added.
`branch_key` and `branch_reason` are frozen (not written by new paths; readers
use `intent ?? branch_reason`) and go in a later contract migration.

Trigger `experiments_lineage_guard` → function
`prevent_experiment_lineage_violation()`, `BEFORE INSERT OR UPDATE OF
split_plan_id, parent_pipeline_run_id, change_set, intent, source_dataset_id,
project_id, workspace_id` (status/result/evidence-lock writes never enter it):

1. Insert-only columns: on UPDATE, `parent_pipeline_run_id`, `change_set` and
   `intent` must be unchanged (`IS NOT DISTINCT FROM OLD`).
2. `source_dataset_id`: NULL → value allowed once; any later change raises.
3. `split_plan_id`: NULL → value allowed only while
   `OLD.scientific_evidence_locked_at IS NULL`; value → other value raises.
4. When `change_set IS NOT NULL` (new-style branch): the parent row must exist
   with `parent.workspace_id = NEW.workspace_id`; `NEW.project_id IS NOT NULL
   AND NEW.project_id = parent.project_id`; `NEW.source_dataset_id IS NOT NULL
   AND NEW.source_dataset_id = parent.source_dataset_id`;
   `parent.split_plan_id IS NOT NULL AND NEW.split_plan_id = parent.split_plan_id`.

Legacy branches (`branch_key` path, `change_set IS NULL`) are not affected.
Branching additionally requires, in the service: parent
`scientific_evidence_locked_at IS NOT NULL`, same ProblemSpec, same target.
`dataset_id` (the prepared dataset) is deliberately not compared: each job
derives its own.

**`ExperimentChangeSet`** (`domain/experiment_changes.py`, Pydantic v2,
`schema_version: Literal[1]`, discriminator `kind`):

| `kind` | Fields | Deterministic validation |
| --- | --- | --- |
| `hyperparameter_override` | `family`, `parameters: dict[str, int\|float\|str\|bool]` | family in `engine/models/registry` for the task; keys ⊂ that family's constructor allowlist; applies to the untuned candidate |
| `family_include` | `family` | registry family for the task, installed library |
| `family_exclude` | `family` | dummy baselines (`majority`, `mean`) can never be excluded |
| `class_weighting` | `mode: none\|balanced\|custom`, `weights?: dict[label, float]` | classification only; labels ⊂ observed classes; weights > 0 |
| `threshold_objective` | `constraints: [{metric, op, value}]`, `cost_false_positive?`, `cost_false_negative?` | binary only; metrics ⊂ `objective.CONSTRAINT_METRICS[task]` |
| `metric_override` | `primary_metric`, `reason` | ⊂ `objective.PRIMARY_METRICS[task]`; `reason` required (recorded) |
| `feature_transform_add` / `feature_transform_remove` | `column`, `transform`, `parameters?` | column exists in the DatasetVersion and is not target/group/time; `transform` ∈ code-owned `FEATURE_TRANSFORM_ALLOWLIST` = {`drop_column`, `keep`, `impute_median`, `impute_most_frequent`, `datetime_extract`} (exactly what the engine executes and codegen reproduces today; each addition needs engine + codegen support); `remove` may not target a leakage exclusion |

Whole-set rules: no duplicate `(kind, family)` / `(kind, column, transform)`;
at most one `metric_override` and one `threshold_objective`; every change is
rejected with a typed error (`invalid_change_set`, `details.path`). The
effective run configuration = parent's materialized `experiments.config` ⊕
change set, materialized into the child's `config` (no ancestor recursion at
run time); the change-set digest enters the search fingerprint so idempotent
replay (`execution_requests` digest binding) works. Metric deltas versus the
parent are computed by the compare read model from `evaluation_metrics`
(P2.4-A may cache them under `result.branch_comparison`; never authoritative).

Not a branch (new root experiment, `parent_pipeline_run_id NULL`): a different
DatasetVersion, target, task type, ProblemSpec, holdout/validation plan, or
re-including a leakage-excluded column.

### 5. `project_decision_records` — append-only project memory

A record is a fact: "actor A proposed/accepted/rejected decision D about
subject S at time T, citing evidence E". Facts never change; later facts refer
to earlier ones through `supersedes_id`.

| Column | Type | Null | Constraints / notes |
| --- | --- | --- | --- |
| `id` | uuid | no | PK |
| `workspace_id` | uuid | no | FK `workspaces(id)` ON DELETE CASCADE; `UNIQUE(workspace_id, id)` |
| `project_id` | uuid | no | CFK → `projects` ON DELETE CASCADE; `UNIQUE(workspace_id, project_id, id)` |
| `decision_type` | varchar(64) | no | CHECK `~ '^[a-z][a-z0-9_]{0,63}$'` (precedent: `ml_jobs.job_type`); allowlist code-owned in `domain/decision_records.py` |
| `state` | varchar(16) | no | CHECK IN (`proposed`,`accepted`,`rejected`); `superseded` is derived, never stored |
| `subject_kind` | varchar(32) | no | CHECK IN (`project`,`problem_spec`,`dataset_version`,`split_plan`,`feature_recipe`,`experiment`,`candidate`,`model_version`) |
| `problem_spec_id` | uuid | yes | FK `(workspace_id, project_id, problem_spec_id) → problem_specs(workspace_id, project_id, id)` NO ACTION; partial index |
| `dataset_id` | uuid | yes | FK `(workspace_id, project_id, dataset_id) → datasets(…)` NO ACTION; partial index; the source dataset |
| `split_plan_id` | uuid | yes | FK `(workspace_id, project_id, split_plan_id) → split_plans(…)` NO ACTION; partial index |
| `feature_set_version_id` | uuid | yes | FK `(workspace_id, project_id, feature_set_version_id) → feature_set_versions(…)` NO ACTION; partial index |
| `experiment_id` | uuid | yes | FK `(workspace_id, project_id, experiment_id) → experiments(…)` NO ACTION; partial index |
| `candidate_id` | uuid | yes | FK `(workspace_id, project_id, candidate_id) → experiment_candidates(…)` NO ACTION; partial index |
| `model_version_id` | uuid | yes | FK `(workspace_id, project_id, model_version_id) → model_versions(…)` NO ACTION; partial index |
| `subject_digest` | varchar(64) | yes | subject's `content_digest`/fingerprint at decision time; CHECK `~ '^[0-9a-f]{1,64}$'` |
| `actor_kind` | varchar(16) | no | CHECK IN (`human`,`rule`,`agent`) |
| `actor_user_id` | uuid | yes | FK `users(id)` ON DELETE NO ACTION (append-only rows cannot be SET NULL); index |
| `actor_rule` | varchar(128) | yes | code-owned rule id, e.g. `selection.cv_winner.v1` |
| `actor_agent_run_id` | uuid | yes | internal agent run (NOOA); no FK until Phase 6 adds `agent_runs` (then a CFK is added additively) |
| `actor_service_token_id` | uuid | yes | external agent acting through `/v1`/MCP with a service token; reserved now, FK added by P3.2-A (`service_tokens`) |
| `rationale` | varchar(4000) | no | bounded text; CHECK `char_length(btrim(rationale)) > 0` |
| `rationale_untrusted` | boolean | no | CHECK `rationale_untrusted = (actor_kind = 'agent')`; rendered as "unverified agent rationale" |
| `facts` | jsonb | no | default `{}`; observed values copied at decision time (metrics, counts); CHECK object, `octet_length(facts::text) ≤ 16384`, top-level forbidden-key backstop (§4 policy) |
| `evidence_refs` | jsonb | no | default `[]`; array of `{kind, id, metric?, scope?}`; CHECK `CASE WHEN jsonb_typeof(evidence_refs) = 'array' THEN jsonb_array_length(evidence_refs) ≤ 64 ELSE false END`; validated at write against project nodes and `evaluation_metrics`; re-authorized on read |
| `details` | jsonb | no | default `{}`; per-type schema-versioned payload (e.g. `ref_moves:[{ref_kind,from,to}]`, `alternatives:[…]`, `change_set_digest`); same bounds as `facts` |
| `schema_version` | integer | no | version of the `details`/`facts` schema for this `decision_type` |
| `policy_version` | varchar(64) | no | e.g. `dclab.decisions.v1` |
| `supersedes_id` | uuid | yes | FK `(workspace_id, project_id, supersedes_id) → project_decision_records(workspace_id, project_id, id)` NO ACTION; CHECK `<> id`; **UNIQUE partial index** (at most one successor — DB-enforced linear chains, no double acceptance) |
| `idempotency_key` | varchar(128) | yes | `UNIQUE(workspace_id, idempotency_key)` partial (P3.1-A replay) |
| `event_at` | timestamptz | no | when the decision happened (= `recorded_at` unless backfilled) |
| `recorded_at` | timestamptz | no | default `now()`; listings order by `(recorded_at DESC, id DESC)` so same-transaction rows have a deterministic order |

CHECK `ck_pdr_subject_matches_kind`: `subject_kind = 'project'` ⇒ all subject
columns NULL; otherwise exactly one non-null and it is the one named by
`subject_kind`. Because `project_id` is NOT NULL, every three-column subject FK
is always enforced (MATCH SIMPLE cannot skip it), so a subject with a NULL or
different `project_id` is rejected by Postgres; the service pre-checks for a
clean 422. CHECK `ck_pdr_actor`: human ⇒ `actor_user_id` set, no rule, no
agent run, no token; rule ⇒ `actor_rule` set (an optional `actor_user_id` may
name the user whose action triggered the rule); agent ⇒
`actor_agent_run_id IS NOT NULL OR actor_service_token_id IS NOT NULL`.

Indexes: `(project_id, recorded_at DESC, id DESC)`, `(project_id, decision_type,
recorded_at DESC, id DESC)`, partial indexes on each subject column,
`(actor_user_id)`, `(actor_agent_run_id)` partial, `(actor_service_token_id)`
partial. Trigger `project_decision_records_append_only` BEFORE UPDATE OR
DELETE → `prevent_canonical_row_mutation()` (`STATE_GRAPH_APPEND_ONLY_TABLES`;
name follows the existing ledgers).

Why not reuse an existing table: `lab_decision_records` is the per-column
missing-value audit of a Labs upload (subject = upload column, LLM ledger
fields) and `decisions` belongs to the frozen Decision.ai vertical
(opportunity → recommended action); neither has a project subject, a state
chain or an actor model, and both are frozen for the Phase 9 contract step.

**State machine** (over a chain, enforced by the service; linearity by the DB):

```
proposed  --accept--> accepted   (new row, state=accepted, supersedes_id=proposal)
proposed  --reject--> rejected   (new row, state=rejected, supersedes_id=proposal)
accepted  --correct-> accepted'  (new row, supersedes_id=old; old becomes superseded)
rejected, superseded: terminal (a new proposal starts a new chain)
```

A row's `effective_state` is `superseded` if any row supersedes it, else its own
`state`; `resolution` of a proposal is the head of its chain. One query, no N+1:

```sql
SELECT r.*, s.id AS superseded_by_id,
       CASE WHEN s.id IS NULL THEN r.state ELSE 'superseded' END AS effective_state
FROM project_decision_records r
LEFT JOIN project_decision_records s ON s.supersedes_id = r.id
WHERE r.workspace_id = :ws AND r.project_id = :project
ORDER BY r.recorded_at DESC, r.id DESC LIMIT :page;
```

Rules kept from `DCLAB_CORE_CONCEPT.md` §5: a record never grants authority
(every read re-authorizes the subject); acceptance does not itself execute an
ML change (it calls the normal command service); rule and human writers set
`rationale_untrusted = false`, agents never may; no chain-of-thought, raw rows
or secrets in any JSON column (bounded + forbidden-key CHECKs).

**Auto-created records** (actor `rule` unless stated; all `accepted`):

| Event | `decision_type` | Subject | Rule id / actor | Evidence |
| --- | --- | --- | --- | --- |
| `model_selection_decisions` insert (winner lock) | `winner_locked` | `candidate` (winner) | `selection.cv_winner.v1` | `cv_aggregate` metrics of winner and runner-up; `facts` copies `selected_score`, `selection_metric`, `selection_policy` |
| new `split_plans` row | `split_plan_created` | `split_plan` | `holdout.planner.v<planner_version>` | `facts` = counts, strategy; `rationale` = `HoldoutPlan.reason` |
| first locked ModelVersion in a project | `ref_initialized` | `model_version` | `refs.bootstrap.v1` | `final_holdout` evaluation; `details.ref_moves` lists all five refs |
| ProblemSpec lock | `problem_spec_locked` | `problem_spec` | human (the locker) | objective/constraints in `facts` |
| explicit ref move (`/v1`, Studio) | `ref_moved` | new target | human (or agent proposal → human acceptance) | required ≥ 1 evidence ref |
| champion promotion | `champion_promoted` | `model_version` | human | ≥ 1 `final_holdout` evidence ref; moves `champion_model` + `feature_recipe` |
| experiment verdict (P4.4-A) | `experiment_accepted` / `experiment_rejected` | `experiment` | human | optional |
| Phase 6 (reserved) | `proposal_accepted` / `proposal_rejected` | proposal subject | human, `actor_agent_run_id` on the proposal row | validator output |

### 6. Staleness and impact

Staleness is **computed on read** from refs; nothing is stored. Upstream
(built-from) edges per kind are exactly the staleness-bearing edges in §1
(`prepared_as` excluded):

- `split_plan` ← `dataset_version` (source, `split_plans.dataset_id`)
- `experiment` ← `problem_spec`, `dataset_version` (source,
  `experiments.source_dataset_id`), `split_plan`, parent `experiment`
- `feature_recipe` ← `experiment` (producing candidates)
- `model_version` ← `experiment` (and through it the source dataset),
  `feature_recipe`

**Definition.** For project P with refs R (ref kind K → current target T_K),
node N is stale iff there exists K ∈ {`problem_spec`, `dataset`, `split_plan`,
`champion_model`} such that the transitive upstream closure of N contains a
node U with `kind(U) = node_kind(K)` and `U ≠ T_K`. Each such `(K, T_K, U)` is
a stale reason. A project with no refs (never bootstrapped) has no stale nodes
and reports `refs_initialized = false`. A ref whose target is itself stale
(e.g. champion built on a superseded dataset) is reported as a stale ref. Only
refs define staleness; a newer locked ProblemSpec that is not the ref does
**not** make anything stale (the ref is the single notion of "current"; see
Alternatives). An experiment whose `source_dataset_id` is NULL (legacy) has no
dataset ancestor and is never dataset-stale; the graph flags it
`lineage_incomplete = true` instead.

**`feature_recipe` is excluded from staleness (Q10 refined).** Today every run
creates its own FeatureSet (`scientific_lineage_service.py:345`,
`pipeline-run-{experiment.id}`), so a `feature_recipe` ref would mark every
non-champion ModelVersion stale by construction — noise, not information. The
ref is still set at bootstrap and moved with champion promotion (founder Q10),
and the node, its edges and its inspector exist; it joins the staleness set
only when FeatureRecipe becomes a reusable, experiment-independent node
(Phase 5). Pending founder confirmation (see §Founder decisions).

**Impact.** `impact(N)` = transitive downstream closure of N (every node whose
upstream closure contains N), grouped by kind. "What becomes stale if ref K
moves to X" = `impact(T_K)` minus nodes already stale, exposed later as
`GET /v1/projects/{id}/refs/{kind}/impact?target=`.

**Algorithm and bounds (P2.3-A `services/graph_service.py`).**

1. Load the project graph with a fixed set of ≤ 10 statements, each filtered
   by `workspace_id` and ordered by existing indexes:
   1. `project_refs` by project;
   2. `problem_specs` by project;
   3. `experiments` by project, newest **500** (`ix_experiments_project_created_at`,
      cursor for more), **joined to `workflow_runs`** for `problem_spec_id`;
   4. parent stubs: `experiments WHERE id IN (parent ids not in the window)`
      — loaded as stub nodes flagged `outside_window = true` so `branch_of`
      edges and upstream closures never dangle (one statement, bounded by 500);
   5. `split_plans` by project;
   6. `datasets WHERE id IN (source_dataset_id ∪ split_plans.dataset_id of
      loaded rows)` (project-scoped datasets alone are not enough: legacy
      rows have NULL `project_id`);
   7. `experiment_candidates WHERE experiment_id IN (loaded)` projected to
      distinct `(experiment_id, feature_set_version_id)` — by experiment ids,
      not `project_id` (nullable), using a new partial index
      `(experiment_id, feature_set_version_id) WHERE feature_set_version_id IS NOT NULL`;
   8. `feature_set_versions WHERE id IN (…)`;
   9. `model_versions WHERE pipeline_run_id IN (loaded experiments)` — again by
      experiment ids, not the nullable `project_id`.
   Per-kind cap 2,000 rows; `truncated = true` plus `next_cursor` when any cap
   hits. No per-node queries (a statement counter in the P2.3-A test asserts
   ≤ 10 statements at 500 experiments).
2. Build adjacency in memory (≈ 2k nodes, ≈ 5k edges worst case) and compute,
   per node, the set of upstream nodes by kind with memoized DFS (O(V+E)).
3. Staleness = compare those sets with refs; impact = reverse BFS from the
   node, result capped at 1,000 node refs with `counts_by_kind` and
   `truncated`.
4. Target p95 < 300 ms at 500 experiments on the dev database; no caching in
   Phase 2 (an ETag over `max(project_refs.updated_at)` + experiment count can
   be added in P3.1-A without changing semantics).

Cross-tenant ids return 404 (`resolve_workspace_access` pattern, ADR 0003).

### 7. Tenancy and integrity

- Every new table: `workspace_id NOT NULL` FK `workspaces(id)`,
  `UNIQUE(workspace_id, id)`, `UNIQUE(workspace_id, project_id, id)`, and
  composite FKs to every parent.
- **Same-project FKs (B2).** No existing table has
  `UNIQUE(workspace_id, project_id, id)`. `0063` adds it to `problem_specs`,
  `datasets`, `feature_set_versions`, `model_versions`, `experiments`,
  `experiment_candidates` (all have `workspace_id`; a unique constraint on
  columns that may be NULL is valid in Postgres). Every `project_refs` target,
  every decision-record subject, `split_plans.dataset_id` and
  `experiments.split_plan_id` is then a three-column FK. MATCH SIMPLE
  semantics: a referencing row with any NULL in the FK columns is not checked;
  this is why the new tables have `project_id NOT NULL` and why
  `experiments.split_plan_id` carries CHECK `split_plan_id IS NULL OR
  project_id IS NOT NULL`. `experiments.source_dataset_id` is a two-column CFK
  (legacy datasets have NULL `project_id`); the service checks project equality.
- Immutability via the existing patterns in `db/integrity.py`: new tuples
  `STATE_GRAPH_IMMUTABLE_TABLES = ("split_plans",)` and
  `STATE_GRAPH_APPEND_ONLY_TABLES = ("project_decision_records",)` with their
  own `state_graph_immutability_upgrade_statements()` (0035/0042 tuples are
  frozen inputs and stay untouched), plus the `project_refs` column freeze and
  DELETE guard (§2) and `prevent_experiment_lineage_violation()` (§4).
  **These helpers serve `create_all` only** (`install_immutability_triggers`
  must call them, including the lineage guard); the migration **inlines the
  identical literal SQL** because `test_historical_alembic_revisions` forbids
  importing `app.db.integrity`, `app.db.evidence_lock` or `app.domain` from a
  revision. The gate test diffs both sources against the live catalog.
  (ARCHITECTURE §3.2's note that helper modules "install" the DDL therefore
  describes `create_all`, not migrations.)
- Cascades: workspace/project ON DELETE CASCADE is declared for the three new
  tables, but their immutability/no-delete triggers reject cascaded deletes
  (`pg_trigger_depth` is not special-cased). Existing evidence tables such as
  `datasets` reach the same outcome differently (their workspace FK is
  NO ACTION). Net effect, unchanged: a workspace with evidence is archived,
  never deleted.
- **ON DELETE for every new FK** (`IMPORTANT_DELETE_ACTIONS` in
  `test_database_foundation_gate.py` gains the non-default ones):

  | FK (new) | ON DELETE |
  | --- | --- |
  | `ml_run_verifications (workspace_id, experiment_id) → experiments` | `SET NULL (experiment_id)` (0058 column-list precedent) |
  | `ml_run_verifications (workspace_id, llm_invocation_id) → llm_invocations` | `SET NULL (llm_invocation_id)` |
  | `ml_run_verifications (workspace_id, run_id) → client_lab_uploads` | `CASCADE` (matches existing FK) |
  | `workflow_run_inputs (workspace_id, workflow_run_id) → workflow_runs` | `CASCADE` (matches existing FK) |
  | `workflow_run_inputs (workspace_id, dataset_id) → datasets` | NO ACTION (matches existing FK) |
  | `experiment_test_predictions (workspace_id, experiment_id) → experiments` | `CASCADE` (matches existing FK) |
  | `llm_invocations (workspace_id, project_id) → projects` | NO ACTION |
  | `split_plans`: `dataset_id`, `assignment_artifact_id`, `created_by` | NO ACTION; `project_id`/`workspace_id` CASCADE (blocked by trigger) |
  | `project_refs`: all five targets, `decision_record_id` | NO ACTION; `project_id`/`workspace_id` CASCADE (blocked by trigger) |
  | `project_decision_records`: all subjects, `supersedes_id`, `actor_user_id` | NO ACTION; `project_id`/`workspace_id` CASCADE (blocked by trigger) |
  | `experiments.split_plan_id`, `experiments.source_dataset_id` | NO ACTION |

- **Missing tenant keys (B3), migration `0064`:** add `workspace_id uuid` to
  `workflow_run_inputs`, `experiment_test_predictions`, `ml_run_verifications`;
  backfill deterministically from the parent (`workflow_runs.workspace_id`,
  `experiments.workspace_id`, `client_lab_uploads.workspace_id` via `run_id`).
  The backfill is complete by construction (every row has a NOT NULL parent),
  so the migration **always** `SET NOT NULL`s and raises with the per-table
  count if any NULL remains. Before adding the secondary CFKs
  (`(workspace_id, dataset_id)`, `(workspace_id, experiment_id)`,
  `(workspace_id, llm_invocation_id)`) it counts cross-workspace mismatches
  (e.g. `workflow_run_inputs` whose dataset's workspace ≠ the run's;
  `ml_run_verifications` whose experiment's or invocation's workspace ≠ the
  upload's) and raises with counts if any exist — never silently nulls or
  skips. The `experiment_test_predictions` UPDATE runs with **only the named
  trigger** disabled: `ALTER TABLE experiment_test_predictions DISABLE TRIGGER
  experiment_test_predictions_evidence_locked` → UPDATE → `ENABLE TRIGGER` →
  assert `pg_trigger.tgenabled = 'O'` for that trigger; no `DISABLE TRIGGER
  ALL`, no try/finally (the revision is one transaction; a failure rolls the
  trigger state back with everything else). Operational note: the UPDATE
  rewrites every row of the largest evidence table and the trigger toggle takes
  ACCESS EXCLUSIVE; run with the worker stopped, in a maintenance window.
- `llm_invocations` expand (`0064`): `workflow_run_id` and `experiment_id`
  become nullable; add `project_id uuid NULL` (CFK → `projects` NO ACTION,
  backfilled from the experiment), `agent_run_id uuid NULL` (no FK yet),
  `actor_service_token_id` is **not** added here (ledger only),
  `provider_kind varchar(32) NULL` with CHECK IN (`llm_provider`,
  `semantic_decision`, `agent_runtime`, `deterministic_fallback`) — the port
  kind of the gateway that produced the row, while the existing `provider`
  column keeps the vendor name (`openai`, `jev`, `nooa`, `fake`); new CHECK
  `num_nonnulls(workflow_run_id, experiment_id, project_id, agent_run_id) ≥ 1`
  (every invocation is attributable). The `purpose` CHECK is left as is
  (widened in P6.2).

### 8. Migration plan — two expand-only revisions

Head today: `0062_run_artifact_types`. Both revisions are hand-written
(`/new-migration …`), inline every DDL string literally (§7), and never import
`app.*`.

**`0063_state_graph_nodes` (P2.2-A):**

| Change | Object |
| --- | --- |
| unique constraints | `UNIQUE(workspace_id, project_id, id)` on `problem_specs`, `datasets`, `feature_set_versions`, `model_versions`, `experiments`, `experiment_candidates` |
| create table | `split_plans` (§3), `project_refs` (§2), `project_decision_records` (§5), with all CHECKs, three-column FKs, indexes |
| add columns | `experiments.source_dataset_id uuid NULL` (+CFK, index), `experiments.split_plan_id uuid NULL` (+three-column FK, index, project CHECK), `experiments.intent varchar(2000) NULL` (+CHECK), `experiments.change_set jsonb NULL` (+CHECKs) |
| index | `experiment_candidates (experiment_id, feature_set_version_id) WHERE feature_set_version_id IS NOT NULL` |
| widen CHECK | `ck_artifacts_type_valid` += `split_assignment` (drop/recreate, as 0062) |
| triggers | `split_plans_immutable`, `project_decision_records_append_only`, `project_refs_columns_immutable`, `project_refs_no_delete`, `experiments_lineage_guard` (`prevent_experiment_lineage_violation()`) |
| data | backfill `experiments.source_dataset_id` from `client_lab_uploads.dataset_id` via `client_lab_uploads.experiment_id` (same workspace asserted; NULL otherwise) |

**`0064_tenant_backfill_llm_attribution` (P2.2-A2, separate PR):**

| Change | Object |
| --- | --- |
| add + backfill + enforce | `workspace_id` on `workflow_run_inputs`, `experiment_test_predictions`, `ml_run_verifications` (NULL → parent-derived backfill → raise on remaining NULLs → `SET NOT NULL`), named-trigger disable procedure (§7) |
| pre-checks | cross-workspace mismatch counts for every secondary CFK; raise with counts |
| add CFKs | per the §7 ON DELETE table, with indexes |
| alter columns | `llm_invocations.workflow_run_id`, `llm_invocations.experiment_id` DROP NOT NULL |
| add columns | `llm_invocations.project_id uuid NULL` (+CFK, index, backfill from the experiment), `agent_run_id uuid NULL`, `provider_kind varchar(32) NULL` (+CHECK); attribution CHECK |

Downgrade. `0063` drops triggers, columns, unique constraints and tables in
reverse order and restores the artifact CHECK; it refuses while any
`split_plans` row or `split_assignment` artifact exists. `0064` restores
NOT NULL on `llm_invocations.workflow_run_id`/`experiment_id`, which fails as
soon as a project-only or agent-only invocation exists — forward repair, not
downgrade (ADR 0004). Orphan `split_assignment` objects (uploaded, then the
DB transaction rolled back) are deleted by the split-plan service's
compensating step; if that fails they are reported by the Phase 8 storage
orphan audit, never cleaned by a downgrade.

Verify (each revision): `upgrade head`, `downgrade -1`, `upgrade head`;
`test_database_foundation_gate.py` with `IMPORTANT_DELETE_ACTIONS` updated;
`test_historical_alembic_revisions.py`; truth artifacts regenerated
(`contracts/sqlalchemy_tables.json` diff and
`fixtures/alembic_head_constraints_triggers.json` regenerated and included).

**Backfill rules for existing runs — honest NULLs:**

- `experiments.source_dataset_id` is backfilled only where the 1:1 upload link
  exists (`client_lab_uploads.experiment_id` → `client_lab_uploads.dataset_id`);
  admin-Lab and other runs stay NULL and are flagged `lineage_incomplete`.
- `experiments.split_plan_id`, `intent`, `change_set` stay NULL for every
  existing run. No SplitPlan is synthesized from `full_plan.split` row lists
  (no artifact exists, cleaning and planner inputs are only partially
  reconstructible). The graph labels such runs "no split plan (pre-Phase-2)";
  they cannot be branched with a change set, only re-run as a new root.
- Existing `parent_pipeline_run_id` values are kept as `branch_of` edges (they
  were explicit); `branch_reason` is surfaced as `intent` when `intent` is NULL.
- `project_refs`: none are created by the migration. Legacy projects get refs
  from their next successful run (bootstrap rule) or from an explicit,
  operator-run `dclab graph bootstrap-refs --project <id>` command (human
  actor, mandatory rationale, writes a `ref_initialized` record flagged
  `details.legacy = true`). Never automatic, never inferred from "latest".
- `project_decision_records`: not written by the migration. P2.2-A ships an
  idempotent, operator-run backfill (`dclab graph backfill-winner-records`)
  that materializes `winner_locked` records from existing
  `model_selection_decisions` (a 1:1 fact, not a guess) with
  `actor_rule = 'selection.cv_winner.backfill.v1'`, `event_at = locked_at`,
  `details.backfilled = true` (founder answer to Q7: yes).
- `llm_invocations.project_id` is backfilled from `experiments.project_id`
  (NULL where the experiment had no project).

### 9. Write-path and API consequences for later prompts

- **P2.2-A**: migration `0063` + model changes + the `create_all` helpers +
  the operator command `dclab graph backfill-winner-records` (Q7).
- **P2.2-A2**: migration `0064` (tenant backfill, `llm_invocations`
  expansion); nothing else, so the lock-heavy backfill is its own PR.
- **P2.2-B**: the upload path sets `experiments.source_dataset_id` at
  creation; holdout + validation stages produce the map and plan identity;
  `services/split_plan_service.py` (new, single owner) does lookup-or-create,
  artifact upload through `app/storage`, digest verification, the reuse copy
  rule (§3) and the `split_plan_created` record; `lineage_service` sets
  `experiments.split_plan_id`; `persist_scientific_plan` stops storing row
  lists and `pipeline_verifier` reads the assignment artifact instead;
  bootstrap refs on first locked ModelVersion; `winner_locked` record on
  selection.
- **P2.3-A**: `graph_service.project_graph()` / `impact()` per §6; routes
  `GET /v1/projects/{id}/graph`, `GET /v1/nodes/{kind}/{id}/impact`.
- **P2.4-A**: `ExperimentChangeSet` per §4; `branch_experiment(parent_id,
  changes, intent)` creates an `execution_requests` row (new operation
  `experiment_branch` added to `EXECUTION_OPERATIONS`) that copies
  `source_dataset_id` and `split_plan_id` from the parent (`dataset_id` is
  rebound to the branch's own prepared dataset as today); worker applies the
  stored map.
- **P2.5-A**: `services/decision_record_service.py` with `record()`, `accept()`,
  `reject()`, `supersede()`, `list()`; `move_ref()` lives in
  `services/project_ref_service.py` and always calls the record service first.
- **P3.1-B / P3.4-A**: resources `GET/POST /v1/projects/{id}/decisions`,
  `GET /v1/projects/{id}/refs`, `POST /v1/projects/{id}/refs/{kind}` (body
  `{target:{kind,id}, expected_version, rationale, evidence_refs}`),
  `GET /v1/split-plans/{id}`; MCP `inspect_project` returns the graph summary
  (nodes by kind, refs, stale counts; never row data), `list_decisions`,
  `record_decision`, `branch_experiment`.
- **P4.2-A–P4.4-A**: stale markers and ref badges come from the graph
  response; the decision timeline reads `effective_state`; Accept/Reject write
  `experiment_accepted/rejected` records.
- **Phase 6** adds `agent_runs`/`agent_proposals` and a CFK for
  `actor_agent_run_id` and `llm_invocations.agent_run_id` additively.
- **Phase 7** ModelRelease references the `champion_model` ref target at
  release time and records a `release_created` decision; no change here.

## Consequences

- Comparisons between experiments in a lineage are provably on the same
  holdout and the same outer folds (shared `assignment_digest`), and the
  improve loop (Phase 5) inherits that guarantee for free.
- "Current" is explicit and auditable: every ref move has a decision record,
  so staleness is deterministic and explainable (`stale_reasons`).
- Project memory exists from Phase 2 on, with the agent-rationale trust label
  the Phase 6 agents need, without any agent table yet.
- Postgres stops absorbing row lists; split maps are digest-addressed objects
  like datasets and models.
- Cost: two migrations (`0063`: 3 new tables, 6 unique constraints, 4
  experiment columns, 5 triggers; `0064`: 3 tenant backfills with a table
  rewrite of `experiment_test_predictions`, `llm_invocations` expansion), each
  its own PR within the ≤ 1 migration / PR budget; 7 nullable subject columns
  on the ledger and 5 on refs instead of a polymorphic id; derived
  `effective_state` means every listing joins the ledger to itself once
  (indexed, bounded); every trigger's SQL exists twice (migration literal +
  `create_all` helper), kept equal by the gate test.
- Two datasets per experiment stay visible (`source_dataset_id` for lineage,
  `dataset_id` for the prepared table); legacy runs without an upload link
  show `lineage_incomplete` rather than a guessed source.
- Legacy runs look incomplete in the graph (no split plan, no refs) — by
  design; the alternative is guessed lineage.

## Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Generic `graph_nodes` / `graph_edges` tables | A parallel system to the existing FKs (non-negotiable 6); drifts from the evidence tables; CORE_CONCEPT §3 explicitly rejects a graph database |
| Store `stale` flags on nodes | Needs invalidation cascades on every ref move; can be wrong after a failed transaction; computing from ≤ 10 bounded queries is cheap |
| Polymorphic `subject_id` / `target_id` without FKs | Violates the database rule that cross-tenant references are impossible in SQL; per-kind CFK columns cost little |
| Mutable `state` column on decision records (column-scoped trigger) | Database rule: append-only tables get no UPDATE path; a chain of facts with a unique successor index is stronger evidence and prevents double acceptance |
| `project_ref_history` table | Duplicates the ledger; `details.ref_moves` already records from/to for every move |
| Keep row assignments in `pipeline_scientific_plans.full_plan` JSONB | Unbounded body in Postgres, no digest identity, no reuse across runs; the database rule sends large bodies to object storage |
| New `experiments.parent_experiment_id` | `parent_pipeline_run_id` already is that column with CFK, index and self-check; a second column is a parallel lineage |
| SplitPlan = holdout only (folds per experiment) | Branch comparisons would differ fold-by-fold; folds are deterministic given the plan, so fixing them costs nothing (founder may veto, Q5) |
| Draft/lockable `split_plans` | No author of draft split plans exists before Phase 6; always-immutable is simpler and matches `datasets` |
| "Newer locked version" also marks nodes stale | Two competing notions of current; locking a spec for exploration would invalidate the whole project; refs alone are explicit |
| Experiment candidates as project-graph nodes | 500 experiments × ~10 candidates explodes the projection; candidates belong to the experiment inspector |
| Auto-promote any run that beats the champion | Removes the human/agent decision the ledger exists to record; bootstrap is the only rule-driven promotion (Q1) |
| Key lineage on `experiments.dataset_id` | It is rebound to a per-job prepared dataset after the holdout lock, so no two runs could ever share a SplitPlan and every run would be a distinct DatasetVersion node |
| Stop rebinding `dataset_id` (keep the source dataset there) | Changes the Phase 1 engine, codegen and reproduction paths that read the prepared table through `dataset_id`; a write-once `source_dataset_id` is additive and honest |
| Reuse `lab_decision_records` or `decisions` for the ledger | Different subjects (upload column; opportunity), no state chain or actor model, both frozen pending the Phase 9 contract step |
| One migration for schema + tenant backfill | The backfill rewrites `experiment_test_predictions` under ACCESS EXCLUSIVE with a trigger toggle; coupling it to the new tables makes the state-graph PR undeployable without a maintenance window |

## Non-goals (Phase 2)

No graph database or generic node table; no stored staleness; no ref history
table; no FeatureRecipe authoring or transform catalog beyond the five
allowlisted transforms; no agent tables, proposals or `agent_run_id` FK
(Phase 6); no ModelRelease / BatchPrediction / MonitoringWindow (Phase 7); no
vector retrieval over decisions; no rename of `experiments` or
`pipeline_run_id` columns; no contract (drop) migrations; no cross-project
refs; no retention or deletion of `split_assignment` artifacts while any
`split_plans` row references them.

## Founder decisions (2026-10-01)

Accepted with the architect's recommendations, except Q7:

1. Bootstrap champion by rule; later runs never auto-promote — **yes**.
2. Legacy projects keep empty refs until their next run; `bootstrap-refs` is the only other path — **yes**.
3. Staleness by refs only — **yes**.
4. Branches never re-include a leakage-excluded column — **yes** (new root + human record).
5. SplitPlan fixes outer CV folds and the holdout — **yes**.
6. Strictly append-only ledger with derived `effective_state` — **yes**.
7. Backfill `winner_locked` records from existing `model_selection_decisions` — **yes**, via the flagged idempotent script, shipped with P2.2-A.
8. Uncompressed canonical split map acceptable for the MVP — **yes**.
9. Ref kinds keep role names — **yes**.
10. `feature_recipe` ref set by bootstrap and moved with champion promotion — **yes**.

**Q10 refined after review (confirmed by the founder, 2026-10-01).** The ref is still
set at bootstrap and moved with champion promotion, but `feature_recipe` is
**excluded from the staleness computation** until FeatureRecipe is a reusable,
experiment-independent node (Phase 5): today every run creates its own
FeatureSet (`scientific_lineage_service.py:345`), so including it would mark
every non-champion ModelVersion stale by construction. See §6.

## Open questions for the founder (answered above)

1. **Bootstrap champion.** Confirm: the first locked ModelVersion in a project
   becomes `champion_model` by rule (`refs.bootstrap.v1`); later runs never
   auto-promote, even when they beat the champion.
2. **Legacy projects.** Leave refs empty until the next run (recommended), with
   the operator-run `bootstrap-refs` command (human actor, rationale) as the
   only other path?
3. **Staleness by refs only.** Confirm dropping the "or has a newer locked
   version" clause from ARCHITECTURE §3.2.
4. **Leakage re-inclusion.** A branch may never re-include a leakage-excluded
   column (new root + human record required). Confirm, or allow with a
   human-actor override record in Phase 2?
5. **Folds in the SplitPlan.** Fix outer CV folds in the plan (recommended, for
   fold-by-fold comparability) or holdout only?
6. **Strict append-only ledger.** Confirm derived `effective_state` over a
   column-scoped `state` UPDATE.
7. **Backfill `winner_locked` records** from existing
   `model_selection_decisions` via a flagged, idempotent script — yes or no?
8. **Split-map size.** Canonical CSV is ~15 MB per 1M rows (immutable, one per
   plan). Acceptable for the MVP, or require gzip-at-rest (digest still over
   the uncompressed bytes)?
9. **Ref kind naming.** Keep role names (`dataset`, `champion_model`) distinct
   from node kinds (`dataset_version`, `model_version`), as proposed?
10. **`feature_recipe` ref in Phase 2.** Set by bootstrap and moved with
    champion promotion (recommended), or leave unset until FeatureRecipe
    authoring exists (Phase 5+)?
