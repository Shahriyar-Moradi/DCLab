# Phase 2 — ML state graph

### P2.1-A — ADR 0006: the ML state graph
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P1.6-A · Review: db-migration-reviewer
Goal: one accepted design that every later prompt builds on.
Read: `docs/mvp/ARCHITECTURE.md` §3.2 and §4, `docs/agentic-program/DCLAB_CORE_CONCEPT.md:101-172`, `docs/DCLAB_CANONICAL_DOMAIN_MODEL.md`, `db/models.py` (projects, problem_specs, datasets, pipeline_scientific_plans, feature_set_versions, experiments, model_selection_decisions, model_versions), `db/integrity.py`, `db/evidence_lock.py`.
Decide and write `docs/adr/0006-ml-state-graph.md`:
- canonical names (Experiment = `experiments`; WorkflowRun = execution envelope) and the public `/v1` vocabulary;
- `project_refs` (kinds: problem_spec, dataset, split_plan, feature_recipe, champion_model) semantics and concurrency (optimistic version);
- `split_plans` columns, lock rules, holdout row-assignment artifact (digest of row-id → fold/holdout map);
- experiment lineage: `parent_experiment_id`, `change_set` typed schema (Pydantic union of change kinds), `intent` text;
- `project_decision_records` schema and state machine; which events auto-create records;
- staleness algorithm and impact query; performance bounds;
- migration plan (expand only) and backfill rules for existing runs (honest nulls, no guessed lineage).
Don't: write code.
Done when: ADR accepted by founder; STATUS.md links it.

### P2.2-A — Schema: split plans, refs, decision records
Model: Opus 5.5 (high) · Size: M · Depends on: P2.1-A · Review: db-migration-reviewer
Do (one migration, use `/new-migration state_graph_nodes`): create `split_plans`, `project_refs`, `project_decision_records` exactly per ADR 0006 with CFKs, immutability/append-only triggers via `db/integrity.py` patterns, indexes; add nullable `experiments.split_plan_id`, `parent_experiment_id` (if not already usable), `change_set` (JSONB, schema-versioned), `intent`; add missing CFKs on `workflow_run_inputs`, `experiment_test_predictions`, `ml_run_verifications`; expand `llm_invocations` (nullable run FKs, `project_id`, `agent_run_id` nullable without FK yet, `provider_kind`).
Verify: migration up/down/up; `test_database_foundation_gate.py`; truth artifacts.

### P2.2-B — Write paths: auto-train creates SplitPlan and refs
Model: Opus 5.5 (high) · Size: M · Depends on: P2.2-A · Review: ml-correctness-reviewer
Do: holdout planning persists a `split_plans` row (or reuses the project's current split ref when dataset+strategy match); experiments reference it; first successful run initializes `project_refs`; selection writes a decision record.
Verify: E2E suite; new test: two runs on same dataset share holdout assignment digest.

### P2.3-A — Graph service and projection API
Model: Opus 5.5 (high) · Size: M · Depends on: P2.2-B · Review: security-reviewer
Do: `services/graph_service.py`: `project_graph(project_id)` → nodes, edges, refs, stale flags, impact(node) → downstream set; bounded queries (no N+1; cap nodes, paginate experiments). Route `GET /v1/projects/{id}/graph` and `GET /v1/nodes/{kind}/{id}/impact`.
Verify: service tests incl. cross-tenant 404, staleness after ref change, performance test with 500 experiments.

### P2.4-A — Branch experiments with typed change sets
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P2.3-A · Review: ml-correctness-reviewer
Do: `ExperimentChange` union (hyperparameter override, family include/exclude, class weighting, threshold objective, feature transform add/remove from allowlist, metric override); `branch_experiment(parent_id, changes, intent)` creates an ExecutionRequest that reuses the parent's locked SplitPlan and DatasetVersion; result stored with diff vs parent metrics.
Don't: allow changes that alter the holdout or dataset in a branch (that is a new root experiment).
Verify: holdout identical across parent/child; invalid change rejected with typed error.

### P2.4-B — Per-experiment reproducible code export
Model: Opus 5.5 (high) · Size: M · Depends on: P2.4-A · Review: ml-correctness-reviewer
Do: extend `model_build_codegen.py` to emit a standalone script/notebook for any experiment (including branch changes); add `GET /v1/experiments/{id}/code`; reproduction test re-runs the script on the stored dataset and matches CV metrics within tolerance.

### P2.5-A — Decision record service
Model: Opus 5.5 (medium) · Size: S · Depends on: P2.2-B · Review: security-reviewer
Do: create/accept/reject/supersede with actor kind (human/rule/agent), evidence refs (node ids + metric names), rationale (agent text labeled untrusted); ref changes require a decision record; `GET /v1/projects/{id}/decisions` with filters.
Verify: state-machine tests; immutability trigger test; tenant tests.
