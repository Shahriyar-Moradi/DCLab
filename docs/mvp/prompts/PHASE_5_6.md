# Phase 6 — Hybrid AI core (runs after Phase 4 Stage 1) and Phase 5 — Verify & Improve (hybrid)

Order (ROADMAP.md; hybrid AI order 2026-10-02 merged with use-and-trust first
2026-10-04): Phase 6 runs after Phase 4 Stage 1 (score, checks, model card) and
**before** Phase 4 Stages 2–5 and Phase 5. Prompt ids are unchanged. Track A's
backend is built here once: A1-A → P6.1-A, A2-A → P6.2-A + P6.2-B, A2-B → P6.10-A
(tool catalog) + P6.3-B (assistant loop and threads API). Execution order inside
Phase 6:
P6.1 → P6.2-A → P6.2-B → P6.10-A → P6.3-A → P6.7 → P6.9 → P6.4 → P6.3-B → P6.6 →
P6.10-B → P6.11 → P6.8. P6.5 runs inside Phase 5 after P5.4.

Layering rule for every prompt: governance policies (data) → enforced by the AI
gateway (the only door to any model) → called only through the agent harness
(the only way to run an agent) → agents. CI rules from P6.2-B and P6.10-A keep
this true.

Rules for every Phase 6 prompt: AI proposes, deterministic code validates and
executes; AI sits before or after a deterministic step, never inside it; every
AI output is typed, cited and logged (`agent_runs`, `llm_invocations`,
`semantic_decision_answers`); every path works with AI disabled; no AI reads the
holdout, selects the winner, computes metrics or builds splits.

## Phase 6

### P6.1-A — ADR 0008: hybrid AI decision model; ADR 0009: governance, gateway, harness and assistant
Model: **Fable 5.1** (high) · Size: L (design only; two ADRs: 0008 decision model / 0009 governance + gateway + harness + in-app assistant) · Depends on: P3.4-A, P2.5-A · Review: security-reviewer, ml-correctness-reviewer
Read: `docs/mvp/ROADMAP.md` § Hybrid AI model, `docs/mvp/AGENTS_NOOA_JEV.md`, `prompts/ASSISTANT.md` (fixed rules 1–7), `../design/prototype/governance.html` and `lab.html`, ADR 0006 §5 (decision records), `packages/dclab_mcp` tool registry, `services/auto_train/` stages, NOOA docs (`docs/tour.md`, `docs/concepts/strategies.md`, `docs/concepts/safety.md` in `github.com/NVIDIA-NeMo/labs-OO-Agents` — re-verify current version), TypeSafe Jev docs (re-verify `jev-*` release and SDK version).
Decide:
- **Decision-point registry** (code-owned): key, stage, AI kind (Jev purpose / agent class), rule implementation, allowed answers, max trust level, default level, fallback.
- **Patterns**: AI-before (proposes inputs to a deterministic step), AI-after (reviews outputs), cross-check (both run; disagreement → L1 review). Which pattern each decision point uses.
- **Trust levels L0–L3**: semantics, who may change a level, promotion evidence from R3 (minimum cases, accuracy ≥ rule, cost and latency budgets), demotion triggers, per-workspace overrides; each change is a decision record.
- **Jev deterministic policy**: pinned release, thresholds per purpose, agreement table (agree / disagree / abstain), cache key = digest of (purpose, release, state), never numeric questions.
- **Lead-agent runtime**: bounded tool loop (max steps, tokens, wall time, cost) over the MCP tool registry executed in-process through the same services; which tools need L1 confirmation; how conversation state is stored (state graph + `agent_events`, not chat history alone); provider/model choice per agent; whether NOOA hosts the loop or the LLM gateway does.
- **Governance policy schema** (`ai_policies` per workspace, with a code-owned platform default): model allowlist per agent role, data classes allowed to leave (metadata / aggregates / sample values / raw rows), trust levels per decision point and their caps, budgets (per workspace, per project, per run), autonomy policy for operations, approvers for policy changes and for L1 decisions, retention of prompts/answers, incident rules (auto-demote a decision point after N validator rejections or a failed R3 run). Who may change what; every change is a decision record.
- **Gateway contract**: one `complete(request) -> response` for LLMs and one `decide(...)` for Jev; request carries agent role, workspace, data class, purpose, prompt release id, budget reservation; gateway resolves provider/model from policy, redacts by data class, enforces budget and rate limits, caches by digest, records `llm_invocations`, honours kill switches (global, workspace, agent, purpose); no provider SDK import anywhere else.
- **Harness lifecycle**: `AgentService.run(spec)` = build ContextEnvelope → redact → reserve budget → runtime (fake / NOOA Predict / lead loop) → tool calls only through the registry with validators and capability checks → validate output (Pydantic + deterministic validator) → persist events/proposals → settle usage → emit eval sample; hooks pre-run / pre-call / pre-tool / post-tool / post-run with allowed effects (observe, deny, modify result, add citation); replay mode from recorded events; what is immutable per run.
- **In-app assistant = lead agent** (absorbs A1-A): `AssistantStep` schema, page context as a hint the server re-resolves, SSE through the BFF, thread persistence in `agent_runs`/`agent_events`, write tools as proposals at the decision point's level, LLM-off behaviour (deterministic quick actions and templates from findings and the model card).
- Who owns cleaning actions: proposal kind vs cleaning recipe.
- NOOA and LiteLLM pins, ContextEnvelope schema, event mapping, proposal state machine, prompt-release versioning.
Done when: ADR(s) accepted by founder; AGENTS_NOOA_JEV.md updated to match.

