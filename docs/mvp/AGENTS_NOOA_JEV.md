# Agents in DCLab: NVIDIA NOOA and TypeSafe Jev

Status: CURRENT overview (2026-10-01; hybrid AI model and order change
2026-10-02; aligned with ADR 0008/0009 on 2026-10-04). External facts re-verified
2026-10-04: `NVIDIA-NeMo/labs-OO-Agents` main `564a3401` (PyPI `nooa` 0.0.10,
depends on `litellm>=1.97.0`; LiteLLM stable 1.104.0), TypeSafe `jev-1.13.0`
with `typesafe-sdk` 0.7.2 (sources in ADR 0009 §9). Supersedes the delivery
order of `docs/agentic-program/JEV_NOOA_INTEGRATION_ARCHITECTURE.md`; its
ownership rules remain valid. Phase 6 (this document's scope) runs after Phase 4
Stage 1 (use and trust) and before the Studio stages of Phase 4; see ROADMAP
§ Hybrid AI model.

**Binding decisions live in the ADRs, not here:**
[ADR 0008](../adr/0008-hybrid-ai-decision-model.md) (decision-point registry,
patterns, trust levels and promotion rules, Jev policy, lead-agent bounds,
decision-record shape, cleaning ownership, AI-off semantics) and
[ADR 0009](../adr/0009-ai-governance-gateway-harness-assistant.md) (tables and
columns, governance policy schema, gateway contract, harness lifecycle and
hooks, tool catalog, threads API and `AssistantStep`, data exposure, pins). Both
were accepted by the founder on 2026-10-04 (models: OpenAI `gpt-6.1-sol` for
complex roles, `gpt-6-luna` for simple roles); where this overview and an ADR
differ, the ADR wins.

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
plus the lead agent's bounded tool loop (P6.3-B). ADR 0008 §6 decides that
**DCLab's harness hosts the lead loop** (`runtime/lead_runtime.py`: each step is
one typed gateway call returning an `AssistantStep`); NOOA is not used for
multi-step loops because its multi-step strategy is CodeAct. LangGraph/Deep
Agents/OpenAI Agents are dropped from the MVP. External agents need no runtime
at all — they use MCP.

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
5. **Trust levels per decision point**, earned through R3 (the registry with
   every point, its stage, pattern, rule, cap and revert target is ADR 0008 §1;
   promotion and demotion numbers are ADR 0008 §4):

| Level | Meaning | Caps (ADR 0008) |
| --- | --- | --- |
| L0 Shadow | AI answers and is logged; the rule decides | every point starts here |
| L1 Advise | AI answer shown with evidence; a person accepts | highest level ever for: target, spec objective, identifier judgments and every other exclusion, leakage flag, split strategy, experiment review, improve action (until ADR 0007), release, rollback proposals, **every lead-agent write tool** (ASSISTANT.md rule 6) |
| L2 Auto + revert | AI value applied when the validator accepts it; one-click revert | numeric ↔ categorical roles among modeled columns, impute-median ↔ impute-most-frequent, families/budget, retrain (new root); highest level inside auto-train; caps are per answer value (ADR 0008 §1b) |
| L3 Auto + notify | agent acts under a workspace policy; person notified | `ops.diagnose` only; automatic rollback is a deterministic rule action under the autonomy policy, not an AI level; never inside auto-train |

Placement patterns around each important deterministic step:

| Pattern | AI role | Examples |
| --- | --- | --- |
| AI-before | proposes the inputs of a deterministic step | target and spec, column roles, leakage suspects, split strategy, families/budget, next improve action |
| AI-after | reviews the outputs of a deterministic step | Critic on every completed run, drift diagnosis, release notes |
| Cross-check | both answer; agreement applies, disagreement goes to L1 review | Jev vs schema inference, agent vs rule improve proposer |

Timing rule (ADR 0008 §2): a worker stage waits only on Jev (≤ 1 s, cache
first); agent-backed AI-before answers are produced before the job and travel as
typed inputs; AI-after reviews are separate jobs. Never AI: model training,
metric calculation, final model selection on validation data, holdout scoring,
the splits themselves, column exclusion above L1. Every path works with AI
disabled (ADR 0008 §8: the stage runs as today plus one event).

## 2. NOOA — verified facts that shape the design

