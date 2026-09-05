# DCLab Experiment — Auto-train: scientific_lineage.csv

**Task:** open_ingest_3e658b612071
**Type:** binary
**Horizon (days):** None
**Rows:** 200
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** CRITICAL
**Split:** stratified_random
**Fusion:** None

## Best single model

- Family: random_forest
- Groups: features
- Validation score: 0.5282192776940875

## Test metrics (held out)

- accuracy: 0.55
- roc_auc: 0.57875
- pr_auc: 0.6312993435970295
- precision: 0.55
- recall: 0.55
- f1: 0.55
- log_loss: 0.6888186102401199
- balanced_accuracy: 0.55
- brier: 0.24828125
- brier_score: 0.24828125
- calibration_gap: 0.2508705357142857
- confusion_matrix: {'tn': 11, 'fp': 9, 'fn': 9, 'tp': 11}
- top_decile_lift: 1.5
- top_k_precision: 0.75
- positive_rate: 0.5

## Comparison (validation)

- baseline logistic_regression / features: 0.4615796080394384
- best single: random_forest (0.5282192776940875)

## Feature group usefulness


## Combinations


## Warnings

- post_outcome_feature: CRITICAL (direct_target_duplicate)
- target_proxy: HIGH (target_proxy, suspicious_name, strong_single_feature_score)
