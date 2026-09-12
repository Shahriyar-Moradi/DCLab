# Scope 8 execution prompts — recommendations, actions, outcomes and impact

Start after verified Scopes 3 and 7. Apply `README.md` and
`EXECUTION_STANDARD.md`. Prediction, recommendation, approval, action, delivery,
outcome and causal impact are separate resources and claims. Feedback never
silently retrains or changes policy.

## Scope implementation boundary

Reuse decisions/predictions/translation, Scope 3 approvals, connector secrets/
egress/adapters, jobs/events and artifact/evidence lineage. Add domain-neutral
decision/action/outcome services and `api/v1_decision_cases.py`. The first
outbound action must be low-risk, reversible or compensatable and pilot-approved.
LangGraph may propose or request a typed action only through DCLab ToolRunner and
approval services; it never delivers an external effect itself. No framework
callback, PydanticAI tool or graph node may bypass the transactional outbox.

## Plan 8.1 — decision cases and recommendation versions

**Contract.** A DecisionCase frames context/objective/constraints; immutable
RecommendationVersion ranks candidates including `do_nothing`, with evidence,
uncertainty and evidence class. It is not an executed action.

### S8-P01A — domain ADR and recommendation contracts

```text
Inventory legacy Opportunity/Prediction/Decision and translation models. Write
an ADR defining domain-neutral DecisionCase, Candidate, RecommendationVersion,
Constraint, EvidenceRef and states; map legacy verticals through adapters rather
than duplicating semantics. Define prediction versus recommendation, uncertainty,
utility/cost, `do_nothing`, human decision and allowed evidence/causal classes.
Add pure schemas/state tests; no provider action.
```

### S8-P01B — decision/recommendation persistence

```text
Add tenant-scoped DecisionCase and immutable RecommendationVersion/candidate/
evidence records with project/problem/dataset/model/run/version lineage, objective,
constraints, status, generator/policy versions, canonical input/output digests,
uncertainty and evidence class. Enforce same-tenant references, ordered candidates,
one `do_nothing`, immutable publication and supersession. Add migration/index/
concurrency tests.
```

### S8-P01C — deterministic recommendation service

```text
Implement create/version/publish/supersede services. Validate authorized current
source evidence, configured objective/constraints, candidate feasibility and
evidence class; calculate deterministic fields outside the LLM. Agent/LLM may
propose structured ranking/rationale but cannot fabricate metrics or change hard
constraints. Require supported citations for claims. Test missing/stale/locked/
cross-workspace evidence and same-key replay.
```

### S8-P01D — uncertainty and language policy

```text
Define machine-enforced language/evidence rules for descriptive, predictive,
experimental and causal claims. Require uncertainty/limitations and forbid
guarantee/causal uplift without qualifying design. Ensure audience-safe technical/
business renderings derive from the same version. Extend translation/banned-term
tests for misleading action urgency, omitted do-nothing and unsupported precision.
```

### S8-P01E — recommendation correctness gate

```text
Create synthetic cases for feasible/infeasible candidates, ties, negative utility,
missing evidence, distribution shift, policy constraint and misleading model
confidence. Assert do-nothing availability, citations, immutable versioning,
honest evidence class and no action side effect. Compare deterministic and agent-
assisted outputs on predefined support/usefulness/cost measures before release.
```

## Plan 8.2 — action proposal, exact approval and transactional outbox

**Contract.** ActionProposal binds a recommendation and canonical provider-
independent action. ActionExecution and OutboxMessage are created with exact
approval consumption atomically; delivery attempts append separately.

### S8-P02A — action/risk/state contracts

```text
Define ActionTypeVersion, ActionProposal, ActionExecution, OutboxMessage and
DeliveryAttempt schemas/states. Action proposal includes recommendation version,
target/provider binding, canonical payload digest, expected effect, risk,
reversibility/compensation, expiry and citations. Execution states distinguish
queued/sending/ambiguous/succeeded/failed/cancelled/reconciled/compensated.
Specify retry/cancel rules and pure transition tests.
```

