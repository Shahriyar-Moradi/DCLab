# Scope 2 prompts — complete agentic operating system

Start only after Scope 1 is fully verified. Scope 2 makes the entire DCLab
workflow agent-supervised in read, shadow, critique and proposal modes. It does
not activate model-build or external-write tools; those arrive in Scope 3/8.
Use the common preamble in `README.md`.

## Plan 2.1 — multi-agent contracts and governance

### S2-P01A — define the supervisor/specialist operating contract

```text
Write an ADR and versioned domain schemas for a supervisor plus Dataset Steward,
Problem/Plan Architect, Preparation/Feature Reviewer, Leakage/Validation Critic,
Experiment Director, Candidate/Metric Critic, Artifact/Provenance Auditor,
Technical/Business Reporter and Reliability/Recovery Analyst. Define task
decomposition, dependency graph, typed specialist inputs/outputs, parent/child
run/task lineage, critique/revision, conflict, human escalation, partial result,
global completion and stop states. Define when one agent is preferred and what
measured threshold justifies specialists. Agents may create proposal records
only; deterministic services own domain facts and future mutations.
```

### S2-P01B — hierarchical authority, budget and threat model

```text
Define and test pure policy for delegation. A child receives the intersection
of parent workspace/project/resource scope, data policy, model policy, tool
catalog, risk tier, deadline and remaining budget; it can never widen them.
Allocate/reserve child steps/tokens/cost/time/jobs atomically and settle back to
the parent. Prevent recursive/unbounded spawning, circular dependencies,
self-approval, policy/tool/model self-promotion, runtime code changes and hidden
memory. Threat-model confused deputies, collusion/consensus failure, poisoned
specialist output, delegation storms and cross-agent data leakage. Add schema
and property tests for monotonic narrowing and global stop conditions.
```

## Plan 2.2 — task, delegation, review and proposal persistence

### S2-P02A — add multi-agent control-plane records

```text
Add additive models/migrations for AgentTask, AgentDelegation, AgentReview or
Critique, AgentProposal, AgentConflict and supervision events. Tasks record
workspace/project, supervisor/child runs, typed objective/result schema,
dependency IDs, state, priority, deadline, input/output digests, allowed tools,
policy/budget snapshots and citations. Delegations record authority narrowing
and child allocation. Reviews append verdict, concerns, evidence and requested
revision. Proposals contain target resource/version, canonical typed patch,
expected effect, risks, validation result and status; never raw arbitrary code/
SQL. Use composite tenant constraints, append-only history and measured indexes.
```

### S2-P02B — concurrency, lineage, retention and recovery proof

```text
Test task DAG validation, cycle rejection, dependency scheduling, concurrent
claim, duplicate delegation, child budget race, supervisor cancellation,
specialist timeout, partial completion, conflict creation, review revisions and
immutable proposal history with real PostgreSQL. Prove child references cannot
cross workspace/project or outlive authorization. Define retention/deletion for
tasks, reviews, proposal bodies, checkpoints and artifacts; invalidating a
source must invalidate derived proposals. Add event cursor and reconstruction
tests so an operator can explain exactly why a specialist ran and what evidence
it returned after process loss.
```

## Plan 2.3 — prompt, model, data and evaluation operations

### S2-P03A — productionize immutable releases and promotion

```text
Complete PromptRegistry, ModelPolicy, ToolPolicy, DataPolicy, BudgetPolicy,
AgentVersion and graph release lifecycles: draft -> candidate -> shadow ->
canary -> active -> retired. Add PromotionRecord with environment, compatible
version set, evaluation run, approver, previous release, deployment ID,
promotion/rollback time and digest. Enforce separation of duties and concurrent
single-active promotion. Route by purpose/data class/region/retention/quality/
latency/cost and allow only stricter fallback. Add admin read/propose/review/
promote/rollback APIs and UI with raw prompt-body capability separated from
metadata access.
```

### S2-P03B — evaluation platform, shadow and kill-switch proof

```text
Persist versioned evaluation suites/cases/runs/results with deterministic
policy/tool/citation assertions, calibrated qualitative rubrics, artifacts for
large fixtures and baseline deltas. Add offline, provider-integration,
adversarial, shadow, canary, promote and rollback orchestration. Emergency
switches must independently disable all external LLM calls, provider/model,
data-context class, agent definition/specialist, write tools, tool/risk tier and
hosted MCP placeholder. Prove new steps observe stricter emergency policy,
running steps cannot become more permissive, provider failure cannot trigger a
retry storm, and rollback restores a historically resolvable release.
```

## Plan 2.4 — Dataset Steward

### S2-P04A — implement dataset supervision in proposal mode

```text
Implement the Dataset Steward specialist using typed read tools for source,
ingestion, asset/version, schema, profile, quality, freshness, lineage and
effective policies. Its structured output reports readiness, uncertainty,
unsafe/unknown columns, target/entity/time candidates, schema drift, quality
violations and required clarification. It may propose—never directly apply—
classification reviews, deterministic mapping versions, exclusions,
transformations, profiling reruns and data-repair plans. Every assertion cites
Dataset/DatasetColumn/Profile/Finding/Policy resources and preserves original
source evidence. Give it no object-store body, secret, arbitrary sample or
mutation tool.
```

