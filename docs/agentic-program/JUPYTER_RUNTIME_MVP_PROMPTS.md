# Runtime MVP — ordered, bounded coding-agent work packages

Status: PROPOSED work orders, not completed implementation.
Prepared: 2026-09-15. Contract: [runtime blueprint](JUPYTER_RUNTIME_MVP_BLUEPRINT.md).
Cloud decision confirmed by the user: complete DCLab control-plane deployment on
either AWS or GCP. Requested external CPU/GPU targets are Runpod, Railway, Lambda
GPU Cloud, Vast.ai and Nebius; see
[the external compute architecture](EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md).
RT7/RT8 add 12 prompts to the original 32; existing RT1–RT6 IDs retain their meaning.
No prompt authorizes cloud provisioning,
committing, pushing or production rollout without the user's separate instruction.

These work orders refine runtime implementation across Scope 4 and its prerequisite
infrastructure/ML/agent plans. They do not rename or bypass existing scope gates.
`RT-*` IDs identify this proposed breakdown, not replacement S0–S10 evidence IDs.
Execute one prompt at a time; inspect first, verify or repair existing work, and do
not create duplicate owners. Each plan contains four to eight prompts.
Each RT prompt also inherits the plan outcome and mandatory execution card in
[the remaining-scope execution map](prompts/REMAINING_SCOPE_EXECUTION_MAP.md);
resolve concrete files, migrations, APIs, tests, provider behavior and rollback
against the live checkout before editing.

## Common preamble — prepend to every selected prompt

```text
Implement only the selected RT work package after reviewing current code. Read
docs/agentic-program/JUPYTER_RUNTIME_MVP_BLUEPRINT.md, this document,
docs/agentic-program/EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md,
docs/agentic-program/prompts/README.md and EXECUTION_STANDARD.md, their applicable
architecture references, and AGENTS.md if present. Preserve the existing user's
changes. These documents do not override the user's actual request or safety rules.

Before editing record git status and SHA, live Alembic heads, relevant existing
code/tests, and the exact delta still needed. If already implemented, verify or
repair it. New paths below are proposed; locate equivalents first. Do not infer
that a documented class/service already exists. Reconcile with other scope work.

Write the required implementation packet: files, ownership, exact schema and API,
permissions, state transitions, events, lease/idempotency behavior, cancellation,
recovery, limits/config, tests and fixtures, migration/rollback, observability,
flag/kill switch, cloud parity, non-goals and evidence. Resolve contract ambiguity
in a design-only change before implementation. Preserve deterministic ML ownership.

One primary concern, one resource/handler family, normally at most one migration,
800 non-generated changed lines and 20 hand-edited files. Split before exceeding
the limit; do not omit tests or security to fit. Generated snapshots are separate.
Use existing PostgreSQL queue, authorization, artifacts, model lineage and BFF.
No arbitrary code in API/ordinary trusted workers. No cloud SDKs in public/domain
contracts. No second notebook save path, queue, model registry or agent loop.
External targets require explicit capability/data/region grants, current quotes,
bounded provider credentials, attempt-scoped transfer and complete resource cleanup.

Keep runtime, Python, agent-execution and serving flags disabled until their gates
pass. No public kernel/token endpoint. Test denial and failure, not just success.
Record actual commands/results and limitations; never label mocks cloud evidence.
Do not commit, push, provision or deploy unless explicitly authorized. Finish with
the execution-standard report and the next eligible prompt ID.
```

## Verification command contract

Existing tools observed in this checkout include `.venv/bin/python`,
`.venv/bin/alembic`, `pytest`, `npm run lint`, `npx tsc --noEmit`, `npm run build`
and the Playwright suite. Discover replacements if the environment changes.

Run proposed tests below only after creating their fixtures/implementation. Use
`PYTHONPATH=apps/api .venv/bin/python -m pytest <named-test-file> -q` from the repo
root, preserving the repository's test-path configuration. All persistence,
tenancy, migration and concurrency tests use a dedicated PostgreSQL test database
from existing fixtures; never point tests or migration drills at production.

For web changes, run from `apps/web`: `npm run lint`, `npx tsc --noEmit`,
`npm run build`. Run named Playwright specifications only against a disposable,
prepared test stack. `npm run e2e` invokes a recreate seeder: review its target and
configuration first; it is not an innocuous read-only command.

After reviewed schema/API/source changes:

```text
.venv/bin/alembic heads
.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent
.venv/bin/python -m scripts.check_truth_drift --alembic-check
git diff --check
```

Expected: one head, identical consecutive generation, no unexplained contract/doc
drift and no whitespace errors. Alembic check requires the disposable database at
the tested head. Generated changes must be inspected, not accepted blindly.
Infrastructure conformance is a separately authorized staging workflow, not a
network/cloud prerequisite for ordinary pull-request CI.

## Plan RT1 — freeze ownership and persist notebook content

Dependencies: current repository truth and session/authorization foundations.
All four prompts are initially design/database only; no executable Python route.

### RT1-A — reconcile stateful Jupyter with the existing program

```text
Inspect SCOPE_04_AGENTIC_NOTEBOOK.md, AWS_GCP_DEPLOYMENT_ARCHITECTURE.md,
AGENT_FIRST_MVP_ARCHITECTURE.md, ML_PLATFORM_INTEGRATION_ARCHITECTURE.md,
MASTER_SCOPE_0_TO_10_PLAN.md, domain/execution_requests.py, domain/ml_jobs.py,
services/model_build_notebook.py and the BFF. Create one runtime ADR under
docs/adr/ using the repository convention if present. Resolve stateful session
versus disposable verification run; Jupyter Server first and Gateway deferred;
the three compute lanes; one-provider deployment; owner-only sessions; lost-RAM
recovery; and the no-auto-replay rule. Map RT prompts to existing Scope 4 plans
and prerequisites without renumbering evidence. Identify infrastructure that must
precede public Python. Record exact source files and existing/missing capabilities.
Update only directly affected planning references after checking their dirty diff.
No schema, dependency, cloud resource or product behavior change. Validate links
and truth tooling; this prompt closes decisions, not a production capability.
```

