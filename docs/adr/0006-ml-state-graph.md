# ADR 0006 — The ML state graph

**Status:** Accepted (founder, 2026-10-01; decisions recorded below)  
**Date:** 2026-10-01  
**Prompt:** P2.1-A (design only; P2.2-A implements the schema)  
**Depends on:** [0003-workspace-selection.md](0003-workspace-selection.md),
[0004-simulation-insights-tenancy.md](0004-simulation-insights-tenancy.md),
[0005-upload-policy.md](0005-upload-policy.md); live Alembic head
`0062_run_artifact_types` (verified 2026-10-01)  
**Consumed by:** P2.2-A/B, P2.3-A, P2.4-A/B, P2.5-A, P3.1-B, P3.4-A, P4.2-A–P4.4-A

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
   DatasetVersion; the row→partition map is an object-storage artifact
   (`split_assignment`) identified by digest; Postgres keeps only counts and
   digests. Reuse is by `plan_digest` equality, verified by `assignment_digest`.
4. Experiment lineage reuses `parent_pipeline_run_id` (no new parent column);
   adds `split_plan_id`, `intent`, and a schema-versioned `change_set` JSONB
   validated by a Pydantic discriminated union of seven change kinds.
5. A branch may change hyperparameters, families, class weights, threshold
   objective, metric, allowlisted feature transforms. It may **not** change
   dataset, split plan, target, task or ProblemSpec: a DB trigger enforces
   dataset and split-plan equality with the parent; anything else is a new root.
6. Every ref move, winner lock, split-plan creation, spec lock and champion
   promotion writes a decision record automatically (actor `rule` or `human`);
   agent rationale is stored with `rationale_untrusted = true`.
7. Staleness is computed, never stored: a node is stale iff an upstream node of
   a ref's kind is not that ref's current target. Impact = downstream closure.
   Both come from one bounded in-memory graph loaded with ≤ 8 SQL statements.
8. Tenancy: `workspace_id` + composite FKs on every new table and column;
   same-project constraints use three-column CFKs `(workspace_id, project_id, id)`.
9. Migration `0063_state_graph_nodes` is expand-only; existing runs get honest
   NULLs (no synthesized split plans, no guessed refs); only deterministic
   parent-derived `workspace_id`/`project_id` backfills run inside the migration.
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
| `dataset_version` | DatasetVersion | `datasets` | `version`, `content_digest` | publication state (ADR 0005) |
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

Graph edges (`GET /v1/projects/{id}/graph`) point from child to the upstream
node it was built from:

| `relation` | from → to | Source column |
| --- | --- | --- |
| `uses_problem_spec` | experiment → problem_spec | `workflow_runs.problem_spec_id` via `experiments.workflow_run_id` |
| `uses_dataset` | experiment → dataset_version | `experiments.dataset_id` |
| `uses_split_plan` | experiment → split_plan | `experiments.split_plan_id` |
| `branch_of` | experiment → experiment | `experiments.parent_pipeline_run_id` |
| `partitions` | split_plan → dataset_version | `split_plans.dataset_id` |
| `produced_by` | feature_recipe → experiment | distinct `(feature_set_version_id, experiment_id)` from `experiment_candidates` |
| `produced_by` | model_version → experiment | `model_versions.pipeline_run_id` |
| `uses_feature_recipe` | model_version → feature_recipe | `model_versions.feature_set_version_id` |
| `uses_dataset` | model_version → dataset_version | `model_versions.dataset_id` |

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
| `problem_spec_id` | uuid | yes | CFK → `problem_specs` |
| `dataset_id` | uuid | yes | CFK → `datasets` |
| `split_plan_id` | uuid | yes | CFK → `split_plans` |
| `feature_set_version_id` | uuid | yes | CFK → `feature_set_versions` |
| `model_version_id` | uuid | yes | CFK → `model_versions` |
| `version` | integer | no | default 1, CHECK ≥ 1; optimistic concurrency token |
| `decision_record_id` | uuid | no | CFK `(workspace_id, project_id, decision_record_id) → project_decision_records(workspace_id, project_id, id)` |
| `moved_at` | timestamptz | no | time of the last move (= `created_at` for bootstrap) |
| `created_at`, `updated_at` | timestamptz | no | server defaults |

