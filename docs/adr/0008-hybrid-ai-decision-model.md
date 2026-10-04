# ADR 0008 — The hybrid AI decision model

**Status:** Accepted (founder, 2026-10-04; decisions recorded in § Founder decisions)  
**Date:** 2026-10-04  
**Prompt:** P6.1-A (design only; P6.2-A … P6.11-A implement)  
**Depends on:** [0005-upload-policy.md](0005-upload-policy.md) (`llm_exposure_policy`),
[0006-ml-state-graph.md](0006-ml-state-graph.md) §4/§5 (change sets, decision records), live Alembic
head `0070_investigation_findings` (verified 2026-10-04)  
**Paired with:** [0009-ai-governance-gateway-harness-assistant.md](0009-ai-governance-gateway-harness-assistant.md)
(where the policy is stored and enforced)  
**Consumed by:** P6.2-A, P6.3-B, P6.4-A, P6.5-A, P6.7-A, P6.8-A, P6.9-A, P6.11-A, P5.3-A (ADR 0007), P7.8-A

### Revision 1 (review fixes, 2026-10-04)

Changes after the security and ML-correctness reviews of the first draft
(status unchanged: Proposed).

- **Outcome scope (new §2b).** Every envelope field and tool result carries
  `outcome_scope ∈ {none, cv, holdout}`; `holdout` never reaches any agent or
  Jev call regardless of principal (the lead agent runs under a human session,
  so the service-token stripping of `api/v1_agent_views.py` did not cover it).
  Registry entries declare their allowed scope (`none` or `cv`). (Revision 1
  kept a champion `holdout_report_only` exception; Revision 2 deletes it.)
- **Caps tightened (§1, §1b).** `column.is_identifier` L2 → **L1**;
  `improve.next_action` L2 → **L1** until ADR 0007 seals holdout scoring for
  iterations; every `lead.*` write tool **L1** with no `job` effect (ASSISTANT.md
  rule 6); `ops.rollback` AI cap **L1** — automatic rollback is a deterministic
  rule action (`ops.rollback_threshold.v1`), not an AI level; `ops.retrain` is a
  new root (branches cannot change the dataset). Caps are now per (point, answer
  value / change kind) (§1b), so an L2 point still has L1 answers (any exclusion,
  re-inclusion, metric or threshold change).
- **Reverts map to existing change kinds (§1b).** `role_override`,
  `missing_value_override`, `domain_fill` do not exist in
  `domain/experiment_changes.py`; reverts are `feature_transform_add/remove`
  with `keep` / `impute_median` / `impute_most_frequent` / `drop_column` /
  `datetime_extract`, or `family_include/exclude`, as the branch service
  materializes them (`experiment_branch_service.py` `_ROLE_TREATMENT`). A
  closure test asserts every `revert.change` validates as an `ExperimentChange`.
- **Split and objective after results (§2).** Pending `split.strategy`
  proposals are superseded once a SplitPlan exists for (source dataset, target,
  task); `spec.objective` once the first experiment completes; disagreement at
  run start stops at `needs_input` before `run_holdout_lock`; the validator may
  only tighten the split structure.
- **Reproducibility (§2c).** Policy and levels are snapshotted at job claim and
  digested into run evidence and the candidate fingerprint; new AI decisions
  happen only on root runs; branches, reverts and re-runs inherit the parent's
  `used` values as `ai_inherited` overrides; precedence branch/human override >
  AI > rule, applied before the identifier guard; entity/group inference stays
  rule-only; Lab-time evidence is metadata only.
- **Evaluation statistics rewritten (§4).** Promotion evidence comes only from
  sealed benchmark partitions and blind labels; user accept/reject/revert is
  `user_decision_exposed` and may only monitor or demote; accuracy is defined
  per point; paired non-inferiority with cluster bootstrap by dataset, Wilson
  lower bounds, Holm correction per R3 run; ECE replaced by in-band precision
  lower bounds; evidence keyed by (prompt release, dated model id); label-free
  drift monitors; rule actors may only demote or propose.
- **Actors (§7).** A Jev value applied at L2 has no agent run, so the record's
  actor is `rule` (`decision_point.<key>.v1`) with the Jev evidence in
  `details.ai`, satisfying `ck_pdr_actor`; acceptance rows carry only human
  text in `rationale`, the agent's text in `details.proposed_rationale`.
- **L0 ≠ off (§8).** "AI off" keeps today's `semantic_*` ledger rows with
  `llm_used = false`; a golden test compares AI-off with L0 under a deliberately
  wrong fake Jev.
- Mis-citations of ADR 0006 Q4 corrected (it concerns re-including a
  leakage-excluded column; `drop_column` is a legal branch change); garbled
  sentence in §3 fixed; founder questions Q2, Q3, Q7 updated.

### Revision 2 (re-review fixes, 2026-10-04)

- **`training.families_budget` (§1, §1b).** L2 allows only the family subset
  (dummy always included) and a time budget ≤ the rule default, with
  `outcome_scope = none` (metadata-driven); `cv` scope only for L1 advice.
  Fixed `hyperparameter_override` and `class_weighting` values are **L1** at
  this point (they skip the nested tuner, so their CV is optimistic); AI-
  supplied *search ranges* for the nested tuner are deferred to ADR 0007. The
  same restriction applies to any future L2 of `improve.next_action`. R3
  outcome for both = the selected winner's metric on the sealed benchmark
  holdout, dataset as unit.
- **No holdout, ever (§2b).** The champion `holdout_report_only` exception is
  deleted; the champion's final evaluation is shown by a deterministic
  template outside any model context (ADR 0009 §7.6).
- **Statistics (§4).** Blind labels are inverse-probability weighted per
  agree/disagree stratum; sealed and blind gates are computed separately and
  both must pass; clusters are bootstrapped within strata; ≥ 20 sealed
  datasets or a wild cluster bootstrap / t with G−1 df; Holm on bootstrap
  p-values; fixed-n decisions with alpha-spending for weekly interim looks;
  "two consecutive runs" is a stability check. Precision gates state
  reachable minimum n (Wilson LB ≥ 0.98 needs n ≥ 189 with 0 errors or
  n ≥ 280 with 1); the L2 gate for `column.semantic_role` is the precision of
  the deployed policy on in-band disagreements where the AI overrides the
  rule; L0 → L1 for yes/no points gets a precision bound; demotion fires on
  evidence of harm (upper bounds), not on thin evidence; repeat-stability is
  an uncached repeat with a flip-rate limit.
- **§4 final pass (ML re-review).** Demotion triggers are level-specific and
  equal to that level's promotion bar (L1: −5 pt / 0.90; L2: −1 pt / 0.98),
  point-estimate clauses dropped; the families/budget R3 outcome is the sealed
  benchmark holdout metric only; weighted (effective-sample-size) precision
  intervals across IPW strata; the `column.semantic_role` override gate
  explicitly uses the pre-registered weighted combination; repeat-stability
  uses a confidence bound on the flip rate.
- **`plan` input (§ Consequences).** Same workspace and project, status
  `accepted` or `applied` (L2 change kinds only), re-validated at job run,
  single use.
- Notes for implementers: `datetime_extract` add at L2 only for columns the
  rule already models; the identifier guard for human branches is kept and
  the validator rejects the validation-group / entity column and
  very-high-uniqueness columns (§2); per-column consistency between
  `column.semantic_role` and `column.missing_value_action` (§1b); P7.8 must
  amend ADR 0006 Q1 for rule-driven champion rollback (§1); the pipeline
  verifier checks `evidence_partition` (§2c); Q3 rewritten.

## Context

Phases 0–3 and Phase 4 Stage 1 produced a deterministic engine, a versioned state
graph with an append-only decision ledger, and an agent interface (`/v1`, SDK,
MCP) that external agents already drive. The founder decisions of 2026-10-02
and 2026-10-04 ([ROADMAP.md § Hybrid AI model](../mvp/ROADMAP.md)) add internal
AI — a lead agent that is also the Studio assistant, specialist NOOA agents,
and Jev typed judgments — with one rule: **AI proposes, deterministic code
validates and executes, and the holdout, splits, metrics and selection are
never touched by AI.**

