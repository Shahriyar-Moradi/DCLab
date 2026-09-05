# DCLab Experiment — Auto-train: telco.csv

**Task:** open_ingest_dd16a41e94ae
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
- Validation score: 0.6266959014115876

## Test metrics (held out)

- accuracy: 0.775
- roc_auc: 0.7232142857142857
- pr_auc: 0.5769349468863199
- precision: 0.6363636363636364
- recall: 0.5833333333333334
- f1: 0.6086956521739131
- log_loss: 0.5365970239835761
- balanced_accuracy: 0.7202380952380952
- brier: 0.17266523327404829
- brier_score: 0.17266523327404829
- calibration_gap: 0.09694681936493486
- confusion_matrix: {'tn': 24, 'fp': 4, 'fn': 5, 'tp': 7}
- top_decile_lift: 1.6666666666666667
- top_k_precision: 0.5
- positive_rate: 0.3

## Comparison (validation)

- baseline logistic_regression / features: 0.6266959014115876
- best single: logistic_regression (0.6266959014115876)

## Feature group usefulness


## Combinations

