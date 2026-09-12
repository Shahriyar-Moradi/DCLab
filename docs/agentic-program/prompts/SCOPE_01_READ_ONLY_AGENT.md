# Scope 1 execution prompts — complete durable read-only agent

Start only after the Scope 0 gate. Apply `README.md` and
`EXECUTION_STANDARD.md`. The released catalog is metadata/evidence read-only:
no build, export, connector, notebook-code, approval, or domain-mutation tool.

## Scope implementation boundary

Extend the existing API service using `domain/agent.py` and cohesive
`services/agent_*.py` modules; mount a resource router such as `api/v1_agent.py`.
Use current SQLAlchemy metadata and PostgreSQL jobs. Add SDK resources beneath
`packages/dclab_client`; add Agent Studio under `apps/web/app/app/agent/` with
components under `apps/web/app/components/agent/`. Use a pinned raw LangGraph
`StateGraph` as the only orchestration runtime, ordinary Pydantic models for
typed state/contracts, and a DCLab-owned provider-neutral gateway whose first
adapter uses the official OpenAI SDK. Do not add PydanticAI, `pydantic-graph`,
LangChain `create_agent`, Agent Server as a second product API, or another
agent loop. Exact names may change only when current conventions require it and
the implementation packet records why.

## Plan 1.1 — agent and policy contracts

**Contract.** Freeze DCLab-owned, framework-independent product schemas and
state machines before a migration while selecting LangGraph as the sole private
execution runtime. Runs are immutable-version-bound and bounded; tool outputs
and LLM outputs are untrusted; approvals are inactive placeholders.

### S1-P01A — agent runtime ADR and resource contracts

```text
Inventory existing LlmInvocation, job, event, authorization and evidence models.
Write the agent-runtime ADR selecting one pinned LangGraph `StateGraph` and
compatible PostgreSQL checkpointer release, with Python support, lockfile,
upgrade/deprecation policy and rejected alternatives. Explicitly exclude
PydanticAI, `pydantic-graph`, LangChain `create_agent` and nested autonomous
loops. Define `domain/agent.py` value objects for definition, version, session,
message, run, step, tool call, product checkpoint reference, citation and event.
Specify IDs, workspace/project lineage, actor, immutable version references,
timestamps, input/output digests and audience. Assign LangGraph only graph
routing/private checkpoint execution; assign DCLab services/PostgreSQL product
state, authorization, tools, budgets, citations, events and recovery policy.
Define how current official-OpenAI-SDK usage is wrapped by the DCLab gateway.
Add schema/dependency-boundary tests only; do not create tables, install an
unpinned package or call a provider.
```

### S1-P01B — run, step and session state machines

```text
Define pure transition functions for session active/closed; run queued/running/
waiting/cancel_requested/succeeded/failed/cancelled/expired; and step pending/
running/waiting/succeeded/failed/skipped/cancelled. List allowed initiators,
terminal behavior, retry/child semantics and event emitted for every transition.
Reject backward/unknown transitions and edits after terminal state. Add exhaustive
table-driven/property tests. Keep these DCLab product transitions independent of
LangGraph private classes and do not encode them only in graph nodes or routes.
```

### S1-P01C — tool, context, citation and structured-output contracts

```text
Define versioned JSON-compatible schemas for tool descriptor/call/result,
ContextEnvelope, context item, citation target/span/digest and final answer with
claim/citation mapping. Bound names, strings, lists, nesting, bytes and item
counts; distinguish user text from trusted instructions and tool/provider data.
Specify safe error categories and redacted previews. Add round-trip and hostile
payload tests. Define a Pydantic `AgentGraphState` containing bounded IDs,
versions, counters, digests and typed previous/next results—never ORM/session,
provider SDK or secret-bearing objects. No arbitrary Python object, SQL, URL or
storage key is accepted.
```

### S1-P01D — prompt, model, data and budget policy contracts

