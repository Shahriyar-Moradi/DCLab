# DCLab Experiment — Auto-train: leakage.csv

**Task:** open_ingest_ccd4cce37862
**Type:** binary
**Horizon (days):** None
**Rows:** 200
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** CRITICAL
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: logistic_regression
- Groups: features
- Validation score: 0.6001117924257346

## Test metrics (held out)

- accuracy: 0.475
- roc_auc: 0.37750000000000006
- pr_auc: 0.4571327471716456
- precision: 0.47368421052631576
- recall: 0.45
- f1: 0.46153846153846156
- log_loss: 0.7239661867045516
- balanced_accuracy: 0.475
- brier: 0.26515316801282945
- brier_score: 0.26515316801282945
- calibration_gap: 0.1948590656900898
- confusion_matrix: {'tn': 10, 'fp': 10, 'fn': 11, 'tp': 9}
- top_decile_lift: 1.0
- top_k_precision: 0.5
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.6001117924257346
- best single: logistic_regression (0.6001117924257346)

## Feature group usefulness


## Combinations


## Warnings

- post_outcome_feature: CRITICAL (direct_target_duplicate)
- target_proxy: HIGH (target_proxy, suspicious_name, strong_single_feature_score)
