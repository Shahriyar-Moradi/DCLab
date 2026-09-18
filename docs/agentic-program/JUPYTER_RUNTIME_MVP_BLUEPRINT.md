# DCLab notebook, training and inference runtime — MVP blueprint

Status: PROPOSED implementation design; not evidence that these capabilities exist.
Prepared: 2026-09-15. User-confirmed deployment model: deploy the complete platform
to **either AWS or GCP**, using the same application release. The authoritative
control plane does not span both clouds. At the user's subsequent request,
Runpod, Railway, Lambda GPU Cloud, Vast.ai and Nebius are external compute targets
under the [external compute architecture](EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md).
No resources were provisioned for these documents.

Implementation companion: [ordered work packages](JUPYTER_RUNTIME_MVP_PROMPTS.md).
This is a refinement proposal for the existing program, not a replacement for
[Scope 4](prompts/SCOPE_04_AGENTIC_NOTEBOOK.md),
[the execution standard](prompts/EXECUTION_STANDARD.md),
[the cloud architecture](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md), or
[the ML integration architecture](ML_PLATFORM_INTEGRATION_ARCHITECTURE.md).
Existing scope and evidence IDs retain their meanings. Resolve the explicit
Scope 4 lifecycle refinement below through an ADR before implementation.

## 1. Recommendation and minimum useful product

Build a DCLab-owned runtime service around **Jupyter Server + ipykernel inside one
isolated sandbox per active notebook session**. Keep the current DCLab frontend;
JupyterLab is not required. Use Kubernetes for home-cloud compute and reviewed
provisioning adapters for external targets. Do not write a
Python execution engine, expose a shared Jupyter server to tenants, or run user
code in the FastAPI process or ordinary privileged ML worker.

The initial vertical slice is deliberately small:

1. An authorized user opens a versioned notebook in a project.
2. Start a CPU runtime, run `x = 41`, then run `x + 1` and receive `42`.
3. Stream text, a bounded table and a Matplotlib PNG into the notebook.
4. Refresh the browser and recover saved outputs; reconnect to the same live
   session without rerunning code.
5. Interrupt or restart. Restart explicitly discards Python variables. Stopping
   releases compute; saved notebook revisions and published artifacts survive.
6. Deny another workspace, block cloud metadata/product-database access, enforce
   quotas and clean up the session after its deadline.

Then add, in order: authorized dataset staging; agent-proposed cell edits with
explicit approval; durable canonical training; batch inference; a minimal approved
model endpoint if online inference is required for the pilot. A cell can train a
small exploratory model immediately, but that does not create a verified DCLab
model version or a production deployment.

### Jupyter Server versus Enterprise Gateway

| Component | What it contributes | DCLab decision |
| --- | --- | --- |
| Jupyter Server | Kernel/session HTTP endpoints and kernel message transport | Use inside the private session sandbox; expose only DCLab APIs to browsers. |
| ipykernel | Stateful Python execution, outputs, exceptions and completion protocol | Use a pinned, tested version in the runtime image. |
| JupyterLab | A complete notebook frontend | Optional future integration, not needed for the existing DCLab UI. |
| Jupyter Enterprise Gateway | Remote-kernel launch/management through infrastructure adapters | Defer until remote kernel fleets, separate kernel clusters or specialized provisioners justify it. |
| JupyterHub | Multi-user Jupyter deployment/login management | Not a second identity/control plane for this custom-UI MVP. |

