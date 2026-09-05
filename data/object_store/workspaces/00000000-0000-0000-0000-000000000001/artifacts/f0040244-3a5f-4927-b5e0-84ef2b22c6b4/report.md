# DCLab Experiment — Auto-train: upload.csv

**Task:** open_ingest_56d0867c0d05
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

- Family: logistic_regression
- Groups: features
- Validation score: 0.61975635827538

## Test metrics (held out)

- accuracy: 0.725
- roc_auc: 0.6733333333333333
- pr_auc: 0.4377246618605645
- precision: 0.4444444444444444
- recall: 0.4
- f1: 0.42105263157894735
- log_loss: 0.5174969258456853
- balanced_accuracy: 0.6166666666666667
- brier: 0.16577755989079832
- brier_score: 0.16577755989079832
- calibration_gap: 0.09900753724066677
- confusion_matrix: {'tn': 25, 'fp': 5, 'fn': 6, 'tp': 4}
- top_decile_lift: 2.0
- top_k_precision: 0.5
- positive_rate: 0.25

## Comparison (validation)

- baseline logistic_regression / features: 0.61975635827538
- best single: logistic_regression (0.61975635827538)

## Feature group usefulness


## Combinations


## Warnings

- customer_id: NONE (identifier_not_a_predictor)