```text
Define immutable draft/published/retired versions for agent, graph, prompt,
model, tool, data and budget policy. Specify purpose, environment, compatible
schema versions, digest, creator/reviewer, activation interval and rollback
reference. Budget dimensions include steps, tool calls, tokens, cost, wall time,
context bytes and concurrency with reserve/settle semantics. Data policy defaults
unknown/null to deny. Add monotonic policy and canonical-digest tests.
```

### S1-P01E — contract threat model and acceptance gate

```text
Threat-model injection, confused deputy, unauthorized citations, tool argument
smuggling, context exfiltration, provider retention, replay, budget race, event
leakage, forged version IDs, nested-loop amplification, checkpoint forgery,
checkpoint/product-state divergence, framework supply-chain compromise and
unsafe framework upgrade. Map every threat to a contract invariant and future
test owner. Review schemas against Scope 0 API/error/version conventions.
Publish state, graph-boundary and recovery diagrams plus a compatibility matrix;
close only if no table/service implementation must invent an unresolved state
or authority decision.
```

## Plan 1.2 — agent control-plane persistence

**Contract.** Add tenant-safe DCLab product tables through current metadata and
migrations. Large message, product-checkpoint and tool bodies become immutable
artifacts when over the bounded inline threshold. LangGraph runtime checkpoint
tables are private, separately named and introduced only in Plan 1.7. No prompt
secrets, hidden reasoning or framework-serialized ORM objects are stored.

### S1-P02A — definitions, versions, sessions and messages schema

```text
Add AgentDefinition, AgentVersion, AgentSession and AgentMessage tables/models.
Definitions carry workspace or system scope, stable key and status; versions
carry schema/digest and immutable policy references; sessions carry workspace,
optional project, actor and close state; messages carry role, bounded safe body
or artifact reference, digest and sequence. Enforce composite tenant lineage,
unique keys/sequences and immutable published versions. Add one additive
migration and model/constraint tests; no run execution yet.
```

### S1-P02B — runs, steps and attempt lineage schema

```text
Add AgentRun and AgentStep with session/version/workspace/project, parent run,
attempt, state, current/maximum bounds, deadlines, cancellation, terminal reason,
input/output digest, lease metadata and ordered step number. Enforce same-tenant
parent/session/version references, one active attempt rule as designed, and
terminal immutability through service plus DB constraints where practical. Add
indexes for session timeline, worker claim and operator terminal scans.
```

### S1-P02C — tool calls, checkpoints and citation schema

```text
Add AgentToolCall, AgentCheckpoint and AgentCitation. Tool calls bind step,
tool-version key, canonical arguments/result digest, status, timing and bounded
safe error; product checkpoints bind the DCLab state-machine cursor, graph
release, opaque runtime checkpoint ID and resume digest, but never contain a
serialized LangGraph state blob. Citations bind answer/message, authorized
resource type/id/version/digest and safe label. Use artifact references for
large bodies. Enforce sequence, workspace lineage, append-only records and no
raw credentials/URLs. Add migration integrity tests.
```

### S1-P02D — agent events and approval placeholder

```text
Add append-only AgentEvent with per-run monotonic sequence, type/schema version,
actor/request/trace correlation and bounded audience-safe payload. Add only the
minimal inactive approval reference needed for forward compatibility; do not
implement approval behavior before Scope 3. Reuse existing event/query patterns
where safe. Test concurrent sequence allocation, cursor ordering, cross-tenant
constraints and immutable history with real PostgreSQL.
```

### S1-P02E — migration compatibility, retention and deletion

```text
Test empty database, live previous-head upgrade, downgrade or documented forward
repair, and application compatibility before/after expand. Define retention for
messages, inline/tool bodies, checkpoints, events and artifact objects; preserve
audit/citation evidence while honoring deletion/tombstone policy. Add bounded
cleanup/reconciliation intent, not destructive ad-hoc SQL. Verify indexes and
query plans for session/run/event reads with versioned fixtures. Specify
independent retention for future private LangGraph checkpoint rows versus DCLab
audit and citation evidence.
```

### S1-P02F — persistence adversarial gate

