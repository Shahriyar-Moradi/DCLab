# Phase 7 — Release, batch prediction, monitoring and agentic MLOps, and Phase 8 — Hosted beta

Every backend prompt here ships its screen in the same phase; see `../UI_COVERAGE.md`. Screens follow `../design/prototype/` under the sync rule of `../design/STUDIO_DESIGN.md`. Assistant tools: A7-A, A8-A in `ASSISTANT.md`.

## Phase 7

### P7.1-A — Safe model package and feature contract
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P2.4-B · Review: security-reviewer
Read: `docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md` §6–7.
Do: package = skops (sklearn) / native (XGBoost JSON, LightGBM text, CatBoost cbm) + manifest (feature contract, dependency lock digest, metrics); Pandera schema compiled from DCLab feature contract (strict, no coercion), including each feature's as-of availability from P5.0-A; joblib/pickle artifacts marked quarantined; loading only in worker.

### P7.2-A — Model release and batch prediction
Model: Opus 5.5 (high) · Size: L · Depends on: P7.1-A · Review: db-migration-reviewer
Do: `model_releases` (immutable; champion ref per project); reuse `batch_predictions` and job `models.batch_predict` from P4.9-A, now filling `model_release_id` and requiring a release for scheduled scoring; `/v1` + MCP tools via the shared catalog; Studio model page actions (Create release, Score with release).

### P7.3-A — MLflow mirror (optional)
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P7.2-A
Do: `TrackingPort` with no-op default and MLflow adapter (separate DB/schema, private), idempotent reconciliation; `tracking_degraded` blocks promotion only when MLflow is configured as required; model page shows the MLflow link and a `tracking_degraded` badge.

### P7.4-A — Monitoring windows and drift
Model: Opus 5.5 (high) · Size: M · Depends on: P7.2-A
Do: `monitoring_windows` (release, window, input/prediction drift metrics, performance when labels arrive); label arrival path: upload a dataset version with purpose `labels` joined on entity + prediction time (no connector needed); Evidently as calculator only; thresholds owned by DCLab; drift → investigation finding (reuse P5.1); with AI off this deterministic finding is the whole response.

### P7.5-A — Rollback and model UI
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P7.4-A
Design: `prototype/models.html` tabs Releases · Batch predictions · Feature contract · Package & integrity; `prototype/monitoring.html` (PSI per window, performance when labels arrive, windows table, thresholds, "With AI off"; the Ops-agent chain only if ADR 0008 allows it).
Do: champion rollback via ref change + decision record; Studio models tab: releases, batch runs, drift charts per monitoring window with the drift finding in plain words, Rollback button with confirm.
Verify: Playwright: release → score → drift window visible → rollback → decision recorded.