### P6.2-A — Agent and governance persistence
Model: Opus 5.5 (high) · Size: L · Depends on: P6.1-A · Review: db-migration-reviewer, security-reviewer
Do: tables `agent_runs` (kind: `assistant`, `lead`, NOOA class keys, `ops`), `agent_events` (append-only), `agent_proposals`, `semantic_decision_answers` per AGENTS_NOOA_JEV.md §6; governance tables `ai_policies` (versioned, per workspace, platform default row, changed_by decision record), `decision_point_policies`, `workspace_llm_budgets`, `prompt_releases` (agent key, version, digest of prompt text, released_at, status), `ai_incidents` (kind, subject, evidence, action taken, resolved_by); add FKs for `llm_invocations.agent_run_id` and `project_decision_records.actor_agent_run_id`; `GovernancePolicyService.effective_policy(workspace)` merging platform default + workspace policy with caps.
Verify: migration tests (`/new-migration`); append-only and policy-cap tests; truth artifacts regenerated.

### P6.2-B — AI gateway
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P6.2-A · Review: security-reviewer
Read: `engine/lab/llm_client.py`, `services/openai_provider.py`, `services/pipeline_audit_service.py`, `llm_invocations` writers (grep).
Do: `app/agents/gateway/` per ADR: `contract.py` (request/response types), `router.py` (provider/model from effective policy and agent role; refuses models off the allowlist), `redaction.py` (strips values above the workspace data class; column names and user text marked untrusted), `budget.py` (reserve → settle; over budget → typed refusal), `cache.py` (digest of prompt release + redacted input), `limits.py` (rate limits per workspace and provider; circuit breaker per provider/purpose), `ledger.py` (one `llm_invocations` write per call incl. cache hits with zero cost), `switches.py` (global / workspace / agent / purpose kill switches read from policy and settings), `providers/` (the only modules importing OpenAI/LiteLLM/TypeSafe SDKs; fake provider for tests). Migrate the four existing LLM writers onto the gateway (closes the ADR 0005 `llm_exposure_policy` note). Import-linter contract in CI: provider SDK imports allowed only under `app/agents/gateway/providers/`. (This is the gateway Track A's A2-A planned; there is no second one.)
Verify: existing LLM decision tests unchanged; allowlist refusal, redaction by data class, budget exhaustion, breaker open, kill-switch tests; CI import rule fails on a planted violation.

### P6.10-A — Agent harness
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P6.2-B · Review: security-reviewer
Do: `app/agents/harness/`: `service.py` (`AgentService.run(spec)`, the only entry point for running an agent; job handler `agents.run`), `context.py` (ContextEnvelope from read-only services; metadata and aggregates only; digest recorded), `hooks.py` (pre-run / pre-call / pre-tool / post-tool / post-run; typed effects: observe, deny with reason, modify tool result, attach citation; built-in hooks: budget, capability check, redaction, event recorder, eval sampler), tool catalog in `app/agents/tools/` (one definition shared with `packages/dclab_mcp`, exported to `contracts/agent_tools.json` with a contract test against `dclab_mcp`; adds `get_impact` over `/v1/nodes/{kind}/{id}/impact` to both; `get_findings`/`get_model_card` from P4.10/P4.11; each tool = schema + effect (read/write) + capability + validator + service call; no tool reads holdout rows, selects winners or computes metrics — property-tested), `validation.py` (Pydantic + deterministic validators; invalid → `rejected_by_validator` proposal, never an exception to the user), `recorder.py` (every prompt release id, redacted input digest, tool call and result digest, output → `agent_events`), `replay.py` (re-run a recorded run with the fake provider fed from the record; asserts identical tool sequence and output). Runtimes plug in via `runtime/base.py`; a CI test fails if any runtime or job can be invoked outside `AgentService.run`.
Verify: harness tests with the fake provider: lifecycle order, each hook effect, denial paths, budget reserve/settle, replay equality; registry property tests.

### P6.3-A — Runtimes: fake + NOOA Predict (on the harness)
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P6.10-A · Review: security-reviewer
Do: `app/agents/runtime/{base,fake_runtime,nooa_runtime}.py` registered with the harness; `nooa` + pinned `litellm` in an optional extra `agents`; NOOA's LLM object is a thin adapter that calls the gateway (no direct provider access); explicit tracing exporters, auto viewer disabled; budgets enforced by the harness (calls, tokens, wall time via asyncio timeout); one instance per run; **test that inspects every registered agent class and fails if any generation method resolves to a non-Predict strategy**.
Verify: fake-runtime tests; NOOA adapter test with a stubbed gateway response (no network).

### P6.7-A — Jev at decision points (deterministic policy)
Model: Opus 5.5 (high) · Size: M · Depends on: P6.2-A · Review: security-reviewer
Do: `app/agents/semantic/{port,deterministic,typesafe_jev,releases,policy}.py`; `typesafe-sdk` pinned in `agents` extra; pinned Jev release; 5 purposes from AGENTS_NOOA_JEV.md §5; `policy.py` applies the ADR 0008 agreement table deterministically (agree → apply; disagree → L1 review; below threshold → rule); answers cached by input digest; all purposes start at L0 (answers logged beside the rule answer, no behavior change); 1 s timeout + circuit breaker; metadata-only state unless workspace policy allows sample values.
Verify: tests with fake Jev; same input → same answer from cache; no network in CI; disabled flag = zero calls.

### P6.9-A — Hybrid decision points in auto-train
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P6.7-A, P6.3-A · Review: ml-correctness-reviewer
Read: `services/auto_train/` (10 stages), `services/decision_record_service.py`, `fixtures/auto_train_event_sequence.json`.
Do: a `DecisionPoint` hook around the target, column-role, leakage, holdout-strategy and family/budget decisions: run the rule (always) and the AI (Jev purpose or agent, when enabled), apply the level from `decision_point_policies` (L0: rule value; L1: rule value + pending proposal; L2: AI value when the validator accepts it, else rule; L3 not allowed in auto-train), write one `semantic_decision_answers`/`agent_proposals` row and one decision record per point (actor, both answers, value used, evidence, revert target); emit a `decision_point_resolved` pipeline event. Column exclusion never above L1. Holdout, selection, metrics untouched.
Verify: event-sequence golden updated only by the new event; with AI disabled every existing auto-train/E2E test passes unchanged; per-level tests with fake Jev/agents (agree, disagree, timeout, over budget, validator rejects).

### P6.4-A — Agent classes
Model: Opus 5.5 (high) · Size: L (one class per PR if needed) · Depends on: P6.3-A, P6.9-A
Do: `ExperimentCriticAgent` first (AI-after: runs on every completed run as a non-interactive job, Batch-API eligible), then `DatasetInvestigatorAgent` and `ExperimentPlannerAgent` (AI-before: feed P6.9 decision points); Pydantic proposals, context builders and deterministic validators per AGENTS_NOOA_JEV.md §3; prompt (docstring) versions recorded.
Verify: golden fake transcripts; validator rejection tests (unknown column, fabricated metric); Critic runs on every completed E2E run when enabled and never changes state.

### P6.3-B — Lead-agent runtime (bounded tool loop)
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P6.4-A · Review: security-reviewer
Do: `app/agents/lead/` per ADR 0008, as a harness runtime: tool registry from P6.10-A shared with `packages/dclab_mcp` (one definition, two transports), in-process execution through the same services and capability checks, step/token/time/cost bounds, L1 tools return a pending proposal instead of acting, conversation turns stored as `agent_events` with references to graph nodes; fake LLM driver for tests. It is the in-app assistant's backend (absorbs A2-B): `POST /v1/assistant/threads`, `POST /v1/assistant/threads/{id}/messages` (SSE), `GET` thread history; read tools run in-process with the signed-in user's workspace and capabilities; write tools become `agent_proposals`; every claim must cite an existing node (validator); rules 1–7 of `ASSISTANT.md` apply.
Verify: scripted fake-LLM sessions: upload → propose spec → run → compare stays within bounds; a tool call outside the token's capabilities is refused; holdout-reading and selection tools do not exist in the registry; unknown tool, invalid step and fabricated citation are rejected; a thread of workspace A is unreadable from B; LLM-off path returns templates.

### P6.6-A — Proposal review flow (API/MCP)
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P6.4-A, P2.5-A
Do: `/v1/agent-runs`, `/v1/proposals` (list/filter by level, decision point, status), `/v1/proposals/{id}/accept|reject|revert`; acceptance calls the normal command service and writes a decision record; revert of an L2 decision restores the rule value via a superseding record; MCP tools `request_agent_review`, `list_proposals`. One proposal model for agents and the assistant; Studio screens: Inbox (P4.16), confirm cards (A4-A).
Verify: per-route tests incl. tenant isolation and idempotent replay; revert test.

### P6.10-B — Test harness for AI
Model: Opus 5.5 (high) · Size: M · Depends on: P6.3-B, P6.7-A, P6.9-A
Do: `apps/api/tests/ai_harness/`: fake provider driven by recorded fixtures (`fixtures/ai/<agent>/<case>.json`: redacted input digest → output) and by scripted scenarios; golden transcripts per agent class and for the lead agent; property tests (CodeAct ban, holdout-blind tool registry, no raw rows leave the gateway, every model call has a ledger row, every agent run has a harness record); chaos cases (provider timeout, breaker open, over budget, invalid output, kill switch mid-run) each ending in the rule fallback; replay test on every recorded fixture; `make test-ai` runs it all offline; the R3 runner (P6.8-A) builds on it.
Verify: suite green offline; a planted violation of each property fails CI.

### P6.11-A — Governance console API
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P6.2-A, P6.8-A · Review: security-reviewer
Do: `GET /v1/governance` (effective policy, model allowlist, data classes, decision-point levels with R3 evidence links, spend vs budget per period, open incidents, recent policy changes), `POST /v1/governance/policy` (proposes a policy change as a decision record; applies on acceptance by an approver; caps enforced), `POST /v1/governance/switches` (kill switches, audited), `GET /v1/agent-runs/{id}/replay` (replay result); MCP read tool `inspect_governance`; SDK methods. Studio page P6.11-UI.
Verify: tenant isolation; non-approver cannot apply a change; switch flip stops the next gateway call; replay endpoint matches the recorded output.

### P6.8-A — R3 evaluation harness and trust levels
Model: **Fable 5.1** (high) · Size: M · Depends on: P6.9-A, P6.4-A, P6.10-B
Do: continuous evaluation corpus from R1 datasets + labeled user decisions; per decision point: AI accuracy vs rule accuracy, disagreement rate, citation validity, validator rejection rate, cost/latency; Jev calibration (reliability, ECE, precision in acting band) per purpose; ablation (AI off) on every release; write the first trust levels into STATUS.md § Agent / Jev release decisions and `decision_point_policies` defaults via decision records; auto-demotion rule wired to `ai_incidents`.
Done when: every decision point and agent has a recorded level with evidence; checkpoint G6 scheduled.

**Phase 6 screens** — built in Phase 4 Stage 4, once the Studio shell exists (with A2-UI and A3-UI in `ASSISTANT.md`):

### P6.7-UI — Jev answers view
Model: Sonnet 5.5 (low) · Size: S · Depends on: P6.7-A, P4.1-A
Do: development-role-only page listing Jev answers beside the rule answer, with the level and value used, per purpose (agreement rate, latency, examples); hidden for other roles.

### P6.8-UI — Agent catalog tab
Model: Sonnet 5.5 (low) · Size: S · Depends on: P6.8-A, P4.8-UI
Design: `prototype/agents.html` Agent catalog (agent, kind, pattern, input envelope, output type, validator, prompt release, model, runs, status).
Do: Agents & tools → Agent catalog: each agent and Jev purpose with its trust level and R3 evidence, on/off per workspace (only where the decision allows), last runs and cost; the Data page "AI (Jev)" column and pipeline AI notes are unhidden for released purposes.

### P6.11-UI — Governance page
Model: Opus 5.5 (high) · Size: M · Depends on: P6.11-A, P4.1-A · Review: security-reviewer
Design: `prototype/governance.html` tabs Policy · Decision points & trust levels · Budgets & spend · Kill switches · Incidents · Audit & replay · Evaluation (R3).
Do: `/governance` reading the real sources: model/provider allowlist and data-class flags (effective policy from `GET /v1/governance`), decision points with the levels ADR 0008 permits, budgets and spend from the P6.2-B ledger, kill switches (global read-only, workspace toggles where allowed), validator rejections and fallbacks as incidents, ledger query and replay of recorded runs, R3 results; policy changes are decision records approved by a capability holder. Tabs without a backend stay hidden.
Verify: capability tests (only approvers change policy); Playwright toggle workspace kill switch → assistant reports off.

### P6.5-A — Improvement hypothesis agent in the loop (runs in Phase 5)
Model: Opus 5.5 (high) · Size: M · Depends on: P6.4-A, P5.4-A · Review: ml-correctness-reviewer
Do: `ImprovementHypothesisAgent` as a proposer for `labs.improve`; the rule proposer always runs too (cross-check): the agent's action is used when its decision point is ≥ L2 and the validator accepts it, otherwise the rule action; disagreements recorded; action must be in the typed action space.
Verify: loop works identically with the agent off; with fake agent proposals; R2 compares both proposers.

Phase 5 prompts ship their screens in the same phase; Phase 6 screens are listed above (Phase 4 Stage 4); see `../UI_COVERAGE.md`. Screens follow `../design/prototype/` under the sync rule of `../design/STUDIO_DESIGN.md`. Assistant tools for these phases: A5-A, A6-A in `ASSISTANT.md`.

## Phase 5

### P5.0-A — Feature-engineering leakage contract
Model: **Fable 5.1** (high) · Size: M (design + contract tests) · Depends on: P2.4-A · Review: ml-correctness-reviewer
Read: `engine/modeling/leakage_auditor.py`, ADR 0006 §4 (`FEATURE_TRANSFORM_ALLOWLIST`), `engine/features/`.
Decide and encode: every feature transform declares inputs and a fit scope (`stateless` / `fold_fitted` / `as_of_aggregate` with an `as_of` time column); fold-fitted transforms run inside the sklearn Pipeline per fold; target encoding only out-of-fold (cross-fitting); history aggregates use a strict cutoff (strictly before the row's time, group-aware); feature selection only inside CV; agent-proposed features never see holdout metrics and are tested as branches on the same SplitPlan; per-loop budget of feature attempts. Also check whether `engine/features/combinations.py` group selection happens inside CV and fix if not.
Verify: contract tests that fail on a leaking target encoder, a whole-table aggregate and an outside-CV selection.

### P5.1-A — Investigation check library (remaining checks)
Model: Opus 5.5 (xhigh) · Size: L (split by check group) · Depends on: P4.10-A, P5.0-A · Review: ml-correctness-reviewer
Read: `services/pipeline_verifier.py`, `engine/modeling/leakage_auditor.py`, `docs/DCLAB_ADAPTIVE_MODEL_BUILDER.md:222-258`.
Do: extend `engine/investigate/` (created in P4.10-A with leakage, overfit gap, duplicates, imbalance, too-good-to-be-true) with the same typed `Finding` and message templates: train/holdout contamination beyond row duplicates, calibration (ECE/Brier), fold instability, subgroup performance gaps, feature drift train→holdout (PSI/KS), temporal shift when a time column exists, multicollinearity, missingness pattern shift, **time-travel check** (shifting the as-of cutoff changes a feature value), **new-feature check** (a single added feature causes an outsized CV jump). Service runs them post-run (job handler) and stores findings; `/v1/experiments/{id}/findings` + MCP `get_findings` return them; they render in the P4.10-UI findings panel with no new screen; the Critic (P6.4) cites them.
Verify: each check has a synthetic positive and negative test; Playwright findings-panel spec still green.

### P5.2-A — Objective optimizer
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P1.4-B
Do: constraint-aware threshold search and cost-weighted objective on out-of-fold predictions; report Pareto points (precision/recall at thresholds); store chosen operating point with reason; `GET /v1/experiments/{id}/operating-points`, `POST …/operating-point` (records a decision).
Verify: synthetic tests with known optimum; holdout never read.

### P5.2-UI — Operating point chooser
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P5.2-A, P4.3-A
Design: `prototype/experiments.html` detail "Per-fold and threshold".
Do: experiment page chart of precision/recall (or cost) by threshold from validation folds, constraint line, current point; "Use this point" writes the decision; plain sentence of what the point means ("flags 12 % of customers, catches 71 % of churners"). Model card (P4.11) shows the chosen point.

### P5.3-A — ADR 0007: improve loop (hybrid)
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P2.4-A, P5.1-A, P6.1-A
Decide: typed action space (from `ExperimentChange` + P5.0 feature transforms), two proposers (rule and agent) with cross-check and the ADR 0008 trust level of the `improve.next_action` decision point, budgets (iterations, wall time, compute cost, LLM cost, feature attempts), stop rules (constraint met, no improvement in k iterations, budget, instability), selection rule across iterations (CV only, same SplitPlan), when the holdout is evaluated (once, for the accepted final iteration), auditing (each iteration = child experiment + decision record naming the proposer), cancellation.

### P5.4-A — Improve loop engine
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P5.3-A · Review: ml-correctness-reviewer
Do: `improve_runs` table (goal, constraints, budget, status, best_experiment_id) via `/new-migration`; job handler `labs.improve` iterating: diagnose (P5.1 findings) → rule proposer chooses next `ExperimentChange` (rule table: imbalance → class weights; constraint gap → threshold; underfit → family/tuning) and the agent proposer slot (filled by P6.5-A) → branch → evaluate → stop check. Emits events per iteration.
Verify: loop tests with fake engine for control flow + one real small dataset; budget and cancel tests.

### P5.5-A — Improve UX, chat and MCP tool
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P5.4-A, A3-UI
Design: `prototype/improve.html` (goal and budget, best feasible metric per iteration chart, iteration cards naming the proposer, guardrails that applied, past loops). The "Disagreements" card appears when P6.5 adds the agent proposer.
Do: Studio loop timeline (iteration cards: proposer, changes, reason, metric deltas, cost, Accept/Reject/Compare/Modify) with live updates and Cancel; "Improve" button on the experiment page with goal, constraints and budget form; `/v1/improve-runs`; MCP `improve(goal, constraints, budget)`, `get_improve_run` via the shared tool catalog (the assistant gets them in A5-A and can start and narrate loops from chat).
Verify: Playwright: start loop → iterations appear → accept best.

### R2-A — Improve-loop benchmark (agent vs rule)
Model: Opus 5.5 (high) · Size: S · Depends on: P5.4-A, P6.5-A, R1-A
Do: run the loop on R1 tasks with generated constraints, once per proposer; report satisfaction rate, iterations, compute and LLM cost; the result sets the `improve.next_action` trust level; add to the weekly benchmark.

### R1-P — Publish the benchmark
Model: Sonnet 5.5 (medium) · Size: S · Depends on: R2-A
Do: generate a public benchmark report from R1/R2 results (tasks, DCLab vs dummy and vs a plain sklearn/AutoML reference, cost, time, seeds) as a static page at `/benchmark` and `docs/BENCHMARK.md`; reproducible command in the report; no customer data.
