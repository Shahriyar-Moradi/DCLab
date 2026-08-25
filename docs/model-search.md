# Model search

Always-on sklearn families: logistic / linear, ridge/lasso/elasticnet, RF, ExtraTrees, gradient boosting, plus dummy baselines.

Optional extra `pip install -e '.[boosting]'` registers XGBoost, LightGBM, CatBoost **if import succeeds**. Missing libraries are skipped, not crashed.

Progressive stages: cheap baselines on single groups → stronger families on more combinations. Candidate fingerprints skip identical retrains when a cache is used.

Deep learning is not default and is not claimed to beat trees on tabular data.
