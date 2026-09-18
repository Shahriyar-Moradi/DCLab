# DCLab external compute architecture

Status: PROPOSED design and requested provider coverage; no adapter or provider is
certified by this document. Reviewed against repository code and official provider
documentation on 2026-09-15. No accounts, credentials, purchases, instances or data
transfers were created during this review.

Extends the [runtime blueprint](JUPYTER_RUNTIME_MVP_BLUEPRINT.md),
[implementation prompts](JUPYTER_RUNTIME_MVP_PROMPTS.md) and
[AWS/GCP deployment contract](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md).
“Lambda” is interpreted as **Lambda GPU Cloud**, not AWS Lambda. “CPU” includes
general computation, memory-heavy and preprocessing workloads; not everything
requires a GPU.

## 1. Decision: one platform home, multiple optional execution providers

Keep the user-confirmed platform deployment choice: the complete authoritative
DCLab control plane runs on **AWS or GCP**. Extend it with independently gated
compute adapters for **Runpod, Railway, Lambda, Vast.ai and Nebius**, as requested.

These are two different choices:

| Decision | Meaning | MVP rule |
| --- | --- | --- |
| Platform home | API/BFF, product PostgreSQL, queue, secret manager, canonical artifacts, audit and lifecycle authority | One AWS or GCP deployment. No second authoritative database elsewhere. |
| Execution target | Where a notebook epoch, training attempt, batch prediction or serving revision consumes compute | A reviewed internal or external compute target selected through DCLab policy. |

A DCLab deployment on AWS can therefore run an approved training attempt on Runpod
and another on Nebius. A deployment on GCP can use the same adapters. This is
external workload placement and data processing, **not** AWS/GCP active-active
product hosting or automatic migration of the platform database.

Keep one Jupyter protocol integration, one notebook model, one job queue and one
model registry. Do not build a different DCLab application for each provider.
Kubernetes remains a home-cloud substrate and one execution adapter, not a mandatory
API that every external provider must pretend to implement.

## 2. Provider coverage and limits

The “proposed DCLab role” column is our design judgment, not provider certification.
Service names, hardware, API versions, regions, limits and pricing must be checked
again during adapter implementation. No quoted GPU price is frozen in this design.

| Provider | Documented mechanism | Proposed DCLab role | Gate / caveat |
| --- | --- | --- | --- |
| Runpod | CPU/GPU Pods, Serverless endpoints and volume APIs | GPU training/batch first; stateful notebook candidate on Pods; stateless serving adapter separately | `runpod_pod` and `runpod_serverless` are different backend kinds. A provider “Pod” is not proof of Kubernetes or VM-grade isolation. |
| Railway | CPU application services and on-demand sandbox VMs | Trusted CPU services/jobs; CPU notebook candidate through the sandbox product | Separate `railway_service` from `railway_sandbox`. No verified GPU target is assumed in this design. Network and environment-image controls must pass DCLab policy. |
| Lambda GPU Cloud | GPU VM lifecycle API and region-scoped filesystems | GPU notebook, training and dedicated inference candidates | `lambda_vm`; certify bootstrap, approved image, private transport, cleanup and capacity behavior; do not expose bundled Jupyter tokens. |
| Vast.ai | Marketplace offer discovery, instance rental and lifecycle APIs | Opt-in, policy-filtered GPU batch capacity; other lanes only after their gates | `vast_instance`; offer/host properties, rental expiry, trust and network behavior are placement inputs. Not interchangeable with a dedicated VM. |
| Nebius | Compute VMs, managed Kubernetes and related compute/storage APIs | GPU/CPU VM candidates first; managed Kubernetes integration where needed | `nebius_vm` initially; `nebius_kubernetes` is separately verified rather than assumed equivalent because it has Kubernetes. |

