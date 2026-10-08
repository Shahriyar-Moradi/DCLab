# ADR 0007 — The improve loop

**Status:** Proposed; **engine build gated on the founder's choice A / B / C**
(§ What v1 is really worth, Q1). The technical design below is the design for
option A and is built only if the founder accepts A.  
**Date:** 2026-10-08 (Revision 1: 2026-10-09; Revision 2: 2026-10-09)  
**Prompt:** P5.3-A (design only; under option A: P5.4-A1a/A1b/A2/A3 implement,
P6.5-A the optional agent proposer, P5.5-A / A5-A the UX and chat, R2-A the
benchmark; under option B: P5.5-B only)  
**Depends on:** [0006-ml-state-graph.md](0006-ml-state-graph.md) §3, §4, §5,
non-negotiable 2 (evidence lock); [0008-hybrid-ai-decision-model.md](0008-hybrid-ai-decision-model.md)
§1/§1b, §2b, §8, §10; [0009-ai-governance-gateway-harness-assistant.md](0009-ai-governance-gateway-harness-assistant.md)
§2.3, §4, §5; live Alembic head `0077_r3_runs` (verified 2026-10-08; option A
needs two additive migrations, `0078_sealed_evidence` (P5.4-A1a) and
`0079_improve_runs` (P5.4-A2); each prompt discovers the live head first).  
**Code this ADR builds on (all verified by `git grep` on 2026-10-08/09):**
`apps/api/app/domain/experiment_changes.py` (`CHANGE_KINDS`, `_identity`),
`apps/api/app/engine/search/generator.py` (`assemble_candidates`,
`balanced_variants`, `class_weight_variants`, the `__tuned` candidate,
`cap = max(cap, len(planned))` for branches), `apps/api/app/engine/search/tuning.py`
(`tuning_plan`, the `truncated` flag), `apps/api/app/engine/modeling/metric_planner.py`
(`MEANINGFUL_IMBALANCE_RATIO`, `MEANINGFUL_MINORITY_FRACTION`),
`apps/api/app/engine/features/contract.py` (`check_feature_budget`),
`apps/api/app/domain/findings.py` (`FINDING_CHECKS`), `apps/api/app/engine/investigate/checks.py`
(`TEST_PARTITION_CHECKS`, `THRESHOLD_FREE_METRICS`, positional `fold_metrics`),
`apps/api/app/engine/modeling/operating_points.py` (`solve`, `solve_lock`,
`fold_spread`, `load_curve`, `curve_digest`, `NEEDS_CONSTRAINT`,
`FOLD_MIN_DENOMINATOR`), `apps/api/app/engine/modeling/objective.py`
(`candidate_thresholds`, `constraint_status`), `apps/api/app/services/operating_point_service.py`
(`choose_operating_point`), `apps/api/app/engine/experiments/runner.py`
(`_run_open_ingest_experiment`, `_fit_and_score_holdout`, `tuning_deadline`,
events `winner_locked`, `final_test_started`), `apps/api/app/engine/types.py`
(`SearchConfig`), `apps/api/app/engine/search/fingerprint.py` (`library_version`),
`apps/api/app/db/evidence_lock.py` (`pipeline_run_scientific_evidence_complete`,
`EVIDENCE_COMPLETE_SQL`, `summary->>'fold_number'`), `apps/api/app/services/evidence_lock_service.py`
(`scientific_evidence_status`), `apps/api/app/db/integrity.py`
(`prevent_experiment_lineage_violation`, `experiments_lineage_guard`),
`apps/api/app/services/auto_train/persistence.py` (ModelVersion,
`initialize_refs_on_first_model`, `persist_model_build_reproduction_artifacts`),
`apps/api/app/services/model_build_codegen.py` (`_render_holdout_eval`),
`apps/api/app/services/observability_service.py` (`PipelineRunObserver.emit`),
`apps/api/app/services/experiment_branch_service.py` (`branch_experiment`,
`load_parent_context`, `materialize_branch`, `_existing_branch`),
`apps/api/app/services/lab_service.py` (`search_from_mapping`, `_git_hash`),
`apps/api/app/services/auto_train_service.py` (`max_candidates=8`, 600 s, 12
trials), `apps/api/app/services/ml_job_service.py` (`process_next_job`,
`execute_job`, `claim_next_queued_job`, `request_job_cancellation`,
`recover_abandoned_jobs`, `ensure_run_capacity`), `apps/api/app/services/service_token_service.py`
(`authenticate_service_token`), `apps/api/app/services/batch_prediction_service.py`
(artifact + lock check), `apps/api/app/domain/service_tokens.py`
(`HUMAN_ONLY_ROUTES`, `TOKEN_ROUTE_SCOPES`), `apps/api/app/api/v1_proposals.py`
(`require_human_session`), `apps/api/app/domain/idempotency.py`
(`RESERVED_IDEMPOTENCY_KEY_PREFIXES`), `apps/api/app/agents/governance/decision_points.py`
(`REGISTRY["improve.next_action"]`), `apps/api/app/agents/tools/shaping.py`
(`HOLDOUT_KEY`, `strip_holdout`), `apps/api/app/domain/agent_records.py`
(`ImprovementActionProposal`).  
**Consumed by:** P5.5-B (option B) or P5.4-A1a, P5.4-A1b, P5.4-A2, P5.4-A3,
P6.5-A, R2-A, P5.5-A, A5-A (option A)

### Revision 2 (round-2 review fixes, 2026-10-09)

- **Scope honesty (new § What v1 is really worth).** The reviewers verified
  against the generator that the draft's W (class weights) and F− can never
  fire and F+ is nearly moot (class-weighted variants and boosters are already
  in every default run). The ADR now gives a candid cost/benefit of (A) the
  full loop engine, (B) a guided Improve flow over existing pieces with no
  engine, (C) defer; recommends **B**; the status is gated on the founder's
  choice (Q1). The option-A design is kept in full.
- **B-R1.** The noise floor is deleted (it was the chance SD of one model's
  fold score on the primary metric, not of a paired delta; it would have made
  acceptance impossible). `d ≡ 0` → `rejected` (reason `no_change`, counts
  toward patience); constant non-zero `d` → `not_comparable`. Power restated
  with Nadeau–Bengio numbers and a worked example in metric units; the false
  "iid ≈ 2 %" label corrected (iid 0.6 %; ρ = 1/K 2.4 %; ρ = 2/K 6.5 %).
  "Recommend 10 folds" is made concrete: `fresh_split` may ask for
  `validation_folds = 10`; otherwise K is fixed by the plan.
- **B-R2.** v1 actions = **OP + F+ only**; W and F− moved to §1c with the
  reviewers' reasons; a blocking finding after an acceptance reverts the
  incumbent pointer instead of training; §0 and §12 rewritten.
- **B-R3.** `k_i` = size of the candidate-id diff from the pure
  `assemble_candidates` at plan time; T = Σ k_i (no "+1", OP not counted);
  per-try comparability check (incumbent trained ids ⊆ probe trained ids,
  identical fingerprints incl. `library_version`, identical per-fold metrics
  for shared ids) replaces the vacuous `git_commit` check; an F+ that would
  move the `__tuned` variant is refused so every probe is a strict superset.
- **SB-R1 / S-R1.** Sealed predicate = today's predicate minus exactly four
  clauses (final-holdout evaluation, test predictions, ModelVersion links,
  model artifact) plus `NOT EXISTS` of each; every other clause byte-identical
  (gate diff test); `evidence_mode` insert-only with `experiments_lineage_guard`
  extended; the runner derives `score_holdout` from `evidence_mode` (one source
  of truth); `create_pipeline_run(evidence_mode=…)`; Python status chain
  variant; no model artifact for sealed runs; security reviewer on A1a.
