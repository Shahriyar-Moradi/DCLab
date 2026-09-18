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
They also use the Scope 1 lifecycle projection and ProjectDecisionService; they
never reconstruct a competing project graph or memory from task/message history.
Extend the same pinned raw LangGraph runtime through code-owned supervisor and
specialist subgraphs. Do not introduce PydanticAI, `pydantic-graph`, LangChain
`create_agent`, OpenAI Agents, another checkpointer authority or a hidden
specialist tool loop inside that runtime. Plan 2.12 is the approved separate
Deep Investigation harness; Plan 2.13 is the approved whole-run OpenAI Agents
adapter. Neither can embed, invoke or be invoked by the supervisor, a
specialist, `agent.turn.v1` or the other runtime. Every AgentRun binds exactly
one code-owned runtime kind/version.
All Scope 2 agents consume DCLab-owned lifecycle, decision, evidence and
normalized monitoring/tracking projections only. They never connect to MLflow,
load model packages, import Pandera/Evidently/skops, or receive provider IDs,
storage locators or credentials. Plan 3.0 owns those adapters; a later read-tool
extension can expose only authorized DCLab projections and citations.
Dataset investigation reuses S0-P09 deterministic scan/profile services. An
agent may request only a registered operation over an authorized DatasetArtifact
and receive bounded aggregate/citation results. It never sees DuckDB, SQL,
paths, Arrow streams or scan configuration, and Polars is not introduced.

## AWS/GCP portability requirements

