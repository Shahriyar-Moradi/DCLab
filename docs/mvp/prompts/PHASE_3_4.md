# Phase 3 — Agent interface (/v1, SDK, CLI, MCP) and Phase 4 — Studio

## Phase 3

### P3.1-A — `/v1` contract conventions
Model: Opus 5.5 (high) · Size: M · Depends on: P2.3-A · Review: security-reviewer
Do: error envelope `{error:{code,message,retryable,request_id,details}}`, opaque cursors, ETag/If-Match on mutable resources, Idempotency-Key on all POST commands (reuse `execution_requests` digest binding); apply to existing 13 `/v1` operations without breaking the SDK (version the client).
Verify: contract tests; `contracts/v1_openapi.json` regenerated.

### P3.1-B — `/v1` resources for the full loop
Model: Opus 5.5 (high) · Size: L (split by resource) · Depends on: P3.1-A
Do: `POST /v1/projects`, `POST /v1/projects/{id}/problem-specs`, `POST /v1/datasets` (multipart upload → ingestion), `GET /v1/experiments` (+filters), `GET /v1/experiments/{id}`, `POST /v1/experiments` (root run), `POST /v1/experiments/{id}/branches`, `GET /v1/experiments/compare?ids=`, `POST /v1/experiments/{id}/cancel`, `GET/POST /v1/projects/{id}/decisions`, `GET /v1/model-versions/{id}`.
Verify: per-route tests incl. tenant isolation and idempotent replay.

### P3.2-A — Service tokens
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P3.1-A · Review: security-reviewer, db-migration-reviewer
Do: `service_tokens` table (hashed secret, workspace, capability scopes, expiry, last_used, revoked_at); create/list/revoke in Studio settings and `/v1`; bearer resolution in `api/deps.py`; tokens can never use cookie/CSRF paths; audit events.
Verify: adversarial tests (wrong workspace, revoked, expired, scope escalation).

### P3.3-A — SDK and CLI
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P3.1-B, P3.2-A
Do: extend `packages/dclab_client` for all new resources (sync; async later); new `dclab` customer CLI entry (separate from internal `app.cli`) with `login/projects/data upload/experiments run|branch|compare|code/decisions` and `--json`; exit codes documented.
Verify: SDK tests with httpx mock transport; CLI smoke tests against test server.

### P3.4-A — MCP server
Model: Opus 5.5 (high) · Size: M · Depends on: P3.3-A · Review: security-reviewer
Do: `packages/dclab_mcp` (official MCP Python SDK, pinned) stdio server using the SDK and a service token. Tools: `inspect_project`, `inspect_dataset`, `propose_problem_spec`, `create_problem_spec`, `run_experiment`, `get_experiment`, `compare_experiments`, `branch_experiment`, `get_experiment_code`, `get_evidence`, `list_decisions`, `accept_proposal`/`record_decision`, `get_model`. Read tools default on; write tools behind `DCLAB_MCP_WRITE_ENABLED`; tool outputs bounded and summarized (no raw rows).
Verify: MCP protocol tests (list tools, call tools against a test server); token scope enforcement.

### P3.5-A — Dogfood with Claude Code
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P3.4-A
Do: `.mcp.json` example (not secrets), `docs/QUICKSTART_MCP.md`, three golden transcripts (classification, regression, branch-and-compare) recorded as tests using a deterministic client script.
Done when: founder completes the Phase 3 exit flow from Claude Code.

## Phase 4

### P4.1-A — Project-centric information architecture
Model: Opus 5.5 (high) · Size: M · Depends on: P3.1-B
Do: new routes `/projects`, `/projects/[id]` with tabs Graph · Data · Experiments · Models · Decisions; navigation in `components/layout/app-navigation.ts`; Decision.ai vertical pages (`/app/opportunities|decisions|insights|dashboards`, home opportunity data) hidden unless `legacy_decision_layer_enabled`; `/lab/runs/[run_id]` redirects into the project experiment page.
Verify: tsc/lint/build; Playwright navigation spec.

### P4.2-A — Graph view
Model: Opus 5.5 (high) · Size: M · Depends on: P4.1-A, P2.3-A
Do: graph rendering of `GET /v1/projects/{id}/graph` (choose one small, reviewed lib, e.g. React Flow, or SVG) with node status, stale markers, ref badges, click → inspector drawer; accessible list fallback.

### P4.3-A — Node inspectors with reason and code
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P4.2-A
Do: inspectors for DatasetVersion (profile, findings), SplitPlan, FeatureRecipe (each transform: reason, formula, importance, code snippet), Experiment (config, change set, metrics, folds), ModelVersion; reuse `components/admin/{Evaluation,FinalModel,ModelComparison,ProcessingSummary}` and `components/model-build/*`.

### P4.4-A — Compare, branch and decide
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P4.3-A, P2.5-A
Do: experiment compare view (metric deltas, change-set diff, cost, time), Accept / Reject / Branch / Modify actions calling `/v1`; decision timeline tab.

### P4.5-A — Client run page cleanup
Model: Sonnet 5.5 (low) · Size: S · Depends on: P4.1-A
Do: remove always-visible technical panel (`apps/web/app/lab/runs/[run_id]/page.tsx:399-401`) for non-development roles; processing state shows only "Analyzing your data".

### P4.6-A — Killer-flow E2E
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.4-A
Do: Playwright spec covering goal → findings → run → inspect feature → view code → branch → accept, on the seeded stack.
