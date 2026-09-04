# DCLab Adaptive Model Builder — Correctness

This document is the production-path proof for Correctness Repairs 1–3.
It does not add feature search, HPO, calibration, thresholds, or ensembles.

The authoritative Labs product path is:

```
POST /app/labs/uploads
  → ClientLabUpload
  → run_auto_train_job
  → structural cleaning
  → HoldoutPlan → FINAL HOLDOUT LOCK
  → one ModelDevelopmentPlan (train-only)
  → missing-value / column roles / feature engineering
  → dataset persistence
  → Experiment / PipelineRun
  → candidate CV on the planned splitter
  → CV-only winner lock
  → winner-only final holdout
  → ModelVersion
  → PipelineVerifier
  → Pipeline Monitor evidence
```

Direct `run_experiment(frame)` remains a unit/integration helper. It is not
the proof that the Labs product builds scientifically correct models.

## HoldoutPlan

Built by `apps/api/app/engine/modeling/holdout_planner.py` after structural
cleaning and **before** the final holdout is locked. `plan_version` is
`dclab.holdout_plan.v1`.

| Situation | Strategy | Isolation |
| --- | --- | --- |
| Ordinary binary | `stratified_random` | class balance preserved |
| Ordinary regression | `random` | shuffled 80/20 |
| Repeated identifier-like entity | `group_disjoint` | `train groups ∩ test groups = ∅` |
| Strong ordered time | `temporal_future` | latest slice; `max(train_time) <= min(test_time)` |
| Grouping and strong time together | `unsupported` | run fails closed |

The planner may use column names, dtypes, row/entity structure, timestamp
structure, the locked target/task, and non-learned structural statistics. It
must not use predictive performance. There is no silent fallback to random
80/20 when group or time isolation is required.

## ModelDevelopmentPlan

Built by `apps/api/app/engine/modeling/leakage_auditor.py` from the **locked
training partition only**. `plan_version` is `dclab.model_development_plan.v1`.

The plan nests:

- ProblemProfile (`dclab.problem_profile.v1`)
- ValidationPlan (`dclab.validation_plan.v1`)
- MetricPlan (`dclab.metric_plan.v1`)
- leakage assessment (`dclab.leakage_audit.v1`)
- `allowed_features` / `excluded_features`
- `group_column` / `time_column`
- recommended model-family hints (not a search)

Validation choices:

| Situation | Strategy |
| --- | --- |
| Ordinary binary | `StratifiedKFold` |
| Ordinary regression | `KFold` |
| Repeated entity, binary | `StratifiedGroupKFold` or `GroupKFold` |
| Repeated entity, regression | `GroupKFold` |
| Strong ordered time | `TimeSeriesSplit` |

Metric choices: binary primary **PR-AUC**; regression primary **MAE**.

HIGH/CRITICAL leakage features and identifiers are excluded from estimators.
The group column stays on the frame for splitting and is never an estimator
feature. A datetime `time_column` keeps the same name after unix-seconds
encoding.

## Single-plan architecture

Production Labs creates **one** HoldoutPlan and **one** ModelDevelopmentPlan
per run.

1. Auto-train plans holdout, locks the test partition, then plans model
   development on train rows only.
2. Both plans are placed on `SearchConfig`.
3. The experiment runner **consumes** those objects. It does not call
   `plan_holdout()` or `plan_model_development()` again when they are supplied.
4. `result.validation_plan` and `result.metric_plan` are the nested objects
   from that one `ModelDevelopmentPlan`.
5. Candidate `cv_strategy` equals the plan validation strategy.
6. Winner `selection_metric` equals the plan primary metric.
7. Every trained candidate `feature_set` is a subset of `allowed_features`
   and is disjoint from excluded columns.

`result.scientific_plan_source` is `"provided"` on the Labs path and
`"computed"` only for direct runner use without a supplied plan. A second,
inconsistent plan in the same persisted report fails
`single_authoritative_development_plan`.

## Production execution order

Observed on real Labs uploads via `MlRunEvent.sequence` (and identical after
DB replay):

```
holdout_plan_selected
  < holdout_locked
  < model_development_plan_locked
  < cv_fold_started
  < winner_locked
  < final_test_started
  < final_test_completed
```

Winner lock uses cross-validation only. Rejected candidates never receive
`test_metrics`. Predictions are persisted (`test_predictions.csv` plus
`experiment.result["test_predictions"]`). Successful runs publish exactly one
`ModelVersion` for the selected candidate. Failed or rejected candidates
cannot own that version.

## Classification example

Fixture: `ordinary_binary` (`age`, `income`, `region`, `outcome`).

Labs path result:

- Holdout `stratified_random`
- Validation `StratifiedKFold`
- Selection metric `pr_auc`
- Winner locked before `final_test_started`
- Winner-only holdout; predictions persisted
- Lineage: upload → workflow run → pipeline run → dataset → candidates →
  winner → ModelVersion
- `PipelineVerifier` overall status `VERIFIED`

