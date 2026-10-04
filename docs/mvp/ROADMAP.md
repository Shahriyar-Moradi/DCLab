# DCLab MVP roadmap (hybrid AI order + use and trust first, merged 2026-10-04)

Principle: build from the inside out — **correct engine → state graph → agent
interface → use and trust → hybrid AI core → agent-first Studio → verify/improve →
operations → hosted beta → expansion** — and make every phase usable by a real
user at its end. Estimates assume one founder + Claude Code; customer discovery
(≥ 5 conversations per week) runs in parallel from day one.

Two founder decisions shape this plan. They were made in separate sessions and
merged on 2026-10-04 (STATUS.md § Plan changes).

**Hybrid AI order (2026-10-02).** The "middle path" between the previous order and
a full AI-first plan: Phase 6 becomes the *Hybrid AI core* and runs before the
Studio work of Phase 4; Phases 4, 5 and 7 are reshaped around it. Phase numbers
are kept as identifiers (code, ADRs and STATUS refer to them). See
[§ Hybrid AI model](#hybrid-ai-model) and [AGENTS_NOOA_JEV.md](AGENTS_NOOA_JEV.md);
visual comparison in `demo/`.

**Use, trust and UI (2026-10-04).**

1. **Every phase ships its UI.** A capability is done only when it is reachable in
   Studio or listed API-only by design in [UI_COVERAGE.md](UI_COVERAGE.md). Phases
   1–3 and the Phase 6 core run before the Studio shell exists; their UI is Phase 4
   Stages 2 and 4.
2. **Use and trust come first.** Scoring new data, five core trust checks and a
   model card move from Phases 5/7 into Phase 4 Stage 1, before the hybrid AI core
   (whose Critic cites them) and before the Studio UI.
3. **The in-app assistant is the lead agent.** Track A
   ([prompts/ASSISTANT.md](prompts/ASSISTANT.md)) keeps the user-facing prompts;
   its backend (ADR, gateway, agent tables, tool catalog, loop) is built once,
   inside Phase 6. One tool catalog is shared with MCP; read tools run, write tools
   become proposals (confirm cards) at the decision point's trust level.
4. **One audience: data scientists and ML engineers.** Decision.ai and marketing
   pages leave the navigation (P4.12-A).
5. **Design partners join at the end of Phase 4**, not Phase 8, and their feedback
   reorders Phases 5–7.
6. **Studio looks like the prototype.** `design/prototype/` is the visual and
   structural target; `design/STUDIO_DESIGN.md` maps every screen to its route,
   backend and prompt, and its sync rule forbids drawing what the backend lacks.
7. **Merge cadence:** build 2–3 phases (or Phase 4 stages) on one branch, run the
   full suite and CI once at the merge point, merge, and fill every STATUS.md
   SHA in one go (EXECUTION_GUIDE.md §5).

**Resulting order:** Phases 0–3 (done) → Phase 4 Stage 1 (use and trust, backend)
→ **Phase 6** (hybrid AI core) → Phase 4 Stages 2–5 (Studio; checkpoint G6 falls
during Stages 2–3 and must pass before Stage 4 exposes AI to users) → Phase 5 →
Phase 7 → Phase 8. Previous orders: 0 → … → 8 in sequence (before 2026-10-02);
0 → 1 → 2 → 3 → 6 → 4 → 5 → 7 → 8 (2026-10-02 to 2026-10-04).

| Order | Phase | Name | Weeks | Ends with a user being able to… |
| --- | --- | --- | --- | --- |
| 1 | 0 | Stabilize the foundation | done | trust that `main` is green and reproducible |
| 2 | 1 | One correct ML engine | done | upload any tabular CSV and get a scientifically valid, baseline-beating model |
| 3 | 2 | ML state graph | done | see lineage, branch an experiment, see what became stale |
| 4 | 3 | Agent interface: `/v1`, SDK, CLI, MCP | done | drive DCLab from Claude Code / Cursor |
| 5 | 4 · Stage 1 | Use and trust (backend + MCP) | 1–2 | score new data, read five trust checks and a model card from the CLI, SDK or MCP |
| 6 | **6** | **Hybrid AI core** (NOOA, Jev, lead agent = assistant backend, decision points, governance, R3) | 3–4 | get a cited AI review of every run and see, at every pipeline decision, what the AI said, what the rule said and what was used |
| 7 | 4 · Stages 2–5 | **Agent-first Studio** | 4–5 | give a goal in the Lab chat (or the forms); watch upload → spec → train → results in the pipeline view with evidence; see trust findings, read a model card, score new data; review proposals in the Inbox |
| 8 | 5 | Verify & Improve (hybrid) | 3–4 | ask "recall ≥ 0.80, best precision" (in Studio, the assistant or MCP) and get audited iterations proposed by AI and rules |
| 9 | 7 | Release, monitoring and **agentic MLOps** | 4 | release a model, schedule scoring, watch drift; have drift explained, retrained and proposed for release; approve or roll back from the UI |
| 10 | 8 | Hosted beta | 3–4 | use DCLab in the cloud with design partners, with LLM budgets and metering |
| 11 | 9 | Expansion (demand-gated) | — | each item its own ADR |

Track A (assistant): backend in Phase 6 (A1-A → P6.1-A, A2-A → P6.2-A + P6.2-B,
A2-B → P6.10-A + P6.3-B); screens in Phase 4 Stage 4: **A2-UI** agent runs and
tool registry · **A3-UI** Lab page (lead-agent chat) · **A4-A** confirm cards on
P6.6 proposals · **A4-B** golden conversations · **A6-A** specialist agents as
skills → **A5** improve (Phase 5) → **A7** operations (Phase 7) → **A8** metering
(Phase 8).

Merge points (one full suite + CI each): **MP-46** after Phase 4 Stage 1 + Phase 6
· **MP-4a** after Phase 4 Stages 2–3 · **MP-4b** after Stages 4–5 (design partners
start) · **MP-5** after Phase 5 · **MP-78** after Phases 7–8.

Track R (R&D, parallel): **R1** benchmark harness (Phase 1, done) → **R3**
agent/Jev evaluation harness, continuous and the source of every trust-level
change (Phase 6) → **R2** improve-loop benchmark, agent vs rule proposer (Phase 5)
→ **R1-P** public benchmark page (Phase 5) → **R4** public evidence library /
"solved problems with proof" (Phase 9).

## Hybrid AI model

DCLab is AI-first in how users work and deterministic in how results are produced.
AI and rules cover each other; neither is trusted alone.

1. **AI proposes at every decision point** in the pipeline and in operations
   (target, column roles, leakage suspects, split strategy, families/budget,
   next improve action, retrain, release, rollback).
2. **Deterministic code validates every proposal**: columns exist, actions are on
   an allowlist, the final holdout is untouched, the budget is not exceeded.
3. **Every decision is a `project_decision_records` row**: who decided (rule,
   agent, Jev, human), why, cited evidence, the rule's answer, and how to revert.
4. **Autonomy is per decision point and earned with evidence**: L0 shadow (AI
   answers, rule decides) → L1 advise (person accepts) → L2 auto-apply with
   one-click revert → L3 auto under a workspace policy with notification. A level
   changes only when R3 shows the AI is at least as good as the rule; the change
   itself is a decision record.
5. **AI sits before or after each important deterministic step, never inside it**:
   *AI-before* proposes inputs (spec, roles, split, candidate actions);
   *AI-after* reviews outputs (Critic on every run, drift diagnosis);
   *cross-check* runs both and flags disagreements for review.
6. **Jev is used deterministically**: pinned release, calibrated thresholds owned
   in code, answers cached by input digest (same input → same answer), and a fixed
   agreement policy (agree → apply; disagree → L1 review; low confidence → rule).
7. **Specialist agents make one structured LLM call** (NOOA Predict); only the
   **lead agent** ("AI data scientist", which is the in-app assistant of Track A)
   runs bounded multi-step tool loops, through the same validated tool catalog as
   the MCP server.
8. **Never AI:** model training, metric calculation, final model selection on
   validation data, holdout scoring, the splits themselves.
9. **Every path works with AI off**; disabling NOOA and Jev leaves every
   Phase 1–5 test green.

### AI governance, gateway and harness (the three layers above the agents)

```
AI governance   policies: who may use which model on which data at which trust level,
                budgets, approvals, audit, evaluation gates, incident response
      │ enforced by
AI gateway      the only door to any model (LLM or Jev): routing, allowlists, redaction,
                budgets, caching, rate limits, circuit breakers, ledger, kill switches
      │ called only through
Agent harness   the standard wrapper around every agent run: context → redact → budget →
                run → tools via registry + validators → validate output → record → settle →
                replay; plus the test harness (fake LLM, recorded sessions, R3)
      │
Agents          lead agent (= in-app assistant), specialists (NOOA Predict), Jev purposes
```

- **No model call outside the gateway** (import-linter rule: only
  `app/agents/gateway/providers/*` may import provider SDKs; CI fails otherwise).
- **No agent run outside the harness** (every runtime is registered with it; the
  MCP server and Studio reach agents only through `AgentService.run`).
- **Governance is data, not code paths**: `ai_policies` per workspace (model
  allowlist, data classes allowed to leave, trust levels, budgets, autonomy policy,
  approvers); every change is a decision record; a governance console shows the
  current policy, spend, levels and incidents.
- **Every run is replayable** from its recorded events (prompts by version, inputs
  by digest, cached answers), so an audit or a regression can be reproduced
  without a live model.

## Phase 0 — Stabilize the foundation

Carry-over from Scope 0: P01A–D verified with CI; P02–P04C locally verified;
P04D and P05B partial (see STATUS.md). Remaining Scope-0 plans 0.5C–0.10 are
re-homed below or into later phases (0.6 `/v1` → Phase 3; 0.9 DataScan → Phase 9
trigger; 0.10 storage → P1.3; 0.7 CI parity → P0.1/P0.3).

| Plan | Outcome |
| --- | --- |
| P0.1 Green, reproducible CI | Locked deps (psycopg3, bounded SQLAlchemy), metadata-cycle fix, CI green on `main` |
| P0.2 Close Scope-0 security debt | S0-P04D and S0-P05B closed with recorded decisions (ADR 0005 upload policy) and exact-SHA CI |
| P0.3 Dev topology | Compose runs a real `worker` service; Makefile portable |
| P0.4 Repo hygiene | Large CSVs out of git with fetch script; old docs indexed as reference; branding fixed |

**Exit gate:** CI green on 3 consecutive `main` commits; single Alembic head;
`docker compose up` trains a sample via the worker; STATUS.md updated.

## Phase 1 — One correct ML engine

| Plan | Outcome |
| --- | --- |
| P1.1 Route admin Lab to open-ingest | All training goes through the open-ingest runner; legacy branch unreachable |
| P1.2 Delete legacy training code | Legacy runner branch, `factory.py`, `app/ml` removed (ensemble/selection moved into engine); sim decision API frozen behind flag |
| P1.3 Storage boundary | Run artifacts/prepared data via `ObjectStorage`; no writes under `REPO_ROOT/data|artifacts` |
| P1.4 Engine quality | Dummy baseline, class weights, multiclass, validation-tuned threshold, ProblemSpec objective/constraints, CatBoost, light Optuna tuning, time budget honored |
| P1.5 Split auto-train | `run_auto_train_job` split into typed stage functions, no behavior change |
| P1.6 Finish-line E2E suite | `test_e2e_lab_run.py` 8 cases + 5 unfamiliar-schema datasets |
| R1 Benchmark harness | 20–30 OpenML tasks run through the engine; results stored; nightly CI regression gate |

**Exit gate:** one training path (grep proves no legacy callers); E2E suite and
benchmark green; benchmark report shows winner beats dummy baseline on every task.

## Phase 2 — ML state graph

| Plan | Outcome |
| --- | --- |
| P2.1 ADR 0006 state graph | Names (Experiment = `experiments`), refs model, SplitPlan node, decision records, staleness semantics |
| P2.2 Schema | `split_plans`, `project_refs`, `project_decision_records`, experiment lineage columns, `llm_invocations` expand, CFK fixes |
| P2.3 Graph service | Lifecycle projection, staleness and impact analysis |
| P2.4 Experiment-as-diff | Branch an experiment with a typed change set, reusing the locked SplitPlan; per-experiment reproducible code export |
| P2.5 Decision records | Accept/reject/supersede; selection and ref changes recorded automatically |

**Exit gate:** graph projection covers upload → ModelVersion; changing a ref marks
dependents stale; a branched experiment shares the holdout and its exported script
reproduces CV metrics within tolerance.

## Phase 3 — Agent interface

| Plan | Outcome |
| --- | --- |
| P3.1 `/v1` contract | Error envelope, cursors, ETags; create project/spec/upload; experiments list/compare/branch; graph; decisions |
| P3.2 Service tokens | Hashed, scoped, expiring machine credentials for SDK/MCP |
| P3.3 SDK + CLI | `dclab` CLI on the SDK with JSON output |
| P3.4 MCP server | `packages/dclab_mcp` stdio server over the SDK, ~14 tools, read/write kill switches |
| P3.5 Dogfood | `.mcp.json` example, golden transcripts, quickstart |

**Exit gate:** from Claude Code via MCP: upload → propose spec → run → compare →
branch → accept, all visible as graph nodes and decision records; no MCP tool has
authority beyond the token's workspace/capabilities.

## Phase 4, Stage 1 — Use and trust (backend + MCP)

| Plan | Outcome |
| --- | --- |
| P4.9 Score new data | Upload a scoring file → predictions from a chosen ModelVersion (worker only, feature-contract check); first slice of P7.2 (done) |
| P4.10 Five core checks | Leakage, overfit gap, duplicates, imbalance, too-good-to-be-true as plain-language findings on every run (done) |
| P4.11 Model card | Drivers, metric in business words, baseline comparison, risks, single holdout result |
| P4.0-A UI–API sync (pulled forward) | Generated TS types from `v1_openapi.json`, drift fails CI, coverage test (done) |

**Exit gate:** from the CLI, SDK or MCP: score a file with a model version, read
the findings of a run and its model card; no field reads the holdout beyond the
locked winner's single evaluation.

## Phase 6 — Hybrid AI core (runs after Phase 4 Stage 1)

Prompts keep their P6 identifiers; execution order is
P6.1 → P6.2-A → P6.2-B → P6.10-A → P6.3-A → P6.7 → P6.9 → P6.4 → P6.3-B → P6.6 →
P6.10-B → P6.11 → P6.8. P6.5 runs inside Phase 5 (it needs the loop engine).
Track A's backend is built here, once (A1-A, A2-A and A2-B are absorbed).

| Plan | Outcome |
| --- | --- |
| P6.1 ADR 0008 hybrid AI decision model + ADR 0009 governance, gateway, harness and assistant | Decision-point registry, AI-before/after/cross-check patterns, trust levels L0–L3 and promotion rules, Jev deterministic policy, lead-agent (= assistant) loop bounds, governance policy schema and approvals, gateway contract, harness lifecycle and hooks, tool catalog, NOOA/LiteLLM pins, data exposure, kill switches (absorbs A1-A) |
| P6.2-A Agent + governance persistence | `agent_runs/events/proposals` (kinds incl. `assistant`), `semantic_decision_answers`, `ai_policies`, `decision_point_policies`, `workspace_llm_budgets`, `prompt_releases`, `ai_incidents` |
| P6.2-B AI gateway | One door to every model: provider interface, model allowlist and routing per agent, redaction by data class, budget reserve/settle, cache by digest, rate limits + circuit breakers, ledger, kill switches, import-linter rule (absorbs A2-A's gateway) |
| P6.10-A Agent harness | Standard run lifecycle with hooks (pre-run, pre-call, pre-tool, post-tool, post-run), one tool catalog shared with MCP (`contracts/agent_tools.json`) with validators, output validation, event recording, replay mode |
| P6.10-B Test harness | Fake LLM driver with recorded fixtures, golden transcripts, property tests (CodeAct ban, holdout-blind registry, no raw rows), chaos cases (timeout, over budget, provider down), R3 runner |
| P6.11 Governance console API | `/v1/governance`: current policy, spend vs budget, trust levels with evidence, open incidents, policy change via decision record; MCP read tool; Studio page P6.11-UI |
| P6.3 Runtimes | A: fake + pinned NOOA Predict + CodeAct ban test. B: lead-agent bounded tool loop over the shared catalog, which is also the in-app assistant backend (threads API, SSE; absorbs A2-B) |
| P6.4 Agent classes | ExperimentCritic (AI-after, every run; cites P4.10 findings and the P4.11 card), DatasetInvestigator and ExperimentPlanner (AI-before) |
| P6.5 Improvement hypothesis agent | Proposer inside the Phase 5 loop, cross-checked by the rule proposer (runs in Phase 5) |
| P6.6 Proposal review (API/MCP) | Accept/reject/revert → command service → decision record; one proposal model for agents and the assistant; Studio inbox and confirm cards follow in Phase 4 Stages 4–5 |
| P6.7 Jev at decision points | `SemanticDecisionPort`, 5 purposes, deterministic agreement policy, cached answers, starts at L0 |
| P6.8 R3 evaluation harness + trust levels | Continuous AI-vs-rule scoring per decision point; first recorded levels per agent/purpose |
| P6.9 Hybrid decision points in auto-train | Each P1.5 stage records AI answer, rule answer, chosen value, level and a decision record; rule used on fallback |
| Screens (built in Phase 4 Stage 4) | A2-UI agent runs + tool registry, A3-UI Lab, P6.7-UI Jev view, P6.8-UI agent catalog, P6.11-UI governance page |

**Exit gate:** every auto-train decision point records AI + rule answers and the
value used; the Critic reviews every completed run with valid citations; each
agent and Jev purpose has a recorded trust level from R3; disabling NOOA and Jev
leaves every Phase 1–3 and Phase 4 Stage 1 test green; per-workspace LLM budget
enforced; CI proves no model call bypasses the gateway and no agent run bypasses
the harness; any recorded agent run can be replayed without a live model.

**Checkpoint G6 (about 4 weeks of R3 data after the gate, during Phase 4 Stages
2–3):** confirm or lower the default trust levels, choose the lead agent's default
model (cost vs quality), and set budget defaults before Phase 4 Stage 4 exposes AI
to users.

## Phase 4, Stages 2–5 — Agent-first Studio (after the Phase 6 gate)

| Stage | Plan | Outcome |
| --- | --- | --- |
| 2 UI foundation + catch-up (Phases 1–3) | P4.0-B Design system and shell | Prototype tokens, shell and `components/studio/*` |
| | P4.1 Project IA, wizard, Data page | Workspace + project sidebar per prototype (Lab, Pipeline, Graph, Data, Experiments, Models, Decisions, Inbox), ⌘K; upload → target → train; Data page with column profile (NEW `/v1/datasets/{id}/profile`) |
| | P4.2 Graph view | Interactive lineage with stale markers, impact and AI-decision markers |
| | P4.3 Node inspectors | Reason, formula, evidence and code for data/feature/split/experiment/model nodes; AI investigation and Critic review |
| | P4.4 Compare, branch, refs, decide | Diff, branch form, champion ref move, decision timeline |
| | P4.5 Client run page cleanup | No technical internals for client role |
| | P4.8 Agents & tools page | Connect (MCP/SDK/CLI/API) and Service tokens tabs |
| 3 UI for use & trust | P4.9-UI / P4.10-UI / P4.11-UI / P4.17-UI | Score page, findings panel, model card page, Pipeline evidence page (every stage with deterministic result, AI-before/after notes, decision-point outcome: AI answer, rule answer, value used, level; artifact digests; checks) |
| 4 Agent-first Studio (after G6) | A2-UI · A3-UI · A4-A · A4-B · A6-A · P6.7-UI · P6.8-UI · P6.11-UI | Agent runs and tool registry tabs; Lab = lead-agent chat (goal → upload → spec → train → results in the background, each decision with level beside the rule answer, "Use forms instead"); confirm cards on P6.6 proposals; golden conversations; specialist agents as skills; Jev view; agent catalog; governance page |
| 5 Home, inbox, focus, demo, partners | P4.15 Home · P4.16 Inbox · P4.12 One story · P4.13 Demo kit · P4.14 Partner kit · P4.6 Killer-flow E2E | Home with activity, open proposals and AI health (NEW `/v1/activity`); Inbox incl. L2 auto-decisions with Revert (NEW `/v1/inbox`); role landing; 2-minute demo; partner install; full Playwright flow |

**Exit gate:** Playwright, against the real backend with the fake LLM: Home → new
project → Lab: goal in chat → AI decisions shown beside the rule answers (accept
one L1) → run → Pipeline evidence (every stage, digest and check) → findings →
Data page → inspect feature → view code → assistant proposes a branch → confirm →
Inbox → compare → accept → score new data → model card. A second spec runs the
same flow with AI off through the forms. Every Phase 4 screen matches its
prototype screen minus elements whose backend comes later; every "planned" Phase 4
row in UI_COVERAGE.md is "exists". Founder steps: demo video recorded; 2–3 design
partners booked.

## Phase 5 — Verify & Improve (hybrid)

| Plan | Outcome |
| --- | --- |
| P5.0 Feature-engineering leakage contract | Fit scopes (`stateless` / `fold_fitted` / `as_of_aggregate`), as-of cutoffs, out-of-fold target encoding, time-travel check — required before any agent proposes features |
| P5.1 Investigation checks | The remaining checks on top of P4.10 (contamination, calibration, fold instability, subgroup, drift between train/holdout periods, …) + time-travel and new-feature checks → ≥ 12 in total, shown in the existing findings panel and cited by the Critic |
| P5.2 Objective optimizer | Constraint-aware threshold/cost optimization on validation folds; operating-point chooser in Studio (P5.2-UI) |
| P5.3 ADR 0007 improve loop | Typed action space, two proposers (agent + rule) with cross-check, budgets, stop rules, holdout rules |
| P5.4 Improve loop engine | Rule proposer + Optuna; agent proposer slot; each iteration a child experiment with change set |
| P6.5 Improvement hypothesis agent | Agent proposer, default only where R2 shows it beats the rule proposer |
| P5.5 Loop UX | Studio timeline (proposer per iteration) + Improve button + MCP `improve` tool + accept/reject; the assistant starts and narrates loops from chat (A5) |
| R2 Loop benchmark | Constraint satisfaction, iterations and cost: agent vs rule proposer on the R1 suite |
| R1-P Public benchmark | `/benchmark` page and `docs/BENCHMARK.md`, reproducible |

**Exit gate:** loop meets the stated constraint on ≥ 70 % of feasible benchmark
tasks without touching holdout; every iteration has change set, proposer, reason,
result, cost; the agent proposer is default only where R2 shows it wins.

## Phase 7 — Release, batch prediction, monitoring and agentic MLOps

| Plan | Outcome |
| --- | --- |
| P7.1 Safe package + feature contract | skops/native formats, Pandera schema from DCLab contract (incl. as-of availability), pickle quarantined |
| P7.2 Release + batch prediction | `model_releases`; extends P4.9 `batch_predictions` with releases; Studio release and scoring actions |
| P7.3 MLflow mirror (optional) | Telemetry mirror behind a port; `tracking_degraded` blocks promotion |
| P7.4 Monitoring + drift | `monitoring_windows`, Evidently calculations, drift investigation check |
| P7.5 Rollback + UI | Champion ref rollback, Models and Monitoring pages per prototype; assistant operations tools (A7) |
| P7.6 Business Outcomes | `/outcomes` for the client role: plain-words results, weekly list, questions, trust and limits, read-only Q&A |
| P7.7 Per-row reasons | Deterministic explainer in the worker; top reasons per scored row |
| P7.8 Ops agent | Monitor → diagnose → retrain branch → Critic → release proposal → rollback, each step at its workspace trust level |

**Exit gate:** ModelVersion → release → batch scoring → drift window → Ops-agent
retrain proposal → approved release → rollback, end to end; API never loads a
model; with AI off, drift still produces a deterministic finding.

## Phase 8 — Hosted beta

| Plan | Outcome |
| --- | --- |
| P8.1 One-cloud deployment | OpenTofu, managed Postgres, object storage, secrets, api/web/worker services |
| P8.2 Security & recovery gate | Adversarial tenancy, secrets, CSP, backup/restore drill, agent/LLM exposure review |
| P8.3 Compute placement | Worker pools; one external provider for heavy jobs (SkyPilot), cost recorded and shown per experiment |
| P8.4 Metering & plans | Usage records (compute, storage, LLM tokens/cost, agent and assistant runs), quotas, entitlements enforced; usage page incl. assistant spend (A8) |
| P8.5 Observability | OTel traces/metrics incl. agent runs, alerts, runbooks; Operator console (P8.5-UI) |
| P8.6 Open-source readiness | Package boundaries for engine/SDK/MCP, license, extraction plan |
| P8.8 Notifications | Email for inbox items, run completion, drift; signed webhooks |
| P8.7 Data safety (old S0-P05C–E) | Content/malware scanner and classifier adapters, resumable quarantine worker, retention and deletion jobs, residency enforcement, operator quarantine-review UI (see ADR 0005) |

**Exit gate:** the design partners from Phase 4 (3 or more) complete a real
project on the hosted beta without database intervention.

## Phase 9 — Expansion (each needs a trigger + ADR)

First candidates, ordered by expected partner demand: **`dlt` connectors
(Postgres, S3)** so teams can start from their own tables, then **forecasting
ModelType** ("how much next month?"). The rest stays demand-gated:

NOOA CodeAct sandbox lane (feature engineering, after P5.0) · notebook/IDE
"ML linter" plugin · R4 public evidence library (solved-problem cards with proof,
similar-problem retrieval) · forecasting ModelType · SLM fine-tuning ModelType on
external GPU · DuckDB DataScanPort for large data · `dlt` connectors (Postgres,
S3, CRM) · stateful Jupyter runtime (RT plan) · business decision layer (old Scope 8 /
Decision.ai; decides removal or rework of the frozen legacy archive surfaces,
which must be removed or formally re-homed by the hard deadline 2027-03-31 —
see [S0-P04D gate](../verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md)) ·
second cloud · online serving.