CHECK `ck_project_refs_target_matches_kind`:
`num_nonnulls(problem_spec_id, dataset_id, split_plan_id, feature_set_version_id, model_version_id) = 1`
AND the non-null column is the one named by `ref_kind`. No per-kind polymorphic
`target_id`: real composite FKs are what make cross-tenant pointers impossible
in SQL.

Concurrency. The service moves a ref with
`UPDATE project_refs SET …, version = version + 1, moved_at = now() WHERE id = :id AND version = :expected`;
zero rows → `409 ref_version_conflict` carrying the current version. Bootstrap
inserts race on `UNIQUE(project_id, ref_kind)`; the loser re-reads and returns
the winner. `/v1` (P3.1-A) maps `If-Match: "<version>"` to `expected_version`
and returns `ETag` on every ref read.

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

A SplitPlan is the project-scoped, immutable partition of one DatasetVersion
into `holdout` and `train`, plus the outer CV fold of every train row. Every
experiment references exactly one; all experiments in a lineage share it, so
their CV and holdout numbers are comparable and the improve loop can never
touch a fresh holdout.

| Column | Type | Null | Constraints / notes |
| --- | --- | --- | --- |
| `id` | uuid | no | PK |
| `workspace_id` | uuid | no | FK `workspaces(id)` ON DELETE CASCADE; `UNIQUE(workspace_id, id)` |
| `project_id` | uuid | no | CFK → `projects` ON DELETE CASCADE; `UNIQUE(workspace_id, project_id, id)` |
| `dataset_id` | uuid | no | CFK → `datasets`; the DatasetVersion partitioned |
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
| `plan_digest` | char(64) | no | sha256 of the canonical plan identity (below); `UNIQUE(project_id, plan_digest)` |
| `assignment_artifact_id` | uuid | no | CFK → `artifacts` (`artifact_type = 'split_assignment'`) |
| `assignment_digest` | char(64) | no | = `artifacts.content_digest` of the map (denormalized; service asserts equality) |
| `holdout_plan_digest` | char(64) | no | `content_digest(HoldoutPlan.to_dict())`; must equal `pipeline_scientific_plans.holdout_plan_digest` of every experiment on this plan (verifier check) |
| `holdout_planner_version` | varchar(64) | no | `HOLDOUT_PLAN_VERSION` |
| `validation_planner_version` | varchar(64) | no | `VALIDATION_PLAN_VERSION` |
| `plan_evidence` | jsonb | no | `{holdout_plan: HoldoutPlan.to_dict(), validation_plan: ValidationPlan.to_dict()}`; CHECK object, `octet_length ≤ 32768`; **no row lists** |
| `reason` | varchar(2048) | no | `HoldoutPlan.reason` (why this strategy) |
| `created_by` | uuid | yes | FK `users(id)`; NULL for worker-created |
| `created_at`, `locked_at` | timestamptz | no | both `now()`; the row is locked from insert |

Indexes: `(project_id, created_at DESC)`, `(dataset_id)`, `(assignment_artifact_id)`.
Trigger `split_plans_immutable` BEFORE UPDATE OR DELETE →
`prevent_canonical_row_mutation()` (new tuple `STATE_GRAPH_IMMUTABLE_TABLES`
in `db/integrity.py`, same pattern as `PROVENANCE_IMMUTABLE_TABLES`; the
0035 tuples stay untouched).