Jupyter Server's [REST interface](https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html)
provides kernel lifecycle operations. Cell execution uses the
[kernel WebSocket protocol](https://jupyter-server.readthedocs.io/en/latest/developers/websocket-protocols.html),
not a hypothetical REST `/execute` endpoint.

Server and Gateway are not mutually exclusive: Server can connect to a remote
Gateway. Gateway does not replace DCLab tenancy, approvals, quotas, artifact
publication, training orchestration or model serving. Its documented availability
modes reconnect to surviving kernels and are marked experimental; this is not a
snapshot/restore mechanism for a dead kernel's RAM. See
[Enterprise Gateway](https://github.com/jupyter-server/enterprise_gateway) and
[its availability guidance](https://jupyter-enterprise-gateway.readthedocs.io/en/latest/operators/config-availability.html).
The recommendation to start with Server is an architectural judgment for this MVP.

## 2. Observed repository foundations and gaps

Inspection baseline: `f71d42d64d1ca53b3c372cdea4d68b723da2a7fc`, with pre-existing
uncommitted planning/truth-artifact changes. This is a dated inspection, not a
replacement CURRENT truth inventory. Discover the live head again before editing;
`alembic heads` returned `0059_auth_session_constraints` during this review.
No production database schema was inspected or changed.

| Existing owner | Reuse | Missing extension |
| --- | --- | --- |
| `apps/api/app/services/model_build_notebook.py`, `model_build_reproduction_service.py` | Generated reproduction notebook/artifact conventions | These export notebooks; they do not provide interactive execution. |
| `apps/api/app/db/models.py`: `ExecutionRequest`, `MlJob` | Durable intent, transactional queue and worker leases | Notebook intent types and runtime-specific records/handlers. |
| `domain/ml_jobs.py`, `services/job_handlers.py` | Code-owned handler registry; ID-only bounded payloads | Runtime lifecycle/reconciliation dispatch. Existing shipped handlers are `labs.auto_train` and `auth.session_cleanup`. |
| `domain/execution_requests.py` | Stable execution intent | Currently only `model_build` is an allowed operation; a notebook operation needs a deliberate constraint/schema update. |
| `Artifact`, `storage/base.py`, `storage/s3.py`, `storage/gcs.py` | Metadata, digest and cloud-neutral object operations | Safe runtime transfer, immutable object-version handling and output quarantine/publication. |
| `RuntimeEnvironment`, `CodeSnapshot`, `ModelVersion` | Environment fingerprints and canonical scientific provenance | `RuntimeEnvironment` is not a live session. `CodeSnapshot` is pipeline-bound, not a generic per-cell record. |
| `api/auth.py`, `api/deps.py`, session/authorization services | Browser identity and workspace enforcement | Notebook capabilities and stream revocation checks. |
| `apps/web/app/api/backend/[...path]/route.ts`, `lib/infrastructure/bff-proxy.ts` | HttpOnly-cookie BFF; streaming HTTP response forwarding | Notebook commands/event resume contract; current header allowlist omits idempotency, conditional-write and SSE-resume headers. No WebSocket upgrade handler exists here. |
| Root `Dockerfile`, `pyproject.toml`, `.github/workflows/ci.yml` | Python 3.12 baseline and existing test pipeline | Separate locked runtime/coordinator images and deployable cloud manifests. No `infra/` directory was present. |

Important correction: `MlJob.job_type` and `handler_key` use format checks, not
closed database enumerations. New valid handler keys do **not** require a migration
merely to register them. Job **statuses** are closed; do not silently insert new
status values. ExecutionRequest operation vocabulary is separately constrained.

## 3. Ownership and deployment topology

```text
Browser: DCLab notebook + chat + training/results views
    | HttpOnly session, CSRF-protected commands, authenticated output stream
    v
Next.js BFF --> FastAPI application services
                    |                         |
                    v                         v
           PostgreSQL intent/state       Artifact metadata + S3/GCS
           existing MlJob queue          immutable source/data/output bodies
                    |
                    v
         runtime coordinator / reconciler (trusted; separate deployment)
                    |
           private Jupyter HTTP/WebSocket
                    v
         isolated session sandbox (untrusted)
         Jupyter Server + ipykernel + scratch inputs/outputs

Separate execution lanes, sharing DCLab ownership and artifact contracts:
  canonical training --> isolated batch compute --> validated ModelVersion
  approved ModelVersion --> serving deployment --> prediction API
  agent worker --> proposal/approval --> the same notebook command service
```

The API accepts intent; it does not wait for Kubernetes provisioning. The runtime
coordinator creates, connects, interrupts, destroys and reconciles sandboxes. It
does not own a second notebook database or another durable queue. A private
Jupyter server is a mechanism inside an untrusted boundary, not an authorization
service or source of trusted metrics.

Notebook source is authoritative in DCLab revisions and artifacts. Do not expose
Jupyter's Contents API as a competing save path. Do not iframe a publicly reachable
Jupyter token URL. Browser clients never receive Kubernetes/Jupyter/cloud tokens.

### Three different lifetimes

| Lane | Lifetime and authority | Durable record |
| --- | --- | --- |
| Interactive notebook | Stateful session; cells share RAM within a kernel epoch | Notebook revision, runtime session, epoch and ordered executions. |
| Training | Bounded job with pinned code/data/environment; independent of notebook RAM | Existing execution request, jobs, workflow/pipeline/candidate/model lineage. |
| Inference | Batch job or independently deployed approved package | Existing prediction lineage plus a serving-deployment record when needed. |

The current Scope 4 disposable-execution design needs an explicit extension for
stateful sessions. Preserve disposable sandboxes for fresh whole-notebook runs and
batch jobs. For interactive mode, reuse a sandbox only within its owning user,
notebook, workspace and bounded session lifetime. Never reuse it for another
tenant. A restart creates a new epoch in a **new sandbox**, so orphan child
processes cannot survive a mere Python-kernel restart.

## 4. AWS and GCP infrastructure contract

One release, one PostgreSQL primary per deployment, one home-cloud provider per
environment. External execution targets are separate: workspaces may select from
explicitly granted Runpod, Railway, Lambda GPU Cloud, Vast.ai or Nebius profiles.
There is no home-cloud switcher, live cross-provider kernel migration, global
dual-writer database or automatic platform-cloud failover in this MVP. See the
[placement, provider and data-policy contract](EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md).

| Concern | AWS deployment | GCP deployment |
| --- | --- | --- |
| Trusted application/coordinator compute | EKS managed node groups | GKE Standard node pools |
| Untrusted CPU session and user-code job isolation | Approved EKS Fargate profile, or separately verified equivalent boundary | GKE Sandbox with gVisor and approved runtime class |
| Product PostgreSQL | RDS PostgreSQL Multi-AZ | Cloud SQL PostgreSQL regional HA |
| Objects / container images | S3 / ECR | GCS / Artifact Registry |
| Trusted workload identity | EKS Pod Identity on supported nodes, or IRSA where required | Workload Identity Federation for GKE |
| Secret/key services | Secrets Manager / KMS | Secret Manager / Cloud KMS |
| Ingress | TLS ingress to BFF/API, private runtime services | Same application contract with GCP ingress implementation |
| Jobs/events | Existing PostgreSQL queue; no required SQS or Redis | Existing PostgreSQL queue; no required Pub/Sub or Redis |
| Telemetry | Shared OpenTelemetry instrumentation, provider-specific sink | Same instrumentation and semantic metrics |

Use the existing proposed `infra/tofu/` and `infra/kubernetes/` ownership, not
separate AWS and GCP application repositories. Pin tooling, providers, image
digests and supported cluster versions in reviewed lock/manifests; this document
does not select an untested “latest” release.

[EKS Fargate](https://docs.aws.amazon.com/eks/latest/userguide/fargate.html) does not
support GPU workloads or DaemonSets. Do not copy a GPU/gVisor/node-agent design
unchanged to Fargate. Validate its network restrictions using the actual pod
security-group/route configuration; a Kubernetes NetworkPolicy manifest alone is
not proof that a particular execution substrate enforces it.

[GKE Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/sandbox-pods)
uses gVisor and has version/hardware-specific GPU support. The proposed initial
profile is CPU-only on both providers. A GPU profile requires a separate isolation,
driver, quota and cost gate; GPU time-sharing must not be assumed to isolate
mutually untrusted tenants.

For database HA, use supported managed configurations and test application
reconnect/reconciliation after failover. HA does not preserve notebook RAM. See
[RDS Multi-AZ](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZSingleStandby.html)
and [Cloud SQL HA](https://docs.cloud.google.com/sql/docs/postgres/configure-ha).

Build provider-independent tests locally, then certify each provider separately.
Cloud credentials/quotas/budget/regions require deployment authorization. Until
both staging gates pass, label the untested provider “designed, not verified.”

## 5. Database design

### Common invariants

New tenant-owned tables use UUID primary keys, `workspace_id`,
`created_at timestamptz NOT NULL DEFAULT now()`, and `UNIQUE(workspace_id, id)`.
Mutable rows also carry `updated_at timestamptz` and `version bigint NOT NULL`.
State fields have explicit CHECK allowlists; SHA-256 fields require 64 lowercase
hex characters, not just a fixed-length string. Relationships use composite
foreign keys, not just application filters. Include `project_id` in composite ownership constraints where
needed to prevent cross-project reuse inside one workspace. Immutable records use
the existing database immutability approach; mutable records have `version bigint`.
Object bodies and source code are artifacts, not job payloads or unbounded JSONB.
Deployment-global, code-owned `runtime_profiles` are the exception to tenant
ownership: they have no workspace ID; selecting a profile still requires workspace
authorization/quota. References to global users/environment fingerprints use their
actual canonical keys, while membership remains an authorization requirement.

The tables below are **proposed**, not present as a finished notebook subsystem.
If another scope implements an owner first, extend that owner instead of adding
another table. Names/types must be reconciled in the schema work packet.

| Proposed table | Required fields beyond common identity | Constraints / access indexes |
| --- | --- | --- |
| `notebooks` | `project_id`, `title varchar(200)`, `created_by_user_id`, nullable `current_revision_id`, `version`, nullable `archived_at` | Current revision must belong to this notebook; active listing index `(workspace_id, project_id, updated_at, id)`. |
| `notebook_revisions` | `notebook_id`, `revision_number bigint`, nullable `parent_revision_id`, `manifest_artifact_id`, `content_digest char(64)`, `created_by_user_id` | Unique `(workspace_id, notebook_id, revision_number)`; parent belongs to same notebook; immutable; optimistic append against current revision. |
| `notebook_cells` | `notebook_id`, `revision_id`, `logical_cell_id uuid`, `position integer`, `cell_type`, `source_artifact_id`, `source_digest char(64)` | Row ID identifies this revision's cell, logical ID links versions. Unique revision/position and revision/logical-ID; position nonnegative; type allowlist `markdown`, `python`, `managed`. |
| `notebook_environment_versions` | `name`, `version_number`, `runtime_environment_id`, `dependency_lock_artifact_id`, `profile_digest`, `approved_by_user_id`, `approved_at`, nullable `revoked_at` | Workspace approval references the existing environment fingerprint; never duplicate global environment identity. Unique workspace/name/version. Revoked environments cannot start new epochs. |
| `notebook_input_bindings` | `notebook_id`, `revision_id`, `binding_name`, `artifact_id`, `expected_digest`, `relative_mount_name`, `purpose`, `access_policy_version` | Unique revision/binding-name; authorized immutable input only; names cannot contain traversal, absolute paths or URLs. Holdout policy enforced at staging, not just initial creation. |
| `runtime_profiles` | `name`, `version_number`, `cpu_millis`, `memory_mib`, `scratch_mib`, `cell_timeout_s`, `idle_timeout_s`, `absolute_timeout_s`, `max_output_bytes`, `policy_digest`, `enabled` | Code-managed reviewed profiles, all bounds positive and capped; unique name/version; reference immutable profile IDs. No user-supplied pod spec/image/service account. |
| `runtime_sessions` | `project_id`, `notebook_id`, `owner_user_id`, `environment_version_id`, `profile_id`, `state`, `current_epoch integer`, `last_activity_at`, `absolute_expires_at`, `lease_owner`, `lease_expires_at`, `fence bigint`, `version`, nullable `failure_code`, `stopped_at` | Unique active session per workspace/notebook/owner via partial index; expiry and reconciliation indexes; owner may not be silently changed. No endpoint token or plaintext secret. |
| `runtime_epochs` | `runtime_session_id`, `epoch_number`, `environment_version_id`, `input_manifest_artifact_id`, `started_at`, nullable `ended_at`, `end_reason` | Unique session/epoch; execution history references exact epoch; environment/input digest cannot mutate mid-epoch. Restart/environment/input changes create new epoch. |
| `notebook_executions` | `notebook_id`, `revision_id`, `runtime_session_id`, `epoch_number`, `execution_request_id`, `mode`, `state`, `requested_by_user_id`, nullable approved-command/agent provenance IDs, timestamps | Mode `cell` or `run_all`; immutable selected revision; unique execution-request association; index workspace/notebook/created-at/id. No separate durable training queue. |
| `cell_executions` | `notebook_execution_id`, `revision_id`, `cell_id`, `runtime_session_id`, `epoch_number`, `execution_sequence bigint`, `state`, `jupyter_msg_id`, `dispatch_fence`, nullable `kernel_execution_count`, timestamps, safe `error_code` | Composite cell/revision/notebook and session/epoch lineage; unique session/epoch/sequence and session/epoch/message-ID. One in-flight cell per session via partial unique index covering dispatching/running/cancelling. |
| `notebook_outputs` | `cell_execution_id`, `output_index`, `mime_type`, `artifact_id`, `digest`, `size_bytes`, `publication_state` | Unique cell/output-index; bounded allowlisted types; published objects immutable. Raw kernel output is never trusted scientific evidence. |
| `notebook_execution_events` | `notebook_execution_id`, `cell_execution_id` nullable, `sequence bigint`, `kind`, `payload jsonb`, `created_at` | Unique execution/sequence; payload object <=16 KiB by DB byte check; index execution/sequence. Bounded transient stream; durable final output referenced by artifact. |
| `runtime_quota_accounts` | `max_sessions`, `max_cpu_millis`, `max_memory_mib`, `budget_limit_units`, `version` | One account/workspace; nonnegative values. Lock row during admission; do not allocate by racy count-then-insert. |
| `runtime_reservations` | `quota_account_id`, nullable typed owner FKs `runtime_session_id`, `ml_job_id`, `model_deployment_id` when serving exists, reserved resource amounts, `state`, `expires_at`, `settled_at` | Exactly one owner by CHECK; composite workspace FKs and unique owner reservation, not an unenforced polymorphic resource ID. Partial index on active expiry; idempotent settlement. Reuse a compatible Scope 2 budget ledger if already implemented; never run two ledgers for one charge. |

For external execution, extend these same runtime/session/quota owners with the
account, target, grant, capability, quote, placement, transfer and usage records in
the external compute design. Do not create one notebook/queue/model schema per
provider or add a permanent GPU-provider field to User/Workspace.

Provider bindings belong in a private infrastructure schema/table, for example
`runtime_private.resource_bindings`: resource ID, generation, opaque provider
resource UID, deployment-profile digest, connection-secret reference and cleanup
state. Only coordinator/cleanup identities can read this table. Do not expose it
through public serializers. Jupyter connection secrets are scoped to one sandbox;
they are not user authentication sessions and must not enter `auth_sessions`.

Future online serving adds `model_deployments` and immutable
`model_deployment_revisions`, referencing the **existing** `ModelVersion`, approved
package/environment, resource profile and deployment intent. Mutable deployment
stores desired/observed state, active revision, version and replica limits. Revision
has unique deployment/revision number and package digest. Do not add a parallel
model registry or generic endpoint table until this slice is selected.

### State machines and failure semantics

- Session: `requested -> provisioning -> ready`; `ready -> restarting -> ready`
  with a new epoch; nonterminal sessions can enter `stopping -> stopped`, `failed`
  or `lost`. Absolute/idle expiry records the reason and initiates stopping.
- Session readiness is distinct from kernel activity (`idle` / `busy` / `unknown`).
  A missing heartbeat is not immediate proof that code stopped.
- Cell: `queued -> dispatching -> running -> succeeded | failed`; cancellation
  passes through `cancelling`. Terminal alternatives are `cancelled`, `timed_out`,
  and `lost` (outcome uncertain). Never call a lost execution successful.
- Cancellation is a request, not a guarantee that earlier in-memory/file effects
  were undone. Interrupt, wait a bounded grace period, then terminate the sandbox
  if needed. Mark the epoch dirty and require restart before more executions.
- Existing MlJob/ExecutionRequest statuses retain their meanings. Domain-level
  cancellation/loss is projected to existing terminal statuses plus safe reason
  codes unless an explicitly reviewed additive status migration is justified.

### Migration strategy

Discover Alembic heads and current metadata before each revision. Add notebook
content tables first, session/epoch/quota tables next, execution/output/event tables
after that. Do not put all schema plus APIs plus infrastructure in one migration
PR. Empty-database and supported previous-head upgrades must both pass.

Add only the necessary ExecutionRequest operation and request-spec validation for
`notebook_execute`; retain old `model_build` behavior. Reuse its workspace
idempotency key and bind requests to a canonical digest; do not create an unrelated
execution idempotency mechanism. Runtime lifecycle commands need similarly scoped
durable receipts, reusing the program's command owner if present.

Use additive expand/contract changes; choose composite keys and lock/index strategy
before SQL. Refresh the existing Alembic catalog/constraint fixtures through their
supported path. Disable features and roll back compatible application images before
any destructive schema action. Preserve executed revisions/events/artifacts; prefer
forward repair after production writes. Do not downgrade a live database merely
because a feature is disabled.

## 6. Application and API contract

Add `domain/notebook.py`, `domain/runtime.py`, `services/notebook_service.py`,
`services/notebook_execution_service.py`, `services/runtime_session_service.py`,
`services/runtime_reconciliation_service.py`, `api/v1_notebooks.py` and
`api/v1_runtimes.py` only if equivalent owners are still absent. Register routers
in `main.py`; reuse `api/deps.py`, existing membership/ML-write/capability services.
Infrastructure adapters go behind these proposed interfaces:

```text
SandboxRuntimePort.start(StartSpec, operation_id) -> RuntimeHandle
SandboxRuntimePort.inspect(handle) -> RuntimeObservation
SandboxRuntimePort.terminate(handle, operation_id) -> TerminationObservation
KernelProtocolPort.connect(handle, epoch) -> KernelConnection
KernelProtocolPort.execute(connection, message_id, source, limits) -> message stream
KernelProtocolPort.interrupt(connection) -> acknowledgement
RuntimeArtifactPort.stage(authorized_manifest, handle) -> StagingReceipt
RuntimeArtifactPort.collect(handle, bounded_manifest) -> QuarantinedArtifactRefs
```

No port accepts arbitrary provider JSON, shell commands for the host, endpoint URLs,
cloud credentials or a caller-selected Kubernetes namespace. Handles are private.

### Proposed HTTP surface

Paths below are API paths; browsers use `/api/backend` in front of them. All IDs are
UUIDs; examples use readable placeholders. New contracts must use the program's
shared `/v1` error/page conventions. If the structured error helper is not yet
implemented, finish that dependency rather than asserting legacy routes already
return it or introducing a competing envelope.

| Request | Body / success response | Key failure / permission |
| --- | --- | --- |
| `POST /v1/notebooks` | `{project_id,title}` -> `201 {id,project_id,current_revision_id,version}` | ML-write authority + enabled notebook capability; 422 invalid title. |
| `GET /v1/notebooks?project_id=...&limit=...&cursor=...` | `200 {items,next_cursor}`; default 20, max 100 | Authorized technical notebook visibility; filter in DB. |
| `GET /v1/notebooks/{id}` | `200 {id,project_id,title,current_revision_id,version}` plus ETag | 404 absent/inaccessible resource. |
| `POST /v1/notebooks/{id}/revisions` | `If-Match` required; `{base_revision_id,cells:[{logical_cell_id,type,source}],input_bindings:[...],environment_version_id}` -> `201 {revision_id,digest,version}` | 428 missing precondition, 412 stale version; max source/notebook limits. Inputs are artifact IDs, not arbitrary paths/URLs. |
| `POST /v1/runtime-sessions` | `{notebook_id,revision_id,environment_version_id,profile_id}` -> `202 {id,state:"requested",epoch:0,version}` | Authorized owner, approved image/profile; 429 quota, 503 disabled/unavailable substrate. |
| `GET /v1/runtime-sessions/{id}` | `200 {id,state,activity,epoch,version,expires_at,reason_code}` | Never serialize private address/token/provider ID. |
| `POST /v1/runtime-sessions/{id}/restart` | `If-Match`; `{expected_epoch}` -> `202 {id,state:"restarting",version}` | Owner only, except audited operator stop; stale epoch 409. Explicit RAM-loss confirmation in UI. |
| `POST /v1/runtime-sessions/{id}/stop` | `{expected_epoch}` -> `202 {id,state:"stopping"}` | Same request replays; already stopped returns 200; cleanup remains available when runtime starts disabled. |
| `POST /v1/notebooks/{id}/executions` | `{revision_id,cell_ids:[...],runtime_session_id,expected_epoch,mode:"cell"}` -> `202 {id,execution_request_id,state:"queued",events_url}` | Source is loaded from saved revision; 409 stale epoch/busy/conflicting revision; 403 missing approval when required. |
| `GET /v1/notebook-executions/{id}` | `200 {id,state,revision_id,epoch,cells,output_refs,last_event_sequence}` | Authorized workspace/notebook read; bounded cell/page projection. |
| `GET /v1/notebook-executions/{id}/events` | `text/event-stream`; resume from `Last-Event-ID` | 410 expired cursor before stream starts; reconnect from final snapshot. No tokens in query strings. |
| `POST /v1/notebook-executions/{id}/cancel` | `{}` -> `202 {id,state:"cancelling"}` | Owner or explicit cancellation authority; terminal request returns current terminal state. |
| `GET /v1/notebooks/{id}/export?revision_id=...` | `200` bounded `.ipynb` download with optional authorized saved outputs | Export cannot embed secrets, private object URLs or revoked data. |

Mutating requests carry `Idempotency-Key` (max 128 characters) and browser CSRF
protection. Reuse returns the same resource/result; same key with different
canonical payload returns 409. Authenticate every request; missing/expired session
is 401, known insufficient permission is 403, oversized input is 413, and provider
diagnostics are redacted. Preserve `X-Request-Id`. The BFF must deliberately forward
`Idempotency-Key`, `If-Match`, `Last-Event-ID` and response `ETag`/`Retry-After` where
allowed; it currently does not do all of this.

Viewer/business roles cannot execute Python. Reuse `can_execute_workspace_ml` for
ML-write eligibility, then add explicit runtime capability, quota and session-owner
checks. Do not reuse the legacy business-capability bypass to enable arbitrary code
for `client_user`. Reading raw cell output/data also requires technical/data access;
workspace membership alone is not blanket access. Revocation stops admission and
invalidates streams; stop affected runtimes under a bounded revocation policy.

## 7. Cell execution, transport and recovery

Use browser HTTP commands plus SSE output for the MVP. The coordinator maintains
the private WebSocket to Jupyter. This fits the current BFF better than assuming
Next.js route handlers support arbitrary WebSocket upgrades. Test proxy buffering,
disconnect cancellation and infrastructure timeouts explicitly.

1. Authorize, check flags, expected revision/epoch, reserve quota and persist intent
   plus existing-queue work atomically. Source/dataset bodies do not enter jobs.
2. Reconcile/create a deterministically labelled sandbox. Use provider object UID
   and generation, not a name alone, to prevent adoption of an unrelated pod.
3. Stage authorized digest-verified inputs before marking the epoch ready. The
   private kernel connection is established and listeners attached before execute.
4. Serialize execution per session. Persist `dispatching`, stable Jupyter message
   ID and fence before sending. Send only the saved source; disable stdin, custom
   comms/widgets and arbitrary user-expressions in this first protocol profile.
5. Correlate replies and outputs using the parent message ID. Wait for both the
   matching execution reply and matching IOPub idle before normal completion;
   channels may be observed in different orders. Bound late asynchronous output
   and mark it explicitly, rather than assuming idle means no future background
   output. See the [Jupyter messaging specification](https://jupyter-client.readthedocs.io/en/stable/messaging.html).
6. Persist bounded event batches, then stream only committed events. Save final
   output artifacts and terminal state after publication succeeds. Execution success
   and scientific verification remain different concepts.
7. On ambiguous send/crash/reconnect, do **not** resend arbitrary Python. Reconcile
   what can be proved; otherwise mark outcome lost/uncertain and require an explicit
   fresh run. Jupyter message IDs do not provide exactly-once execution.

Use one leased coordinator owner per session. Database fencing alone cannot stop a
stale socket writing to a kernel: after lease loss, quarantine the epoch and ensure
the old sandbox is terminated before a new owner starts another epoch. If termination
cannot be verified, fail closed and keep its resource reservation. Browser reconnect
does not require a new epoch; unsafe coordinator takeover does. This conservative
MVP loses RAM on coordinator failover rather than claiming transparent recovery.

MlJob retries may reconcile/start/stop a resource idempotently; they must never
blindly rerun a dispatched cell. Reconciliation has heartbeats and bounded retry;
cell execution outcome is owned by `cell_executions`, not inferred from queue success.

SSE events: `execution.queued`, `cell.started`, `output.appended`,
`output.truncated`, `cell.completed`, `cell.failed`, `execution.cancelled`,
`runtime.lost`, `execution.completed`. IDs are monotonic execution sequence numbers;
transport may redeliver, clients deduplicate. Batch small text chunks at up to
100 ms / 16 KiB, enforce per-execution output/rate caps, and never store every byte
as a row. Treat PostgreSQL notifications, if used, only as wake-up hints; polling
the durable cursor must recover missed notifications. Do not hold a DB connection
or transaction open for the lifetime of every browser stream.

Reauthorize streams on reconnect and at a bounded interval (proposed 15 seconds),
close on session/membership revocation, send safe heartbeats, propagate aborts to
upstream fetches, and cap concurrent streams. Output already delivered to a browser
cannot be recalled. A slow client is disconnected and resumes, not allowed to grow
unbounded server memory.

## 8. Data, security and reproducibility boundary

Treat all notebook Python, Jupyter services in the sandbox, uploaded files and
kernel outputs as untrusted. Even a signed runtime image executes untrusted code.
Never trust a kernel-reported success, metric, package digest or timing as verified
scientific evidence without independent validation.

- Sandbox: non-root, no privilege escalation, no privileged mode, no host mounts,
  Docker socket, host network, Kubernetes service-account token or product DB
  credentials. Drop capabilities, pin image digest, use read-only root filesystem
  with bounded writable scratch. Verify effective process/resource bounds on each
  substrate; do not claim an unsupported manifest field enforces them.
- Networking: default-deny new outbound connections, including metadata, cluster
  API, other tenants, product databases, public internet, DNS tunnelling and IPv6
  bypasses. Allow only the precise private coordinator ingress/established replies.
  **Local loopback required by Jupyter/kernel communication remains allowed.** A
  blanket loopback ban from a generic sandbox checklist is incompatible here.
  External-target transport may permit the reviewed attempt-scoped relay described
  in the external compute architecture; this grants neither unrestricted kernel
  egress nor access to the home cloud's private network.
- The sandbox may know its own Jupyter connection secret. This is not tenant
  isolation; it grants no DCLab/cloud rights. Validate every incoming message as
  attacker-controlled. A same-pod sidecar is not a safe place for privileged cloud
  credentials because the kernel shares its network/security boundary.
- Trusted transfer workers fetch authorized artifacts with their own workload
  identity. Transfer exact bytes into a sandbox through a bounded private protocol;
  kernels do not receive cloud SDK credentials, broad presigned URLs, product API
  tokens or general egress. Revalidate data permission/purpose before staging.
- Runtime inputs are ephemeral copies with digest checks and read-only bindings
  where enforced. Existing S3/GCS metadata includes provider/bucket/key/digest, but
  the current port lacks a first-class immutable version-reference operation.
  Extend it deliberately for version/generation preconditions or prove immutable
  content-addressed writes plus protected overwrite policy. Never rely on an
  unversioned mutable key as a reproducibility guarantee.
- Output publication: collect only explicit relative paths under the export root;
  reject symlinks, traversal, special files, archives with unsafe expansion and
  oversized manifests. Upload to quarantine; independently verify size/digest/type;
  commit Artifact/NotebookOutput references; garbage-collect unreferenced staging
  after a grace period. DB commit and object upload are not one transaction.
- Render `text/plain`, bounded validated JSON tables and decoded PNG in the MVP.
  Escape text. Disable active HTML, SVG, JavaScript, arbitrary iframes and widgets;
  no raw `innerHTML`. Re-encode images in a constrained decoder with pixel/byte
  limits. Markdown also needs a safe renderer without active embedded content.
- No runtime `pip install`, user Dockerfiles, package-network access, terminals,
  external connectors, secret injection or agent orchestration frameworks in the
  initial image. Native Python can create subprocesses; enforce the sandbox/process
  budget rather than claiming that hiding a terminal prevents shell execution.
- Pin source revision, actual execution order, environment/lock/image digest,
  immutable input digests, random seeds and relevant hardware. A locked image alone
  does not prove deterministic GPU/numerical results or capture hidden notebook RAM.

Fresh whole-notebook verification runs execute the chosen revision in order in a
new epoch with no inherited state. Out-of-order exploratory results remain labelled
exploratory. Protected holdout data is not made freely available to a Python cell;
holdout evaluation stays behind existing deterministic services.

## 9. Training, inference and agentic notebook integration

### Training

Small experiments may run in the notebook with its limits. For durable training,
submit a typed DCLab model-build request with approved dataset/feature/validation
configuration. Reuse `model_build_service.py`, `workflow_execution_service.py`,
`auto_train_service.py`, existing job and immutable pipeline/model owners. The
notebook is a client and provenance link, not a second auto-train implementation.

Longer jobs run independently of the browser/session. Separate trusted orchestration
from untrusted training code. Use a provider-neutral training adapter over isolated
Kubernetes batch compute for the home-cloud path and the independently gated
external compute adapters for requested GPU/CPU targets. SageMaker Training and
Vertex custom jobs remain optional later adapters, not simultaneous MVP
requirements. Preserve deterministic
split/holdout/evidence policy. User-authored training scripts, if introduced later,
remain exploratory until a reviewed training-spec integration and validation gate
admit them; never promote a pickle from a cell into the trusted API process.

Keep canonical attempts in DCLab. A batch object has bounded deadline, explicit
retry ownership and cleanup policy. In the initial adapter, avoid hidden execution
retries (`backoffLimit: 0`, no container restart); a new DCLab attempt creates fresh
compute and immutable outputs. Reconcile duplicate provider pods/late outputs and
publish only the authorized winning attempt. Kubernetes Jobs are not an exactly-once
execution guarantee; see [Job behavior](https://kubernetes.io/docs/concepts/workloads/controllers/job/).
Checkpoint restart, if added, verifies code/data/image/checkpoint digests and is not
the same as replaying a notebook cell.

Use the selected MLflow integration behind existing program ports for tracking;
do not give the kernel unrestricted MLflow credentials. DCLab owns final metrics,
verification and ModelVersion promotion; incomplete required tracking blocks verified
promotion, not all access to exploratory outputs.

### Inference

Batch inference is the first production slice: authorized model version + input
artifact -> bounded queued job -> schema-validated output artifact and lineage.
Inspect existing prediction code before extending it; do not reinterpret its
business prediction tables as a complete arbitrary-model serving registry.

If the MVP must offer online inference, add one **CPU, approved-package** serving
profile. A separate serving deployment verifies package signatures/digests and
feature schema, loads the model outside FastAPI, implements readiness, bounded
concurrency, deadlines and rate limits, and exposes a private service. The DCLab API
authenticates and routes requests. Do not expose the notebook's kernel or a notebook
HTTP server as the inference endpoint.

Proposed serving API: `POST /v1/model-deployments` with model-version/profile IDs
returns 202; `GET /v1/model-deployments/{id}` returns desired/observed state;
`POST /v1/model-deployments/{id}/predict` accepts a bounded schema-checked request
and returns predictions plus model-version/request IDs; stop/rollback commands are
asynchronous and versioned. Fail with 503 when unavailable, 429 at concurrency
capacity, 422 for schema mismatch, 413 for oversized input and 504 on timeout.
Prediction bodies must not enter general logs. Reuse existing prediction response
contracts where compatible; freeze the exact feature/response schema in its work
packet because it depends on the selected model contract.

Deployment states: `requested -> provisioning -> ready -> draining -> stopped`,
with `failed` and degraded observations. Rollback selects a previously approved
immutable deployment revision, never silently substitutes another model on error.
Keep at least one warm replica if promising interactive latency; scale-to-zero,
multi-model routing and GPU serving are later explicit capacity decisions.

### Agentic notebook

The agent lives in the trusted, separately isolated agent-worker lane selected by
the existing program. It proposes a versioned cell diff, explains intended inputs
and resource limits, and waits for the required approval. DCLab ToolRunner validates
and reauthorizes the same notebook command used by a human.

Approval binds notebook/base revision, new revision/cell digests, input bindings,
environment/profile, budget and intended action. Any change invalidates approval.
The agent cannot request arbitrary runtime settings, access credentials, call
Kubernetes/Jupyter directly, execute tools found inside output text, alter holdout
policy or mark its own outputs verified. Bound repair attempts, tool calls, duration
and spending. Re-read permissions at execution time; record principal, approval,
agent-run reference and resulting execution IDs. Start with propose-and-approve,
not unrestricted autonomous cell execution.

## 10. Safe defaults, scale and operations

These are **proposed initial policy values**, not measured service guarantees or
currently available configuration. Implement typed settings in `config.py` and
safe examples; record actual effective provider resources and approved exceptions.

| Setting / policy | Initial default |
| --- | --- |
| `DCLAB_NOTEBOOKS_ENABLED`, `DCLAB_NOTEBOOK_PYTHON_ENABLED` | `false`; explicit allowlist pilot activation only after applicable gates. |
| `DCLAB_NOTEBOOK_AGENT_EXECUTION_ENABLED`, `DCLAB_MODEL_SERVING_ENABLED` | `false`; independent release flags. |
| `DCLAB_RUNTIME_ADMISSION_ENABLED` | `false` until provider conformance passes; global kill switch blocks new sessions/jobs. |
| `DCLAB_RUNTIME_BACKEND` | Explicit approved adapter in deployed mode; no automatic local-process fallback. |
| `DCLAB_RUNTIME_IMAGE_DIGEST`, `DCLAB_RUNTIME_POLICY_DIGEST` | Required in deployed mode; no mutable-tag fallback. |
| `DCLAB_RUNTIME_IDLE_TIMEOUT_SECONDS` | 900; authorized execution, not open SSE connections, resets inactivity. |
| `DCLAB_RUNTIME_ABSOLUTE_TIMEOUT_SECONDS` | 14400; warn before expiration; do not silently preserve RAM. |
| `DCLAB_CELL_TIMEOUT_SECONDS`, `DCLAB_CELL_INTERRUPT_GRACE_SECONDS` | 120 and 10; longer approved training uses a job, not a longer browser request. |
| `DCLAB_RUNTIME_MAX_SESSIONS_PER_USER`, `...PER_WORKSPACE` | 1 and 2; account-wide deployment ceiling 10 until capacity test. |
| CPU / memory / scratch profile | 2 vCPU, 4 GiB, 1 GiB application limits; account for runtime/provider overhead. |
| Source / notebook / cells | 64 KiB per code cell, 1 MiB saved source per notebook, 200 cells. |
| Output / image / input | 2 MiB text/event data per execution; 4 MiB and 4 megapixels per PNG; 10 MiB published output per cell; 256 MiB staged input per session. |
| Stream / event policy | 16 KiB event maximum; 7-day replay retention initially; final artifacts follow workspace retention; 2 browser streams per execution/user. |
| Process limit | Target hard cap 256 including runtime overhead; must be demonstrably enforceable on each selected substrate or production profile stays disabled. |
| Egress / GPU / terminal / package installation | Denied / disabled / disabled / disabled. |

Feature flags do not bypass security controls. Separate “stop admitting” from
“terminate active runtimes.” Emergency termination is an audited operator action;
revocation, deadlines, cleanup and quota settlement continue when admission is off.
Track the provider until destruction is confirmed; unavailable cloud APIs do not
mean resources have stopped costing money.

Start with one managed PostgreSQL service and bounded pools per process. Do not
allocate one persistent DB connection per kernel or browser stream. Budget the
sum of API, worker, coordinator, migrations, monitoring and any separate tracker
connections against the database limit. Reserve headroom. Use short row locks,
existing skip-locked job claims and indexed cursor reads. No database sharding,
Kafka, Redis or event-table partitioning is required for the first ten sessions;
add them only after measured queue/event/retention pressure and a replacement plan.

Stateless API/BFF replicas and per-session coordinator leases allow horizontal
growth. Runtime capacity scales separately from the API. Keep training concurrency
separate so it cannot consume all notebook capacity. Multi-zone application replicas
protect the control plane; individual kernels are intentionally disposable and may
lose RAM during infrastructure failure. Communicate this accurately in the UI.

Measure: admitted/rejected sessions, ready latency, active age, queue wait, execution
latency, interrupt-to-termination time, lost epochs, output truncation, event lag,
lease conflicts, orphan resources, quota settlement lag, resource utilization,
estimated compute spend, training publication failures and inference p95/error rate.
Metric labels use bounded classes/provider/profile/state, not user IDs, raw cell
source or unbounded exception strings. Audit events include workspace/resource,
actor, request/approval IDs and action outcome; never secrets, prompts or raw rows.

Proposed alerts: orphan resource older than five minutes; missed cleanup deadline;
nonzero isolation-policy violation; repeated lease conflicts; unreconciled reservation
older than five minutes; sustained admission failure or inference error spike.
Tune numerical SLOs only after load tests; do not advertise an unmeasured uptime or
runtime-start latency. Rate-limit repeated alerts and assign an operator owner.

Runbooks must cover runtime start failure, stuck interrupt, lost coordinator, lost
kernel, output flood, suspected sandbox escape, quota leak, object publication
failure, DB failover, provider outage, serving rollback and credential compromise.
Each gives detection, containment, exact scoped inspection, recovery verification
and escalation. Never use broad unlabelled resource deletion.

Back up PostgreSQL with managed PITR and retain versioned artifacts/images/lockfiles.
Test restore into an isolated environment, reconcile dangling metadata/objects, and
mark old active sessions lost rather than reconnecting restored IDs to arbitrary
live resources. Cross-cloud migration is a controlled drain/export/copy/restore/
digest-validation exercise; PostgreSQL backup alone does not move object bodies,
secret references, encryption access or container images. Kernel RAM is not backed up.

## 11. Release sequence and definition of done

Execute the [companion work packages](JUPYTER_RUNTIME_MVP_PROMPTS.md), one bounded
PR per prompt. Map them into the master plan through an ADR; do not renumber earlier
evidence or treat them as authorization to execute all program scopes.

1. Freeze stateful-session and Jupyter adapter contracts; inspect existing code.
2. Add notebook revisions and session/epoch persistence with PostgreSQL tests.
3. Build the private runtime image, coordinator and isolated local integration rig.
4. Deliver manual cell execution and safe output UI behind flags.
5. Complete AWS/GCP isolation, staging, expiry and recovery conformance; enable only
   the verified pilot deployment after explicit release approval.
6. Integrate agent proposals/approval after prerequisite authorization/tool policy.
7. Connect canonical training, batch inference and optional CPU serving, preserving
   scientific ownership and testing independently of live notebook sessions.
8. Run end-to-end release, restore, cancellation, cost and operational drills.

Runtime work can begin as a disabled vertical slice; public untrusted Python must
not ship before the Scope 4 isolation gate and relevant Scope 0/2/3/9 dependencies.
Bring the necessary runtime infrastructure work forward from Scope 9 through the
plan reconciliation; do not interpret late infrastructure numbering as permission
to expose Python before isolation exists.

Minimum observed acceptance evidence on each claimed provider:

- Two cells share state; another user/workspace does not. Restart clears state;
  browser refresh does not rerun code. Explicit fresh-run reproduces pinned inputs.
- Text, exceptions, bounded tables/plots, large output, disconnect/reconnect,
  expired cursor and permission revocation behave as documented.
- Simultaneous run/start requests respect DB uniqueness, quota and serialized
  execution; duplicate HTTP requests create one intent; uncertain sends never replay.
- CPU/memory/process/disk exhaustion, metadata/network attempts, unsafe outputs,
  object traversal and forged provider/kernel identifiers fail within the boundary.
- Killing API/coordinator/kernel, losing DB connectivity and failing cleanup expose
  honest states, leak no authority and eventually reconcile resources/reservations.
- Agent proposals require valid approval; hostile cell output cannot authorize a
  tool; model promotion and holdout evaluation use existing deterministic services.
- Training survives notebook stop; inference uses an approved immutable package
  independently; backup restore and model rollback are tested.
- Same-SHA backend/SDK/web/browser CI, locked image digests and actual cloud gate
  evidence agree. No claim of production readiness based only on mocked Kubernetes
  or external-provider APIs. External availability is scoped by provider, backend,
  region/profile, workload lane, data policy and home-cloud integration evidence.

This document and its companion are design deliverables only. They do not change
application behavior, add dependencies or migrations, deploy cloud resources, or
certify the current software as a working agentic notebook.
