# DCLab Experiment — Auto-train: telco.csv

**Task:** open_ingest_43c4a4db0527
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
- Validation score: 0.4854066385340675

## Test metrics (held out)

- accuracy: 0.7
- roc_auc: 0.7799999999999999
- pr_auc: 0.5067269331975215
- precision: 0.4375
- recall: 0.7
- f1: 0.5384615384615384
- log_loss: 0.4570875213590691
- balanced_accuracy: 0.7
- brier: 0.14845673038607504
- brier_score: 0.14845673038607504
- calibration_gap: 0.06588762037326666
- confusion_matrix: {'tn': 21, 'fp': 9, 'fn': 3, 'tp': 7}
- top_decile_lift: 2.0
- top_k_precision: 0.5
- positive_rate: 0.25

## Comparison (validation)

- baseline logistic_regression / features: 0.4854066385340675
- best single: logistic_regression (0.4854066385340675)

## Feature group usefulness


## Combinations

