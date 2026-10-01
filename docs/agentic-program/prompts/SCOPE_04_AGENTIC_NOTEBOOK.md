# Scope 4 execution prompts — managed agentic notebook and isolated compute

Every prompt inherits the plan-level outcome and mandatory live-checkout
execution card in [the remaining-scope map](REMAINING_SCOPE_EXECUTION_MAP.md).
Complete one reviewable lettered work unit at a time.

Start after the authoritative S2-P11F gate; S2-P12/S2-P13/S2-P14 may run in parallel
and do not block this scope. Any write/build cell also requires Scope 3. Apply
`README.md` and `EXECUTION_STANDARD.md`. Managed cells call typed DCLab services.
Python remains disabled until the real isolated runtime passes Plan 4.7.
The notebook is a secondary investigation/implementation view, never DCLab's
project or ML-lifecycle source of truth.
Read `../JUPYTER_RUNTIME_MVP_BLUEPRINT.md` and
`../EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md` before runtime work. Execute
RT1-A before Plan 4.1 and RT7-A before adding runtime placement persistence or
an external adapter; the RT work packages refine these plans without changing
their evidence IDs.

## Scope implementation boundary

Add `domain/notebook.py`, cohesive `services/notebook_*.py`, code-owned notebook
job handlers, `api/v1_notebooks.py`, SDK resources and
`apps/web/app/app/notebooks/`. Store revision/cell metadata in PostgreSQL and
large immutable outputs in object storage. Never execute code inside API/worker-
ML processes. Agent-initiated notebook work enters through DCLab application
services and the same LangGraph/ToolRunner boundary. The notebook scheduler and
isolated code runtime are not agent graphs and must not embed PydanticAI or
another orchestration loop. An OpenAI Agents, Deep Investigation or allowed NOOA runtime may
request a separately authorized sandbox task only through the same DCLab
control-plane contract; no agent framework runs inside the sandbox. Notebook revisions bind canonical lifecycle node and
ProjectDecisionRecord IDs/versions; they do not duplicate either state.
Notebook cells may cite DCLab TrackingReference, ModelPackageManifest,
FeatureContract and MonitoringWindow projections after Plan 3.0, but never use
MLflow credentials/URLs/types, persist Pandera/Evidently objects, or load a model
package outside the approved isolated/ML worker boundary. Notebook output is not
an experiment tracker, registry, release decision or monitoring authority.
Managed query/profile cells call the S0-P09 DataScan application service with
an authorized immutable artifact and registered typed operation. DuckDB remains
inside the ML/data worker; notebook/API/agent processes receive neither DuckDB,
SQL, paths nor Arrow readers. Python sandboxes do not gain DuckDB or Polars as
preinstalled capabilities in the MVP.
The S2-P12 Deep Investigation worker has no model-visible shell. A separately
approved code-assisted investigation, S2-P13 OpenAI Agents purpose or S2-P14
NOOA proposal runtime may request
this sandbox only through a versioned, read-input/write-quarantined-output DCLab
tool with its own run, policy and budget. Deep Agents/OpenAI Agents/NOOA never become
part of the sandbox control plane, and the sandbox never receives an agent
runtime credential, connector secret or product-database access.
An interactive Python session owns one isolated sandbox and kernel only for the
same authorized owner, notebook revision and runtime epoch. Cells within that
epoch may reuse volatile memory; a restart creates a new epoch and never claims
to restore RAM. Fresh verification, training and batch work use disposable
attempts. If completion is uncertain, reconcile provider and control-plane state
before retrying and never automatically replay a cell with possible side effects.

## AWS/GCP portability requirements

Notebook and arbitrary-Python execution use one `SandboxRuntimePort` and one
immutable execution contract. The AWS adapter targets an approved isolated EKS
Fargate/equivalent profile; the GCP adapter targets GKE Sandbox/gVisor. Both
must enforce no service-account token/metadata credential, no host mount or
privilege, denied-by-default egress, identical resource/output/time bounds,
quarantine, cancellation and cleanup. If either cloud cannot satisfy the
verified profile, Python stays disabled there—never fall back to an ordinary
worker. Notebook APIs and records contain no provider runtime identifiers.

Runpod, Railway, Lambda GPU Cloud, Vast.ai and Nebius are optional execution
targets behind the same runtime/placement contracts, not alternate DCLab homes.
Use RT7/RT8 for explicit workspace grants, current capabilities and quotes,
data-transfer policy, provider-scoped credentials and cleanup verification.
Canonical lifecycle rows and artifacts remain in the deployment's AWS/GCP home;
an external target receives only an attempt-scoped manifest and bounded inputs.

