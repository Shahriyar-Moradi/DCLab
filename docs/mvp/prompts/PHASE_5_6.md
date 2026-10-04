# Phase 5 — Verify & Improve, and Phase 6 — Internal agents (NOOA) + Jev shadow

Every backend prompt here ships its screen in the same phase (`-UI` prompt or a UI line in `Do:`); see `../UI_COVERAGE.md`. Screens follow `../design/prototype/` under the sync rule of `../design/STUDIO_DESIGN.md`. Assistant tools for these phases: A5-A, A6-A in `ASSISTANT.md`.

## Phase 5

### P5.1-A — Investigation check library (remaining checks)
Model: Opus 5.5 (xhigh) · Size: L (split by check group) · Depends on: P4.10-A · Review: ml-correctness-reviewer
Read: `services/pipeline_verifier.py`, `engine/modeling/leakage_auditor.py`, `docs/DCLAB_ADAPTIVE_MODEL_BUILDER.md:222-258`.
Do: extend `engine/investigate/` (created in P4.10-A with leakage, overfit gap, duplicates, imbalance, too-good-to-be-true) with the same typed `Finding` and message templates: train/holdout contamination beyond row duplicates, calibration (ECE/Brier), overfit gap (train vs CV), fold instability, subgroup performance gaps, feature drift train→holdout (PSI/KS), temporal shift when a time column exists, multicollinearity, missingness pattern shift. Service runs them post-run (job handler) and stores findings; `/v1/experiments/{id}/findings` + MCP `get_findings` return them; they render in the P4.10-UI findings panel with no new screen.
Verify: each check has a synthetic positive and negative test; Playwright findings-panel spec still green.

### P5.2-A — Objective optimizer
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P1.4-B
Do: constraint-aware threshold search and cost-weighted objective on out-of-fold predictions; report Pareto points (precision/recall at thresholds); store chosen operating point with reason; `GET /v1/experiments/{id}/operating-points`, `POST …/operating-point` (records a decision).
Verify: synthetic tests with known optimum; holdout never read.

### P5.2-UI — Operating point chooser
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P5.2-A, P4.3-A
Design: `prototype/experiments.html` detail "Per-fold and threshold".
Do: experiment page chart of precision/recall (or cost) by threshold from validation folds, constraint line, current point; "Use this point" writes the decision; plain sentence of what the point means ("flags 12 % of customers, catches 71 % of churners"). Model card (P4.11) shows the chosen point.

### P5.3-A — ADR 0007: improve loop
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P2.4-A, P5.1-A
Decide: typed action space (from `ExperimentChange`), proposer interface (deterministic now, agent later), budgets (iterations, wall time, compute cost), stop rules (constraint met, no improvement in k iterations, budget, instability), selection rule across iterations (CV only, same SplitPlan), when the holdout is evaluated (once, for the accepted final iteration), auditing (each iteration = child experiment + decision record), cancellation.

### P5.4-A — Improve loop engine
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P5.3-A · Review: ml-correctness-reviewer
Do: `improve_runs` table (goal, constraints, budget, status, best_experiment_id) via `/new-migration`; job handler `labs.improve` iterating: diagnose (P5.1 findings) → deterministic proposer chooses next `ExperimentChange` (rule table: imbalance → class weights; constraint gap → threshold; underfit → family/tuning) → branch → evaluate → stop check. Emits events per iteration.
Verify: loop tests with fake engine for control flow + one real small dataset; budget and cancel tests.

### P5.5-A — Improve UX and MCP tool
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P5.4-A
Design: `prototype/improve.html` (goal and budget, best feasible metric per iteration chart, iteration cards naming the proposer, guardrails that applied, past loops). The "Disagreements" card appears only when P6.5 adds the agent proposer.
Do: Studio loop timeline (iteration cards: changes, reason, metric deltas, cost, Accept/Reject/Compare/Modify) with live updates and Cancel; "Improve" button on the experiment page with goal, constraints and budget form; `/v1/improve-runs`; MCP `improve(goal, constraints, budget)`, `get_improve_run` via the shared tool catalog (assistant gets them in A5-A).
Verify: Playwright: start loop → iterations appear → accept best.

### R2-A — Improve-loop benchmark
Model: Opus 5.5 (high) · Size: S · Depends on: P5.4-A, R1-A
Do: run the loop on R1 tasks with generated constraints; report satisfaction rate, iterations, cost; add to nightly.

### R1-P — Publish the benchmark
Model: Sonnet 5.5 (medium) · Size: S · Depends on: R2-A
Do: generate a public benchmark report from R1/R2 results (tasks, DCLab vs dummy and vs a plain sklearn/AutoML reference, cost, time, seeds) as a static page at `/benchmark` and `docs/BENCHMARK.md`; reproducible command in the report; no customer data.

## Phase 6

### P6.1-A — ADR 0008: agent runtime
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P5.3-A · Review: security-reviewer
Read: `docs/mvp/AGENTS_NOOA_JEV.md`, NOOA docs (`docs/tour.md`, `docs/concepts/strategies.md`, `docs/concepts/safety.md` in `github.com/NVIDIA-NeMo/labs-OO-Agents` — re-verify current version), `docs/agentic-program/AGENT_FIRST_MVP_ARCHITECTURE.md` §4.
Decide (building on ADR 0009, the in-app assistant, which already fixed the tool catalog, gateway and agent tables; and on the trust-level question raised by `../design/prototype/governance.html`: whether any decision point may auto-apply with revert (L2) or act and report (L3), or MVP stays L0/L1; and who owns cleaning actions — proposal kind vs cleaning recipe): exact NOOA version pin, LiteLLM pin, ContextEnvelope schema and redaction rules, budgets, event mapping, proposal state machine, data-policy flags for LLM exposure, kill switches.