- Should-fixes applied: codegen omits refit/holdout cells for sealed runs
  (S-R2); looks counted conservatively as scored runs that reached
  `SELECTION_LOCKED` (S-R3); service-only `SearchConfig` fields passed as
  explicit kwargs, stripped on every copy, generous cap instead of "no
  deadline" (S-R4, security S1); final run's objective = the goal's
  constraints (S-R5); plain goal must equal the primary metric (S-R6); fold
  scores from locked `cv_fold` evaluations keyed by `summary.fold_number`,
  curve folds paired positionally with identical counts and `curve_digest`
  (S-R7, S8); `start_truncated` 409, realised-time probe budget, Optuna
  `truncated` flag, distinct `nothing_comparable` outcome (S-R8); acceptance
  CHECK keyed on `final_experiment_id`, three-column CFK (S-R9, S5); fresh
  split + kept-current limit stated (S-R10); null tests with ρ = 1/K band,
  symmetric null in R2-A, reseed set labelled a smoke test (S-R11); A1 split
  into A1a/A1b (S-R12); state-machine races (watchdog holds the continuation,
  hook pulls `available_at` forward, `job.id = continuation_job_id` guard,
  live-iteration partial unique) (S2); hook after the terminal commit, lock
  order `improve_runs` → `ml_jobs`, watchdog-only paths listed (S3);
  `finalizing` driven by the final run's terminal hook, generic experiment
  cancel refuses tokens on loop runs, outcomes when the final run is cancelled
  before/after the look, retry = HTTP replay only (S4); tokens refused on
  branches of sealed parents (S6); authority re-check reuses
  `authenticate_service_token` conditions + `can_execute_workspace_ml`, every
  stop cancels the in-flight probe (S7); L2 agent run enqueued, never awaited
  (S9); `labs.improve` not counted by `ensure_run_capacity`, 429 → backoff
  (S10); benchmark-only modes unreachable in production (S11); workspace cap
  counts `queued/running/finalizing`, `waiting_user` expires, token start
  refused when N + T > bound/2, DB-counted rate limit (S12); `model_versions`
  INSERT trigger refuses sealed runs (S13); NITs (fold count from the winner,
  OP not in T, flagged-denominator `δ_min`, `goal_met` labelled "met on CV"
  and requiring a feasible incumbent, `NEEDS_CONSTRAINT` 422, retry of
  `nondeterministic_rerun`, compare-view residual, catalog tools built in A3,
  reserved prefix on the acceptance record, downgrade note, `feature_recipe`
  ref move intended, workspace-filtered queries, hook trusts
  `job.workspace_id`).

### Revision 1 (review fixes, 2026-10-09)

- Action space cut from ten draft rows to four; agent proposer an optional
  later add-on. B1 acceptance rule rewritten (t-based, NB-corrected, loop
  multiplicity T). B2 reseed null. B3 sealed evidence mode. B4 final run =
  branch of the probe's parent with the probe's own change set, pinned budget,
  `expected_selection` checked before the holdout. B5 `objective` vs `target`;
  iteration 0 = operating point, loop continues. B6 threshold-free plain
  goals. SB1 per-surface authority with token clamps. SB2 loop = re-enqueued
  state machine. SB3 `PROPOSER_READABLE_CHECKS = FINDING_CHECKS −
  TEST_PARTITION_CHECKS`. ~25 should-fixes applied (sealing via train-only
  `SplitFeatures`, looks from events, time-ordered plans ineligible, fresh-split
  labels, infeasible shortfall rule, N frozen per iteration, human acceptance
  in DB and route sets, reserved idempotency prefixes, authority re-check,
  cancel/accept locking, holdout values never feed non-holdout fields,
  `answer_kinds`, L2 only for family-subset kinds, agent output validation,
  three-column CFKs, `sealed_set_*` field names, AI-off writes no
  `decision_point_resolved`, `autonomy.improve.auto_accept` dropped). New §0,
  §12, P5.4-A split, rejected/deferred table.

## Context

What exists and is reused, not duplicated:

- **Branches are the unit of change.** `branch_experiment` creates a child on
  the parent's locked SplitPlan from a typed `ExperimentChangeSet`;
  `load_parent_context` requires a `COMPLETED` parent with
  `scientific_evidence_locked_at`. Comparisons on one SplitPlan are on the same
  holdout and the same outer folds (ADR 0006 §3).
- **Every run scores the holdout once and the evidence lock requires it.**
  `_run_open_ingest_experiment` locks the CV winner (`winner_locked`,
  `SELECTION_LOCKED`), then `_fit_and_score_holdout`; `run_persistence` creates
  a ModelVersion, initialises refs on the first model, persists the
  reproduction script (whose `_render_holdout_eval` cell scores the holdout)
  and refuses to finish unless `pipeline_run_scientific_evidence_complete`
  holds: a `final_holdout` evaluation with metrics, test predictions, a
  ModelVersion with a `role = model` artifact, plus the trained winner, locked
  feature set, preprocessing steps, hyperparameters per candidate, complete
  `cv_fold` evaluations keyed by `summary.fold_number`, dependency lock and
  feature manifest.
- **The default run already contains most of what a loop would try.**
  Auto-train roots cap candidates at 8 (`max_candidates=8`, 600 s, 12 trials);
  the default binary portfolio holds logistic regression, random forest,
  XGBoost, LightGBM and CatBoost; `balanced_variants` adds class-weighted
  variants whenever the training profile is imbalanced
  (`MEANINGFUL_IMBALANCE_RATIO = 2.0`, `MEANINGFUL_MINORITY_FRACTION = 0.35`),
  which is looser than the `class_imbalance` warning (binary minority < 10 %);
  `tuning_plan` adds one nested-CV `__tuned` variant of the strongest available
  family; a branch lifts the candidate cap to everything its change set
  implies (`cap = max(cap, len(planned))`). What a branch can still add:
  `extra_trees` / `gradient_boosting` (binary), `ridge` / `lasso` /
  `elasticnet` / `extra_trees_regressor` / `gradient_boosting_regressor`
  (regression).
- **Trust checks and the optimizer are done** (P5.1-A, P5.2-A): the five
  `TEST_PARTITION_CHECKS` read test rows; the winner's out-of-fold operating
  curve (pooled and per fold) is stored; `solve` finds the best threshold under
  constraints; `candidate_thresholds` always includes "flag nothing"; a person
  records `operating_point_chosen` (human only).
- **Governance exists**: `improve.next_action` registry row (cross-check, cap
  L1, no `answer_kinds`), `ImprovementActionProposal`, budgets, switches.
- **Workers run one job at a time** (`cli/main.py` → `process_next_job`);
  agent runs are `agents.run` jobs on the same queue.
- **Product direction (founder, 2026-10-08).** `demo/product/v7_final/src/improve.html`:
  plain words, honest; "the rule table — always on; suggestions from the
  assistant — optional, switched off until tested"; outcome "No change beyond
  noise; current model kept." The founder finds the platform too complicated.

## What v1 is really worth

Two review rounds reduced the loop's honest v1 content to: **set the operating
point** (already built: P5.2-A engine + P5.2-UI), **try the few families the
default run does not include**, and **one honest look at the final test set**
for the accepted try. Class weights, boosters and a tuned variant are already
in every default run; feature engineering, hyperparameter steps and leakage
fixes are not safely automatable in v1 (§1c).

