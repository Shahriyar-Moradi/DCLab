# DCLab Experiment — Auto-train: upload.csv

**Task:** open_ingest_638da274dbfb
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
- Validation score: 0.7908041762746371

## Test metrics (held out)

- accuracy: 0.7
- roc_auc: 0.8245614035087719
- pr_auc: 0.8150571457620627
- precision: 0.8
- recall: 0.5714285714285714
- f1: 0.6666666666666666
- log_loss: 0.5460052708496071
- balanced_accuracy: 0.706766917293233
- brier: 0.18535551256710486
- brier_score: 0.18535551256710486
- calibration_gap: 0.1392732945137652
- confusion_matrix: {'tn': 16, 'fp': 3, 'fn': 9, 'tp': 12}
- top_decile_lift: 1.4285714285714286
- top_k_precision: 0.75
- positive_rate: 0.525

## Comparison (validation)

- baseline logistic_regression / features: 0.7908041762746371
- best single: logistic_regression (0.7908041762746371)

## Feature group usefulness


## Combinations


## Warnings

- person_id: NONE (identifier_not_a_predictor)
