# Track A — The in-app assistant (agentic chat in Studio)

The assistant is the **Lab** page of every Studio project (design:
`../design/prototype/lab.html`) plus a compact panel on the other project pages. A user asks in
plain language ("why did model B win?", "what is stale?", "try class weights",
"score this file") and the assistant answers from the project's real evidence,
using tools. It can **propose** actions (run, branch, predict, record a decision)
as confirm cards; the user clicks to execute them.

It is the third face of one agent layer, not a new system:

| Who drives | How | Phase |
| --- | --- | --- |
| External agents (Claude Code, Cursor, Codex) | MCP → SDK → `/v1` | 3 (done) |
| **The in-app assistant** (Studio chat) = the **lead agent** | lead-agent runtime (P6.3-B) → same tool catalog → services | backend 6; screens 4 (Stage 4) → 8 (this track) |
| Specialist agents (NOOA classes), improve loop | proposal service → same services | 6, 5 |

**Merged with the hybrid AI order (2026-10-04, ROADMAP.md).** The assistant and
the hybrid plan's lead agent ("AI data scientist") are one runtime. Its backend
is built once in Phase 6: A1-A is part of P6.1-A (ADR 0009), A2-A is P6.2-A
(tables) + P6.2-B (gateway), A2-B is P6.10-A (tool catalog) + P6.3-B (loop and
threads API). This track keeps the screens and the per-phase tool additions.

## Fixed rules (ADR 0009 may refine, not loosen them)

1. **One tool catalog.** The assistant and the MCP server expose the same tools,
   with the same names, inputs and output shaping (holdout-blind, bounded, no raw
   rows). The catalog is code-owned (`app/agents/tools/`) and exported to
   `contracts/agent_tools.json`; a test fails if MCP and the catalog disagree.
2. **Read tools run, write tools propose.** Read tools execute in-process with the
   signed-in user's workspace and capabilities. Write tools are never executed by
   the assistant: they create a proposal shown as a confirm card; the click goes
   through the normal `/v1` command path (Idempotency-Key) and the decision record
   says `proposed_by=assistant`, `actor=<human>`. Proposals use the one
   `agent_proposals` model and P6.6 accept/reject routes. Only a decision point
   that ADR 0008 and R3 raise to L2+ may auto-apply (with revert, as a record);
   chat write tools start at L1.
3. **Typed, bounded loop.** Each step is one structured LLM call through the LLM
   gateway returning a validated `AssistantStep` (answer | tool calls from the
   allowlist). DCLab runs the loop with budgets (steps, tokens, wall time). No
   generated code is executed. This keeps the NOOA rule "Predict only".
4. **Logged.** Every LLM call is a `llm_invocations` row; each thread is an
   `agent_runs` row of kind `assistant`, with append-only `agent_events`.
5. **Works with the LLM off.** With `assistant_enabled=false` (the default) or the
   provider down, the panel shows deterministic quick actions and template
   explanations built from findings and the model card. No feature depends on it.
6. **Untrusted text stays untrusted.** Column names, values and file content are
   data, labelled as such in context; the worst a prompt injection can do is
   produce a bad suggestion that a human must still confirm.
7. **Grounded answers.** Every claim about the project cites a node (experiment,
   finding, decision) that the UI renders as a link; the validator rejects
   citations to nodes that do not exist.

## Prompts

### A1-A, A2-A, A2-B — absorbed into Phase 6
A1-A (ADR 0009) is part of P6.1-A; A2-A (gateway and agent tables) is P6.2-A +
P6.2-B; A2-B (tool catalog, read-only assistant loop, threads API with SSE) is
P6.10-A + P6.3-B. See `PHASE_5_6.md`. Do not run them as separate prompts.

### A2-UI — Agent runs and Tool registry tabs
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P4.8-UI, P6.10-A, P6.3-B
Design: `prototype/agents.html` Agent runs (run id, agent, subject, steps/calls, tokens, cost, outcome, trace as `EventList`) and Tool registry (tool, effect, capability, level, validator, MCP ✓, assistant ✓; the "no such tool exists" row lists the forbidden operations).
Do: Agents & tools page gets both tabs, from `GET /v1/agent-runs` and `contracts/agent_tools.json`; replay link per run (P6.11-A).
Verify: tsc/lint/build; component tests.

