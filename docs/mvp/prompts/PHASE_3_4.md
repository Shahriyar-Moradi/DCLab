# Phase 3 — Agent interface (/v1, SDK, CLI, MCP) and Phase 4 — Studio, use and trust, assistant

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

## Phase 4 — Studio, use and trust the model, assistant

Order (founder decisions 2026-10-02 and 2026-10-04, merged; ROADMAP.md): Stage 1
(use-and-trust backend) → **Phase 6, the hybrid AI core** (`PHASE_5_6.md`) →
Stage 2 (Studio catch-up UI for everything Phases 1–3 built) → Stage 3 (UI for
use and trust) → Stage 4 (agent-first Studio: the Lab chat with the lead agent as
the in-app assistant, Track A screens in `ASSISTANT.md`, and the Phase 6 screens)
→ Stage 5 (home, inbox, demo and partner kits). Checkpoint G6 falls during Stages
2–3 and must pass before Stage 4 exposes AI to users. Forms remain the AI-off path
for everything the chat can do. Every row in `../UI_COVERAGE.md` marked
"planned" for Phase 4 closes in this phase.

**Design target:** every Studio screen follows the prototype in
`../design/prototype/` under the sync rule of `../design/STUDIO_DESIGN.md` §1:
every value comes from an API field, elements without a backend are not drawn.
Each UI prompt names its prototype file in a `Design:` line.

### Stage 1 — Use and trust the model (backend + MCP)

### P4.9-A — Score new data with a model version
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P3.1-B3 · Review: ml-correctness-reviewer, db-migration-reviewer, security-reviewer
Goal: a user uploads a new file and downloads predictions from a chosen model version.
Read: `services/ml_job_service.py`, `engine/serving/`, `api/v1_*.py`, ADR 0005, P7.1-A/P7.2-A below (this prompt is their first slice).
Do: scoring input is a normal dataset upload (`POST /v1/datasets`, purpose `scoring`, no target, same policy/quarantine path); `batch_predictions` table (via `/new-migration`) with `model_version_id`, nullable `model_release_id` (filled in Phase 7), input dataset, status, row counts, contract-check result, output artifact key; job `models.batch_predict` in the worker only: check feature contract (missing required columns fail with a clear message; extra and target columns ignored), apply the full fitted pipeline and the stored operating threshold, write CSV/Parquet to object storage; `POST /v1/model-versions/{id}/predictions`, `GET /v1/predictions/{id}`, signed download; MCP `predict` (write, proposal-style like `run_experiment`) and `get_prediction`; SDK + CLI `dclab predict`.
Don't: load models in the API; add releases, monitoring or rollback (Phase 7); score across workspaces.
Verify: tests for contract failures, row-count equality, threshold applied, tenant isolation, idempotent replay; `generate_truth_artifacts --verify-idempotent`.
Done when: upload → predict → download works from CLI and MCP on the seeded stack.

### P4.10-A — Five core trust checks as plain-language findings
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P2.3-A · Review: ml-correctness-reviewer
Goal: every run shows the checks people understand at once.
Read: `engine/modeling/leakage_auditor.py`, `services/pipeline_verifier.py`, `data_quality_findings` / `ml_run_verifications` models, P5.1-A below.
Do: create `engine/investigate/` (pure, typed `Finding(check, severity, evidence, recommendation_kind, message)`) with: target leakage (from the existing audit), train-vs-CV overfit gap, duplicate rows (within train and across train/holdout), class imbalance, too-good-to-be-true score (CV metric near perfect or far above baseline); job step after each run stores findings in the **existing** findings owner (search first; a new table only if the db-migration-reviewer agrees); deterministic plain-language message templates with the numbers filled in; `GET /v1/experiments/{id}/findings`; MCP `get_findings`.
Don't: read the holdout for anything except the duplicates-across-split check (row hashes only).
Verify: each check has a synthetic positive and negative test; E2E lab suite still green.

### P4.11-A — Model card
Model: Opus 5.5 (high) · Size: M · Depends on: P4.10-A · Review: ml-correctness-reviewer
Goal: every model version explains itself in one page.
Do: read-model service + `GET /v1/model-versions/{id}/card` (JSON + Markdown): top drivers (reuse stored importance; if absent compute permutation importance on validation folds in the engine during the run, never on holdout), metric explained in business words from the ProblemSpec objective (e.g. "of 100 flagged customers, about N really churn"), comparison with the dummy baseline, the single holdout result labelled as such, known risks from findings, data and split summary, "LLM used: yes/no"; MCP `get_model_card`.
Verify: card tests for classification, regression, multiclass; no field reads holdout beyond the locked winner's single evaluation.

