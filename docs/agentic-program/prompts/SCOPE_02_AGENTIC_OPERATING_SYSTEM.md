# Scope 2 execution prompts — complete agentic operating system

Start only after verified Scope 1. Apply `README.md` and
`EXECUTION_STANDARD.md`. Agents supervise the whole deterministic pipeline in
read, shadow, critique and proposal modes. Domain writes, builds, exports,
external actions and arbitrary code remain disabled.

## Scope implementation boundary

Extend Scope 1 through cohesive `domain/agent_supervision.py`,
`services/agent_supervision_*.py`, code-owned specialist modules and a resource
router such as `api/v1_agent_operations.py`. Proposals are typed immutable
records, not SQL/code blobs. Specialists call existing dataset, ProblemSpec,
scientific, model-build, evidence, artifact and observability query services.
Extend the same pinned raw LangGraph runtime through code-owned supervisor and
specialist subgraphs. Do not introduce PydanticAI, `pydantic-graph`, LangChain
`create_agent`, another checkpointer authority or a hidden specialist tool loop.

## Plan 2.1 — multi-agent contracts and governance

**Contract.** The supervisor owns a persisted DAG, global completion and shared
budget. Each specialist receives the intersection of parent authority and a
smaller context/tool set. Conflict never resolves through silent majority.

### S2-P01A — operating-model ADR and specialist boundaries

```text
Write the multi-agent ADR defining supervisor, Dataset Steward, Problem/Plan
Architect, Preparation/Feature Reviewer, Leakage/Validation Critic, Experiment
Director, Candidate/Metric Critic, Artifact/Provenance Auditor, audience-safe
Reporters and Reliability/Recovery Analyst. For each specify objective, typed
input/output, allowed read tools, forbidden behavior, citations, escalation and
completion. Define when the single Scope 1 agent is sufficient and how each
specialist maps to a versioned LangGraph subgraph invoked through the same
ToolRunner/gateway/checkpointer boundary. Add contract schemas/tests only; do
not spawn specialists or add another runtime.
```

### S2-P01B — task DAG, review, conflict and proposal contracts

```text
Define versioned Task, Delegation, Review/Critique, Conflict and Proposal value
objects. Tasks carry dependencies, priority/deadline, expected result schema and
stop conditions. Reviews append verdict/concerns/evidence/revision request;
conflicts list positions and deterministic/human resolver; proposals bind target
resource/version, canonical typed patch, expected effect, risk and validation.
Reject cycles, arbitrary code/SQL and mutable historical reviews in pure tests.
```

### S2-P01C — hierarchical authority and budget policy

```text
Define a pure derivation function where child workspace/project/resource/data/
model/tool/risk/deadline/budget authority is the intersection of parent and
specialist policy. Reserve child steps, calls, tokens, cost, time, bytes and job
slots atomically and settle unused capacity to the parent. Bound depth, fan-out,
parallelism and total tasks. Add property tests proving monotonic narrowing,
global exhaustion and cancellation propagation.
```

### S2-P01D — graph release and compatibility contract

```text
Define immutable AgentGraphVersion containing node specialist versions, edges,
input/output schemas, conflict policy, budget allocation, concurrency and stop
rules. Specify draft/shadow/canary/active/retired compatibility with prompt,
model, tool and data policy releases. Canonical digest includes every behavior-
relevant field. Add validation for missing nodes, cycles, incompatible schemas,
unbounded branches and retired dependency. Bind each release to a code-owned
LangGraph topology key/digest and compatible pinned runtime version; never store
or execute LLM-generated Python/graph code.
```

### S2-P01E — multi-agent threat and acceptance gate

```text
Threat-model delegation storms, confused deputy, collusion, poisoned specialist
output, prompt/tool self-promotion, self-approval, hidden memory, cross-agent
leakage, conflict manipulation and cost amplification. Map each threat to a
contract assertion/test/kill switch. Publish state/DAG/sequence diagrams and
confirm deterministic services remain final authority. Do not permit persistence
implementation to start with unresolved authority or conflict rules.
```

## Plan 2.2 — task, delegation, review and proposal persistence

