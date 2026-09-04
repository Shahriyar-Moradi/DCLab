# DCLab Adaptive Model Builder (Phase 1)

Phase 1 is a **scientific planning layer**. It decides how a tabular open-ingest
run should be profiled, validated, scored, and leakage-audited **before**
candidates train. It does not search features, tune hyperparameters, calibrate
probabilities, optimize decision thresholds, or build ensembles.

The layer exists because a generic sklearn path can look accurate while being
scientifically wrong: a post-outcome column inflates holdout accuracy; a
repeated customer leaks across folds; a timestamp-sorted problem is shuffled;
an imbalanced label is ranked with accuracy. Adaptive Model Builder makes those
choices explicit, persisted, observable, and verifier-checked.

## Why it exists

Open ingest accepts ordinary CSVs. The pipeline must still:

1. Profile the **locked training partition only**.
2. Choose one validation strategy that matches the task structure.
3. Choose one primary selection metric.
4. Audit prediction-time feature availability and leakage risk.
5. Lock a single `ModelDevelopmentPlan` that candidates must obey.

Accuracy is not the success criterion for Phase 1. If removing a leaked feature
drops apparent score from 0.94 to 0.84, that is **correction**, not regression.

## Open-ingest order

```
upload → target/task → structural cleaning
      → pre-split structural analysis → HoldoutPlan → FINAL HOLDOUT LOCK
      → train-only ProblemProfile
      → ValidationPlan
      → MetricPlan
      → LeakageAuditor
      → ModelDevelopmentPlan
      → missing-value / column roles / feature engineering / candidates
      → CV on the planned splitter
      → CV-only winner lock
      → final fit on full train
      → winner-only holdout
      → PipelineVerifier (authoritative)
      → OpenAI auditor (advisory)
```

Auto-train creates the one authoritative `ModelDevelopmentPlan` on the locked
training partition, before missing-value decisions, column roles, and feature
engineering. Production Labs passes that exact plan (and the `HoldoutPlan`) into
the experiment runner through `SearchConfig`. The runner consumes it and does
not call `plan_model_development()` again. Direct `run_experiment()` use may
still plan autonomously when no plan is supplied. Planning Observatory events
are emitted once, at the production planning pass.

The locked holdout is never profiled, audited, or used for metric or splitter
choice. A raw timestamp that caused temporal validation remains
`time_column` even if feature engineering later stores unix seconds in that
column. The group column remains on the frame for validation and is never an
estimator feature.

Open-ingest `TaskSpec.validation_strategy` and `evaluation_metric` are populated
from the locked ValidationPlan and MetricPlan. They are compatibility fields
and must not contradict execution.

Pre-split HoldoutPlan may use only column names, dtypes, row/entity structure,
timestamp structure, the locked target/task, and non-learned structural
statistics. It must not use predictive performance.

## HoldoutPlan

Built by `apps/api/app/engine/modeling/holdout_planner.py` from the cleaned
table **before** the final holdout is locked. This is a separate object from
ValidationPlan; they are not merged.

| Situation | Final holdout |
| --- | --- |
| Ordinary binary | `stratified_random` |
| Ordinary regression | `random` |
| Repeated identifier-like entity | `group_disjoint` (`train groups ∩ test groups = ∅`) |
| Strong ordered time structure | `temporal_future` (latest slice; `max(train_time) <= min(test_time)`) |
| Grouping and strong temporal together | `unsupported` (run fails closed) |

The implementation never silently falls back to a random 80/20 split when
group or time evidence requires stronger isolation. Split metadata records
strategy, requested vs actual test size, group overlap count, time ranges,
and train/test provenance.

## ProblemProfile

Built by `apps/api/app/engine/modeling/problem_profile.py` from the training
frame.

Records task type, train row/feature counts, class balance or regression
target summary, numeric/categorical/boolean/datetime/identifier/text columns,
repeated-entity candidates, time candidates, and geo coordinate pairs.

It does not inspect holdout rows, does not store row provenance arrays, and
does not choose a model family.

## ValidationPlan

Built by `apps/api/app/engine/modeling/validation_planner.py`.

| Situation | Strategy |
| --- | --- |
| Ordinary binary | `StratifiedKFold` |
| Ordinary regression | `KFold` |
| Repeated identifier-like entity, binary | `StratifiedGroupKFold` or `GroupKFold` |
| Repeated entity, regression | `GroupKFold` |
| Strong ordered time structure | `TimeSeriesSplit` |
| Grouping and strong temporal together | `unsupported` (run fails closed) |

