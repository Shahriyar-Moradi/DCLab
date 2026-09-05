# DCLab Experiment — Auto-train: plans.csv

**Task:** open_ingest_1112558be16f
**Type:** binary
**Horizon (days):** None
**Rows:** 200
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** MEDIUM
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: logistic_regression
- Groups: features
- Validation score: 0.4585055661429001

## Test metrics (held out)

- accuracy: 0.425
- roc_auc: 0.44749999999999995
- pr_auc: 0.5084095267155997
- precision: 0.4117647058823529
- recall: 0.35
- f1: 0.3783783783783784
- log_loss: 0.7040084771768468
- balanced_accuracy: 0.425
- brier: 0.25542150128589886
- brier_score: 0.25542150128589886
- calibration_gap: 0.002506407218576756
- confusion_matrix: {'tn': 10, 'fp': 10, 'fn': 13, 'tp': 7}
- top_decile_lift: 1.0
- top_k_precision: 0.5
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.4585055661429001
- best single: logistic_regression (0.4585055661429001)

## Feature group usefulness


## Combinations


## Warnings

- customer_id: NONE (identifier_not_a_predictor)
- gender: MEDIUM (high_correlation_alone)
