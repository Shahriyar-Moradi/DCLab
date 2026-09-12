# Scope 3 execution prompts — controlled commands and agent-directed builds

Start only after verified Scope 2. Apply `README.md` and
`EXECUTION_STANDARD.md`. Activate only typed application commands. No arbitrary
SQL, Python, filesystem, provider, prompt promotion or external action tool.

## Scope implementation boundary

Reuse ExecutionRequest, MlJob, WorkflowRun, PipelineRun, ProblemSpec,
`model_build_service.py`, workflow/job handlers, evidence locks and Scope 2
proposals plus the Scope 1 lifecycle and ProjectDecisionService. Add cohesive
command/approval services and a router such as
`api/v1_model_builds.py`; web, SDK and agent tools call the same services.
LangGraph nodes may request only versioned ToolRunner operations; they never
execute commands directly, hold approval authority or introduce a second tool/
agent loop. Do not add PydanticAI or framework-native command tools.

## Plan 3.1 — canonical atomic model-build command

**Contract.** Same actor/workspace/idempotency key/canonical payload digest
returns the same build. A conflicting digest is 409. Intent, lineage, job,
audit/event and initial budget reservation become durable atomically.

### S3-P01A — command ADR and request contract

```text
Inventory every current model-build creation path in lab/admin/services/jobs.
Write an ADR selecting one ModelBuildCommandService and typed request containing
workspace, project, ProblemSpec/dataset/plan versions, resource policy,
idempotency key and optional validated Scope 2 proposal. Define canonical digest,
preconditions, readiness, capability, quota/budget, response, state and errors.
List deprecated adapters and non-goals; do not implement routes yet.
```

### S3-P01B — transaction and idempotency implementation

```text
Implement the command transaction using existing models: authorize current
membership/capability, lock/validate source versions and readiness, reserve
quota/budget, create or replay ExecutionRequest, WorkflowRun, PipelineRun and
MlJob, then append audit/event and links. Use one code-owned handler/payload of
IDs and safe options. Same digest replays; different digest conflicts. Inject
failure at every write and prove no orphan or duplicate remains.
```

### S3-P01C — plan/proposal validation and scientific preconditions

```text
Validate ProblemSpec, dataset/access/classification, scientific-plan version,
metric direction, validation/holdout and resource bounds through existing
deterministic services. A Scope 2 proposal is input evidence only and must match
current base/version/digest plus approved patch schema. Re-run validation inside
the transaction; never trust UI/agent readiness. Test stale, locked, quarantined,
cross-tenant and holdout-weakened inputs.
```

### S3-P01D — `POST /v1/model-builds` and read resources

```text
Add create/get/list endpoints with Scope 0 error/page/request-ID conventions,
Idempotency-Key, explicit workspace, typed response links and safe status. Define
201 first create, 200 replay, 409 digest/precondition conflict, 422 validation,
429 quota and anti-enumeration behavior. Routes call only the command/query
services. Update deterministic OpenAPI and add full negative contract tests.
```

### S3-P01E — Python client and legacy adapters

```text
Add typed SDK create/get/list model-build methods and idempotency support. Route
existing web/lab/admin creation paths through the same service or a documented
compatibility adapter; remove duplicate transaction logic only after parity
tests. The client remains HTTP-only. Add operation/client parity plus regression
tests proving identical resource IDs/state/events across web, SDK and service.
```

### S3-P01F — command completion gate

```text
Run PostgreSQL concurrency with many identical/conflicting requests, failure
injection, two-workspace/resource substitution, readiness/quota denial, worker
pickup and full existing scientific regression. Verify one intent/run/job/audit
lineage and no early holdout access. Add create/replay/conflict/latency metrics,
runbook and feature flag; record evidence before agent tool activation.
```

## Plan 3.2 — cancellation, retry and recovery

**Contract.** Cancellation is cooperative and terminal evidence is preserved.
Retry/repair creates a child attempt/branch with explicit change and parent
citations; it never resets or overwrites a completed run.

### S3-P02A — lifecycle and child-lineage contract

```text
Define allowed cancellation and retry/branch transitions across ExecutionRequest,
WorkflowRun, PipelineRun, stage runs and MlJob. Specify propagation order,
terminal/no-op/conflict behavior, cancellable external operations, child attempt
number, change digest, parent evidence, inherited immutable plan and new budget.
Map every state to API/event/audience-safe status. Add pure transition tests.
```