**Contract.** Use current SQLAlchemy metadata, composite tenant constraints and
append-only history. Large task/review/proposal bodies use protected artifacts;
queryable state and digests remain in PostgreSQL.

### S2-P02A — task and dependency schema

```text
Add AgentTask and dependency edges bound to workspace/project, supervisor run,
graph/node version, objective/result schema, state, priority, deadline, input/
output digest, attempts and stop reason. Enforce same-run/same-tenant edges,
unique dependency pair and acyclic DAG through service validation plus defensive
constraints. Index runnable dependency/state and run timeline queries. Add an
additive migration and PostgreSQL integrity tests.
```

### S2-P02B — delegation and child-allocation schema

```text
Add AgentDelegation linking parent task/run, child task/run, authority snapshot,
budget reservation, depth and status. Persist canonical parent/child scope and
prove the child snapshot is not wider before insert. Enforce same workspace/
project, one child allocation per idempotency key and immutable accepted
delegation. Add concurrent allocation, duplicate, cancellation and overflow
tests with real PostgreSQL.
```

### S2-P02C — review, conflict and proposal schema

```text
Add append-only AgentReview, AgentConflict and AgentProposal. Reviews bind
reviewer version, subject version/digest, verdict, structured concerns, citations
and requested revision. Conflicts bind competing outputs and resolution state.
Proposals bind target type/id/version/digest, versioned patch schema, expected
effect, risk, validation and supersession. Store large bodies as artifacts and
reject cross-tenant/unknown target types.
```

### S2-P02D — supervision events and query indexes

```text
Add versioned supervision events for task ready/claimed/completed, delegation,
review, conflict, proposal and escalation with per-run ordering and request/trace
correlation. Define audience-safe projections and opaque cursors. Add measured
indexes for runnable tasks, unresolved conflicts/proposals and operator timelines;
verify query plans using bounded graph fixtures. Never put full prompts/outputs
or high-cardinality labels into events/metrics.
```

### S2-P02E — migration, retention and reconstruction gate

```text
Test empty/live-head upgrade, forward repair, task DAG constraints, concurrent
claim/allocation, immutable review/proposal history and two-workspace substitution.
Define retention/deletion for task/review bodies, artifacts and audit skeletons;
source invalidation must mark derived proposals unusable. Reconstruct a complete
supervision timeline after process loss from rows/events/digests. Record migration
and query evidence before specialist services are added.
```

## Plan 2.3 — prompt, model, data, tool and evaluation operations

**Contract.** Extend Scope 1 registries to governed promotion. Runtime never
edits active releases; promotion is an authorized deterministic service with
separation of duties and immutable evaluation evidence.

### S2-P03A — release lifecycle and PromotionRecord persistence

```text
Add or complete draft -> candidate -> shadow -> canary -> active -> retired
lifecycles for graph/agent/prompt/model/tool/data/budget releases. Add
PromotionRecord with environment, compatible release set/digests, evaluation
run, proposer/approver, previous release, deployment/canary, activation and
rollback. Enforce immutable bodies, one compatible active set and separation of
duties. Add migration, concurrency and rollback-lineage tests.
```

### S2-P03B — model/provider/data routing service

```text
Implement deterministic server routing by purpose, data class, region,
retention/training policy, structured/tool capability, quality tier, latency and
cost ceiling. Fallback may only be policy-compatible and equal-or-stricter; no
provider selection by user text or LLM output. Return selected release IDs and
reason. Test unavailable region, conflicting requirements, retired models,
provider outage and strict fail-closed behavior.
```

### S2-P03C — evaluation persistence and runner

```text
Add EvaluationSuite/Case/Run/Result with immutable fixtures/artifact digests,
release set, environment, deterministic assertions, calibrated rubric versions,
baseline, per-case result and aggregate. Implement offline fake, controlled
provider, adversarial, shadow and canary run modes as durable jobs. Bound fixture
size/provider spend and prevent customer content in shared suites. Test resume,
duplicate run and partial provider failure.
```

### S2-P03D — promotion, rollback and kill-switch services

