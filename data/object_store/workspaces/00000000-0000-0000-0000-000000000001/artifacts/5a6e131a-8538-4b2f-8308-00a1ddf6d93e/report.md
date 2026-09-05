# DCLab Experiment — Auto-train: reproducibility.csv

**Task:** open_ingest_13060a8c2d16
**Type:** binary
**Horizon (days):** None
**Rows:** 200
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** LOW
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: xgboost
- Groups: features
- Validation score: 0.5729734442438453

## Test metrics (held out)

- accuracy: 0.525
- roc_auc: 0.5375
- pr_auc: 0.5378489498245191
- precision: 0.5333333333333333
- recall: 0.4
- f1: 0.45714285714285713
- log_loss: 1.1525795484235213
- balanced_accuracy: 0.525
- brier: 0.3529221748907291
- brier_score: 0.3529221748907291
- calibration_gap: 0.2052152679115534
- confusion_matrix: {'tn': 13, 'fp': 7, 'fn': 12, 'tp': 8}
- top_decile_lift: 1.0
- top_k_precision: 0.5
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.5187481512270494
- best single: xgboost (0.5729734442438453)

## Feature group usefulness


## Combinations