### RT1-B — immutable notebook revisions and cell identity

```text
Inspect db/models.py, db/integrity.py, artifact_service.py, the revision/lineage
patterns and PostgreSQL fixtures. Implement notebook, notebook_revision and
notebook_cell contracts from blueprint section 5, first in domain/notebook.py and
then current SQLAlchemy metadata. Store source bodies via existing Artifact;
distinguish stable logical_cell_id from immutable revision-cell row ID. Enforce
workspace/project/notebook lineage, unique revision numbers/positions/logical IDs,
nonnegative positions, source byte bounds and immutable published revisions.
Handle creation/current-revision cyclic references with an explicit transaction
and FK strategy. Add one additive migration discovered from the live head.
Create tests/test_notebook_revisions.py: concurrent appends, cross-tenant/project
FKs, forbidden updates/deletes, digest mismatch and rollback of failed save.
Run it plus existing artifact/lineage tests. No execution, runtime provisioning,
frontend or arbitrary notebook import. Feature remains disabled.
```

### RT1-C — approved environments and immutable input bindings

```text
Reuse RuntimeEnvironment fingerprints and storage/base.py; do not repurpose
pipeline-bound CodeSnapshot. Implement notebook_environment_versions and
notebook_input_bindings with explicit composite references, revocation, purpose,
source digest and path-safe binding names. Environment approval records a pinned
image/lock/profile contract; users cannot supply runtime images or package lists.
Define staged-data authorization through existing data-access services. A changed
input/environment invalidates the pending execution approval and requires a new
epoch. Add at most one migration. Create tests/test_notebook_bindings.py for
cross-workspace/project references, revoked access/environment, protected holdout,
absolute/traversal names, immutable binding versions and unsupported image tags.
No network download or cloud credentials in the runtime; staging implementation
comes later. Document rollback as disabling new bindings, not deleting history.
```

### RT1-D — save/read/export services and migration closure

```text
Implement services/notebook_service.py for atomic save, optimistic concurrency,
authorized reads and revision export. Reuse model_build_notebook.py conventions
where applicable without changing its reproduction output. Export the selected
revision with bounded authorized saved outputs; no private URLs, credentials or
implicit live-kernel contents. Implement service contracts first; public routes
belong to RT4. Create tests/test_notebook_content_service.py and
test_notebook_migrations.py. Prove empty/previous-head upgrade, model/catalog
agreement, stale-write rejection, permission denial, orphan-artifact cleanup
after failed transaction and deterministic export. Run original reproduction
artifact tests as regression. Refresh generated facts using their sole generator.
No Python execution or infrastructure. Retain the prior application compatibility
window and document forward repair for any persisted notebook revisions.
```

## Plan RT2 — sessions, quotas and durable execution state

Dependencies: RT1. Proposed APIs are not enabled by these prompts.
Reconcile RT7-A before finalizing these schemas; RT7 extends these owners rather
than introducing another runtime/job/reservation subsystem.

### RT2-A — resource profiles, sessions and epochs

```text
Implement domain/runtime.py, runtime profile/session/epoch models and
services/runtime_session_service.py. Apply blueprint states, immutable environment
per epoch, owner-only session reuse, partial uniqueness for active sessions,
optimistic version checks, absolute/idle expiry and indexed reconciliation.
Keep runtime-private provider bindings inaccessible to public serializers.
Do not put Jupyter tokens in auth_sessions, job payloads or general metadata.
One migration maximum; split profile/session ownership if needed. Create
tests/test_runtime_sessions.py for simultaneous start, epoch mismatch, owner
isolation, expired/revoked inputs, transitions and DB FK rejection. Test safe
public projections. Use a fake runtime port; no cluster or real user code yet.
Flag disabled; stopping/cleanup contracts remain callable when admission is off.
```

### RT2-B — admission reservations and bounded spending

```text
Inspect existing and recently implemented budget/entitlement services first.
Reuse a compatible ledger; otherwise add blueprint runtime quota/reservation
tables and services/runtime_quota_service.py. Lock the workspace admission account
when reserving sessions/CPU/memory/budget; enforce deployment and user caps without
racy count-then-insert. Unique reservation ties a resource to one settlement.
Do not release capacity merely because a cloud lookup timed out. Record effective
provider sizing and estimated, not fictitiously exact, spend. Create
tests/test_runtime_quotas.py with concurrent admission, duplicate settlement,
expired reservation reconciliation, provider-unavailable cleanup and revocation.
Add rejection/settlement-lag metrics and quota-leak runbook. No paid billing system,
automatic cloud budget purchase, GPU reservation or new queue.
```

### RT2-C — notebook/cell execution and event persistence

```text
Implement notebook_executions, cell_executions, notebook_outputs and bounded
notebook_execution_events, splitting migration PRs if necessary. Reuse
ExecutionRequest for notebook_execute; review its closed operation constraint and
request-spec allowlist explicitly. New MlJob handler slugs do not require a new
enum migration. Atomically record intent and dispatch work using existing queue
services. Source/data enter by revision/artifact IDs. Persist stable message IDs,
epoch/sequence/fence, in-flight partial uniqueness, exact cell/revision lineage,
event byte checks and output publication state. Create
tests/test_notebook_execution_state.py for same-key replay, conflicting payload,
parallel dispatch, lost/cancelled outcomes and event sequencing. No automatic
cell retries. Map terminal domain outcomes onto existing intent/job statuses
without silently adding unsupported values. No live kernel connection yet.
```