```text
Implement propose/review/promote/rollback through application services with
current admin capability, exact compatible digest, required passing safety gates
and transactionally consistent active pointer. Rollback creates a record and
restores a known compatible set; it never edits history. Add global/purpose/
workspace provider, model, graph and tool kill switches evaluated at dispatch.
Test stale approval, concurrent promotion and emergency disable.
```

### S2-P03E — admin API and operations UI

```text
Add protected `/v1` admin resources and UI for release metadata, evaluation
status/deltas, propose/review/promote/rollback and kill switches. Raw prompt body,
model credentials and evaluation sensitive fixtures require distinct capabilities
and are not sent to ordinary operators. Use ETag/idempotency and stable errors.
Add API/component/browser tests for role separation, stale version, denied body
and rollback confirmation.
```

### S2-P03F — LLM operations release gate

```text
Run migration, promotion-race, role-separation, routing/fallback, safety-eval,
shadow/canary and rollback/kill-switch drills. Verify a safety assertion blocks
promotion despite aggregate quality and runtime requests retain the exact release
set. Add dashboards/alerts/runbooks for evaluation regression, cost, breaker and
release changes. Record a reversible inactive release before specialist work.
```

## Plan 2.4 — Dataset Steward

**Contract.** Add one specialist over existing ingestion, DatasetColumn,
profile, readiness, classification and data-access queries. Output is a cited
assessment and typed proposal; it cannot publish data or change classifications.

### S2-P04A — steward input/output and tool release

```text
Define DatasetStewardInput with dataset/version/purpose and minimal envelope;
output contains readiness findings, quality/drift concerns, mapping/classification
questions, proposed typed patches, uncertainty and citations. Register only
dataset/source/access/schema/profile/readiness/drift read tools with strict
bounds. Add schema/tool contract tests and an agent version/prompt candidate.
Do not expose rows or mutation tools.
```

### S2-P04B — deterministic evidence assembler

```text
Build a service that assembles authorized Dataset, DatasetColumn, DataSource,
DataAccess, IngestionRun, profile, quarantine/classification and prior-version
drift facts into a minimal ContextEnvelope. Compute deterministic readiness and
hard blockers before LLM invocation. Every fact maps to a citation/version/
digest. Test null policy, partial profile, failed ingest, deleted object and
cross-workspace access.
```

### S2-P04C — steward execution and proposal validation

```text
Implement one specialist task handler using the gateway and strict structured
output. Validate proposed mappings/roles/classification changes against allowed
patch schema, current source version and deterministic policy; impossible or
unsafe suggestions become concerns/escalations. Persist review/proposal/events
and settle child budget. No proposal is applied. Test malformed, overconfident,
uncited and stale-source output.
```

### S2-P04D — drift, privacy and adversarial evaluation

```text
Create synthetic datasets covering type ambiguity, target leakage indicators,
missingness, duplicates, schema drift, sensitive columns, poisoned names and
conflicting metadata. Assert hard blockers, citation correctness, no row/secret
exposure, calibrated uncertainty and safe questions. Compare steward versus
deterministic-only baseline on predefined usefulness, cost and latency measures.
```

### S2-P04E — steward shadow gate

```text
Add steward metrics/operator view for findings, proposal types, policy blocks,
latency/cost and disagreement with deterministic readiness. Run in shadow for
versioned workflows, inspect false positives/negatives and exercise kill switch,
provider outage and source invalidation. Promote only to visible proposal mode,
never auto-apply, after hard safety and value thresholds pass.
```

## Plan 2.5 — Problem and experiment-plan architects

**Contract.** Specialists propose immutable ProblemSpec and scientific-plan
versions using existing target intent, problem, scientific and lineage services.
Clarifying questions are preferred to unsupported assumptions.

### S2-P05A — Problem Architect contract and evidence

```text
Define typed objective/ProblemSpec proposal with target, task, positive class,
entity, prediction time, label window, horizon, constraints, business utility,
ambiguities, questions and citations. Assemble minimal authorized schema/profile/
intent evidence and deterministic target candidates. Register read-only tools.
Reject absent version/digest or unsupported target/task claims in schema tests.
```

### S2-P05B — Problem Architect execution and validation