### S2-P04B — steward correctness, privacy and drift evaluation

```text
Create cases for numeric/categorical/time/text/identifier/sensitive/unknown/
malformed columns, ambiguous targets, numeric strings, missing/sentinel values,
duplicates, stale data, rare groups, source drift and injection inside names or
profile text. Compare proposed changes with deterministic validators. Hard-fail
unsafe exposure, invented statistics, cross-tenant citations, automatic safety
downgrade, unsupported target choice or silent mapping activation. Measure
readiness accuracy, clarification quality, citation completeness, redundant
tool calls, cost and latency against the single-agent baseline.
```

## Plan 2.5 — Problem and Plan Architects

### S2-P05A — objective and ProblemSpec architect

```text
Implement a specialist that converts a user business objective plus authorized
dataset evidence into a typed draft ProblemSpec proposal. Separate user facts,
inferences and defaults. Include objective, prediction target, task type,
entity/time semantics, eligible population, prediction/decision horizon,
metric/business utility, constraints, audience, prohibited claims,
assumptions, missing decisions and source citations. Use deterministic target/
task services as evidence and fail closed on material ambiguity. The result is
an immutable proposal/diff and cannot edit the current ProblemSpec.
```

### S2-P05B — scientific experiment-plan architect and tests

```text
Implement a specialist that proposes validation, holdout, primary/secondary
metrics, leakage exclusions, feature groups, candidate/resource bounds,
expected artifacts and approval/cost class while reusing PipelineScientificPlan
schemas and deterministic planning validators. Test classification,
regression, temporal/entity grouping, insufficient sample/class balance,
conflicting objective/metric, arbitrary target names, unsupported causality and
holdout leakage. Hard-fail plans that use final holdout for tuning, omit a
required entity/time boundary, exceed budget, or cite mutable/nonexistent
evidence. Store validator output with the proposal.
```

## Plan 2.6 — preparation, feature, leakage and validation critics

### S2-P06A — preparation and feature review specialist

```text
Implement a specialist that audits proposed/current cleaning, missing-value,
column-role, encoding, scaling, feature construction and feature-lineage
decisions. It receives persisted decisions and safe profiles, not full rows.
Produce typed findings with severity, evidence, affected columns/features,
scientific risk and a proposed new plan version or deterministic repair—not an
in-place edit. Enforce fold-local fitting, train-only evidence, unknown-category
handling, reproducible transformations and feature-source lineage. LLM semantic
advice cannot override deterministic invalidity.
```

### S2-P06B — leakage and validation adversarial critic

```text
Implement a separate critic for structural, semantic, temporal, entity,
post-outcome and target-proxy leakage plus split/metric/holdout validity. Run it
before a proposal may be marked ready. Test direct target copies, near-
identifiers, post-event timestamps, fold contamination, group crossover,
preprocessing fit on holdout, semantic false positive/negative, poisoned field
descriptions and instruction injection. Cross-check deterministic LeakageAuditor
and verifier outputs; deterministic rejection wins. Record dissent/conflict
rather than averaging incompatible conclusions. Measure detection/false-
positive rate and citation accuracy.
```

## Plan 2.7 — Experiment Director and Candidate/Metric Critic

### S2-P07A — stage-aware experiment director

```text
Implement an Experiment Director that reads WorkflowRun/PipelineRun,
scientific-plan locks, stage events, jobs, failures, candidates, folds, metrics,
selection, holdout, model and artifact status. In Scope 2 it monitors and emits
diagnosis, next-safe-step, missing-evidence and child-run proposals; it cannot
start/cancel/retry. Model the expected stage state machine and distinguish
queued/running/waiting-for-input/failed/cancelled/completed. A long build becomes
a durable wait linked to ExecutionRequest and resumes from events. Never hide
failed candidates or claim completion from HTTP acceptance.
```

### S2-P07B — candidate/metric critique and bounded iteration proposal

```text
Implement the Candidate/Metric Critic over persisted search evidence. Evaluate
candidate diversity, hyperparameter bounds, fold stability, baseline
comparison, metric/objective alignment, calibration/uncertainty, winner lock,
single final holdout and population limitations. It may propose a child
experiment with parent citation, exact hypothesis/change digest, expected
information value, cost and stop rule. Test cherry-picking, repeated holdout
optimization, invented metrics, failed-candidate concealment, unstable folds,
wrong metric direction and unsupported causal/deployment claims. Enforce
portfolio limits even though execution remains disabled.
```

## Plan 2.8 — artifacts, provenance and reporting specialists

### S2-P08A — artifact and provenance auditor

```text
Implement a specialist that checks Artifact metadata/object existence/digest,
source/data/code/runtime/plan/input/output lineage, locked evidence,
Visualization schema, report/prediction/model references and retention state.
It can propose reconciliation, quarantine, regeneration or missing-evidence
work but cannot overwrite an immutable object or issue a download. Return typed
availability/corruption/lineage findings and exact citations. Integrate with a
dry-run artifact reconciler. Test missing object, wrong digest, orphan body,
cross-workspace key, stale signed URL, incomplete artifact, malicious MIME and
source deletion/reclassification.
```