```text
Exercise two workspaces, forged parent/session/version IDs, concurrent message/
step/event creation, duplicate tool/checkpoint writes, terminal-row mutation,
orphan artifact references, forged opaque runtime checkpoint IDs and process
loss around commits. Prove database and service invariants reject cross-tenant
or inconsistent lineage and no checkpoint can prove authorization. Run migration
and integrity suites and record the live head/digests before services depend on
the schema. Repair only Plan 1.2 defects.
```

## Plan 1.3 — policy releases and usage ledger

**Contract.** Policies are immutable releases, selected server-side by purpose
and environment. Usage reservations prevent concurrent overspend; provider
reports never replace local bounds.

### S1-P03A — policy registry persistence

```text
Add prompt, model, tool, data and budget policy release records or one typed
registry design approved by ADR. Each release stores scope, semantic/schema
version, canonical body/artifact digest, status, environment, compatibility,
creator/reviewer and activation/retirement. Enforce one applicable active release
per key/environment and immutable published bodies. Prompt-body permission is
separate from metadata permission. Add additive migration and conflict tests.
```

### S1-P03B — budget account, reservation and settlement ledger

```text
Add budget account/snapshot, reservation and settlement entries bound to agent
run/step and policy version. Reserve atomically for steps, calls, tokens, cost,
time, bytes and concurrency; same idempotency key replays; settlement cannot
exceed reservation without explicit fail-closed policy. Release abandoned leases
through a reconciler. Test concurrent reservations, duplicate settlement,
negative/overflow values, expiry and cancellation in PostgreSQL.
```

### S1-P03C — evolve LLM invocation lineage

```text
Add nullable compatibility fields to existing LlmInvocation for workspace,
agent run/step, prompt/model/data/budget/tool releases, provider request digest,
usage, latency, outcome and safe error category. Backfill historical rows as
legacy/unknown without granting exposure. Ensure request/response bodies and
hidden reasoning are not retained. Add composite lineage where possible,
indexes for cost/incident queries and compatibility tests for old callers.
```

### S1-P03D — registry and budget services

```text
Implement transport-neutral selection, publish, resolve-active, reserve, settle,
cancel and reconcile services. Require current capability for publish/metadata/
body access; runtime receives immutable snapshots, not mutable registry rows.
Return typed domain errors for missing compatibility, budget denial and race.
Emit safe events/metrics with policy IDs and low-cardinality reason codes. Do
not add admin UI or autonomous promotion yet.
```

### S1-P03E — policy/ledger completion gate

```text
Run migration, two-workspace, immutability, single-active, compatibility and
budget race tests. Simulate crash before/after reservation and settlement and
prove reconciliation neither leaks capacity nor double-charges. Verify raw
prompt bodies require the separate capability and logs expose no body/secret.
Record query plans and rollback/disable behavior before provider work begins.
```

## Plan 1.4 — provider-neutral LLM gateway

**Contract.** Add DCLab-owned `services/llm_gateway.py` and a narrow adapter
package while wrapping `openai_provider.py`. All callers use ordinary Pydantic
request/result contracts; CI uses a deterministic fake. Do not use PydanticAI
or a framework-provided model/tool loop.

### S1-P04A — gateway interface and request pipeline

```text
Define LlmGateway.complete_structured(request, schema) with purpose, immutable
policy snapshots, ContextEnvelope digest, deadline, idempotency/correlation and
reserved budget. Resolve provider/model server-side, validate context exposure,
bound serialized bytes and reject unsupported capabilities before network I/O.
Return typed content/usage/finish/safe-error metadata. Unit-test with no provider;
do not expose OpenAI, LangGraph or other provider/framework SDK types outside
their adapters.
```

### S1-P04B — deterministic fake provider

```text
Implement a scriptable fake adapter supporting valid structured responses,
malformed JSON/schema, unknown fields, refusal, tool proposal, truncation,
timeouts, retryable/permanent/rate-limit errors, usage mismatch and cancellation.
Responses derive deterministically from fixture keys, not prompt wording. Add a
shared adapter contract and use the fake for all CI orchestration tests. Ensure
fixtures contain no customer data and failure bodies are bounded/redacted.
```

### S1-P04C — production provider adapter