| Fact (source in the NOOA repo) | Consequence for DCLab |
| --- | --- |
| Agents are Python classes; `async def` methods with body `...` are LLM "generation methods"; docstring = prompt, return annotation = typed contract (`src/nooa/agent.py`, `docs/tour.md`) | Agent classes map 1:1 to DCLab roles with Pydantic proposal types |
| `PredictStrategy`: one structured LLM call, validated output, no code execution | **The only strategy allowed in the MVP** |
| `CodeActStrategy` is the **default** and its `execution_backend` defaults to `"inprocess"` (`config/strategy_config.py`, main 2026-10-02); `docs/concepts/safety.md`: "The containment boundary is outside the Python process" | CodeAct is banned in API/worker processes; a CI test fails if any DCLab agent method resolves to a CodeAct strategy. A CodeAct lane needs its own container (Phase 9) |
| `CodeActConfig.max_iterations: int | None = None` (unbounded); no cost budget | DCLab enforces budgets itself (max calls, tokens, wall time) around every run |
| LLM access via LiteLLM; custom `api_base`, retry and timeout configs | Provider/model resolved by DCLab settings; pin `litellm` to a reviewed version (supply-chain history) |
| Instance history accumulates; concurrent calls serialize | One agent instance per AgentRun, never reused across requests or tenants |
| Event hooks (`event_manager.on/intercept`), OTel/JSONL tracing; tracing auto-exports to a viewer on :5001 | Map events into `agent_events`; configure exporters explicitly; disable auto viewer |
| Alpha, "research software", ~weekly breaking changes | Pin exact version; only `app/agents/runtime/nooa_runtime.py` imports `nooa` |

## 3. NOOA in DCLab — software design