```text
Execute the specialist through structured output, then validate columns/types,
target leakage/time semantics, access/classification, current source version and
required user decisions using deterministic services. Persist a typed diff or
clarifying-question result, never edit ProblemSpec. Test ambiguous target,
classification, binary positive class, time-series and stale evidence cases.
```

### S2-P05C — Experiment Plan Architect contract

```text
Define a proposal for metric/direction, validation method/folds/groups/time,
holdout, split seed, preparation/feature rules, candidate/resource portfolio and
stop criteria bound to a ProblemSpec/dataset digest. Encode scientific hard
constraints separately from agent rationale. Reuse existing scientific-plan
types and verifier; do not invent a parallel training specification.
```

### S2-P05D — plan validation and architect evaluation

```text
Validate every proposed plan through target intent, leakage, validation and
resource policy before persistence. Create fixtures for imbalance, groups,
temporal ordering, small data, unsupported metric, excessive compute and missing
entity/time. Assert the specialist asks when evidence is insufficient and cannot
weaken holdout/evidence locks. Measure valid-plan rate, useful questions, cost
and latency against a deterministic baseline.
```

### S2-P05E — architect shadow release

```text
Integrate Problem/Plan Architect tasks into the supervision graph after Dataset
Steward dependencies, with proposal/review/conflict events and human escalation.
Run shadow on versioned completed and pre-build cases; inspect disagreements and
exercise source/policy invalidation. Add dashboards/runbook/kill switch and
enable proposal visibility only after safety gates pass.
```

## Plan 2.6 — preparation, feature, leakage and validation critics

**Contract.** Critics review a frozen proposed plan and deterministic evidence.
They emit typed findings/diffs; existing preprocessing/leakage/validation code
and verifier remain authoritative.

### S2-P06A — preparation and feature review contract

```text
Define typed findings for column role, missing treatment, encoding/scaling,
rare category, feature group, leakage risk and unsupported transformation with
severity, evidence, proposed plan diff and confidence. Assemble fold-safe
preparation/feature metadata from existing services and `ml/features.py`/
`feature_groups.py` contracts without exposing raw rows. Add schema/tool tests.
```

### S2-P06B — Leakage/Validation Critic contract

```text
Define findings for target/temporal/group/duplicate/post-outcome leakage,
pre-split fitting, holdout misuse, metric mismatch and invalid CV. Include a
hard-block recommendation only when supported by deterministic rule/evidence;
otherwise request review. Register minimal plan/dataset/lineage read tools and
add hostile/uncited output validation. The critic cannot approve its own fix.
```

### S2-P06C — critic execution and deterministic precedence

```text
Implement specialist handlers and proposal validators. Run deterministic
verifiers before and after LLM review; deterministic failure always blocks and
LLM “safe” cannot override it. Persist separate reviews, conflicts and proposed
immutable plan revisions. Deduplicate equivalent findings by canonical digest.
Test provider failure, contradictory critics, stale plan and budget exhaustion.
```

### S2-P06D — adversarial scientific evaluation

```text
Build versioned fixtures for leakage variants, fold-local preprocessing, grouped/
temporal splits, rare classes, identifier memorization, duplicate entities,
post-outcome columns and misleading names. Assert recall for deterministic hard
cases, low unsupported-claim rate, citations and no raw exposure. Compare single
critic, paired critics and deterministic-only cost/latency/value.
```

### S2-P06E — critic shadow gate

```text
Place critics after plan proposal and before experiment direction in the graph.
Expose findings/diffs and unresolved conflicts to reviewers without applying
them. Add disagreement/safety/cost metrics, false-positive review workflow and
kill switch. Run replay on completed scientific plans and record whether each
finding would have improved, duplicated or harmed deterministic decisions.
```

## Plan 2.7 — Experiment Director and Candidate/Metric Critic

**Contract.** These agents inspect persisted stage/candidate/fold/metric state,
failure evidence and budgets. They may propose bounded child experiments but do
not create/cancel/retry them until Scope 3.

### S2-P07A — Experiment Director state contract

```text
Define stage-aware input/output for queued/running/failed/completed pipeline
states: diagnosis, next evidence needed, safe recovery proposal, candidate
portfolio concern, stop recommendation and citations. Assemble data from
WorkflowRun/PipelineRun/stages/MlJob/events/verification through audience-safe
queries. Explicitly distinguish observed fact, hypothesis and unsupported state.
Add schema/tool contract tests.
```

