# Scope 1 prompts — complete durable read-only agent

Do not begin this file until the Scope 0 release gate is verified. Keep every
agent tool read-only. Use the common preamble in `README.md`.

## Plan 1.1 — contracts and state machine

### S1-P01A — freeze agent domain contracts

```text
Write the Scope 1 agent ADRs and versioned Pydantic domain contracts before
adding persistence. Define AgentRun states created, queued, running,
waiting_for_tool, waiting_for_user, completed, failed, cancelled, expired,
budget_exhausted and policy_blocked; allowed/forbidden transitions; immutable
terminal states; one-step-per-job behavior; source channels; autonomy L0/L1;
response components; citation contract; failure codes; and cancellation/retry
semantics. Define strict schemas for objectives, plans, tool selection/results,
clarification, cited final answers and progress events. Unknown fields fail.
Record that framework state is supplementary and DCLab records are authoritative.
```

### S1-P01B — prove the contracts as pure logic

```text
Implement table-driven unit tests for every state transition and terminal
invariant, structured-output acceptance/rejection, response/citation
requirements, canonical digest stability, bounded fields and failure mapping.
Test invalid tools, resource IDs, prose where JSON is required, absent
citations, oversized content, ambiguous objectives, policy blocks and budget
exhaustion. Add schema snapshots so a control-flow contract change requires
review. Do not call a live LLM or database in these tests.
```

## Plan 1.2 — agent control-plane persistence

### S1-P02A — add agent definitions, sessions, runs and messages

```text
Add a small additive migration and SQLAlchemy models for AgentDefinition and
immutable AgentVersion; AgentSession; AgentMessage; and AgentRun. Use UUIDs,
workspace/project/user or service-principal lineage, composite tenant foreign
keys, bounded redacted text/JSON, content/objective/configuration digests,
parent retry lineage, timestamps, expiry/retention and measured list/status
indexes. AgentVersion records graph key/version plus prompt/model/tool/budget
policy references and is immutable after promotion. A run snapshots effective
policy IDs/digests and hard budgets. Messages store redacted display content,
classification and optional encrypted Artifact reference—never hidden reasoning.
```

### S1-P02B — add steps, calls, checkpoints, citations and events

```text
Add AgentStep, AgentToolCall, AgentCheckpoint, AgentCitation and AgentEvent in a
separate migration. Steps/events/checkpoints are append-only and monotonically
sequenced per run. Tool calls store registered name/version, risk, redacted
arguments/results, digests, idempotency identity and status; Scope 1 allows R0/
T0 reads only. Checkpoints contain bounded IDs/counters/next node and a digest,
not copied data. Citations identify resource type/ID/field/version digest and
must be tenant-validatable. Add pending AgentApproval structure only if needed
for future compatibility, but expose no approval/write behavior. Test cross-
workspace FK rejection, sequence concurrency, immutability and delete/retention.
```

## Plan 1.3 — policy versions and usage ledgers

### S1-P03A — add immutable policy registries

```text
Add logical/version pairs for prompt templates/releases, model-routing
policies, tool policies, budget policies and data-policy sets. Common version
fields include parent/version/status/schema/digest/creator/time/evaluation and
immutable-after-promotion enforcement. Store bounded configuration or an
access-controlled Artifact reference. Seed a disabled/candidate read-only
agent configuration with metadata-only data policy, R0/T0 tools, fake model
route and conservative budgets. Promotion is a pointer/record, not row editing.
Add uniqueness/concurrent-promotion tests and historical resolution.
```

### S1-P03B — budget ledger and LLM invocation evolution

```text
Add atomic budget reservation/settlement/release/expiry records for steps, LLM
calls, tokens, estimated/actual numeric cost and currency, tool calls, wall
time, concurrent runs and owned jobs. Extend llm_invocations rather than
creating a second ledger: optional agent_run/agent_step context, prompt version,
attempt, retryable/error, provider request metadata, cached/fallback state and
precise cost. Enforce one valid pipeline, agent or evaluation context and same-
workspace links. Migrate compatibly with existing pipeline rows. Test concurrent
reservations at boundary/one-over, abandoned repair and legacy LLM behavior.
```

## Plan 1.4 — provider-neutral LLM gateway

### S1-P04A — gateway and deterministic fake

```text
Implement a transport-neutral LlmGateway receiving an already-built bounded
envelope, declared purpose, immutable model/prompt policy IDs, output schema and
budget reservation. Return validated output or a typed failure plus provider,
model, usage, latency, finish reason, request ID, safety signals and attempts.
Build a deterministic scripted fake for valid output, invalid JSON/schema,
unknown tool, timeout, rate limit, transient/permanent failure, delayed
response, cancellation, usage/cost and malicious reflection. Implement bounded
schema repair (maximum one by default), retry classification, deadlines and
circuit-breaker interfaces. Provider-specific types must not escape.
```

### S1-P04B — first production adapter and outage tests