Requested vs actual fold counts are recorded. Too-small minority or group
counts reduce folds with an explicit fallback reason. Group folds must have
zero group overlap. Time folds must be chronological.

Grouping is used only for identifier-like repeated entities, not for ordinary
low-cardinality categoricals — including `*_id` codes with few distinct values
(for example `delivery_category_id` with three levels).

## MetricPlan

Built by `apps/api/app/engine/modeling/metric_planner.py`.

- Binary primary metric: **PR-AUC**, including balanced labels. Imbalance
  ratio and minority fraction are still recorded when meaningful.
- Regression primary metric: **MAE**. RMSE, R2, and MSE are secondary.

The experiment overwrites `TaskSpec.evaluation_metric` with the planned
primary metric so winner selection cannot silently use a different score.

## FeatureAvailability

Each candidate predictor is labeled:

`known_before_prediction | known_at_prediction | known_after_prediction | unknown`

Sources: `deterministic`, `llm`, or `explicit_configuration`. Availability is
an input to leakage risk; it is not by itself an exclude rule except where the
auditor’s deterministic policy says so (for example a post-outcome datetime
with supporting stats).

## LeakageAuditor

`apps/api/app/engine/modeling/leakage_auditor.py` is the open-ingest drop
authority. The older `detect_leakage` helper remains for non-open-ingest paths
and for benchmark comparison only.

Risk: `NONE | LOW | MEDIUM | HIGH | CRITICAL`.
Action: `keep | keep_with_warning | requires_review | exclude`.

Deterministic rules (train only):

- Direct target duplicate → CRITICAL exclude.
- Combined name + statistical proxy, or post-outcome datetime with evidence →
  HIGH exclude.
- Name tokens alone do **not** exclude.
- Correlation or single-feature score alone does **not** exclude (MEDIUM
  `requires_review`).
- Identifiers are excluded from estimators and may still be the `group_column`.
- Datetime-like columns are not treated as identifiers.

Conservative auto-train keeps only `keep` and `keep_with_warning`.

## Optional semantic LLM role

When a column is ambiguous, the auditor may consult `semantic_leakage` with
bounded `LeakageReviewEvidence` (no raw rows). The LLM may propose
availability/risk/reasons only. It cannot keep or exclude a feature. The
decision validator rejects HIGH/CRITICAL without supporting stats. Provider
failure is fail-closed (no LLM decision). OpenAI pipeline audits remain a
separate advisory purpose.

## ModelDevelopmentPlan

The locked plan contains:

- nested ProblemProfile, ValidationPlan, MetricPlan
- feature availability assessments
- leakage assessment
- `allowed_features` / `excluded_features`
- `group_column` / `time_column`
- recommended model-family **hints** (not a search)
- `plan_version`

Candidates are assembled from allowed features only. HIGH/CRITICAL excluded
columns must not appear in `feature_set`. Open-ingest candidate fingerprints
include dataset version, feature set, model family, hyperparameters,
preprocessing config, holdout plan version/strategy, validation plan
version/strategy, primary metric, and model-development-plan version. They do
not include evidence payloads. Two scientifically different configurations
must not share a fingerprint.

## Observability

Reuse `MlRunEvent`. No second event system. Holdout planning is emitted after
structural cleaning and before CV. Train-only scientific planning is emitted
once after holdout lock and before missing-value decisions and CV. The runner
does not emit a second planning pass when Labs already supplied the plan.

| Event | Stage |
| --- | --- |
| `holdout_plan_selected` | `holdout_plan` |
| `holdout_locked` | `holdout_lock` |
| `problem_profile_started` / `problem_profile_completed` | `problem_profile` |
| `validation_plan_selected` | `validation_plan` |
| `metric_plan_selected` | `metric_plan` |
| `leakage_audit_started` | `leakage_audit` |
| `feature_leakage_warning` | `leakage_audit` |
| `feature_excluded_for_leakage` | `leakage_audit` |
| `leakage_audit_completed` | `leakage_audit` |
| `model_development_plan_locked` | `model_development_plan` |

Payloads cap name lists and never include datasets, `train_source_rows`,
`test_source_rows`, or sample rows. The existing Observatory sanitizer still
redacts secrets and provenance keys.