```text
Refactor existing OpenAI integration behind the gateway as the first adapter.
Configure endpoint/model/region/retention/training guarantees through model/data
policy and secret references. Apply connect/read/total timeouts, bounded retries
with jitter and Retry-After, circuit breaker and cancellation. Validate structured
output strictly with ordinary Pydantic models and record only safe invocation
metadata. Use the official OpenAI SDK directly inside this adapter. Add mocked
transport tests; no live credential in PR CI and no PydanticAI dependency.
```

### S1-P04D — usage, budget and idempotency settlement

```text
Integrate atomic reservation before provider dispatch and settlement after every
success/failure/timeout. Enforce local maximum even when provider usage is absent
or inconsistent; record estimation provenance. Replayed completed idempotency
keys return the recorded result reference, while ambiguous network outcomes are
not blindly re-sent. Test crash points, concurrent calls, over-budget response,
cancellation and circuit-open behavior.
```

### S1-P04E — telemetry, configuration and synthetic smoke

```text
Add typed provider settings, boot validation, separate feature/provider kill
switches and a manually/securely scheduled synthetic smoke using non-sensitive
fixtures. Emit request count, latency, token/cost, retry, refusal, schema failure
and breaker state by bounded provider/model-purpose labels. Add outage/rotation/
retention runbooks. Logs/traces must omit prompt/context/response bodies and keys.
```

### S1-P04F — gateway adversarial gate

```text
Run the shared contract against fake and mocked production adapters, including
prompt injection strings, huge/malformed outputs, provider HTML errors, hangs,
429/5xx, cancellation, budget races and policy revocation between scheduling and
dispatch. Prove no call occurs for denied data/purpose and every invocation has
version/cost lineage. Run the synthetic smoke outside PR CI and record evidence
without provider content before enabling Scope 1 orchestration.
```

## Plan 1.5 — data policy and immutable context envelope

**Contract.** Add `services/data_policy_service.py` and
`services/context_envelope_builder.py`. Metadata-only is the initial product;
raw rows and unrestricted free text remain unavailable.

### S1-P05A — effective data-policy resolver

```text
Resolve effective exposure from workspace, project, dataset and column policy,
classification source/confidence, purpose, provider/model capability, region,
retention and user capability. Choose the strictest applicable rule; unknown,
null, conflict or stale classification denies. Return a versioned decision with
reason codes and source IDs, not a boolean alone. Add pure policy matrices and
PostgreSQL tests for mixed-column/two-workspace cases.
```

### S1-P05B — metadata-only ContextEnvelope builder

```text
Build immutable bounded envelopes from authorized project, ProblemSpec, dataset
schema/profile, run state, completed metrics and artifact metadata. Every item
records resource/version/digest, classification, audience and citation target.
Separate system instructions, user text, trusted structured facts and untrusted
source text. Deterministically sort, truncate and digest. Do not include rows,
credentials, signed URLs, internal paths or hidden errors.
```

### S1-P05C — injection and content-boundary controls

```text
Normalize and label untrusted dataset names/descriptions/provider/tool text;
prevent it from becoming system/tool instructions. Reject control characters,
oversized nesting and unsupported media; escape only at the correct renderer,
not by corrupting source facts. Add adversarial fixtures for indirect injection,
Unicode/confusable content, formula-like text, fake citations and exfiltration
requests. Verify structured trusted fields remain distinguishable end to end.
```

### S1-P05D — invalidation, retention and deletion hooks

```text
Record envelope source versions/digests and invalidate reuse when membership,
classification, policy, source version, retention or deletion state changes.
Never cache authorization beyond the documented bound. Store large envelope
bodies only as protected artifacts with TTL; preserve enough digest/lineage for
audit after body deletion. Test concurrent policy change, revoked membership,
deleted source and stale checkpoint resume.
```

### S1-P05E — exposure and citation gate

```text
Run a matrix across provider/purpose/data class/region/retention/capability and
two workspaces. Assert denied fields never appear in gateway request serialization,
logs, traces, errors, caches or citations. Verify every included fact resolves
to a currently authorized source/version/digest and deterministic envelopes are
byte-stable. Add policy-denial metrics/runbook and keep any row-level mode off.
```

