# Scope 3 prompts — controlled commands and agent-directed model builds

Start only after Scope 2's shadow/proposal gate. Use the common preamble.

## Plan 3.1 — canonical model-build command

### S3-P01A — implement ModelBuildCommandService

```text
Design and implement one atomic ModelBuildCommandService used by web, /v1, SDK
and agents. Input identifies workspace/project/Dataset/ProblemSpec and a typed,
validated plan plus optional agent proposal/approval. Re-authorize current
principal; resolve exact versions; validate target/entity/time/task/metric/
validation/holdout, data/model policy, resource state, quota and conservative
compute budget. Canonicalize intent and idempotency digest. In one transaction
create or replay ExecutionRequest, WorkflowRun, PipelineRun shell, MlJob and
event/audit/link records. Same key/digest returns the same resource; changed
digest conflicts. Preserve existing Labs behavior through an adapter.
```

### S3-P01B — expose and prove `POST /v1/model-builds`

```text
Add POST/list/get /v1/model-builds with 202 resource/status/event links and the
standard error envelope. Update OpenAPI and dclab_client before any agent tool
uses it. Test success, invalid/ambiguous target, stale Dataset/ProblemSpec,
locked plan, capability/policy/quota/budget denial, cross-workspace IDs, same/
different idempotency digest and concurrent duplicate with real PostgreSQL.
Assert final rows and links. Run the entire scientific suite to prove the new
command calls the existing deterministic engine and does not create a second
training path.
```

## Plan 3.2 — cancellation, retry and recovery

### S3-P02A — cooperative cancellation through the execution graph

```text
Implement CancellationService spanning AgentRun, AgentTask, ExecutionRequest,
MlJob, WorkflowRun and PipelineRun. A command records cancel request; queued
work cancels before claim; workers check between stages/folds and before
publish; active external/provider calls may finish but cannot schedule a next
step; incomplete objects are quarantined; immutable completed evidence remains.
Add model-build cancel endpoint/client/UI and ordered events. Define terminal
aggregation when multiple owned jobs exist. Test cancel before claim, during
stage/heartbeat, after completion, duplicate cancel and cross-workspace denial.
```

### S3-P02B — child retry/branch and ambiguous recovery

```text
Implement retry as a child ExecutionRequest/PipelineRun with explicit parent,
attempt/change/hypothesis lineage; never reset the parent. Validate whether the
same plan is replayable or a new immutable plan is required. Preserve holdout
discipline and artifacts. Add retry endpoints/client/UI. Inject worker death
before/after command commit, stage evidence, checkpoint and publish. Reconcile
ambiguous state from committed resources before retrying. Test attempt caps,
backoff, cancellation inheritance policy, same/different idempotency and event
aggregation across parent/child.
```

## Plan 3.3 — exact approval service

### S3-P03A — implement approval policy and ledger

```text
Complete AgentApproval/ApprovalService for sensitive read, paid compute,
internal mutation, publish and external-action risk tiers. Canonicalize action
type/schema/version, workspace, resource versions, exact payload, estimated
cost/volume, policy and expiry; hash it. Store requester/proposer, approver,
role/capability, status/reason/timestamps and separation-of-duties requirement.
Approval is one-time and cannot widen credential scope. Any material edit makes
the digest different. Add pending/list/get/approve/reject/revoke APIs with safe
summaries and notifications.
```

### S3-P03B — atomic consumption and adversarial tests

```text
Consume approval in the same transaction that creates the command/outbox
intent; otherwise resume the waiting AgentRun only after durable decision.
Test wrong workspace/action/digest/schema/version, expired/denied/revoked/
consumed, proposer self-approval, lost membership, concurrent approvers,
one-field payload edits, replay through another tool/channel and cancellation
while waiting. Assert only one consumer wins and no command exists after a
failed check. Instrument pending age, decision and suspicious substitution;
add operator runbook and approval-class kill switch.
```

## Plan 3.4 — activate bounded agent write tools

### S3-P04A — register typed internal command tools