### S3-P02B — cooperative cancellation implementation

```text
Implement request_cancel through one application service with authorization,
ETag/idempotency, cancellation timestamp/actor/reason and event. Workers check
before claim/expensive stage and after external/compute return, stop scheduling
new stages, release reservations and terminalize consistently. Quarantine partial
unverified outputs rather than publish. Test concurrent finish/cancel, repeated
cancel, lost worker and non-cancellable section.
```

### S3-P02C — retry/branch command implementation

```text
Implement retry/branch as a new child ExecutionRequest/PipelineRun/MlJob with
parent ID, attempt, hypothesis/change schema/digest and cited failure/evidence.
Reuse immutable valid inputs/artifacts by reference only after digest/access
validation; never alter parent rows. Enforce maximum attempts/portfolio budget
and same-key replay. Test changed payload conflict, parent wrong state, cross-
workspace parent and concurrent retry.
```

### S3-P02D — lifecycle API, SDK and aggregation

```text
Add cancel/retry endpoints and SDK helpers with 202 pending, 200 replay/no-op and
stable conflict/denial responses. Aggregate parent/child status/events without
hiding individual terminal reasons. Use opaque event cursor and bounded tree
depth/page. Update web callers only after contract tests. Add OpenAPI examples
for cancel requested, cancelled, retry child and ambiguous recovery.
```

### S3-P02E — recovery and artifact gate

```text
Inject failures at cancellation propagation, stage completion, artifact upload,
event append and child enqueue. Reconcile orphan jobs/runs, ambiguous partial
objects and stuck cancel requests deterministically. Prove verified parent
artifacts/evidence remain immutable and quarantined outputs cannot be consumed.
Add lifecycle/recovery metrics and runbook; record end-to-end evidence.
```

## Plan 3.3 — exact approvals and risk policy

**Contract.** Approval binds actor, workspace, action schema/version, canonical
payload digest, risk, expiry and one-time use. It never expands authentication,
membership, data policy or tool authority.

### S3-P03A — approval/risk ADR and schemas

```text
Define risk tiers for sensitive read, compute, internal mutation, publish/export
and external action, with required capability, approver separation, expiry and
reauthentication. Define ApprovalRequest/Decision/Consumption and exact summary
schemas, canonicalization/versioning and states requested/approved/rejected/
expired/consumed/revoked. Specify no-approval and always-approval classes. Add
digest golden vectors and pure policy tests; no action execution yet.
```

### S3-P03B — approval persistence and constraints

```text
Add tenant-scoped approval request/decision/consumption records with target
action type/schema/payload digest, requester/approver, risk/policy version,
expiry, reason and timestamps. Enforce immutable decisions, one terminal decision,
one consumption and same-workspace principals/resources. Store only bounded safe
summary, not secrets/raw payload. Add migration, race and cross-tenant tests.
```

### S3-P03C — ApprovalService and atomic consumption

```text
Implement request/review/revoke/expire/validate_and_consume services. Recompute
current action digest and policy, re-authorize executor/approver and source
versions at execution, then consume in the same transaction as command intent.
Any field/version/policy/auth change invalidates approval. Test concurrent
consumption, stale membership, self-approval, clock expiry, replay and transaction
rollback without consumed-orphan state.
```

### S3-P03D — approval API and safe presentation contract

```text
Add bounded `/v1/approvals` list/get/request/review/revoke resources with ETag,
idempotency, explicit workspace and capability. Response shows exact human-
reviewable summary, risk, expected effect/cost, expiry and changed-since warning,
without secrets/internal payload. Add SDK types and negative tests for resource
substitution, stale decision and unauthorized reviewer. No external actions yet.
```

### S3-P03E — adversarial approval gate

```text
Test canonicalization ambiguity, Unicode/numeric/order changes, one-field
substitution, reused approval for another workspace/action/version, self-review,
concurrent consume, expired/revoked account and crash around consumption/command.
Verify logs/events provide nonrepudiable IDs/digests but no sensitive body. Add
pending/expiry/denial/consume metrics, cleanup/runbook and emergency mutation
disable before tools use approvals.
```

## Plan 3.4 — activate bounded agent command tools

**Contract.** Tool handlers adapt validated Scope 2 proposals to the same
command services used by API/web. Initial tools are enumerated and independently
kill-switchable. No generic update, SQL, code, connector or external-action tool.

### S3-P04A — write-tool policy and catalog release