## Plan 1.6 — agent application services

**Contract.** Services own authorization and transactions; routes, workers and
tools call them. Split by session/run/budget/registry/citation/event concern and
avoid a god `AgentService`.

### S1-P06A — session and message services

```text
Implement create/list/get/close session and append/list message services with
current workspace membership, optional project authorization, bounded content,
monotonic ordering, idempotency and artifact spillover. User messages are
untrusted and immutable; assistant messages reference run/version/citations.
Define safe not-found/denied/conflict errors and event emission. Test two
workspaces, concurrent append, closed session and duplicate keys.
```

### S1-P06B — run and version-binding services

```text
Implement create/get/list/cancel/retry run services. At creation atomically
resolve and snapshot compatible agent/graph/prompt/model/tool/data/budget
versions, validate feature/allowlist and reserve initial budget. Persist intent
and `agent.turn.v1` job atomically. Retry creates a child attempt with immutable
parent reference; it never edits terminal evidence. Test policy races and
same-key replay/different-digest conflict.
```

### S1-P06C — prompt/tool registries and execution authorization

```text
Implement runtime lookup by immutable version and purpose. Tool descriptors are
code-owned, schema-versioned, read-only in Scope 1 and mapped to one application
query service. At call time re-check principal/session/workspace/resource/data
policy, current membership and budget; scheduled authority is insufficient.
Return bounded typed results with citation candidates. Test registry mismatch,
revocation and argument smuggling.
```

### S1-P06D — budget service integration

```text
Wrap reserve/settle/release/reconcile behind AgentBudgetService and require it
for run, step, provider and tool operations. Define global/run/child dimensions,
deadline computation and terminal reason on exhaustion. Reservations and state
changes share a transaction where possible; otherwise use explicit recoverable
intent. Add injected-clock and concurrent-worker tests plus low-cardinality
budget-denial/exhaustion metrics.
```

### S1-P06E — citation validation and answer finalization

```text
Implement CitationValidator that resolves resource/version/digest through
authorized query services, checks claim mappings and rejects inaccessible,
changed, deleted or unsupported evidence. Finalization validates structured
answer schema, audience-safe wording and citation coverage before appending the
assistant message. Define “no supported answer” as a successful safe outcome.
Add mixed valid/invalid and revocation-race tests.
```

### S1-P06F — event service and application-service gate

```text
Implement monotonic append/list event service with opaque cursor, audience-safe
projection and correlation. Run service-level scenarios from session creation
through queued run/cancel/finalize without a real provider. Prove transaction
rollback leaves no orphan job/reservation/event and replay is stable. Document
service ownership and forbid routes/workers from direct agent-row mutation.
```

## Plan 1.7 — LangGraph runtime and bounded worker

**Contract.** Register only `agent.turn.v1` in the existing dispatcher and use
one pinned raw LangGraph `StateGraph` as the sole orchestration runtime. One job
may traverse bounded pure nodes but performs at most one provider or tool
operation, persists DCLab product records plus a private runtime checkpoint, and
stops before scheduling the next turn. No in-memory recursive or nested agent
loop owns durability.

### S1-P07A — versioned graph topology and typed state

```text
Implement the code-owned `read_only_agent_v1` raw LangGraph `StateGraph` using
the Pydantic AgentGraphState contract. Nodes load/authorize, build context,
reserve, request one typed NextAction, validate, execute zero or one tool,
validate citations, checkpoint/yield, finalize or fail. Routing predicates are
pure deterministic functions over validated state. Do not use LangChain
`create_agent`, PydanticAI, `pydantic-graph`, ORM/provider objects in state or
dynamic graph generation. Add graph compilation/version-digest and pure traces
for every route and terminal/invalid state. The LLM cannot authorize, select
worker handler keys or introduce nodes/tools.
```

### S1-P07B — PostgreSQL checkpointer and ownership boundary