**Plan identity** (`plan_digest`) = sha256 of canonical JSON of:
`{dataset_content_digest, target_column, task_type, holdout:{strategy,
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
`__dclab_source_row__` (0-based position in the DatasetVersion's stored
table); rows removed by structural cleaning are absent, so the map also fixes
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

"Two runs may share a split plan" therefore means: same DatasetVersion, target,
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
| `split_plan_id` | uuid | yes | CFK `(workspace_id, split_plan_id) → split_plans`; index; set once (root: by the worker after planning; branch: at creation) |
| `intent` | varchar(2000) | yes | free text for root and branch runs ("why this run") |
| `change_set` | jsonb | yes | CHECK object with integer `schema_version` and array `changes` (1–32 items), `octet_length ≤ 16384`, no forbidden keys (reuse the `execution_requests` no-secrets pattern); CHECK `change_set IS NULL OR parent_pipeline_run_id IS NOT NULL` |

`parent_pipeline_run_id` is reused as the parent edge (it already has a CFK,
self-parent CHECK and index); no `parent_experiment_id` column is added.
`branch_key` and `branch_reason` are frozen (not written by new paths; readers
use `intent ?? branch_reason`) and go in a later contract migration.

Trigger `experiments_lineage_guard` (BEFORE INSERT OR UPDATE):

1. `split_plan_id`, `parent_pipeline_run_id` and `change_set` cannot change once
   non-null (write-once).
2. When `change_set IS NOT NULL`: the parent must exist in the same workspace
   and project, `NEW.dataset_id = parent.dataset_id`, `parent.split_plan_id IS
   NOT NULL` and `NEW.split_plan_id = parent.split_plan_id`.

Legacy branches (`branch_key` path, `change_set IS NULL`) are not affected.
Branching additionally requires, in the service: parent
`scientific_evidence_locked_at IS NOT NULL`, same ProblemSpec, same target.

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
| `problem_spec_id` | uuid | yes | CFK → `problem_specs` |
| `dataset_id` | uuid | yes | CFK → `datasets` |
| `split_plan_id` | uuid | yes | CFK → `split_plans` |
| `feature_set_version_id` | uuid | yes | CFK → `feature_set_versions` |
| `experiment_id` | uuid | yes | CFK → `experiments` |
| `candidate_id` | uuid | yes | CFK → `experiment_candidates` |
| `model_version_id` | uuid | yes | CFK → `model_versions` |
| `subject_digest` | varchar(64) | yes | subject's `content_digest`/fingerprint at decision time |
| `actor_kind` | varchar(16) | no | CHECK IN (`human`,`rule`,`agent`) |
| `actor_user_id` | uuid | yes | FK `users(id)` (no ON DELETE action: append-only rows cannot be SET NULL) |
| `actor_rule` | varchar(128) | yes | code-owned rule id, e.g. `selection.cv_winner.v1` |
| `actor_agent_run_id` | uuid | yes | no FK until Phase 6 adds `agent_runs` (then a CFK is added additively) |
| `rationale` | varchar(4000) | no | bounded text; non-empty |
| `rationale_untrusted` | boolean | no | CHECK `actor_kind <> 'agent' OR rationale_untrusted`; rendered as "unverified agent rationale" |
| `facts` | jsonb | no | default `{}`; observed values copied at decision time (metrics, counts); CHECK object, `≤ 16384` bytes, no forbidden keys |
| `evidence_refs` | jsonb | no | default `[]`; array of `{kind, id, metric?, scope?}` (≤ 64); validated at write against project nodes and `evaluation_metrics`; re-authorized on read |
| `details` | jsonb | no | default `{}`; per-type schema-versioned payload (e.g. `ref_moves:[{ref_kind,from,to}]`, `alternatives:[…]`, `change_set_digest`); same bounds as `facts` |
| `schema_version` | integer | no | version of the `details`/`facts` schema for this `decision_type` |
| `policy_version` | varchar(64) | no | e.g. `dclab.decisions.v1` |
| `supersedes_id` | uuid | yes | CFK `(workspace_id, project_id, supersedes_id) → project_decision_records(workspace_id, project_id, id)`; CHECK `<> id`; **UNIQUE partial index** (at most one successor — DB-enforced linear chains, no double acceptance) |
| `idempotency_key` | varchar(128) | yes | `UNIQUE(workspace_id, idempotency_key)` partial (P3.1-A replay) |
| `event_at` | timestamptz | no | when the decision happened (= `recorded_at` unless backfilled) |
| `recorded_at` | timestamptz | no | default `now()` |

CHECK `ck_pdr_subject_matches_kind`: `subject_kind = 'project'` ⇒ all subject
columns NULL; otherwise exactly one non-null and it is the one named by
`subject_kind`. CHECK `ck_pdr_actor`: human ⇒ `actor_user_id` set, no rule, no
agent run; rule ⇒ `actor_rule` set (an optional `actor_user_id` may name the
user whose action triggered the rule); agent ⇒ `actor_agent_run_id` set.

Indexes: `(project_id, recorded_at DESC)`, `(project_id, decision_type,
recorded_at DESC)`, partial indexes on each subject column, `(actor_agent_run_id)`
partial. Trigger `project_decision_records_immutable` BEFORE UPDATE OR DELETE →
`prevent_canonical_row_mutation()` (`STATE_GRAPH_APPEND_ONLY_TABLES`).

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
ORDER BY r.recorded_at DESC LIMIT :page;
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
(built-from) edges per kind are exactly the edges in §1:

- `split_plan` ← `dataset_version`
- `experiment` ← `problem_spec`, `dataset_version`, `split_plan`, parent `experiment`
- `feature_recipe` ← `experiment` (producing candidates)
- `model_version` ← `experiment`, `dataset_version`, `feature_recipe`

**Definition.** For project P with refs R (ref kind K → current target T_K),
node N is stale iff there exists K such that the transitive upstream closure
of N contains a node U with `kind(U) = node_kind(K)` and `U ≠ T_K`. Each such
`(K, T_K, U)` is a stale reason. A project with no refs (never bootstrapped)
has no stale nodes and reports `refs_initialized = false`. A ref whose target
is itself stale (e.g. champion built on a superseded dataset) is reported as a
stale ref. Only refs define staleness; a newer locked ProblemSpec that is not
the ref does **not** make anything stale (the ref is the single notion of
"current"; see Alternatives).

**Impact.** `impact(N)` = transitive downstream closure of N (every node whose
upstream closure contains N), grouped by kind. "What becomes stale if ref K
moves to X" = `impact(T_K)` minus nodes already stale, exposed later as
`GET /v1/projects/{id}/refs/{kind}/impact?target=`.

**Algorithm and bounds (P2.3-A `services/graph_service.py`).**

1. Load the project graph with a fixed set of ≤ 8 statements, each filtered by
   `workspace_id` and `project_id` and ordered by existing indexes:
   `problem_specs`, `datasets` (project rows ∪ ids referenced by loaded
   experiments), `split_plans`, `experiments` (newest **500**, cursor for
   more), `experiment_candidates` projected to distinct
   `(feature_set_version_id, experiment_id)`, `feature_set_versions`,
   `model_versions`, `project_refs`. Per-kind cap 2,000 rows; `truncated = true`
   plus `next_cursor` when any cap hits. No per-node queries (a statement
   counter in the P2.3-A test asserts ≤ 10 statements at 500 experiments).
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
  `UNIQUE(workspace_id, id)`, and composite FKs to every parent; same-project
  references use three-column CFKs through `UNIQUE(workspace_id, project_id, id)`.
- Immutability via the existing patterns in `db/integrity.py`: new tuples
  `STATE_GRAPH_IMMUTABLE_TABLES = ("split_plans",)` and
  `STATE_GRAPH_APPEND_ONLY_TABLES = ("project_decision_records",)` with their
  own `state_graph_immutability_upgrade_statements()` (0035/0042 tuples are
  frozen inputs and stay untouched); `install_immutability_triggers` gains the
  call for `create_all`. `project_refs` is mutable by design (optimistic
  version); it has no delete path in services.
- `experiments_lineage_guard` (§4) lives beside the evidence-lock DDL in
  `db/evidence_lock.py` style, installed by the same migration.
- Cascades: workspace/project ON DELETE CASCADE is declared for the new tables
  but the immutability triggers reject cascaded deletes (existing behavior for
  `datasets` etc.): a workspace with evidence is never deleted, only archived.
- Missing tenant keys fixed in P2.2-A: add `workspace_id uuid` to
  `workflow_run_inputs`, `experiment_test_predictions`, `ml_run_verifications`;
  backfill deterministically from the parent (`workflow_runs.workspace_id`,
  `experiments.workspace_id`, `client_lab_uploads.workspace_id` via `run_id`);
  add CFKs `(workspace_id, workflow_run_id)`, `(workspace_id, experiment_id)`,
  `(workspace_id, run_id)` and `(workspace_id, dataset_id)` where applicable.
  The backfill UPDATE on `experiment_test_predictions` must temporarily
  `DISABLE TRIGGER experiment_test_predictions_evidence_locked` (locked runs
  reject UPDATEs) and re-enable it in the same migration. `SET NOT NULL` only
  after the migration asserts zero remaining NULLs; otherwise leave nullable
  and record the count in the P2.2-A evidence.
- `llm_invocations` expand: `workflow_run_id` and `experiment_id` become
  nullable; add `project_id uuid NULL` (CFK → `projects`, backfilled from the
  experiment), `agent_run_id uuid NULL` (no FK yet), `provider_kind
  varchar(32) NULL` (CHECK `~ '^[a-z][a-z0-9_]{0,31}$'`); new CHECK
  `num_nonnulls(workflow_run_id, experiment_id, project_id, agent_run_id) ≥ 1`
  (every invocation is attributable). The `purpose` CHECK is left as is
  (widened in P6.2).

### 8. Migration plan — `0063_state_graph_nodes` (expand-only)

Head today: `0062_run_artifact_types`. One revision, hand-written, `/new-migration state_graph_nodes`.

| Change | Object |
| --- | --- |
| create table | `split_plans` (§3), `project_refs` (§2), `project_decision_records` (§5), with all CHECKs, CFKs, indexes |
| add columns | `experiments.split_plan_id uuid NULL` (+CFK, index), `experiments.intent varchar(2000) NULL`, `experiments.change_set jsonb NULL` (+CHECKs) |
| add columns | `workflow_run_inputs.workspace_id`, `experiment_test_predictions.workspace_id`, `ml_run_verifications.workspace_id` (uuid NULL → backfill → CFK → NOT NULL if complete) |
| alter columns | `llm_invocations.workflow_run_id`, `llm_invocations.experiment_id` DROP NOT NULL |
| add columns | `llm_invocations.project_id uuid NULL` (+CFK, index), `agent_run_id uuid NULL`, `provider_kind varchar(32) NULL`; attribution CHECK |
| widen CHECK | `ck_artifacts_type_valid` += `split_assignment` (drop/recreate, as 0062) |
| triggers | `split_plans_immutable`, `project_decision_records_immutable`, `experiments_lineage_guard` |
| data | backfill `workspace_id` (three tables) and `llm_invocations.project_id` from parents only |

Downgrade drops the triggers, columns and tables in reverse order and restores
the artifact CHECK; it succeeds only while no `split_assignment` artifact
exists (forward repair otherwise, ADR 0004). Verify: `upgrade head`,
`downgrade -1`, `upgrade head`; `test_database_foundation_gate.py`; truth
artifacts regenerated (`contracts/sqlalchemy_tables.json` diff included).

**Backfill rules for existing runs — honest NULLs:**

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

- **P2.2-B**: holdout + validation stages produce the map and plan identity;
  `services/split_plan_service.py` (new, single owner) does lookup-or-create,
  artifact upload through `app/storage`, digest verification, and the
  `split_plan_created` record; `lineage_service` sets `experiments.split_plan_id`;
  `persist_scientific_plan` stops storing row lists; bootstrap refs on first
  locked ModelVersion; `winner_locked` record on selection.
- **P2.3-A**: `graph_service.project_graph()` / `impact()` per §6; routes
  `GET /v1/projects/{id}/graph`, `GET /v1/nodes/{kind}/{id}/impact`.
- **P2.4-A**: `ExperimentChangeSet` per §4; `branch_experiment(parent_id,
  changes, intent)` creates an `execution_requests` row (new operation
  `experiment_branch` added to `EXECUTION_OPERATIONS`) that copies
  `dataset_id` and `split_plan_id` from the parent; worker applies the stored map.
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
- Cost: one migration touching 7 existing tables plus 3 new ones (within the
  ≤ 1 migration / PR budget but near the 800-line budget — P2.2-A should not
  bundle write-path changes); 7 nullable subject columns on the ledger and 5 on
  refs instead of a polymorphic id; derived `effective_state` means every
  listing joins the ledger to itself once (indexed, bounded).
- Legacy runs look incomplete in the graph (no split plan, no refs) — by
  design; the alternative is guessed lineage.

## Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Generic `graph_nodes` / `graph_edges` tables | A parallel system to the existing FKs (non-negotiable 6); drifts from the evidence tables; CORE_CONCEPT §3 explicitly rejects a graph database |
| Store `stale` flags on nodes | Needs invalidation cascades on every ref move; can be wrong after a failed transaction; computing from ≤ 8 bounded queries is cheap |
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