```text
Define exact versioned tools for draft ProblemSpec version, validate readiness,
create/cancel/retry model build, request approved export and create corrective
child proposal. For each specify input schema, resource/version preconditions,
capability, risk/approval, idempotency, budgets, allowed states and result schema.
Publish a candidate tool release disabled by default. Reject arbitrary patch
paths/unknown fields in contract tests.
```

### S3-P04B — ProblemSpec/readiness command adapters

```text
Implement draft-version and readiness tools over existing ProblemSpec/target/
data/scientific services. Agent provides a typed proposal ID/base digest; service
re-authorizes and creates an immutable draft child version or returns validation.
No in-place edit or self-approval. Record tool call, command/resource IDs and
citations, append the corresponding project decision and refresh lifecycle
impact. The decision record never substitutes for the command. Test stale base,
changed dataset, invalid target and cross-workspace.
```

### S3-P04C — model-build lifecycle command adapters

```text
Implement create/cancel/retry adapters over Plan 3.1/3.2 services with exact
payload digest, current policy, budget and approval where required. Return
durable resource/status links, not a fabricated completion. Re-check tool release
and membership at dispatch. Test duplicate calls, policy revoked after planning,
approval substitution, cancellation race and worker failure.
```

### S3-P04D — export request boundary

```text
Define export request as a durable typed command referencing authorized immutable
artifacts/data policy, intended audience/format/expiry and exact approval when
required. Implement request/metadata only using existing artifact service and
job boundary; delivery remains private/bounded. Prevent agent-chosen storage
paths, arbitrary URLs or raw-row export. Test classification, deleted source,
changed artifact and duplicate request.
```

### S3-P04E — tool execution security and release gate

```text
Run the shared tool contract plus two-workspace, revoked membership, stale
proposal, unknown tool/version, malformed patch, budget, approval, duplicate job,
provider injection and cancellation tests. Prove every mutation maps to one
application command/audit/event and agents cannot call private services. Add
per-tool success/denial/latency/cost metrics, runbook and staged allowlist.
```

## Plan 3.5 — bounded experiment iteration

**Contract.** A child experiment changes one declared hypothesis/plan dimension,
inherits immutable parent evidence, has a separate budget, and stops before
holdout-driven tuning or unbounded search.

### S3-P05A — iteration policy and hypothesis schema

```text
Define allowed change classes (data/preparation/feature/candidate/metric/resource)
and prohibited changes (target leakage, holdout tuning, evidence rewrite, code).
Child proposal records hypothesis, one canonical typed diff, parent citations,
expected signal, validation, estimated budget, maximum depth/portfolio and stop
rule. Add pure tests for one-change discipline, equivalent digest, holdout and
budget policy.
```

### S3-P05B — portfolio budget and selection service

```text
Implement atomic portfolio accounting across parent and all descendants for
runs, candidates, CPU/GPU time, wall time, tokens/cost and concurrent children.
Require a valid proposed diff and deterministic scientific validation before
reservation. Selection of next proposal is code-owned/ranked by policy, not an
unbounded agent loop. Test concurrent children, cancellation, failure settlement
and exhausted global cap.
```

### S3-P05C — child experiment command and lineage

```text
Extend model-build command to create an immutable child plan/run from a validated
iteration proposal. Bind parent run/evidence, base/new plan digests, hypothesis,
budget and agent/tool versions. Never copy mutable result rows or reveal holdout
metrics before the allowed gate. Add same-key replay, stale parent, invalid plan
and cross-workspace tests.
```

### S3-P05D — stop/evaluation rules and agent feedback

```text
Implement stop on budget/depth/count/time, repeated-equivalent proposal,
insufficient improvement, instability, safety failure, user cancel or policy
revocation. Feed only CV/allowed evidence into the next proposal; final holdout
is evaluation, not tuning input. Persist decision reasons/citations through
ProjectDecisionService and expose a safe summary to the supervisor. Record why
an attempt continued/stopped, which constraint was met or missed and which
candidate was selected/rejected. Test ties, noisy gains and late results.
```

### S3-P05E — scientific/economic iteration gate

```text
Run synthetic portfolios covering useful correction, no improvement, oscillation,
duplicate hypothesis, failed child, concurrent proposals, budget exhaustion and
tempting holdout leakage. Assert bounded termination, immutable lineage and
honest cost/quality accounting. Compare to single deterministic build; add
portfolio dashboards/runbook/kill switch and enable only for allowlisted plans.
```