### RT2-D — lease fencing, reconciliation and recovery fixtures

```text
Implement services/runtime_reconciliation_service.py and runtime handler family
in services/job_handlers.py using existing worker claims/heartbeat conventions.
Separate retryable provisioning reconciliation from non-retryable ambiguous code
dispatch. Fake provider fixtures must simulate create-then-timeout, duplicate
name/wrong UID, dead coordinator, old fenced socket, DB outage, stuck deletion
and expiry. Adopt only proven matching resources. After uncertain ownership,
quarantine the epoch and prove old sandbox termination before replacement.
Create tests/test_runtime_reconciliation.py; assert no duplicate execution intent,
no early quota release and no silent lost-to-success transition. Add bounded
orphan/lease alerts and runbooks. No new queue, indefinite retry, cloud SDK in the
service contract or claim that a DB fencing token alone stops a stale WebSocket.
```

## Plan RT3 — isolated compute and Jupyter integration

Dependencies: RT1–RT2 for integration. Cloud module design can be reviewed earlier;
live cloud actions need explicit authorization. Six prompts keep security separate.
RT3-B is the home-cloud Kubernetes adapter, not a requirement to emulate Kubernetes
on external providers. RT7/RT8 add private provisioners under the same runtime ports.

### RT3-A — locked runtime image and local isolation harness

```text
Add runtime/jupyter/Dockerfile, a separately locked runtime dependency definition,
entrypoint/config and an isolated integration harness under infra/tests/runtime/.
Do not install Jupyter into the production API image. Pin tested Python 3.12,
Jupyter Server/ipykernel and approved analysis packages by a reproducible lock;
record SBOM, scan result and immutable image digest. No user pip, terminal,
privileged sidecar, host mount, Docker socket, service-account/cloud/product token
or agent framework. Configure non-root, read-only root, bounded scratch, process
limits, request/output limits and fail-closed authentication. Local Jupyter
loopback remains permitted. Create tests/test_runtime_image_contract.py and a
real private-kernel smoke harness. Test effective controls, not just YAML strings.
Local Docker smoke is development evidence, not AWS/GCP isolation certification.
```

### RT3-B — common Kubernetes runtime adapter and admission policy

```text
Implement infrastructure/runtime/kubernetes.py behind SandboxRuntimePort and
infra/kubernetes/runtime/base/. The module prefix is apps/api/app/. Use only
code-owned pod templates, private endpoints, immutable images, resource labels,
UID/generation checks, non-restarting session pods and trusted coordinator RBAC.
The kernel service account has no Kubernetes API token. Enforce start/inspect/
terminate idempotency, namespace/profile allowlists and finite cleanup deadlines.
API/domain objects contain no provider objects. Add tests/test_runtime_kubernetes.py
with synthetic cluster responses, malicious caller specs and create-then-timeout.
Add manifest policy tests rejecting unsafe substitutions. If an isolation runtime
is missing, refuse creation; never fall back to an ordinary shared-host pod.
No full cluster provisioning in this prompt. Validate AWS/GCP overlay requirements
without pretending their network controls are identical.
```

### RT3-C — Jupyter lifecycle and execution protocol adapter

```text
Implement infrastructure/runtime/jupyter.py behind KernelProtocolPort. Inspect
official REST/WebSocket protocol docs for the pinned versions. Use private kernel
lifecycle endpoints and connect channels before sending saved execute_request.
Correlate parent IDs; disable stdin/custom comms and unsupported active output.
Handle stream/error/display/clear-output/update-display within explicit bounds;
mark unsupported MIME rather than executing browser code. Success requires the
matching reply and idle, with a documented bounded policy for late output.
No transparent resend after an uncertain send. Interrupt timeout escalates to
sandbox termination through its owner. Create tests/test_jupyter_protocol.py
with recorded synthetic messages for channel reordering, duplicates, malformed
binary/JSON, oversized output and late messages. Add real-kernel integration:
x=41 then x+1, exception, plot, interrupt, disconnect and explicit restart.
```

### RT3-D — authorized input transfer and output publication

```text
Implement services/notebook_artifact_service.py and a private transfer adapter.
Reuse artifact_service.py and storage/base.py/S3/GCS adapters. Define immutable
object-version/generation handling without exposing provider SDK types. Trusted
transfer workers use their own credentials outside the kernel boundary; copy
only authorized digest-verified input and collect bounded explicit exports.
No broad signed URL, cloud credential or product API token enters the kernel.
Quarantine outputs, independently validate bytes/type/digest and commit Artifact
references; reconcile upload-without-DB-commit and missing objects. Create
tests/test_notebook_artifact_transfer.py for both provider adapter contracts,
permission revocation, holdout denial, mutable source mismatch, symlink/traversal,
archive bomb, oversized PNG and failed publication. No kernel-generated model
package loads in trusted API/transfer processes. Test cleanup and retention.
```

### RT3-E — AWS substrate and bounded staging conformance

```text
Design/implement infra/tofu/aws/ and infra/kubernetes/runtime/overlays/aws/ in
bounded PRs, reusing any cloud modules already created by Scope 9. Use managed
trusted nodes and an approved CPU Fargate isolation profile. Define private
subnets/endpoints, exact pod security groups/routes, least-privilege identities,
S3/ECR boundaries, quotas and cleanup. Validate Fargate constraints: no GPU,
DaemonSet-dependent security or assumed Kubernetes NetworkPolicy enforcement.
Create infra/tests/runtime/aws_conformance.py with authorized, narrowly scoped
probes for cross-tenant, metadata, product DB, cluster API, public/DNS/IPv6 egress,
resource exhaustion, fake pod UID and cleanup failure. Plan/validate locally;
apply only with explicit cloud/account/region/budget authorization. Record real
effective controls and image/SHA; missing process/network isolation blocks READY.
No relaxed fallback to make a test pass. Document scoped teardown and costs.
```