```text
Add the first approved hosted provider adapter behind the gateway with server-
side credentials, explicit connect/read timeout, cancellation where supported,
structured output, maximum sizes, regional endpoint option, usage parsing and
health/quota signals. Route only by server policy and purpose—never a free-form
model ID. Test through a fake HTTP transport; add an opt-in synthetic-only live
smoke that never runs in normal PR CI. Prove rate-limit Retry-After handling,
bounded jitter/retries, circuit opening, stricter-only fallback, reservation
settlement on all outcomes, redacted logs/traces and provider-wide kill switch.
```

## Plan 1.5 — context and data policy

### S1-P05A — DataPolicyService and metadata-only envelope

```text
Implement DataPolicyService and ContextEnvelopeBuilder over authorized query
services. Scope 1 envelopes may contain principal/workspace/project,
ProblemSpec, dataset schema/profile/quality/freshness/policy metadata,
completed-run evidence/events/safe summaries, allowed tools, budgets and
relevant redacted turns. Exclude raw rows, sensitive values, credentials,
storage keys/URLs, model binaries, hidden reasoning and unrestricted artifacts.
Every item records source type/ID/version, classification, transformation and
digest. Persist envelope metadata, included/excluded counts, byte/token
estimate, policy version and digest; content retention is off by default.
```

### S1-P05B — exposure and injection adversarial matrix

```text
Test deny, metadata_only, aggregate_only, redact, allow_sample,
allow_full_bounded and null/unknown decisions even though Scope 1 activates only
metadata-safe context. Include secrets, direct/quasi identifiers, rare values,
free text, URLs, markup, Unicode controls and nested data. Treat uploaded,
connector, notebook, tool and provider content as untrusted data separated from
policy instructions. Prove another workspace's IDs, instructions to reveal
prompts/secrets, claimed new tools, arbitrary URLs and encoded injection cannot
change tools/context/authorization. Test deterministic transformations,
minimum aggregation groups, reclassification/deletion invalidation and envelope
size/truncation notices.
```

## Plan 1.6 — application services

### S1-P06A — sessions, runs, versions and budgets

```text
Implement transport-neutral AgentDefinitionService, AgentSessionService,
AgentRunService, PromptRegistry, ModelPolicyService and AgentBudgetService.
Support authorized create/list/get/rename/close/archive sessions, one run per
user message, immutable effective configuration snapshot, valid transition,
cancel, expire and child retry. Use ETag/version for mutable session metadata.
Re-authorize on run creation and every step. Reserve before work, settle actual
usage and repair abandoned reservations. Define transaction boundaries and
stable domain errors. Test owners/roles/two workspaces, concurrency, expiry,
retention and emergency stricter policy during an existing run.
```

### S1-P06B — tools, checkpoints, citations and events

```text
Implement AgentToolRegistry/executor, AgentCheckpointService,
AgentCitationService/Validator and AgentEventService. Tool registration uses
code-owned stable name/version, strict schemas, scope/capability, workspace
behavior, risk, idempotency, timeout/cost/result-size and data classes. The
runtime resolves the handler, re-authorizes, validates arguments/resources,
executes one read, bounds/classifies output, persists result/citations and emits
events. Checkpoint/result/next-state commit atomically. Reject arbitrary import
paths and duplicate registration. Test cross-tenant citations, stale resources,
oversized/malicious results, event cursor ordering and append-only behavior.
```

## Plan 1.7 — durable orchestrator and worker

### S1-P07A — bounded one-turn orchestrator

```text
Implement AgentOrchestrator as a small explicit graph: authorize -> load
checkpoint -> check cancel/expiry/policy/budget -> build context -> classify
intent -> select zero or one read tool -> validate observation -> ask user or
answer with citations -> checkpoint/terminalize. Do not start with an unbounded
ReAct loop. Add an allowlisted agent.turn.v1 MlJob handler and worker deployment
filter. Commit pending external intent before provider call and durable result/
usage/checkpoint afterward. Enqueue the next step only after commit. Long waits
become durable states, never a held HTTP request.
```

### S1-P07B — crash, duplicate, cancellation and bounds proof

```text
Inject worker death before LLM call, after response before result, after result
before checkpoint, after checkpoint before enqueue, before/after tool read,
during cancellation and while waiting for user. Define safe reconciliation for
ambiguous provider responses and prove duplicate job delivery cannot duplicate
a logical call. Test lease/heartbeat/reclaim, maximum steps/LLM/tool calls,
context/output bytes, tokens/cost, wall time, concurrency, session window,
expiry and cancellation propagation. Every run reaches completed, failed,
cancelled, expired, budget_exhausted or policy_blocked with stable evidence.
```

## Plan 1.8 — read-only tools

### S1-P08A — implement the first tool catalog