## Plan 3.6 — unified command and approval experience

**Contract.** Web, SDK, CLI and agent display/request the same command, approval,
lifecycle and project-decision resources. The three product views resolve the
same IDs/versions. UI never infers approval validity or command completion.

### S3-P06A — shared command/approval client models

```text
Complete Python and TypeScript models for build command, lifecycle, proposal,
approval, project decision, comparison, budget/cost, denial and event projections. Generate or manually map
from OpenAPI with parity checks. Include idempotency, ETag and exact resource
links; exclude internal handler/storage/provider detail. Add schema fixtures for
all terminal and pending states.
```

### S3-P06B — build and iteration web workflow

```text
Route existing lab/model-build creation through the canonical `/v1` command.
Show active workspace, immutable inputs/versions, validation, estimated bound,
idempotent submission, status/events, cancel/retry and child lineage. Handle
409/422/429, reload, duplicate click and race with completion. No optimistic
“running/succeeded” state before server confirmation. Reuse lifecycle and
implementation deep links so feature/formula/config/environment evidence is
inspectable without making code the primary workflow.
```

### S3-P06C — exact approval review experience

```text
Build requester/reviewer views for exact summary/digest version, risk, expected
effect/cost, evidence, expiry and changed-since status. Require capability,
separation and explicit confirm/reject reason; use ETag and refresh on conflict.
Never display secrets/raw payload or allow approval by hidden default. Add
keyboard/accessibility and concurrent-review component tests.
```

### S3-P06D — agent proposal-to-command experience

```text
In Agent Studio, show typed proposal diff, deterministic validation, citations,
budget and whether approval is required. Provide Accept, Reject, Compare and
Modify: accept requests the existing command where applicable; reject records a
decision only; compare uses immutable runs; modify creates a new typed proposal.
No control executes a client-side patch. Display durable command/run/decision
lineage and policy denial. Test stale proposal, altered resource, revoked
membership, approval expiry and workspace switch without leaking prior state.
```

### S3-P06E — cross-surface E2E gate

```text
Run equivalent web, SDK, CLI preview and agent journeys for create/replay/conflict, approval,
cancel, retry, child iteration, denial and recovery. Assert identical resource
IDs/state/events/audit/decisions, lifecycle impact and audience-safe outputs.
Deep-link conversation/workflow/implementation views. Run accessibility and DOM/
network leakage scans. Document support/operator workflow, flags and rollback
before controlled-write preview.
```

### S3-P06F — early ML engineer SDK/CLI preview

```text
Inspect `packages/dclab_client` and the Scope 5 CLI package plan. Extend the same
HTTP-only client with the bounded Core ML path: inspect project lifecycle and
decisions, validate/propose/build, list/watch/cancel/retry, compare candidates,
download verified reproduction artifacts and inspect cost. Add the eventual
`packages/dclab_cli` skeleton only if absent, with `project`, `lifecycle`,
`decision`, `build` and `model compare` commands over that SDK; never import API
internals or create a temporary second CLI. Use the explicit non-browser token
flow for an internal allowlist only; no public machine-identity claim before
Scope 5. Define JSON/JSONL, exit codes, timeout/signal/idempotency, explicit
workspace and redaction now so Scope 5 can extend compatibly. Add mocked/live API,
two-workspace, cancellation, output-golden and secret-scan tests. Mark packages
preview/private and publish nothing before Scope 5 supply-chain gates.
```

## Plan 3.7 — controlled-write release gate

**Contract.** Scope 3 passes only with existing scientific tests plus new
authorization, approval, idempotency, recovery and agent-mutation campaigns.

### S3-P07A — scientific regression campaign

```text
Run all preparation/leakage/validation/CV-selection/holdout/reproducibility/
evidence-lock tests plus new command and child iteration paths. Add cases proving
agents cannot alter locked plans/results, tune from holdout, bypass readiness or
create unsupported candidates. Compare outputs for unchanged deterministic
inputs and investigate every drift before proceeding.
```

### S3-P07B — concurrency and failure-injection campaign

```text
Stress duplicate/conflicting creates, concurrent approvals/consumption, cancel/
finish race, retry race, child budget allocation, worker loss and failures around
each transaction boundary. Prove exactly one durable intent/consumption/effect,
terminal reconciliation and no orphan budget/job/artifact. Use real PostgreSQL
and production-shaped worker processes.
```

### S3-P07C — authorization and mutation adversarial campaign