```text
Install the ADR-pinned LangGraph and compatible PostgreSQL checkpointer packages
through the project lockfile. Configure private runtime tables in a dedicated
`agent_runtime` schema with a least-privilege worker role, bounded serialization,
encryption/retention requirements and an explicit migration/upgrade procedure.
Map LangGraph thread/checkpoint IDs opaquely to DCLab AgentRun and
AgentCheckpoint references; public services never query runtime tables and a
thread ID never proves tenancy. Test create/resume/list-for-recovery, schema
upgrade, malformed/oversized state, cross-run substitution, cleanup and product/
checkpoint mismatch. Do not deploy LangGraph Agent Server or expose checkpoint
APIs.
```

### S1-P07C — worker turn, checkpoint and next-job handoff

```text
Register `agent.turn.v1` with a narrow handler. Claim run/step under lease,
re-authorize, invoke only the pinned graph release, reserve, execute at most one
provider or ToolRunner operation, persist result/product checkpoint, settle,
append event, commit the runtime checkpoint, and enqueue the next turn atomically
or through explicit recoverable intent. Payload contains IDs/version only. Stop
the graph at the defined turn boundary; never call an until-complete autonomous
loop. Add heartbeat, deadline, bounded serialization and tests proving one
external operation per delivered job.
```

### S1-P07D — cancellation, interrupts and crash recovery

```text
Check cancellation/policy revocation/deadline before dispatch and after external
return. Use LangGraph interrupt/resume only at named idempotent boundaries; code
before an interrupt must be safe to replay. Define safe late results, waiting,
maximum wait and expiry. Inject failure before/after claim, provider/tool
dispatch, DCLab result, runtime/product checkpoint, settlement, event and
next-job enqueue. Prove duplicate delivery cannot repeat a tool/provider
operation or cost settlement. Reconcile product/checkpoint divergence,
terminalize lost work and reconstruct after process restart with PostgreSQL
concurrency tests.
```

### S1-P07E — orchestration limits and backpressure

```text
Enforce maximum steps, tool/provider calls, tokens, cost, context/result bytes,
wall time, active runs per workspace/user and queue age. Reject or queue fairly
with stable reason/retry metadata; never create unlimited child jobs. Add metrics
for state/queue/lease/checkpoint/recovery/exhaustion by bounded labels and a
worker drain/graph kill-switch runbook. Bound graph checkpoint bytes and pure
node traversal per turn. Benchmark with synthetic bounded fixtures.
```

### S1-P07F — orchestrator system gate

```text
Run deterministic fake-provider system cases for direct answer, multi-tool,
policy block, insufficient evidence, malformed decision, outage, cancellation,
crash recovery, duplicate job and every budget. Verify terminal reconstruction,
events, citations, reservations, product/runtime checkpoint agreement, exactly
one agent loop and no write tool. Include pinned dependency/SBOM and framework
upgrade/rollback evidence. Record latency/cost step baselines and only then
enable the handler for an internal allowlist.
```

## Plan 1.8 — read-only tool catalog

**Contract.** Tools are typed adapters over existing application queries, never
raw DB/object access. Initial results are metadata/evidence summaries with hard
page/byte/time bounds and citation targets.

### S1-P08A — inventory and version the first catalog

```text
Map user questions to the smallest initial tools: identity/capabilities,
projects/ProblemSpec, dataset schema/profile/readiness, execution/build status,
bounded events, completed scientific evidence, artifact metadata and safe
technical/business summaries. For each define name/version, JSON input/output,
required capability/data policy, allowed states, maximum items/bytes/time and
citation mapping. Register code-owned descriptors; no dynamic import/tool name.
```

### S1-P08B — implement identity/project/dataset tools

```text
Implement tools through workspace, project, problem, data access, dataset column
and profile services. Require explicit resource IDs where ambiguity is unsafe;
lists use opaque cursors and bounded fields. Return classification-aware schema/
aggregate metadata only, never rows or signed URLs. Re-authorize on every call.
Add contract tests for empty, denied, deleted, quarantined and cross-workspace.
```

### S1-P08C — implement execution/evidence/artifact tools