## Plan 4.1 — notebook domain and storage model

**Contract.** A notebook has immutable revisions; a revision has ordered typed
cells and pinned environment/input references. Execution produces attempts and
immutable outputs; editing creates a new revision with optimistic concurrency.

### S4-P01A — notebook ADR and state contracts

```text
Write the notebook ADR covering Notebook, Revision, Cell, Environment, Execution,
InputBinding and Output contracts; draft/published/archived notebook state;
revision parent/digest; cell type/order/config schema; execution lifecycle; and
managed versus isolated runtimes. Define collaboration boundary, retention,
authorization and non-goals. Reconcile stateful session/epoch ownership with
disposable verification/batch attempts and the external placement owner defined
by RT7-A. Add pure schema/state tests. Do not create tables or enable Python.
```

### S4-P01B — notebook/revision/cell persistence

```text
Add tenant-scoped Notebook, NotebookRevision and NotebookCell models/migration.
Notebook carries workspace/project/title/status/current revision; revision carries
parent/version/digest/author/message and immutable ordered snapshot; cells carry
stable logical ID, order, type/schema version and bounded config/body or artifact.
Enforce same-tenant lineage, unique revision/order/logical ID and immutable
published revisions. Add PostgreSQL constraints/index tests.
```

### S4-P01C — environment and input binding persistence

```text
Add immutable NotebookEnvironmentVersion and NotebookInputBinding. Environment
pins managed runtime version or isolated image digest, package/locale/timezone/
resource/network policy and schema. Inputs bind resource type/id/version/digest,
classification and access snapshot but require current access at execution.
Reject mutable “latest”, arbitrary image/URL/package and cross-tenant references.
Add migration and canonical-digest tests.
```

### S4-P01D — execution/output persistence and object placement

```text
Add NotebookExecution, CellExecution and NotebookOutput with attempt/state,
revision/environment/input digests, job/cancel/deadline, timing/resource usage,
safe error and output artifact metadata. Outputs are typed text/table/chart/
resource/file references with strict inline/size/media bounds. Enforce same-
tenant lineage and terminal immutability. Add indexes for runnable/status/timeline
queries and object orphan prevention.
```

### S4-P01E — migration, retention and integrity gate

```text
Test empty/live-head migration, revision immutability, optimistic conflicts,
cross-workspace IDs, invalid cell/environment/output types, orphan objects and
concurrent revision creation. Define retention/deletion/hold behavior for drafts,
executions and large outputs plus idempotent cleanup/reconciliation intent.
Measure canonical notebook queries and record rollback before managed cells.
```

## Plan 4.2 — managed cell runtime

**Contract.** Managed cells are code-owned typed commands/queries. Initial types:
Markdown, DCLab query, profile, chart, build intent/status, evidence, decision and
agent objective. No arbitrary SQL, Python, shell, network or storage path.

### S4-P02A — managed cell registry and schemas

```text
Define a versioned code-owned registry for each managed cell with config/input/
output schema, required capability/data policy, allowed resource states, timeout,
page/byte/output bounds, cacheability and whether it is read, proposal or Scope 3
command. For query/profile cells, define a closed typed grammar containing only
registered operation/template version, immutable dataset/artifact binding,
approved columns, allowlisted comparison/group/filter operators, aggregate
enum, stable ordering and row/byte/time limits. Do not accept SQL, expressions,
paths, URLs, pragmas, DuckDB options or extension names. Reject unknown versions/
fields and arbitrary handler names. Add shared schema contract tests and a
disabled feature release.
```

### S4-P02B — Markdown/query/profile/evidence cells

```text
Implement deterministic Markdown rendering plus query/profile/evidence cells over
authorized application services. Bind explicit workspace/resource/version/digest,
use bounded pages and audience-safe projections, and produce typed outputs with
citations. Query/profile cells call only the DataScan query service; it resolves
authorization/digest, chooses a code-owned template and returns a normalized
bounded table/profile artifact. Never pass SQL or adapter settings through cell
config, expose DuckDB/Arrow objects, or silently fall back to an unbounded pandas
load. Record operation/template version, inputs, limits and result digest in
cell lineage. After Plan 3.0, render only normalized DCLab tracking/package/feature-
contract/monitoring evidence with DCLab citations; never render an MLflow URL or
type, Pandera schema/error, Evidently report object or backend locator. Sanitize
rendered content and never accept provider HTML/URLs. Test empty/denied/deleted/
quarantined/stale/degraded/two-workspace, digest mismatch, oversized result,
timeout/cancel, malicious typed filters and Markdown. Assert SQL/path/extension
payloads fail validation before job creation.
```