### S8-P02B — action/outbox persistence

```text
Add tenant-scoped models/migration with recommendation/proposal/approval/provider
config/action type versions, canonical digest, idempotency, state, timestamps and
safe provider references. Outbox and ActionExecution commit atomically with one
consumed approval; attempts record request/response digests, provider ID, timing
and safe error. Enforce immutable proposal/payload, unique effect key and same-
tenant lineage. Add race tests.
```

### S8-P02C — proposal and validation service

```text
Implement ActionProposalService that maps a current recommendation candidate to
one allowlisted action schema, validates target/config/secret status, policy/
constraints/risk, exact canonical payload and expiry, then persists proposal.
Agent output is untrusted and cannot select arbitrary URL/provider/action type.
Material change creates a new proposal/digest and invalidates approval. Test
schema smuggling, stale recommendation and cross-tenant target.
```

### S8-P02D — exact approval and enqueue transaction

```text
Implement execute_proposal transaction: re-authorize actor/workspace/capability,
revalidate source/action/config/policy/current digest, atomically consume exact
Scope 3 approval and create ActionExecution/OutboxMessage/audit/event. Same
idempotency/digest replays; changed digest conflicts. Cancellation before claim
marks outbox non-dispatchable. Inject failure at each write and prove no approval
consumed without durable execution.
```

### S8-P02E — outbox dispatcher and delivery ledger

```text
Implement code-owned outbox claim/lease/heartbeat/backoff using integration
worker allowlist. Resolve secret at dispatch, re-check kill switch/policy, call
one adapter operation, persist attempt and terminal/ambiguous state, then emit
event. Never blindly retry ambiguous effect; schedule reconciliation. Bound
attempts/deadline/body and test duplicate worker, lease loss, 429/5xx/timeout.
```

### S8-P02F — outbox/approval adversarial gate

```text
Test one-field payload changes, Unicode/canonicalization, reused approval, self-
approval, stale policy/config/secret, concurrent execute, duplicate outbox,
cancel/claim race, ambiguous timeout and crash around provider/result commit.
Assert one durable intended effect, exact audit lineage and zero secret/provider
body leakage. Add pending/ambiguous/age/failure metrics and disable/runbooks.
```

## Plan 8.3 — first outbound provider action

**Contract.** Select one low-risk action with official sandbox, idempotency or
strong reconciliation, scoped credential and documented compensation. If no
candidate meets this bar, stop rather than implementing an unsafe adapter.

### S8-P03A — provider/action selection ADR

```text
Score pilot actions on user value, maximum harm/value, reversibility, provider
idempotency, read-after-write/reconciliation, cancellation, sandbox, stable target
ID, scoped auth, quotas, audit and region/retention. Choose one exact action
schema/version and compensation class with product/security owner. Pin official
API/version and known ambiguity. No generic webhook or arbitrary HTTP action.
```

### S8-P03B — action adapter and restricted transport

```text
Implement one adapter method execute/reconcile and compensate/cancel only if the
provider truly supports it. Use Scope 7 restricted egress/secret context,
provider idempotency key derived from DCLab execution, timeouts/rate limits and
bounded/redacted bodies. Normalize provider target/result/status. Add faithful
fake and shared action adapter contract; no DB access in adapter.
```

### S8-P03C — target/config validation and execution mapping

```text
Validate provider account/config/target against authorized discovered metadata
or allowlist before proposal/execution; prevent user/agent URL or identifier
confusion. Map canonical domain action to pinned provider request and response to
typed result. Record provider request ID/digest, never secret/raw body. Test
deleted/wrong-account target, stale config, schema version and malicious labels.
```

### S8-P03D — ambiguous delivery reconciliation