What exists today and must be reused, not duplicated:

- The automatic training job is eleven typed stage functions
  (`services/auto_train/`: `load_profile → target_resolution →
  structural_cleaning → holdout_lock → train_only_decisions → split_plan →
  column_roles → preprocessing_setup → training → persistence → finalize`).
  Four of them already call an OpenAI "decision agent" through
  `engine/lab/llm_client.py` (purposes `semantic_target`,
  `semantic_missing_value`, `semantic_column_type`, `semantic_leakage`), each
  with a deterministic fallback and a `llm_invocations` row — written even when
  the LLM is not consulted (`lab_decision_ledger.py`, `llm_used = false`,
  asserted by `test_pipeline_observability.py`); the pipeline verifier has two
  more purposes (`pipeline_audit_routine/deep`). All six are behind flags that
  production refuses to enable (ADR 0005 item 7).
- `project_decision_records` (ADR 0006 §5) distinguishes `human`, `rule` and
  `agent` actors (`ck_pdr_actor`: agent ⇒ agent run or service token), reserves
  `actor_agent_run_id`, and marks agent rationales untrusted.
- Branch change sets (`domain/experiment_changes.py`: `hyperparameter_override`,
  `family_include/exclude`, `class_weighting`, `threshold_objective`,
  `metric_override`, `feature_transform_add/remove` over the allowlist
  `drop_column`, `keep`, `impute_median`, `impute_most_frequent`,
  `datetime_extract`) and the SplitPlan guarantee make most AI decisions
  revertible as a typed child experiment on the same holdout. Branches apply
  column treatments before the identifier guard (`auto_train/branch.py`
  `apply_role_overrides`).
- Final-holdout values are withheld from **service-token** principals at the
  route layer (`api/v1_agent_views.py`: `strip_holdout`, `withhold_holdout_code`,
  champion `holdout_report_only`) and again in the MCP server (`server.py`:
  `cv_only`, `cv_record`); a human session sees everything. The lead agent runs
  under a human session and calls services in-process, so neither guard applies
  to it today.
- The MCP server exposes 14 holdout-blind tools with bounded, untrusted-marked
  output (`packages/dclab_mcp/shaping.py`), and a token scope model
  (`read`, `projects:write`, `datasets:write`, `experiments:write`,
  `decisions:propose`) in which no token may ever accept a decision.

`AGENTS_NOOA_JEV.md` §1b sketched trust levels and patterns; this ADR makes
them binding and concrete enough for P6.7-A/P6.9-A to code against. ADR 0009
decides the storage, gateway, harness and assistant transport.

External facts re-verified on 2026-10-04 (details and URLs in ADR 0009 §9):
NOOA PyPI 0.0.10 (2026-09-04; `CodeActStrategy` is the default and runs
in-process by default, `CodeActConfig.max_iterations = None`); LiteLLM stable
1.104.0 (2026-10-03); TypeSafe `jev-1.13.0` (aliases `jev-latest` and
`jev-preview` both point at it), `typesafe-sdk` 0.7.2 (2026-09-26).

## Decision

### Summary (ten lines)

1. A **code-owned decision-point registry** names every place where AI may
   contribute; nothing outside the registry calls a model about a decision.
2. Each point has one **pattern**: AI-before, AI-after or cross-check; agent-
   backed AI-before points run *before* the training job, Jev points run inline
   under a 1 s budget, AI-after points run as separate jobs. The worker never
   waits on an LLM.
3. **Final-holdout outcomes never enter an AI call**, whoever the principal is;
   every field is tagged with an outcome scope and the registry allows `none`
   or `cv` per point.
4. **Trust levels L0–L3** are per decision point per workspace, capped in code
   per (point, answer value), and start at L0 everywhere. L3 does not exist
   inside auto-train; `lead.*` writes and all exclusions are capped at L1.
5. **Promotion needs sealed-benchmark and blind-label evidence** (§4) and a
   named approver; **demotion is automatic** on incidents. Both are recorded.
6. **Jev is deterministic**: pinned release, code-owned thresholds, agreement
   table, cache by tenant-scoped input digest, never numeric questions,
   metadata by default.
7. **One decision record per point per run** holds both answers, the value
   used, the level and a revert that is an existing change kind (§7).
8. The **lead agent loop is hosted by DCLab's harness**, not by NOOA; every
   step is one typed gateway call. Its write tools are proposals at L1.
9. **Cleaning actions belong to the deterministic cleaning recipe**; AI only
   proposes typed overrides that become branch changes.
10. **Never AI**: training, metrics, selection, holdout scoring, splits,
    entity/group inference, loop control, exclusion above L1, release above L1.

### 1. Decision-point registry

Owner: `app/agents/governance/decision_points.py` (code; the table below is
its content at P6.9-A). A `DecisionPoint` entry is frozen data:

| Field | Meaning |
| --- | --- |
| `key` | `<area>.<decision>`; stable identifier used by policies, records, events, R3 |
| `stage` | auto-train stage function, or the job/phase that hosts the point |
| `pattern` | `ai_before` / `ai_after` / `cross_check` (§2) |
| `ai_kind` | `jev:<purpose>` or `agent:<agent_key>` (or `none`) |
| `rule_id` | code-owned deterministic rule, versioned (`schema_inference.identifier.v1`) |
| `answer_type` | Pydantic type of both answers (allowlisted enums, column references, typed change sets) |
| `effects` | what applying the AI value does: `none`, `flag`, `branch_change:<kind>`, `proposal:<type>`, `ref_move` |
| `outcome_scope` | highest outcome scope the AI may see for this point: `none` or `cv` (§2b); `holdout` is not a legal value |
| `evidence_partition` | what the evidence is computed on: `metadata` (Lab time, no SplitPlan) or `train` (locked training partition) |
| `cap` | highest level code ever allows for the point; §1b caps individual answers lower |
| `default_level` | level a new workspace gets when the platform row is absent — L0 for every point |
| `fallback` | always the rule value (or "no action" when there is no rule) |
| `budget` | p95 latency and cost per 1 000 decisions the point must stay within (R3 gate) |
| `phase` | prompt that wires it |

Initial registry (caps are decisions of this ADR; defaults are all **L0**):

