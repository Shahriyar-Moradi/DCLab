# DCLab Experiment — Auto-train: datetime.csv

**Task:** open_ingest_68babcafb5ac
**Type:** binary
**Horizon (days):** None
**Rows:** 120
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** LOW
**Split:** temporal_future
**Fusion:** None

## Best single model

- Family: logistic_regression
- Groups: features
- Validation score: 0.6263625263625263

## Test metrics (held out)

- accuracy: 0.5
- roc_auc: 0.5416666666666666
- pr_auc: 0.592681368277007
- precision: 0.5
- recall: 1.0
- f1: 0.6666666666666666
- log_loss: 0.6935702873640341
- balanced_accuracy: 0.5
- brier: 0.2502115004162531
- brier_score: 0.2502115004162531
- calibration_gap: 0.01912813023949922
- confusion_matrix: {'tn': 0, 'fp': 12, 'fn': 0, 'tp': 12}
- top_decile_lift: 1.0
- top_k_precision: 0.5
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.6263625263625263
- best single: logistic_regression (0.6263625263625263)

## Feature group usefulness


## Combinations

