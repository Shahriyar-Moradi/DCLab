# DCLab Experiment — Auto-train: business-plane.csv

**Task:** open_ingest_c6041834d3c4
**Type:** binary
**Horizon (days):** None
**Rows:** 110
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
- Validation score: 0.9760972977639645

## Test metrics (held out)

- accuracy: 0.8636363636363636
- roc_auc: 0.9256198347107437
- pr_auc: 0.9247880906971817
- precision: 0.8333333333333334
- recall: 0.9090909090909091
- f1: 0.8695652173913043
- log_loss: 0.3295787809405889
- balanced_accuracy: 0.8636363636363636
- brier: 0.10852223549821552
- brier_score: 0.10852223549821552
- calibration_gap: 0.22775289000424692
- confusion_matrix: {'tn': 9, 'fp': 2, 'fn': 1, 'tp': 10}
- top_decile_lift: 2.0
- top_k_precision: 1.0
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.9760972977639645
- best single: logistic_regression (0.9760972977639645)

## Feature group usefulness


## Combinations