Pipeline Monitor adds compact panels (not a redesign): Problem Profile,
Validation Strategy (including requested/actual folds and group overlap),
Final Holdout, Metric Strategy, Leakage Audit, Allowed Features, Excluded
Features.

## Verifier checks

`PipelineVerifier` remains authoritative. OpenAI audit remains advisory.

New deterministic checks:

- `model_development_plan_exists`
- `validation_plan_exists`
- `validation_strategy_matches_task`
- `validation_fold_count_truthful`
- `group_validation_has_zero_group_overlap`
- `temporal_validation_respects_order`
- `primary_metric_matches_selection_metric`
- `leakage_audit_exists`
- `critical_leakage_feature_not_modeled`
- `excluded_features_not_in_candidates`
- `final_test_not_used_in_problem_profile`
- `holdout_plan_exists`
- `holdout_strategy_matches_problem_structure`
- `holdout_train_test_disjoint`
- `group_holdout_has_zero_group_overlap`
- `temporal_holdout_respects_order`
- `single_authoritative_development_plan`
- `runner_validation_matches_plan`
- `runner_metric_matches_plan`
- `candidate_features_match_plan`
- `task_metric_matches_plan`
- `candidate_fingerprint_contains_plan_identity`

A group-aware ValidationPlan with a random or stratified final holdout fails.
A TimeSeriesSplit ValidationPlan with a random final holdout fails.

Missing plan evidence is `NOT_VERIFIABLE`. Present-but-violating evidence is
`FAIL`. Corruption tests inject group overlap, reverse time, metric mismatch,
excluded features in candidates, holdout statistics in ProblemProfile, group
overlap in the final holdout, a reversed temporal holdout, and a group or
temporal CV paired with a random final holdout.

## Benchmark methodology

Fixtures live in `apps/api/tests/adaptive_modeling/` and are **not** imported
by production services. The user’s hyper-ack notebook is a conceptual
reference only.

Required fixture names:

`binary_balanced`, `binary_imbalanced`, `regression`, `repeated_entity`,
`temporal`, `leakage_fixture`, `datetime_detection`, `geo_detection`.

Phase 1 is compared with:

1. Naive modeling that keeps a post-outcome proxy (`result_code`).
2. The older name-or-AUC `detect_leakage` helper.
3. The Phase 1 auditor + ValidationPlan + MetricPlan.

Comparisons score **validation correctness, metric selection, entity
isolation, temporal order, leakage detection, and holdout integrity**. If
Phase 1 accuracy is lower because a leaked feature was removed, do not call
that a regression.

## Phase 1 does NOT implement

- advanced feature search
- hyperparameter optimization
- threshold optimization
- calibration
- ensembles
- a new event table or a redesigned Pipeline Monitor

## Known limitations

- Semantic leakage review is optional, fail-closed, and cannot drop columns.
- Grouping plus strong temporal structure is unsupported rather than given a
  custom splitter.
- Low-cardinality `*_id` codes (for example a 3-level `delivery_category_id`)
  are treated as categoricals, not grouping keys.
- Conservative auto-train treats `requires_review` as excluded; that can drop
  ambiguous-but-legitimate columns.
- Model-family hints are not a portfolio search.
- The older `detect_leakage` path still exists for non-open-ingest experiments.
- Date/geo detection is profile evidence; Phase 1 does not add geo-specific
  estimators.
- Fold reduction is truthful but still uses sklearn splitters, not nested CV.

## Phase 2 handoff

Do not start Phase 2 from this document’s planning types. Phase 2, if
approved later, would be a separate capability slice:

1. Advanced feature search over the **allowed** feature set only.
2. Hyperparameter optimization inside the locked ValidationPlan.
3. Threshold optimization on train/CV evidence, never on the final holdout.
4. Probability calibration.
5. Constrained ensembles of leakage-safe members.

Phase 2 must not weaken holdout lock, group isolation, chronological
validation, MetricPlan selection, or LeakageAuditor exclusions.

Production Labs-path proofs (upload → auto-train → ModelVersion → verifier)
are recorded in [DCLAB_ADAPTIVE_MODEL_BUILDER_CORRECTNESS.md](DCLAB_ADAPTIVE_MODEL_BUILDER_CORRECTNESS.md).
