# Phase 7 — Release, batch prediction, monitoring and Phase 8 — Hosted beta

Every backend prompt here ships its screen in the same phase; see `../UI_COVERAGE.md`. Assistant tools: A7-A, A8-A in `ASSISTANT.md`.

## Phase 7

### P7.1-A — Safe model package and feature contract
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P2.4-B · Review: security-reviewer
Read: `docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md` §6–7.
Do: package = skops (sklearn) / native (XGBoost JSON, LightGBM text, CatBoost cbm) + manifest (feature contract, dependency lock digest, metrics); Pandera schema compiled from DCLab feature contract (strict, no coercion); joblib/pickle artifacts marked quarantined; loading only in worker.

### P7.2-A — Model release and batch prediction
Model: Opus 5.5 (high) · Size: L · Depends on: P7.1-A · Review: db-migration-reviewer
Do: `model_releases` (immutable; champion ref per project); reuse `batch_predictions` and job `models.batch_predict` from P4.9-A, now filling `model_release_id` and requiring a release for scheduled scoring; `/v1` + MCP tools via the shared catalog; Studio model page actions (Create release, Score with release).

### P7.3-A — MLflow mirror (optional)
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P7.2-A
Do: `TrackingPort` with no-op default and MLflow adapter (separate DB/schema, private), idempotent reconciliation; `tracking_degraded` blocks promotion only when MLflow is configured as required; model page shows the MLflow link and a `tracking_degraded` badge.

### P7.4-A — Monitoring windows and drift
Model: Opus 5.5 (high) · Size: M · Depends on: P7.2-A
Do: `monitoring_windows` (release, window, input/prediction drift metrics, performance when labels arrive); Evidently as calculator only; thresholds owned by DCLab; drift → investigation finding (reuse P5.1).

### P7.5-A — Rollback and model UI
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P7.4-A
Do: champion rollback via ref change + decision record; Studio models tab: releases, batch runs, drift charts per monitoring window with the drift finding in plain words, Rollback button with confirm.
Verify: Playwright: release → score → drift window visible → rollback → decision recorded.

## Phase 8

### P8.1-A — One-cloud deployment
Model: Opus 5.5 (high) · Size: L · Depends on: Phase 7 gate · Review: security-reviewer
Do: choose GCP or AWS (ADR 0010, founder decision based on first design partner); OpenTofu for managed Postgres, object storage, secret manager, container services for api/web/worker, private networking, migrations job, CI deploy with environment approval. Reuse `docs/agentic-program/AWS_GCP_DEPLOYMENT_ARCHITECTURE.md` mappings for the chosen provider only.

### P8.2-A — Security and recovery gate
Model: **Fable 5.1** (high) review + Opus 5.5 fixes · Size: M · Depends on: P8.1-A
Do: adversarial tenancy suite against staging, secret scanning, CSP/headers check, rate limits, backup + point-in-time restore drill, incident runbooks.

### P8.3-A — Compute placement
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A
Do: `ComputePort` with `home` (worker pool) and one external target via SkyPilot for heavy training jobs; job payload references artifacts by id; cost recorded as micro-units; no customer data leaves the home cloud unless workspace policy allows; cost column on the Experiments tab and in compare.

### P8.4-A — Metering, quotas, plans
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A
Do: `usage_records` (compute seconds, storage bytes, LLM tokens/cost, agent runs) from existing ledgers; enforce `workspace_entitlements` at command admission; Studio usage page (compute, storage, LLM and assistant spend, quota bars).

### P8.5-A — Observability
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P8.1-A
Do: OpenTelemetry traces/metrics (low-cardinality labels), structured logs, alerts (job failures, queue age, error rate), runbooks under `docs/runbooks/`.

### P8.5-UI — System health page
Model: Sonnet 5.5 (low) · Size: S · Depends on: P8.5-A
Do: `/admin/monitoring` shows queue depth and age, worker heartbeats, failed jobs with links, error rate; admin role only.

### P8.6-A — Open-source readiness
Model: Opus 5.5 (high) · Size: M · Depends on: P8.1-A
Do: ensure `app/engine` has no imports from services/db/api (import-linter rule), publishable packages `dclab-engine`, `dclab-client`, `dclab-mcp` with own pyproject; license choice ADR; plan for a clean public repo (fresh history, no customer/business docs).

### P8.7-A — Data safety (old S0-P05C–E)
Model: Opus 5.5 (xhigh) · Size: L (split) · Depends on: P8.1-A · Review: security-reviewer, db-migration-reviewer
Do: content/malware scanner and classifier adapters, resumable quarantine worker, retention and deletion jobs, residency enforcement (ADR 0005); operator quarantine-review page (release / reject with reason, audit trail).
Verify: adversarial upload tests; deletion job tests; Playwright review flow.