```text
Attempt cross-workspace/resource substitution, stale membership, self-approval,
payload canonicalization tricks, forged proposal/citation/tool/version, prompt
injection, direct private route/service use and unsupported patch paths. Assert
fail-closed stable errors, safe audit and zero unauthorized mutation. One failure
blocks release regardless of other quality results.
```

### S3-P07D — load, quota and operational recovery

```text
Measure command latency, job queue/fairness, cancellation observation, approval
age and bounded iteration under recorded workloads. Validate quotas/backpressure,
worker drain/restart, stuck intent reconciliation, artifact quarantine and every
kill switch. Establish initial alerts from observed limits and execute runbooks
with named owners.
```

### S3-P07E — controlled-build go/no-go

```text
Run migrations, backend/SDK/web/browser/full-system suites in a production-shaped
environment and complete one allowlisted proposal -> approval -> build -> cancel/
retry or child -> verified result flow without DB intervention. Publish exact
release/tool/policy versions, evidence and limitations. Enable only enumerated
commands for the allowlist; external actions and arbitrary code remain off.
Proceed to Plan 3.8 only after this controlled-build gate passes.
```

## Plan 3.8 — model registration, batch prediction and monitoring MVP

**Contract.** Reuse `ModelAsset`, `ModelVersion`, `PredictionTask`, `Prediction`,
`RuntimeEnvironment`, `CodeSnapshot`, artifact/reproducibility/evidence services,
`admin_model_registry_service.py`, `admin_monitoring_service.py`, `ml/predict.py`,
ExecutionRequest/MlJob and the lifecycle/decision services. Deliver one narrow
batch path; do not add online REST/streaming/edge serving, arbitrary model code,
automatic retraining, multi-cloud provisioning or a second registry.
Use focused test homes `apps/api/tests/test_model_release_contract.py`,
`test_model_release_persistence.py`, `test_model_release_service.py`,
`test_batch_prediction_service.py`, `test_model_monitoring_service.py`,
`test_model_operations_api.py`, `packages/dclab_client/tests/test_model_operations.py`
and `apps/web/e2e/core-ml-model-operations.spec.ts`; extend a proven equivalent
owner and document the substitution rather than adding duplicate suites.

### S3-P08A — batch-release architecture and contracts

```text
Inventory existing model registry, prediction, artifact, runtime/code snapshot,
monitoring and `/v1` behavior. Write an ADR defining ModelRelease,
BatchPredictionRun, FeatureContract and MonitoringWindow using existing records
where sufficient. Define release draft/validating/ready/active/rolled_back/failed
and batch queued/running/succeeded/failed/cancelled states; exact ModelVersion,
model/preprocessor/feature manifest/environment/code digests; input dataset
version; output artifact; baseline/current windows; permissions, idempotency,
approval, quotas and rollback. Choose one supported CPU batch format/use case.
Define fail-closed typed settings and `.env.example` entries including
`DCLAB_MODEL_RELEASE_ENABLED=false`, `DCLAB_BATCH_PREDICTION_ENABLED=false`,
`DCLAB_MODEL_MONITORING_ENABLED=false` and explicit maximum input rows/bytes,
output bytes, execution seconds and concurrent runs; production cannot boot with
the features enabled and missing bounds.
Map every lifecycle edge and rejected alternative. Add pure contracts/transitions
and compatibility tests only; do not create a parallel model registry.
```

### S3-P08B — persistence and tenant integrity

```text
Discover the live Alembic head and first prove whether current ModelVersion,
PredictionTask/Prediction and observability rows can carry the contract. Add only
missing tenant-scoped ModelRelease, BatchPredictionRun, FeatureContract and
MonitoringWindow/metric records, with immutable version/digest references,
ExecutionRequest/MlJob/artifact links, state/timing/usage/safe failure, rollback
lineage and current-release uniqueness as designed. Enforce composite workspace/
project/model/dataset relationships, terminal immutability, one idempotency digest
and indexes for active release, batch queue/history and unevaluated windows. Test
empty/live-head migration, cross-tenant IDs, concurrent activation/rollback,
retention and forward repair. Do not copy model or prediction bodies into JSON.
```

### S3-P08C — immutable package, environment and feature-contract verifier