Supervisor, specialist, Deep Investigation and OpenAI-adapter product contracts
are identical on AWS and GCP. Each worker is one digest-pinned OCI image with a
provider-neutral workload/resource profile; EKS/GKE identity, network and
runtime-state endpoints are injected only by deployment adapters. No agent may
see cloud credentials, bucket URLs, cluster APIs or provider resource IDs.
Cancellation, restart, budgets, checkpoints, citations, metrics and kill
switches must pass the same offline suite and both-cloud staging gate.

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
completion. Bind inputs/outputs to canonical lifecycle node IDs/versions and
define which proposed/reviewed outcomes append a ProjectDecisionRecord. Define
when the single Scope 1 agent is sufficient and how each
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
effect, risk, validation and supersession. Link proposal/review/conflict outcomes
to ProjectDecisionRecord rather than adding another memory table; a proposed or
accepted decision still cannot apply the patch. Store large bodies as artifacts
and reject cross-tenant/unknown target types.
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
dataset/source/access/schema/profile/readiness/drift and allowlisted aggregate-
slice read tools with strict bounds. Each scan-backed tool identifies a DCLab
operation/template version and typed dimensions/filters/aggregates; it accepts
no SQL, expression, path, URL or DuckDB setting. Add schema/tool contract tests
and an agent version/prompt candidate. Do not expose rows or mutation tools.
```

### S2-P04B — deterministic evidence assembler

```text
Build a service that assembles authorized Dataset, DatasetColumn, DataSource,
DataAccess, IngestionRun, profile, quarantine/classification and prior-version
drift facts into a minimal ContextEnvelope. Compute deterministic readiness and
hard blockers before LLM invocation. Every fact maps to a citation/version/
digest. When a missing fact requires calculation, call the S0-P09 DataScan
application service with an authorized artifact and registered bounded template;
persist/reuse its normalized DCLab result rather than returning Arrow or scanning
inside the agent worker. Test null policy, partial profile, failed ingest,
deleted object, digest mismatch, scan timeout/cancellation and cross-workspace.
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
exposure, no SQL/template injection, enforced scan limits, calibrated
uncertainty and safe questions. Compare steward versus
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
prove deterministic reconstruction without invoking a second loop inside the
authoritative graph. The later S2-P12 worker is not a recovery target or tool.
```

### S2-P09F — supervisor system gate

```text
Run complete fake-provider graph cases for dataset-to-report supervision,
clarifying question, deterministic block, critic conflict, specialist outage,
partial result, budget exhaustion, cancellation and restart. Verify authority
narrowing, proposal-only catalog, citations, events and terminal accounting.
Verify the exact LangGraph/checkpointer release, code-owned graph digest, bounded
subgraph namespaces and absence of PydanticAI/Deep Agents/high-level agent
dependencies from the API and authoritative worker-agent environment.
Benchmark against Scope 1 single agent and enable only shadow graphs after
observed safety/value/cost evidence.
```

## Plan 2.10 — agentic operations UI

**Contract.** Extend Agent Studio with server-projected graph/task/review/
proposal/evaluation state synchronized with the Scope 1 project lifecycle and
decision timeline. UI controls request deterministic services; they do not
mutate rows or interpret raw agent/provider bodies.

### S2-P10A — API projections and client hooks

```text
Add bounded `/v1` resources for graph runs, tasks/dependencies, specialist
activity, reviews, conflicts, proposals, escalations, linked lifecycle nodes,
project decisions and release/evaluation metadata. Define role/audience
projections, pages/cursors/ETags and safe diff
schemas. Add Python/TypeScript client types/hooks keyed by workspace/run. Raw
prompt/output/policy bodies remain separately protected or absent.
```

### S2-P10B — task graph and specialist timeline

```text
Build accessible agent-task graph/list fallback, task detail and chronological event views
showing state, dependencies, specialist/version, citations, bounded budget and
safe failure. Support large graphs through pagination/virtualization rather than
loading all nodes. Handle partial/out-of-order updates, reconnect and workspace
switch. Clearly distinguish this execution DAG from the ML lifecycle graph and
deep-link tasks to their lifecycle subjects. Add component tests for every state
and keyboard navigation.
```

### S2-P10C — review, proposal diff and conflict experience

```text
Render typed resource-aware diffs with base/current/proposed version, validation,
risk, expected effect and citations. Provide review/escalation controls only when
the server exposes capability/state; use ETag and explicit confirmation. In Scope
2 controls may review/reject/request revision, never execute domain mutation.
Append the corresponding proposed/accepted/rejected/superseded project decision
with the exact reviewed digest, rationale and citations. Test stale proposal,
concurrent reviewer, malicious labels, denied role and decision/proposal mismatch.
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
S2-P12 separately evaluates Deep Agents only after this authoritative-graph gate
passes and only across the isolated SDK/API worker boundary. Store analysis/
version/digests as evidence.
```

### S2-P11E — dashboards, thresholds and operational drill

```text
Create dashboards for graph success/partial/block/failure, task/review/conflict,
proposal invalidation, safety assertions, provider/tool latency, budget and
baseline delta. Set alert/error-budget thresholds from observed shadow results.
Drill graph/provider/specialist kill switches, rollback, worker drain and stuck
graph recovery. Assign owners and link exact runbooks.
```

### S2-P11F — authoritative-graph promotion gate

```text
Run migrations, full regression, scenario corpus, counterfactual replay,
adversarial/failure campaign, ablations, API/UI and operator recovery in a
production-shaped environment. Publish exact release set and evidence showing
pipeline coverage, authority narrowing, citations, proposal-only behavior and
incremental value/cost. Enable proposal mode only for allowlisted workspaces;
mark Scope 3 eligible when this gate passes. S2-P12A and S2-P13A may begin as
parallel tracks. Their failure cannot delay safe deterministic Scope 3
development, but the required Deep Investigation gate and the recorded OpenAI
adapter go/no-go decision must close before the production-MVP release review.
```

## Plan 2.12 — isolated multi-mode Deep Investigation worker

**Contract.** Implement the architecture in
`docs/agentic-program/DEEP_AGENTS_INVESTIGATION_COPILOT.md` as explicitly
requested, proposal-only dataset/scientific, experiment/model and
operations/drift investigation jobs. The API owns authorization, an
InvestigationRun view over the same AgentRun ID/state/events, budgets and
terminal state plus an immutable InvestigationContextBundle; it is not a
parallel table or lifecycle. A
separate `services/deep_investigation_agent/` worker owns the pinned Deep Agents
harness and only calls allowlisted DCLab SDK/API reads plus a non-model-visible
claim/lease/gateway/completion client. The API-side lease service owns every
PostgreSQL MlJob operation. The worker does not import `apps/api`, receive product database or
object-store credentials, share the `worker-agent` dependency environment or
checkpoint namespace, or call/be called by raw LangGraph nodes. The first
release disables Deep Agents subagents, persistent memory, skills that mutate
behavior, host filesystem, shell, arbitrary HTTP and code execution.
This plan may run in parallel with Scope 3 after S2-P11F. It is independently
deployable/removable and no S2-P12 failure can disable or delay the authoritative
agent graph, controlled commands or model operations. S2-P12H is nevertheless a
production-MVP release prerequisite for the three agreed modes; disablement
degrades investigation only and never disables the deterministic workflow.

### S2-P12A — architecture ADR and zero-conflict dependency proof

```text
Read docs/agentic-program/DEEP_AGENTS_INVESTIGATION_COPILOT.md, the Scope 1
runtime ADR/pins produced by S1-P01A/S1-P07B, the Scope 2 supervisor ADR, root
pyproject.toml, Dockerfile, docker-compose.yml, apps/api/app/config.py,
apps/api/app/services/job_dispatcher.py, apps/api/app/services/job_handlers.py
and packages/dclab_client/pyproject.toml. Add an ADR that approves exactly one
Deep Agents runtime: the isolated Deep Investigation worker with
dataset_scientific, experiment_model and operations_drift modes. Record that deepagents is
a LangChain create_agent harness using LangGraph internally; compare its full
transitive LangChain/LangGraph/checkpointer requirements with worker-agent pins.
Choose independently supported/tested pins and treat versioned DCLab HTTP/worker
schemas—not a shared Python ABI—as the compatibility boundary. Never upgrade
worker-agent to satisfy Deep Agents. Require a separate
services/deep_investigation_agent/pyproject.toml, lock/SBOM, image/process,
workload identity, handler allowlist, trace namespace and runtime-state schema/
store. Diagram both execution paths and prove there is no import, invocation,
tool, subagent, checkpoint, database-role or lifecycle-authority edge between
them. Define rollout, dependency-upgrade compatibility/replay, kill switch and
rollback to the pre-Deep-Agents image. List rejected alternatives: embedding
create_deep_agent in a raw LangGraph node, implementing Scope 2 specialists as
Deep Agents subagents, sharing provider credentials/checkpoints, or exposing a
second public agent server. Do not install a dependency or implement behavior
in this prompt. Add a static architecture assertion/test design and stop on any
unresolved version or ownership conflict. Maximum change: ADR, diagrams and
contract-test skeleton under 500 hand-edited lines.
Explicitly record S2-P11F—not S2-P12H—as the gate that unblocks Scope 3,
S2-P12H as a production-MVP release prerequisite, and removal of
`worker-investigation` as a no-impact rollback for deterministic Core ML.
```

### S2-P12B — DCLab-owned investigation contracts and durable intent

```text
Inspect the AgentRun/AgentStep/AgentEvent/AgentProposal/Citation, ExecutionRequest,
MlJob, budget, lifecycle and ProjectDecisionRecord owners implemented by Scopes
1–2 before adding a table. Add ordinary Pydantic contracts in the existing
agent domain module or apps/api/app/domain/agent_investigation.py for
investigation-request.v1, InvestigationProposal, InvestigationClaim,
InvestigationContextBundle, ResourceCitation, LeakageFinding, ValidationFinding,
ProposedAction and Alternative. Request fields bind workspace/project, objective
version/digest, immutable dataset/resource versions, context-bundle ID/version/
digest, code-owned investigation mode enum and maximum iterations/model calls/tool calls/
tokens/cost/time/context/result bytes. Result assertions are classified as
deterministic_fact, cited_observation, hypothesis, recommendation or unknown;
each has a stable claim ID, bounded text, finite optional confidence and its own
citations. Every fact/observation requires a supporting resource/version/digest;
each recommendation references the evidence and assumption claim IDs it uses.
Build the immutable audience-safe context bundle through existing profile,
target/prediction-moment, leakage/validation, lineage, experiment/cost and
ProjectDecisionService queries. Store IDs, versions, freshness and digests, not
raw rows or duplicated reports; a refresh creates a new version. Any new profile
or aggregate slice is prepared before model execution by the DCLab DataScan
service using an authorized artifact, registered template and bounded Arrow
result. The context builder and Deep Agents worker cannot import DuckDB,
receive SQL/paths or retain Arrow batches.
Reuse AgentRun with purpose=investigation_copilot and AgentProposal with a typed
proposal kind unless the current schema demonstrably cannot enforce the state
machine. The public `investigation_id` is that AgentRun ID, not a new identity.
Reuse ExecutionRequest and MlJob with code-owned handler
investigation.copilot.v1. States are queued, running, cancelling, succeeded,
blocked, failed, cancelled and expired; attempts are append-only and terminal
results immutable. Implement InvestigationService so authorization, current
source versions, policy and budget reservation plus run/job/event persist
atomically with same-key/same-digest replay and same-key/different-digest 409.
If a migration is necessary, inspect the live Alembic head and add at most one
expand revision with composite workspace/project FKs, checks, unique idempotency
digest and indexes for tenant/state/created-at and active jobs. Add PostgreSQL
tests in apps/api/tests/test_agent_investigation_persistence.py for empty/live
upgrade, two-workspace substitution, concurrent create, stale source, terminal
immutability, context-bundle digest mismatch and failure injection. No agent framework dependency or provider
call belongs in the API. Maximum change: one resource family, one migration and
approximately 800 non-generated lines.
```

### S2-P12C — read-only DCLab API/SDK tool boundary

```text
Extend the cohesive /v1 agent/project routers established by S1-P09 and S2-P10,
packages/dclab_client/dclab_client/types.py and its resource client modules; do
not create a private SQL/service import path. Add
`POST /v1/projects/{project_id}/investigations`,
`GET /v1/projects/{project_id}/investigations`,
`GET /v1/projects/{project_id}/investigations/{investigation_id}`,
`GET .../{investigation_id}/events`, `GET .../{investigation_id}/result`,
`POST .../{investigation_id}/cancel` and
`POST .../{investigation_id}/reviews`. Return 202 for first accepted async work,
200 for exact idempotent replay/read/cancel replay and 201 for a newly persisted
review; list/events use bounded opaque cursors. Add an API-owned
InvestigationWorkerLeaseService over the existing
MlJob claim/heartbeat/settlement services plus non-public worker endpoints for
claim, lease heartbeat, cancellation status and complete/fail, protected by
dedicated investigations:work and investigations:complete workload capabilities,
private ingress and attempt/lease tokens. Use exact private paths
`POST /internal/worker-investigations/claim`,
`POST /internal/worker-investigations/{attempt_id}/heartbeat`,
`GET /internal/worker-investigations/{attempt_id}/cancellation`,
`POST /internal/worker-investigations/{attempt_id}/complete` and
`POST /internal/worker-investigations/{attempt_id}/fail`. The worker transport contains IDs,
bounds, safe status and validated terminal result only; it is not public OpenAPI
and is never registered as a model tool. Use the standard
error envelope: 400 invalid cursor/request shape, anti-enumerating 403/404,
409 idempotency/source/state/ETag conflict, 422 invalid scientific/resource
contract, 429 quota/budget, and safe 503 dependency unavailable. Define a
versioned worker SDK tool bundle containing only inspect_dataset_version,
get_dataset_profile, get_dataset_aggregate_slice,
get_investigation_context_bundle, get_feature_contract, get_lineage_subgraph,
get_project_constraints, search_project_decisions and compare_experiment_runs.
Define three signed mode manifests with strict subsets of this catalog;
model-package/batch/drift tools remain unavailable to operations_drift until
S3-P08F/G and are never silently exposed to another mode. Each call
uses immutable IDs, current short-lived service identity narrowed to initiating
workspace/project/resources/purpose, server re-authorization, strict Pydantic
request/result, stable citations, pagination, timeout and response-byte limits.
`get_dataset_aggregate_slice` accepts only a public typed DCLab request backed by
an allowlisted operation/template version and immutable dataset version. It
accepts no SQL, arbitrary expression, path, URL, DuckDB setting or raw-row
output, and is a normal API call to DataScan query service—not a DuckDB
dependency in `worker-investigation`.
Return trusted DCLab metadata/policy separately from untrusted user/dataset/
artifact text and never interpolate the latter into system instructions. Create
a signed/versioned capability manifest containing each custom tool schema,
purpose, resource types, maximum response bytes and rollout state; the custom-
tool registry and Deep Agents filesystem allowlist both deny by default.
The DCLab product tool catalog contains no claim/heartbeat/complete,
create/update/delete, generic HTTP,
SQL, object path/URL, raw row, prompt, secret, command, approval, graph dispatch
or completion tool. Add OpenAPI/SDK parity, route, scope, cancellation, stale-
version, malicious-filter, huge-result and two-workspace tests in apps/api/tests
and packages/dclab_client/tests. Document token TTL/audience and emergency read/
completion revocation. Keep the PR below one public resource family and 20
hand-edited files.
```

### S2-P12D — separate Deep Agents worker and DCLab gateway adapter

```text
Create services/deep_investigation_agent/ with its own pyproject.toml and locked
dependency group from the S2-P12A pins, package modules for config, DCLab SDK
tools, DCLabGatewayChatModel, contracts, harness, handler and tests, plus a
minimal non-root worker image. Do not import apps/api or reuse worker-agent graph
modules. Implement a LangChain-compatible chat model adapter that sends all
model requests through the DCLab provider-neutral gateway with purpose, release,
workspace/project/run/attempt, data-policy, budget, deadline and trace context;
the worker receives no provider secret. Reuse the Scope 1 internal gateway
transport if present; otherwise add the non-public workload-authenticated
`POST /internal/llm/invocations` adapter over the same gateway service, accepting
only a registered purpose/release/run/attempt and bounded structured messages,
and returning the standard validated provider-neutral response/usage/error.
It returns 409 for stale release/run, 422 for contract/policy failure, 429 for
budget/rate and safe 503 for provider unavailability. Construct create_deep_agent with the
validated InvestigationProposal response format and only the S2-P12C tool
objects. Explicitly disable the general-purpose subagent, pass no sync/async
subagents, configure no persistent memory or behavior-changing skill, and use
job-scoped StateBackend/virtual state with no host filesystem or execute tool.
Permit only the reviewed Deep Agents planning/todo and virtual file operations
against that StateBackend in addition to the DCLab read tools; remove the task
delegation tool and prove virtual paths cannot reach host/product/durable state.
Inspect the final model-visible tool list at startup and fail closed if `task`,
`execute`, a mutation tool or an unregistered custom tool is present. Verify
filesystem restrictions independently from custom DCLab tool authorization.
System instructions require deterministic-service precedence, fact/hypothesis
separation, citations, alternatives, unresolved questions and no action claim.
The handler accepts only investigation.copilot.v1 IDs/bounds, fetches its request
through the workload-authenticated lease client/SDK, heartbeats and observes
cancellation outside the harness, rechecks policy before each model/tool
boundary, validates the final response, and submits it through the non-model-
visible completion client. It never imports a queue/ORM model or opens product
PostgreSQL.
Add deterministic fake model/SDK tests proving the exact combined tool inventory,
no subagent/task/execute tool, no virtual-filesystem path or network escape,
gateway routing, context-bundle binding, claim-level citation output,
schema/citation rejection, timeout and sanitized errors. Add a dependency/import
test that fails if deepagents/LangChain agent modules enter root API or
worker-agent runtime packages. No UI, database migration or production deploy
in this prompt; keep the worker scaffold reviewable under approximately 800
non-generated lines.
```

### S2-P12E — bounds, cancellation, recovery and runtime-state isolation

```text
Implement hierarchical limits across InvestigationService, MlJob,
worker-investigation, DCLabGatewayChatModel and SDK tools: iterations, model
calls, tool calls, tokens, precise cost microunits, wall time, context/result
bytes and workspace concurrency. Reserve before work and settle exactly once;
add fail-closed typed API limits/flags to apps/api/app/config.py and .env.example,
and worker transport/backend/timeouts to
services/deep_investigation_agent/config.py and its example environment file.
Production rejects missing limits, unknown backend, StateBackend persistence
beyond policy, enabled subagents and any FilesystemBackend/LocalShellBackend.
Fallback/retry cannot exceed the original policy. Support cooperative cancel at
job lease, model gateway, tool HTTP and harness turn boundaries, with terminal
reason codes budget_exhausted, deadline_exceeded, cancelled, policy_revoked,
source_stale and dependency_unavailable. A worker loss closes or creates one
numbered retry attempt from the immutable request after re-authorization; it
never recursively resumes or duplicates a submitted proposal. The first release
uses job-scoped StateBackend only and restarts from the pinned immutable request/
InvestigationContextBundle; do not add a durable Deep Agents checkpointer or
store in this plan. Record observed duration and restart cost. A later dedicated
prompt/ADR may approve a short-retention `deep_agents_runtime` schema only when
evidence proves restart-from-request insufficient; it must use a least-privilege
identity, separate migrations/IDs/encryption/deletion and no shared worker-agent
checkpointer table.
Implement per-workspace and global flags DCLAB_INVESTIGATION_COPILOT_ENABLED=false
by default and DCLAB_INVESTIGATION_COPILOT_KILL_SWITCH=true as fail-closed
emergency disable semantics, with production validation rejecting unbounded or
host-backed configuration. Add race/failure tests for cancel during model/tool,
budget concurrency, worker death before/after completion, duplicate callback,
stale source mid-run, context-bundle mismatch, membership revocation and gateway
outage. Emit bounded attempt/budget/terminal events and add recovery/kill-switch/
state-cleanup runbooks at docs/runbooks/investigation-copilot.md. Instrument low-cardinality
run duration/status/reason, model/tool count, token/cost reservation/settlement,
cancellation latency, citation-validation failure and active-work metrics; never
label with tenant/project/resource ID or log working content. Do not add
persistent agent memory, shell or sandbox.
```

### S2-P12F — cited proposal review and synchronized product experience

```text
Add deterministic output validation and projection before any result is visible:
resolve every ResourceCitation through current authorization, verify resource
type/ID/version/digest and evidence span, reject fact/observation claims without
citations and recommendation claims without evidence/assumption claim IDs, mark
novel leakage/validation ideas as hypotheses until deterministic verification,
and prohibit unsupported causal or completion claims. Persist only bounded
validated AgentProposal/result metadata and protected artifact bodies according
to Scope 1 retention; do not persist hidden reasoning, todos or raw temporary
files. Extend the project/Agent Studio routes established by S1-P10/S2-P10 using
apps/web/lib/infrastructure/api-client.ts, apps/web/lib/domain/schemas.ts and
apps/web/app/components/agent/InvestigationCopilotPanel.tsx,
InvestigationProposalView.tsx and the established project Agent Studio route
with an Investigation Copilot start form, queued/
running/cancelling/blocked/failed/cancelled/expired/succeeded states, budget and
cost display, visually distinct facts/observations/hypotheses/recommendations/
unknowns, a keyboard-accessible “Why this claim?” citation drawer, alternatives,
unresolved questions and a resource-aware proposal diff. Show source freshness,
cost and missing evidence before review. Conversation, ML workflow and
implementation views deep-link to the same project/resource versions. Review
may accept, reject, request revision or supersede the proposal through existing
services; that review appends the corresponding ProjectDecisionRecord but does
not execute a command. “Create reviewed command” is a separate explicit action
with fresh validation, authorization, idempotency and approval. Hide unavailable Scope 3 model/drift tools through server
capability, not client inference. Add component/accessibility/browser tests for
keyboard and screen-reader use, reload/reconnect, stale citation, malicious
Markdown/labels, denied role, workspace switch, revision conflict and all states.
No arbitrary chat, deployment button, raw prompt/checkpoint view or client-side
authorization logic. Split API projection and UI if the change would exceed 20
hand-edited files. Emit investigation.requested, claimed, cancelled,
proposal_validated, proposal_rejected and completed audit/event types with
bounded reason and correlated AgentRun/attempt/tool/citation IDs.
```

### S2-P12G — comparative, adversarial and scientific evaluation

```text
Extend the S2-P11 evaluation corpus/replay runner rather than building a second
eval store. Add synthetic/de-identified cases for unfamiliar tabular datasets,
schema/profile/lineage interpretation, known and tempting false leakage,
temporal/group split errors, metric/constraint conflict, experiment comparison,
model-selection rationale, incomplete evidence, malicious data labels, prompt
injection in metadata, stale/tampered InvestigationContextBundle and unavailable future model/drift capabilities. Compare
Investigation Copilot with deterministic query/report output and the authoritative
Scope 2 raw-LangGraph supervisor; measure useful-proposal rate, citation precision/
recall, deterministic-finding coverage, unsupported-claim and false-leakage rate,
claim-level citation completeness, abstention quality, reviewer calibration,
review revision/acceptance, time saved, cost per accepted proposal, latency,
model/tool calls, tokens and cost. Inject
malformed model output, unauthorized/oversized/poisoned tool results, timeouts,
429/5xx, cancellation, worker loss, stale source, policy change and kill switch.
Hard-fail on cross-tenant data, command/write attempt, invalid citation,
deterministic-fact override, holdout weakening, hidden second-runtime invocation,
unbounded retry or secret/raw-row leakage regardless of average quality. Verify
that no deepagents checkpoint/memory record appears in lifecycle, decision,
OpenAPI or public SDK truth. Store release/pin/fixture/result digests as existing
EvaluationRun evidence and publish an explicit cost/value threshold. Run with a
deterministic fake in PR CI; any live-provider smoke uses synthetic content,
bounded spend and protected credentials through the DCLab gateway only.
Place API/evaluation cases in
apps/api/tests/test_agent_investigation_evaluation.py and worker harness cases in
services/deep_investigation_agent/tests/test_investigation_harness.py; execute
them with the package-manager commands pinned by S2-P12A plus the existing Scope
2 evaluation command, and record the exact commands/results in evidence.
```

### S2-P12H — multi-mode shadow release and architecture-conformance gate

```text
Build and scan the separate worker-investigation image and SBOM; assert that the
API/worker-agent images contain no deepagents package and that the investigation
image contains no product DB/object-store/provider credential or API private
imports. In a production-shaped staging environment, submit an explicit DCLab
investigation request and prove API -> durable job -> isolated Deep Agent ->
read-only SDK tools -> validated cited proposal -> authorized review ->
ProjectDecisionRecord, with no raw LangGraph invocation and no domain command.
Exercise two-workspace isolation, service-token narrowing/expiry, dependency and
gateway outage, cancellation, restart-from-request, duplicate completion, budget/time/
response limits, feature flag, global kill switch, image rollback and state TTL
cleanup. Verify separate process identity, network policy, handler allowlist,
dependency lock and checkpoint/runtime namespace using automated conformance
tests. Run accessibility/UI/API/SDK parity and the S2-P12G hard evaluations.
Enable dataset_scientific and experiment_model in shadow for allowlisted
synthetic/de-identified projects when their safety assertions pass; enable
operations_drift in shadow only after the Scope 3.8 resource/tool gate. Do not
enable customer command execution, shell, persistent memory or subagents.
Record commands,
durations, digests, dashboards, alerts, owners, runbooks, rollback and known
limitations in the S2-P12 evidence record. Prove worker removal/disablement
leaves the S2-P11F authoritative graph and every eligible Scope 3 path healthy.
This gate releases the three Deep Investigation modes for the production-MVP
review; it does not gate Scope 3 development or authorize any command.
```

## Plan 2.13 — isolated OpenAI Agents API runtime adapter

**Contract.** Evaluate and, only if the gates pass, implement the OpenAI Agents
API as a provider-hosted runtime selected for an entire DCLab AgentRun. It is
not the default DCLab agent operating system, a LangGraph node/tool/subagent, a
Deep Investigation tool or a replacement for DCLab product state. Initial use
is limited to an explicitly requested bounded audit or Scope 4 sandbox task.
The official SDK remains inside a dedicated adapter/worker package. DCLab owns
authorization, immutable inputs, runtime selection, budgets, function-tool
execution, approvals, usage reconciliation, citations, terminal state,
retention and audit. The provider owns only its private harness/session.

Use focused test homes
`apps/api/tests/test_external_agent_runtime_contract.py`,
`apps/api/tests/test_openai_agent_session_service.py`,
`services/openai_agent_runtime/tests/test_openai_agents_adapter.py`,
`test_required_action_gateway.py`, `test_environment_policy.py` and
`test_openai_agents_release_gate.py`. Extend equivalent current owners rather
than creating parallel tests. No live credential or network belongs in PR CI.

### S2-P13A — runtime authority, API maturity and retention ADR

```text
Read docs/agentic-program/AGENT_FIRST_MVP_ARCHITECTURE.md and the current
official OpenAI Agents API architecture, session, function-tool, environment,
sandbox-security, data-retention and changelog/deprecation documentation.
Inventory S1-P01/S1-P07 runtime contracts, AgentRun/Step/Event/ToolCall/Citation,
LLM gateway, budgets, ToolRunner, MlJob, Scope 4 sandbox contracts and all
installed OpenAI packages. Write an ADR that treats the API/SDK as beta until
official maturity evidence says otherwise and selects one whole-run runtime
key such as `openai_agents.v1`. Define the only eligible purposes, supported
environment kinds (`none` and a separately approved isolated sandbox), data
classes, regional/retention rules, required scopes and unavailable built-ins.
Diagram DCLab -> dedicated adapter -> OpenAI harness/session and required action
-> DCLab gateway; prove no edge invokes or is invoked by raw LangGraph or Deep
Agents. Compare managed-session benefit, lock-in, data handling, failure modes,
latency and cost against DCLab LangGraph and Deep Investigation baselines.
Define provider API/SDK pin, compatibility window, removal path, feature flag,
global kill switch and explicit no-go criteria. Do not install a package, create
an agent/session, migrate data or enable a tool. Maximum change: ADR, dependency
proof and test skeleton under 500 hand-edited lines.
```

### S2-P13B — provider-neutral external runtime/session contracts

```text
Extend the existing AgentRun domain contract with one code-owned runtime_kind,
runtime_version and provider-neutral ExternalAgentSessionReference rather than
creating a second public agent-run model. Define fields for provider key,
opaque external agent/session/turn references, configured agent release digest,
environment policy/version, input/context digest, provider state/last event
cursor, required-action count, usage/cost reconciliation state, retention/
deletion deadline, result digest, safe failure reason and timestamps. Do not
store provider chain-of-thought, unbounded session events, raw dataset rows,
secret values or provider SDK objects. Reuse AgentRun queued/running/waiting/
cancelling/succeeded/failed/cancelled/expired states and map provider states
explicitly; unknown state fails closed and never implies completion. If current
columns cannot enforce uniqueness/reconciliation, inspect the live Alembic head
and add one bounded tenant-scoped external-session reference table with
workspace/project/run composite lineage, unique provider/session reference,
immutable release/input digest, monotonic event sequence and retention indexes.
Add pure transition/digest plus real-PostgreSQL empty/live upgrade, cross-tenant,
duplicate webhook/session, stale run, retention and forward-repair tests. This
prompt creates no provider call or public route.
```

### S2-P13C — dedicated adapter, session lifecycle and reconciliation

```text
Create `services/openai_agent_runtime/` with its own pinned dependency group,
configuration, provider-neutral transport contracts, adapter, deterministic
fake, redaction and tests. It must not import raw LangGraph/Deep Agents runtime
packages, SQLAlchemy models, product DB sessions or object-store clients. Build
an API-side OpenAIAgentSessionService that authorizes the purpose/resources,
resolves a server-owned agent release, reserves budget and atomically persists
AgentRun/ExecutionRequest/MlJob/event before dispatch. The dedicated worker uses
the official beta Agents API to create/continue/cancel/retrieve one session and
maps streaming/webhook/poll events to bounded versioned DCLab inputs. Persist
intent before each network operation; correlate by DCLab run/attempt/idempotency
digest; reconcile create/continue/cancel timeouts by retrieving the known
session before retrying. Never retry an ambiguous session create with a new
identity until reconciliation proves absence. Add explicit connect/read/total
timeouts, bounded retries, rate handling and cancellation. Store only normalized
status/usage/result/citation metadata. Test malformed/unknown provider events,
duplicates/out-of-order delivery, disconnect, webhook replay/forgery, 429/5xx,
ambiguous create/cancel, worker loss and kill switch with a scripted fake.
Maximum change: one adapter package and one handler family under approximately
800 non-generated lines; split API/webhook transport if needed.
```

### S2-P13D — DCLab-mediated required actions and tool denial

```text
Define a signed/versioned OpenAI agent tool manifest derived from the existing
DCLab ToolRegistry for the eligible purpose. Use strict JSON Schema with
additionalProperties=false and smaller-or-equal argument/result bounds. When a
session enters requires_action, read only the authoritative pending
required_actions and bind provider session/turn/call/name/arguments digest to a
DCLab AgentToolCall. Re-authorize current workspace/project/resources, runtime
purpose, tool release, capability, policy, source versions, budget, idempotency
and exact approval before ToolRunner execution. A function_call in provider
history alone is not pending authority. Persist normalized result/failure before
submitting the matching tool result; on ambiguous submission retrieve the
session and reconcile the same turn/call rather than execute again. Default
manifest is read-only. Any Scope 3 command is separately allowlisted and uses
its existing approval/idempotency behavior; no generic SQL, HTTP, filesystem,
shell, code, provider, MCP-discovery, agent-runtime or worker-dispatch tool is
available. Reject parallel actions in the first release and enforce one
external operation per DCLab durable turn. Add tests for forged/duplicate/stale
calls, changed arguments, unknown tool, revoked access, approval race, budget,
cancel, result replay, malicious output and cross-runtime invocation attempts.
```

### S2-P13E — environment, sandbox, credential and MCP policy

```text
Implement code-owned environment policy resolution. Use `environment.type=none`
for read-only audit/proposal work. A task requiring files or code may use only
the Scope 4 verified isolated sandbox adapter with per-run image, unprivileged
identity, no product database, scoped immutable inputs, quarantined outputs,
CPU/memory/disk/process/time/output limits and denied-by-default egress; do not
create a second sandbox product. Keep the application OpenAI key and all
third-party/provider credentials outside the environment. If outbound access is
approved, route only allowlisted destinations through the DCLab credential
broker using short-lived purpose-bound credentials. The environment receives no
connector secret, object-store master credential, unrestricted network, host
mount or production deployment token. Disable OpenAI built-in web, computer,
shell/apply-patch, remote MCP, plugin and arbitrary package-install capabilities
unless a later per-tool ADR and threat gate approves one. If hosted MCP is used,
allow only DCLab's Scope 6 facade with audience/resource-bound token and the same
tool manifest; no arbitrary remote MCP server. Add isolation, metadata-service,
DNS/redirect, egress, credential-read, host-path, resource-exhaustion and output-
smuggling tests plus credential compromise and sandbox termination runbooks.
```

### S2-P13F — comparative evaluation and beta release decision

```text
Extend the existing agent evaluation/replay store with identical synthetic and
de-identified tasks executed through raw LangGraph, Deep Investigation and the
OpenAI Agents adapter only where their purposes overlap. Measure task success,
citation validity, unsupported claims, tool-call correctness, retries, time,
tokens/cost, cancellation latency, session reconciliation and operator effort.
Hard-fail on cross-tenant output, unauthorized/duplicate tool execution,
retention violation, secret/raw-row leakage, hidden runtime nesting, unbounded
retry, deterministic-fact override or sandbox/egress escape. Run dependency and
import assertions proving the official Agents SDK is absent from API,
worker-agent and worker-investigation images. Exercise provider outage,
webhook/poll loss, ambiguous required-action result, deletion, feature disable,
global kill switch and rollback to an image without the adapter. Record one of
three outcomes: SHADOW_ALLOWED for named purposes, DISABLED_PENDING_EVIDENCE or
REJECTED. The production-MVP review requires this recorded decision, not forced
provider activation. If allowed, enable only for allowlisted synthetic or
de-identified projects first and publish pins, retention, limits, dashboard,
alerts, owner and removal runbook. Prove disabling/removing the adapter leaves
all LangGraph, Deep Investigation and deterministic ML tests healthy.
```