### RT3-F — GCP substrate and equal conformance

```text
Implement infra/tofu/gcp/ and infra/kubernetes/runtime/overlays/gcp/, reusing Scope 9
owners. Use GKE Standard with an approved gVisor sandbox profile for untrusted CPU
code, private services, Workload Identity only for trusted components and separate
GCS/Artifact Registry permissions. No workload identity credentials in kernels.
Run the same behavior contract as AWS through infra/tests/runtime/gcp_conformance.py;
include effective runtime-class verification and missing-sandbox refusal. Validate
the same egress, tenant, quota, exhaustion, revocation and recovery attacks.
Actual cluster versions/features must be recorded, not inferred from documentation.
Do not enable GPU simply because a GKE version supports it. Cloud apply needs
explicit authorization. Compare outputs with AWS using provider-neutral results;
record untested/blocked gates honestly. No production enablement in this prompt.
```

## Plan RT4 — manual notebook APIs, SDK and frontend

Dependencies: RT1–RT3 behavior. API/UI development may use protocol fixtures while
flags remain off; public Python additionally requires real provider security gates.

### RT4-A — public notebook content API and SDK contracts

```text
Implement api/v1_notebooks.py content/revision/export endpoints from blueprint
section 6, mounted in main.py. Reuse deps.py, shared /v1 errors/pages, resource
authorization and notebook_service.py. Enforce technical data visibility, bounded
source/cell limits, Idempotency-Key, If-Match, 428/412/409/413/422 distinctions and
inaccessible-resource 404 behavior. Reconcile any missing shared error convention
as a dependency; no parallel error format. Add typed resource methods under
packages/dclab_client/dclab_client/ using existing HTTP transport. Create
tests/test_notebook_api.py and packages/dclab_client/tests/test_notebooks.py.
Test exact requests/responses, tenant denial, source bounds, lost response replay,
stale saves and safe export. Regenerate OpenAPI/truth artifacts intentionally.
No live execution endpoints or notebook UI in this content-contract PR.
```

### RT4-B — session and execution command APIs

```text
Implement api/v1_runtimes.py and notebook execution commands against existing
session/execution services. Apply blueprint start/get/restart/stop/run/cancel
schemas, expected-epoch/version checks and asynchronous 202 responses. ML-write
eligibility is necessary but not sufficient: require runtime capability, current
dataset access, owner, quota and flags. Legacy client capability bypass must not
grant Python. Restart requires explicit loss-of-state intent; stop remains usable
under kill switch. Create tests/test_runtime_api.py and SDK runtime tests for
disabled substrate, unauthorized actor, conflicting key/epoch, simultaneous run,
quota 429, terminal cancel replay and redacted provider errors. No raw Jupyter URLs,
tokens, pod specs or caller-selected source overriding the persisted revision.
Split route/SDK PRs if the common review budget is exceeded.
```

### RT4-C — durable SSE and safe BFF transport

```text
Implement bounded execution events and resumable stream endpoint. Stream committed
events with sequence IDs, heartbeat, cursor expiry 410, duplicate tolerance and
periodic authorization. Reuse bff-proxy.ts and the existing catch-all route;
deliberately allow Idempotency-Key/If-Match/Last-Event-ID request headers and
ETag/Retry-After response headers. Propagate browser disconnect to the upstream
fetch. Disable intermediary buffering for the exact stream route and prove it in
the test ingress. Do not hold DB transactions/connections for stream lifetimes.
Create tests/test_notebook_event_stream.py and extend test_browser_bff.py for
revocation, slow clients, truncated output, expired cursor and header safety.
No browser bearer or token query string. No claim that an HTTP route is a
WebSocket proxy. Storage/stream backpressure and cleanup must stay bounded.
```

### RT4-D — notebook editing and runtime interaction UI

```text
Implement apps/web/app/app/notebooks/[notebookId]/page.tsx and focused components
under apps/web/app/components/notebooks/, reusing ProductPrimitives.tsx,
query-provider.tsx, session-provider.tsx and api-client.ts. Render ordered cells,
save/conflict state, approved environment/profile selector, runtime start/stop,
run/run-all/interrupt/restart, duration and resource state. Use saved revision IDs;
never silently execute unsaved edits. Warn before destructive RAM loss and show
out-of-order/dirty epoch state. Support keyboard navigation, visible focus,
labelled controls, screen-reader status and reduced motion. Editor dependency, if
needed, requires a bounded version/bundle/accessibility review. Add
apps/web/e2e/notebook-editing.spec.ts for save conflict, denied user, runtime expiry,
keyboard-only execution and safe restart. Preserve the existing visual system;
this is not a dashboard redesign or simultaneous collaborative editor.
```

### RT4-E — output rendering and reconnect experience

```text
Build NotebookOutput.tsx, RuntimeStatus.tsx and execution event hooks beside RT4-D
components, or reuse equivalents. Render only approved escaped text, bounded
tables and safe PNG; show unsupported MIME, truncation and publication failure.
Never render active HTML/SVG/JavaScript or load arbitrary remote media. Avoid
unbounded DOM growth; limit visible logs with a saved-artifact affordance.
Reconnect from event cursor, deduplicate, handle 410 with snapshot refresh and
show lost/uncertain outcome without rerun. Create apps/web/e2e/notebook-runtime.spec.ts
for x=41/x+1, plot/error output, reload without replay, slow network, malicious
HTML, large output and membership revocation. Run lint/type/build and these browser
checks against a dedicated test stack. No agent execution or new model promotion.
```

### RT4-F — close the manual runtime gate