### S4-P02C — chart/decision cells

```text
Implement chart and decision cells using Visualization and decision query/
translation services. Chart specs are an allowlisted versioned grammar with data
references and row/series/point limits; rendering cannot execute JavaScript.
Decision outputs separate prediction/recommendation/evidence class and audience.
Store large results as artifacts. Add deterministic snapshot/schema and banned-
term/causal-language tests.
```

### S4-P02D — build-intent and agent-objective cells

```text
Implement build and agent cells as adapters to Scope 3 ModelBuildCommandService
and Scope 1/2 agent services. A cell creates or references durable intent/run
using idempotency, exact versions, current policy/approval and budget; it does
not execute inline. Return resource/status/event links. Test replay/conflict,
policy denial, cancel, stale revision and workspace switch.
```

### S4-P02E — managed runtime contract gate

```text
Run the shared registry suite across all cell types for authorization, schema,
state, output bounds, cancellation, timeout, idempotency, citations, redaction
and two workspaces. Prove no managed config can select SQL/code/URL/handler or
bypass command approvals. Add per-cell latency/error/output metrics, kill switch
and operator ownership before execution jobs.
```

## Plan 4.3 — APIs, execution jobs and exports

**Contract.** API edits immutable revisions with ETag; execution is a durable
job with one cell transition per bounded handler step. Export reproduces a pinned
revision/environment/input/output manifest.

### S4-P03A — notebook and revision application services

```text
Implement create/list/get/archive notebook and create/list/get revision services.
Revision creation accepts base ETag/version plus a complete bounded cell snapshot
or validated typed diff and computes canonical digest. Authorize workspace/project
and every input reference. Concurrent edits return conflict with current version;
published history is immutable. Add service tests for replay, stale base and
cross-tenant bindings.
```

### S4-P03B — execution command and scheduler

```text
Implement start execution service that validates revision/environment/input
digests, current authorization/data policy, cell registry versions, Scope 3
approval/budget and idempotency, then atomically creates execution/first cell job/
event. Scheduler advances ordered/dependency cells, checkpoints after each and
stops on configured failure policy. Payloads contain IDs only. Test transaction
failure and duplicate enqueue.
```

### S4-P03C — cancellation, retry and recovery

```text
Implement cooperative cancellation and cell/execution child retry without
editing earlier outputs. Reconciler handles lost leases, missing next job,
partial object upload and stale running state; unverifiable output is quarantined.
Retry binds parent attempt and exact revision/environment/input. Test cancel at
every checkpoint, duplicate job, restart, missing artifact and terminal races.
```

### S4-P03D — `/v1` notebook and execution resources

```text
Add typed `/v1/notebooks`, revisions, cells-as-revision-content, executions,
cell executions, outputs, events, cancel/retry and export-request endpoints with
Scope 0 errors/pages/request IDs, ETag and idempotency. Stream authorized outputs
through artifact service. Add OpenAPI and negative tests for body/size/order/
state/workspace/version errors. Routes do not run cells.
```

### S4-P03E — SDK and reproducible export

```text
Add Python client notebook/revision/execution models, iterators, wait/cancel/retry
and bounded output download. Implement export as a durable artifact containing
notebook/revision/environment/input/output manifests, cell sources/configs,
digests and audience-safe rendered form; omit credentials/signed URLs. Verify
import/read or reproduction contract as defined. Add digest and live API tests.
```

### S4-P03F — API/job/export completion gate

```text
Run migration, concurrency, duplicate, cancellation/recovery, two-workspace,
classification, artifact, API/SDK parity and reproducible-export tests. Restart
workers/API mid-execution and reconstruct exact state. Add queue/cell/execution/
output metrics, stuck-execution/object-reconciliation runbooks and feature flag.
Record evidence before agent revision proposals or UI.
```

## Plan 4.4 — agent notebook collaboration

**Contract.** Agents propose typed diffs against a base revision and explain
errors with citations. Humans or authorized Scope 3 commands accept; an agent
cannot silently rewrite the current revision or inject code.