```text
Add versioned tools to draft a new ProblemSpec proposal/version, validate build
readiness, create/cancel/retry a model build, request an authorized artifact
export and create a corrective child proposal. Each uses existing application
services, strict additionalProperties=false schemas, current authorization,
risk/budget/precondition/approval policy, deterministic idempotency and bounded
results with resource links. Do not expose generic SQL, service/function call,
engine class, arbitrary config patch, code execution, publish or external
action. Activate per agent version/workspace flag only after schema review.
```

### S3-P04B — execution-time policy and duplicate-tool proof

```text
Run the full per-tool suite: valid/invalid/additional arguments, forged IDs,
changed membership/policy/budget between plan and execution, stale proposal,
wrong approval, timeout/cancel, duplicate delivery before/after checkpoint,
result truncation/classification, citations and audit. Prove web/SDK/agent
produce identical resources and errors. Ensure an LLM cannot select handler
keys, import paths, raw payload JSON or unavailable tools. Query the effective
catalog for every role/autonomy level and snapshot it in tests.
```

## Plan 3.5 — bounded experimental iteration

### S3-P05A — implement portfolio and child-run policy

```text
Allow the Experiment Director to request a child build only from a typed
proposal containing parent run citation, hypothesis, one or more explicit
changes, expected information value, cost, validation, unchanged scientific
constraints and stop rule. Enforce maximum depth/children/compute/time/cost,
deduplicate equivalent hypotheses and require approval by threshold. Never use
final holdout results to tune the next candidate portfolio; require a new valid
evaluation design when needed. Persist iteration portfolio and usage lineage.
```

### S3-P05B — iteration scientific/economic evaluation

```text
Test useful improvement, no-op duplicate, invalid leakage repair, metric
shopping, repeated holdout tuning, excessive breadth/depth, budget exhaustion,
parent failure, cancellation and conflicting critics. Assert immutable parent
evidence, single winner holdout, deterministic validator authority and explicit
stop/deny. Evaluate success versus fixed single-run baselines, compute spent,
quality gained and false continuation rate. Keep automatic L3 iteration behind
an independent kill switch and disabled unless thresholds pass.
```

## Plan 3.6 — unified command and approval UX

### S3-P06A — web/SDK command and approval experience

```text
Make existing web build creation use the canonical command. Add SDK helpers and
Agent Studio plan/action cards showing exact workspace, versions, changes,
risk, estimated cost, remaining budget, approval need, cancellation limits and
status. Add pending approvals list/detail plus approve/reject/revoke with step-
up flow where policy requires. Show child lineage and preserve audience-safe
views. Reload from server state and prevent double-submit. No UI confirmation
or CLI --yes may bypass server approval.
```

### S3-P06B — whole-system lifecycle E2E

```text
Run browser and live SDK tests: propose -> approve -> command accepted -> worker
stages -> completed evidence -> cited explanation; plus denial, expiry,
duplicate submit, cancellation, failed run and child retry. Test two workspaces,
role changes during approval, network retry and page refresh. Assert accessible
status/focus/alerts, stable errors/request IDs and exact database/event rows.
The same test must prove legacy Labs still uses the deterministic engine.
```

## Plan 3.7 — release gate

### S3-P07A — scientific and security regression gate

```text
Add agent mutation evaluation cases for valid build, ambiguity, rejection,
duplicate, stale resource, cross-tenant ID, approval substitution, cancellation,
recovery and bounded iteration. Make tenant/policy/approval/citation/scientific
violations blocking. Run migrations, 945+ existing tests, scientific integrity,
OpenAPI/SDK, concurrency and browser E2E. Record baseline deltas and no hidden
failure or holdout leakage.
```

### S3-P07B — controlled rollout and rollback

```text
Create separate flags for each write tool, risk tier, agent version and
workspace. Add dashboards for command acceptance/completion, approval age,
idempotency conflict, cancellation latency, retries, compute/cost and policy
denial. Exercise disable during queued/running/waiting states, schema-compatible
application rollback and migration recovery in staging. Promote only to an
allowlisted controlled-write cohort with owner and support runbook.
```