### Stage 2 — UI foundation and catch-up for Phases 1–3 (after the Phase 6 gate)

### P4.0-A — Keep the UI and the API in sync
Model: Opus 5.5 (high) · Size: M · Depends on: P3.1-B3
Goal: a backend contract change cannot silently break or bypass the UI.
Do: generate TypeScript types from `contracts/v1_openapi.json` (`npm run gen:api`, one reviewed generator, e.g. `openapi-typescript`) into `apps/web/lib/infrastructure/v1/`; typed `/v1` client on the existing `apiGet/apiPost` with Idempotency-Key and If-Match helpers; `/v1` zod schemas checked against generated types (`satisfies`) so drift fails `tsc`; CI step regenerates and fails on diff; a test that fails when an operation in `v1_openapi.json` has no row in `docs/mvp/UI_COVERAGE.md`.
Don't: move client-facing schemas out of the banned-terms block in `lib/domain/schemas.ts`.
Verify: `npx tsc --noEmit`, `npm run lint`, `npm run test:components`, `npm run build`; the coverage test fails on a fake new operation.

### P4.0-B — Studio design system and shell
Model: Sonnet 5.5 (high) · Size: M · Depends on: P4.0-A
Design: `prototype/app.css`, `prototype/app.js`, any screen for the shell; STUDIO_DESIGN.md §2–3.
Do: port the tokens (light + dark) into the Tailwind theme and CSS variables; build `components/studio/*` (Shell, Crumbs, CommandBar, PageHead, Card, SectionTabs, Tabs, Pill, Level, Stat, DataTable, KeyValue, EventList, Chat, Banner, CodeBlock, Chart; ReviewNotes dev-only); role-based sidebar config with items gated by feature/backend availability; a `/dev/studio-kit` page (development role only) showing every component in both themes.
Don't: copy prototype sample data; add pages beyond the kit.
Verify: tsc/lint/build; Playwright screenshot of the kit in light and dark; axe accessibility check has no serious violations.

### P4.1-A — Project-centric information architecture
Model: Opus 5.5 (high) · Size: M · Depends on: P4.0-B
Design: `prototype/app.js` nav groups; `prototype/experiments.html` page head.
Do: routes `/home` (placeholder until P4.15), `/projects`, `/projects/[id]/{lab,pipeline,graph,data,experiments,improve,models,monitoring,decisions}` in the Studio shell (project pages appear in the sidebar only when their backend exists); ⌘K command bar searching projects, experiments, models and decisions through existing list endpoints; navigation in `components/layout/app-navigation.ts`; project header with ref badges; Decision.ai vertical pages (`/app/opportunities|decisions|insights|dashboards`, home opportunity data) hidden unless `legacy_decision_layer_enabled`; `/lab/runs/[run_id]` redirects into the project experiment page (live progress from `/v1/model-builds/*` keeps working); workspace `/inbox` route (filled by P4.16); the BFF forwards `Idempotency-Key` and Content-Length on `/v1` writes (carried from P3.1-B1; skip if already done); placeholder slot for the assistant panel (A3-UI).
Verify: tsc/lint/build; Playwright navigation spec.

### P4.1-B — New project wizard: upload → target → train
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P4.1-A
Design: `prototype/lab.html` "Use forms instead" path; `prototype/home.html` "+ New project".
Do: wizard on `/projects/new`: upload (`POST /v1/datasets`) with profile preview, target and task (`propose_problem_spec` then create; target confirmation), optional objective and constraints (P1.4-B objective contract), "Train" (`POST /v1/experiments`) → lands on the experiment page with live progress; Experiments tab "New run" reuses the same form.
Verify: Playwright: CSV → trained experiment on the seeded stack.

### P4.1-C — Data page
Model: Opus 5.5 (high) · Size: M · Depends on: P4.1-A · Review: ml-correctness-reviewer, security-reviewer
Design: `prototype/data.html` tabs Versions · Columns & roles · Leakage audit · Findings & questions · Policy & access.
Do: NEW `GET /v1/datasets/{id}/profile` over the existing profile/column tables (per column: type, role used, rule role, missing %, unique, transform, importance when an experiment exists; statistics from training rows of the current SplitPlan only), policy fields (ADR 0005 upload policy, data class for AI) on the dataset read; MCP `inspect_dataset` returns the same shape via the catalog; UI tabs: Versions (list, digest, used-by from the graph, "make current" = ref move), Columns & roles, Leakage audit (from experiment evidence), Findings (slot filled by P4.10-UI), Policy & access. The "AI (Jev)" column and AI explanations show only for purposes whose P6.8 release decision allows it.
Verify: profile tests incl. holdout rows never counted and tenant isolation; Playwright Data page spec.