### A3-UI — Lab page: lead-agent chat and assistant panel
Model: Sonnet 5.5 (high) · Size: M · Depends on: P6.3-B, P4.1-A, P4.1-B, checkpoint G6 · Review: security-reviewer
Design: `prototype/lab.html`: session header (model, budget, step limit, data class, tools available / needing approval), user and agent messages with "N tool calls · time · cost · run id", proposal chips with `Level` badge and the rule's answer beside the AI's, live run card ("E1 · running · stage 6 of 10") linking to the Pipeline page, right column "This run at a glance" + "Agent capabilities in this session" + "Sessions"; "Use forms instead" opens the P4.1-B wizard.
Do: the user attaches data and states a goal; the lead agent uploads, profiles, proposes the spec, split and budget, runs the experiment and reports results through tools, in the background (job + streamed `agent_events`); each AI decision is shown with its trust level, evidence and the rule's answer; L1 decisions render as accept/reject chips; cost and step budget shown per turn; budget exhaustion or "AI off" switches to the P4.1-B forms. `/projects/[id]/lab` full page + compact panel (toggle + shortcut) on other project pages; streaming messages over the BFF; citations as links into Graph/Experiments/Decisions; "LLM used" label; page context sent with each message; every figure in the header and summary comes from the thread/run API. LLM-off mode: quick actions ("Explain this experiment", "What is stale?", "Compare with champion") from deterministic templates. Proposal chips show the level the backend reports.
Verify: tsc/lint/build; Playwright with the fake LLM: ask → streamed answer → click citation opens inspector; churn-style goal → decisions → run → results; budget exhaustion falls back to forms; component tests for decision chips.

### A4-A — Action proposals and confirm cards
Model: Opus 5.5 (high) · Size: M · Depends on: A3-UI, P4.4-A, P6.6-A · Review: security-reviewer
Design: `prototype/lab.html` proposal chips and "Accept all 4 and run" / "Review each"; confirmed or pending proposals also appear in the Inbox (P4.16).
Do: write tools in the catalog (`run_experiment`, `branch_experiment`, `predict`, `record_decision`, `propose_problem_spec`) already become `agent_proposals` (P6.3-B); confirm cards call `POST /v1/proposals/{id}/accept|reject` (P6.6-A, no assistant-only route); accept executes through the existing command service with an Idempotency-Key and writes the decision record (`proposed_by=assistant`); confirm cards in the panel show the exact change set and estimated cost; proposals expire.
Verify: a proposal can never execute without a confirm by a user who holds the capability; replayed confirm is idempotent; Playwright: "try class weights" → card → confirm → new branched experiment appears in the graph.

### A4-B — Golden conversations
Model: Sonnet 5.5 (medium) · Size: S · Depends on: A4-A
Do: extending the P6.10-B fixtures, 10 golden conversations (explain winner, what is stale, why a feature was excluded, compare, branch proposal, score file, leakage question, refusal to reveal holdout, injection in a column name, LLM-off) as tests with the fake LLM; an opt-in `scripts/assistant_live_eval.py` against a real provider, run by hand, results recorded in STATUS.md.

### A5-A — Assistant in Phase 5 (verify and improve)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P5.5-A, A4-A
Do: catalog gains `improve` (proposal), `get_improve_run`, `get_operating_points`; the panel can start an improve loop from a goal sentence ("recall with precision ≥ 70 %") as a confirm card and narrate iterations from events; MCP gets the same tools via the catalog.

### A6-A — Specialist agents as assistant skills (Phase 4 Stage 4)
Model: Opus 5.5 (high) · Size: M · Depends on: P6.6-A, P6.8-A, A4-A · Review: security-reviewer (Phase 4 Stage 4, since Phase 6 is done by then)
Do: the assistant can call `request_agent_review` (DatasetInvestigator, ExperimentPlanner, ExperimentCritic) and shows the returned proposals in the same proposal list as P6.6; only agents whose P6.8 trust level allows it are callable; one proposal model, one review flow.

### A7-A — Operations tools (Phase 7)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P7.5-A, A4-A
Do: catalog gains `list_releases`, `get_drift`, and proposals `create_release`, `batch_predict`, `rollback_champion`; panel explains a drift window from the drift finding.

### A8-A — Assistant metering and limits (Phase 8)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P8.4-A
Do: assistant tokens and cost flow into `usage_records`; per-workspace monthly cap and kill switch enforced before each step; usage page shows assistant spend.