### P7.6-A — Business Outcomes view
Model: Sonnet 5.5 (high) · Size: M · Depends on: P7.5-A, P4.11-A, A4-A
Design: `prototype/client.html` (outcome stats in plain words, "How the model was built", this week's list, questions for you, trust and limits, plain-language Q&A).
Do: `/outcomes` for the client role from the champion release, latest batch prediction and model card; questions are proposals of kind `question` addressed to the client; Q&A uses the assistant with a read-only client tool set (stored predictions and card only, no experiment internals); banned-terms scanner covers the page; business boundary banner kept.
Verify: banned-terms scan; Playwright client journey; client role cannot reach developer routes.

### P7.7-A — Per-row prediction reasons
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P7.2-A · Review: ml-correctness-reviewer
Do: deterministic explainer in the engine, run in the worker during batch scoring (tree SHAP for tree families, coefficients × values for linear, permutation fallback), top-k reasons per row stored with the predictions; reasons expressed in original feature names; shown in the Outcomes list and the Models → Batch predictions table.
Verify: reasons reproducible for a fixed seed; additivity check where the method guarantees it.

### P7.8-A — Ops agent (agentic MLOps)
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P7.5-A, P6.3-B, P6.8-A · Review: ml-correctness-reviewer, security-reviewer
Do: `OpsAgent` on the lead-agent runtime, triggered by monitoring windows: monitor (status line per window) → diagnose (DatasetInvestigator on the drifted window, cited statistics) → retrain (branch the champion's experiment on the new DatasetVersion, same split rules) → review (Critic: new vs champion on the same evaluation, per segment) → release proposal → rollback when a release underperforms on labeled windows. Each step is a decision point with a workspace trust level (defaults: monitor/diagnose/review L3, retrain L2, release L1, rollback L3 with a threshold); `workspace_autonomy_policy` (max retrains per week, auto-release off by default, rollback threshold, LLM budget); every action is a decision record; Studio inbox shows the chain with Approve release / See comparison / Ignore.
Verify: end-to-end test with a synthetic drift dataset and fake LLM: drift → diagnosis → retrain → proposal → approval → release; rollback test; with AI off the drift finding still appears and nothing else acts.

## Phase 8

### P8.1-A — One-cloud deployment
Model: Opus 5.5 (high) · Size: L · Depends on: Phase 7 gate · Review: security-reviewer
Do: choose GCP or AWS (ADR 0010, founder decision based on first design partner); OpenTofu for managed Postgres, object storage, secret manager, container services for api/web/worker, private networking, migrations job, CI deploy with environment approval. Reuse `docs/agentic-program/AWS_GCP_DEPLOYMENT_ARCHITECTURE.md` mappings for the chosen provider only.

### P8.2-A — Security and recovery gate
Model: **Fable 5.1** (high) review + Opus 5.5 fixes · Size: M · Depends on: P8.1-A
Do: adversarial tenancy suite against staging, secret scanning, CSP/headers check, rate limits, backup + point-in-time restore drill, incident runbooks, review of agent/LLM data exposure and lead-agent tool permissions.

### P8.3-A — Compute placement
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A
Do: `ComputePort` with `home` (worker pool) and one external target via SkyPilot for heavy training jobs; job payload references artifacts by id; cost recorded as micro-units; no customer data leaves the home cloud unless workspace policy allows; cost column on the Experiments tab and in compare.

### P8.4-A — Metering, quotas, plans
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A
Do: `usage_records` (compute seconds, storage bytes, LLM tokens/cost, agent and assistant runs) from existing ledgers, reusing the P6.2 workspace LLM budgets; enforce `workspace_entitlements` at command admission; Studio usage page (compute, storage, LLM and assistant spend, quota bars).

### P8.5-A — Observability
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P8.1-A
Do: OpenTelemetry traces/metrics (low-cardinality labels), structured logs, alerts (job failures, queue age, error rate), runbooks under `docs/runbooks/`.

### P8.5-UI — Operator console
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P8.5-A
Design: `prototype/operator.html` tabs Workspaces · Jobs & workers · Quarantine · Platform AI caps · Benchmarks & gates.
Do: `/operator` (operator role only; replaces `/admin/monitoring`): workspaces with quotas and usage, queue depth and age, worker heartbeats, failed jobs, quarantine metadata (P8.7), platform AI caps (read-only, code-owned), R1/R2/R3 results; never shows customer rows.

### P8.6-A — Open-source readiness
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A
Do: ensure `app/engine` has no imports from services/db/api (import-linter rule), publishable packages `dclab-engine`, `dclab-client`, `dclab-mcp` with own pyproject; license choice ADR; plan for a clean public repo (fresh history, no customer/business docs).

### P8.7-A — Data safety (old S0-P05C–E)
Model: Opus 5.5 (xhigh) · Size: L (split) · Depends on: P8.1-A · Review: security-reviewer, db-migration-reviewer
Do: content/malware scanner and classifier adapters, resumable quarantine worker, retention and deletion jobs, residency enforcement (ADR 0005); operator quarantine-review page (release / reject with reason, audit trail).
Verify: adversarial upload tests; deletion job tests; Playwright review flow.

### P8.8-A — Notifications and webhooks
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A, P4.16-A · Review: security-reviewer
Design: `prototype/settings.html` Notifications and Integrations → Webhooks.
Do: email notifications for inbox items, run completion and drift findings (per-user preferences); signed outbound webhooks (`run.completed`, `proposal.created`, `drift.alert`) with retries and a delivery log; settings tabs to manage both.
Verify: signature and retry tests; no tenant data in webhook bodies beyond ids and summaries.