### S4-P04A — notebook diff and proposal schema

```text
Define versioned operations add/remove/move/update-cell-config/body/input with
stable logical cell IDs, base revision/digest, expected result, risk and citations.
Restrict operations by cell registry and policy; Python/code cells are excluded
until Plan 4.6. Canonicalize and validate conflicts, order and maximum change size.
Add pure round-trip, equivalent and malicious patch tests.
```

### S4-P04B — proposal generation tools and context

```text
Register minimal read tools for notebook/revision/cell schema, executions/errors
and cited DCLab resources. Build bounded envelopes with explicit untrusted cell
content. Implement an agent task producing a typed diff or explanation, validate
against current base and persist AgentProposal/review events. No proposal is
applied. Test injection in Markdown/output and stale/deleted inputs.
```

### S4-P04C — review/apply service

```text
Implement review and authorized apply as creation of a new immutable revision.
Revalidate base/current ETag, cell schemas, input access, data policy, command
approval and resource versions at apply time. Material change after review
conflicts and requires new proposal/review. Persist reviewer, proposal/diff digest
and resulting revision. Test concurrent proposals and partial application denial.
```

### S4-P04D — error explanation and repair proposals

```text
Build audience-safe structured explanations from cell state, safe error category,
resource versions and runbook metadata; do not pass stack/provider/object bodies
to the LLM. Repair suggestions are typed diff or retry proposals with expected
effect and citations. Test infrastructure versus user/policy error, malicious
error text, insufficient evidence and repeated failed repair.
```

### S4-P04E — collaboration adversarial gate

```text
Run proposal/review/apply under concurrent human edit, workspace/policy change,
malicious cell/output, unknown patch, changed approval, deleted input and agent
retry loops. Assert immutable history, exact reviewed diff, no code enablement,
bounded revisions and valid citations. Add proposal/apply/conflict metrics,
kill switch and rollback through selecting prior revision, not row mutation.
```

## Plan 4.5 — notebook UI

**Contract.** Use existing AppShell/UI primitives and BFF. The browser edits
draft state but persists immutable revisions and renders only typed safe outputs.
Notebook UI is synchronized with the project Conversation, ML Workflow and
Implementation views through canonical IDs and deep links.

### S4-P05A — routes, hooks and editor state

```text
Create notebook list/new/detail routes, typed hooks and local editor state keyed
by workspace/notebook/base revision. Model loading/empty/offline/conflict/archived
states and bounded autosave only if it creates safe drafts. Use ETag and preserve
unsaved edits during a conflict without silently overwriting. Add component tests
for zero/one/many notebooks and workspace switch.
```

### S4-P05B — cell editor and managed execution

```text
Build ordered cell add/move/remove/config/body controls from the server cell
registry; unknown types render read-only unsupported state. Implement run all/
run selected only through execution API, with durable status/events/cancel/retry.
Prevent double submit and show active revision/environment/input versions. Add
keyboard reorder/focus and slow/error/reload tests.
```

### S4-P05C — output and provenance presentation

```text
Render allowlisted text/table/chart/resource/file outputs with size/pagination/
download bounds and safe escaping. Add provenance drawer showing cell/revision/
environment/input/output digests, command/agent/run links and citations. Never
render arbitrary HTML/script/provider URL or expose storage keys. Include links
to the producing lifecycle node and relevant project decisions; implementation
content remains inspectable when authorized. Test malicious content, missing/
quarantined output and revoked access.
```

### S4-P05D — agent diff/review experience

```text
Show agent proposal as typed cell-aware diff with base/current version, citations,
risk, policy/approval and validation. Provide accept/reject/request-revision only
from current server capability/state; refresh on ETag conflict. Show error
explanations separately from trusted facts. Add concurrent edit/review and
malicious label tests.
```

### S4-P05E — browser/accessibility gate

```text
Run component and Playwright journeys for create/edit/revise/execute/cancel/retry/
reload/export, conflict, agent proposal and workspace/policy revocation. Test
large cells/outputs, narrow viewport, keyboard/focus and automated accessibility.
Verify notebook, lifecycle and conversation routes display the same resource
versions and that cell order/kernel history cannot change project truth.
Scan DOM/network/storage for secrets/raw internals/cross-tenant content. Record
performance and keep Python controls absent until isolated runtime release.
```

## Plan 4.6 — isolated Python beta

