# DCLab Experiment — Auto-train: repeated_customer.csv

**Task:** open_ingest_7a063057d135
**Type:** binary
**Horizon (days):** None
**Rows:** 100
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** LOW
**Split:** group_disjoint
**Fusion:** None

## Best single model

- Family: logistic_regression
- Groups: features
- Validation score: 0.8487103174603174

## Test metrics (held out)

- accuracy: 0.85
- roc_auc: 0.9733333333333334
- pr_auc: 0.9428571428571428
- precision: 1.0
- recall: 0.4
- f1: 0.5714285714285714
- log_loss: 0.2484428767557215
- balanced_accuracy: 0.7
- brier: 0.08114213983791453
- brier_score: 0.08114213983791453
- calibration_gap: 0.18207331212703484
- confusion_matrix: {'tn': 15, 'fp': 0, 'fn': 3, 'tp': 2}
- top_decile_lift: 4.0
- top_k_precision: 1.0
- positive_rate: 0.25

## Comparison (validation)

- baseline logistic_regression / features: 0.8487103174603174
- best single: logistic_regression (0.8487103174603174)

## Feature group usefulness


## Combinations


## Warnings

- customer_id: NONE (identifier_not_a_predictor)