```text
Run content, persistence, protocol, SDK, web and real-kernel tests plus both claimed
provider conformance suites. Recheck auth/BFF, queue and model-reproduction
regressions. Demonstrate one complete user flow from notebook creation to cell
state sharing, authorized data/plot, reconnect, stop and verified resource cleanup.
Kill coordinator/kernel and prove no implicit replay, no leaked capacity and
honest lost states. Record commands/durations, SHA/image/policy digests, warnings,
operator owners and limitations in docs/verification/RT_MANUAL_RUNTIME_GATE.md.
Do not enable public Python if any isolation or deployment claim lacks evidence.
Repair only defects in this manual runtime slice. Any rollout is a separately
approved allowlist change, independently reversible without dropping tables.
```

## Plan RT5 — agent proposals and controlled execution

Dependencies: manual runtime gate and the existing Scope 2/3 ToolRunner, command,
approval, budget and agent-runtime gates. Four prompts; no additional agent loop.

### RT5-A — notebook proposal and approval contract

```text
Inspect the actually implemented agent/command/approval owners from Scope 2/3.
Add typed notebook edit/run proposals that bind base/new revision, cell digests,
input bindings, environment/profile and resource budget. Reuse the approved
command ledger, not a notebook-specific approval database. Approval changes
neither canonical scientific evidence nor kernel state. Add
tests/test_notebook_agent_approval.py for stale diff, changed input/environment,
expired approval, revoked membership and cross-project references. User edits
invalidate pending approval; rejected proposals remain inspectable but inert.
No provider tool directly calls Jupyter/Kubernetes, and no execution yet. Flag
DCLAB_NOTEBOOK_AGENT_EXECUTION_ENABLED remains false. If prerequisites do not
exist, finish the contract and report the dependency instead of inventing them.
```

### RT5-B — ToolRunner notebook commands and bounded recovery

```text
Add approved notebook edit/run tools to the existing code-owned registry and
ToolRunner path. Reauthorize each invocation and call the same services as human
APIs. Persist agent-run/approval/request/revision/execution references and settle
budgets through the existing ledger. A lost cell outcome cannot trigger automatic
rerun or an unbounded repair loop. Tool result is typed, size-bounded and carries
untrusted output/provenance markers. Create tests/test_notebook_agent_tools.py
for repeated tool requests, stale approval, fake resource IDs, budget exhaustion,
cancellation and uncertain outcome. Preserve one runtime per AgentRun and the
existing deterministic services. No model-deployment, secret, shell, unrestricted
HTTP or holdout-evaluation authority is added by this notebook tool family.
```

### RT5-C — cell diff review and agent-result presentation

```text
Extend notebook UI with proposed-versus-current cell diff, stated inputs/resource
limits, approve/reject controls and linked execution history. Distinguish
explanation/proposal from actual execution and exploratory output from verified
evidence. Do not silently accept edits or preapprove future repair attempts.
Use existing chat/approval primitives if available. Add
apps/web/e2e/notebook-agent-approval.spec.ts for keyboard approval, rejection,
stale revision, concurrent manual edit, quota denial and stopped runtime.
Approval controls must remain accessible and show material cost/data consequences.
Keep agent execution separately disabled until RT5-D. No new chat platform,
autonomous background execution or redesign of the ML lifecycle interface.
```

### RT5-D — adversarial agentic notebook gate

```text
Create tests/test_notebook_agent_adversarial.py and bounded fixtures containing
hostile Markdown, printed fake system instructions, forged tool JSON, fake metrics,
oversized content and requests to access secrets/cross-workspace data. Prove none
can authorize execution, expand permissions, change model promotion or bypass
review. Test approval replay, cancellation, lost runtime and maximum repair budget.
Run prior notebook/browser/agent regression gates and record
docs/verification/RT_AGENT_NOTEBOOK_GATE.md with exact results and remaining
limitations. Repair only this integration. Propose a narrow human-approved pilot;
do not declare unrestricted autonomous notebooks safe or enable the flag without
release authorization. Kill switch must stop new agent commands while cleanup
and authorized human inspection remain available.
```

## Plan RT6 — training, inference and production operations

Dependencies: relevant runtime isolation and existing deterministic ML/MLflow
integration gates. Training/serving are independent of agent rollout. Eight prompts.

### RT6-A — notebook-to-canonical-training contract

```text
Inspect model_build_service.py, workflow_execution_service.py, auto_train_service.py,
current ExecutionRequest APIs, ModelVersion/CodeSnapshot/RuntimeEnvironment and
ML_PLATFORM_INTEGRATION_ARCHITECTURE.md. Add a typed notebook-origin link/command
that submits approved dataset/feature/validation configuration to the existing
model-build path. Do not run a second implementation inside notebook services.
Record notebook revision/epoch as provenance, never as a replacement for pipeline
evidence. Create tests/test_notebook_training_submission.py for canonical lineage,
duplicate submission, stale input, holdout denial and stopping the notebook while
training continues. Exploratory Python models remain unverified. No automatic
promotion of kernel-produced pickles or arbitrary scripts into canonical training.
```

### RT6-B — provider-neutral durable batch execution adapter

```text
Implement one bounded training compute port/adapter beside existing workflow
services and infrastructure/runtime/. Trusted orchestration persists DCLab
attempts; isolated Kubernetes Jobs receive pinned code/data/environment manifests
without product credentials. Use explicit deadline, no hidden container restart
or automatic code retries, deterministic resource identity and cleanup policy.
Reconcile duplicate/late provider objects and publish only the authorized attempt.
Create tests/test_training_runtime_adapter.py for create timeout, duplicate pod,
worker restart, cancelled job, expired attempt and late output. Reuse AWS/GCP
isolation profiles and provider contract tests. Training must survive browser and
notebook termination. Do not implement SageMaker and Vertex simultaneously, replace
MlJob with cloud workflow state, or add GPU/distributed training in this slice.
```

