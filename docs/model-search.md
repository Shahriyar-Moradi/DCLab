# Model search

Always-on sklearn families: logistic / linear, ridge/lasso/elasticnet, RF, ExtraTrees, gradient boosting, plus dummy baselines.

The production Labs open-ingest portfolio also requires XGBoost, LightGBM, and CatBoost. Install the same extra used by CI:

```bash
pip install -e ".[boosting]"
```

Those libraries still register only if import succeeds (a broken local build should not crash candidate generation), but a clean environment that omits `.[boosting]` is incomplete: open-ingest will only emit logistic regression and random forest, and tests that require XGBoost will fail.

Progressive stages: cheap baselines on single groups → stronger families on more combinations. Candidate fingerprints skip identical retrains when a cache is used.

Deep learning is not default and is not claimed to beat trees on tabular data.