| Key | Stage (hook) | Pattern | AI | Rule | Answer | Effect of applying AI | Scope | Cap | Why the cap | Phase |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `target.column` | `run_target_resolution` (before `needs_input`) | AI-before | `agent:dataset_investigator` (ranks candidates); `jev:column.semantic_role` feeds it | target inference (`lab_decision_ledger.record_target_selection`, replaces `semantic_target`) | ordered candidate list + task type | proposal `problem_spec`; the human confirms (today's `needs_input`) | none | **L1** | the target defines the lineage; a wrong target is a new root, not a revert | P6.9 |
| `spec.objective` | `propose_problem_spec` / Lab | AI-before | `agent:experiment_planner` | `metric_planner` default metric | primary metric + constraints from the allowlist | proposal `problem_spec` (locked spec version, `ref_moved` on accept) | none | **L1** | changes what "better" means for every later comparison; superseded once results exist (§2) | P6.4 |
| `column.is_identifier` | `run_column_roles` (`infer_column_roles`) | cross-check | `jev:column.is_identifier` | `schema_inference` identifier rule | yes/no per column | `identifier` → column not modeled = an exclusion | none | **L1** | every exclusion is a human decision (§1b); the rule alone may still exclude as today | P6.7/P6.9 |
| `column.semantic_role` | `run_column_roles` (`split_column_roles`, replaces `semantic_column_type`) | cross-check | `jev:column.semantic_role` | `split_column_roles` + column-type rule | numeric / categorical code / identifier / datetime / free text / other | role used by the fold-local Pipeline; revert = `feature_transform_add` (`impute_median` ↔ `impute_most_frequent` / `keep`) | none | **L2** for numeric ↔ categorical among columns the rule already models; L1 for identifier / free text / other (exclusions) | reversible by a typed branch on the same holdout; exclusions are not | P6.7/P6.9 |
| `column.missing_value_action` | `run_train_only_decisions` (missing-value plan, replaces `semantic_missing_value`) | AI-before | `agent:dataset_investigator` (batched per dataset) | missing-value rule table | one action per column: `impute_median`, `impute_most_frequent`, `drop_column`, `domain_fill(value from a code-owned table)` | overrides in the cleaning recipe; revert = `feature_transform_add` of the rule's action | none | **L2** for `impute_median` ↔ `impute_most_frequent`; L1 for `drop_column` and `domain_fill` | drop is an exclusion; a fill value is domain knowledge a person confirms | P6.9 |
| `feature.leakage_suspect` | `run_train_only_decisions` (leakage auditor, train partition) | cross-check | `jev:feature.leakage_suspect` + `agent:dataset_investigator` (explanation) | `leakage_auditor` name/availability rules | per column: `exclude` / `review_flag` / `clear` | AI may add a **review flag** only; exclusion is always a human decision | none | **L1** | exclusion narrows the feature space of the whole lineage and "name alone never excludes" (ARCHITECTURE §5); ADR 0006 Q4 then forbids re-inclusion without a new root | P6.7/P6.9 |
| `split.strategy` | `run_holdout_lock` (holdout planner input, before the lock) | AI-before | `agent:experiment_planner` | `holdout_planner` (stratified / random / group_disjoint / temporal_future; group+temporal fails closed) | strategy + time/group column + holdout fraction band | planner input override; the planner's own rules still validate (§2 tighten-only); revert = new root (the plan is immutable) | none | **L1** | the SplitPlan is the comparison basis of a lineage; not revertible in place | P6.9 |
| `training.families_budget` | `run_preprocessing_setup` → `SearchConfig` | AI-before | `agent:experiment_planner` | portfolio rule (all allowlisted families + dummy) | subset of the family allowlist (dummy always), time budget ≤ the rule default; optionally fixed `hyperparameter_override` / `class_weighting` values as **advice** | `SearchConfig.families` / `max_training_seconds` (fields in the fingerprint); revert = `family_include/exclude` | **none** at L2 (metadata-driven: profile bands, task, row/column counts); `cv` only for the L1 advice form | **L2** for the family subset and time budget only; **L1** for fixed hyperparameters and class weights (they bypass the nested tuner, so their CV is optimistic and would win selection unfairly); AI-supplied *search ranges* for the nested tuner are an ADR 0007 extension | dummy baseline and CV selection untouched; at `none` scope the planner cannot adapt to earlier CV results on reused folds | P6.9 |
| `experiment.review` | job `agents.run` after `run_finalize` | AI-after | `agent:experiment_critic` | `pipeline_verifier` + P4.10 findings (replaces `pipeline_audit_*` LLM modes) | verdict `promote_candidate` / `needs_work` / `reject` + cited findings and **CV** metrics | none (advice shown on the experiment and in the Inbox) | cv | **L1** | a verdict never promotes; `champion_promoted` stays human (ADR 0006 Q1); the Critic never sees holdout values | P6.4 |
| `improve.next_action` | `labs.improve` iteration (P5.4) | cross-check | `agent:improvement_hypothesis` | rule proposer table | one typed `ExperimentChange` | next child experiment proposed; the loop applies it only at ≥ L2 | cv | **L1** until ADR 0007 fixes when the holdout is scored for a loop; a future L2 is limited to the `training.families_budget` L2 kinds (family subset, time budget, and tuner search ranges if ADR 0007 adds them) — never fixed hyperparameter or class-weight values | iterations must not tune on a holdout that is scored more than once; the per-SplitPlan count of holdout looks is shown in the loop UI | P6.5 |
| `ops.diagnose` | P7.4 drift window job | AI-after | `agent:dataset_investigator` | drift finding (deterministic) | diagnosis with cited finding ids | notification text | cv | **L3** | no state change; notify-only | P7.8 |
| `ops.retrain` | P7.8 Ops agent | AI-before | `agent:ops` | none (workspace autonomy policy: max per week, trigger thresholds) | **root** experiment request (new dataset version, same spec) | job `experiments.run` (a branch cannot change the dataset — ADR 0006 §4); revert = cancel / ignore the experiment | none | **L2** | creates evidence, never a release | P7.8 |
| `ops.release` | P7.8 | AI-before | `agent:ops` | release gate (P7.2 deterministic checks; comparison on rows outside both models' training sets) | release proposal for a ModelVersion | proposal `model_release` | cv | **L1** | release changes what customers get; founder rule "auto-release off" | P7.8 |
| `ops.rollback` | P7.5/P7.8 | AI-before (explanation + proposal) | `agent:ops` | **`ops.rollback_threshold.v1`**: a deterministic rule action under the autonomy policy that rolls back on its own and notifies; AI cannot veto it | rollback yes/no + explanation | proposal `ref_move` of `champion_model` (AI); the rule acts directly | cv | **L1** for the AI | the trigger is a rule; the AI adds the explanation or asks earlier. **P7.8 must amend ADR 0006** (founder decision 1: "later runs never auto-promote"; a rule-driven champion rollback is a rule-actor `ref_moved`) before this rule action exists | P7.8 |
| `command.intent_route` | assistant turn (P6.3-B) | AI-before | `jev:command.intent_route` | none (UX router; the lead agent's own step is the fallback) | one of the tool/quick-action keys or `other` | pre-selects the quick action; the user still confirms any write | none | **L1** | pure UX; no state change | P6.7/P6.3-B |
| `proposal.completeness` | after any proposal is validated | AI-after | `jev:proposal.completeness` | validator verdict | score 0–4 | display only | none | **L0** | a hint for reviewers, never a gate | P6.7 |
| `lead.propose_problem_spec`, `lead.record_decision`, `lead.move_ref`, `lead.run_experiment`, `lead.branch_experiment`, `lead.predict` (later `lead.improve`, `lead.batch_predict`, `lead.propose_release`) | assistant write tools | AI-before | `agent:lead` | none | the tool's typed arguments | proposal (confirm card); **no direct execution** | cv | **L1** (all) | ASSISTANT.md rule 6: the worst a prompt injection can do is a suggestion a human confirms; raising any `lead.*` to L2 needs an amendment of that rule | P6.3-B / A5 / A7 |

Registry invariants (property-tested in P6.10-B over point × answer value ×
change kind):

- Any entry whose effect excludes a column, locks a spec, creates a split plan,
  moves a ref, releases a model or executes a write tool has `cap ≤ L1`.
- No entry's rule, evidence or effect reads holdout rows or holdout outcomes,
  computes a metric, selects a winner, builds a split or infers the entity /
  group column (`infer_entity_column` stays rule-only); the forbidden-operation
  list of ADR 0009 §6 is checked against every `effects` value.
- `cap ≤ L2` for every entry whose `stage` is an auto-train stage (**L3 does
  not exist in auto-train**).
- `default_level = L0` for all; the first non-zero levels come from P6.8-A.
- Every `revert.change` validates as an `ExperimentChange` (closure test).
- Legacy purposes map onto registry keys (`semantic_target → target.column`,
  `semantic_column_type → column.semantic_role`, `semantic_missing_value →
  column.missing_value_action`, `semantic_leakage → feature.leakage_suspect`,
  `pipeline_audit_* → experiment.review`); P6.2-B moves their writers onto the
  gateway, P6.9-A moves their call sites onto the `DecisionPoint` hook, and the
  old `decision_agent_*` / `pipeline_llm_verifier_*` settings are retired after
  P6.9-A (kill switches replace them).

#### 1b. Caps per answer value and change kind

A point's `cap` is the maximum; the answer decides the actual ceiling:

| Answer value / change kind | Ceiling | Revert (an existing `ExperimentChange`) |
| --- | --- | --- |
| numeric ↔ categorical role change for a column the rule already models | L2 | `feature_transform_add` `impute_median` (numeric) / `impute_most_frequent` (categorical) / `keep`, as `experiment_branch_service._ROLE_TREATMENT` materializes them |
| `impute_median` ↔ `impute_most_frequent` | L2 | `feature_transform_add` of the rule's action |
| `family_include` / `family_exclude` (never a `NON_EXCLUDABLE_FAMILIES` dummy), time budget ≤ rule default | L2 | the inverse change / the rule's budget |
| fixed `hyperparameter_override`, fixed `class_weighting` | **L1** | `hyperparameter_override` with the parent's values / `class_weighting none` — a fixed value bypasses the nested tuner; only a person may accept that trade |
| tuner search ranges (AI-proposed, inside code-owned bounds) | not in this ADR; ADR 0007 decides (candidate for L2 because the nested tuner still selects) | — |
| any exclusion: role `identifier` / `free_text` / `other`, `drop_column`, leakage `exclude` | **L1** | re-inclusion is a human branch (`feature_transform_add keep`) and, for leakage-excluded columns, a new root (ADR 0006 Q4) |
| re-including an excluded column | **L1** | — |
| `metric_override`, `threshold_objective`, spec changes | **L1** | human `ref_move` / branch |
| `domain_fill` (value from a code-owned table only) | **L1** | `feature_transform_add` of the rule's action |
| `datetime_extract` add for a column the rule already models; remove | L2 | the inverse change |
| `datetime_extract` add for a column the rule does not model | **L1** | it is a re-inclusion |

Two points set the same column treatment (`experiment_branch_service.py`
`_ROLE_TREATMENT`: `impute_median → numeric`, `impute_most_frequent →
categorical`): `resolve()` checks per-column consistency between
`column.semantic_role` and `column.missing_value_action` and falls back to the
rule for both when they conflict; the validator bounds one-hot expansion of a
numeric column re-typed as categorical (cardinality cap from the engine);
reverts always use `feature_transform_add` of the rule's transform, never
`feature_transform_remove` of an imputation (which drops the column,
`_column_treatments`).

### 2. Patterns and where they run

| Pattern | Definition | Timing | What the record compares |
| --- | --- | --- | --- |
| **AI-before** | AI proposes the *inputs* of a deterministic step; the step validates and runs them | agent-backed: before the job (Lab turn, `propose_problem_spec`, `run_experiment` with planner output attached to the execution request); Jev-backed: inline in the stage | AI input vs rule input; the step's output is deterministic given the input |
| **AI-after** | AI reviews the *outputs* of a deterministic step | a separate `agents.run` job after the step/run completes; never blocks the run | AI verdict vs deterministic verifier/findings |
| **Cross-check** | both answer independently; agreement applies (at ≥ L2), disagreement goes to L1 review, abstention uses the rule | inline for Jev (1 s budget); loop-level for the improve proposers | the two answers and the agreement outcome |

Hard timing rule for the worker: an auto-train stage waits only on **Jev
answers (≤ 1 s per batch, cache first)**. Agent answers for AI-before points
are produced before the job and travel as typed inputs (`ExperimentPlanProposal`
accepted or auto-applied → overrides on the execution request); if absent, the
stage runs rule-only. A run therefore never fails or slows because of an LLM.

**Decisions that cannot follow results.** A pending `split.strategy` proposal
is auto-superseded (status `superseded`, reason `plan_exists`) as soon as a
SplitPlan exists for (source dataset, target, task) — the plan is the
comparison basis and may not be re-chosen after a result is known. A pending
`spec.objective` proposal is superseded once the first experiment of the spec
completes. If, at run start, the AI value for `split.strategy` disagrees with
the rule and the point is L1, the run stops at `needs_input` **before**
`run_holdout_lock`; nothing is split until a person answers. The planner's
validator may only **tighten** structure: random/stratified → group-disjoint
or temporal-future when the rule found a group or time column; it never
loosens (a temporal rule answer cannot become random), and the holdout
fraction has a code-owned floor.

**`resolve()` precedence inside a stage:** explicit branch/human override >
AI value (at ≥ L2, validator-accepted) > rule value. The identifier guard of
`apply_role_overrides` is **kept as is** for human branches (AI identifier
judgments are L1-only, so no AI override ever needs to pass it); in addition
the role validator rejects any override of the validation group / entity
column and of very-high-uniqueness columns (unique ratio above the
`schema_inference` identifier threshold), for humans and AI alike.

The hook (P6.9-A) is one function per point inside the stage:

```
resolve(point, rule_answer, ai_answer | None, level, validator, overrides) -> Resolution
  Resolution: value_used, source (rule|ai|human|ai_inherited|human_pending), agreement,
              record (decision_point_resolved), event (decision_point_resolved),
              proposal (L1 only), semantic_answer rows (Jev only)
```

`rule_answer` is always computed first; `ai_answer` comes from the gateway or
the attached proposal; `validator` is the point's deterministic validator
(columns exist, allowlists, planner rules, §1b ceilings). Level semantics are §3.

#### 2b. Outcome scope: the holdout never enters an AI call

- Every `ContextEnvelope` field, tool result field and evidence record carries
  `outcome_scope ∈ {none, cv, holdout}` (orthogonal to the data class of
  ADR 0009 §8). `holdout` covers every final-holdout value: metrics, the
  `final_holdout` evaluation, `final_test_*` events, `HOLDOUT_METRICS` in
  exported code, holdout rows of the model card, holdout-scoped findings.
- **Agent consumer mode is independent of the principal.** Any service or
  read-model call that carries an `agent_run_id`, every catalog tool shaper and
  the context builder strip `holdout` **in the service / read-model layer**
  (ADR 0009 §6 moves `strip_holdout`, `cv_only`, `cv_record`,
  `withhold_holdout_code` and the model-card withholding into
  `app/agents/tools/shaping.py`; the route-layer and MCP guards keep calling
  the same code). A human session in the Lab therefore gets the same
  holdout-blind results as a service token when an agent is the consumer.
- **There is no exception.** The champion's final evaluation (today's
  `holdout_report_only` for service tokens) is shown to the user by a
  deterministic template outside any model context (ADR 0009 §7.6 *Champion
  final evaluation*); the lead agent may point to it, never read it.
- The registry declares the allowed scope per point (§1): `none` for
  `target.column`, `spec.objective`, `split.strategy`, `column.*`,
  `feature.leakage_suspect`, `training.families_budget` at L2, `ops.retrain`,
  `command.*`, `proposal.*`; `cv` for `training.families_budget` L1 advice,
  `improve.next_action`, `experiment.review`, `ops.diagnose`, `ops.release`,
  `ops.rollback`, `lead.*`.
- The assistant's number-in-text validator (ADR 0009 §7.1) matches **CV**
  metrics only; a holdout number in an answer is a rejected step.
- P6.10-B property test — **no holdout, ever**: for every principal and every
  agent consumer, no tool output, no envelope field and no transcript item
  contains a key or scope matching `holdout|final_test`.

#### 2c. Reproducibility of AI decisions

- The effective policy and levels are **snapshotted at job claim**
  (`policy_digest` on the run) and digested into the run evidence and the
  candidate fingerprint (`engine/search/fingerprint.py`); family choice on root
  runs is a `SearchConfig.families` field that is part of the fingerprint.
- New AI decisions are made **only on root runs**. Branches, reverts and
  re-runs inherit the parent's `decision_point_resolved.used` values as
  `ai_inherited` branch overrides (recorded in the child's record with
  `source: ai_inherited`); a revert flips the override to the recorded rule
  answer. Test: parent at L2, demote the point, branch with an unrelated change
  → identical modeled columns.
- Evidence partition: at Lab time (no SplitPlan) only metadata is available to
  any AI; inside a run, aggregates come from the locked training partition only
  (Jev bands from `engineered_train`, never from `inp.profile`, which covers
  the whole file). The record stores `evidence_partition`, and the pipeline
  verifier (`services/pipeline_verifier.py`) gains a check that every
  `decision_point_resolved` record of a run says `train` (or `metadata`),
  failing the run's verification otherwise.
- ADR 0009 §5.5 replay covers **agent runs**; auto-train decision points are
  reproduced by the cache (Jev) and the inherited overrides, not by replay.

### 3. Trust levels

| Level | Name | What happens at the point | Who sees what | Who may set it |
| --- | --- | --- | --- | --- |
| **L0** | Shadow | rule value used; AI answer logged beside it (`semantic_decision_answers` / `agent_proposals` with status `shadow`) | development role only (P6.7-UI); no user-facing effect | default; an approver may set it from any level (down) |
| **L1** | Advise | rule value used **now**; AI answer becomes a `proposed` proposal with evidence; a human accepts or rejects; acceptance applies through the normal command path (branch / spec / ref move) | user sees AI answer beside the rule answer with a `Level` badge | approver, with §4 evidence |
| **L2** | Auto + revert | AI value used when the validator accepts it (and §1b allows L2 for that answer), else rule; proposal status `applied`; a `revert` action restores the rule value through a superseding record and a branch change; Inbox shows it | user sees "applied, revertible" | approver, with §4 evidence; never for `cap ≤ L1` points |
| **L3** | Auto + notify | agent acts under the workspace autonomy policy without a pending item; the person is notified; still revertible where the effect allows | notification + record | approver; only `ops.diagnose` in this ADR; never inside auto-train |

Rules:

- **Effective level** = `min(workspace row, platform row, code cap, §1b
  ceiling of the answer)`, where the platform row is written by P6.8-A from R3
  and the workspace row by the workspace's approvers (ADR 0009 §2
  `decision_point_policies`). A workspace may lower, never exceed, the
  platform level. Evidence and levels are keyed by (prompt release id, dated
  model id); a pair without evidence is L0 (§4).
- **`off` is a switch, not a level** (ADR 0009 §4): at `off` the point behaves
  as "AI off" (§8) and no AI answer is produced.
- Every level change, up or down, manual or automatic, is a new accepted row
  in `decision_point_policies` carrying actor, rationale and evidence (R3 run
  ids, incident ids). **Rule actors may only demote or propose**; a promotion
  row needs a named human decider — a workspace approver for a workspace row,
  a named platform admin for a platform row.
- **Who may change:** the role-derived authority `can_approve_ai_policy`
  (ADR 0009 §3: an unsuspended `workspace_owner` / `workspace_admin`
  membership; platform staff cannot approve customer policies). L1
  acceptances need the authority a human needs today for the underlying
  command (`can_execute_workspace_ml`); service tokens never accept.

### 4. Promotion and demotion rules (numbers)

R3 (P6.8-A) computes, per decision point and per Jev purpose, keyed by
(prompt release id, dated provider model id):

**Evidence sources.** Promotion uses only (a) **sealed benchmark partitions**:
R1 datasets split into a development part (used for prompt and threshold work)
and a sealed part never used for either, rotated per prompt release, plus an
obfuscated-column-name variant; and (b) **blind labels**: a random sample of
production decisions stratified by agree/disagree (over-sampling
disagreements), labeled by a reviewer who sees only metadata and neither
answer (`label_source = blind`); every blind case carries its sampling
probability and estimates use **inverse-probability weights per stratum**.
User accept/reject/revert is `user_decision_exposed` — the user saw both
answers — and may only **monitor or demote**, never promote. **Gates are
computed separately for the sealed source and the blind source and both must
pass** (a pre-registered weighted combination is allowed only when a source
has fewer than its minimum cases, and is then stated in the R3 report). R3
runs bypass the answer cache (or count only questions with ≥ N new digests),
fit thresholds and measure precision on disjoint splits, deduplicate by
question digest, keep labels as append-only versions with precedence blind >
benchmark > exposed, keep workspace evidence inside its workspace (platform
R3 = benchmarks + explicit opt-in), and need ML-write authority, a rate limit
and labeler ≠ proposal author for `ground_truth` writes.

**Outcome per point.** Yes/no points (`column.is_identifier`,
`feature.leakage_suspect`): per-band precision and recall against blind
labels plus McNemar on the disagreement cases. Choice points
(`column.semantic_role`, `column.missing_value_action`, `target.column`):
exact-match accuracy, and for the L2 gate of `column.semantic_role` the
**precision of the deployed policy**: among in-band disagreements where the AI
would override the rule with a §1b-eligible answer, the share where the AI is
right. `training.families_budget`, `improve.next_action`: paired
non-inferiority of the **selected winner's metric on the sealed benchmark
holdout** between the AI input and the rule input, dataset as unit — never
the CV metric of the resulting experiment (which the planner could have
adapted to) and not an outer-fold score (which equals the selection CV unless
an outer loop wraps the whole selection, which the engine does not do). `experiment.review`: agreement with
verifier findings and citation validity. `split.strategy`: agreement with the
planner on benchmark datasets whose correct structure is known (group / time
columns planted).

**Statistics.** Non-inferiority of (AI − rule) with margin m is a paired
one-sided test at α = 0.05 whose p-value comes from a cluster bootstrap by
dataset (clusters resampled **within strata** for blind data); it needs ≥ 20
sealed datasets, or with fewer a small-sample correction (t with G − 1 df or a
wild cluster bootstrap) — plain cluster bootstrap under-covers at G = 5. The
quoted "lower bound ≥ −m" is the equivalent 95 % one-sided bound. Precision
gates are Wilson 95 % lower bounds (LB): **LB ≥ 0.98 needs n ≥ 189 in-band
cases with 0 errors or n ≥ 280 with 1 error**; LB ≥ 0.90 needs n ≥ 100 with
≤ 4 errors. Calibration is checked as *in-band precision LB ≥ band edge*
(0.90 for Noul, 0.80 for Choice); ECE is reported with equal-mass bins and an
upper confidence bound only when n ≥ 1 000, never as a gate. All gate
p-values across points in one R3 run are Holm-corrected. **Windows:** each
gate is a fixed-n decision at the pre-registered n; weekly interim looks use
alpha-spending (O'Brien–Fleming boundary) so that looking early does not
inflate α; "two consecutive R3 runs" is a stability check (same direction
twice), not extra evidence. The schedule is pre-registered in `releases.py`
(weekly plus one run per prompt release or model id change).

| Transition | Minimum cases | Quality | Safety | Operations | Process |
| --- | --- | --- | --- | --- | --- |
| L0 → L1 | ≥ 100 sealed and ≥ 30 blind in-band cases; stability check over 2 runs | non-inferiority with m = 5 pts on each source; yes/no points: in-band precision **weighted** LB ≥ 0.90 on each source — the in-band cases span the agree/disagree strata with different IPW weights, so the interval is a Wilson bound on the Kish effective sample size (or a weighted-logit interval), never a raw-count bound | validator rejection ≤ 5 % (≥ 50 proposals); citation validity ≥ 99 %; zero data-exposure findings | p95 latency and cost within the point's budget | approver accepts the R3 proposal (record) |
| L1 → L2 (`column.semantic_role`, `column.missing_value_action`, `training.families_budget` only) | sealed: ≥ 20 datasets and ≥ 300 cases; blind: ≥ 100 cases; stability over 2 consecutive runs. For the deployed-policy precision of `column.semantic_role` the override cases are few (~20 sealed datasets yield far fewer than 189), so **this one gate explicitly uses the pre-registered IPW-weighted combination of sealed and blind override cases** (stated in the R3 report; "both sources must pass" is not waived for the other gates) and needs an effective n of ≥ 189 with 0 errors or ≥ 280 with ≤ 1 | non-inferiority with m = 1 pt on each source (effectively superiority at this n, intended); `column.semantic_role`: deployed-policy precision weighted LB ≥ 0.98; exposed acceptance ≥ 80 % over 30 days with ≥ 50 proposals (monitoring signal, not evidence) | validator rejection ≤ 2 % (≥ 100 proposals); no open incident on the point | same | approver; founder Q2 decides whether L2 is enabled at all in the MVP |
| L2 → L3 (`ops.diagnose` only) | ≥ 4 weeks at L2, ≥ 100 applied decisions | revert rate ≤ 2 % | no incident in 30 days | same | approver + autonomy policy names the point |

Demotion fires on **evidence of harm**, never on thin evidence (automatic
unless stated; each writes an `ai_incidents` row and a
`decision_point_policies` row with `actor_rule = governance.auto_demote.v1`):

| Trigger | Action |
| --- | --- |
| ≥ 5 `rejected_by_validator` proposals for one point in 24 h, or ≥ 2 % over 7 days (≥ 50 proposals) | demote one level |
| R3 run, point at **L1**: 95 % **upper** bound of (AI − rule) < −5 pts, or weighted precision upper bound < 0.90 (yes/no points), on either source | L1 → L0 |
| R3 run, point at **L2**: 95 % **upper** bound of (AI − rule) < −1 pt, or Wilson upper bound of the deployed-policy precision < 0.98 | L2 → L1 |
| Revert rate of L2 decisions > 10 % over 30 days (≥ 30 applied), or ≥ 3 reverts in 7 days | L2 → L1 |
| Label-free drift: AI–rule disagreement rate or abstain rate departs from the R3 baseline (two-proportion test, ≥ 200 decisions, or CUSUM alarm) | demote one level; blind audit of the period |
| Jev repeat-stability check fails: an **uncached** repeat of ≥ 200 recent in-band questions with the same model id has a Wilson 95 % **lower** bound of the in-band flip rate above 1 % (evidence of instability, not a raw count) | L0 for the purpose until R3 re-runs |
| Any data-exposure property-test failure or redaction incident involving the point | L0 + agent/purpose switch off until an approver resolves |
| Provider breaker open > 1 h | no level change; fallback keeps running; incident `provider_failure` |
| Approver decision | any time, any direction down |

Demotion bars equal the promotion bars of the same level (−5 / 0.90 at L1,
−1 / 0.98 at L2), so a result that would not have blocked promotion can never
demote, and a point cannot stay at a level whose own bar it demonstrably
fails (−2 pt at L2 would allow exactly that). No point-estimate clause: at a
true gap of 0 with 10 % disagreement a "point < −2 with n ≥ 200" rule fires in
about 19 % of runs on noise alone.

Continuous blind audit: ~5 % of L2 decisions are sampled for blind labeling
every week. Re-promotion after an automatic demotion requires a new R3 run
after the fix (prompt release or validator change), not just time passing.

### 5. Jev: deterministic use

- **Pinned release:** `jev-1.13.0` via `typesafe-sdk==0.7.2` (ADR 0009 §9).
  Aliases (`jev-latest`, `jev-preview`) are never used. A bump is a prompt
  release change (new `release_version` in `releases.py`) and resets every
  purpose to L0 until R3 re-runs.
- **Purposes** (owned in `app/agents/semantic/releases.py`, each with its
  question text, allowed state fields, thresholds and max level):

| Purpose | Primitive | State sent (metadata only, from `engineered_train` inside a run) | Acting band | Pairs with | Cap |
| --- | --- | --- | --- | --- | --- |
| `column.is_identifier` | Noul (P(yes)) | column name (untrusted), dtype, uniqueness band, null band, name tokens | p ≥ 0.90 → yes; p ≤ 0.10 → no; else abstain | `column.is_identifier` | L1 |
| `column.semantic_role` | Choice (6 options) | name, dtype, cardinality band, value-pattern band (digits / mixed / date-like / long text), null band | confidence ≥ 0.80 | `column.semantic_role` | L2 (§1b) |
| `feature.leakage_suspect` | Noul | column name, target name, task, availability text from the spec (untrusted), dtype | p ≥ 0.90 → flag; else no flag | `feature.leakage_suspect` | L1 |
| `command.intent_route` | Choice over tool/quick-action keys + `other` | the user's message (untrusted, ≤ 2 000 chars) — only when the workspace policy flag `data.user_text_to_jev` is on | confidence ≥ 0.80 | `command.intent_route` | L1 |
| `proposal.completeness` | Score (0–4) | proposal summary fields (typed) | display only | `proposal.completeness` | L0 |

- **Never numeric:** counts, ratios and dates are turned into categorical bands
  (`unique_ratio: ">0.99"`, `null_rate: "0–1%"`) before they enter `state`; no
  question asks Jev to compute, compare numbers or read metrics.
- **Agreement table** (applied by `semantic/policy.py`; identical for every
  purpose):

| Jev vs rule | In acting band | L0 | L1 | L2 |
| --- | --- | --- | --- | --- |
| agree | yes | rule value; logged `agree` | rule value; logged | same value applied; `applied` |
| disagree | yes | rule value; logged `disagree` | rule value + `proposed` review item | Jev value if the validator and §1b accept it, else rule; `applied`, flagged in Inbox |
| any | no (abstain) | rule value; `abstain` | rule value | rule value |
| timeout / breaker / budget / switch / policy unavailable | — | rule value; `unavailable` | rule value | rule value |

- **Cache key** = `sha256(workspace_id ‖ purpose ‖ release_version ‖ model_id ‖
  data_class ‖ canonical_json(state) ‖ question_key)`; lookups filter by
  workspace (no cross-workspace cache); the cache is the first non-cached
  `semantic_decision_answers` row per digest, retained for replay. The same
  input gives the same answer.
- **Budget:** 1 s timeout per batched call (≤ 50 questions, state ≤ 8 KB),
  circuit breaker opens after 5 provider 5xx/timeouts in 60 s for 15 min, cost
  cap per run from the workspace budget. Any failure is `unavailable` → rule.
- **Data class:** metadata only. Sample values (≤ 5 distinct values per
  column, never for AI- or rule-judged identifiers, free-text or
  `sensitivity = restricted` columns) only when the workspace policy allows
  `sample_values` **and** every involved dataset/column label allows it
  (ADR 0009 §8). Raw rows never. User text only with `data.user_text_to_jev`.
- **Prohibited forever:** task type from numbers, metric reading, model
  selection or promotion, automatic column exclusion, loop control.

### 6. Lead agent and specialists

**Host.** The lead agent (= the in-app assistant, `prompts/ASSISTANT.md`) is a
**DCLab-owned bounded loop in the harness** (`runtime/lead_runtime.py`), not a
NOOA agent: NOOA's multi-step strategy is CodeAct (generated Python, in-process
by default, no iteration bound), which is banned in API/worker processes, and
its `PredictStrategy` is a single call by design. Each loop step is one gateway
`complete()` returning a validated `AssistantStep` (ADR 0009 §7); tool calls
run in-process through the shared catalog in agent consumer mode (§2b).
Specialists (`experiment_critic`, `dataset_investigator`, `experiment_planner`,
`improvement_hypothesis`) are NOOA `PredictStrategy` classes with a
gateway-backed LLM adapter (P6.3-A); the runtime asserts at start that the
model client is the gateway adapter and the strategy is `PredictStrategy`, and
a CI test fails if any registered method resolves to another strategy.

**Bounds** (defaults in the platform policy; workspace may lower):

| Scope | Steps | Tokens (in+out) | Wall time | Cost | Tool calls |
| --- | --- | --- | --- | --- | --- |
| one turn | ≤ 8 | ≤ 60 k | ≤ 120 s | ≤ $0.25 | ≤ 6 per step, ≤ 20 per turn |
| one thread (session) | ≤ 40 | ≤ 300 k | ≤ 15 min active | ≤ $1.00 | — |
| specialist run | 1 call (+1 retry on invalid output) | ≤ 24 k | ≤ 60 s | ≤ $0.10 | 0 |

Exhaustion ends the turn with a typed `budget_exhausted` message and the
"Use forms instead" path; nothing half-applied remains because write tools are
proposals.

**Tools.** Read tools execute; **every write tool creates an `agent_proposals`
row at L1** (a confirm card) — there is no `job` effect in the MVP. Tool list,
effects and capabilities: ADR 0009 §6. There is no tool to read rows, pick a
winner, compute a metric, build a split or run code.

**State.** A thread is one `agent_runs` row of kind `assistant`; every user
message, step, tool call/result digest, assistant message and proposal is an
`agent_events` row. The prompt for a step is rebuilt from (a) the
`ContextEnvelope` re-resolved from the graph under the signed-in user's
workspace (refs, stale flags, open proposals, levels, last experiments with
CV metrics, findings, model-card summary without the final evaluation) and
(b) the bounded transcript of the thread's events — the graph is the memory;
chat history alone is never the source of truth.

**Model.** Provider and model are policy data per agent role (ADR 0009 §3).
Founder decision Q1: OpenAI for every LLM role — **`gpt-6.1-sol` for complex
work** (the lead agent / assistant, `experiment_planner`, `experiment_critic`,
`improvement_hypothesis`, the Phase 7 ops agent) and **`gpt-6-luna` for simple,
high-volume work** (`dataset_investigator`'s batched per-column judgments, the
migrated legacy decision purposes, the routine pipeline-audit verifier). A
workspace may move a complex role down to `gpt-6-luna` to save cost, never to a
model outside the platform allowlist. No model id is hard-coded outside the
platform default policy file.

**Ops agent** (Phase 7) is a second instance of the same bounded loop with the
`ops.*` points and the workspace autonomy policy; it is not a new runtime.

### 7. The decision record of an AI decision

New `decision_type` values (allowlist in `domain/decision_records.py`,
`schema_version = 1`), all on `project_decision_records`:

| Type | Written when | State / actor | Subject |
| --- | --- | --- | --- |
| `decision_point_resolved` | once per decision point per root run (or per Lab proposal batch) where AI participated (L0–L3), and once per inherited point on a branch | `accepted`; actor `rule` (`actor_rule = decision_point.<key>.v1`) when the rule value is used **or when a Jev value is applied at L2** (the deterministic policy applied it; no agent run exists, so `ck_pdr_actor` forbids `agent`); actor `agent` (`actor_agent_run_id`) only when an agent run's value is applied; an L1 point writes it with the rule value and links the pending proposal | the stage's node: `experiment` (auto-train points), `problem_spec` (`spec.*`, `target.column`), `split_plan` (`split.strategy`), `dataset_version` (Lab-time column points) |
| `proposal_accepted` / `proposal_rejected` (reserved by ADR 0006) | a human decides an L1 proposal (P6.6-A) | `accepted` / `rejected`, actor `human`, `supersedes_id` → the `decision_point_resolved` row; `rationale` is the human's text or the fixed string `accepted assistant proposal <id>`; the agent's text is `details.proposed_rationale` and is always rendered untrusted | the proposal's subject |
| `proposal_reverted` | a human reverts an L2/L3 applied value | `accepted`, actor `human`, supersedes the applied row; `details.revert` names the branch executed | same |

`details` (bounded ≤ 16 KB, no raw values, column names only as bounded
strings) for `decision_point_resolved`:

```json
{
  "decision_point": "column.semantic_role",
  "pattern": "cross_check",
  "level": 2, "cap": 2, "answer_ceiling": 2,
  "outcome_scope": "none", "evidence_partition": "train",
  "policy_digest": "…", "prompt_release_id": "…", "model_id": "jev-1.13.0",
  "rule":   {"rule_id": "split_column_roles.v3", "answer": {"<col>": "categorical_code"}},
  "ai":     {"kind": "jev", "purpose": "column.semantic_role", "release": "column.semantic_role@3",
             "answer": {"<col>": "categorical_code"}, "confidence": {"<col>": 0.91},
             "agent_run_id": null, "invocation_ids": ["…"], "semantic_answer_ids": ["…"]},
  "agreement": "agree | disagree | abstain | unavailable | partial",
  "used":   {"source": "rule | ai | human | ai_inherited", "answer": {"<col>": "categorical_code"}},
  "validator": {"verdict": "accepted", "reasons": []},
  "proposal_id": null,
  "revert": {"kind": "branch_change",
             "change": {"kind": "feature_transform_add", "column": "<col>", "transform": "impute_median"}},
  "columns_total": 31, "columns_detailed": 31
}
```

Per-column points write **one record per point per run**, with the per-column
detail capped at 64 columns (counts beyond). `facts` copies the numbers the
answer relied on (bands, counts). `evidence_refs` cite the experiment /
dataset / findings as ADR 0006 requires. `revert.kind` is one of
`branch_change` (with a `change` that validates as an `ExperimentChange`),
`ref_move`, `proposal_reject`, `new_root` (not revertible in place: target,
split, exclusion) or `none` (AI-after advice).

The decision record is the audit; the proposal row (ADR 0009 §2) is the
workflow item; the Jev answer row is the evaluation sample. All three link to
each other by id.

### 8. "AI off" at every point

| Situation | Behaviour at the point |
| --- | --- |
| Integration disabled (no `agents` extra installed, provider key absent, platform `AI_ENABLED` false — the default) or any kill switch off (global / workspace / agent / purpose) | the stage runs exactly as before Phase 6, **including** today's `semantic_*` `llm_invocations` rows with `llm_used = false` and reason "deterministic evidence was sufficient" (`lab_decision_ledger.py`; asserted by `test_pipeline_observability.py`); the pipeline event `decision_point_resolved` is emitted with `ai: "off"`; **no** decision record, proposal or Jev row is written (so the existing E2E and golden suites change only by the new event) |
| L0 (shadow) | identical values to AI-off by construction; a golden test runs the same upload AI-off and at L0 with a deliberately **wrong** fake Jev and asserts identical modeled columns, missing-value plan, search config and result digest |
| Level L0–L3 but provider timeout / breaker / budget / invalid output / policy unloadable | rule value; record written with `agreement: unavailable`; `llm_invocations` row with the refusal; counted for incidents |
| AI-after point with AI off | no Critic job is queued; the experiment page shows findings and the verifier only |
| Lead agent with AI off | threads API returns deterministic quick actions and templates (ADR 0009 §7.6); `llm_used = false` |

The engine, selection, holdout and splits never consult any of the above.

### 9. Cleaning actions: who owns them

The **cleaning recipe** owns every cleaning action: structural cleaning
(`run_structural_cleaning`), the missing-value plan and the column-type
decisions (`run_train_only_decisions`, `lab_decision_records`), all
deterministic, train-only and fold-aware. AI contributes only through
decision points (`column.missing_value_action`, `column.semantic_role`,
`column.is_identifier`, `feature.leakage_suspect`) whose applied values are
**typed overrides** the recipe already understands — the column treatments the
branch service materializes from `feature_transform_add/remove`
(`keep` / `numeric` / `categorical` / `drop`, `datetime_extract`).

Proposal payloads that touch cleaning are therefore limited to those change
kinds plus `review_flag`; a proposal never carries code, a transform
definition, a free-form fill value or a row filter. `domain_fill` values come
only from a code-owned table keyed by semantic role. New cleaning
*capabilities* (a new action type) are engine changes with tests, never agent
output. Phase 5's feature transforms follow P5.0's contract, not this section.

### 10. Never AI (restated as code rules)

Training, metric computation, model selection on validation data, holdout
scoring, reading holdout rows or holdout outcomes, building splits, entity or
group column inference, column exclusion above L1, spec lock / split plan /
release / champion promotion / write-tool execution above L1, loop control
(stop rules and selection across iterations), and any effect in auto-train
above L2. Property tests in P6.10-B assert each against the registry and the
tool catalog.

## Consequences

- P6.9-A adds one hook per point in five stage functions, one new pipeline
  event, a `SearchConfig.families` field in the fingerprint and inherited
  overrides on branches; the AI-off path is the current code path.
- Planner-backed points run before the job, so `run_experiment` /
  `propose_problem_spec` gain an optional typed `plan` input (a proposal id) —
  a `/v1` additive change in P6.9-A. The service accepts it only when the
  proposal belongs to the same workspace **and** project, has status
  `accepted` (human) or `applied` (L2, and then only for §1b L2 change
  kinds), has not been used before (single use: the proposal row records the
  consuming execution request id), and its payload re-validates against the
  current graph and the §1b ceilings **when the job runs**; otherwise the run
  proceeds rule-only and records the refusal.
- Holdout blindness becomes a property of the service layer (agent consumer
  mode), not of the principal; the Lab and MCP share one shaping module.
- The six legacy LLM purposes disappear as separate code paths after P6.9-A;
  their flags become kill switches, which lifts the ADR 0005 item 7 production
  refusal once the gateway enforces `llm_exposure_policy`.
- Every AI decision costs one `project_decision_records` row per point per run
  and one `semantic_decision_answers` row per Jev question; both are bounded
  and indexed by project.
- Trust rises only with sealed-benchmark and blind-label data; exposed user
  decisions never promote. The first weeks of Phase 6 are L0 everywhere by
  construction, and checkpoint G6 decides the first L1/L2 rows.
- With `lead.*` capped at L1, the Lab never runs a job without a click; this
  is the price of ASSISTANT.md rule 6.

## Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Let NOOA host the lead agent's multi-step loop (CodeAct) | in-process generated Python by default, unbounded iterations, sandbox Linux-only and bypassable (`AGENTS_NOOA_JEV.md` §2); the typed single-call loop keeps the "Predict only" rule and replayability |
| Keep holdout stripping at the route layer per principal | the lead agent is a human-session consumer calling services in-process; only a service-layer consumer mode is checkable by a property test |
| One global trust level per workspace | a leakage flag and a column role carry different risk; R3 evidence is per point |
| One cap per point (first draft) | an L2 point still has irreversible answers (exclusions, metric changes); the ceiling must follow the answer |
| Levels stored on `project_decision_records` | levels are workspace policy, not project facts (`project_id` NOT NULL, 7 subject CFKs) |
| Agent decisions inline in the worker stage (synchronous LLM) | a training run would wait minutes on a provider and fail on outages; AI-before answers travel as inputs instead |
| Separate "AI decision" table instead of decision records | a parallel ledger (non-negotiable 6); ADR 0006 §5 already models actor, evidence, supersession and revert |
| New change kinds `role_override` / `missing_value_override` | the branch service already materializes roles and imputations from `feature_transform_add/remove`; new kinds would be a second path through ADR 0006 §4 |
| User accept/reject as promotion evidence | the user saw both answers (anchoring); only blind labels and sealed partitions measure the AI |
| ECE ≤ 0.05 as a gate | at n = 300 a perfectly calibrated purpose fails about half the time; in-band precision lower bounds are the operative claim |
| L2 for `lead.run_experiment` (budgeted job) | ASSISTANT.md rule 6 requires a human confirm for every write; compute spend by injection is still a write |
| L3 inside auto-train for "safe" points | every auto-train decision has an asking path (needs_input, Inbox); L2 already applies reversibly |
| Fine-tune Jev per workspace | TypeSafe does not fine-tune or adapt on customer data (docs.typesafe.ai/models, 2026-10-04); calibration of thresholds on user labels is the customization |
| Use `jev-latest` | moving alias breaks the cache and the evidence behind a level |

## Non-goals

No AI inside the engine; no agent-authored cleaning code or feature code
(Phase 9 CodeAct lane, after P5.0); no per-user trust levels; no autonomous
release or champion promotion; no cross-workspace evidence sharing for levels;
no retrieval over past decisions for prompting (the envelope is built from the
graph); no change to the ADR 0006 record state machine or change-kind
vocabulary; no AI decision on branch runs.

## Founder decisions (2026-10-04)

All recommendations below were accepted except Q1, which the founder changed:

1. **Models (changed).** OpenAI is the only LLM provider for the MVP:
   `gpt-6.1-sol` for complex roles (lead agent / assistant, Planner, Critic,
   Improvement hypothesis, ops agent) and `gpt-6-luna` for simple, high-volume
   roles (Dataset Investigator, migrated legacy decision purposes, routine
   verifier). The allowlist is exactly these two ids plus Jev for semantic
   decisions (ADR 0009 §3, §9). OpenAI publishes no dated snapshot for either
   id (models page, 2026-10-04), so the bare ids are pinned; the gateway
   records the provider-reported resolved model on every ledger row, and a
   change of that value counts as a model change (levels for the affected
   (prompt release, model) pair fall to L0 until R3 re-runs, §3/§4). If OpenAI
   publishes dated snapshots, P6.2-B pins them instead.
2. **L2 in the MVP:** yes, only for `column.semantic_role` (numeric ↔
   categorical among modeled columns), `column.missing_value_action`
   (`impute_median` ↔ `impute_most_frequent`) and `training.families_budget`
   (family subset and time budget), each gated by §4 and first set at G6.
3. **Promotion statistics:** §4 as written.
4. **Leakage:** AI only flags; exclusion always waits for a person.
5. **Planner answers before the job,** superseded once results exist; never
   block a worker stage.
6. **One decision record per point per run.**
7. **Ops defaults:** auto-retrain ≤ 2 per week at L2 as new roots;
   auto-release off; automatic rollback is the rule action
   `ops.rollback_threshold.v1` with the AI capped at L1; `ops.diagnose` may
   reach L3.

## Open questions for the founder (answered above)

1. **Default lead-agent model and provider.** Recommended: Sonnet 5.5 as the
   lead and specialist default, Haiku 4.5 as the fallback for the Investigator
   and intent routing, Opus 5.5 allowed for the lead by workspace policy only;
   the Critic runs batch-eligible. Exact dated provider model ids are pinned in
   the platform default policy at P6.2-B. Alternative: keep OpenAI (today's
   `gpt-4o-mini` decision agent) as the single provider for the MVP.
2. **Is L2 allowed anywhere in the MVP?** Recommended: yes, but only for
   `column.semantic_role` (numeric ↔ categorical among modeled columns),
   `column.missing_value_action` (`impute_median` ↔ `impute_most_frequent`) and
   `training.families_budget`, each gated by §4 and first set at checkpoint
   G6, never before. `column.is_identifier` and `improve.next_action` are L1
   in this revision. Alternative: cap the MVP at L1 everywhere.
3. **Promotion statistics.** Confirm §4: sealed partitions and
   inverse-probability-weighted blind labels, gated separately and both must
   pass; ≥ 20 sealed datasets (or a small-sample correction); non-inferiority
   margins 5 pt (L1) / 1 pt (L2) from a cluster bootstrap; precision gates as
   Wilson lower bounds with reachable n (LB ≥ 0.98 needs 189 cases with 0
   errors or 280 with 1; LB ≥ 0.90 needs 100 with ≤ 4); the L2 gate for
   `column.semantic_role` is the deployed-policy precision on overrides
   (weighted combination of sealed and blind cases, effective n ≥ 189/0 or
   280/1); Holm correction and alpha-spending for interim looks; demotion is
   level-specific and mirrors the promotion bars (L1: UB of gap < −5 pt or
   precision UB < 0.90; L2: UB < −1 pt or precision UB < 0.98), with no
   point-estimate clauses; exposed acceptance as monitoring only. These are
   deliberately conservative; R3 can propose relaxations with evidence.
4. **`feature.leakage_suspect` cap L1 (flag only).** Confirm that AI never
   excludes a column, even at p ≥ 0.99 with the rule agreeing — exclusion
   always waits for a person (recommended), or allow L2 when **both** Jev and
   the rule say `exclude` (the rule alone already excludes today).
5. **Planner answers before the job.** Confirm that Planner-backed points
   (`split.strategy`, `training.families_budget`, `spec.objective`) are
   resolved at proposal time, superseded once results exist, and never block a
   worker stage (recommended); the alternative is a synchronous call with a
   30 s cap inside the stage.
6. **One record per point per run** (recommended) versus one per column for
   the three per-column points (fully granular, ~30× more rows).
7. **Ops defaults for Phase 7** (recorded now so ADR 0009's policy schema has
   values): auto-retrain ≤ 2 per week at L2 as new roots; auto-release off
   (L1); automatic rollback is the **rule action** `ops.rollback_threshold.v1`
   (precision −0.05 over 2 labeled windows, notify) under the autonomy policy
   with the AI capped at L1; `ops.diagnose` may reach L3. Confirm or change.