```text
On timeout/disconnect/5xx after send, mark ambiguous and query provider by
idempotency key/request/target state before any retry. Classify observed applied,
not applied, conflicting or unknown; require operator for unresolved. Apply
compensation only through a new exact-approved action when policy requires.
Test every ambiguity/retry/late-response ordering with the faithful fake.
```

### S8-P03E — cancellation and compensation behavior

```text
Define cancellation cutoff before dispatch and provider-supported cancellation
after dispatch; never claim reversal when unavailable. Model compensation as a
linked action with expected/observed result, separate approval and provider
evidence. Preserve original execution. Test cancel/claim race, partial provider
state, compensation failure/duplicate and policy revocation.
```

### S8-P03F — provider sandbox canary gate

```text
Run proposal/approval/outbox/execute/reconcile/cancel/compensate against faithful
fake and official sandbox using synthetic targets. Exercise duplicate, rate,
auth rotation, ambiguity, provider outage and two workspaces; verify at most one
effect within documented provider guarantees. Record limits/owners/metrics/
runbooks and canary a tiny allowlist with real-time action kill switch.
```

## Plan 8.4 — outcomes, corrections and impact evidence

**Contract.** OutcomeObservation records what was observed after an action or
do-nothing case; corrections append new versions. ImpactAssessment states method
and evidence class. FeedbackSignal is evaluation input, never automatic learning.

### S8-P04A — outcome/impact ADR and contracts

```text
Define OutcomeObservation, OutcomeCorrection, AttributionWindow,
ImpactAssessment and FeedbackSignal with source/action/do-nothing/case references,
observed interval/value/unit, collection method, quality, uncertainty and evidence
class. Define descriptive association versus experimental/causal qualification,
late/missing outcomes and correction/supersession. Add pure schema/language tests.
```

### S8-P04B — persistence and immutable correction lineage

```text
Add tenant-scoped outcome/impact/feedback records with decision/recommendation/
action/model/dataset versions, source/provider IDs/digests, event/observed/recorded
times and supersession. Enforce same-tenant lineage, unique source observation
idempotency and immutable originals. Correct through a new record with reason/
actor. Index due attribution windows and case/action timelines. Add migration tests.
```

### S8-P04C — outcome ingestion and attribution services

```text
Implement manual/API/connector outcome recording through typed services with
current authorization, schema/unit/time validation, idempotency and source
quality. Match to action/do-nothing/case only by explicit stable keys/window and
record ambiguity rather than guessing. Schedule due/missing outcome checks as
bounded jobs. Test late/duplicate/out-of-order/corrected/wrong-tenant observations.
```

### S8-P04D — impact assessment and causal-language enforcement

```text
Implement deterministic method eligibility for descriptive before-after,
controlled experiment, randomized/quasi-experimental or unsupported; store
assumptions/population/window/metric/comparator/uncertainty and cited artifacts.
Only qualifying design can emit causal label. LLM may explain an assessment but
cannot upgrade evidence class. Test confounding, no comparator, do-nothing,
small sample and corrected outcome.
```

### S8-P04E — outcome/impact evaluation gate

```text
Run outcome lag/missingness, duplicate/correction, attribution ambiguity,
do-nothing, experimental and misleading causal-language cases. Prove feedback
does not trigger training/policy mutation. Add outcome coverage/age/quality,
correction, impact-method and missing-window metrics plus collection/correction/
privacy runbooks. Record limitations before closed-loop UI.
```

## Plan 8.5 — APIs, clients, UI and agent tools

**Contract.** All surfaces show the chain case -> recommendation version ->
proposal -> approval -> execution/attempt -> outcome -> impact, with distinct
states/evidence. Mutations call shared services and exact approvals.

### S8-P05A — `/v1` decision/action/outcome resources

```text
Add bounded case/recommendation/proposal/execution/delivery/outcome/impact/
feedback resources and create/review/execute/cancel/reconcile/correct commands as
supported. Use scopes/capabilities, ETag/idempotency, opaque pages, request IDs and
safe audience projections. Provider IDs/errors and sensitive payload fields are
protected. Add OpenAPI, state/error and two-workspace tests.
```

