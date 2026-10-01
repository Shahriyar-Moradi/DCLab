---
paths:
  - "apps/api/app/engine/**"
  - "apps/api/app/services/auto_train_service.py"
  - "apps/api/app/services/*experiment*"
  - "apps/api/app/ml/**"
---

# ML engine rules

- There must be exactly ONE training path: the open-ingest runner. Do not add
  features to the legacy `runner.run_experiment` branch, `engine/experiments/factory.py`
  or `app/ml/*`; route callers to the open-ingest path instead.
- Every fit-dependent step (impute, scale, encode, select, target-encode) lives
  inside an sklearn `Pipeline` fit per CV fold. Never call `fit`/`factorize`/
  `get_dummies`/`detect_leakage` on the full frame before the split.
- Model selection uses CV/validation evidence only. The final holdout is scored
  exactly once, for the locked winner, after `ModelSelectionDecision` is persisted.
- Every candidate portfolio includes a dummy baseline; report whether the winner beats it.
- Never default the target to the last column or the entity to the first column.
- No dataset-specific names (churn, TotalCharges, tenure…) in core logic.
- Add/extend a test in `apps/api/tests/test_ml_automation_e2e.py` (or the E2E lab suite)
  proving holdout isolation for any change to splitting, preprocessing or selection.
