# DCLab Experiment — Auto-train: regression.csv

**Task:** open_ingest_78d377efb87f
**Type:** regression
**Horizon (days):** None
**Rows:** 110
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** MEDIUM
**Split:** random
**Fusion:** None

## Best single model

- Family: lightgbm_regressor
- Groups: features
- Validation score: -15.846804452556745

## Test metrics (held out)

- mae: 13.673981579328832
- mse: 267.1401527665928
- rmse: 16.344422680737082
- r2: -0.006024740651962235
- mape: 102.82376836459079
- smape: 165.47144273358361
- median_absolute_error: 12.56234153587026

## Comparison (validation)

- baseline linear_regression / features: -16.015691897054595
- best single: lightgbm_regressor (-15.846804452556745)

## Feature group usefulness


## Combinations


## Warnings

- feature: MEDIUM (high_correlation_alone)
