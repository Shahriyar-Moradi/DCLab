# DCLab MVP roadmap (new order)

Principle: build from the inside out — **correct engine → state graph → agent
interface → use and trust + Studio + assistant → verify/improve → internal agents
→ operations → hosted beta → expansion** — and make every phase usable by a real
user at its end.

Revised 2026-10-04 (founder decision):

1. **Every phase ships its UI.** A capability is done only when it is reachable in
   Studio or listed API-only by design in [UI_COVERAGE.md](UI_COVERAGE.md). Phases
   1–3 were backend-first; their UI catch-up is Phase 4 Stage 2.
2. **Use and trust come first.** Scoring new data, five core trust checks and a
   model card move from Phases 5/7 into Phase 4, before the Studio catch-up UI.
3. **The in-app assistant is a track (A)** that starts in Phase 4 and grows every
   phase ([prompts/ASSISTANT.md](prompts/ASSISTANT.md)): one tool catalog shared
   with MCP, read tools run, write tools become confirm cards.
4. **One audience: data scientists and ML engineers.** Decision.ai and marketing
   pages leave the navigation (P4.12-A).
5. **Design partners join at the end of Phase 4**, not Phase 8, and their feedback
   reorders Phases 5–7.
6. **Merge cadence:** build 2–3 phases (or Phase 4 stages) on one branch, run the
   full suite and CI once at the merge point, merge, and fill every STATUS.md
   SHA in one go (EXECUTION_GUIDE.md §5).

Estimates assume one founder + Claude Code; customer discovery (≥ 5 conversations
per week) runs in parallel from day one.

| Phase | Name | Weeks | Ends with a user being able to… |
| --- | --- | --- | --- |
| 0 | Stabilize the foundation | 1–2 | trust that `main` is green and reproducible |
| 1 | One correct ML engine | 2–3 | upload any tabular CSV and get a scientifically valid, baseline-beating model |
| 2 | ML state graph | 3 | see lineage, branch an experiment, see what became stale |
| 3 | Agent interface: `/v1`, SDK, CLI, MCP | 2–3 | drive DCLab from Claude Code / Cursor |
| 4 | Studio, use and trust, assistant | 5–6 | upload a CSV in the web UI, train, see trust findings, read a model card, score new data, and ask the assistant to explain or propose the next experiment |
| 5 | Verify & Improve | 3–4 | ask "optimize recall with precision ≥ 70 %" (in Studio, the assistant or MCP) and get audited iterations |
| 6 | Internal agents (NOOA) + Jev shadow | 3 | receive cited agent proposals in Studio and the assistant, and accept/reject them |
| 7 | Release, batch prediction, monitoring | 3 | release a model, schedule scoring, watch drift charts, roll back from the UI |
| 8 | Hosted beta | 3–4 | use DCLab in the cloud with design partners |
| 9 | Expansion (demand-gated) | — | each item its own ADR |

Track A (assistant, parallel from Phase 4): **A1–A4** ADR, gateway, read-only
assistant, panel, confirm cards (Phase 4) → **A5** improve (Phase 5) → **A6** agent
skills (Phase 6) → **A7** operations (Phase 7) → **A8** metering (Phase 8).

Merge points (one full suite + CI each): **MP-4a** after Phase 4 Stages 1–3 ·
**MP-4b** after Phase 4 Stages 4–5 (design partners start) · **MP-56** after
Phases 5–6 · **MP-78** after Phases 7–8.

Track R (R&D, parallel): **R1** benchmark harness (Phase 1) → **R2** improve-loop
benchmark (Phase 5) → **R1-P** public benchmark page (Phase 5) → **R3** agent/Jev
evaluation corpus (Phase 6) → **R4** public evidence library / "solved problems with proof" (Phase 9).

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

## Phase 4 — Studio, use and trust, assistant

| Stage | Plan | Outcome |
| --- | --- | --- |
| 1 Use & trust (backend + MCP) | P4.9 Score new data | Upload a scoring file → predictions from a chosen ModelVersion (worker only, feature-contract check); first slice of P7.2 |
| | P4.10 Five core checks | Leakage, overfit gap, duplicates, imbalance, too-good-to-be-true as plain-language findings on every run |
| | P4.11 Model card | Drivers, metric in business words, baseline comparison, risks, single holdout result |
| 2 UI foundation + catch-up (Phases 1–3) | P4.0 UI–API sync | Generated TS types from `v1_openapi.json`, drift fails CI, coverage test against UI_COVERAGE.md |
| | P4.1 Project IA + new-project wizard | Projects → Graph / Data / Experiments / Models / Decisions; upload → target → train in the browser |
| | P4.2 Graph view | Interactive lineage with stale markers and impact |
| | P4.3 Node inspectors | Reason, formula, evidence and code for data/feature/split/experiment/model nodes |
| | P4.4 Compare, branch, refs, decide | Diff, branch form, champion ref move, decision timeline |
| | P4.5 Client run page cleanup | No technical internals for client role |
| | P4.8 Connect an agent | Token + `.mcp.json` snippet + CLI login in Settings |
| 3 UI for use & trust | P4.9-UI / P4.10-UI / P4.11-UI | Score page, findings panel, model card page |
| 4 Assistant (Track A) | A1–A4 | ADR 0009, LLM gateway + agent tables, read-only assistant (SSE), Studio panel, confirm cards, golden conversations |
| 5 Focus, demo, partners | P4.12 One story · P4.13 Demo kit · P4.14 Partner kit · P4.6 Killer-flow E2E | DCLab-only navigation, 2-minute demo (MCP + Studio), partner install, full Playwright flow |