```text
Implement a verification service over artifact_service, reproducibility_service,
evidence locks, ModelVersion, RuntimeEnvironment, CodeSnapshot and feature
manifest/lineage. A release is ready only when model/preprocessor/feature artifacts
exist, digests/MIME/size match, environment/code are pinned, deterministic build
verification passed and the ordered input feature contract defines names, types,
nullable/category/unknown handling and prediction-time availability. Quarantine
pickle/code formats not allowed by the ADR and never dynamically import an
unverified artifact. Return typed reason codes and append lifecycle/audit events.
Test missing/tampered artifacts, incompatible environment, reordered/type-drifted
features, leakage-only features, stale evidence and two workspaces.
```

### S3-P08D — release and rollback command services

```text
Implement create/validate/activate/rollback through one ModelReleaseService using
current authorization, exact ModelVersion/feature/environment digests, policy,
idempotency, ETag and approval when required. Activation atomically selects one
eligible release for the supported batch target and appends audit/event plus a
ProjectDecisionRecord explaining promotion; it never mutates ModelVersion.
Rollback creates/activates a new release transition referencing the prior safe
release and records why. Add transaction failure injection, same/different digest
replay, stale approval, revoked membership, concurrent promotion and no-safe-
rollback tests. Provide independent batch-release kill switch.
```

### S3-P08E — bounded batch inference job

```text
Register one code-owned `batch_prediction.run.v1` handler. The command binds
active release, authorized immutable input Dataset/Artifact version, feature
contract, output schema, resource budget, idempotency and optional label column
excluded from features; atomically create intent/run/job/event. Worker streams
bounded input, verifies digest/schema, loads only the verified supported package,
uses `ml/predict.py`/current serving artifact contract, writes predictions as a
private immutable artifact and persists safe counts/distribution/usage—not raw
values in events/logs. Support cooperative cancel, deadline, retry/reconciliation
without duplicate outputs. Test exact predictions on frozen fixtures, column
order/type/category failures, large streaming bounds, worker loss, duplicate job,
quarantined input/output and cross-workspace access.
```

### S3-P08F — monitoring windows and investigation proposals

```text
Implement deterministic reference/current MonitoringWindow evaluation for input
schema/quality/drift and prediction distribution; add performance/calibration
only when delayed labels and metric contracts are valid. Version every method,
threshold, population, time window and multiple-testing policy; distinguish no
data, insufficient volume, drift, degradation and infrastructure failure. An
alert appends evidence and may schedule an agent investigation that produces a
typed, cited ProjectDecisionRecord/retraining or rollback proposal only—never an
automatic retrain/deploy. Test stable/shifted/missing/late/corrected-label, false-
alarm, low-volume, stale baseline and policy-revocation cases. Emit bounded
metrics/alerts and add disable/recompute runbooks.
```

### S3-P08G — `/v1`, SDK/CLI and synchronized UI

```text
Add cohesive `/v1/model-releases`, batch-prediction runs, monitoring windows,
investigations and rollback resources using standard pages/errors/ETag/
Idempotency-Key/status links. Extend the existing Python client and S3-P06F CLI,
not parallel packages, with register/validate/activate/batch/watch/monitor/rollback.
Extend current model registry/monitoring pages and project workspace with release,
batch and monitoring lifecycle nodes plus Conversation/Workflow/Implementation
deep links. Show exact model/feature/environment/input/output versions, status,
metrics, costs, decisions and rollback target; no storage paths/raw rows/client-
inferred success. Add OpenAPI parity, component/accessibility and equivalent
web/SDK/CLI/agent two-workspace journeys. Define 201 first create, 200 read/
idempotent replay, 202 accepted async validation/batch/monitor/rollback, 409
digest/state/precondition conflict, 422 feature/input validation and 429 quota,
with anti-enumerating 403/404 behavior.
```

### S3-P08H — Core ML MVP model-operations gate

```text
Run migrations, deterministic package verification, batch prediction golden
fixtures, idempotency/concurrency, cancellation/restart, artifact tamper, feature
skew, drift/late-label, authorization, approval, rollback and full scientific
regressions in a production-shaped environment. Have one data scientist promote
a verified result and inspect/compare its rationale; have one ML engineer perform
the same release, batch, monitor and rollback path through SDK/CLI without DB
access. Reconstruct lifecycle and decision history exactly, scan logs/artifacts/
clients for secrets/raw rows and measure batch latency/memory/cost. Add dashboards,
alerts, runbooks and independent release/inference/monitor flags. Publish the Core
ML evidence and limitations; online serving, automatic retraining, business
actions and arbitrary code remain disabled and do not block this gate.
```