**Contract.** Python executes in a disposable separate sandbox control/data
plane with immutable images, no credentials, no host/private network/open egress,
strict resources and a narrow artifact publisher. Disabled by default.

### S4-P06A — sandbox threat model and platform ADR

```text
Threat-model host escape, metadata/private network, cross-job files/processes,
secret/env leakage, fork bombs, disk/output exhaustion, dependency poisoning and
artifact smuggling. Include future model-generated investigation code and prove
that neither raw LangGraph nor Deep Agents runs inside the sandbox or reaches
its control interface from model-generated code. Evaluate deployment-native isolation options and choose one
with measurable CPU/memory/PID/disk/time/network controls, immutable image and
disposable workspace. Define trust boundary, operator owner, residual risk and
kill switch. Do not implement in the API container.
```

### S4-P06B — environment/image supply chain

```text
Build a minimal pinned runtime image through protected CI with lockfile, package
allowlist, non-root/read-only rootfs, no package manager/credential tooling where
practical, SBOM, vulnerability scan, signature and provenance. EnvironmentVersion
references image digest and policy. Reject user-selected image/package/network.
Add reproducible build and signature verification tests before scheduling.
```

### S4-P06C — sandbox control-plane service

```text
Implement a narrow service that accepts execution/session ID, runtime epoch,
signed short-lived input manifest and resource/network/output policy. Create one
isolated sandbox per interactive epoch, reuse it only for authorized cells in the
same owner/notebook/revision epoch, and destroy it on stop, expiry, revocation or
failure. Fresh verification, training and batch attempts remain disposable.
Stream bounded status and capture exit/resource use. Use workload identity only
for the control plane, never inside user code. Support cancel/deadline, heartbeat,
lease and idempotent provider reconciliation; an uncertain result must not
automatically replay user code. Do not expose it to Investigation Copilot in this
scope; define a future capability boundary rather than a generic execute tool.
Add fake-backend contract plus staging-backend tests.
```

### S4-P06D — input staging and no-egress execution

```text
Materialize only authorized immutable input artifacts into a read-only per-job
directory after digest/size/type validation. Run code as unprivileged UID with
no host mounts, secrets, service token, DNS or network; enforce CPU/memory/PID/
disk/file/output/wall limits. Capture stdout/stderr as untrusted bounded output.
Test missing/changed input, cancellation and concurrent sandbox isolation.
```

### S4-P06E — artifact publisher and notebook integration

```text
Accept outputs only through a post-execution publisher that walks a fixed output
directory without symlink/path/device escape, validates count/size/MIME/archive,
scans/quarantines, hashes and writes via artifact service. Bind objects to cell
execution/environment/input/code digests. Register Python cell schema/handler
behind workspace allowlist and exact approval/resource quota. No arbitrary URL.
```

### S4-P06F — isolated beta operational gate

```text
Deploy the actual sandbox backend in staging; verify identity/network policies,
image signature, quotas, cleanup and total disable. Run benign reproducibility,
cancel/restart, concurrent tenant and resource exhaustion cases. Add sandbox
queue/resource/kill/cleanup metrics, alerts and incident runbooks. Do not call it
isolated or expose Python UI until Plan 4.7 attack campaign passes.
```

## Plan 4.7 — sandbox proof and release

**Contract.** Verification targets the real staging boundary, not mocks alone.
Any host/private network/secret/cross-tenant/resource-control escape blocks Python.

### S4-P07A — escape and privilege campaign

```text
Attempt filesystem traversal/symlink/device/proc access, container/runtime socket,
privilege escalation, namespace escape, ptrace, cross-process/job reads, unsafe
syscalls and persistence after termination against the selected platform. Verify
non-root/read-only/no-host-mount/seccomp-or-platform controls and cleanup. Use
approved safe test payloads and record platform/image/policy digests.
```

### S4-P07B — egress and metadata campaign

```text
Attempt DNS, IPv4/IPv6, unexpected loopback services, link-local, cloud metadata,
private ranges, redirects, proxy/env, unapproved Unix sockets and straightforward
covert egress. Allow only the exact local Jupyter/kernel IPC and authenticated,
attempt-scoped control/transfer channel required by the reviewed runtime design.
Verify every other path is denied and no credentials/tokens appear in environment,
files or process metadata. Test that code cannot forge control-plane callbacks or
reuse another attempt's channel. Any connection outside the allowlist or any secret
observation blocks release.
```

### S4-P07C — exhaustion and artifact-smuggling campaign