**Exit gate:** Playwright, against the real backend with the fake LLM: new project
→ upload → train → findings → inspect feature → view code → assistant proposes a
branch → confirm → compare → accept → score new data → model card. Every
"planned P4.x" row in UI_COVERAGE.md is "exists"; with the assistant disabled all
of it still works. Founder steps: demo video recorded; 2–3 design partners booked.

## Phase 5 — Verify & Improve

| Plan | Outcome |
| --- | --- |
| P5.1 Investigation checks | The remaining checks on top of P4.10 (contamination, calibration, fold instability, subgroup, drift between train/holdout periods, …) → ≥ 12 in total, shown in the existing findings panel |
| P5.2 Objective optimizer | Constraint-aware threshold/cost optimization on validation folds; operating-point chooser in Studio (P5.2-UI) |
| P5.3 ADR 0007 improve loop | Typed action space, budgets, stop rules, holdout rules |
| P5.4 Improve loop engine | Deterministic proposer + Optuna; each iteration a child experiment with change set |
| P5.5 Loop UX | Studio timeline + Improve button + MCP `improve` tool + accept/reject; assistant `improve` (A5) |
| R2 Loop benchmark | Constraint satisfaction rate and cost on the R1 suite |
| R1-P Public benchmark | `/benchmark` page and `docs/BENCHMARK.md`, reproducible |

**Exit gate:** loop meets the stated constraint on ≥ 70 % of feasible benchmark
tasks without touching holdout; every iteration has change set, reason, result, cost.

## Phase 6 — Internal agents (NOOA) and Jev shadow

| Plan | Outcome |
| --- | --- |
| P6.1 ADR 0008 agent runtime | NOOA Predict-only, budgets, envelope, ledger (per AGENTS_NOOA_JEV.md) |
| P6.2 Agent persistence for NOOA | Extends A2-A tables for agent kinds; `semantic_decision_answers` (gateway and agent tables already built in A2-A) |
| P6.3 Runtimes | Fake runtime + pinned NOOA adapter + CodeAct ban test + event mapping |
| P6.4 Agent classes | DatasetInvestigator, ExperimentPlanner, ExperimentCritic |
| P6.5 Improvement hypothesis agent | Optional proposer inside the Phase 5 loop |
| P6.6 Proposal review | API/MCP/Studio accept/reject → command service → decision record; one proposals panel for agents and assistant; assistant skills (A6) |
| P6.7 Jev shadow | `SemanticDecisionPort`, 5 purposes in shadow, answers ledger; development-only shadow view (P6.7-UI) |
| P6.8 Agent & Jev gate (R3) | Golden tasks, citation validity, ablation vs deterministic; release decision per agent/purpose; Settings → Agents (P6.8-UI) |

**Exit gate:** each agent and Jev purpose has a recorded decision; disabling NOOA
and Jev leaves every Phase 1–5 test green.

## Phase 7 — Release, batch prediction, monitoring

| Plan | Outcome |
| --- | --- |
| P7.1 Safe package + feature contract | skops/native formats, Pandera schema from DCLab contract, pickle quarantined |
| P7.2 Release + batch prediction | `model_releases`; extends P4.9 `batch_predictions` with releases; Studio release and scoring actions |
| P7.3 MLflow mirror (optional) | Telemetry mirror behind a port; `tracking_degraded` blocks promotion |
| P7.4 Monitoring + drift | `monitoring_windows`, Evidently calculations, drift investigation check |
| P7.5 Rollback + UI | Champion ref rollback, Studio models tab with releases, batch runs, drift charts; assistant operations tools (A7) |

**Exit gate:** ModelVersion → release → batch scoring → drift window → rollback,
end to end, API never loads a model.

## Phase 8 — Hosted beta

| Plan | Outcome |
| --- | --- |
| P8.1 One-cloud deployment | OpenTofu, managed Postgres, object storage, secrets, api/web/worker services |
| P8.2 Security & recovery gate | Adversarial tenancy, secrets, CSP, backup/restore drill |
| P8.3 Compute placement | Worker pools; one external provider for heavy jobs (SkyPilot), cost recorded and shown per experiment |
| P8.4 Metering & plans | Usage records, quotas, entitlements enforced; usage page incl. assistant spend (A8) |
| P8.5 Observability | OTel traces/metrics, alerts, runbooks; admin system health page (P8.5-UI) |
| P8.6 Open-source readiness | Package boundaries for engine/SDK/MCP, license, extraction plan |
| P8.7 Data safety (old S0-P05C–E) | Content/malware scanner and classifier adapters, resumable quarantine worker, retention and deletion jobs, residency enforcement, operator quarantine-review UI (see ADR 0005) |

**Exit gate:** the design partners from Phase 4 (3 or more) complete a real
project on the hosted beta without database intervention.

## Phase 9 — Expansion (each needs a trigger + ADR)

First candidates, ordered by expected partner demand: **`dlt` connectors
(Postgres, S3)** so teams can start from their own tables, then **forecasting
ModelType** ("how much next month?"). The rest stays demand-gated:

NOOA CodeAct sandbox lane (feature engineering) · notebook/IDE "ML linter" plugin ·
R4 public evidence library (solved-problem cards with proof, similar-problem
retrieval) · forecasting ModelType · SLM fine-tuning ModelType on external GPU ·
DuckDB DataScanPort for large data · `dlt` connectors (Postgres, S3, CRM) ·
stateful Jupyter runtime (RT plan) · business decision layer (old Scope 8 /
Decision.ai; decides removal or rework of the frozen legacy archive surfaces,
which must be removed or formally re-homed by the hard deadline 2027-03-31 —
see [S0-P04D gate](../verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md)) ·
second cloud · online serving.