### S2-P07B — candidate and metric critique contract

```text
Define findings for CV distribution/stability, candidate failures, resource use,
metric direction/suitability, calibration/fairness where configured, selection
margin and holdout discipline. A child proposal includes one hypothesis, one
typed change digest, parent evidence citations, estimated budget and stop rule.
It cannot use holdout evidence to tune candidates. Add validation/property tests.
```

### S2-P07C — stage-aware specialist handlers

```text
Implement director/critic tasks with state-specific allowed tools and structured
outputs. Validate against current plan/run/candidate versions, deterministic
selection and evidence locks. Persist proposed recovery/child diffs, reviews and
conflicts; deduplicate and stop when terminal/budget/policy changes. Test race
with run progress, failed jobs, partial metrics and provider outage.
```

### S2-P07D — replay and scientific safety evaluation

```text
Replay synthetic and historical-safe completed runs at each stage, including
infrastructure failure, invalid data, unstable candidates, metric ties, resource
exhaustion and misleading holdout results. Score diagnosis accuracy, supported
recommendation, prohibited holdout use, citation validity, cost and latency.
Compare against deterministic status and single-agent baseline; any evidence-lock
violation fails the release.
```

### S2-P07E — experiment supervision shadow gate

```text
Integrate director and critic after approved plan/critic tasks in shadow only.
Add task/run/operator timelines, disagreement categories, proposed child budget
and stop metrics. Exercise cancellation, stale run, restart and graph kill switch.
Review a bounded sample with ML owners before proposal visibility; do not expose
an execute control until Scope 3 command/approval gates.
```

## Plan 2.8 — artifact/provenance audit and audience-safe reporting

**Contract.** Auditors use digests/lineage/verification; reporters derive cited
technical or business representations. Neither changes artifacts or invents
causal claims.

### S2-P08A — artifact and provenance audit contract

```text
Define typed checks for missing/mismatched object, digest/size/media, orphaned or
cross-tenant reference, incomplete lineage, unreproducible environment, stale
report/visualization and evidence-lock mismatch. Reuse artifact, lineage,
reproducibility and verifier services with metadata-only tools. Findings carry
severity, resource/version/digest, deterministic check and repair proposal.
```

### S2-P08B — deterministic reconciliation and auditor handler

```text
Run deterministic object/row/lineage checks before the LLM; use the specialist
only to synthesize/triage supported findings and propose typed reconciliation,
regeneration or quarantine intent. Never fetch arbitrary storage keys or mutate
objects. Persist audit review/proposal/citations. Test missing object, digest
mismatch, duplicate metadata, wrong tenant, deleted source and verifier failure.
```

### S2-P08C — technical and business report contracts

```text
Define two structured outputs over the same evidence: technical report may show
approved scientific detail; business report uses the translation layer and safe
decision language. Each claim declares evidence class, uncertainty and citations;
causal wording is forbidden without qualifying design. Charts reference approved
Visualization/Artifact metadata. Add banned-term, unsupported-claim and malicious
label tests.
```

### S2-P08D — reporter execution and rendering safety

```text
Implement reporter tasks using minimal audience-specific envelopes and validated
structured outputs. Render Markdown/JSON through safe components; never render
provider HTML, script, arbitrary URL or hidden reasoning. Persist report body as
immutable artifact with digest/version and searchable metadata. Invalidate when
source authorization/version changes. Test admin/developer/business projections.
```

### S2-P08E — audit/report shadow gate

```text
Evaluate known artifact faults, reproducibility gaps, technical summaries,
business translations and unsupported causal prompts. Assert deterministic
fault detection, citation coverage, audience separation, XSS safety and no
storage/provider leakage. Add missing-object/report-regression metrics and
runbooks; promote only read/proposal views with independent auditor/reporter
kill switches.
```

## Plan 2.9 — LangGraph supervisor orchestration