```text
Attempt CPU/memory/PID/fork/thread, disk/inode/file-count, stdout/stderr, wall-
time and decompression exhaustion plus symlink/hardlink/path/archive/polyglot/
MIME output smuggling. Assert bounded termination, fair capacity, quarantined
outputs and complete cleanup without harming other tenants. Measure enforcement
latency and residual artifacts.
```

### S4-P07D — reproducibility and failure recovery

```text
Run identical code/environment/input twice and compare output manifests/digests
where determinism is expected; record declared nondeterminism otherwise. Kill
control plane/sandbox/object store at each checkpoint, then reconcile exact
terminal state without duplicate publish. Test cancel, timeout, lost callback,
node loss and image retirement. Execute restore/runbooks.
```

### S4-P07E — production-MVP Python release gate

```text
Run full regressions plus real attack campaigns under independent security
review. Publish isolation evidence, residual risks, platform limits, capacity/
cost and incident owner. If passed, enable one signed image and bounded cell type
for a tiny allowlist/canary with real-time kill switch and no egress. Otherwise
keep Python disabled and block the production-MVP go/no-go while managed notebook
remains available for remediation/testing. Passing this gate does not
automatically give code execution to an agent runtime; each runtime/purpose/tool
requires a separate policy release, smaller-or-equal budgets and an independent
kill switch. Prove the deterministic ML path and managed notebook remain healthy
when isolated Python is globally disabled.
```

## Plan 4.8 — NOOA notebook collaborator

**Contract.** Add a NOOA-powered notebook collaborator only when S2-P14H records
`SHADOW_ALLOWED` for this exact purpose and S4-P07E has released the required
isolation profile. It reads bounded DCLab notebook/lifecycle/evidence views and
returns a typed revision proposal. It never connects to Jupyter, executes a cell,
applies a patch, approves a command or owns notebook state. DCLab review/apply and
execution services remain authoritative. If NOOA is disabled/rejected, record
this plan not applicable; Scope 4 remains releasable. Read
`docs/agentic-program/JEV_NOOA_INTEGRATION_ARCHITECTURE.md` before every prompt.

Use focused test homes `apps/api/tests/test_nooa_notebook_contract.py`,
`test_nooa_notebook_context.py`, `test_nooa_notebook_proposal_service.py`,
`test_nooa_notebook_execution_handoff.py`,
`apps/web/e2e/nooa-notebook-collaboration.spec.ts` and
`test_nooa_notebook_release_gate.py`; extend existing notebook proposal owners
instead of duplicating routes, tables or UI.

### S4-P08A — collaborator contract and agent-class release

```text
Inventory S4-P04 proposal/diff/review/apply contracts, S2-P14 agent release/tool
facade and RT5 controlled execution work. Define NotebookReviewAgent as an
immutable S2-P14 agent-class release with exact purpose, base notebook/revision/
ETag, lifecycle bindings, objective, environment/input digests and typed
NotebookRevisionProposal output. Allow only registry-valid cell insert/update/
delete/move operations, bounded code/config/Markdown bodies, expected effect,
risk and citations; prohibit binary output, SQL, credentials, paths, provider
locators, execution claims and direct changes to canonical lifecycle state.
Choose Predict or CodeAct from measured need and inherit smaller-or-equal NOOA
limits. Add schema/golden-digest, stale-base and unsupported-operation tests plus
an ADR addendum proving the collaborator is a whole-run worker purpose, not a
notebook kernel agent. Do not create tables, routes or execute NOOA in this prompt.
```

### S4-P08B — bounded context and Jev advisory tools

```text
Implement notebook-specific read methods through the S2-P14 private DCLab facade:
read_notebook_revision, read_bound_lifecycle, read_cell_result_summary and
read_authorized_evidence. Return exact IDs/versions/digests, bounded output
summaries and citation handles; never return Jupyter tokens, kernel messages,
object paths, signed URLs, database clients, full unrestricted artifacts or
another user's draft. If S1-P12F allowed a named notebook-review release, expose
request_semantic_review only for its code-owned questions and treat abstention/
confidence as advisory. The agent cannot choose question/model/threshold or send
raw cell outputs outside their data policy. Re-authorize every call and record
ToolCall/Citation/SemanticDecision lineage. Test output/prompt injection,
malicious notebook content, oversize/truncation, stale/deleted outputs,
cross-workspace/resource substitution, revoked membership and operation with Jev
absent or disabled.
```

