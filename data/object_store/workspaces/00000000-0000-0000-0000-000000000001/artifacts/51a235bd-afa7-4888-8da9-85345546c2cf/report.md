# DCLab Experiment — Auto-train: explorer.csv

**Task:** open_ingest_56ed8d65c353
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
- Validation score: 0.5976410999228386

## Test metrics (held out)

- accuracy: 0.45
- roc_auc: 0.5025
- pr_auc: 0.5314878719065376
- precision: 0.45454545454545453
- recall: 0.5
- f1: 0.47619047619047616
- log_loss: 1.1830129791674904
- balanced_accuracy: 0.45
- brier: 0.3801245039963736
- brier_score: 0.3801245039963736
- calibration_gap: 0.3562661302058647
- confusion_matrix: {'tn': 8, 'fp': 12, 'fn': 10, 'tp': 10}
- top_decile_lift: 1.5
- top_k_precision: 0.75
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.5334243429487975
- best single: xgboost (0.5976410999228386)

## Feature group usefulness


## Combinations