**Contract.** The supervisor schedules persisted tasks whose dependencies are
satisfied, applies hierarchical budgets, requests reviews, surfaces conflicts
and produces a cited partial/final synthesis through the same pinned LangGraph
runtime established in Scope 1. One job performs one bounded task transition or
external operation and then yields; specialists are code-owned subgraphs, not
nested autonomous frameworks.

### S2-P09A — decomposition and DAG materialization

```text
Implement validated supervisor decomposition from a supported objective/template
into an AgentGraphVersion DAG compiled from a code-owned LangGraph topology.
Materialize tasks/dependencies/delegations idempotently with current policy
snapshots and child reservations. Reject unknown specialists/tools, cycles,
excessive depth/fan-out or unjustified task creation. The first release permits
only code-owned graph templates with bounded structured parameters; the LLM may
select among authorized templates but cannot generate executable graph code.
```

### S2-P09B — ready-task scheduler and specialist dispatch

```text
Register a bounded supervision handler that claims ready tasks with leases,
re-authorizes parent/child scope, dispatches exactly one specialist operation,
checkpoints result, settles allocation and emits events before scheduling the
next transition through the Scope 1 runtime/checkpointer boundary. Invoke a
specialist as a versioned per-task LangGraph subgraph with narrowed context,
tools and budget; it cannot retain hidden cross-task memory. Enforce graph
parallelism and workspace fairness. Test duplicate claim, checkpoint namespace,
dependency race, child timeout, cancellation and worker restart using PostgreSQL
jobs.
```

### S2-P09C — review, revision and conflict resolution

```text
Implement code-owned review requirements and conflict rules per output type.
Route deterministic invariant conflicts to hard block, resolvable schema issues
to bounded revision, and judgment disputes to human escalation with all cited
positions. Limit review/revision rounds and budget. No specialist reviews or
approves its own release/proposal. Test oscillation, collusion-like agreement,
poisoned reviewer output and stale subject version.
```

### S2-P09D — partial result, escalation and final synthesis

```text
Define global completion for all-success, useful-partial, blocked, cancelled,
expired and failed graphs. Build final synthesis only from validated task outputs
and citations, explicitly listing unresolved questions/conflicts and omitted
failed specialists. Human escalation has durable state, required capability,
expiry and resume semantics. Test late results, membership change and no-supported-
answer without inventing conclusions.
```

### S2-P09E — global limits and recovery

```text
Enforce graph-wide steps/tasks/depth/fanout/parallelism/tokens/cost/time/bytes/jobs
plus per-specialist limits. Reconcile lost leases, orphan child reservations,
ready tasks without jobs and terminal graphs with active children. Cancellation
propagates top-down while completed evidence remains. Add failure injection at
every task/delegation/review checkpoint, including DCLab/runtime divergence, and
prove deterministic reconstruction without invoking a second agent loop.
```

### S2-P09F — supervisor system gate

```text
Run complete fake-provider graph cases for dataset-to-report supervision,
clarifying question, deterministic block, critic conflict, specialist outage,
partial result, budget exhaustion, cancellation and restart. Verify authority
narrowing, proposal-only catalog, citations, events and terminal accounting.
Verify the exact LangGraph/checkpointer release, code-owned graph digest, bounded
subgraph namespaces and absence of PydanticAI/high-level agent dependencies.
Benchmark against Scope 1 single agent and enable only shadow graphs after
observed safety/value/cost evidence.
```

## Plan 2.10 — agentic operations UI

**Contract.** Extend Agent Studio with server-projected graph/task/review/
proposal/evaluation state. UI controls request deterministic services; they do
not mutate rows or interpret raw agent/provider bodies.

### S2-P10A — API projections and client hooks

```text
Add bounded `/v1` resources for graph runs, tasks/dependencies, specialist
activity, reviews, conflicts, proposals, escalations and release/evaluation
metadata. Define role/audience projections, pages/cursors/ETags and safe diff
schemas. Add Python/TypeScript client types/hooks keyed by workspace/run. Raw
prompt/output/policy bodies remain separately protected or absent.
```

### S2-P10B — task graph and specialist timeline

```text
Build accessible graph/list fallback, task detail and chronological event views
showing state, dependencies, specialist/version, citations, bounded budget and
safe failure. Support large graphs through pagination/virtualization rather than
loading all nodes. Handle partial/out-of-order updates, reconnect and workspace
switch. Add component tests for every state and keyboard navigation.
```