### S4-P08C — proposal persistence, review and apply integration

```text
Reuse S4-P04 NotebookRevisionProposal persistence and service; add only the
runtime/agent/semantic-decision/citation references it cannot currently express.
Do not create a NOOA proposal table. No migration is expected unless the current
generic proposal lacks an immutable source-run reference; if required, discover
the live head and add one nullable expand-phase composite tenant/run FK with
index and compatibility tests. Validate the NOOA result into the same canonical
patch format, persist it before presentation and mark it proposed/rejected/
accepted/superseded through existing transitions. Apply rechecks current base
ETag, membership/capability, policy, input/environment/cell registry releases,
citations, budgets and exact approval, then creates a new immutable revision.
Acceptance does not execute it. Test replay/conflict, concurrent human edit,
stale evidence/policy, rejected/superseded proposal, forged source run,
cross-tenant lineage and clean rollback with NOOA disabled.
Reuse the generic notebook proposal transport; if S4-P04 did not already expose
it, add `GET /v1/notebooks/{notebook_id}/proposals`, `GET .../proposals/{id}`,
`POST .../proposals/{id}/reviews` and `POST .../proposals/{id}/apply` rather than
NOOA-specific routes. Review binds decision/comment/proposal digest/base ETag;
apply requires `If-Match` and idempotency and returns the new immutable revision,
not an execution. Define 200/201 success and the common 401/403/404/409/413/422/
429 errors: stale base/digest/idempotency conflict is 409, bounds are 413 and an
invalid operation/state is 422. Add OpenAPI, HTTP-client and API/service parity
tests. Keep execution on the existing notebook execution endpoint.
```

### S4-P08D — controlled execution handoff and recovery

```text
Connect an accepted NOOA-authored revision to the existing notebook execution
command only through an explicit user action and current approval policy. The
command creates the ordinary ExecutionRequest/MlJob/runtime session or attempt;
the NOOA worker never receives sandbox/Jupyter credentials and never waits on or
controls the kernel. Bind source agent run/proposal/revision, exact image/input/
environment and approval/idempotency digests into lineage. Revalidate all
authority at dispatch, and apply the existing serialize/cancel/reconcile rules;
never automatically replay a cell after an ambiguous result. Execution failure
may produce a new error-explanation proposal but cannot mutate/retry the accepted
revision silently. Test duplicate execute clicks, approval revocation, policy/
image retirement, cancel race, runtime outage, ambiguous completion, worker loss,
Jev/NOOA kill during execution and evidence reconstruction from canonical rows.
```

### S4-P08E — collaborative notebook experience

```text
Extend existing notebook proposal/review UI and hooks rather than creating a
second editor. Show the exact base/current revision, cell-level diff, source
NOOA agent/runtime release, citations, advisory Jev result where authorized,
expected effect, risk, validation/approval state and stale/conflict status.
Provide accept, reject and request-revision actions; execution remains a separate
clearly labeled action after acceptance. Hide internal prompts, Python locals,
generated CodeAct transcript, provider data and credentials. Key query state by
workspace/notebook/revision, cancel on workspace switch and prevent old-tenant
proposal flashes. Add loading/empty/partial/denied/expired/cancelled/failure states,
keyboard diff navigation, screen-reader announcements and component/Playwright
tests for proposal review, concurrent edit, rejection, acceptance without run,
explicit run, reconnect and workspace switch.
```

### S4-P08F — adversarial and release gate

```text
Run S2-P14H plus Scope 4 proposal, browser and real sandbox campaigns for the
notebook purpose. Attack malicious notebook instructions/outputs, hidden cells,
oversized patches, path/SQL/credential/provider injection, forged citations,
stale revision/approval, cross-tenant IDs, direct Jupyter/control-channel access,
automatic execution and result-based retry loops. Hard-fail if NOOA/Jev can
approve, apply or execute, if the worker receives a kernel/provider/product
credential, if deterministic validation is bypassed, or if disabling NOOA harms
manual notebooks. Measure valid-proposal and reviewer-acceptance rates, citation
precision, correction cycles, latency/cost and execution outcomes against the
existing S4-P04 baseline. Exercise purpose/global kill switches, worker/image
rollback, deletion and incident runbooks. Record SHADOW_ALLOWED for an allowlist,
DISABLED_PENDING_EVIDENCE or REJECTED with exact releases/limits and known risks.
No result changes S4-P07E or grants broader agent code-execution authority.
```