### P6.2-A — Agent persistence for NOOA classes
Model: Opus 5.5 (high) · Size: M · Depends on: P6.1-A, A2-A · Review: db-migration-reviewer, security-reviewer
Note: the LLM gateway and `agent_runs`/`agent_events`/`agent_proposals` moved forward to A2-A (assistant track).
Do: extend them for NOOA agent kinds (class key, version, prompt digest), add `semantic_decision_answers` per AGENTS_NOOA_JEV.md §6; no second gateway or proposal table.
Verify: migration tests; assistant and existing LLM tests unchanged.

### P6.3-A — Runtimes: fake + NOOA Predict
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P6.2-A · Review: security-reviewer
Do: `app/agents/runtime/{base,fake_runtime,nooa_runtime}.py`; `nooa` + pinned `litellm` in an optional extra `agents`; NOOA LLM configured from DCLab settings; explicit tracing exporters, auto viewer disabled; budgets enforced outside NOOA (calls, tokens, wall time via asyncio timeout); one instance per run; **test that inspects every registered agent class and fails if any generation method resolves to a non-Predict strategy**; job handler `agents.run`.
Verify: fake-runtime tests; NOOA adapter test with a stubbed LiteLLM response (no network).

### P6.4-A — Agent classes
Model: Opus 5.5 (high) · Size: L (one class per PR if needed) · Depends on: P6.3-A
Do: `DatasetInvestigatorAgent`, `ExperimentPlannerAgent`, `ExperimentCriticAgent` with Pydantic proposals, context builders and deterministic validators per AGENTS_NOOA_JEV.md §3; prompt (docstring) versions recorded.
Verify: golden fake transcripts; validator rejection tests (unknown column, fabricated metric).

### P6.5-A — Improvement hypothesis agent in the loop
Model: Opus 5.5 (high) · Size: M · Depends on: P6.4-A, P5.4-A · Review: ml-correctness-reviewer
Do: `ImprovementHypothesisAgent` as an optional proposer for `labs.improve` (flag); falls back to deterministic proposer on invalid/abstain; action must be in the typed action space.
Verify: loop works identically with flag off; with fake agent proposals.

### P6.6-A — Proposal review flow
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P6.4-A, P2.5-A
Design: `prototype/inbox.html` (agent proposals land in the Inbox from P4.16), `prototype/experiments.html` "Critic review" section, `prototype/pipeline.html` AI-before/AI-after notes per stage (now unhidden).
Do: `/v1/agent-runs`, `/v1/proposals/{id}/accept|reject`; acceptance calls the normal command service and writes a decision record; MCP tools `request_agent_review`, `list_proposals` via the shared tool catalog; Studio proposals panel (project-level, one list for agent and assistant proposals) with citations and "untrusted rationale" labeling; "Ask an agent to review" button on dataset and experiment pages.
Verify: Playwright: request review (fake runtime) → proposal → accept → decision record.

### P6.7-A — Jev shadow mode
Model: Opus 5.5 (high) · Size: M · Depends on: P6.2-A · Review: security-reviewer
Do: `app/agents/semantic/{port,deterministic,typesafe_jev,releases}.py`; `typesafe-sdk` pinned in `agents` extra; pinned `jev-1.13.0`; 5 purposes from AGENTS_NOOA_JEV.md §5 in **shadow** (answers logged beside deterministic answer, no behavior change); 1 s timeout + circuit breaker; metadata-only state unless workspace policy allows sample values.
Verify: tests with fake Jev; no network in CI; disabled flag = zero calls.

### P6.7-UI — Jev shadow view
Model: Sonnet 5.5 (low) · Size: S · Depends on: P6.7-A
Do: development-role-only page listing shadow answers beside the deterministic answer per purpose (agreement rate, latency, examples); hidden for other roles.

### P6.8-A — Agent and Jev release gate (R3)
Model: **Fable 5.1** (high) · Size: M · Depends on: P6.5-A, P6.7-A
Do: evaluation corpus from R1 datasets + labeled decisions; measure citation validity, validator rejection rate, usefulness vs deterministic (ablation), cost/latency; Jev calibration (reliability, ECE, precision in acting band) per purpose; write release decisions into STATUS.md (`SHADOW_ALLOWED` / `ADVISORY_ALLOWED` / `DISABLED_PENDING_EVIDENCE` / `REJECTED`).

### P6.8-UI — Agent catalog tab
Model: Sonnet 5.5 (low) · Size: S · Depends on: P6.8-A
Design: `prototype/agents.html` Agent catalog (agent, kind, pattern, input envelope, output type, validator, prompt release, model, runs, status).
Do: Agents & tools → Agent catalog: each agent and Jev purpose with its release decision, on/off per workspace (only where the decision allows), last runs and cost; the Data page "AI (Jev)" column and pipeline AI notes are unhidden for released purposes.

### P6.9-A — Governance page
Model: Opus 5.5 (high) · Size: M · Depends on: P6.8-A, A2-A · Review: security-reviewer
Design: `prototype/governance.html` tabs Policy · Decision points & trust levels · Budgets & spend · Kill switches · Incidents · Audit & replay · Evaluation (R3).
Do: `/governance` reading the real sources: model/provider allowlist and data-class flags (ADR 0008/0009 settings), decision points with the levels ADR 0008 permits, budgets and spend from `llm_invocations`/`agent_runs`, kill switches (global read-only, workspace toggles where allowed), validator rejections and fallbacks as incidents, ledger query and replay of recorded runs, R3 results; policy changes are decision records approved by a capability holder. Tabs without a backend stay hidden.
Verify: capability tests (only approvers change policy); Playwright toggle workspace kill switch → assistant reports off.