Official sources for this mapping:
[Runpod API](https://docs.runpod.io/api-reference/overview),
[Runpod CPU/GPU creation](https://docs.runpod.io/api-reference/pods/POST/pods),
[Railway services](https://docs.railway.com/integrations/api/manage-services),
[Railway sandboxes](https://docs.railway.com/sandboxes),
[Lambda API](https://docs.lambda.ai/public-cloud/cloud-api/),
[Vast instance creation](https://docs.vast.ai/api-reference/creating-instances-with-api),
[Nebius services](https://docs.nebius.com/overview/services).

Important provider-specific facts affect the contract:

- Runpod storage types have different lifetimes. Its current lifecycle guidance
  says network-volume-attached Pods cannot be stopped, only terminated; independent
  volume retention can continue billing. DCLab Stop therefore means end the session
  and reconcile resources, not blindly call an identically named vendor operation.
  [Runpod lifecycle](https://docs.runpod.io/pods/manage-pods)
- Railway's documented sandbox is a VM and its SDK currently requires Node.js 22+.
  Its `ISOLATED` networking mode still permits outbound internet; it is not an
  internet-deny mode. Therefore it does not automatically satisfy DCLab's restricted
  Python network profile. The adapter must prove stronger enforcement or keep that
  capability disabled. Treat provider checkpoints as opaque until their semantics
  are tested; do not promise portable Python-memory recovery.
  [Railway sandbox contract](https://docs.railway.com/sandboxes)
- Lambda instances have architecture/region/resource constraints; some are ARM.
  Filesystem locality must be validated. Do not assume one x86/CUDA image runs on
  every advertised machine. [Lambda instances](https://docs.lambda.ai/public-cloud/on-demand/)
- Vast documents host-dependent security/network characteristics and that hosts can
  access files on their machines. A marketplace verification badge is not DCLab
  authorization for sensitive data, nor proof of a stronger kernel boundary.
  [Vast security](https://docs.vast.ai/guides/reference/faq/security),
  [instance lifecycle](https://docs.vast.ai/guides/instances/manage-instances)

Design all five adapters now, enable only tested combinations. Begin with one GPU
provider pilot (Runpod is a reasonable first adapter candidate) and the existing
home-cloud CPU runtime. Add Nebius/Lambda, Railway CPU and Vast in bounded adapter
releases. This order is implementation advice, not a claim about price or reliability.

## 3. Software boundaries

```text
DCLab notebook / training / inference UI
            |
            v
Existing API + authorization + approvals + MlJob intent
            |
            v
Compute placement service ----> PostgreSQL policy / placement / cost records
            |
      capability + data-policy + quota + quote checks
            |
            v
Existing runtime/training/serving ports
            |
      private compute provisioning adapters
            +--> home-cloud Kubernetes
            +--> Runpod Pods / Serverless
            +--> Railway Sandbox / Service
            +--> Lambda VM
            +--> Vast instance
            +--> Nebius VM / Kubernetes

Remote work receives an attempt-scoped manifest and bounded data transfer.
Remote work never connects to the product PostgreSQL or owns DCLab lifecycle state.
```

Proposed owners under `apps/api/app/`, locating equivalents before adding:

- `domain/compute.py`: typed requirements, capabilities, quotes, placement and
  normalized states/errors; no provider SDK imports.
- `services/compute_placement_service.py`: authorize and select among approved
  targets, reserve budget, persist placement and queue work. No network call inside
  a DB transaction.
- `services/compute_account_service.py`: provider-account registration/rotation via
  opaque secret references, capability validation and workspace grants.
- `services/compute_usage_service.py`: estimates, observed usage and billing
  reconciliation over the existing quota/reservation owner.
- `infrastructure/compute/registry.py` and provider modules `runpod.py`, `railway.py`,
  `lambda_cloud.py`, `vast.py`, `nebius.py`: private API translation only.
- `infrastructure/compute/transport.py`: reviewed private connection or narrowly
  scoped outbound relay; no arbitrary user-selected tunnel or URL.
- `api/v1_compute.py`, SDK compute resources and notebook runtime-selection UI:
  expose safe choices and DCLab IDs, never credentials/vendor resource handles.

Keep `SandboxRuntimePort`, `KernelProtocolPort`, training and serving ownership from
the blueprint. Their internal provisioner can share this small mechanism contract:

```text
ComputeProvisioningPort.discover(account_ref, requirements) -> CapabilitySnapshot
ComputeProvisioningPort.quote(target_ref, requirement_digest) -> Quote
ComputeProvisioningPort.create(placement_manifest, operation_id) -> ResourceObservation
ComputeProvisioningPort.inspect(binding_ref) -> ResourceObservation
ComputeProvisioningPort.terminate(binding_ref, operation_id) -> CleanupObservation
ComputeProvisioningPort.usage(binding_ref, cursor) -> UsagePage
```

Stop/resume, checkpoint, streaming, job submission and autoscaling are optional,
separately typed capabilities. Do not require every adapter to fake them. A
serverless request/job is not a resumable Jupyter session. Do not install another
multi-cloud scheduler, ML platform or agent loop to replace DCLab intent ownership.

Railway's Node-specific SDK, if selected, belongs in a small pinned private adapter
process/image such as `runtime/adapters/railway/`. It does not force an unrelated
upgrade of the current Node 20 web CI or move provider keys into Next.js. Prefer a
documented supported protocol/SDK; do not reverse-engineer private browser calls.

### Capability model

Version each target's capability snapshot and expire it (proposed admission TTL:
five minutes). Store observed time, adapter/image/policy versions, evidence refs
and status `unknown | verified | unsupported | temporarily_unavailable | revoked`.
Unknown security capabilities fail closed. Availability is not a reservation.

Requirements include workload lane (`interactive`, `training`, `batch_inference`,
`online_inference`), code trust (`reviewed_code`, `untrusted_python`), CPU architecture,
minimum dedicated/burst CPU, RAM, scratch/persistent storage, maximum duration,
GPU vendor/model/count, **minimum VRAM per GPU**, driver/CUDA compatibility, device
sharing mode, region/datacenter, permitted data class, network policy and capacity
type (`on_demand`, `interruptible`, `reserved` where verified). Do not add unsupported
TPU/other accelerators just because a free-form configuration field permits them.

VRAM is not blindly summed across GPUs. Multi-GPU/distributed requirements need
explicit software support and topology/interconnect evidence. CPU/RAM bundled with
a GPU are not free independent pools. Record requested and actually allocated
resources; reject undersized/zero-GPU substitutions. GPU sharing is never presumed
to isolate tenants. A matched GPU name does not guarantee reproducible numerics.

Each target records what it can truly enforce: isolation boundary, egress/metadata
denial, no product secrets, image/VM base digest, region attestation, process/time/
output limits, idle/deadline cleanup, stable session protocol and safe publication.
Trusted batch availability does not grant arbitrary multi-tenant Python support.

## 4. Database extension — shared PostgreSQL, not five databases

The previous notebook/runtime schema is still proposed. Extend those owners when
implemented; do not create parallel sessions, jobs, artifacts, models or budgets.
New tenant rows carry UUID, workspace ID, creation timestamp and composite lineage
FKs; immutable versions/events are append-only and mutable rows have optimistic
versions. Monetary values use integer micro-units or fixed-precision decimals,
explicit ISO currency and units, never floating point or mixed-currency totals.

| Proposed owner | Fields / relationship | Required constraint / index |
| --- | --- | --- |
| `compute_accounts` | Platform-admin-owned account ID; code-owned provider slug; safe display name; secret reference/version; status; adapter version; last credential validation | Global administrative table for DCLab-managed accounts in MVP, not a tenant-readable credential store. Unique provider/account alias; no key value in row. BYO customer cloud accounts are a later ownership/consent extension. |
| `compute_targets` | Account ID; backend kind; approved region/datacenter; resource/template manifest digest; policy/capability version; enabled flag | Global reviewed target catalog; immutable target configuration versions. Private vendor IDs in bindings. No caller-supplied endpoint or offer overrides. |
| `workspace_compute_grants` | Workspace ID; exact target-version ID; policy version; allowed lanes/data classes; resource/cost ceilings; approver; revoked timestamp | Unique workspace/target/policy-version; a deliberate FK to the global target catalog is allowed. Only authorized grant managers can approve; inactive target/revoked grant always wins. |
| `compute_capability_snapshots` | Target version; normalized capability schema version/digest; observed/expires timestamps; evidence artifact ref; state | Unique target/version/digest; expiry index; immutable evidence. Adapter responses bounded/validated, not arbitrary vendor JSON. |
| `compute_quotes` | Workspace/target/grant/profile; requirement digest; capability snapshot; currency and compute/storage/egress/startup estimates; maximum approved amount/duration; price timestamp/expiry | Unique workspace/id and scoped idempotency key+request digest; positive limits; expiry index. Quote is estimate, not capacity or guaranteed invoice. Public response redacts provider offers/account IDs. |
| `compute_placements` | Workspace; quote/target/policy/profile versions; attempt number; one typed owner: runtime session+epoch, MlJob training/batch attempt, or serving deployment revision; desired/observed state; fence; lease; failure code | XOR owner CHECK and composite workspace FKs; unique owner/attempt; indexed reconciliation state/lease expiry. An interactive epoch has at most one active placement. No generic unenforced polymorphic owner ID. |
| Existing proposed `runtime_private.resource_bindings` | Extend to placement, provider-account and generation; resource kind/UID; private endpoint/certificate ref; cleanup state | Unique account/backend-kind/vendor-UID; multiple child resources per placement (compute, disk, IP, endpoint). No competing binding table. Never delete by name alone. |
| `compute_transfers` | Workspace/placement; canonical source or quarantine artifact ID; direction; manifest/expected digest; data-policy version; destination region; byte budget; state; grant expiry; cleanup receipt | Composite artifact/placement lineage; unique placement/direction/manifest/attempt; cleanup-expiry index. Provider cache ref stays private; bytes are not a second canonical Dataset/Artifact. |
| `compute_usage_events` | Workspace/placement/reservation; meter; quantity/unit; interval; `estimated | observed | billed`; amount/currency; source-event digest; optional supersedes-event ID | Append-only; unique account/source-event digest; index workspace/time and placement/meter/time. Reconciliation must not add estimate+observation+invoice as three charges. |

Extend `runtime_profiles` for accelerator constraints, architecture and approved
backend requirements; do not put a single permanent `gpu_provider` column on User
or Workspace. A workspace can approve multiple targets and a run chooses one.
Extend the existing reservation ledger with GPU counts/types, storage/egress and
pending-cleanup exposure. No second budget ledger for external providers.

For fields referencing global account/target catalog rows, authorization comes from
the explicit workspace grant. Other tenant-to-tenant references still require
composite FKs. If later adding workspace-owned provider accounts, add ownership
constraints and separate sharing/consent policy; never silently expose one tenant's
account to another through a globally readable catalog.

The current `storage/factory.py` intentionally resolves one canonical S3/GCS
backend and rejects mismatched provider/bucket reads. Keep that behavior. The
existing `storage/s3.py` does not by itself configure every S3-compatible endpoint.
External caches need a separate reviewed transfer adapter or explicit endpoint
registry, with endpoint identity and version semantics; changing the global S3
endpoint is not a valid way to redirect existing artifact IDs to Runpod/Nebius.

Migration order: accounts/target catalog -> grants/capabilities/quotes -> placement
and private binding extension -> transfers/usage. Discover live Alembic head each
time; use bounded additive migrations and shared metadata. If RT2 has not shipped,
design its schema with these references now and split implementation accordingly.
Rollback disables admission/targets, drains and reconciles resources, and retains
usage/provenance; it does not drop history or abandon billable provider resources.

## 5. Placement, credentials and remote transport

1. User selects an approved profile/target, or explicitly allows a bounded set of
   targets. The application filters by current grants, lane, code/data policy,
   hardware, region, deadlines and budget before asking for a quote.
2. Persist the quote. Approval binds requirement/input/code/model digests, permitted
   target set/regions, quote/cost ceiling and policy versions. Requote/reapprove
   when outside those bounds; never use price ranking to override residency.
3. Lock/reserve workspace and platform-account capacity/budget. Persist placement
   and existing-queue provisioning intent atomically. Vendor create happens later.
4. Provider API credentials are fetched only by the trusted provisioner in the home
   cloud. Use short-lived federation where supported; where a provider needs an API
   key, keep a restricted, rotated third-party key in the home secret manager.
   The AWS/GCP static-key prohibition remains unchanged. No provider API key enters
   user code, browser, source artifact, model, job JSON, Terraform state or logs.
5. Bootstrap a reviewed runner using a pinned manifest and one-use enrollment
   material scoped to this placement/attempt. Bind enrollment to the independently
   observed provider resource and authenticated connection as far as supported;
   it is not cryptographic proof against the underlying host administrator.
6. Prefer private authenticated connectivity. Where unavailable, a narrowly exposed
   HTTPS/mTLS relay endpoint in the home cloud can accept outbound authenticated
   runner connections. It is a specific placement data-plane protocol, **not** a
   general VPN into DCLab or a tunnel to PostgreSQL. Disable redirects/SSRF and
   arbitrary destinations; enforce message sizes, audience, sequence and rate.
7. Stage exact authorized artifacts, start the workload through its existing lane,
   stream bounded events and independently validate outputs. End with resource and
   storage cleanup plus budget/usage reconciliation.

The kernel remains without product/cloud credentials and unrestricted egress.
If the provider permits a stronger VM/container separation, place the trusted
runner outside the untrusted kernel boundary. Do not call a same-container or
same-privilege sidecar a security boundary. If the provider cannot separate/enforce
these rights, it is not eligible for that untrusted-Python profile.

An unavoidable attempt-scoped relay/transfer capability grants only that attempt's
approved inputs, bounded output upload and heartbeat/result submission. It cannot
create compute, read arbitrary objects, query SQL, authorize tools or promote a
model. Treat runner events/results as untrusted. In managed serverless modes, the
home adapter may submit/poll the provider protocol without a persistent remote
runner; it still obeys the same data/cost/result contracts.

No default public Jupyter/SSH/model URL for users. The browser continues through
DCLab BFF/SSE. Do not disable TLS verification because a marketplace Jupyter image
uses a self-signed certificate; replace/terminate through a reviewed authenticated
transport. Built-in provider cloud-sync integrations must not receive broad home
storage credentials. Vast explicitly documents credential movement for its sync
mechanism; DCLab uses its own bounded transfer boundary instead.
[Vast cloud sync](https://docs.vast.ai/guides/instances/storage/cloud-sync)

## 6. Data policy and physical-provider trust

External execution is an explicit data disclosure/processing destination, even if
the UI still says DCLab and the transport is encrypted. Datasets, model weights,
prompts, outputs, logs, checkpoints and telemetry all need placement classification.
Missing classification or unknown region blocks transfer; never default unknown
customer data to “public.” Enforce this for each artifact/input, not just workspace.

Proposed pilot policy: external admission off by default; public/synthetic datasets
may enter an explicitly enabled target after technical tests. Customer-confidential
data requires organization/provider/region approval and applicable security/privacy
review. Marketplace/community hosts are denied for confidential data by default.
Provider branding/certifications alone do not waive the execution isolation gate.

Encryption at rest/in transit does not protect plaintext being processed from an
untrusted host administrator. Do not promise that a VPN, a Docker container, an
encrypted disk or a secret manager solves this. Stronger confidential-computing
claims require separate hardware/attestation/application evidence and are outside
this initial design. No sensitive pilot is authorized by creating these documents.

Canonical artifacts remain in home S3/GCS. External disks/object volumes are
attempt caches or approved checkpoints with manifests, digest checks, TTL and
cleanup. Avoid shared writable cross-tenant volumes and global sensitive-data
deduplication. Record retained-storage charges after compute stops. Publish outputs
to quarantine before home Artifact/ModelVersion associations become visible.
Verify checkpoint compatibility with code/data/environment/hardware on fresh attempts.

## 7. Recovery and cost semantics

Normalized placement states: `requested -> reserving -> provisioning -> staging ->
ready -> running -> draining -> terminating -> terminated`; terminal execution
outcomes and cleanup state are separate. Alternatives include `failed`, `lost`,
`interrupted` and `cleanup_pending`. Map actual vendor observations explicitly.
“Stopped,” “destroyed,” “request cancelled” and “no longer billed” are not synonyms.

Idempotency binds DCLab operation ID to request digest. Providers may not offer
native create idempotency: after a timeout search/reconcile the recorded operation
label/handle and await a bounded consistency window. If create outcome remains
ambiguous, mark it uncertain and prohibit blind duplicate creation. Keep the
reservation and escalate cleanup; don't assume that no search result means no bill.

Never live-migrate Python variables between providers. A provider switch means a
new placement and epoch with explicit state loss; restore only published compatible
artifacts/checkpoints, not assumed RAM. Uncertain notebook code is never replayed.
Preemption/failover may create a fresh **training attempt** only if approved retry
policy and pinned checkpoint/data/code allow it, with new attempt provenance.
Serving fallback is similarly versioned and approved; no silent model/region change.

Price comparison includes allocated resources, minimum billing, boot/image-pull
time where billed, idle time, persistent disks, home-cloud egress, return transfer,
warm inference replicas and interruption/retry exposure. Quote currency/unit and
freshness are explicit. An estimate is not a hard provider billing cap; delayed
metering and failed deletion can exceed it. Show estimated and billed separately.

Admission has deployment, workspace, provider-account and target ceilings. Kill
switches exist globally and per account/target/lane. Stop-admission never disables
cleanup. Home provider account keys live only in the provisioner; an independent
reaper with narrowly scoped discovery/termination rights handles orphans. Use native
TTL/deadline when supported plus DCLab watchdogs, but record what still runs/bills
if the home cloud or provider API is unreachable. Reserve cleanup headroom and alert
on unresolved resources; do not promise zero orphan spend under a total outage.

## 8. API and frontend additions

Use existing `/v1`, BFF, idempotency, CSRF, error envelope, paging and authorization
conventions. Ordinary users select target IDs/profile IDs; only reviewed admin
operations manage credentials/catalog/grants. Public provider **display names** are
fine; private account/instance/offer IDs and credentials are not public identities.

| Proposed request | Success / core contract | Failure behavior |
| --- | --- | --- |
| `GET /v1/compute-targets?workload_kind=interactive` | 200 authorized choices: DCLab ID, provider label, region, hardware summary, allowed lanes, capability state and observed time | Filter inaccessible targets, never expose global account catalog. |
| `POST /v1/compute-quotes` | Body `{target_id,profile_id,workload_kind,input_manifest_id,max_duration_seconds}`; 201 `{id,expires_at,currency,estimate,ceiling,requirement_digest,limitations}` | 403 grant/data policy; 409 unsupported capability; 422 invalid profile; 429 quota; 503 discovery unavailable. No guaranteed capacity claim. |
| Existing runtime start / training / deployment command | Add `{compute_quote_id}` and bind it to saved code/input/model/profile/owner; 202 durable intent plus placement ID | 409 expired/mismatched quote or changed target; 403 missing approval; same-key conflict remains 409. |
| `GET /v1/compute-placements/{id}` | 200 safe state, lane, target label/region, requested/observed resources, usage summary, cleanup status and limitation/reason | 404 inaccessible placement; no remote endpoint/token. |
| `POST /v1/compute-placements/{id}/terminate` | 202 cleanup request, or current state on replay; restricted owner/operator policy | Not a deletion of notebook/model history; warn about scratch loss. |
| `GET /v1/compute-usage?project_id=...` | 200 bounded per-currency estimate/observed/billed summary with reconciliation status | Technical/cost visibility permission; never sum currencies without explicit conversion record. |

Add runtime selector beside existing start controls: CPU/GPU profile, provider,
region, per-GPU VRAM, estimated cost/ceiling, interruption risk, data-transfer
warning and current readiness. Disabled choices explain the missing capability.
Show “compute stopped; storage retained” and “cleanup pending” explicitly. Changing
provider while a notebook is live offers stop/new epoch, not an invisible switch.
No arbitrary provider JSON editor. Agent tools use the same immutable quote/approval
path and cannot select unapproved destinations or increase spending authority.

## 9. Delivery, tests and release claims

The [runtime prompt guide](JUPYTER_RUNTIME_MVP_PROMPTS.md) adds RT7 (shared placement
foundation) and RT8 (provider adapters and matrix gate). Existing RT1–RT6 IDs stay
stable. Design RT7-A before finalizing RT2 resource schema and RT3 provisioner;
implement required shared pieces before any external-provider execution is enabled.

Required offline tests: capability filtering, cross-workspace grant/placement FKs,
quote expiry, idempotency conflicts, ambiguous create, host/offer changes, region
and architecture mismatch, missing VRAM, counterfeit results, relay replay, revoked
transfer grant, output quarantine, provider rate limits, eventual consistency,
interruptions, leftover disks/endpoints, invoice deduplication and flag behavior.
No paid provider access or credential requirement in ordinary PR CI.

Protected live tests are explicit and bounded per target/account/region. Prove
runtime state sharing where offered, separate training/serving lifecycle, actual
hardware, network/isolation behavior, data provenance, cancellation, idle/absolute
expiry and all billable-child cleanup. Test each enabled external adapter from both
supported home-cloud deployments before claiming complete AWS/GCP integration parity.
Provider-side security evidence may be reused only for the identical attested
target configuration; home transport/storage/identity paths still need both tests.

Track readiness by `(home cloud, external provider, backend kind, region/profile,
workload lane, code trust, data policy, adapter/image/policy release)`, not a global
`RUNPOD_READY=true`. Failure of Vast notebook isolation need not block reviewed-code
Runpod training or the core deterministic product. Missing capabilities are visible
and disabled, never hidden behind a generic “cloud supported” label.

Implement typed defaults in `config.py` and safe examples when coding begins:
external admission false; explicit provider/target/region allowlists; no unknown
data transfer; automatic fallback false; five-minute quote/capability freshness;
finite execution/cleanup deadlines; small per-account concurrency; independent
provider breakers; and an explicit transfer-byte/cost budget. Values are policy
proposals, not new settings already available in the running application.

Observe queue/placement latency, capacity rejection, actual GPU/profile mismatch,
cross-provider transfer bytes/time, estimate variance, unknown outcomes, retained
storage, cleanup age, provider API failures and usage reconciliation lag. Use
bounded labels (provider/backend/lane/profile/status) and redacted audit references.
Runbooks cover compromised provider key, inaccessible remote runner, host change,
expired rental, provider account suspension, runaway spend, stalled publication,
region-policy violation and orphan cleanup. No unlabelled account-wide deletion.

Non-goals: relocate product PostgreSQL to every provider; buy reserved capacity
automatically; use a broad provider key in user code; guarantee commodity-provider
host confidentiality; add a second scheduler/model registry; live-migrate kernels;
or claim every provider supports every CPU/GPU/security feature. The requested
five-provider coverage is a design and implementation program, not completed runtime
availability.