| Option | What the person gets | What it costs | Honest limits |
| --- | --- | --- | --- |
| **A — full loop engine** (this ADR's design) | the Improve screen of the demo: goal, budget, unattended tries, "beats the noise" verdicts, one sealed final look, honest look counts | a new evidence-lock mode (`0078`), sealed runner/persistence/codegen paths, a job state machine with watchdogs, token clamps, two migrations; four prompts ≈ L + M + L + M (≈ 2 500–3 500 lines incl. tests) plus P5.5-A, P6.5-A, R2-A | in v1 only extra families are tried (≤ 2 for binary); power at the default 5 folds is low (§5); most loops end "no change beyond noise"; one full re-run per acceptance |
| **B — guided Improve without an engine** | the same screen as a guided flow over existing pieces: goal → operating point (P5.2 card, no retrain) → "try another family" = an ordinary branch (`family_include`, scored normally) → the §5 verdict ("beats the noise" / "within noise") computed as a **read model** from the locked `cv_fold` evaluations of parent and child → each try labelled with the plan's holdout look count ("this is look k on this split") | one UI prompt (P5.5-B, Sonnet, M) and one small read-model prompt (comparison verdict + look count, Opus, S); no migration, no new job, no sealed mode | every try spends a holdout look (labelled, never hidden); nothing runs unattended; no budgets or stop rules; the person clicks each try |
| **C — defer Improve** | nothing now; the demo page stays "Planned" | 0 | the demo's central honesty story ("final test set used once") waits |

**Recommendation: B.** It delivers the demo's words and its honesty
(operating point, the same folds, a labelled look count, "kept the current
model") with pieces that exist and are tested, in one UI prompt; A spends two
migrations and a new evidence-lock mode to automate at most two extra tries at
low power; the look-count label makes B honest where A would be sealed. A's
design stays on file for when people demonstrably want more tries than they
click (R2-A can then be run on B's data: how often does "try another family"
beat the noise?). **Q1 to the founder: A, B or C?** The rest of this ADR is
the option-A design; §5 (verdict) and §6.4 (look count) also serve option B.

## Decision (option A design)

### 0. The loop in three sentences

1. You say what "better" means (one metric, optionally with a constraint such
   as recall ≥ 0.80) and how much it may cost.
2. DCLab first checks whether a different decision threshold already meets the
   goal on the current model (no retraining); then it tries, one at a time, the
   model families the current run has not tried, each on the same
   cross-validation folds, and keeps the current model unless a try clearly
   beats the noise.
3. A person accepts the result; only that one accepted model is scored on the
   final test set, once.

### Summary (ten lines)

1. **Two actions** (§1): set the operating point (no retrain) and
   `family_include` of a family the run has not tried; everything else is
   "later" (§1c).
2. **The rule table is the loop**; the agent proposer is an optional later
   add-on, recorded beside it and never applied at cap L1 (§2).
3. **Every try is a sealed CV-only child experiment** (`evidence_mode =
   sealed`): today's evidence lock minus exactly four holdout clauses (§6.1).
4. **The holdout is scored once per loop**, after a person accepts, by one
   ordinary branch of the probe's parent with the probe's own change set, its
   realised budget pinned, aborted at `SELECTION_LOCKED` if its CV differs
   (§6.2).
5. **Acceptance** = paired per-fold deltas on identical folds must exceed
   `t_{1−0.05/T, K−1}` Nadeau–Bengio-corrected SEs; T = the loop's own
   pre-registered number of new candidates; no noise floor (§5).
6. **Comparability** per try: the probe is a strict superset of the incumbent
   with identical shared candidates, else `not_comparable` (§5).
7. **Plan-wide N** only bounds proposing (30, hard 60) and feeds the caveat.
8. **Budgets** and **stop rules** have one enforcement point each and a fixed
   precedence; "no change beyond noise; current model kept" is a normal
   outcome (§3, §4, §12).
9. **The loop is a re-enqueued state machine** on `ml_jobs`: one step per job,
   never waiting on another job (§8).
10. **Never AI**: stop rules, selection, acceptance and holdout scoring are
    deterministic code or a person (ADR 0008 §10).

### 1. Typed action space (v1)

Owner: `app/services/improve/action_space.py` (P5.4-A2). Every
`ExperimentChange` kind appears exactly once (closure test): one row is an
action, seven are refused.

| # | Action | Value rule | Preconditions | Validator | Retrain? | New candidates `k_i` (counted in T and N) | Motivated by |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OP | **set the operating point** (no `ExperimentChange`) | `solve(load_curve(result["operating_curve"]), PointObjective(objective))` on the incumbent's pooled out-of-fold curve | binary; goal has an `objective` with constraints | `constraint_report` | **no** | 0 (not in T) | iteration 0 of a constrained goal |
| F+ | `family_include` of one installed family not in the lineage's portfolio | next family in `FAMILY_PREFERENCE` (code-owned order) over `available_families(task_type)` − portfolio | such a family exists; **the include must not move the `__tuned` variant** (`tuning_plan(task_type, families ∪ {f})` picks the same family as before, else the family is skipped: the probe must stay a strict superset); the probe budget allows the new candidates (§3) | `materialize_branch` (`unknown_family`, `family_already_in_portfolio`) | yes | `|ids(assemble_candidates(start ⊕ change)) − trained ids(incumbent)|` computed with the **pure generator at plan time** (a branch lifts the candidate cap, so e.g. an `extra_trees` include on an imbalanced root may add `extra_trees` plus previously capped `*__balanced` variants: k = 3, not 1) | none required: the families not yet tried are the plainest next step |
| — | `class_weighting`, `family_exclude`, `hyperparameter_override`, `threshold_objective`, `metric_override`, `feature_transform_add`, `feature_transform_remove` | — | **refused in v1** (§1c) | validator rejects | — | — | — |

Rules: one action per try; identity dedupe over the loop and the lineage
(`_identity`, walking `parent_pipeline_run_id`); no feature transforms in v1,
so `check_feature_budget` is called with `requested = 0` (its docstring is
aligned to "per loop" by P5.4-A2).

#### 1b. The rule proposer (`rule_proposer.v1`) and the goal

Owner: `app/services/improve/rule_proposer.py`, pure over the incumbent's
locked evidence, the stored findings restricted to `PROPOSER_READABLE_CHECKS =
FINDING_CHECKS − TEST_PARTITION_CHECKS` (ten checks; a test asserts the set),
the operating curve and the loop's own history. **The try sequence is
pre-planned at loop start**: OP (if constrained), then F+ for each eligible
family in preference order, truncated to `max_iterations`; each planned try's
`k_i` comes from the pure generator diff against the start's trained ids;
later evidence can only remove tries, never add. T = Σ k_i over the planned
F+ tries (§5).

| Step | Proposes |
| --- | --- |
| 0 | **OP** on the start's curve, recorded as iteration 0 (`operating_point`), shown as "the current model already reaches … at threshold t (met on CV)" or "no threshold meets the constraint; the model must change"; the loop **continues** |
| 1…m | F+ for each eligible family |
| end | `None` → stop `proposer_exhausted` |

After an acceptance, if the new incumbent carries a blocking finding (§4) the
loop **reverts the incumbent pointer** to the previous incumbent (no probe is
trained) and stops `blocked`.

**Goal (`ImproveGoal`, ≤ 4 KB).** Exactly one of:

- `plain = {metric}`: **must equal the run's primary metric** (the metric the
  probes select their winner by, `selection_metric`; otherwise an old
  candidate could "win" on the goal without any new model, uncounted in T) and
  must be threshold-free: binary `roc_auc`, `pr_auc`, `log_loss`, `brier`
  (the binary default primary metric `pr_auc` qualifies); multiclass
  `log_loss`, `accuracy`, `macro_f1`; regression `rmse`, `mae`, `r2`, `mape`.
  Threshold metrics as plain goals are 422 `goal_needs_constraint`.
- `objective = {maximize: m ∈ CURVE_METRICS − {flagged_share}, constraints:
  [≤ MAX_CONSTRAINTS rows {metric, op, value}]}` (binary only); `maximize ∈
  NEEDS_CONSTRAINT` (`precision`, `recall`, `specificity`) without a
  constraint is 422; scored per fold at each run's own solved threshold (§5).

Plus an optional `target = {metric, value}`. The goal never changes the run's
spec or primary metric; for a constrained goal the **final run** receives the
goal's constraints as its `SearchConfig.objective` (§6.2) so the locked
threshold is the goal's operating point.

#### 1c. Later actions (each needs its own ADR amendment with tests)

| Action | Why not in v1 (review reason) |
| --- | --- |
| `class_weighting balanced` (W) | can never fire: `balanced_variants` already adds weighted variants whenever minority share < 35 % or ratio ≥ 2, looser than the `class_imbalance` warning (binary minority < 10 %), so weighted candidates always exist when the warning fires; a lineage at `class_weighting none` dedupes W by its singleton `_identity`; without a profile `balanced_variants` returns nothing even with `force` |
| `family_exclude` as undo (F−) | can never fire: `rejected_suspicious` covers the same `fail` checks as `blocking_finding`, so an accepted incumbent never carries one; and the rule would reject the model F+ just beat. Replaced by reverting the incumbent pointer (§1b) |
| `family_exclude` of the winner ("simpler, not better") | needs a non-inferiority acceptance rule |
| regularization steps, the tuned variant of the current family | overrides apply to untuned candidates only; the generator tunes the strongest available family, not a named one |
| calibration via custom weights | class weights do not fix calibration |
| collinear / low-importance drops | supervised selection on the evaluation folds, invisible to the feature contract |
| role re-type, `datetime_extract`, feature reverts, registry extensions (`ratio`) | no motivating finding; P5.0 contract work first |
| loops on time-ordered SplitPlans | expanding-window folds are heteroscedastic; the curve uses the last fold only |

### 2. Proposers and the optional agent add-on

The **rule proposer** is the loop. The **agent proposer** (P6.5-A,
`agent:improvement_hypothesis`, class
`app/agents/classes/improvement_hypothesis.py` — not yet existing) is a
second answer recorded beside the rule's and never applied at cap L1.

| Level of `improve.next_action` | Applied | Recorded | Proposal row |
| --- | --- | --- | --- |
| `off` / AI off / `proposers.agent = false` (default) | rule | iteration row `agent: "off"`; **no** `decision_point_resolved`, `agent_runs` or `llm_invocations` row (ADR 0008 §8) | none |
| L0 | rule | one `decision_point_resolved` per try (ADR 0008 §7; actor `rule`; subject = the probe) | `shadow` |
| **L1 (cap today)** | rule | same | disagree → `proposed` `ImprovementActionProposal` (acknowledgement; never branches) |
| L2 (future) | agent `family_include` when the validator accepts; else rule | `used.source = ai` | `applied` |
| abstain / invalid / timeout / budget / switch | rule | `agreement: abstain | unavailable | late` | `rejected_by_validator` for invalid output |

**Timing (never a wait).** The agent run is an `agents.run` job on the same
one-job worker, so a step can never wait for it. At every level the proposing
step enqueues the agent run and exits; at L0/L1 the rule action is branched in
the same step and the agent answer is reconciled later or marked `late`; at a
future L2 the step enqueues the agent run only, and the agent run's terminal
hook (or the watchdog after `limits.specialist.wall_s`) continues the loop with
the agent's or the rule's action.

**Before any level above L1 (P6.5-A):** `answer_kinds = {family_subset}` on
the registry row with a test; ADR 0008 §2c / Non-goals amended (an applied
answer is a branch change); agent output validation: a §1 row, identity not
tried, `cited_findings` ⊆ envelope finding ids (never a `TEST_PARTITION_CHECKS`
id), `cited_cv_metrics` checked against the envelope, `rationale` ≤ 4 000 chars
only in `agent_proposals.proposed_rationale`, `_no_holdout` on every text
field, envelope under ADR 0009 §8 data classes.

### 3. Budgets and enforcement points

| Budget | Field | Default | Hard max | Enforced where | At exhaustion |
| --- | --- | --- | --- | --- | --- |
| tries | `max_iterations` | 6 | 20 | step, before proposing | stop `budget_iterations` |
| wall time | `max_wall_seconds` | 7 200 | 43 200 | step, before enqueueing a probe; a probe is never started unless its full budget (below) fits the remainder | stop `budget_wall` |
| probe time budget | derived | the start's **realised** training time for its trained candidates plus a code-owned estimate for the `k_i` new candidates (a cap never changes completed candidates; a binding cap makes the try `not_comparable`) | 3 600 s per probe | `SearchConfig.max_training_seconds` of the probe (service-only kwarg) | try `not_comparable` (`truncated`) |
| training seconds | `max_training_seconds_total` | `max_iterations ×` probe budget | 28 800 | step, from `stage_timings` | stop `budget_compute` |
| LLM cost | `max_llm_micros` | 2 000 000 | `budgets.project_month_micros` | the step skips the agent when `settled + held + worst_case > cap` | agent proposer stops; loop continues |
| feature attempts | `FEATURE_ATTEMPT_BUDGET_PER_LOOP` | 8 | 8 | `check_feature_budget` (v1 requests 0) | n/a |
| patience | `patience` | 3 | `max_iterations` | after each evaluated try; counts `rejected` (incl. `no_change`); `not_comparable` / `failed` / `cancelled` count toward `max_iterations` only | stop `no_improvement` |
| live loops | one per SplitPlan (partial unique) and `max_live_improve_runs_per_workspace` | 2 | 4 | `POST` under the `ml-runs:<workspace>` advisory lock, counted over `improve_runs` in `queued` / `running` / `finalizing` (`waiting_user` holds only its plan's slot and **expires after 14 days** to `completed` / `kept_current`); `labs.improve` jobs are **not** counted by `ensure_run_capacity` (a loop must not block itself); probes are; a 429 when enqueueing a probe leaves the iteration `planned` and reschedules with backoff inside the wall budget | 429 |
| comparisons per plan | `CV_COMPARISON_BOUND_PER_SPLIT_PLAN` | 30 | 60 | step, before proposing; a **token** start is refused when `N + T > bound / 2` | stop `comparison_bound` |
| token starts | per principal | 5 / hour | 5 / hour | counted in the DB over `improve_runs.created_by_service_token_id`, clamped to `min(code default, workspace policy)` | 429 |
| fresh splits | per (DatasetVersion, target) | 2 | 2 | `POST` with `fresh_split` (humans) | 409 |

Every stop keeps the best so far and **cancels the in-flight probe**
(`request_job_cancellation`).

### 4. Stop rules and precedence

| Order | `stop_reason` | Fires when | Outcome |
| --- | --- | --- | --- |
| 1 | `cancelled` | cancel requested, or authority revoked (§8; detail `authority_revoked`) | `cancelled` |
| 2 | `failed` | the step job exhausted `max_attempts`, or the same try failed twice | `failed` |
| 3 | `blocking_finding` | the incumbent has `target_leakage` or `implausible_score` `fail` at start; after an acceptance the pointer is reverted first (§1b) | `blocked` |
| 4 | `goal_met` | a `target` exists, the incumbent is feasible (`solve` status `optimal` for constrained goals) and `mean_K − t_{0.95,K−1} · se_NB(fold scores)` clears the target; labelled "met on CV" (a selected incumbent's CV is optimistic) | `goal_met` |
| 5 | `no_improvement` | `patience` consecutive `rejected` tries | `kept_current` if nothing accepted, else `improved` |
| 6 | `unstable_evidence` | incumbent `fold_instability` warning with `worst_fold_at_baseline = true` | same |
| 7 | `comparison_bound` | N ≥ bound | same |
| 8 | `budget_*` | §3 | same |
| 9 | `proposer_exhausted` | no planned try left | same, or **`nothing_comparable`** when no try could be compared (every try `not_comparable`): the sentence is then "no try could be compared fairly", never "no change beyond noise" |

Eligibility at `POST` (409 with reason): start `COMPLETED` and locked;
`evidence_mode = scored`, or a sealed root from `fresh_split`; the winner's
actual fold count (`n_folds`) ≥ 5; validation strategy not time-ordered;
the start skipped no candidate for time and has no `truncated` tuning
(`start_truncated` otherwise); no blocking finding; N < bound; at least one
eligible F+ (`nothing_to_try` otherwise — the honest answer for most default
runs).

### 5. Selection across iterations (the scientific core)

**Only CV on the same SplitPlan.** Two experiments are comparable iff they
share `split_plan_id`.

**Evidence source.** Per-fold scores are read from the **locked** `cv_fold`
`model_evaluations` rows of each run's winning candidate, keyed by
`summary.fold_number` (`experiments.result` stays writable after the lock, so
it is never the source). Constrained goals use each run's stored
`operating_curve` per-fold counts: curve folds carry no fold number, so they
are paired positionally **and** the comparison asserts identical per-fold
(positives, negatives) on both sides and stores both `curve_digest`s; a
mismatch is `not_comparable`. The comparison inputs are frozen into
`improve_iterations.comparison`.

**Per-fold scores.** Plain goal: `s_E,f` = the winner's fold-f value of the
primary metric (sign-flipped for `LOWER_IS_BETTER`). Constrained goal: `t_E =
solve(curve_E, objective).index` (returned even when infeasible); `s_E,f` = the
objective metric at `t_E` on fold f (`goal_fold_scores(curve, objective)`, a
pure helper P5.4-A1b adds next to `fold_spread`); a fold whose denominator is
below `FOLD_MIN_DENOMINATOR` has no finite score → the try is
`not_comparable`. If E is infeasible, the comparison uses the per-fold
constraint shortfall at `t_E` with the same rule; a feasible candidate beats an
infeasible incumbent only through that paired rule.

**Comparability (per try, before any delta).** The probe must be a strict
superset of the incumbent: incumbent trained candidate ids ⊆ probe trained
ids; for every shared id the fingerprint (incl. `library_version`) and the
per-fold metrics are identical (1e-9); neither run skipped a candidate for
time nor has `truncated` tuning. Else `not_comparable` with reason
`engine_or_data_drift` / `truncated` / `superset_violated`. This replaces any
commit check (`_git_hash` reads disk HEAD, returns `None` without `.git`, and
ignores which code a worker loaded) and proves determinism before the final
run.

**Paired deltas.** `d_f = s_cand,f − s_inc,f` over K folds; `mean_d`;
`sd_d = sd(d_f, ddof = 1)`; **`se_NB = sd_d · sqrt(1/K + 1/(K−1))`**
(Nadeau–Bengio; ×1.5 at K = 5, ×1.45 at K = 10). No noise floor: the
`t_{K−1}` quantile already prices a lucky small `sd_d` under the NB model, and
the only available floor (`fold_noise_std`) is the chance SD of one model's
fold score on the primary metric, not of a paired delta. **`d ≡ 0`** (the old
winner stays the winner) → `rejected`, reason `no_change`, counts toward
patience. Constant non-zero `d` (`sd_d = 0`, `mean_d ≠ 0`) → `not_comparable`.

**Pre-registered multiplicity.** `T = Σ_i k_i` over the planned F+ tries
(§1b), frozen on `improve_runs.planned_comparisons` at start; T ≥ 1; OP adds
nothing. Plan-wide N is not in the margin.

**Acceptance rule ("try i beats the incumbent").**

```
q        = t_{1 − 0.05 / T, K − 1}
margin   = q · se_NB
δ_min    = max(0.001, 1 / n_min) for count-based metrics, where n_min is the smallest
           fold denominator (flagged rows for precision; positives for recall);
           0.001 · |s_inc| otherwise
accept   ⇔ mean_d > margin  AND  mean_d ≥ δ_min  AND  not rejected_suspicious
```

**Error rate.** Family-wise over the loop at α = 0.05 under the NB model
(fold-score correlation ρ ≤ 1/K): simulated per-loop false acceptance ≈ 2.4 %
(K = 5, T = 6); i.i.d. folds would give 0.6 % (the rule is conservative there);
ρ = 2/K gives 6.5 % — the rule assumes ρ ≤ 1/K, which the symmetric null of
§10 checks. q: (K = 5, T = 6) 3.96; (5, 10) 4.60; (5, 11) 4.70; (10, 6) 2.93;
(10, 10) 3.25.

**Power (NB-correlated folds), per true per-fold gain in fold-delta SDs:**

| K | T | 1 SD | 2 SD | 3 SD | 4 SD |
| --- | --- | --- | --- | --- | --- |
| 5 | 6 | 0.11 | 0.42 | 0.78 | — |
| 5 | 11 | 0.06 | 0.29 | 0.63 | 0.87 |
| 10 | 6 | 0.33 | 0.92 | 1.00 | — |

**Worked example (binary, primary `pr_auc`, ≈ 2 000 rows).** With K = 5 and a
typical paired fold-delta SD of 0.020, `se_NB = 0.020 × 0.671 = 0.0134`; at
T = 6 the margin is `3.96 × 0.0134 ≈ 0.053` PR-AUC: a try must beat the
current model by more than about five PR-AUC points on average across the
folds. At K = 10 (`se_NB = 0.020 × 0.459 = 0.0092`, q = 2.93) the margin is
≈ 0.027. Hence K ≥ 5 is required and 10 is recommended; K is fixed per
SplitPlan, so the recommendation is actionable only at root creation or
through `fresh_split` with `validation_folds = 10` (§6.5; Q3).

**`rejected_suspicious`.** A try whose own findings include `target_leakage`
or `implausible_score` `fail` is never accepted.

**Plan-wide N (bound and caveat only).** N = experiments on the plan (filtered
by `workspace_id`) with a `model_selection_decisions` row, plus queued/running
experiments on the plan, plus the `k_i` of in-flight loop tries. Read under the
lock order `ml-runs:<workspace>` → `split-plan-comparisons:<plan>`; frozen on
each iteration row (`comparisons_before`). Bound 30 (hard 60): at N ≥ bound the
loop stops `comparison_bound` and `POST` is refused (409
`split_plan_exhausted`); manual branches stay allowed and show the caveat:
"Best of N tries on the same folds: the cross-validation number is a little
optimistic; the final test set is the honest estimate." The final run's
holdout number is shown on its own; the generic compare view's holdout delta
against the start is a known residual for P5.5-A to hide for loop runs.

### 6. The holdout

**Decision: tries are sealed CV-only runs; the holdout is scored once, after a
person accepts.**

1. **Sealed evidence (P5.4-A1a, migration `0078_sealed_evidence`).**
   - `experiments.evidence_mode varchar(8) NOT NULL DEFAULT 'scored' CHECK IN
     ('scored', 'sealed')`, set at **INSERT** through
     `create_pipeline_run(evidence_mode=…)` (the branch service's
     `before_commit` runs after the flush, so it cannot set it); **insert-only**:
     `experiments_lineage_guard` is dropped and recreated with `evidence_mode`
     in its `UPDATE OF` list and `prevent_experiment_lineage_violation`
     refuses any change of it.
   - **One source of truth.** The runner derives `score_holdout` from the
     experiment's `evidence_mode`; `SearchConfig.score_holdout` is an explicit
     **service-only kwarg** of `execute_experiment`, excluded from the
     `search_from_mapping` allowlist and from every `/v1` schema; never stored
     in `config`.
   - **Sealed predicate** = today's `pipeline_run_scientific_evidence_complete`
     **minus exactly four clauses** — (a) the `final_holdout` evaluation with
     metrics, (b) `experiment_test_predictions`, (c) the ModelVersion links,
     (d) the `role = model` artifact — **plus `NOT EXISTS` of each** (a stray
     holdout row blocks a sealed lock); every other clause (trained winner,
     locked feature-set version with rows, preprocessing steps, hyperparameters
     per candidate, every trained candidate with all folds as completed
     `cv_fold_runs` each with a `cv_fold` evaluation keyed by
     `summary.fold_number`, dependency lock, feature manifest) is
     **byte-identical** and the scored branch of the replaced function is
     textually unchanged (gate diff test). `scientific_evidence_status`
     (`reproducibility → model_links → holdout_complete`) gets the same sealed
     variant.
   - Sealed runner: ends after `winner_locked`, importance and curve; no
     `_fit_and_score_holdout`, predictions, artifacts, `final_*` events;
     `result["holdout"] = {"scored": false, "reason": "improve_probe", "rows": n}`.
     `run_persistence`: no ModelVersion, no `initialize_refs_on_first_model`,
     no model artifact; the reproduction script omits the final-refit and
     holdout cells (`_render_holdout_eval` not rendered; test: no `X_holdout`
     / `holdout_scores` text); the feature-set version may still move the
     `feature_recipe` ref (intended: it is CV evidence).
   - Checks: `investigate_result` with a train-only `SplitFeatures(test =
     None)`; the five `TEST_PARTITION_CHECKS` → `not_evaluated` with reason
     `holdout_sealed`; `multicollinearity` runs; `overfit_gap` → `not_evaluated`
     (no final fit).
   - Readers: model card, codegen, notebook, `/v1` read models render "final
     test set: not used (improve try)"; `champion_promoted` and `move_ref` to
     `champion_model` refuse sealed runs; **`model_versions` gets an INSERT
     trigger refusing rows whose `pipeline_run_id` is sealed** (batch
     prediction accepts any locked run with an artifact, so defence in depth);
     `pipeline_verifier` accepts the sealed shape; `ctx.cancellable` never
     flips. **Branching a sealed parent is human-only**: `POST
     /v1/experiments/{id}/branches` and the catalog `branch_experiment` refuse
     token/agent principals when the parent is sealed (403
     `sealed_parent_human_only`), so a token cannot score a probe's holdout by
     branching it.
   - Amendment to ADR 0006 non-negotiable 2 (§ Proposed amendments 9).
2. **Final scoring = one ordinary branch run (P5.4-A1b/A2/A3).** On acceptance
   of try i, with the accepting human as actor: `branch_experiment(parent =
   the probe's parent_pipeline_run_id, changes = the probe's own change_set,
   intent = code template, service_overrides = …)` where a new
   **service-only `service_overrides` parameter** (not a request field) carries
   `evidence_mode = scored`, `expected_selection = {candidate_id, trained_ids,
   fingerprints, per-fold metrics, trials per fold}` (pinned from the probe),
   `max_training_seconds` = a generous cap (3 × the probe's realised time; any
   binding cap aborts before the look), and `objective` = the goal's
   constraints (so `solve_lock` locks the goal's threshold; the spec's
   primary metric and selection are unchanged). **All service-only fields are
   stripped whenever `config` is copied** (`branch_experiment`,
   `search_from_mapping`); a manual branch of a final run is an ordinary run
   (test). At `SELECTION_LOCKED` the runner compares with `expected_selection`
   and **aborts before `final_test_started`** on mismatch
   (`nondeterministic_rerun`; no look spent; the loop returns to
   `waiting_user` and the person may retry at most twice; the job payload
   carries `improve_run_id` so the final run's terminal hook drives
   `finalizing → completed | failed | waiting_user`). The design can fail
   safely if engine, libraries, image or installed families differ between
   probe and final run, or BLAS/CPU ties beyond 1e-9 — then no look is spent.
   Cost: one full re-run (folds × candidates × tuning), stated on the accept
   card.
3. **"Kept the current model" scores nothing.** With `fresh_split` this means
   a kept-current loop yields **no test number at all** (the sealed root has
   none); a root re-run path is a later amendment, not v1.
4. **Looks per SplitPlan** are counted **conservatively**: every `scored`
   experiment on the plan (filtered by `workspace_id`) that reached
   `SELECTION_LOCKED` (has a `model_selection_decisions` row) counts as a look,
   whether or not its `final_test_started` event was persisted
   (`PipelineRunObserver.emit` is best-effort) and whether or not it later
   failed or was cancelled. Per run: `sealed_set_consulted`,
   `sealed_set_looks_before/after`, `sealed_set_label` ∈ {`clean`,
   `carried_over`, `reported_before`, `reused`} as in Revision 1 (§6.4 table);
   a final run cancelled or failed **after** `final_test_started` is a look: the
   loop ends `completed` / `improved` with `final_experiment_id` set and the
   look recorded; cancelled **before** it → `waiting_user`, no look. A retried
   accept is only an HTTP replay (same iteration, same idempotency key);
   `accepted_iteration_seq` is write-once.
5. **Fresh SplitPlan.** `POST` with `start = {from_experiment_id, fresh_split:
   true, validation_folds?: 5 | 10}` creates a sealed default-config root
   (`carry_overrides: true` → `carried_over`) with a server-derived split seed,
   ≤ 2 per (DatasetVersion, target); humans only; its final run is the plan's
   only look (`initialize_refs_on_first_model` may then run for a project's
   first model — intended).
6. **Never an agent-visible holdout figure**; nothing after the final run reads
   holdout values (status, outcome, iteration rows and Inbox text are fixed
   before it); test 25 mutates holdout values.
7. **Residual.** Token-started plain experiments and branches still score the
   holdout and `get_findings` exposes test-partition status bits (amendment 6,
   Q5).

### 7. Auditing

| Artifact | One per | Content |
| --- | --- | --- |
| probe experiment | try | `branch_experiment` with a code-template `intent`, the action as `change_set`, `evidence_mode = sealed`; idempotency key `improve:<run_id>:<seq>` (reserved prefix) |
| `improve_iterations` row | try | rule/agent answers, agreement, frozen comparison (`fold_number`-keyed deltas, `mean_d`, `se_NB`, q, margin, T, `δ_min`, curve digests, verdict, reason), `k_i`, `comparisons_before`, notes, timestamps |
| `decision_point_resolved` | try, **only when the agent proposer ran** | ADR 0008 §7; actor `rule`; subject = the probe |
| `experiment_accepted` | loop end, written **only by the improve service** (idempotency key `improve:<run_id>:accept`) | actor `human`; subject = the accepted probe or the start; `details.improve_run_id`; readers trust only `improve_runs.acceptance_record_id`; a generic record carrying `details.improve_run_id` is refused |
| `improve_runs` row | loop | §9 |
| Inbox notice | `waiting_user`, `blocked`, `failed` | existing `inbox_read_service` |

**Human acceptance.** `POST /v1/improve-runs/{id}/accept` (`HUMAN_ONLY_ROUTES`
+ `require_human_session`; tokens 403; Idempotency-Key required; `If-Match`)
with `{iteration: i}` — only an iteration of this run with status `accepted` —
or `{keep_current: true}`; decided on CV only. DB CHECK:
`final_experiment_id IS NOT NULL ⇒ accepted_by_user_id IS NOT NULL AND
acceptance_record_id IS NOT NULL`; `acceptance_record_id` is a three-column
CFK `(workspace_id, project_id, id) → project_decision_records`
(`uq_pdr_workspace_project_id`).

### 8. The loop job: a re-enqueued state machine, cancel, resume

- **One step per job.** `job_type = improve`, `handler_key = labs.improve`,
  `target_id = improve_run_id`, payload `{improve_run_id}`; the handler loads
  the run **filtered by `job.workspace_id`** and exits unless `job.id =
  improve_runs.continuation_job_id` (stale or forged jobs do nothing). A step:
  lock the run (`FOR UPDATE`; lock order **`improve_runs` before `ml_jobs`**
  everywhere), re-check authority, evaluate a terminal probe, apply §4,
  propose, enqueue the probe, create the next continuation, persist, exit.
- **Continuation.** With every probe the step creates **one** watchdog
  `labs.improve` job (`available_at = now + DEFAULT_HEARTBEAT_TIMEOUT_SECONDS`)
  and stores its id in `continuation_job_id`. The probe's terminal hook does
  not enqueue a second job: it runs **after** the probe job's terminal commit,
  in its own transaction, best-effort, never touching the probe's status, and
  pulls the watchdog forward (`UPDATE ml_jobs SET available_at = now() WHERE id
  = continuation_job_id AND status = 'queued'`). Paths only the watchdog covers
  (no hook fires): a queued probe cancelled through
  `/v1/experiments/{id}/cancel`, an attempt-exhausted job failed in
  `claim_next_queued_job`, a job failed by `recover_abandoned_jobs`. A partial
  unique index on `improve_iterations(improve_run_id) WHERE status IN
  ('planned','running')` guarantees one live try. The final run's job carries
  `improve_run_id` too; its terminal hook drives `finalizing`.
- **Authority per step.** Human-started: creator still has
  `can_execute_workspace_ml` on the workspace. Token-started: the conditions of
  `authenticate_service_token` (token active, kill switch, creator active,
  creator's explicit workspace role) plus the `experiments:write` scope plus
  `can_execute_workspace_ml`. Failure → stop `cancelled` / `authority_revoked`,
  in-flight probe cancelled. Probes of token-started loops carry
  `initiated_by_service_token_id` (feature contract `proposed_by = "agent"`).
- **Cancel.** `POST …/cancel` (`If-Match`): lock the run, conditional status
  update, `request_job_cancellation` on the continuation and on the live probe
  or final run. Humans may cancel any live state; tokens only `queued` /
  `running`. The generic `/v1/experiments/{id}/cancel` **refuses token
  principals** for a probe or final run of a loop unless the loop itself is
  token-cancellable. A human cancel during `finalizing` after
  `final_test_started` still completes the look (§6.4).
- **Resume.** Steps are idempotent (iteration row with its idempotency key
  written before `branch_experiment`; `_existing_branch`); `improve:` and
  `improve-final:` join `RESERVED_IDEMPOTENCY_KEY_PREFIXES`; the run's own
  idempotency is bound through the existing `idempotency_keys` table
  (principal-scoped).

### 9. Data model (P5.4-A2, migration `0079_improve_runs`, additive)

As Revision 1 §9 with these changes: experiment references and
`acceptance_record_id` are three-column CFKs; `improve_iterations` carries
`project_id` and a three-column CFK `(workspace_id, project_id,
improve_run_id) → improve_runs`; partial unique on live iterations; the
acceptance CHECK of §7; `status` transitions add `finalizing → waiting_user`
(`final_run_cancelled_before_look`, `nondeterministic_rerun`) and
`waiting_user → completed` on expiry; `waiting_user_expires_at`; comparison
and `experiment_id` set once (null → value). `0078_sealed_evidence` (A1a):
`experiments.evidence_mode`, `CREATE OR REPLACE` of the predicate, recreated
`experiments_lineage_guard`, the `model_versions` INSERT trigger, mirrored in
`create_all` and the gate test; truth artifacts regenerate. Downgrade note: on
`0078` downgrade, already-locked sealed rows look like scored runs with no
holdout and the champion refusal disappears — the note says so and the
downgrade refuses while sealed rows exist unless forced.

### 10. Interfaces per surface, and the benchmark

Surfaces as Revision 1 §10 (Studio direct; assistant L1 `ToolCallProposal`
under `lead.improve` with `estimated_cost_micros` / `estimated_duration_s`;
MCP/token direct `POST` with clamps: budget only lowered, `proposers.agent`
forced off, no `fresh_split`, 5 starts/h counted in the DB, refused when
`N + T > bound/2`, cancel only `queued`/`running`, never accept). The catalog
tools `improve` and `get_improve_run` are **built in P5.4-A3**; A5-A wires the
assistant. Token read models drop user ids.

**Benchmark protocol (R2-A).** As Revision 1 (scored roots, generated goals,
proposer configs) with: (a) the **reseed null** (probes differing only by
`SearchConfig.seed`) is a ρ ≈ 0 smoke test: ≥ 200 loops, Wilson UB ≤ 5 %; (b)
a **symmetric null** — synthetic data with exchangeable feature halves, the
same family trained on each half as incumbent and probe — checks the ρ ≤ 1/K
assumption with real fold correlation; (c) a pure simulation at ρ = 1/K with
best-of-k selection asserts the per-comparison acceptance rate sits in a band
around α/T (i.i.d. folds cannot detect a missing NB factor). **Benchmark-only
modes** (reseed probes, simulated L2) exist only in the R2-A harness: not a
`SearchConfig` field, `/v1` field, policy key or proposer flag; refused outside
the benchmark setting (test).

### 11. AI off, kill switches, levels

As Revision 1 §11: rule-only by default; no agent rows of any kind with AI
off; L0/L1 identical tries by construction.

### 12. Honest limits

- The loop's v1 value is the **operating point** (no retraining) and an
  **honest single look** at the final test set; its only retraining action
  tries the one or two model families the default run leaves out. Class
  weights, boosters and a tuned variant are already in every default run.
- It detects **large** effects only: at the default 5 folds a try must beat
  the current model by about three fold-delta standard deviations (≈ 5
  PR-AUC points in the worked example of §5). Most loops will end "No change
  beyond noise; current model kept." That is the correct answer, not a
  failure; for most default runs the honest answer at `POST` time is
  "nothing to try".
- It does not engineer features, tune hyperparameters, fix leakage, re-split
  or change the goal.
- A cross-validation number chosen among N tries is optimistic; the one
  holdout look is the honest number, and only after a person accepts; with a
  fresh split, keeping the current model yields no test number.
- It costs one full re-run for the accepted try.

## Consequences: P5.4-A split (option A only)

| Prompt | Model / size | Builds | Must have a test that |
| --- | --- | --- | --- |
| **P5.4-A1a — Sealed evidence mode** (first) | Opus 5.5 (xhigh) · **L** · migration `0078_sealed_evidence` · ml-correctness + security + db-migration reviewers | `experiments.evidence_mode` (insert-only; `experiments_lineage_guard` recreated); the four-clause sealed predicate + `NOT EXISTS`, `scientific_evidence_status` variant; `create_pipeline_run(evidence_mode=…)`; sealed runner path (derived from `evidence_mode`); `run_persistence` without ModelVersion / refs / artifact / predictions; codegen without refit/holdout cells; readers ("not used"), champion / ref-move refusal, `model_versions` INSERT trigger, verifier; sealed-parent branch refusal for tokens | (1) a sealed run never calls `_fit_and_score_holdout`, emits no `final_*` event, has no `final_holdout` row, predictions, ModelVersion or model artifact, is `COMPLETED` + locked, accepted by `load_parent_context`; (2) the scored branch of the replaced SQL is textually unchanged; a stray `final_holdout` row blocks a sealed lock; a scored run without one blocks; a probe missing one `cv_fold` evaluation cannot lock; `UPDATE experiments SET evidence_mode` is refused; (3) the five `TEST_PARTITION_CHECKS` are `not_evaluated` / `holdout_sealed`, `multicollinearity` runs; (4) the reproduction script has no `X_holdout` / `holdout_scores`; (5) champion promotion, ref moves and a `model_versions` INSERT refuse sealed runs; batch prediction cannot target one; (6) token branch of a sealed parent is 403; (7) migration up/down on a disposable DB, one head, gate test, truth artifacts |
| **P5.4-A1b — Reproducible final run** (second) | Opus 5.5 (xhigh) · **M** · no migration · ml-correctness reviewer | service-only kwargs on `execute_experiment` / `branch_experiment` (`service_overrides`: `evidence_mode`, `expected_selection`, pinned budget, objective), stripped on every `config` copy and excluded from `search_from_mapping`; `expected_selection` abort at `SELECTION_LOCKED`; realised-time budget helper; comparability helper (superset, fingerprints incl. `library_version`, per-fold equality, `truncated` / skipped detection); `goal_fold_scores` with per-fold count assertion and `curve_digest` | (8) mismatch aborts before `final_test_started` (spy); a pinned re-run reproduces its selection within 1e-9; (9) a manual branch of a final run inherits none of the service-only fields; no `/v1` schema accepts them; (10) a binding time cap / `truncated` tuning / a moved `__tuned` variant / a changed shared fingerprint each yield `not_comparable` with its reason; (11) `goal_fold_scores` refuses mismatched per-fold counts and sub-`FOLD_MIN_DENOMINATOR` folds |
| **P5.4-A2 — The loop** (third) | Opus 5.5 (xhigh) · **L** · migration `0079_improve_runs` · ml-correctness + security + db-migration reviewers | `app/services/improve/` (`action_space.py`, `rule_proposer.py` with plan-time `k_i` from `assemble_candidates`, `comparison.py`, `service.py`, `loop_handler.py`); step job, watchdog, terminal hooks, locks; budgets, stop rules, N and looks; acceptance service; tables; reserved prefixes; `ensure_run_capacity` exclusion | (12) action-space closure; refused rows rejected; F+ that moves `__tuned` is skipped; `k_i` equals the real generator diff on an imbalanced root (= 3 for `extra_trees`); (13) `PROPOSER_READABLE_CHECKS` disjointness + byte-identical proposer output under mutation of the five checks; (14) the rule (pure): `fold_number` pairing, lower-is-better flip, K < 5 refusal, `se_NB`, `δ_min` with flagged denominators, `no_change` → rejected, constant non-zero → `not_comparable`, infeasible shortfall, `rejected_suspicious`; (15) null: 2 000 simulated loops at ρ = 1/K with best-of-k selection, per-comparison rate within a band around α/T and per-loop ≤ 5 % (Wilson UB); one real-dataset reseed set (≥ 20 loops, zero acceptances) labelled a smoke test; (16) "max precision s.t. recall ≥ x" records iteration 0 and trains afterwards; `goal_met` needs a feasible incumbent; plain goal ≠ primary metric and `NEEDS_CONSTRAINT` without constraint are 422; a class-weight-only probability shift under `roc_auc` / `pr_auc` is never accepted; (17) full loop on one real small dataset: sealed tries, one look after accept, `kept_current` scores nothing, `nothing_to_try` / `nothing_comparable` / `start_truncated` outcomes; final run failing or cancelled after `final_test_started` counts as a look, before it returns to `waiting_user`; (18) labels; (19) N counts probes, manual branches, in-flight runs and `k_i` under the lock order, frozen at enqueue; bound refuses `POST` and stops a loop; token start refused above bound/2; (20) each budget stops with its reason, cancels the live probe, keeps the best so far; (21) §4 precedence; shuffled labels fire stop rules before any try; (22) one worker finishes a loop; hook + watchdog firing together and a forged/stale `labs.improve` job → exactly one probe; the watchdog-only paths continue the loop; (23) cancel at a step boundary, mid-probe, in `finalizing`; direct probe cancel; accept-vs-cancel race; `waiting_user` expiry; (24) idempotent resume; planted `improve:` key refused; (25) authority: token revoked, creator deactivated, role downgraded → loop stops and the probe is cancelled; (26) AI-off golden vs L0 wrong fake agent; AI-off writes no `decision_point_resolved`; (27) migration, triggers, CHECKs, three-column CFKs |
| **P5.4-A3 — API, tools, accept** (fourth) | Opus 5.5 (high) · **M** · no migration · security reviewer | four routes, route sets, `If-Match`, Idempotency-Key with reserved prefixes and principal scoping, token clamps and DB-counted rate limit, read models with shaping and user-id dropping, `improve` / `get_improve_run` catalog + MCP tools and goldens, OpenAPI/SDK/CLI | (28) token 403 on `/accept`; no tool reaches `/accept`; cross-workspace 404 everywhere; (29) clamps, rate limit, `fresh_split` refusal and cap 2 with server seed; token cannot cancel `waiting_user` / `finalizing`; generic experiment cancel refuses tokens on loop runs; (30) the assistant `improve` is an L1 `ToolCallProposal` whose card carries cost and duration; accepting an L1 `ImprovementActionProposal` performs nothing; (31) the final run's actor is the accepting human; the acceptance record carries the reserved key; forged `details.improve_run_id` refused; the DB CHECK holds; (32) no `holdout|final_test` key in any improve read model for tokens/agents, and mutating holdout values changes no non-holdout field; benchmark-only modes unreachable through `/v1` |

Under option B: **P5.5-B** (Sonnet 5.5, M): the guided Improve screen over
`POST …/operating-point`, `POST …/branches` with `family_include`, and a new
read-model prompt (Opus 5.5, S) that computes the §5 verdict from locked
`cv_fold` evaluations and the §6.4 look count per plan; no migration.

## Alternatives considered

As Revision 1, plus: a noise floor from `fold_noise_std` (wrong scale and
metric; withdrawn); counting looks from `final_test_started` events only
(best-effort writes undercount; conservative count chosen); a `git_commit`
comparability check (vacuous; replaced by the per-try superset check); keeping
W and F− in v1 (provably never fire).

## Non-goals

As Revision 1; additionally no class-weight or family-exclusion actions, no
root re-run for fresh-split kept-current loops, no automatic acceptance.

## Proposed amendments to ADR 0006 / 0008 / 0009 (text not edited here)

1–5, 7, 8 as Revision 1 (with `answer_kinds = {family_subset}` only in 1, and
"the L2 agent run is enqueued, never awaited; its terminal hook continues the
loop" in 2).
6. **ADR 0008 §2b (founder decision, Q5).** Runs and branches started by
   service tokens or agents become CV-only, making holdout scoring a person's
   act; `get_findings` for agents then carries no test-partition status bits.
9. **ADR 0006 non-negotiable 2 / §5 (evidence lock).** A `sealed` mode:
   `experiments.evidence_mode` (insert-only), the sealed predicate = the scored
   predicate minus exactly the four holdout clauses plus `NOT EXISTS` of each,
   every other clause unchanged; sealed rows are locked, branchable by humans
   only, never promotable, never a ModelVersion.

## Open questions for the founder

1. **A, B or C?** (§ What v1 is really worth; recommendation B.)
2. **Acceptance is always a person** (recommended) — under A.
3. **Folds**: keep 5 as the default and offer 10 only through `fresh_split`
   (as designed), or make 10 the default `validation_folds` for new plans
   (power 0.92 vs 0.42 at a 2-SD effect)?
4. **Defaults** (A): 6 tries, 2 h, $2 AI, patience 3, bound 30 (hard 60), 2
   live loops, 14-day `waiting_user` expiry, 2 fresh splits, 5 token starts/h.
5. **Token-started runs CV-only** (amendment 6): accept the behaviour change
   for `POST /v1/experiments` and `/branches` under a token?
6. **Demo copy**: the demo's Try 1 (ratio feature), Try 2 (drop a column) and
   Try 3 (weaker class weights) are not v1 actions under A or B; change the
   demo's tries to "operating point" and "another family", or keep them as
   direction labelled "later"?

## Rejected or deferred review items

| Item | Decision | Reason |
| --- | --- | --- |
| S-R5 optional leave-one-fold-out threshold solve | deferred | removes in-sample threshold optimism but needs its own null evidence; the final run's objective fix is the v1 answer |
| S-R10 root re-run path for fresh-split kept-current | deferred (limit stated in §6.3, §12) | a sealed root cannot be re-run without a change set; needs a new path |
| S-R3 fail-closed look registration before `_predict(X_test)` | not adopted; conservative count instead | counting every scored run that reached the lock over-counts safely and needs no new write in the runner's holdout path |
| Make 10 folds the default SplitPlan | deferred to founder Q3 | changes every new plan; the ADR offers 10 through `fresh_split` |
| S-R1 "optionally require `config->>'score_holdout' = 'false'`" | rejected | `score_holdout` is no longer stored in `config` (single source of truth is `evidence_mode`) |
| S12 "or expire `waiting_user`" vs "count only queued/running/finalizing" | both adopted | they address different abuses (slot parking vs. plan parking) |
| Security 8 / amendment 6 token runs CV-only | deferred to founder Q5 | changes existing `/v1` behaviour outside this ADR |
| Draft actions W, F−, A2–A10, tuned variant | deferred (§1c) | review reasons per row |
| The whole engine (option A) | gated on Q1; recommendation B | reviewers showed v1's engine value is the operating point plus an honest look plus ≤ 2 extra families |
