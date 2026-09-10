# Scope 8 prompts — recommendations, actions, outcomes, and impact

Use the common preamble. Prediction, recommendation, action, outcome and causal
impact are separate resources and claims.

## Plan 8.1 — decision cases and recommendations

### S8-P01A — domain-neutral decision schema/service

```text
Add DecisionCase and immutable RecommendationVersion without forcing every
domain into Customer/Transaction. Link workspace/project/ProblemSpec, Dataset/
entity or cohort digest, WorkflowRun/PipelineRun/ModelVersion, objective/window
and state. Recommendations record candidate actions including do_nothing,
eligibility/constraints/capacity/cost/risk, predictive/experimental/uplift/
causal evidence class, expected value/incremental value/uncertainty,
assumptions, deterministic/LLM/user source, policy/evidence digest and expiry.
Corrections create versions.
```

### S8-P01B — recommendation correctness and language gate

```text
Implement deterministic eligibility/constraint/ranking service; LLM may
translate or request evidence but cannot invent lift/value. Test stale/low-
quality/out-of-population cases, capacity/compliance blocks, insufficient value,
do nothing, predictive versus experiment evidence, recomputation from versions,
client-safe explanation and cross-workspace links. Make unsupported causal
language, missing do-nothing where required, invented numbers or unreferenced
evidence blocking failures.
```

## Plan 8.2 — actions, approval and outbox

### S8-P02A — action proposal/execution and exact digest

```text
Add ActionProposal and ActionExecution linked to recommendation and exact
Approval. Canonical payload includes provider/connector, action/schema,
target/cohort snapshot, fields, schedule, evidence, cost/volume, compensation,
policy and expiry. Store redacted summary/digest, risk/scope/status and actor;
sensitive body is encrypted Artifact only when retention requires. Define draft,
proposed, approval_required, approved, queued, delivering, delivered,
acknowledged/completed, denied/expired/cancelled/failed/compensation states.
```

### S8-P02B — transactional outbox and delivery ledger

```text
Add OutboxEvent and DeliveryAttempt with workspace/aggregate/event/schema,
bounded safe payload or Artifact, digest/dedupe, destination, status/availability,
attempt/lease/heartbeat/error and timestamps. In one transaction consume exact
approval, mark action ready and create outbox. Worker resolves credential and
uses DCLab action ID/provider idempotency. Persist safe receipt; retry only
classified transient failures; reconcile ambiguous acceptance before resend.
Test concurrent claim/approval/delivery and database/provider failures.
```

## Plan 8.3 — first outbound action

### S8-P03A — implement one low-risk action adapter

```text
Choose one low-risk action matching the Scope 7 pilot. Implement separate typed
validate, deliver, get-delivery and compensate capabilities; no inbound adapter
reuse that mixes permissions. Define reversible-before-acceptance,
compensatable, or irreversible class and dry-run/staging behavior. Use narrow
secret/egress context, strict target/payload bounds, provider quota and exact
receipt mapping. Expose no generic external-action tool.
```

### S8-P03B — ambiguous result and compensation suite

```text
In fake and provider sandbox test duplicate claim, provider idempotency and no-
idempotency reconciliation, timeout before/after acceptance, throttling,
permanent failure, cancel before claim, cancel after acceptance, compensation
success/failure/unavailable, credential rotation and malicious response. Prove
no duplicate effect, secret/target leakage or changed approved payload. Record
every attempt and operator-safe recovery.
```

## Plan 8.4 — outcomes and impact

### S8-P04A — outcome, correction and feedback model

```text
Add immutable/versioned OutcomeObservation with subject/cohort, event/value/
unit, observation and ingestion time, source connector/event, action/
recommendation links, attribution window, quality/completeness and lineage;
corrections append/supersede. Add FeedbackSignal for user accept/reject/edit,
delivery, observed result and agent correction with target/scope/weight/source.
Feedback is evidence only and never directly rewrites prompts/models or triggers
silent training.
```

### S8-P04B — impact and causal-evidence contract

```text
Add ImpactAssessment for descriptive, pre/post, matched, randomized, uplift or
causal methods with baseline/treatment, estimate/interval, assumptions,
validity warnings, data/code/evidence digests and policy-validated
causal_claim_allowed. For qualifying experiments persist eligibility,
randomization unit, treatment/control/do-nothing, assignment/exposure,
outcomes/windows, metrics/stopping/exclusions/power/analysis version. Test late/
duplicate/corrected outcomes, windows, multiple actions, missing assignment,
noncompliance and no causal label without evidence.
```

## Plan 8.5 — APIs, surfaces and agents

### S8-P05A — application APIs and clients

```text
Expose decision cases/recommendations/action proposals/approval/execute/action
status+deliveries/outcomes/impact/feedback through application services, /v1,
SDK and CLI with cursors, ETags, idempotency, request IDs, standard errors and
audience policy. A command returns durable resources, not implied outcome.
Business views hide internal ML/provider/prompt details but retain evidence
class, uncertainty, freshness and action state.
```

### S8-P05B — agent and UI closed-loop experience

```text
Give agents typed tools to explain/create recommendation proposals, request
exact action approval, submit an approved narrow action, monitor delivery,
inspect outcomes and propose a new model build from a versioned feedback dataset.
They cannot self-approve or bypass outbox. Build UI for candidate/do-nothing,
evidence/constraints/cost/risk, exact approval, delivery/reconciliation,
outcome/impact distinction and compensation. Test refresh, roles, two
workspaces, stale proposal and accessible statuses.
```

## Plan 8.6 — complete-loop gate

### S8-P06A — end-to-end decision-to-outcome proof

```text
Run one real sandbox/staging flow: synchronized dataset -> deterministic model
evidence -> recommendation with do nothing -> exact approval -> atomic outbox ->
provider delivery/receipt -> observed outcome/correction -> impact assessment ->
feedback dataset -> new build proposal. Inject duplication, outage, ambiguity,
worker death, cancellation and expired approval. Assert trace/citation and
immutable lineage at every boundary.
```

### S8-P06B — operations, privacy and release controls

```text
Add dashboards/alerts for approval age, outbox age/attempts, ambiguous delivery,
delivery/acknowledgement, outcome lag/completeness, funnels and do-nothing rate.
Write disable provider/action, reconcile, compensate and subject deletion
runbooks. Test retention/deletion through source, derived datasets,
recommendations, encrypted payloads and outcomes while preserving lawful
non-content audit. Independent flags control proposal, approval and delivery.
```
