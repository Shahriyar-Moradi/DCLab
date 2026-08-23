# First ML Experiment

## Objective

Prove that automated feature/model exploration can produce a robust purchase-probability layer quickly and transparently.

## Dataset contract

One row per entity at a defined prediction timestamp.

Columns should include:
- entity_id
- prediction_timestamp
- historical features only
- target: purchase_within_30d

## Split

Example:
- train: months 1–6
- validation: month 7
- test: month 8

Do not randomly mix future rows into training when that would not match production.

## Candidate search

Generate combinations of feature groups.

For each feature set:
- preprocessing
- logistic regression
- random forest
- LightGBM
- XGBoost

Add hyperparameter variants within a compute budget.

Example target:
300 candidate runs.

## Evaluation

Primary metric: PR-AUC when purchase is imbalanced.

Also calculate:
- ROC-AUC
- precision
- recall
- F1
- calibration/Brier score
- subgroup metrics
- temporal validation

## Selection

Keep only candidates that:
- pass leakage checks
- pass minimum quality threshold
- pass stability checks
- are not near-duplicate predictions

Select approximately 20–50 if the data supports it.

## Ensemble

Use OOF predictions.

Compare:
- best single model
- simple average
- weighted blend
- stacking

## Report

The experiment report must show:

1. Time spent
2. Candidate count
3. Valid candidate count
4. Selected count
5. Best single model
6. Ensemble metrics
7. Calibration
8. Diversity/correlation
9. Feature groups used
10. Top signals
11. Data quality
12. Leakage checks
13. Compute cost
14. Reproducibility metadata

## Success condition

The ensemble does not need to win every dataset.

The system succeeds if it reliably finds strong solutions with dramatically less manual experimentation, while remaining honest about uncertainty and preserving reproducibility.