### S2-P08B — technical and business reporter

```text
Implement audience-specific Reporters that synthesize only verified cited
facts. Technical output covers assumptions, target/entity/time, preparation,
leakage, candidates, folds, metric, selection, holdout, verification,
reproducibility, limitations and next proposals. Business output uses the
translation layer and omits raw ML operations/internal prompts/provider
details while preserving evidence class, uncertainty, freshness, risks and
what is not causal. Validate every claim/citation after rendering. Test banned
terms, causal overstatement, hallucinated numbers, missing/invalid citations,
partial/failed runs, policy-redacted evidence and malicious artifact text.
```

## Plan 2.9 — supervisor orchestration

### S2-P09A — implement governed task-graph execution

```text
Implement SupervisorOrchestrator over AgentTask DAGs. It chooses only registered
specialists from immutable graph policy, creates narrowed child tasks, schedules
ready dependencies, persists child results/reviews, requests revision or human
input, resolves deterministic precedence and synthesizes a final cited answer/
proposal set. Parallelize independent read tasks within workspace/provider/job
limits, but preserve deterministic merge order. Bound depth, breadth, tasks,
steps, tools, LLM calls, bytes, cost and time globally and per child. One failed
specialist yields a typed partial/fail decision, never silent omission.
```

### S2-P09B — conflict, recovery and delegation-storm proof

```text
Inject contradictory specialists, stale citations, child timeouts, provider
outages, invalid schema, malicious child output, duplicate delivery, supervisor
and child worker death, cancellation at each boundary, budget exhaustion and
human-input expiry. Prove deterministic facts/policy override LLM consensus;
unresolved scientific conflicts escalate; child tools cannot widen; all budget
is settled; checkpoints reconstruct the DAG; and no recursion/delegation storm
occurs. Test partial answer labeling and exact task/citation trace. Compare
serial and parallel execution for stable semantic output and no database race.
```

## Plan 2.10 — agentic operations UI

### S2-P10A — task graph, review and proposal experience

```text
Extend Agent Studio with a task/dependency graph or accessible list, specialist
identity/purpose, current state, evidence/citations, critique/revision chain,
conflicts, human questions, proposed resource diffs, expected effect/risk/cost,
hierarchical budget and stop reason. Users can inspect and reject proposals but
cannot activate domain mutations in Scope 2. Clearly label deterministic facts,
LLM advice, user decisions and unresolved uncertainty. Preserve refresh/
reconnect and audience filtering. Do not expose hidden reasoning, raw prompts,
provider bodies or forbidden technical details.
```

### S2-P10B — admin release/evaluation and accessibility UI

```text
Add protected admin views for agent/graph/prompt/model/tool/data/budget versions,
evaluation comparisons, promotion request/approval, shadow/canary status,
rollback and kill switches. Separate raw prompt-body access. Add component/E2E
tests for DAG progress, parallel tasks, revision, conflict/escalation, partial
failure, cancellation, policy/budget block, promotion separation of duties and
rollback. Verify keyboard traversal, focus, semantic statuses, announcements,
contrast and reduced motion. A client user must never see internal model/prompt
or cross-workspace release data.
```

## Plan 2.11 — whole-pipeline shadow evaluation

### S2-P11A — end-to-end shadow and counterfactual replay

```text
Build versioned scenarios spanning ingest/profile -> objective/ProblemSpec ->
preparation/leakage/plan -> candidates/folds/selection/holdout -> artifacts/
reports, using existing immutable synthetic runs. Execute the multi-agent team
in shadow/proposal mode and compare its outputs to deterministic evidence,
human labels and the Scope 1 single-agent baseline. Include success, ambiguity,
invalid data, forced candidate failure, corrupted artifact, policy denial,
provider failure, cancellation and unsupported causality. Store per-specialist
and overall quality, hard failures, task/tool count, latency and cost.
```

### S2-P11B — ablation, promotion threshold and operational gate

```text
Run specialist ablations and alternate routing to prove each extra agent adds a
defined benefit worth cost/latency. Set predeclared blocking thresholds for
policy/citation/scientific correctness and minimum improvement for task success
or diagnostic quality. Load/soak test task/checkpoint/event growth and provider
backpressure. Create dashboards/alerts/runbooks for delegation depth, child
failure, conflict age, budget imbalance, schema invalidity, stale citations and
shadow regression. Promote the graph only to a Scope 2 read/shadow/proposal
allowlist. Record that all domain write tools remain disabled.
```

## Scope 2 completion prompt

Map every pipeline stage to its supervising agent, typed tools, proposal schema,
tests, telemetry and kill switch. Query production-like policy to prove
delegation narrowing and no active domain mutation. Scope 3 is blocked until the
multi-agent team beats the predeclared baseline and every safety hard gate is
VERIFIED.
