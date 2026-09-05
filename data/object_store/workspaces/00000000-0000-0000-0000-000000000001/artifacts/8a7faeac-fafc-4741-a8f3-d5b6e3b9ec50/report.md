# DCLab Experiment — Auto-train: observability.csv

**Task:** open_ingest_f803543cbb44
**Type:** binary
**Horizon (days):** None
**Rows:** 120
**Candidates generated:** 4
**Trained:** 3
**Failed:** 1
**Robust:** 3
**Diverse selected:** 1
**Leakage risk:** LOW
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: logistic_regression
- Groups: features
- Validation score: 0.9558822490640673

## Test metrics (held out)

- accuracy: 0.875
- roc_auc: 0.9500000000000001
- pr_auc: 0.9636072261072262
- precision: 0.9230769230769231
- recall: 0.8571428571428571
- f1: 0.8888888888888888
- log_loss: 0.2994937315980557
- balanced_accuracy: 0.8785714285714286
- brier: 0.09751493811335586
- brier_score: 0.09751493811335586
- calibration_gap: 0.25085406141834216
- confusion_matrix: {'tn': 9, 'fp': 1, 'fn': 2, 'tp': 12}
- top_decile_lift: 1.7142857142857142
- top_k_precision: 1.0
- positive_rate: 0.5833333333333334

## Comparison (validation)

- baseline logistic_regression / features: 0.9558822490640673
- best single: logistic_regression (0.9558822490640673)

## Feature group usefulness


## Combinations