### S2-P10C — review, proposal diff and conflict experience

```text
Render typed resource-aware diffs with base/current/proposed version, validation,
risk, expected effect and citations. Provide review/escalation controls only when
the server exposes capability/state; use ETag and explicit confirmation. In Scope
2 controls may review/reject/request revision, never execute domain mutation.
Test stale proposal, concurrent reviewer, malicious labels and denied role.
```

### S2-P10D — release/evaluation administration

```text
Build protected views for graph/agent/prompt/model/tool/data/budget release
metadata, compatibility, evaluation assertions/deltas, promotion history,
kill-switch status and rollback. Keep raw prompt body and secrets out of ordinary
admin responses. Require reason/confirmation for promotion/rollback and show
immutable evidence links. Add separation-of-duty and stale-version E2E.
```

### S2-P10E — operations UI gate

```text
Run component/accessibility/browser journeys for running/partial/blocked/failed/
cancelled graphs, specialist conflict/revision, invalidated proposal, evaluation
failure, promotion/rollback and workspace switch. Scan DOM/network/storage for
raw prompts, hidden reasoning, secrets, internal paths and cross-tenant data.
Verify large graph performance with recorded fixtures and document UI kill switch.
```

## Plan 2.11 — whole-pipeline shadow evaluation

**Contract.** Use versioned synthetic and authorized replay fixtures. Promotion
requires hard safety assertions plus predefined incremental value over the Scope
1/deterministic baseline; higher cost/latency must be visible.

### S2-P11A — end-to-end scenario corpus

```text
Create versioned scenarios from ingest/profile/classification through problem,
plan, preparation, leakage, experiment state, candidate evidence, artifacts and
reports. Include clean, ambiguous, incomplete, drifted, leaking, failed and
malicious cases with expected deterministic facts, allowed proposals, required
questions/conflicts and forbidden behavior. Use synthetic/de-identified fixtures
and immutable source digests.
```

### S2-P11B — counterfactual replay harness

```text
Implement a durable replay mode that feeds historical-safe snapshots to the
single agent and multi-agent graph without changing domain state. Freeze release
set/time/random/provider fixtures, capture task/tool/proposal/citation/budget
traces and compare to expected assertions. Prevent current/future evidence from
leaking into an earlier replay point. Test reproducibility and partial resume.
```

### S2-P11C — failure, attack and recovery campaign

```text
Inject provider/tool timeouts, malformed/poisoned output, worker death, duplicate
jobs, stale sources, authorization revocation, budget races, delegation storms,
critic conflict, missing artifacts and policy kill switch. Assert bounded stop,
no widened authority/write, correct partial result, redacted evidence and full
reconstruction. One tenant/security/scientific violation fails the release.
```

### S2-P11D — specialist ablation and value analysis

```text
Run code-owned graph variants removing or combining specialists. Measure hard-
case detection, useful supported proposal rate, question quality, unsupported
claim/conflict rate, task/tool count, latency, tokens and cost. Define uncertainty
and reviewer sample size; do not promote complexity whose incremental value does
not exceed the agreed threshold. All variants compile through the same
LangGraph runtime and DCLab policies; do not compare by adding another framework.
Store analysis/version/digests as evidence.
```

### S2-P11E — dashboards, thresholds and operational drill

```text
Create dashboards for graph success/partial/block/failure, task/review/conflict,
proposal invalidation, safety assertions, provider/tool latency, budget and
baseline delta. Set alert/error-budget thresholds from observed shadow results.
Drill graph/provider/specialist kill switches, rollback, worker drain and stuck
graph recovery. Assign owners and link exact runbooks.
```

### S2-P11F — Scope 2 promotion gate

```text
Run migrations, full regression, scenario corpus, counterfactual replay,
adversarial/failure campaign, ablations, API/UI and operator recovery in a
production-shaped environment. Publish exact release set and evidence showing
pipeline coverage, authority narrowing, citations, proposal-only behavior and
incremental value/cost. Enable proposal mode only for allowlisted workspaces;
do not start Scope 3 until all hard gates pass.
```
