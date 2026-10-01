# Phase 5 — Verify & Improve, and Phase 6 — Internal agents (NOOA) + Jev shadow

## Phase 5

### P5.1-A — Investigation check library
Model: Opus 5.5 (xhigh) · Size: L (split by check group) · Depends on: P2.3-A · Review: ml-correctness-reviewer
Read: `services/pipeline_verifier.py`, `engine/modeling/leakage_auditor.py`, `docs/DCLAB_ADAPTIVE_MODEL_BUILDER.md:222-258`.
Do: `engine/investigate/` pure checks returning typed `Finding(check, severity, evidence: metrics/columns, recommendation_kind)`: target leakage, train/holdout contamination, duplicates across splits, imbalance, calibration (ECE/Brier), overfit gap (train vs CV), fold instability, subgroup performance gaps, feature drift train→holdout (PSI/KS), temporal shift when a time column exists, multicollinearity, missingness pattern shift. Service runs them post-run (job handler) and stores findings; `/v1` + MCP `get_evidence` return them.
Verify: each check has a synthetic positive and negative test.

### P5.2-A — Objective optimizer
Model: Opus 5.5 (xhigh) · Size: M · Depends on: P1.4-B
Do: constraint-aware threshold search and cost-weighted objective on out-of-fold predictions; report Pareto points (precision/recall at thresholds); store chosen operating point with reason.
Verify: synthetic tests with known optimum; holdout never read.

### P5.3-A — ADR 0007: improve loop
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P2.4-A, P5.1-A
Decide: typed action space (from `ExperimentChange`), proposer interface (deterministic now, agent later), budgets (iterations, wall time, compute cost), stop rules (constraint met, no improvement in k iterations, budget, instability), selection rule across iterations (CV only, same SplitPlan), when the holdout is evaluated (once, for the accepted final iteration), auditing (each iteration = child experiment + decision record), cancellation.

### P5.4-A — Improve loop engine
Model: Opus 5.5 (xhigh) · Size: L · Depends on: P5.3-A · Review: ml-correctness-reviewer
Do: `improve_runs` table (goal, constraints, budget, status, best_experiment_id) via `/new-migration`; job handler `labs.improve` iterating: diagnose (P5.1 findings) → deterministic proposer chooses next `ExperimentChange` (rule table: imbalance → class weights; constraint gap → threshold; underfit → family/tuning) → branch → evaluate → stop check. Emits events per iteration.
Verify: loop tests with fake engine for control flow + one real small dataset; budget and cancel tests.

### P5.5-A — Improve UX and MCP tool
Model: Sonnet 5.5 (medium) · Size: M · Depends on: P5.4-A
Do: Studio loop timeline (iteration cards: changes, reason, metric deltas, cost, Accept/Reject/Compare/Modify); `/v1/improve-runs`; MCP `improve(goal, constraints, budget)`, `get_improve_run`.

### R2-A — Improve-loop benchmark
Model: Opus 5.5 (high) · Size: S · Depends on: P5.4-A, R1-A
Do: run the loop on R1 tasks with generated constraints; report satisfaction rate, iterations, cost; add to nightly.

## Phase 6

### P6.1-A — ADR 0008: agent runtime
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P5.3-A · Review: security-reviewer
Read: `docs/mvp/AGENTS_NOOA_JEV.md`, NOOA docs (`docs/tour.md`, `docs/concepts/strategies.md`, `docs/concepts/safety.md` in `github.com/NVIDIA-NeMo/labs-OO-Agents` — re-verify current version), `docs/agentic-program/AGENT_FIRST_MVP_ARCHITECTURE.md` §4.
Decide: exact NOOA version pin, LiteLLM pin, ContextEnvelope schema and redaction rules, budgets, event mapping, proposal state machine, data-policy flags for LLM exposure, kill switches.

### P6.2-A — Agent persistence and LLM gateway
Model: Opus 5.5 (high) · Size: L · Depends on: P6.1-A · Review: db-migration-reviewer, security-reviewer
Do: tables `agent_runs`, `agent_events` (append-only), `agent_proposals`, `semantic_decision_answers` per AGENTS_NOOA_JEV.md §6; add FK for `llm_invocations.agent_run_id`; refactor `engine/lab/llm_client.py` + `services/openai_provider.py` into `app/agents/gateway/` with a provider interface, DB-backed cache keyed by input digest, uniform ledger writes.
Verify: migration tests; existing LLM decision tests unchanged.

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
Do: `/v1/agent-runs`, `/v1/proposals/{id}/accept|reject`; acceptance calls the normal command service and writes a decision record; MCP tools `request_agent_review`, `list_proposals`; Studio proposal panel with citations and "untrusted rationale" labeling.

### P6.7-A — Jev shadow mode
Model: Opus 5.5 (high) · Size: M · Depends on: P6.2-A · Review: security-reviewer
Do: `app/agents/semantic/{port,deterministic,typesafe_jev,releases}.py`; `typesafe-sdk` pinned in `agents` extra; pinned `jev-1.13.0`; 5 purposes from AGENTS_NOOA_JEV.md §5 in **shadow** (answers logged beside deterministic answer, no behavior change); 1 s timeout + circuit breaker; metadata-only state unless workspace policy allows sample values.
Verify: tests with fake Jev; no network in CI; disabled flag = zero calls.

### P6.8-A — Agent and Jev release gate (R3)
Model: **Fable 5.1** (high) · Size: M · Depends on: P6.5-A, P6.7-A
Do: evaluation corpus from R1 datasets + labeled decisions; measure citation validity, validator rejection rate, usefulness vs deterministic (ablation), cost/latency; Jev calibration (reliability, ECE, precision in acting band) per purpose; write release decisions into STATUS.md (`SHADOW_ALLOWED` / `ADVISORY_ALLOWED` / `DISABLED_PENDING_EVIDENCE` / `REJECTED`).
