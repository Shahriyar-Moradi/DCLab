# Agents in DCLab: NVIDIA NOOA and TypeSafe Jev

Status: CURRENT design (2026-10-01), built on verified research of
`NVIDIA-NeMo/labs-OO-Agents` at commit `2598f1f5` (PyPI `nooa` 0.0.10) and the
TypeSafe docs (`jev-1.13.0`, `typesafe-sdk` 0.7.x). Supersedes the delivery
order of `docs/agentic-program/JEV_NOOA_INTEGRATION_ARCHITECTURE.md`; its
ownership rules remain valid.

## 1. Where agents sit

```
External agents (Claude Code, Cursor) ──MCP──► /v1 ──► services   (Phase 3)
In-app assistant (Studio chat) ── shared tool catalog ──► read: services · write: confirm card ──► /v1   (Track A, Phase 4+)
Internal agents (NOOA classes) ── typed proposals ──► proposal service ──► same services (Phase 6)
Typed semantic judgments (Jev) ── advisory answers ──► decision ledger (shadow first, Phase 6)
```

The authority model is unchanged from `AGENT_FIRST_MVP_ARCHITECTURE.md` §4:
agents observe, explain and **propose**; deterministic services validate and
execute; a human (or an explicit policy) accepts. "Higher autonomy changes who
initiates a command, not who validates or executes it."

The in-app assistant (ADR 0009, `prompts/ASSISTANT.md`) is not a second
runtime: it is a DCLab-owned bounded loop of typed single LLM calls through the
same gateway, using the same tool catalog as MCP and the same `agent_runs` /
`agent_proposals` tables (built early, in A2-A).

The internal runtime set is reduced from four (LangGraph, Deep Agents, OpenAI
Agents, NOOA) to **one: NOOA**, plus deterministic fakes. LangGraph/Deep Agents/
OpenAI Agents are dropped from the MVP. External agents need no runtime at all —
they use MCP.

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
  context.py          # builds ContextEnvelope from read-only services (metadata, aggregates, no raw rows)
  validators.py       # deterministic proposal validators (columns exist, metrics exist, actions allowlisted)
  service.py          # create/run/cancel AgentRun, persist events/proposals, settle usage
  runtime/
    base.py           # AgentRuntime protocol: run(spec, envelope) -> RuntimeResult
    fake_runtime.py   # deterministic, used by all tests and when NOOA is disabled
    nooa_runtime.py   # the ONLY module importing nooa; PredictStrategy only
  classes/
    dataset_investigator.py
    experiment_planner.py
    experiment_critic.py
    improvement_hypothesis.py
```

Agent classes (all PredictStrategy, all return proposals):

| Class | Input (ContextEnvelope) | Output proposal | Deterministic validator | Phase |
| --- | --- | --- | --- | --- |
| DatasetInvestigatorAgent | profile, column stats, deterministic findings, target candidates | `DatasetInvestigationProposal`: prioritized findings, questions for the user, semantic-role suggestions | every column cited exists; no new statistics accepted unless they match the profile | 6 |
| ExperimentPlannerAgent | ProblemSpec draft, profile summary, allowed families/budgets | `ExperimentPlanProposal`: metric + rationale, split strategy, candidate families, budget | metric/split/families from code allowlists; split must pass holdout planner rules | 6 |
| ExperimentCriticAgent | experiment evidence: CV/holdout metrics, folds, findings, verification results | `ExperimentReviewProposal`: findings with metric citations, verdict promote/needs_work/reject | cited metrics must exist in `evaluation_metrics`; verdict never auto-applies | 6 |
| ImprovementHypothesisAgent | constraint gap, diagnostics, prior iterations | `ImprovementActionProposal`: one action from the typed action space (class weight, threshold, family, hyperparameter range, allowlisted feature transform) | action type and ranges allowlisted; budget remaining; never touches holdout | 6 (plugs into Phase 5 loop) |

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

`AgentService.run()` (in the worker, as an `MlJob` handler `agents.run`):
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
data class).

Purposes, all advisory:

| Purpose | Question | State sent | Deterministic rule it shadows | Earliest activation |
| --- | --- | --- | --- | --- |
| `column.is_identifier` | Noul | column name, dtype, uniqueness band (e.g. "unique ratio > 0.99"), name tokens | `schema_inference` identifier rule | shadow → advisory hint after gate |
| `column.semantic_role` | Choice: numeric / categorical code / identifier / datetime / free text / other | name, dtype, cardinality band, value-pattern band | `split_column_roles` + column-type decision | shadow → review hint |
| `feature.leakage_suspect` | Noul | column name + target name + availability text | leakage auditor name rules | shadow → adds "review" flag only, never excludes |
| `command.intent_route` | Choice over Studio/MCP capabilities + "other" | user's text command | none (UX router) | shadow → route with confirmation |
| `proposal.completeness` | Score (0–4) | agent proposal summary | none | display only |

Release gate per purpose (recorded in STATUS.md as `SHADOW_ALLOWED`,
`ADVISORY_ALLOWED`, `DISABLED_PENDING_EVIDENCE` or `REJECTED`): ≥ 300 labeled
examples (benchmark datasets + reviewed user decisions), reliability diagram and
ECE, precision ≥ 98 % inside the acting band (e.g. Noul p ≥ 0.9 or ≤ 0.1,
Choice confidence ≥ 0.8), latency/cost within budget, zero policy leaks. Prohibited
forever: task type from numbers, metric reading, model selection/promotion,
automatic column exclusion, loop control.

## 6. Persistence (Phase 6 migrations)

| Table | Key columns | Rules |
| --- | --- | --- |
| `agent_runs` | id, workspace_id, project_id, agent_key, agent_version, runtime (`fake`/`nooa_predict`), runtime_version, model, purpose, subject_kind/subject_id, context_digest, status, limits (json), usage, cost_micros, error_code, idempotency_key, created_by, timestamps | CFK to project; unique (workspace_id, idempotency_key); one active run per (subject, agent_key) |
| `agent_events` | run_id, seq, type, bounded payload, created_at | append-only trigger; unique (run_id, seq) |
| `agent_proposals` | id, run_id, workspace_id, proposal_type, schema_version, payload (validated json), citations, validator_verdict, status (`proposed`/`rejected_by_validator`/`accepted`/`rejected`/`superseded`/`expired`), decided_by, decided_at, decision_record_id | payload immutable; only status transitions allowed |
| `semantic_decision_answers` | invocation_id → `llm_invocations`, purpose, release_version, question_key, answer, probabilities, confidence, deterministic_answer, mode (`shadow`/`advisory`), outcome, ground_truth, labeled_by | evaluation corpus for the release gate |
| `llm_invocations` (expand) | make `workflow_run_id`/`experiment_id` nullable; add `project_id`, `agent_run_id`, `provider_kind` | existing ledger stays the single usage/cost record |

## 7. Release order

1. Phase 2: `llm_invocations` expand + `project_decision_records` (needed by everything).
2. Phase 5: improve loop works **without** agents (deterministic proposer).
3. Phase 6: agent tables → fake runtime → NOOA runtime (Predict) → 4 classes →
   proposal review UI/MCP → Jev shadow → agent/Jev eval gate.
4. Phase 9 (only if Phase 6 evidence shows value): NOOA CodeAct lane in a
   separate sandbox container for feature-engineering proposals whose code is
   then re-executed by the deterministic engine under CV.