```text
Wrap execution request, model build, observability, evidence lock, verification,
lineage and artifact metadata queries. Expose audience-safe stage/status/failure,
metrics and provenance only for completed/authorized evidence; clearly label
provisional state. Artifact tool returns metadata/digest, not content/download.
Test missing/failed/running/locked states, large timelines and internal-detail
redaction.
```

### S1-P08D — tool runner and result sanitation

```text
Implement one ToolRunner that validates schema, canonicalizes arguments,
re-authorizes, reserves budget, applies timeout/page/byte limits, invokes the
mapped service and validates/sanitizes results. Record call/result digests and
safe error category. Reject unknown version/fields/resource types and provider-
supplied tool names. LangGraph nodes can call only this runner; framework-native
or directly decorated tools are prohibited. Add malicious argument/result,
timeout and revocation tests.
```

### S1-P08E — catalog safety and coverage gate

```text
Run shared contract tests for every tool, including two workspaces, suspended
membership, policy change, huge result, injection text, secret/storage/internal
field scan and citation resolution. Prove catalog diff contains no create/update/
delete/export/code/connector action. Measure result sizes/latency, document tool
owners and independent catalog kill switch, and freeze the Scope 1 tool release.
```

## Plan 1.9 — agent `/v1` API and Python client

**Contract.** Mount `api/v1_agent.py` using Plan 1.6 services and Scope 0 common
errors/pages/request IDs. The SDK remains HTTP-only and workspace-explicit.

### S1-P09A — session and message endpoints

```text
Add `POST/GET /v1/agent/sessions`, `GET/DELETE-or-close /sessions/{id}` and
`POST/GET /sessions/{id}/messages` with typed request/response/page schemas,
idempotency on creates, ETag where mutable, explicit workspace and current
membership/capability. Define 201/200/202/204 and stable 400/401/403/404/409/422/
429 behavior. Route code only validates transport and calls services.
```

### S1-P09B — run, step, event, citation and control endpoints

```text
Add run create/get/list, bounded steps, opaque-cursor events, citations, cancel
and child retry endpoints. Creation returns durable resource/links; polling uses
ETag or cursor and Retry-After where useful. Cancellation is 202 until observed;
retry is idempotent and only for allowed terminal states. Ensure audience-safe
projections and test cross-session/workspace ID substitution.
```

### S1-P09C — OpenAPI and negative transport contract

```text
Document examples and schemas without leaking prompt/tool internals. Add API
tests for missing/malformed workspace, invalid state, stale ETag, tampered
cursor, duplicate key, body/limit overflow, unsupported media, internal error
and rate/quota denial. Update deterministic OpenAPI snapshot and classify the
change as additive. Verify request IDs and no FastAPI detail/provider body leak.
```

### S1-P09D — Python client agent resources

```text
Add typed session/message/run/step/event/citation models and sync client methods,
iterators and wait/cancel helpers using existing transport/error/retry rules.
Waiters bound timeout/poll interval and honor server Retry-After; creates expose
idempotency. Keep explicit workspace and safe body/stream limits. Add mocked
transport plus live PostgreSQL API tests; no API internal imports.
```

### S1-P09E — API/SDK parity gate

```text
Generate an operation-to-client parity manifest and fail CI on drift. Run the
complete read-only conversation lifecycle through the client, including reload,
cancel, retry, events and citations, plus two-workspace denial and provider
failure. Verify no write/export/code route exists. Record compatibility/version
and rollback behavior before Agent Studio consumes the API.
```

## Plan 1.10 — Agent Studio UI

**Contract.** Build beneath `apps/web/app/app/agent/`; reuse AppShell, session/
query providers and UI primitives. The UI renders server capabilities and run
state but never invents authorization or success.

### S1-P10A — information architecture and typed client hooks

```text
Define routes for session list/new/session detail and component boundaries for
messages, composer, run progress, tool steps, citations, budget/policy notices
and feedback. Add schemas/hooks keyed by workspace/session/run using the BFF.
Model loading/empty/blocked/error/offline/terminal states explicitly. Keep raw
prompts, hidden reasoning and admin policy bodies out of client types.
```

### S1-P10B — session and objective experience

