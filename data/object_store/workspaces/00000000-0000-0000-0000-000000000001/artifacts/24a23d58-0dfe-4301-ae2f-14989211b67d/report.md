# DCLab Experiment — Auto-train: missing.csv

**Task:** open_ingest_199dd6c041d4
**Type:** binary
**Horizon (days):** None
**Rows:** 220
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
- Validation score: 0.5443943001084804

## Test metrics (held out)

- accuracy: 0.7727272727272727
- roc_auc: 0.780952380952381
- pr_auc: 0.669479375597889
- precision: 0.6666666666666666
- recall: 0.5714285714285714
- f1: 0.6153846153846154
- log_loss: 0.5054155729060864
- balanced_accuracy: 0.719047619047619
- brier: 0.16456994688004412
- brier_score: 0.16456994688004412
- calibration_gap: 0.1471626199617971
- confusion_matrix: {'tn': 26, 'fp': 4, 'fn': 6, 'tp': 8}
- top_decile_lift: 2.357142857142857
- top_k_precision: 0.75
- positive_rate: 0.3181818181818182

## Comparison (validation)

- baseline logistic_regression / features: 0.5443943001084804
- best single: logistic_regression (0.5443943001084804)

## Feature group usefulness


## Combinations

