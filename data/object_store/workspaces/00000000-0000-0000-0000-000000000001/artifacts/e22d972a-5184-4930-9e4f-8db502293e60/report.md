# DCLab Experiment — Auto-train: telco_like.csv

**Task:** open_ingest_cf234e845481
**Type:** binary
**Horizon (days):** None
**Rows:** 120
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** LOW
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: logistic_regression
- Groups: features
- Validation score: 0.4354897500160658

## Test metrics (held out)

- accuracy: 0.875
- roc_auc: 0.8703703703703705
- pr_auc: 0.8217592592592593
- precision: 1.0
- recall: 0.5
- f1: 0.6666666666666666
- log_loss: 0.418284547866189
- balanced_accuracy: 0.75
- brier: 0.1295266177276769
- brier_score: 0.1295266177276769
- calibration_gap: 0.24105781683550123
- confusion_matrix: {'tn': 18, 'fp': 0, 'fn': 3, 'tp': 3}
- top_decile_lift: 4.0
- top_k_precision: 1.0
- positive_rate: 0.25

## Comparison (validation)

- baseline logistic_regression / features: 0.4354897500160658
- best single: logistic_regression (0.4354897500160658)

## Feature group usefulness


## Combinations


## Warnings

- customer_id: NONE (identifier_not_a_predictor)