### RT6-C — training publication and verified model gate

```text
Connect batch outputs to existing artifact, reproducibility, verifier, model and
selected tracking owners. Independently validate package format/digest/environment,
feature contract and canonical metrics; keep protected evaluation in deterministic
services. Approved safe model formats load only in isolated workers, not API or
artifact-transfer code. Partial object upload/DB/tracker failure cannot produce
a verified ModelVersion. Create tests/test_training_runtime_publication.py for
corrupt package, wrong input/code digest, unauthorized winner, tracking degradation,
late duplicate output and publication recovery. Record rollback/retention handling.
No new model registry, MLflow authority, hidden notebook state or second tracker.
Run existing model-build/reproduction/scientific tests as regression.
```

### RT6-D — batch inference through existing prediction ownership

```text
Inspect domain/prediction.py, ml/predict.py, engine/serving/artifacts.py, existing
prediction models/APIs and feature/package contracts. Freeze a typed batch API
using approved model-version/input-artifact IDs; reuse compatible records and
add only missing lifecycle links. Exact response/status/error schema belongs in
the work packet before implementation. Execute in the isolated batch lane, with
schema/size bounds, idempotent intent, cancellation and verified output artifacts.
Create tests/test_batch_inference_runtime.py for schema mismatch, revoked model,
cross-tenant input, excessive rows/bytes, timeout and duplicate publication.
Do not log prediction bodies or load model packages into FastAPI. No online
endpoint, automatic model substitution or arbitrary user prediction function.
```

### RT6-E — optional CPU serving deployment persistence and control

```text
Execute if online inference is part of the selected MVP pilot. Inspect existing
deployment owners before adding model_deployments/model_deployment_revisions.
Implement one additive migration, typed domain/service state and private compute
adapter: approved ModelVersion/package/environment, profile, desired/observed
state, optimistic version, immutable revision, readiness and bounded replicas.
Reuse durable intent/jobs and quota ledger; map existing statuses deliberately.
Create tests/test_model_deployment_state.py for cross-tenant references, duplicate
create, unapproved/corrupt package, failed rollout and stable rollback target.
No public endpoint yet; flag off. One CPU profile only, no arbitrary image,
GPU, auto-switching model, autoscaling policy framework or external endpoint URL.
```

### RT6-F — optional prediction API and serving isolation

```text
Freeze the chosen model's exact request/response schema, then add bounded deployment
control and prediction routes to api/v1_model_deployments.py with typed SDK methods.
Reauthorize tenant/model access for every prediction; route to a private separate
serving process that verifies/loads the approved package. Enforce size, schema,
rate, concurrency and timeout limits; return explicit 413/422/429/503/504 errors
and model-version/request IDs. Create tests/test_model_serving_api.py for readiness,
overload, revocation, timeout, untrusted package and rollback. Add a minimal existing
model-detail UI status/action rather than a new dashboard. Verify AWS/GCP adapter
parity and private ingress. Do not expose notebook kernels or an unauthenticated
public model server. No raw feature/prediction logging; rollout still disabled.
```

### RT6-G — operations, capacity, cleanup and restore drills

```text
Add bounded OpenTelemetry metrics/audit integration using observability_service.py,
plus runbooks under docs/runbooks/runtime/. Include start failure, output flood,
stuck interrupt, lost coordinator, orphan resources, reservation leak, DB/provider
outage, serving rollback and suspected escape. Audit operator termination separately
from admission disable. Build a ten-session capacity fixture with independent
training load, DB-pool limits and bounded event retention; record measured start,
execution, event-lag and cleanup behavior, not invented SLOs. Test managed DB restore
and artifact/image digest recovery only in an authorized isolated environment.
Restored active session records become lost; no attempted RAM restoration. Keep
cross-cloud migration offline/drained, never dual-write. Record retention, cost
ceilings, operator ownership and alert thresholds with evidence.
```

### RT6-H — exact-release acceptance and handoff

```text
Run full backend/SDK suites, frontend lint/type/build, selected whole-system and
notebook browser tests, truth/idempotent-regeneration and migration checks from
the documented clean test environment. Run authorized staging conformance for
each provider claimed supported on the same application/policy/image release.
Demonstrate manual cells, saved/reconnected outputs, approved agent action where
enabled, durable training, batch inference and online prediction if selected.
Include denial, quota exhaustion, kill switches, lost session, orphan cleanup,
restore and serving rollback. Record docs/verification/RT_MVP_RELEASE_GATE.md
with exact SHA, commands, durations, warnings, provider-specific limitations and
owners. Missing cloud credentials/evidence are explicit blockers to that claim,
not grounds to weaken tests. Repair only gate defects. Recommend a phased allowlist
rollout; actual enablement requires explicit release approval. Do not claim all
Scope 0–10 work is complete because this runtime slice passed.
```

## What to execute first

Start with **RT1-A**, not a large Kubernetes deployment or agent implementation.
Then reconcile **RT7-A** before finalizing runtime persistence/provisioning. Execute
RT7 shared foundation and the selected RT8 provider adapter before enabling that
external target; numerical placement at the end of this file is not a dependency
on shipping all training/serving features first.
The first functional milestone is **RT4-F**, a manual, isolated, stateful notebook
with proven cleanup. Agent control is a later gate; training/inference remain their
own lanes. RT6-E/F can be deferred only when online inference is explicitly outside
the pilot, and must then be labelled deferred rather than complete.

Prompts specify intended files and tests, not permission to skip inspection. Every
new test path, deployment path and setting in this guide is a proposed addition
unless the current checkout proves an equivalent already exists.

