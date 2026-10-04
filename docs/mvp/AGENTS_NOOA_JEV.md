# Agents in DCLab: NVIDIA NOOA and TypeSafe Jev

Status: CURRENT design (2026-10-01; hybrid AI model and order change 2026-10-02),
built on verified research of `NVIDIA-NeMo/labs-OO-Agents` at commit `2598f1f5`
(PyPI `nooa` 0.0.10) and the TypeSafe docs (`jev-1.13.0`, `typesafe-sdk` 0.7.x).
Supersedes the delivery order of
`docs/agentic-program/JEV_NOOA_INTEGRATION_ARCHITECTURE.md`; its ownership rules
remain valid. Phase 6 (this document's scope) runs after Phase 4 Stage 1 (use and trust)
and before the Studio stages of Phase 4; see ROADMAP § Hybrid AI model. ADR 0008 (P6.1-A) turns §1b into binding decisions.

## 1. Where agents sit

```
User (Studio Lab chat = in-app assistant) ──► Lead agent ── shared tool catalog ──► read: services · write: proposal / confirm card ──► /v1
                                                                  (backend P6.3-B; screens Phase 4 Stage 4, Track A)
External agents (Claude Code, Cursor) ──MCP──► /v1 ──► services                    (Phase 3, done)
Specialist agents (NOOA Predict) ── typed proposals ──► proposal service ──► services (Phase 6)
Typed semantic judgments (Jev) ── answers + deterministic policy ──► decision points (Phase 6)
```

The authority model is unchanged from `AGENT_FIRST_MVP_ARCHITECTURE.md` §4:
agents observe, explain and **propose**; deterministic services validate and
execute; a human (or an explicit policy) accepts. "Higher autonomy changes who
initiates a command, not who validates or executes it."

The in-app assistant (ADR 0009, `prompts/ASSISTANT.md`) is not a second
runtime: it **is** the lead agent — a DCLab-owned bounded loop of typed single
LLM calls through the same gateway, using the same tool catalog as MCP and the
same `agent_runs` / `agent_proposals` tables (all built once in Phase 6: P6.2-A,
P6.2-B, P6.10-A, P6.3-B).

The internal runtime set is reduced from four (LangGraph, Deep Agents, OpenAI
Agents, NOOA) to **one: NOOA** for specialist agents, plus deterministic fakes,
plus the lead agent's bounded tool loop (P6.3-B; ADR 0008 decides whether NOOA or
the LLM gateway hosts it). LangGraph/Deep Agents/OpenAI Agents are dropped from
the MVP. External agents need no runtime at all — they use MCP.

## 1b. Hybrid decision model

AI and deterministic code cover each other. Five mechanisms:

1. **Tools are the safety boundary.** The lead agent and specialists act only
   through the same tools as the MCP server (`inspect_dataset`,
   `propose_problem_spec`, `run_experiment`, `compare_experiments`,
   `branch_experiment`, later `propose_release`). Every tool validates its input
   in deterministic code; no tool reads the holdout, picks the winner, computes a
   metric or builds a split.
2. **The state graph is the agents' memory.** Agents read nodes and decision
   records to know what was tried, what worked and what is stale; chat history is
   not the source of truth.
3. **Jev does the many small typed judgments** (is this an ID, a date, a leak
   suspect, what did the user mean) at ~100 ms; LLM agents do reasoning and
   planning.
4. **Every AI action is a proposal with evidence** and a decision record (actor,
   rationale, cited evidence, rule answer, value used, revert target). Low-risk
   decisions are applied automatically and reversibly; higher-risk ones wait.
5. **Trust levels per decision point**, earned through R3:

| Level | Meaning | Typical decision points |
| --- | --- | --- |
| L0 Shadow | AI answers and is logged; the rule decides | every point at first |
| L1 Advise | AI answer shown with evidence; a person accepts | spec, split strategy, column exclusion, release |
| L2 Auto + revert | AI value applied when the validator accepts it; one-click revert | column roles, model families, next improve action, retrain |
| L3 Auto + notify | agent acts under a workspace policy; person notified | monitoring status, drift diagnosis, rollback past a threshold |

Placement patterns around each important deterministic step:

| Pattern | AI role | Examples |
| --- | --- | --- |
| AI-before | proposes the inputs of a deterministic step | target and spec, column roles, leakage suspects, split strategy, families/budget, next improve action |
| AI-after | reviews the outputs of a deterministic step | Critic on every completed run, drift diagnosis, release notes |
| Cross-check | both answer; agreement applies, disagreement goes to L1 review | Jev vs schema inference, agent vs rule improve proposer |

Never AI: model training, metric calculation, final model selection on
validation data, holdout scoring, the splits themselves. Every path works with
AI disabled.

## 2. NOOA — verified facts that shape the design

| Fact (source in the NOOA repo) | Consequence for DCLab |
| --- | --- |
| Agents are Python classes; `async def` methods with body `...` are LLM "generation methods"; docstring = prompt, return annotation = typed contract (`src/nooa/agent.py`, `docs/tour.md`) | Agent classes map 1:1 to DCLab roles with Pydantic proposal types |
| `PredictStrategy`: one structured LLM call, validated output, no code execution | **The only strategy allowed in the MVP** |
| `CodeActStrategy` is the **default** and runs generated Python **in-process** (`config/strategy_config.py:85`); its sandbox backend is Linux/fork-only and its broker bypasses visibility (`runtime/sandbox/executor.py`, open issue #173) | CodeAct is banned in API/worker processes; a CI test fails if any DCLab agent method resolves to a CodeAct strategy. A CodeAct lane needs its own container (Phase 9) |
| `max_iterations` defaults to unbounded (`strategy_config.py:40`); no cost budget | DCLab enforces budgets itself (max calls, tokens, wall time) around every run |
| LLM access via LiteLLM; custom `api_base`, retry and timeout configs | Provider/model resolved by DCLab settings; pin `litellm` to a reviewed version (supply-chain history) |
| Instance history accumulates; concurrent calls serialize | One agent instance per AgentRun, never reused across requests or tenants |
| Event hooks (`event_manager.on/intercept`), OTel/JSONL tracing; tracing auto-exports to a viewer on :5001 | Map events into `agent_events`; configure exporters explicitly; disable auto viewer |
| Alpha, "research software", ~weekly breaking changes | Pin exact version; only `app/agents/runtime/nooa_runtime.py` imports `nooa` |

## 3. NOOA in DCLab — software design

```
apps/api/app/agents/
  contracts.py        # Pydantic: ContextEnvelope, AgentRunSpec, proposals, Finding, Citation
  registry.py         # code-owned agent catalog: key → class, version, allowed proposal types, budgets
  validators.py       # deterministic proposal validators (columns exist, metrics exist, actions allowlisted)
  governance/         # POLICY LAYER (data, not code paths)
    policy.py         #   effective_policy(workspace): platform default ⊕ workspace ai_policies, with caps
    levels.py         #   decision-point registry + trust levels L0–L3, promotion/demotion rules
    incidents.py      #   ai_incidents: open, auto-demote, resolve
    console.py        #   read model for /v1/governance
  gateway/            # THE ONLY DOOR TO ANY MODEL
    contract.py       #   complete(request) for LLMs, decide(...) for Jev
    router.py         #   provider/model from policy + agent role; allowlist
    redaction.py      #   strip values above the workspace data class; mark untrusted text
    budget.py         #   reserve → settle against workspace_llm_budgets
    cache.py          #   digest(prompt release, redacted input) → answer
    limits.py         #   rate limits, circuit breakers
    ledger.py         #   one llm_invocations row per call
    switches.py       #   kill switches: global / workspace / agent / purpose
    providers/        #   the ONLY modules importing OpenAI / LiteLLM / TypeSafe SDKs (+ fake)
  harness/            # THE ONLY WAY TO RUN AN AGENT
    service.py        #   AgentService.run(spec): the lifecycle below; job handler agents.run
    context.py        #   ContextEnvelope from read-only services (metadata, aggregates, no raw rows)
    hooks.py          #   pre-run / pre-call / pre-tool / post-tool / post-run; typed effects
    tools/registry.py #   one tool registry shared with packages/dclab_mcp (schema + capability + validator)
    validation.py     #   Pydantic + deterministic validators → proposed | rejected_by_validator
    recorder.py       #   agent_events: prompt release, input digest, tool calls, output
    replay.py         #   re-run a recorded run offline; asserts identical sequence and output
  runtime/
    base.py           # AgentRuntime protocol: run(spec, envelope, tools) -> RuntimeResult
    fake_runtime.py   # deterministic, used by all tests and when NOOA is disabled
    nooa_runtime.py   # the ONLY module importing nooa; PredictStrategy only; LLM = gateway adapter
    lead_runtime.py   # bounded multi-step tool loop for the lead agent
  semantic/           # Jev port, deterministic policy, releases (see §5)
  classes/
    dataset_investigator.py
    experiment_planner.py
    experiment_critic.py
    improvement_hypothesis.py
    lead_agent.py
    ops_agent.py
```

Harness lifecycle (every agent run, no exceptions):
build envelope → redact → reserve budget → run runtime → each tool call: registry
lookup → capability check → validator → service → record → validate output →
persist proposals/events → settle usage → emit eval sample. CI rules: no provider
SDK import outside `gateway/providers/`; no runtime invoked outside
`AgentService.run`; no registry tool reads holdout rows, selects a winner or
computes a metric.

Agent classes (all PredictStrategy, all return proposals):

| Class | Input (ContextEnvelope) | Output proposal | Deterministic validator | Phase |
| --- | --- | --- | --- | --- |
| DatasetInvestigatorAgent | profile, column stats, deterministic findings, target candidates | `DatasetInvestigationProposal`: prioritized findings, questions for the user, semantic-role suggestions | every column cited exists; no new statistics accepted unless they match the profile | 6 |
| ExperimentPlannerAgent | ProblemSpec draft, profile summary, allowed families/budgets | `ExperimentPlanProposal`: metric + rationale, split strategy, candidate families, budget | metric/split/families from code allowlists; split must pass holdout planner rules | 6 |
| ExperimentCriticAgent | experiment evidence: CV/holdout metrics, folds, findings, verification results | `ExperimentReviewProposal`: findings with metric citations, verdict promote/needs_work/reject | cited metrics must exist in `evaluation_metrics`; verdict never auto-applies | 6 |
| ImprovementHypothesisAgent | constraint gap, diagnostics, prior iterations | `ImprovementActionProposal`: one action from the typed action space (class weight, threshold, family, hyperparameter range, allowlisted feature transform) | action type and ranges allowlisted; budget remaining; never touches holdout | 6 (plugs into Phase 5 loop) |
| LeadAgent ("AI data scientist") | user goal + data, graph summary, decision-point levels, tool registry | tool calls (bounded loop) and L1 proposals | every tool call validated by the service it calls; step/token/time/cost bounds | 6 (runtime P6.3-B), 4 (chat P4.7) |
| OpsAgent | monitoring windows, findings, champion evidence | retrain branch, release proposal, rollback | same tools; workspace autonomy policy; release never above L1 by default | 7 (P7.6) |

Sketch (API names verified in NOOA source):

```python
from nooa import Agent, hidden, strategy
from nooa.strategies import PredictStrategy

class ExperimentCriticAgent(Agent, llm=gateway_llm()):
    """You review ML experiments. You only propose; you never change state.
    Cite metric names exactly as given. Say 'insufficient evidence' when unsure."""

    @strategy(PredictStrategy())
    async def review(self, evidence: ExperimentEvidence) -> ExperimentReviewProposal:
        """Assess leakage, overfitting, metric validity and baseline margin."""
        ...
```

`AgentService.run()` (in the worker, as an `MlJob` handler `agents.run`; harness detail above):
build envelope → digest it → create `agent_runs` row → instantiate one agent →
call the method under DCLab budget/timeout → map NOOA events to `agent_events`
→ validate output with the deterministic validator → store `agent_proposals`
(status `proposed` or `rejected_by_validator`) → settle usage into
`llm_invocations`. Accepting a proposal calls the normal command service and
writes a `project_decision_records` row; agents never call commands directly.

## 4. Jev — verified facts that shape the design

| Fact (docs.typesafe.ai) | Consequence |
| --- | --- |
| Hosted inference only; **no per-account fine-tuning**, no self-host (`models.md`) | "Train your own Jev" is not possible. Reframe: users customize Jev questions and DCLab calibrates thresholds on their labels; custom trained decision models are a DCLab ModelType (classical now, SLM in Phase 9) |
| Choice (≤255 options, probabilities, confidence), Score (2–10 levels), Noul (P(yes)) | Map to `SemanticDecisionPort` question types |
| `jev-latest` alias moves; pinned `jev-1.13.0` | Pin the version per release; re-evaluate on bump |
| ~100 ms typical, $0.042 / 1M input tokens, 40 req/s; no SLA, "as is" MCA | Always optional, circuit breaker, 1 s timeout, deterministic fallback |
| Weak at counting/arithmetic/dates; vulnerable to prompt injection in state (`model-jaggedness/jev-1.13.md`) | Pass computed numbers as categories; never ask Jev numeric questions; treat column names/values as untrusted text |
| Retention "as long as necessary", region undocumented, ZDR enterprise-only | No customer raw values sent by default; workspace policy flag required for sample values; metadata-only otherwise |
| Announced 2026-09-15, SDK had 2 breaking releases in 2 weeks | Shadow-only until evidence; adapter isolates SDK |

## 5. Jev in DCLab — design

`apps/api/app/agents/semantic/`: `port.py` (`SemanticDecisionPort.decide(purpose,
release, state, questions) -> answers`), `deterministic.py` (default backend),
`typesafe_jev.py` (only module importing `typesafe_sdk`), `releases.py`
(code-owned question releases: purpose, version, questions, thresholds, allowed
data class), `policy.py` (deterministic agreement policy).

**Jev is used deterministically.** The model answer is one input to fixed code:
pinned release; thresholds per purpose owned in `releases.py`; answers cached by
digest of (purpose, release, state), so the same input always gives the same
answer and runs are reproducible from the cache; and a fixed agreement table:

| Jev vs rule | Confidence in acting band | Outcome at L2 | Outcome at L0/L1 |
| --- | --- | --- | --- |
| agree | yes | apply (same value) | rule value; logged |
| disagree | yes | Jev value if the validator accepts it, else rule; flagged in inbox | rule value + L1 review item |
| any | no (abstain band) | rule value | rule value |
| timeout / breaker open / over budget | — | rule value | rule value |

Purposes and their maximum trust level:

| Purpose | Question | State sent | Deterministic rule it pairs with | Max level |
| --- | --- | --- | --- | --- |
| `column.is_identifier` | Noul | column name, dtype, uniqueness band (e.g. "unique ratio > 0.99"), name tokens | `schema_inference` identifier rule | L2 after gate |
| `column.semantic_role` | Choice: numeric / categorical code / identifier / datetime / free text / other | name, dtype, cardinality band, value-pattern band | `split_column_roles` + column-type decision | L2 after gate |
| `feature.leakage_suspect` | Noul | column name + target name + availability text | leakage auditor name rules | L1: adds a review flag, never excludes |
| `command.intent_route` | Choice over Studio/MCP capabilities + "other" | user's text command | none (UX router) | L1: route with confirmation |
| `proposal.completeness` | Score (0–4) | agent proposal summary | none | L0: display only |

Trust-level gate per purpose (recorded in STATUS.md as `SHADOW_ALLOWED`,
`ADVISORY_ALLOWED`, `DISABLED_PENDING_EVIDENCE` or `REJECTED`): ≥ 300 labeled
examples (benchmark datasets + reviewed user decisions), reliability diagram and
ECE, precision ≥ 98 % inside the acting band (e.g. Noul p ≥ 0.9 or ≤ 0.1,
Choice confidence ≥ 0.8), latency/cost within budget, zero policy leaks. Prohibited
forever: task type from numbers, metric reading, model selection/promotion,
automatic column exclusion, loop control.

## 5b. AI governance

Governance is the policy layer that the gateway and harness enforce. It is data
per workspace (with a code-owned platform default and caps), never a parallel
code path, and every change is a `project_decision_records` row (or a
workspace-level record for workspace policies).

| Policy area | Contents | Enforced by |
| --- | --- | --- |
| Models | allowlist per agent role (lead, specialist, Jev); default model; fallbacks | gateway router |
| Data | highest data class that may leave: metadata → aggregates → sample values → raw rows (raw never by default); untrusted-text marking | gateway redaction |
| Autonomy | trust level per decision point (≤ code cap); ops autonomy policy (auto-retrain, auto-release off, rollback threshold) | harness + decision points + Ops agent |
| Budgets | per workspace / project / run: cost, calls, tokens, wall time; alert thresholds | gateway budget + harness bounds |
| Approvals | who may accept L1 decisions, change levels, flip switches, change policy | decision record service, governance API |
| Evaluation gates | minimum R3 evidence to promote a level; auto-demotion after N validator rejections or a failed R3 run | `levels.py`, `incidents.py` |
| Audit | every call in `llm_invocations`, every run in `agent_runs/agent_events`, replayable; retention of prompts/answers | gateway ledger, harness recorder |
| Kill switches | global / workspace / agent / purpose; flip is audited; next call falls back to the rule | gateway switches |

The governance console (`/v1/governance`, Studio page in P4.9) shows the
effective policy, levels with evidence, spend vs budget, incidents and history.

## 6. Persistence (Phase 6 migrations)

| Table | Key columns | Rules |
| --- | --- | --- |
| `agent_runs` | id, workspace_id, project_id, agent_key, agent_version, runtime (`fake`/`nooa_predict`/`lead_loop`), runtime_version, model, purpose, subject_kind/subject_id, context_digest, status, limits (json), usage, cost_micros, error_code, idempotency_key, created_by, timestamps | CFK to project; unique (workspace_id, idempotency_key); one active run per (subject, agent_key) |
| `agent_events` | run_id, seq, type, bounded payload, created_at | append-only trigger; unique (run_id, seq) |
| `agent_proposals` | id, run_id, workspace_id, proposal_type, schema_version, payload (validated json), citations, validator_verdict, status (`proposed`/`rejected_by_validator`/`accepted`/`rejected`/`superseded`/`expired`), decided_by, decided_at, decision_record_id | payload immutable; only status transitions allowed |
| `semantic_decision_answers` | invocation_id → `llm_invocations`, purpose, release_version, question_key, answer, probabilities, confidence, deterministic_answer, mode (`shadow`/`advisory`), outcome, ground_truth, labeled_by | evaluation corpus for trust levels; adds `level` (L0–L3) and `policy_outcome` |
| `ai_policies` | workspace_id (NULL = platform default), version, policy (validated json: models, data class, autonomy, budgets, approvers, retention), changed_by_decision_record_id, created_at | append-only versions; effective = latest accepted; caps from code |
| `decision_point_policies` | workspace_id, decision_point_key, level, max_level (code), changed_by_decision_record_id, timestamps | one row per (workspace, decision point); a change requires a decision record |
| `prompt_releases` | agent_key, version, prompt_digest, released_at, status | code-owned prompts; every call records the release id |
| `ai_incidents` | id, workspace_id, kind (validator_rejections / eval_failure / budget / provider), subject (decision point / agent), evidence, action (auto_demote / switch_off / none), opened_at, resolved_by, resolved_at | drives auto-demotion |
| `workspace_llm_budgets` | workspace_id, period, limit_micros, spent_micros, alert_threshold | checked by the gateway before every call; over budget → rule fallback |
| `llm_invocations` (expand) | make `workflow_run_id`/`experiment_id` nullable; add `project_id`, `agent_run_id`, `provider_kind` | existing ledger stays the single usage/cost record |

## 7. Release order (since 2026-10-04)

1. Phase 2 (done): `llm_invocations` expand + `project_decision_records`.
2. Phase 3 (done): `/v1`, service tokens, SDK/CLI, MCP — the tool surface agents use.
   Phase 4 Stage 1: score new data, five trust checks, model card — the evidence
   the Critic and the assistant cite.
3. Phase 6: ADR 0008/0009 → agent + governance tables → AI
   gateway → agent harness → fake + NOOA Predict runtime → Jev at decision points
   (L0) → hybrid decision points in auto-train → Critic, Investigator, Planner →
   lead-agent runtime → proposal review API/MCP → test harness → governance
   console API → R3 and first trust levels → checkpoint G6.
4. Phase 4 Stages 2–5: agent-first Studio (Lab chat with the lead agent as the
   in-app assistant, pipeline view with evidence, inbox, graph, home, governance).
5. Phase 5: feature-leakage contract → checks → improve loop with rule and agent
   proposers (P6.5) → R2 agent vs rule.
6. Phase 7: release, monitoring and the Ops agent (agentic MLOps).
7. Phase 9 (only if evidence shows value, and after P5.0): NOOA CodeAct lane in a
   separate sandbox container for feature-engineering proposals whose code is
   then re-executed by the deterministic engine under CV.