```text
Implement accessible session list/create/close and message composer with bounded
input, idempotent submit, double-submit prevention and clear active workspace.
On send, show the durable server message/run rather than an optimistic invented
answer. Preserve reload/back-forward behavior and safely recover expired session
or membership. Add component tests for empty/error/denied/slow states.
```

### S1-P10C — progress, cancellation and reconnect

```text
Render ordered server events/steps with audience-safe labels, current bound
usage and terminal reason. Poll/reconnect using opaque cursor and backoff; stop
on terminal/visibility loss as appropriate. Implement cancel/retry from current
server state and handle race with completion. Test duplicate/out-of-order event
responses, network loss, workspace switch and page reload without data leakage.
```

### S1-P10D — citation, policy and feedback experience

```text
Render claim-linked citations with resource type/name/version/digest and an
authorized navigation target; never render arbitrary provider URLs/HTML. Show
policy/budget blocks with safe reason and next action. Add explicit helpful/not-
helpful plus bounded comment feedback as evidence only, not automatic learning.
Test missing/revoked citations, malicious labels and keyboard/screen-reader flow.
```

### S1-P10E — browser and accessibility gate

```text
Run component and Playwright journeys for zero/one/many workspaces, new and
existing session, direct answer, tool answer, policy block, provider error,
cancel, retry, reload/reconnect, revoked membership and narrow/mobile viewport.
Perform automated accessibility checks and manual keyboard/focus review. Verify
no tokens/raw internals leak into DOM/storage/errors before allowlisted release.
```

## Plan 1.11 — evaluation, operations and internal release

**Contract.** Evaluation is versioned evidence with hard safety assertions and
quality/cost/latency baselines. Scope 1 releases behind feature flag plus user/
workspace allowlist and separate provider kill switch.

### S1-P11A — versioned golden and adversarial suite

```text
Create synthetic versioned cases for project/dataset readiness, running/failed/
completed builds, insufficient evidence and audience-safe explanation. Assert
tool choice/arguments, policy decision, structured schema, citation coverage,
unsupported-claim refusal, bounds and terminal state; add injection/exfiltration/
cross-tenant/secret/provider-failure cases. Store large fixtures/results as
artifacts with digests. One safety failure fails the run regardless of average.
```

### S1-P11B — evaluation runner and baseline comparison

```text
Implement deterministic offline evaluation over the fake provider and a
controlled synthetic provider-integration mode. Persist suite/release/result,
per-assertion evidence, latency/token/cost and baseline delta. Define calibrated
rubrics only for non-deterministic quality and require reviewer identity. Add CI
for deterministic cases and a scheduled/manual provider run. No production
promotion is automatic in Scope 1.
```

### S1-P11C — observability, alerts and operator views

```text
Add run/step/job/provider/tool/policy/citation/budget metrics and trace links with
bounded labels; structured logs use IDs/digests/reason codes only. Create operator
queries/dashboard definitions for stuck/failed/expired runs, queue age, breaker,
budget exhaustion, citation invalidation and evaluation regression. Define alert
thresholds from observed internal baselines and assign owners.
```

### S1-P11D — runbooks, flags and recovery drills

```text
Document provider outage, runaway cost, stuck queue, corrupt checkpoint,
unauthorized citation, policy rollback, user deletion and total agent disable.
Implement global/workspace/user allowlist plus independent orchestration/provider
flags with fail-closed defaults. Drill cancellation, worker restart, breaker,
policy rollback, LangGraph/checkpointer compatible upgrade and total disable
without schema rollback; record observed recovery. Document dependency rollback,
runtime-schema forward repair and product/checkpoint reconciliation.
```

### S1-P11E — internal end-to-end release gate

```text
Run migrations, full regression, two-workspace, fake/provider synthetic,
adversarial, budget-concurrency, crash-injection, API/SDK and browser E2E. Have an
allowlisted user complete the supported questions without DB intervention.
Publish the Scope 1 evidence record with versions, quality/cost/latency, safety
assertions and limitations, including exact LangGraph/checkpointer pins and proof
that no nested agent runtime is installed or invoked. Do not start Scope 2 until
every hard gate passes.
```