## Plan RT7 — external-compute placement foundation

Dependencies: RT1-A decisions; implementation integrates with the relevant RT1–RT4
owners. This plan does not provision cloud resources. Six bounded work packages.

### RT7-A — home-versus-compute ADR and capability contracts

```text
Inspect the external compute architecture, current runtime blueprint, AWS/GCP
contract, RT1–RT3, storage/factory.py, domain/ml_jobs.py, config.py and any new
runtime code. Record the ADR: AWS/GCP is the platform home; approved external
compute is per epoch/attempt/deployment revision. Freeze domain/compute.py typed
requirements, target/capability/quote/resource observations and error mapping.
Reuse SandboxRuntimePort/training/serving owners; add only a shared private
provisioning mechanism, not another scheduler. Specify CPU architecture, per-GPU
VRAM/count, driver/image, region, trust, egress and deadline predicates. Unknown
capabilities deny admission. Create tests/test_compute_contracts.py for unsupported
features, zero/undersized GPU, ARM/x86 mismatch and absent isolation evidence.
No provider network call, registry-wide dependency install or production flag.
Map implementation dependencies without renumbering original scope evidence.
```

### RT7-B — accounts, grants, placement and quote schema

```text
Inspect current SQLAlchemy metadata and runtime/command/budget owners. Add global
admin-owned compute accounts/target versions, explicit workspace grants and
capability/quote/placement persistence from the external design. Split migrations
at catalog/grant versus placement boundaries; no PR exceeds the common size limit.
Use opaque secret refs, bounded schemas, immutable versions and XOR typed owner
FKs to runtime epochs, jobs or serving revisions. Never store provider keys or an
unenforced generic owner ID. Extend runtime_private.resource_bindings instead of
adding a competing binding store. Create tests/test_compute_persistence.py for
cross-workspace references, unauthorized grants, stale versions, quote expiry,
one-active-placement-per-epoch and supported-head upgrade. Preserve existing
runtime history and disable-on-rollback behavior. No BYO customer cloud accounts
or permanent provider column on Workspace/User in this initial ownership model.
```

### RT7-C — credential broker and policy-based admission

```text
Implement compute_account_service.py and compute_placement_service.py using
existing authorization, secret, intent and quota services. Only the home-cloud
provisioner retrieves provider credentials; rotate/revoke them without logging or
putting them in IaC state, jobs, images or user code. Prefer federation where
supported; a restricted third-party API key is not an exception for AWS/GCP keys.
Filter targets by lane/trust/data/region/hardware before quotes; lock shared
workspace/account budgets and persist placement plus MlJob atomically. Provider
create is outside DB transactions. Revalidate approval and quote at dispatch.
Create tests/test_compute_admission.py for revoked grants/keys, overspend races,
expired quote, wrong region, changed image and disabled capability. Automatic
fallback remains false; authorized fallback cannot exceed approved bounds.
No purchase, provider account creation, broad discovery or raw provider JSON API.
```

### RT7-D — external relay and artifact transfer boundary

```text
Extend runtime transport/artifact owners with infrastructure/compute/transport.py
and compute_transfers; preserve the single home S3/GCS canonical storage registry.
Use private transport or the documented attempt-scoped authenticated outbound
relay with one-use enrollment, resource binding, expiry, sequence and byte caps.
Never expose PostgreSQL, general product API authority, Jupyter tokens or a broad
VPN to a remote kernel. Where runner/kernel separation cannot be enforced, keep
untrusted-Python capability off. Stage only classified/approved artifacts and
quarantine returned bytes; use no broad vendor cloud-sync credentials. Add
tests/test_compute_transport.py and test_compute_transfers.py for forged/replayed
enrollment, SSRF, expired/revoked data grant, wrong workspace, host change, mutable
object digest and output flood. External caches need explicit retention/cleanup
records. No global storage-endpoint switch, shared tenant cache or RAM migration.
```

### RT7-E — metering, ambiguous creation and orphan cleanup

```text
Implement compute_usage_service.py plus provider reconciliation using the existing
queue/reservation owner. Capture estimate/observed/billed separately with decimal
or integer money units, currency, source-event deduplication and reconciled totals.
Include storage/egress/startup/idle/cleanup exposure; never promise a hard invoice
cap from a stale estimate. Handle create-then-timeout without blind duplicate
creation and require verified termination of every owned billable child resource.
Keep cleanup running when admission/credential validation fails; narrow reaper
authority to registered accounts and verified resource ownership. Create
tests/test_compute_reconciliation.py and test_compute_usage.py for eventual
consistency, API outage, leftover disk/endpoint, interrupted attempt, invoice replay
and no early quota release. Add scoped kill switches, spend/orphan metrics and
runbooks. No unlabelled account-wide delete or automatic reserved-capacity purchase.
```

### RT7-F — target selection, quote APIs and shared gate

```text
Add api/v1_compute.py and typed SDK resources for authorized targets, quotes,
placement status/terminate and usage from the external architecture. Integrate
compute_quote_id into existing runtime/training/serving commands, not a second
execution endpoint. Freeze exact schemas and BFF headers; test 403/404, unsupported
capability 409, stale quote 409, 422/429/503 and idempotent replay. Add target/profile
selection to existing notebook controls with GPU/VRAM, region, transfer warning,
estimated ceiling, interruption risk and cleanup state. No vendor key/URL editor.
Create tests/test_compute_api.py, packages/dclab_client/tests/test_compute.py and
apps/web/e2e/compute-selection.spec.ts. Split API/SDK/UI at review boundaries.
Run lint/type/build plus shared offline contracts; external flags remain disabled.
Record the contract gate and eligible RT8 adapters, not provider readiness.
```

## Plan RT8 — external-provider adapters and release matrix

