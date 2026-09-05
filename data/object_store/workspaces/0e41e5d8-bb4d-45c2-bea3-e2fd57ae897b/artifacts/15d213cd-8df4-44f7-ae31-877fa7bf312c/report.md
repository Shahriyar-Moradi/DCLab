# DCLab Experiment — Auto-train: science_regression.csv

**Task:** open_ingest_3f08275eafc1
**Type:** regression
**Horizon (days):** None
**Rows:** 180
**Candidates generated:** 4
**Trained:** 4
**Failed:** 0
**Robust:** 4
**Diverse selected:** 1
**Leakage risk:** MEDIUM
**Split:** random
**Fusion:** None

## Best single model

- Family: linear_regression
- Groups: features
- Validation score: -30.572640302547473

## Test metrics (held out)

- mae: 34.96181955421652
- mse: 1592.9617359296922
- rmse: 39.91192473346396
- r2: -0.004754005286049079
- mape: 20.750429206432266
- smape: 20.09185210493208
- median_absolute_error: 37.92263864118934

## Comparison (validation)

- baseline linear_regression / features: -30.572640302547473
- best single: linear_regression (-30.572640302547473)

## Feature group usefulness


## Combinations


## Warnings

- tenure: MEDIUM (high_correlation_alone)
