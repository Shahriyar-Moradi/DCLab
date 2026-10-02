# Phase 1 — One correct ML engine

### P1.1-A — Route the admin Lab through the open-ingest runner
Model: Opus 5.5 (xhigh) · Size: L (split if > budget) · Depends on: P0.3-A · Review: ml-correctness-reviewer
Goal: every training request uses `_run_open_ingest_experiment`.
Read: `services/lab_training_service.py:120-230`, `services/lab_service.py:318-420`, `engine/experiments/runner.py:979-1256`, `services/auto_train_service.py`, `api/lab.py:150-420`, `cli/main.py:137`.
Do: make admin train endpoints (`/admin/datasets/{id}/train`, `/use-cases/{slug}/train`, `/admin/experiments/{id}/run`) and `dclab experiment run` create an `ExecutionRequest` + `labs.auto_train` job with explicit target (use-case target becomes an explicit target hint, not alias inference); return 202 + run id; keep response shapes the admin UI needs (adapt `apps/web/lib/application/hooks.ts:421-520` if necessary).
Don't: delete legacy code yet; change open-ingest science.
Verify: `test_lab_training.py`, `engine/test_lab_api.py`, `test_auto_train_service.py`, `test_ml_automation_e2e.py`, admin Playwright spec `whole-system.spec.ts`.
Done when: `grep -n 'strategy="use_case"'` has no production callers; admin Lab runs appear as normal open-ingest runs.

### P1.2-A — Move ensemble/selection into engine; delete legacy training
> **Executed scope (2026-10-01):** `app/ml/*` and the factory power the frozen Decision.ai
> vertical (`/app/decisions/generate`, simulations, CI `seed_conversion_model.py`), whose
> URLs are kept until 2027-03-31 (P0.2-A). So `app/ml` is **frozen, not deleted**:
> `factory.py` moved from `engine/experiments/` into `app/ml/`, `engine/` owns ensemble and
> selection (`app/ml` re-exports them), and `engine/` has no `app.ml` imports. The legacy
> runner branch is deleted; legacy strategies are normalized onto open-ingest using the
> task's declared feature groups. Deleting `app/ml` moves to the Phase 9 decision-layer rework.
Model: Opus 5.5 (high) · Size: L · Depends on: P1.1-A · Review: ml-correctness-reviewer
Do: move `app/ml/ensemble.py`, `app/ml/selection.py` into `engine/ensemble/`, `engine/selection/` (real modules, not re-exports); delete runner legacy branch (`runner.py:1258-1703`) and its dead open-ingest arms; delete `engine/experiments/factory.py`, `app/ml/train.py`; replace `ml/predict.py` usage in `generate_service.py` and `sim/*` with a frozen stub that returns a clear "legacy simulation disabled" error behind flag `legacy_decision_layer_enabled` (default true only for existing tests that need fixtures — otherwise convert those tests to assert the disabled response). Update string references in `model_build_codegen.py:773` and `domain/reproducibility.py:21`.
Verify: full backend suite; `grep -rn "app.ml\b\|factory" apps/api/app` clean.
Done when: one training path remains and the full suite is green.

### P1.3-A — Run artifacts through ObjectStorage
Model: Opus 5.5 (high) · Size: M · Depends on: P1.2-A · Review: db-migration-reviewer
Read: `engine/serving/artifacts.py`, runner write sites (`runner.py:947-952, 1210-1213`), `auto_train_service.py:982-985, 1481`, `api/lab.py:156-161`, `reproducibility_service.py:144-352`, `technical_run_report.py:41`, `storage/factory.py`.
Do: engine writes to a temp workspace dir; the service uploads outputs as `artifacts` rows with tenant-prefixed keys and digests; add `experiments.artifact_prefix` (expand migration; keep `artifact_dir` for old rows); readers materialize via storage. Prepared CSVs and admin uploads use the dataset materialization path.
Verify: run with `STORAGE_PROVIDER=local` pointing outside repo; reproducibility + report tests; grep for `REPO_ROOT / "artifacts"` and `"data"` in non-legacy code is empty.

### P1.4-A — Baseline, imbalance and multiclass
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P1.2-A · Review: ml-correctness-reviewer
Do: add `DummyClassifier(strategy="prior")`/`DummyRegressor(median)` candidate always evaluated and reported (never selectable as winner unless all others fail); `class_weight="balanced"` / `scale_pos_weight` variants for imbalanced binary; support multiclass end to end (`runner.py:356, 610` `proba[:,1]` assumptions, metrics: macro-F1, log-loss, balanced accuracy; metric planner); fix `metric_planner.py:66-84` so balanced binary can use ROC-AUC when spec says so; `OneHotEncoder(handle_unknown="infrequent_if_exist" or drop=None)`; honor `max_training_seconds`.
Verify: new engine tests per behavior; E2E multiclass dataset (iris-like synthetic); `test_ml_automation_e2e.py`.

### P1.4-B — Objective, constraints and validation-tuned threshold
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P1.4-A · Review: ml-correctness-reviewer
Do: extend ProblemSpec contract with `objective` (primary metric override + reason, constraints like `precision>=0.70`, optional cost matrix); threshold chosen on out-of-fold predictions to satisfy constraints; persist threshold + constraint status as evaluation metrics; holdout reports at the locked threshold only.
Verify: tests proving threshold never sees holdout; constraint-unsatisfiable case reports clearly.

### P1.4-C — CatBoost and bounded tuning
Model: Opus 5.5 (high) · Size: M · Depends on: P1.4-A · Review: ml-correctness-reviewer
Do: add CatBoost family when installed; add Optuna (new dependency, bounded) inner-CV tuning on train folds only with a per-family trial budget and global time budget; candidate fingerprint includes search space + seed.
Verify: determinism test (same seed → same winner); time budget test; benchmark subset.

### P1.5-A — Split `run_auto_train_job` into typed stages
Model: Opus 5.5 (high) · Size: L · Depends on: P1.3-A · Review: ml-correctness-reviewer
Do: extract stages (load/profile, target, clean, holdout, plans, roles, train, persist, finalize) into functions with Pydantic inputs/outputs in `services/auto_train/`; keep event emission identical; no behavior change.
Verify: full auto-train + E2E suite; event sequence snapshot test unchanged.

### P1.6-A — Finish-line E2E suite
Model: Opus 5.5 (high) · Size: M · Depends on: P1.4-B · Review: ml-correctness-reviewer
Do: create `apps/api/tests/test_e2e_lab_run.py` with the 8 cases from `DCLAB_MASTER_IMPLEMENTATION_SPEC.md` §27 (lifecycle, missing values + fit-on-train, unseen category, regression KFold, invalid dataset fails cleanly, mid-run refresh, completed refresh without retrain, holdout integrity) plus datasets A–E from §28 (unfamiliar classification/regression/fraud, arbitrary target name with explicit target, integer category code). No mocks of the engine.
Verify: the new file passes in < 5 min on CI.

### R1-A — Benchmark harness
Model: Opus 5.5 (high) · Size: M · Depends on: P1.4-A · Review: ml-correctness-reviewer
Do: `benchmarks/harness/` that downloads a pinned list of 20–30 OpenML tasks (binary, multiclass, regression; small/medium), runs the engine directly (no DB) with fixed seeds, writes `benchmarks/results/<date>.json` (CV/holdout metrics, dummy margin, time); `make benchmark`; nightly GitHub workflow; regression check vs last accepted baseline (tolerance per task).
Verify: harness run on 5 tasks locally.
Done when: baseline committed; nightly job configured.