## Regression example

Fixture: `regression` (`tenure`, `usage`, `segment`, `revenue`).

Labs path result:

- Holdout `random`
- Validation `KFold` (no stronger grouping or time structure)
- Selection metric `mae`
- Holdout metrics include MAE, RMSE, and R2
- Winner-only holdout
- `PipelineVerifier` `VERIFIED`

## Grouped example

Fixture: `repeated_entity` with repeated `customer_id`.

Labs path result:

- Holdout `group_disjoint` on `customer_id`
- From the persisted engineered CSV and split source-row ids:
  `train customers ∩ test customers = ∅`
- Validation `StratifiedGroupKFold` or `GroupKFold`
- Every fold: `fold train customers ∩ fold validation customers = ∅`
  (fold `group_overlap` is empty, and the same fact is recomputed from
  `train_provenance` / `validation_provenance` joined to `customer_id`)
- Raw `customer_id` is absent from estimator columns and candidate
  `feature_set`

## Temporal example

Fixture: `temporal` with ordered `as_of_date`.

Labs path result:

- Holdout `temporal_future`; `max(train_time) <= min(test_time)` on split
  metadata **and** on persisted unix-seconds `as_of_date` after production
  `datetime_to_unix_seconds` feature engineering
- Validation `TimeSeriesSplit`; every fold is chronological
- `time_column` remains `as_of_date`
- Pipeline Monitor holdout payload includes `train_time_max` and
  `test_time_min`

## Leakage example

Fixture: `leakage` with `safe_feature`, `post_outcome_feature`,
`target_proxy`, `region`, `outcome`.

Labs path result:

- Leakage audit excludes `post_outcome_feature` and `target_proxy`
- Those columns are in `removed_features` and are absent from feature
  engineering output used by estimators and from candidate `feature_set`
- `safe_feature` remains allowed and is modeled
- The run still completes and publishes a ModelVersion

## PipelineVerifier

Every production E2E reruns `PipelineVerifier` on the persisted
`technical_report`. Successful Labs probes are `VERIFIED` (warnings are
allowed only when documented in the check ledger).

Deliberate corruption of a **real** Labs `technical_report` must fail:

| Corruption | Check |
| --- | --- |
| Group holdout overlap | `group_holdout_has_zero_group_overlap` |
| Temporal holdout reversal | `temporal_holdout_respects_order` |
| Second inconsistent development plan | `single_authoritative_development_plan` |
| Metric mismatch | `primary_metric_matches_selection_metric` |
| Excluded feature in a candidate | `excluded_features_not_in_candidates` |
| Winner test before lock | `final_fit_after_lock` |

## Pipeline Monitor

The existing Pipeline Monitor (no redesign) shows:

- Holdout Strategy, including group overlap and temporal bounds
  (`train_time_max`, `test_time_min`)
- Validation Strategy, including group overlap and fold counts
- Metric Strategy
- Leakage Audit
- Allowed Features / Excluded Features
- Fold-by-fold CV
- Candidate comparison with locked winner vs not evaluated
- Final holdout

## Tests

Production-path fixtures and helpers:

- `apps/api/tests/adaptive_modeling/fixtures.py`
  (`ordinary_binary`, `regression`, `repeated_entity`, `temporal`, `leakage`)
- `apps/api/tests/adaptive_modeling/production.py`
  (`POST /app/labs/uploads` then `run_auto_train_job`)

Proof suite: `apps/api/tests/test_adaptive_modeling_production_e2e.py`.

## Known limitations

- Feature search, HPO, calibration, threshold optimization, and ensembles are
  out of scope. Allowed features are used as a single group combination.
- Grouping plus strong temporal structure remains `unsupported` (fail-closed).
- Name-only suspicious columns are not excluded; exclusion requires
  statistical or availability evidence as defined by the auditor.
- Conservative auto-train treats `requires_review` as excluded.
- Direct `run_experiment()` without a supplied plan still computes its own
  plan (`scientific_plan_source="computed"`). That path is not the Labs
  product proof.
- After datetime-to-unix encoding, fold ISO timestamps may look like epoch
  instants if an integer unix value is formatted as a pandas Timestamp. Order
  is still proven from the numeric persisted `time_column` and from
  `train_time_max <= validation_time_min` on fold evidence.
- Browser Playwright (`apps/web` `npm run e2e`) is a separate seeded cluster
  on port 55432 and API port 8001. It is not a substitute for the pytest
  Labs-path scientific proofs.

## Definition of done

Repair 3 is complete only when all of the following are true:

- real Labs binary, regression, grouped, temporal, and leakage E2Es pass
- adaptive final holdout is proven on the product path
- one authoritative HoldoutPlan and ModelDevelopmentPlan per run
- candidate features match the plan
- winner selection matches the plan
- winner-only holdout is proven
- ModelVersion lineage is proven
- PipelineVerifier passes (or documented warnings only)
- event order is proven, including DB replay
- existing tests remain green