### P4.2-A — Graph view
Model: Opus 5.5 (high) · Size: M · Depends on: P4.1-A, P2.3-A
Design: `prototype/graph.html` (★ refs, orange stale nodes, inspector drawer with actions).
Do: render `GET /v1/projects/{id}/graph` (one small reviewed lib, e.g. React Flow, or SVG) with node status, stale markers, ref badges, AI-decision markers (from P6.9 decision records); click → inspector drawer; "what becomes stale" from `GET /v1/nodes/{kind}/{id}/impact`; accessible list fallback.

### P4.3-A — Node inspectors with reason, evidence and code
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P4.2-A
Design: `prototype/experiments.html` tabs All experiments · detail (Candidates, Per-fold and threshold, Feature importance) · Code; `prototype/graph.html` inspector.
Do: inspectors for DatasetVersion (profile, AI investigation from P6.4 when released), SplitPlan, FeatureRecipe (each transform: reason, formula, importance, code snippet), Experiment (config, change set, CV metrics, folds, evidence, findings slot, Critic review (P6.4), reproducible code from `/v1/experiments/{id}/code` with copy/download), ModelVersion; reuse `components/admin/{Evaluation,FinalModel,ModelComparison,ProcessingSummary}` and `components/model-build/*`.

### P4.4-A — Compare, branch, refs and decide
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P4.3-A, P2.5-A
Design: `prototype/experiments.html` Compare and Branch builder tabs (typed change set, validation preview); `prototype/decisions.html` (append-only list, detail, Revert/Correct = supersede).
Do: compare view (`/v1/experiments/compare`: metric deltas, change-set diff, cost, time); Branch form (typed change set → `POST /v1/experiments/{id}/branches`); Cancel; "Make champion" ref move with If-Match and conflict message; Decisions tab timeline with Accept / Reject / Supersede.
Verify: Playwright: branch → compare → accept → decision appears.

### P4.5-A — Client run page cleanup
Model: Sonnet 5.5 (low) · Size: S · Depends on: P4.1-A
Do: remove the always-visible technical panel (`apps/web/app/lab/runs/[run_id]/page.tsx:399-401`) for non-development roles; processing state shows only "Analyzing your data". (The full business view `prototype/client.html` is P7.6-A.)

### P4.8-UI — Agents & tools page (Connect and Tokens tabs)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.1-A, P3.5-A
Design: `prototype/agents.html` tabs Connect: MCP · SDK · CLI · API and Service tokens.
Do: `/agents` with Connect (ready `.mcp.json` snippet, SDK and CLI examples, `/v1` conventions and OpenAPI link, read/write switch explained) and Service tokens (move the existing `ServiceTokensPanel` here: scopes, expiry, last use, revoke). Tool registry and Agent runs tabs arrive with A2-UI, Agent catalog with P6.8-UI.

### Stage 3 — UI for use and trust

### P4.9-UI — Score new data
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.9-A, P4.3-A
Design: `prototype/models.html` Model versions and Batch predictions tabs ("Score a file").
Do: model page "Score new data": upload, contract-check result in plain words (missing columns listed), progress, download; list of past scorings.

### P4.10-UI — Findings panel
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.10-A, P4.3-A
Design: `prototype/data.html` Findings tab (event list) and Data quality chips; `prototype/pipeline.html` stage 10.
Do: findings panel on every experiment page and in the experiment inspector: severity, plain message, the numbers, "what to do" (link to Branch with a pre-filled change when the recommendation maps to one); badge count on the Experiments tab. P5.1 checks appear here automatically.

### P4.11-UI — Model card page
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.11-A, P4.9-UI
Design: `prototype/models.html` "MV v4 · lineage" and "What a promotion requires"; plain-words list of `prototype/client.html` "How the model was built".
Do: Models tab and model page "Card" tab rendering the card; print/PDF-friendly layout; Markdown download.

### P4.17-UI — Pipeline evidence page
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P4.3-A, P4.10-UI
Design: `prototype/pipeline.html` (run switcher, 4 stat cards, 10 stage cards with deterministic result, artifact digest, decision record link, checks; "Decision points in this run" and "Cost and provenance").
Do: `/projects/[id]/pipeline/[experimentId]` from `/v1/model-builds/{id}`, `/events`, `/artifacts`, the run's decision records and findings; stage order from the engine's typed stages (P1.5-A); "AI-before / AI-after" notes and "Decision points in this run" (AI answer, rule answer, value used, level) come from the P6.9 decision records and P6.4 Critic review, and "Replay AI" calls `GET /v1/agent-runs/{id}/replay` (P6.11-A); with AI off these rows show the rule answer only; download bundle uses existing artifact downloads.
Verify: Playwright on a seeded completed run: 10 stages, digests shown, decision links open the Decisions page.