### S8-P05B — SDK and CLI parity

```text
Add typed Python client and CLI methods/commands for inspect/list/watch, propose,
approval review, execute/cancel, outcome record/correct and impact read. Machine
output preserves distinct resource/state/evidence classes and never prompts.
Require exact workspace/version/idempotency and bounded wait. Add live API parity,
exit/signal and secret/redaction tests.
```

### S8-P05C — decision-to-outcome UI

```text
Build case/recommendation comparison including do-nothing, constraints,
uncertainty/evidence/citations; exact action proposal/approval/execution timeline;
and outcome/impact/correction views. Show active workspace, version/digest, cost/
risk and changed-since state. Never collapse “recommended” into “executed” or
“outcome” into “causal impact”. Add accessible state/component tests.
```

### S8-P05D — agent tools and audience-safe explanation

```text
Register read/proposal tools for cases/recommendations/actions/outcomes/impact;
execute only through Scope 3 exact command/approval and the enumerated adapter.
Use minimal envelopes and citations; prohibit arbitrary target/provider/payload.
Agent explanations must label prediction/recommendation/action/outcome/evidence
class and uncertainty. Test injection, stale version and causal overclaim.
```

### S8-P05E — cross-surface gate

```text
Run equivalent web/SDK/CLI/agent flows from case through do-nothing or approved
action, ambiguous recovery, outcome correction and impact display. Assert same
resources/state/events/audit, two-workspace isolation, exact approval, accessible
UI and safe machine output. Scan DOM/logs/errors for secret/provider/internal
leakage and document supported pilot action.
```

## Plan 8.6 — complete-loop verification and release

**Contract.** One complete supported loop must survive duplicate delivery,
ambiguous provider state, outcome delay/correction and operator recovery without
false causal or completion claims.

### S8-P06A — deterministic full-loop fixtures

```text
Create versioned synthetic fixtures for recommendation with do-nothing, exact
action, successful/failed/ambiguous delivery, cancellation/compensation, on-time/
late/missing/corrected outcome and descriptive/experimental impact. Define exact
expected rows/events/digests/citations/language. Ensure fixtures contain no live
credentials/customer/provider identifiers.
```

### S8-P06B — duplicate and ambiguity campaign

```text
Inject duplicate proposal/approval/execute/outbox jobs, network timeout before/
after provider effect, late responses, lease loss and service restart. Assert one
approval consumption/intended effect, reconciliation before retry, immutable
attempt history and explicit unresolved state. Run with real PostgreSQL and
faithful provider fake; one duplicate effect blocks release.
```

### S8-P06C — outcome and language campaign

```text
Exercise do-nothing, delayed/out-of-order/duplicate/corrected outcomes, ambiguous
attribution, missing comparator and nonexperimental data. Assert no causal label
or claimed business value beyond method evidence and all versions/corrections are
visible. Run translation/agent/UI/API/CLI output scans for forbidden conflation
or unsupported certainty.
```

### S8-P06D — privacy, retention and operator recovery

```text
Test classification, restricted targets, retention/deletion/holds across action
payload artifacts, delivery attempts, outcomes and impact. Verify secrets/raw
provider bodies are absent, deletion preserves required audit skeleton/digests,
and operators can resolve ambiguous/failed/missing outcomes through typed actions.
Drill credential compromise, action kill switch and provider outage runbooks.
```

### S8-P06E — Scope 8 go/no-go

```text
Run migrations, full regressions, adapter sandbox, security, load, API/SDK/CLI/
UI/agent and complete-loop E2E in staging. Have an allowlisted user complete the
supported decision-to-outcome workflow without DB intervention. Publish exact
provider/action limits, observed effect guarantees, outcome/impact evidence and
owners. Keep rollout narrow and immediately disableable.
```
