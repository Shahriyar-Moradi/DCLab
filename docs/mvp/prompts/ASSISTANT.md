# Track A — The in-app assistant (agentic chat in Studio)

The assistant is the chat panel on every Studio project page. A user asks in
plain language ("why did model B win?", "what is stale?", "try class weights",
"score this file") and the assistant answers from the project's real evidence,
using tools. It can **propose** actions (run, branch, predict, record a decision)
as confirm cards; the user clicks to execute them.

It is the third face of one agent layer, not a new system:

| Who drives | How | Phase |
| --- | --- | --- |
| External agents (Claude Code, Cursor, Codex) | MCP → SDK → `/v1` | 3 (done) |
| **The in-app assistant** (Studio chat) | assistant service → same tool catalog → services | **4 → 8 (this track)** |
| Internal agents (NOOA classes), improve loop | proposal service → same services | 5, 6 |

## Fixed rules (ADR 0009 may refine, not loosen them)

1. **One tool catalog.** The assistant and the MCP server expose the same tools,
   with the same names, inputs and output shaping (holdout-blind, bounded, no raw
   rows). The catalog is code-owned (`app/agents/tools/`) and exported to
   `contracts/agent_tools.json`; a test fails if MCP and the catalog disagree.
2. **Read tools run, write tools propose.** Read tools execute in-process with the
   signed-in user's workspace and capabilities. Write tools are never executed by
   the assistant: they create a proposal shown as a confirm card; the click goes
   through the normal `/v1` command path (Idempotency-Key) and the decision record
   says `proposed_by=assistant`, `actor=<human>`.
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

### A1-A — ADR 0009: in-app assistant
Model: **Fable 5.1** (high) · Size: M (design only) · Depends on: P3.4-A · Review: security-reviewer
Read: this file, `docs/mvp/AGENTS_NOOA_JEV.md`, `packages/dclab_mcp/dclab_mcp/server.py`, `engine/lab/llm_client.py`, `services/openai_provider.py`, ADR 0001 (BFF).
Decide: tool catalog shape and export; `AssistantStep` schema; loop budgets and stop rules; whether each step uses NOOA Predict or the gateway directly; context envelope (page context is a hint, the server re-resolves); redaction and data-policy flags; SSE through the BFF; persistence (moves the `agent_runs`/`agent_events`/`agent_proposals` schema from P6.2 forward); kill switches (global, per workspace); LLM-off behaviour.
Done when: ADR accepted by the founder; ARCHITECTURE.md and AGENTS_NOOA_JEV.md updated to cite it.

### A2-A — LLM gateway and agent persistence (moved forward from P6.2)
Model: Opus 5.5 (high) · Size: L · Depends on: A1-A · Review: db-migration-reviewer, security-reviewer
Do: `app/agents/gateway/` with a provider interface (refactor `engine/lab/llm_client.py` + `services/openai_provider.py` into it), DB-backed cache keyed by input digest, uniform `llm_invocations` writes, budgets; migration (via `/new-migration`) for `agent_runs`, `agent_events` (append-only), `agent_proposals` per AGENTS_NOOA_JEV.md §6 with `kind` (`assistant` now, NOOA classes in Phase 6); FK `llm_invocations.agent_run_id`.
Verify: migration tests; existing LLM decision tests unchanged; gateway tests with a fake provider (no network).

### A2-B — Assistant service, read-only
Model: Opus 5.5 (xhigh) · Size: L · Depends on: A2-A · Review: security-reviewer, ml-correctness-reviewer
Do: `app/agents/tools/` catalog with the 8 read tools MCP already has (`inspect_project`, `inspect_dataset`, `get_experiment`, `compare_experiments`, `get_experiment_code`, `get_evidence`, `list_decisions`, `get_model`), a new `get_impact` (over `/v1/nodes/{kind}/{id}/impact`, also added to MCP), plus `get_findings`/`get_model_card` once P4.10/P4.11 land; export `contracts/agent_tools.json` and a contract test against `dclab_mcp`; assistant loop service; `POST /v1/assistant/threads`, `POST /v1/assistant/threads/{id}/messages` streaming SSE; `GET` thread history; fake LLM for all tests.
Don't: execute any write; send raw rows or holdout metrics to the LLM.
Verify: loop tests (budget stop, invalid step rejected, unknown tool rejected, fabricated citation rejected), tenant isolation (thread of workspace A unreadable from B), LLM-off path.

### A3-UI — Assistant panel in Studio
Model: Sonnet 5.5 (medium) · Size: M · Depends on: A2-B, P4.1-A
Do: right-side panel on `/projects/[id]/**` (toggle + keyboard shortcut), streaming messages, tool-step chips ("read 3 experiments"), citations as links into the graph/inspectors, "LLM used" label, page context sent with each message (current tab, selected node); BFF streaming passthrough; LLM-off mode shows quick actions ("Explain this experiment", "What is stale?", "Compare with champion") rendered from deterministic templates.
Verify: tsc/lint/build; Playwright with the fake LLM: ask → streamed answer → click citation opens inspector.

### A4-A — Action proposals and confirm cards
Model: Opus 5.5 (high) · Size: M · Depends on: A3-UI, P4.4-A · Review: security-reviewer
Do: write tools in the catalog (`run_experiment`, `branch_experiment`, `predict`, `record_decision`, `propose_problem_spec`) become `agent_proposals`; `POST /v1/assistant/proposals/{id}/confirm|dismiss`; confirm executes through the existing command service with an Idempotency-Key and writes the decision record (`proposed_by=assistant`); confirm cards in the panel show the exact change set and estimated cost; proposals expire.
Verify: a proposal can never execute without a confirm by a user who holds the capability; replayed confirm is idempotent; Playwright: "try class weights" → card → confirm → new branched experiment appears in the graph.

### A4-B — Golden conversations
Model: Sonnet 5.5 (medium) · Size: S · Depends on: A4-A
Do: 10 golden conversations (explain winner, what is stale, why a feature was excluded, compare, branch proposal, score file, leakage question, refusal to reveal holdout, injection in a column name, LLM-off) as tests with the fake LLM; an opt-in `scripts/assistant_live_eval.py` against a real provider, run by hand, results recorded in STATUS.md.

### A5-A — Assistant in Phase 5 (verify and improve)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P5.5-A, A4-A
Do: catalog gains `improve` (proposal), `get_improve_run`, `get_operating_points`; the panel can start an improve loop from a goal sentence ("recall with precision ≥ 70 %") as a confirm card and narrate iterations from events; MCP gets the same tools via the catalog.

### A6-A — Internal agents as assistant skills (Phase 6)
Model: Opus 5.5 (high) · Size: M · Depends on: P6.6-A, A4-A · Review: security-reviewer
Do: the assistant can call `request_agent_review` (DatasetInvestigator, ExperimentPlanner, ExperimentCritic) and shows the returned proposals in the same proposal list as P6.6; only agents whose P6.8 release decision allows it are callable; one proposal model, one review flow.

### A7-A — Operations tools (Phase 7)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P7.5-A, A4-A
Do: catalog gains `list_releases`, `get_drift`, and proposals `create_release`, `batch_predict`, `rollback_champion`; panel explains a drift window from the drift finding.

### A8-A — Assistant metering and limits (Phase 8)
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P8.4-A
Do: assistant tokens and cost flow into `usage_records`; per-workspace monthly cap and kill switch enforced before each step; usage page shows assistant spend.
