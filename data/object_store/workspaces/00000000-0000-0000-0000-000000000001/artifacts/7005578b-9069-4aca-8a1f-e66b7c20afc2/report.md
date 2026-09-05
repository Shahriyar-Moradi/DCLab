# DCLab Experiment — Auto-train: telco.csv

**Task:** open_ingest_7a4548b2d275
**Type:** binary
**Horizon (days):** None
**Rows:** 80
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
- Validation score: 0.4947335204478061

## Test metrics (held out)

- accuracy: 0.4375
- roc_auc: 0.46875
- pr_auc: 0.628525641025641
- precision: 0.46153846153846156
- recall: 0.75
- f1: 0.5714285714285714
- log_loss: 0.6978118733810208
- balanced_accuracy: 0.4375
- brier: 0.252342775015868
- brier_score: 0.252342775015868
- calibration_gap: 0.01554012783491876
- confusion_matrix: {'tn': 1, 'fp': 7, 'fn': 2, 'tp': 6}
- top_decile_lift: 2.0
- top_k_precision: 1.0
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.4947335204478061
- best single: logistic_regression (0.4947335204478061)

## Feature group usefulness


## Combinations


## Warnings

- customer_id: NONE (identifier_not_a_predictor)
- gender: MEDIUM (high_correlation_alone)
