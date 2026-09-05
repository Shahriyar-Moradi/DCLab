# DCLab Experiment — Auto-train: classification.csv

**Task:** open_ingest_53ef186b7f2d
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
- Validation score: 0.8885145609655414

## Test metrics (held out)

- accuracy: 0.8181818181818182
- roc_auc: 0.9504132231404958
- pr_auc: 0.9543335452426362
- precision: 0.8181818181818182
- recall: 0.8181818181818182
- f1: 0.8181818181818182
- log_loss: 0.333727863509917
- balanced_accuracy: 0.8181818181818182
- brier: 0.1050000756555825
- brier_score: 0.1050000756555825
- calibration_gap: 0.1761369199473464
- confusion_matrix: {'tn': 9, 'fp': 2, 'fn': 2, 'tp': 9}
- top_decile_lift: 2.0
- top_k_precision: 1.0
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.8885145609655414
- best single: logistic_regression (0.8885145609655414)

## Feature group usefulness


## Combinations