```text
Implement versioned read tools for current identity/workspace; list/get
projects and ProblemSpecs; list/get dataset versions, approved column/profile/
quality/freshness/lineage metadata; list/get ExecutionRequests and ModelBuilds;
read incremental build events; read locked evidence and audience-safe business
summary; list visualization and artifact metadata; and compare completed runs
deterministically if the existing service supports it. Every list is bounded/
cursor-based and every output identifies source resources/classification. Tools
call application/query services, not ORM objects or engine classes. Expose no
raw object access, signed URL, export or write.
```

### S1-P08B — per-tool authorization and contract suite

```text
For every read tool, test valid/additional/invalid fields, missing and cross-
tenant IDs, role/capability matrix, suspended/revoked membership, resource
state, current authorization recheck, timeout/cancel, result bound,
classification, citation validity, audit and safe errors. Prove the effective
catalog has no mutation, code, connector, secret, export, provider URL or
generic query capability. Test technical and business audience projections so
client users cannot see internal candidate names/metrics/prompts while allowed
engineers can inspect authorized evidence. Generate reviewed schema snapshots.
```

## Plan 1.9 — agent API and Python client

### S1-P09A — `/v1/agent` resources

```text
Add session create/list/get/patch; message list/create; run get/steps/events/
citations; run cancel/resume/retry endpoints. Message creation persists the
user message, creates one AgentRun and queues one turn atomically, then returns
202 with resource/status/event links. Lists use opaque cursors; mutable session
metadata uses ETag/If-Match; errors follow the standard envelope. Resume accepts
only an eligible waiting_for_user state and a schema-valid response. Retry
creates child lineage. Enforce workspace, ownership/capability, limits,
idempotency and audit. Start with cursor polling; do not add SSE yet.
```

### S1-P09B — SDK resources and live contract tests

```text
Extend dclab_client with typed agent sessions/messages/runs/steps/events/
citations and cancel/resume/retry methods, cursor iterators and an explicit
bounded waiter. Map stable server errors to typed exceptions and retain request
IDs. Preserve HTTP-only package isolation. Add sync contract tests against a
live PostgreSQL API for two workspaces, pagination, idempotent message submit,
waiting/resume, cancellation, event resumption, terminal failures and token/
content redaction. Update the OpenAPI/SDK coverage gate.
```

## Plan 1.10 — Agent Studio

### S1-P10A — build the read-only Agent Studio UI

```text
Add an Agent panel integrated with secure session/workspace/project context.
Implement session list/new/rename/close; objective/message composer; durable
run progress timeline; current node/status; cited resource cards with safe
links; assumptions/risks/limitations; budget usage; policy or budget blocked
states; cancel/retry; clarification/resume; and explicit feedback rating/reason.
The UI must say read-only and show when an LLM/tool is used. Reload/reconnect
from server state, not browser-only state. Never render hidden reasoning or raw
tool/provider bodies. Use accessible status announcements, focus and keyboard
behavior.
```

### S1-P10B — UI component and browser E2E

```text
Add component tests for loading, empty, running, waiting, completed, failed,
cancelled, expired, budget/policy block, stale and retry states; workspace
switch; citation failure; malicious Markdown; and feedback that does not train.
Add whole-system browser E2E using the fake provider: open session, ask about
dataset readiness and a completed/failed build, observe persisted steps, reload,
follow citations, request clarification/resume and cancel. Prove cross-workspace
resource substitution is denied and client/technical audience content differs.
Run accessibility automation plus manual critical-flow checks.
```

## Plan 1.11 — evaluation, operations and release

### S1-P11A — deterministic and adversarial evaluation gate

```text
Create versioned synthetic evaluation cases for dataset readiness, failed
build diagnosis, completed evidence, business translation, ambiguity/refusal,
provider/tool failure, budget/cancel/recovery, cross-tenant references,
sensitive/unknown columns, direct/indirect injection, secret exfiltration and
unsupported causal claims. Hard gates: authorization/policy, forbidden tools/
content, valid schema/terminal state, citation identity and budget. Grade task
success, clarity, completeness, citation usefulness and tool efficiency. Store
suite/case/agent/prompt/model/tool/data-policy/code versions, exact assertions,
usage and baseline delta. One safety violation fails regardless of average.
```

### S1-P11B — dashboards, runbooks and internal preview

```text
Instrument request -> run -> step -> LLM/tool -> checkpoint with OpenTelemetry
and bounded redacted logs. Add metrics/dashboards for terminal outcomes,
latency, steps/calls, invalid schemas, policy denials, citation failures,
provider errors/throttles, budget exhaustion, cost, approval age placeholder,
lease recovery and queue age. Add alerts/runbooks for provider outage, retry
storm, stuck run, budget spike, policy denial spike, suspected tenant leak and
kill-switch rollback. Release behind agent/provider/tool/data-context feature
flags plus workspace allowlist. Run staging synthetic smoke and have internal
users complete the acceptance workflow; record L0–L6 evidence and limitations.
```

## Scope 1 completion prompt

Run the complete exit checklist in the master plan. The final review must query
the effective tool registry and prove no write/export/code/connector action is
reachable. Scope 2 remains blocked unless every Scope 1 hard gate is VERIFIED.