Dependencies: RT7 shared contracts and the selected runtime/training/serving lane.
Each adapter is independently enabled. Live tests require explicit account, region,
data class, budget and cleanup authorization. Six prompts; split an adapter if needed.

### RT8-A — Runpod CPU/GPU Pod adapter

```text
Implement infrastructure/compute/runpod.py for the Pod backend first using the
current official API. Inspect support for CPU/GPU templates, image/driver/region
selection, stable operation identity, discovery, lifecycle and billing evidence.
Map container/attached/network volumes separately; normalize DCLab stop into the
actual supported cleanup sequence. Verify allocated GPU/VRAM instead of assuming
restart preserved capacity. Do not assume a Runpod Pod is a Kubernetes pod or a
VM-isolated kernel. Create tests/test_compute_runpod.py and bounded live conformance
under infra/tests/compute/runpod/. Prove eligible workload/network/isolation limits,
unknown-create reconciliation, retained storage and safe output publication.
Keep Serverless as a separately typed/gated backend, never as a stateful notebook
substitute. Start with classified approved pilot data and reviewed batch code;
enable untrusted notebooks only if that distinct profile genuinely passes.
```

### RT8-B — Railway CPU service and sandbox adapter boundary

```text
Implement infrastructure/compute/railway.py; use a pinned private Node adapter
process under runtime/adapters/railway/ if the supported SDK requires it. Keep
service and sandbox backend contracts separate, and do not upgrade unrelated web
dependencies or expose the provider key in Next.js. Verify available image/base
pinning, command/process limits, network policy, reconnect and timeout semantics
against current official APIs. Provider checkpoints are not portable notebook
RAM until proven. Do not infer DCLab egress denial from a vendor isolation label.
Create tests/test_compute_railway.py plus approved CPU conformance in
infra/tests/compute/railway/. Test absolute expiry despite a live stream/process,
service cleanup and lost epoch. GPU is unsupported in DCLab until a separately
documented/tested target exists. Restricted Python stays off for any missing gate;
trusted CPU eligibility is evaluated independently, never an implicit bypass.
```

### RT8-C — Lambda GPU Cloud VM adapter

```text
Implement infrastructure/compute/lambda_cloud.py against Lambda GPU Cloud, not
AWS Lambda. Support reviewed VM profiles, architecture-compatible environment
bootstrap, attached filesystem locality and authenticated runtime transport.
Vendor-returned Jupyter/SSH credentials are private bootstrap material, never
browser links or public resource identity. Normalize provisioning, unhealthy,
interrupted and terminated observations; reconcile uncertain launches before
any retry. Create tests/test_compute_lambda.py for capacity loss, wrong architecture,
filesystem mismatch, credential redaction, restart/new epoch and billed-child
cleanup. Add bounded authorized checks under infra/tests/compute/lambda/ for actual
GPU resources and the selected notebook/training/serving lane. No assumption of
universal stop/resume, public unauthenticated Jupyter or shared confidential cache.
Record capability-specific readiness and a scoped termination runbook.
```

### RT8-D — Vast.ai offer-based GPU adapter

```text
Implement infrastructure/compute/vast.py with explicit offer discovery and rental
creation. Match region, hardware, lease duration, host class, reliability policy,
bandwidth/storage cost and required security capabilities before quoting.
Persist offer evidence privately and revalidate before acceptance; no cheapest-
offer override of data/host policy. Reject confidential data by default and never
give broad home-cloud credentials to vendor sync tools or instances. Create
tests/test_compute_vast.py for stale/changed offer, host change, expiry, interruption,
missing network control, uncertain create and orphan disks. Live conformance in
infra/tests/compute/vast/ uses explicit approved hosts/data. Do not assume nested
VM isolation can be installed into a provider container. Keep unsupported notebook
profiles unavailable; reviewed batch eligibility is a separate grant/gate.
Document data/process trust limits and cleanup evidence without security guarantees
that the host administrator can defeat.
```

### RT8-E — Nebius CPU/GPU compute adapter

```text
Implement infrastructure/compute/nebius.py using reviewed VM resource/profile,
identity, disk/network and image APIs. Reuse the private provisioner/transport,
not another product database or model registry. Managed Kubernetes is an optional
separately tested backend; a Kubernetes label does not prove isolation parity.
No Nebius object endpoint may replace canonical home S3/GCS by configuration
trick; external cache transfer uses explicit endpoint/identity/digest semantics.
Create tests/test_compute_nebius.py for quota/capacity failure, wrong device/region,
VM identity mismatch, attached disk cleanup, credential loss and late output.
Authorized infra/tests/compute/nebius/ checks actual allocation and chosen lane
security/recovery. Distributed multi-GPU/interconnect support is not implied by
single-GPU success. Record supported profile combinations and scoped rollback.
```

### RT8-F — provider/home-cloud conformance and rollout gate

```text
Run the shared contract suite against all five adapters with network-free fixtures.
For each claimed live target run authorized conformance from AWS and GCP homes,
including identity/relay/artifact paths, not just provider create/delete. Record
readiness by provider/backend/region/profile/lane/code-trust/data-policy and exact
adapter/image/application/policy release. Prove CPU/GPU allocation, session state
where offered, training independence, serving contract where selected, quote/usage,
revocation, ambiguous creation, provider outage and complete billable-child cleanup.
Write docs/verification/RT_EXTERNAL_COMPUTE_GATE.md; mark missing credentials,
capacity, isolation or data approval as unverified/blocked capability, never PASS.
Run truth regeneration, SDK/API/web checks and model/evidence regressions. Recommend
one-provider allowlist pilot, then incremental expansion. No blanket all-providers
production claim, silent fallback, customer-data transfer or actual rollout without
explicit approval. Each target can be disabled without dropping product history.
```