```
apps/api/app/agents/                      (authoritative layout: ADR 0009 §1)
  contracts.py        # Pydantic: ContextEnvelope, AgentRunSpec, AssistantStep, proposals, Citation
  governance/         # POLICY LAYER (data, not code paths)
    platform_default.py  code-owned AiPolicyV1 default + CAPS
    policy.py         #   effective_policy(workspace): platform default ⊕ workspace ai_policies, with caps
    decision_points.py#   decision-point registry (ADR 0008 §1) + effective_level(workspace, key)
    incidents.py      #   ai_incidents: open, auto-demote, resolve
    console.py        #   read model for /v1/governance
  gateway/            # THE ONLY DOOR TO ANY MODEL
    contract.py       #   complete(request) for LLMs, decide(request) for Jev; typed Refusal
    router.py         #   provider/model from policy + agent role; allowlist
    redaction.py      #   drop envelope fields above the effective data class (tag-based); untrusted text
    budget.py         #   reserve → settle against workspace_llm_budgets counters
    cache.py          #   digest(prompt release, model, redacted input, schema) → answer
    limits.py         #   rate limits, circuit breakers
    ledger.py         #   one llm_invocations row per call, cache hits and refusals included
    switches.py       #   kill switches: global → workspace → agent → purpose → provider
    providers/        #   the ONLY modules importing OpenAI / LiteLLM / TypeSafe SDKs (+ fake)
  harness/            # THE ONLY WAY TO RUN AN AGENT
    service.py        #   AgentService.run(spec): the lifecycle below; job handler agents.run
    context.py        #   ContextEnvelope from read-only services (metadata, aggregates, no raw rows)
    hooks.py          #   pre-run / pre-call / pre-tool / post-tool / post-run; closed effect sets
    validation.py     #   Pydantic + deterministic validators → proposed | applied | shadow | rejected_by_validator
    recorder.py       #   agent_events: prompt release, input digest, tool calls, output
    replay.py         #   re-run a recorded run offline; asserts identical tool sequence and output digests
  tools/              # ONE TOOL CATALOG (catalog.py + definitions/<tool>.py + shaping.py)
                      #   exported to contracts/agent_tools.json; consumed by MCP, lead agent, Studio forms
  runtime/
    base.py           # AgentRuntime protocol: run(spec, envelope, tools, hooks) -> RuntimeResult
    fake_runtime.py   # deterministic, used by all tests and when NOOA is disabled
    nooa_runtime.py   # the ONLY module importing nooa; PredictStrategy only; LLM = gateway adapter
    lead_runtime.py   # bounded multi-step tool loop for the lead agent (= the in-app assistant)
  semantic/           # Jev port, deterministic policy, releases (see §5)
  prompts/<agent_key>/v<N>.md   # code-owned prompt text; digest → prompt_releases
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
| ExperimentCriticAgent | experiment evidence: **CV** metrics, folds, findings, verification results (never final-holdout values — ADR 0008 §2b) | `ExperimentReviewProposal`: findings with CV metric citations, verdict promote_candidate/needs_work/reject | cited metrics must exist in `evaluation_metrics` with CV scope; verdict never auto-applies | 6 |
| ImprovementHypothesisAgent | constraint gap, diagnostics, prior iterations | `ImprovementActionProposal`: one action from the typed action space (class weight, threshold, family, hyperparameter range, allowlisted feature transform) | action type and ranges allowlisted; budget remaining; never touches holdout | 6 (plugs into Phase 5 loop) |
| LeadAgent ("AI data scientist" = the in-app assistant) | user goal + data, graph summary, decision-point levels, tool catalog | `AssistantStep` per step: tool calls (bounded loop), answers with citations, L1 proposals | every tool call validated by the catalog validator and the service it calls; citation validator; bounds of ADR 0008 §6 | 6 (runtime P6.3-B), 4 Stage 4 (A3-UI) |
| OpsAgent | monitoring windows, findings, champion evidence (CV scope) | retrain as a new root experiment, release proposal, rollback proposal with explanation | same tools; workspace autonomy policy; release and rollback proposals never above L1; automatic rollback is the rule action `ops.rollback_threshold.v1` | 7 (P7.8) |

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
| Hosted inference only; "not fine-tuned or LoRA-adapted with customer data", no self-host (`docs.typesafe.ai/models`, 2026-10-04) | "Train your own Jev" is not possible. Reframe: users customize Jev questions and DCLab calibrates thresholds on their labels; custom trained decision models are a DCLab ModelType (classical now, SLM in Phase 9) |
| Choice (≤255 options, probabilities, confidence), Score (2–10 levels), Noul (P(yes)) | Map to `SemanticDecisionPort` question types |
| `jev-latest` and `jev-preview` aliases both point at `jev-1.13.0` today and move | Pin `jev-1.13.0` per release (ADR 0008 §5); a bump resets purposes to L0 until R3 re-runs |
| ~100 ms typical, $0.042 / 1M input tokens (output free), 100K tokens/s and 80 req/s that "can change without notice"; 64k context (32k for `state`); no SLA | Always optional, circuit breaker, 1 s timeout, batches ≤ 50 questions, deterministic fallback |
| Weak at counting/arithmetic/dates; vulnerable to prompt injection in state (`model-jaggedness/jev-1.13.md`) | Pass computed numbers as categories; never ask Jev numeric questions; treat column names/values as untrusted text |
| "Not trained on customer requests"; ZDR enterprise-only; region undocumented | No customer raw values sent by default; sample values only by workspace policy **and** dataset `llm_exposure_policy` (ADR 0009 §8); metadata-only otherwise |
| Early access since 2026-09-15; `typesafe-sdk` 0.5.7 → 0.7.2 between 2026-09-11 and 2026-09-26 | Shadow-only until evidence; `typesafe-sdk==0.7.2` pinned; adapter isolates SDK |

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
| `column.is_identifier` | Noul | column name, dtype, uniqueness band (e.g. "unique ratio > 0.99"), name tokens | `schema_inference` identifier rule | L1 (an identifier judgment is an exclusion — ADR 0008 §1b) |
| `column.semantic_role` | Choice: numeric / categorical code / identifier / datetime / free text / other | name, dtype, cardinality band, value-pattern band (from the locked training partition) | `split_column_roles` + column-type decision | L2 after gate for numeric ↔ categorical among modeled columns; L1 for identifier / free text / other |
| `feature.leakage_suspect` | Noul | column name + target name + availability text | leakage auditor name rules | L1: adds a review flag, never excludes |
| `command.intent_route` | Choice over Studio/MCP capabilities + "other" | user's text command | none (UX router) | L1: route with confirmation |
| `proposal.completeness` | Score (0–4) | agent proposal summary | none | L0: display only |

Trust levels per purpose are rows of `decision_point_policies` (platform row from
R3, workspace row ≤ platform) and are recorded in STATUS.md § Agent / Jev release
decisions. The gates are ADR 0008 §4, on sealed benchmark partitions and blind
labels only (user accept/reject/revert is exposed and may only monitor or
demote; blind labels are inverse-probability weighted and the two sources are
gated separately): L0 → L1 needs ≥ 100 sealed and ≥ 30 blind cases with a
precision lower bound ≥ 0.90 for yes/no purposes; L1 → L2 (only
`column.semantic_role` among the Jev purposes) needs ≥ 20 sealed datasets and
≥ 300 cases plus ≥ 100 blind, a paired cluster-bootstrap non-inferiority with
margin 1 pt, and a Wilson lower bound ≥ 0.98 of the deployed-policy precision on
in-band overrides (reachable only with ≥ 189 cases and 0 errors or ≥ 280 with
1; bands Noul p ≥ 0.9 or ≤ 0.1, Choice confidence ≥ 0.8), Holm correction and
alpha-spending per R3 run, latency/cost within budget, zero policy leaks;
evidence is keyed by (prompt release, dated model id); demotion is automatic on
upper-bound evidence of harm, incidents, label-free drift and a failed
uncached repeat-stability check. Prohibited forever: task type from numbers,
metric reading, model selection/promotion, automatic column exclusion, loop
control.

## 5b. AI governance

Governance is the policy layer that the gateway and harness enforce. It is data
per workspace (with a code-owned platform default and caps), never a parallel
code path. Every change is an append-only row in the versioned policy tables
(`ai_policies`, `decision_point_policies`: proposed → accepted chains with
actor, rationale and evidence — ADR 0009 §2.5–2.6 and §2.9 explain why these
workspace-scoped changes are not `project_decision_records`); project-scoped AI
decisions are `project_decision_records` rows (`decision_point_resolved`,
`proposal_*`, ADR 0008 §7). The policy schema (`AiPolicyV1`) and caps are ADR
0009 §3.

| Policy area | Contents | Enforced by |
| --- | --- | --- |
| Models | allowlist per agent role (lead, specialist, Jev); default model; fallbacks | gateway router |
| Data | highest data class that may leave: metadata → aggregates → sample values → raw rows (raw never by default); untrusted-text marking | gateway redaction |
| Autonomy | trust level per decision point (≤ code cap); ops autonomy policy (auto-retrain, auto-release off, rollback threshold) | harness + decision points + Ops agent |
| Budgets | per workspace / project / run: cost, calls, tokens, wall time; alert thresholds | gateway budget + harness bounds |
| Approvals | who may accept L1 decisions, change levels, flip switches, change policy | decision record service, governance API |
| Evaluation gates | minimum R3 evidence to promote a level; auto-demotion after N validator rejections or a failed R3 run | `decision_points.py`, `incidents.py` |
| Audit | every call in `llm_invocations`, every run in `agent_runs/agent_events`, replayable; retention of prompts/answers | gateway ledger, harness recorder |
| Kill switches | global / workspace / agent / purpose; flip is audited; next call falls back to the rule | gateway switches |

The governance console (`/v1/governance` in P6.11-A, Studio page P6.11-UI) shows
the effective policy, levels with evidence, spend vs budget, incidents and
history.

## 6. Persistence (Phase 6 migration `0071_agents_governance`, P6.2-A)

Column-level schema, constraints, triggers and indexes are **ADR 0009 §2**; this
table is the index of it.

| Table | Purpose | Mutability |
| --- | --- | --- |
| `agent_runs` | one row per agent run; kinds `assistant` (= a Lab thread), `lead`, `specialist`, `ops`; runtime `fake` / `nooa_predict` / `lead_loop`; limits, usage, cost_micros, policy and context digests, subject CFKs | header frozen by trigger; status/usage/cost mutable |
| `agent_events` | ordered, bounded, redacted record of every run (prompts by release id, inputs by digest, tool calls, outputs); the replay source | append-only |
| `agent_proposals` | the one proposal model for specialists, the lead agent and the assistant (`ToolCallProposal`); statuses `shadow` / `proposed` / `rejected_by_validator` / `accepted` / `rejected` / `applied` / `reverted` / `superseded` / `expired` | payload immutable; status transitions only |
| `semantic_decision_answers` | every Jev answer beside the rule answer, agreement, level, value used; the R3 corpus (labels added once) | append-only except the label columns |
| `ai_policies` | `AiPolicyV1` per workspace plus the platform default row (`workspace_id IS NULL`); proposed → accepted chains | append-only |
| `decision_point_policies` | trust level per (workspace or platform, decision point) with cap, actor and evidence; effective = min(workspace, platform, code cap) | append-only |
| `workspace_llm_budgets` | limits and period counters (`reserved_micros`, `spent_micros`) per workspace / project / run kind; reserve → settle | mutable counters, reconciled nightly against the ledger |
| `prompt_releases` | code-owned prompt versions (agents and Jev purposes) by digest; every call records one | rows never deleted; `retired` on change |
| `ai_incidents` | validator rejections, eval failures, budget, provider, data exposure, revert rate → auto-demotion / switch-off | status fields mutable |
| `llm_invocations` (expand) | CFK `agent_run_id` → `agent_runs`; `prompt_release_id`, `cache_hit`, `cost_micros`, `data_class`, `agent_role`, `decision_point_key`, `refusal_code`; purpose CHECK becomes a key regex | existing ledger stays the single usage/cost record |
| `project_decision_records` (expand) | CFK `actor_agent_run_id` → `agent_runs`; decision types `decision_point_resolved`, `proposal_reverted` | unchanged (append-only) |

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
