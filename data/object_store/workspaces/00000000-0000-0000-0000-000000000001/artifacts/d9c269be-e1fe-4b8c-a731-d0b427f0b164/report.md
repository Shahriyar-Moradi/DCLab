# DCLab Experiment — Auto-train: classification.csv

**Task:** open_ingest_d6252a9a345a
**Type:** binary
**Horizon (days):** None
**Rows:** 100
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** MEDIUM
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: lightgbm
- Groups: features
- Validation score: 0.9810034013605442

## Test metrics (held out)

- accuracy: 0.95
- roc_auc: 0.9722222222222222
- pr_auc: 0.9944444444444444
- precision: 1.0
- recall: 0.9444444444444444
- f1: 0.9714285714285714
- log_loss: 0.1406064884040563
- balanced_accuracy: 0.9722222222222222
- brier: 0.039542412524013915
- brier_score: 0.039542412524013915
- calibration_gap: 0.09916259133299155
- confusion_matrix: {'tn': 2, 'fp': 0, 'fn': 1, 'tp': 17}
- top_decile_lift: 1.1111111111111112
- top_k_precision: 1.0
- positive_rate: 0.9

## Comparison (validation)

- baseline logistic_regression / features: 0.9305574980574981
- best single: lightgbm (0.9810034013605442)

## Feature group usefulness


## Combinations


## Warnings

- customer_id: NONE (identifier_not_a_predictor)
- age: MEDIUM (high_correlation_alone)