### Stage 4 — Agent-first Studio (after checkpoint G6)
The backend already exists from Phase 6 (A1-A, A2-A and A2-B were absorbed into P6.1-A, P6.2-A/B, P6.10-A and P6.3-B). Prompts: A2-UI → A3-UI → A4-A → A4-B → A6-A (`ASSISTANT.md`), then P6.7-UI, P6.8-UI, P6.11-UI (`PHASE_5_6.md`).

### Stage 5 — Home, inbox, focus, demo, partners, end-to-end

### P4.12-A — One audience, one story
Model: Sonnet 5.5 (low) · Size: S · Depends on: P4.1-A
Do: after login each role lands in its workspace (STUDIO_DESIGN.md §3: developer/admin → `/home`, client → existing client pages until `/outcomes` in P7.6, operator → `/admin/monitoring` until `/operator` in P8.5-UI); `/` tells the DCLab story ("an ML lab your AI agent can drive, which proves every model is correct") with one call to action; marketing routes (`industries`, `solutions`, `pricing`, `showcase`, `platform`, `company`, `resources`, `business`) removed from navigation and redirected unless `legacy_marketing_pages_enabled`; after login developers land on `/projects`.
Don't: delete the frozen Decision.ai code (deadline 2027-03-31 decision, Phase 9).

### P4.15-A — Home and activity read models
Model: Opus 5.5 (high) · Size: M · Depends on: P4.4-A · Review: security-reviewer
Do: NEW `GET /v1/activity` (workspace or project scope; cursor-paged projection over decision records and run lifecycle events: actor kind rule/agent/person, subject node, one-line summary); project list gains summary fields (goal from current ProblemSpec, champion metrics from the champion ref, latest run status) without N+1 queries; no new tables.
Verify: projection tests, tenant isolation, pagination.

### P4.15-UI — Home
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.15-A, P4.16-A
Design: `prototype/home.html` (4 stat cards, Projects table, Activity, Inbox preview, Quick actions). Agent activity and open proposals come from `/v1/activity` and `/v1/inbox`; "AI health" and spend cards read the P6.2-B ledger and P6.11-A governance API (plan spend after P8.4).
Verify: Playwright Home spec on the seeded stack.

### P4.16-A — Inbox read model
Model: Opus 5.5 (high) · Size: M · Depends on: P4.4-A · Review: security-reviewer
Do: NEW `GET /v1/inbox` (workspace-wide, capability-filtered): proposed decision records, pending agent and assistant proposals (P6.6 `agent_proposals`), open questions, run-completed notices; tabs needs a decision / applied automatically (L2 decisions with Revert via P6.6; empty while every decision point is below L2) / done; counts endpoint for the sidebar badge; actions reuse the existing accept/reject/supersede routes and the P6.6 proposal accept/reject/revert routes.
Verify: tests for capability filtering (a user without `decisions:accept` sees but cannot act), tenant isolation.

### P4.16-UI — Inbox
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.16-A
Design: `prototype/inbox.html` (item cards with evidence, rule answer, level, Approve / Reject / See comparison).
Verify: Playwright: proposed decision → inbox → approve → decision record accepted.

### P4.13-A — Demo kit
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.11-UI, A4-A
Do: `dclab demo seed` creates a churn demo project (public dataset, one planted leakage column the checks catch); `docs/DEMO.md` with a 2-minute script for two paths: Claude Code via MCP and Studio with the assistant; deterministic demo mode for the fake LLM.
Done when: founder records the demo video.

### P4.14-A — Design-partner kit
Model: Sonnet 5.5 (low) · Size: S · Depends on: P4.13-A
Do: one-command local install (`make partner-up` on the compose stack, demo users off, own admin created interactively), `docs/PARTNERS.md` (what to bring: one real use case; what we need back), in-app "Send feedback" link to a GitHub issue template; nothing collected silently.
Done when: founder has 2–3 partners booked; their first runs are noted in STATUS.md.

### P4.6-A — Killer-flow E2E
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.4-A, P4.11-UI, P4.17-UI, A4-A
Do: Playwright on the seeded stack with the fake LLM driver: Home → new project → Lab: goal in chat → AI decisions shown with their level beside the rule answer (accept one L1) → run → Pipeline evidence with every stage and digest → findings → Data page → inspect feature → view code → assistant proposes a branch → confirm → Inbox shows the run → compare → accept → score new data → model card; a second spec runs the same flow with AI off through the forms.
