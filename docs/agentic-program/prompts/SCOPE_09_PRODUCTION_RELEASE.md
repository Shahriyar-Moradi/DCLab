# Scope 9 execution prompts — production platform and measured pilot

Every prompt inherits the plan-level outcome and mandatory live-checkout
execution card in [the remaining-scope map](REMAINING_SCOPE_EXECUTION_MAP.md).
Complete one reviewable lettered work unit at a time.

Start when the product scopes included in the pilot have passed their gates.
Apply `README.md`, `EXECUTION_STANDARD.md` and
`../AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`. Read
`../EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md` and the RT7/RT8 work packages in
`../JUPYTER_RUNTIME_MVP_PROMPTS.md` before compute-placement infrastructure.
AWS and Google Cloud are equal supported targets; select approved regions and
residency per deployment. All
infrastructure changes require reviewed plans and environment approval.
The production-MVP release set includes the bounded specialist roster,
three-mode Deep Investigation, isolated Python, hosted read MCP and the initial
upload/S3/GCS/SQL/CRM/Snowflake connector pack. S2-P13F must record the OpenAI
Agents adapter decision; the adapter may remain disabled without blocking the
deterministic or LangGraph paths.
When Jev or NOOA is evaluated for the release, S1-P12F/S2-P14H must record its
purpose/runtime decision. They remain optional: an excluded integration is
`NOT_APPLICABLE` to the pilot and deployment, secrets, routing and public
capability claims must cleanly omit it.

## Scope implementation boundary

Create a reviewed `infra/` layout only after the platform ADR. Separate dev, CI,
staging and production accounts/projects, state, identities and secrets. Deploy
web, API and handler-allowlisted workers independently. Notebook compute remains
isolated or disabled. Managed PostgreSQL/object/secrets/telemetry are private.
Deploy a private pinned MLflow tracking service only for the verified Plan 3.0
adapter. Give it a separate backend database/schema identity and object prefix;
it is not a public UI/API, tenant authority, registry authority or deployment
control plane. Only approved ML/reconciliation workers may reach it. Pandera,
Evidently and safe model-format libraries remain inside the ML-worker image;
OpenTelemetry remains the operational telemetry boundary.
DuckDB/PyArrow exists only in the reviewed ML/data-worker scan dependency group
behind S0-P09 DataScanPort. DuckDB is not deployed as a service, provisioned a
database or given network/product-DB/object-store credentials. Production locks
in-memory connections, code-owned templates, extension/external-access denial
and memory/thread/input/output/temp/time limits. API/web/agent/investigation/
MCP images contain neither DuckDB nor Polars.
Run the authoritative pinned raw LangGraph runtime only inside `worker-agent`; its dedicated
PostgreSQL checkpointer schema uses a least-privilege identity and is not public.
Run the verified S2-P12 Deep Agents harness only inside a separate
`worker-investigation` dependency/image/identity/runtime namespace with no
product database, object-store or provider credentials. It calls the existing
DCLab API through narrowed read/completion scopes and is never connected to
`worker-agent`. Do not deploy LangGraph Agent Server, Deep Agents Agent Server or
a second agent-facing API for the MVP. When S2-P13F allows a canary, deploy its
adapter separately with no product DB/object credentials, one-runtime-per-run
enforcement and DCLab-mediated required actions; otherwise deploy no Agents API
runtime and record the no-go/disabled evidence. Deploy pinned `dlt` only in
connector-worker images. Airbyte/Openflow remain external interoperability
options, Snowflake remains a read-only source, and none receives DCLab product-
database or lifecycle authority. Hosted MCP is a public-SDK facade with its own
OAuth audience and read/write kill switches.
When S1-P12F permits a Jev purpose, only the API-owned semantic-decision gateway
holds its secret and outbound allowlist; workers never receive it. When S2-P14H
allows NOOA, deploy `worker-nooa` separately with no provider/product DB/object/
cloud/Jupyter-control-plane credentials and only expiring access to private
DCLab model/tool facades. Otherwise deploy neither unit and prove clean disable.

## AWS/GCP portability requirements

Use one pinned OpenTofu CLI, pinned AWS/Google providers, one Kubernetes base
and small provider overlays. AWS uses EKS, RDS PostgreSQL, S3, Secrets Manager,
KMS and approved AWS edge/telemetry modules. GCP uses GKE, Cloud SQL PostgreSQL,
Cloud Storage, Secret Manager, Cloud KMS and approved GCP edge/telemetry modules.
Both deploy identical signed OCI digests and DCLab contracts. One environment
has one provider; no active-active database/object writes or automatic cross-
cloud failover are in the MVP. Both providers must independently pass plan,
policy, staging, restore, golden-path, rollback and cost gates before the
corresponding `AWS_READY` or `GCP_READY` release state is recorded.

AWS/GCP readiness applies to the complete authoritative platform home. Runpod,
Railway, Lambda GPU Cloud, Vast.ai and Nebius are separately gated per-execution
targets only. Keep independent readiness for provider, backend kind, lane, region,
architecture and data class; never route to an external provider merely because
it is cheaper or because the home cloud is unavailable.

## Plan 9.1 — environment and infrastructure architecture

**Contract.** One IaC tool and both cloud-provider versions are selected and pinned. Environments
have separate state/credentials and least-privilege workload identities. Network,
DNS/TLS/WAF and configuration are code-reviewed and reproducible.

### S9-P01A — requirements and platform decision ADR

```text
Read AWS_GCP_DEPLOYMENT_ARCHITECTURE.md and
EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md. Collect approved AWS and GCP regions,
residency, availability/RTO/RPO, workload/storage/egress, isolation, managed-
service, team-skill, compliance and cost constraints. Confirm the common
OpenTofu plus Kubernetes-base architecture and pin the CLI, AWS/Google
providers, EKS/GKE/Kubernetes versions, module sources, Kustomize and state/
locking/encryption approach; document rejected ECS/Cloud Run and provider-
native workflow alternatives, ownership and exit cost. Define production release boundary and
which non-MVP business/advanced scopes are excluded. Include private MLflow backend/object
capacity, isolation, retention, backup/reconciliation and outage requirements,
plus Deep Investigation, connector workers, isolated Python, hosted MCP and the
OpenAI adapter decision. These agreed MVP workstreams cannot be silently omitted.
Record which external provider/backend/lane combinations are disabled, pilot or
production candidates; their credential, transport, data-class, quota, quote,
cleanup and cost-control policies; and the evidence required from each AWS/GCP
home. Do not provision them or conflate external-target readiness with the
canonical EKS/GKE decision.
Stop for stakeholder decision where business
requirements are absent; do not silently choose only one cloud or change the
canonical EKS/GKE decision in an implementation PR.
```

### S9-P01B — account/project and environment bootstrap

```text
Create `infra/tofu/modules/aws/`, `infra/tofu/modules/gcp/` and provider stack
roots for separate dev/CI/staging/production AWS accounts and GCP projects,
remote encrypted locked state, CI federation, break-glass role and least-privilege
operators. Pin providers/modules and validate tags/owners/budgets/regions. No
long-lived cloud key in repository/CI. Add format/validate/policy/plan tests and
document bootstrap/recovery for each provider without applying production in an
unapproved prompt. Keep provider outputs behind one reviewed deployment-output
schema and never place secret values in state.
```

### S9-P01C — network, DNS, TLS and edge controls

```text
Implement equivalent AWS VPC and GCP VPC modules for private database/object/
secrets access and controlled worker
egress, public web/API/MCP ingress, load balancer/WAF/rate limits, trusted proxy
headers, DNS and managed TLS rotation. Separate notebook and connector egress
policies. Put MLflow on a private service endpoint reachable only from approved
ML/reconciliation identities; deny browser, public API/MCP, agent, notebook,
connector and action-worker reachability. Give worker-investigation
egress only to the DCLab API/telemetry and
its dedicated short-retention runtime store when approved; explicitly deny
product DB/object storage, provider endpoints, worker-agent and arbitrary
Internet. Deny public management/data planes by default. Add IaC policy tests for
open CIDRs/public S3/GCS buckets/plain HTTP/metadata and diagram both provider
flows. Provider differences must not weaken the shared ingress/egress contract.
```

### S9-P01D — workload identity and configuration contract

```text
Create distinct Kubernetes service accounts and cloud workload identities for
web, API, ML, MLflow tracking,
reconciliation, agent, investigation, optional OpenAI-agent adapter, integration, notebook-control
and migrations with only required DB/schema/object/secret/queue/
telemetry permissions. Define typed environment config source, validation,
versioning and rollout. MLflow cannot read the DCLab product schema or object
prefix; DCLab ML workers get only the narrow tracking endpoint/credential and
authorized artifact prefixes. Investigation receives only API read and private worker-
lease/gateway/completion plus optional isolated runtime-state access, never
product database/object/provider access.
Use EKS Pod Identity for supported node-backed workloads and IRSA where the
approved Fargate sandbox requires it; use Workload Identity Federation for GKE.
No static cloud key or shared admin credential. Test forbidden cross-role access
in AWS and GCP staging and production-default fail closed. Document rotation,
identity propagation delay and break-glass.
```

### S9-P01E — environment architecture gate

```text
Run OpenTofu validate/plan/provider-lock/policy/security/cost checks for AWS and
GCP in every environment; review
state isolation, network reachability, identities, DNS/TLS and disaster boundary.
Perform staging bootstrap/destroy of a disposable environment on each provider
where approved and record observed outputs. Publish the normalized capability
matrix, architecture/owners/risks/rollback. Do not apply production until
subsequent data/deployment/security gates.
```

## Plan 9.2 — managed data, object and secret operations

**Contract.** DCLab PostgreSQL is authoritative for product state; object storage
is private/immutable-aware; secrets/KMS are managed. MLflow has a separately
owned backend database/schema identity and object prefix with no cross-database
FK or Alembic ownership. Migrations run as controlled identities. Backup/restore,
tracking reconciliation and object/row reconciliation are tested, not assumed.

### S9-P02A — managed PostgreSQL and access roles

```text
Provision the same supported PostgreSQL major/version contract as private RDS
for PostgreSQL Multi-AZ and Cloud SQL for PostgreSQL HA, with
multi-zone/maintenance/backups/logging settings based on RTO/RPO and cost. Create
separate owner/migrator/application/read-operator roles, TLS enforcement and
PgBouncer/pooling configuration when required. Deny public/network and superuser
application access. Test connectivity/permission matrix and failover behavior in
staging. Provision the MLflow backend database or schema with a distinct owner/
runtime identity and migration lifecycle; prove MLflow cannot read DCLab product
tables and DCLab API/agent roles cannot read MLflow internals. Do not add FKs,
views or shared ORM models across the boundary. Reject Aurora/AlloyDB-only
extensions or semantics from the MVP portability baseline. Run the same SQL,
Alembic, constraint, isolation, failover and connection-pool suite on both.
```

### S9-P02B — controlled migration pipeline

```text
Build one CI/CD migration image/job using the provider-specific short-lived
migrator identity, exact image/SHA and Alembic
head verification. Run preflight backup/space/lock/long-transaction checks,
expand/backfill/enforce phases, compatibility smoke and post-head truth check.
Require approval for production and one migrator lock. Test empty/N-1/staging
snapshot/failed revision/forward repair on RDS and Cloud SQL; application
startup never auto-migrates and provider authentication is not schema behavior.
```

### S9-P02C — private object storage and lifecycle

```text
Provision per-environment private S3 and GCS implementations with provider KMS,
public-access prevention, versioning/retention as required, lifecycle for quarantine/temp/
outputs and narrowly scoped workload identities. Validate upload/download digest,
multipart cleanup and event/log policy. Test API/worker/notebook/connector access
matrix, wrong-tenant key prevention and recovery from deleted/changed object.
Give MLflow a dedicated non-public prefix/bucket policy; only MLflow and approved
ML/reconciliation roles may access it, and the browser never receives its paths
or signed URLs. Apply one adapter conformance suite for conditional writes,
opaque versions, retention/hold, digest, multipart/resumable cleanup, recovery
and fail-closed unsupported semantics.
```

### S9-P02D — secrets, KMS and rotation operations

```text
Provision AWS Secrets Manager/KMS and Google Secret Manager/Cloud KMS policies
for application/session/provider/connector/MLflow
credentials with versioning, workload-specific access, audit and rotation. IaC/
state/logs contain references only. Implement dual-read/safe rotation where the
application contract requires it and emergency revoke. Test each workload denial,
rotation during work, unavailable manager and no local-production fallback on
both. Application configuration stores versioned opaque SecretRef/KeyRef values,
never an ARN or Google resource name in public/domain state.
```

### S9-P02E — PITR, restore and reconciliation gate

```text
Execute timed DCLab and MLflow-backend PostgreSQL PITR to isolated AWS and GCP
staging, S3/GCS object version/backup restore, secret/KMS recovery, product row-object and
TrackingReference-to-MLflow reconciliation using documented runbooks.
Verify tenant/integrity/evidence locks, Alembic head, digests and application
read-only smoke after restore. Verify DCLab truth remains authoritative when
MLflow restore is behind or unavailable, and block verification/promotion until
bounded reconciliation closes any gap. Record achieved RPO/RTO, data gaps and owners;
repair procedures must not overwrite production during the drill. Record RDS
and Cloud SQL plus S3 and GCS results separately; same-cloud recovery does not
substitute for the cross-cloud drill in S9-P08E.
```

## Plan 9.3 — service and worker deployments

**Contract.** Deploy separate web/API/ML/agent/integration/notebook-control and
private MLflow units with distinct identity, handler allowlist, resources,
scaling and health. Deploy investigation after required S2-P12H. Optionally
deploy semantic-decision egress and `worker-nooa` only after their gates. Deep Agents is
isolated in that image and cannot enter API or agent
images; runtime authority is never shared. EKS and GKE use the same OCI digests,
Kubernetes base and application configuration schema.

### S9-P03A — immutable images and deployment units

```text
Create minimal non-root immutable images for web/API/worker roles with pinned
dependencies, read-only filesystem where practical, health/readiness and graceful
shutdown. Configure each worker with an explicit code-owned handler allowlist and
identity. Keep migrations/admin/debug tools out of runtime entrypoints. Build,
scan, sign and test images in CI; tag deployments by digest, not mutable tag.
The agent-worker image contains the reviewed LangGraph/checkpointer pins and no
PydanticAI, Deep Agents, high-level LangChain agent or unused alternate graph
runtime. Build a separate non-root worker-investigation image containing the
S2-P12 locked Deep Agents stack and `packages/dclab_client`, with only
investigation.copilot.v1 enabled; it contains no API private modules, raw
worker-agent graph, product DB/object/provider client or host shell tooling.
If S2-P14H allows it, build a separate non-root digest-pinned `worker-nooa`
image containing only the reviewed NOOA release and DCLab facade client. Prove
the API, worker-agent, worker-investigation and notebook-control images do not
import NOOA, and prove `worker-nooa` contains no provider/cloud/product-storage
SDK, Kubernetes client, Jupyter control client, shell entrypoint or writable
dependency cache. If S1-P12F allows Jev, keep `typesafe-sdk` and its secret only
in the semantic-decision gateway dependency/configuration group.
The ML worker allowlist includes only the reviewed model-build, batch-prediction
and model-monitor handlers; release activation remains an API/service command,
not a worker-selected action. Its dependency group pins MLflow client, Pandera,
Evidently, skops, DuckDB/PyArrow and approved native model runtimes; API/web/agent/MCP images do
not contain those packages. Deploy a pinned private MLflow service image with no
public ingress or DCLab application modules and with its dedicated backend/
artifact identity. Confirm W&B and Polars are absent from every MVP runtime/SBOM.
Image tests must prove no DuckDB CLI/server entrypoint or persistent catalog,
extension cache/install path, writable home dependency or unrestricted egress.
Push the same signed digest to ECR and Artifact Registry; rebuilding separately
for a provider is prohibited unless platform architecture differs and both
artifacts have equivalent provenance and tests.
```

### S9-P03B — deployment manifests and safe rollout

```text
Create `infra/kubernetes/base/` and minimal `overlays/aws`/`overlays/gcp` for
replicas/resources/ports/network/service accounts/config references/probes/
disruption. Render both from the same base. Use rolling/canary settings
that preserve API/worker schema compatibility; drain workers and leases on
termination. Define smoke, rollback and feature-flag ordering. Add manifest policy
tests for privilege, host mounts, public exposure and mutable images. Order
private checkpointer-schema compatibility before agent-worker rollout and keep
runtime rollback compatible with persisted checkpoints or use documented
forward repair. Roll out private MLflow and prove migration/health/isolation
before enabling tracking-required ML handlers; adapter rollback keeps durable
DCLab intents pending/degraded for reconciliation. If included, roll out worker-
investigation independently after its read API,
runtime-store and dependency conformance checks; stopping it must leave raw
LangGraph, deterministic ML and existing reads healthy.
Roll out optional semantic-decision egress and `worker-nooa` independently with
their own identity, NetworkPolicy, queue/handler allowlist, quotas, readiness,
flags and kill switches. Rollback must preserve immutable invocation/attempt
evidence while new Jev calls abstain/fall back to the deterministic path and new
NOOA requests are rejected safely; neither outage may affect required paths.
Map arbitrary-Python jobs only through the verified `SandboxRuntimePort`: the
approved EKS Fargate/equivalent profile and GKE Sandbox/gVisor must satisfy the
same no-token/no-host/no-egress/resource/quarantine tests.
```

### S9-P03C — autoscaling, quotas and tenant fairness

```text
Configure initial min/max capacity and scale signals from measured CPU/memory/
request latency/queue age by worker class. Enforce workspace/user concurrency,
job/resource quotas and backpressure before autoscaling. Protect PostgreSQL/
provider/object downstream connection limits. Give investigations explicit model/
tool/token/cost/time/context/result/concurrency ceilings; autoscaling never
bypasses gateway or workspace budgets. Load-test noisy tenant versus normal
tenant and cold scale behavior; record supported envelope and cost.
For DataScan jobs, cap concurrent connections per worker/workspace and enforce
memory, threads, input bytes, Arrow rows/bytes, temp bytes and wall time before
autoscaling. Demonstrate noisy scan isolation and bounded cancellation cleanup.
Express resource classes portably and map them to reviewed EKS/GKE node pools;
do not put EC2 or Compute Engine machine types in DCLab jobs. Record equivalent
capacity envelopes and normalized cost per workload on both providers.
```

### S9-P03D — graceful degradation and dependency failure

```text
Define behavior when provider, object store, secrets, telemetry, connector,
MLflow tracking, DataScan adapter, agent, Deep Investigation worker, Jev
semantic-decision provider, NOOA worker/private facade or notebook
service is unavailable. An investigation
failure returns a safe terminal/partial result and cannot impair worker-agent or
core authorized reads. MLflow failure cannot erase or contradict DCLab state:
the worker records pending/degraded sync, retries/reconciles idempotently and
prevents affected builds from becoming verified/promotable. Core authorized reads should degrade
honestly where safe; writes that cannot preserve invariants fail closed. Implement
timeouts/breakers/readiness that do not create restart storms. Test partial
dependency outages, region network loss, worker drain and recovery order.
DuckDB disabled/timeout/OOM/bound failures return stable DCLab scan states and
never cause an unrestricted pandas retry; only the explicit bounded rollback
flag selects pandas for a whole operation, with identical authorization/limits.
Run the dependency-loss matrix separately for AWS and GCP, including workload-
identity, managed PostgreSQL, object-store, secret/KMS, regional edge and
telemetry outages. Provider-native errors normalize to the same safe DCLab codes.
Jev timeout/malformed/low-confidence/rate-limit conditions must abstain or use
the documented deterministic alternative without converting uncertainty to an
allow decision. NOOA crash/lease loss/duplicate delivery must reconcile one
attempt and one proposal without replaying an uncertain tool operation.
```

### S9-P03E — deployment/capacity gate

```text
Deploy the same release digest to AWS and GCP staging via protected pipelines,
run migration then full smoke and
representative load/soak/worker-restart/node-loss tests. Verify handler/identity/
network separation, no secret in image/env output, bounded scaling and graceful
rollback to compatible image. Exercise verified model release, batch prediction,
monitoring window and rollback with immutable artifacts, including MLflow
timeout/replay/recovery and tracking-reference reconciliation. Prove MLflow
network/database/object isolation and ML-library image boundaries. Exercise one
explicit cited Deep Investigation proposal and
prove separate agent/investigation identities, networks, handlers, runtime
namespaces and image dependency graphs. Publish capacity,
costs, flags and runbooks before
production canary.
Run production-shaped Parquet/Arrow profile, aggregate-slice, leakage-candidate,
drift-window and bounded-notebook-query fixtures. Assert semantic parity,
deterministic digests, scan limits, cancellation/temp cleanup, no extension/
network/path/SQL escape, no persistent DuckDB artifact and no Polars package.
Record separate `AWS_STAGING_PASS` and `GCP_STAGING_PASS` evidence; one cannot
substitute for the other. Exercise provider overlay rollback without changing
the Kubernetes base or application/domain contracts.
```

## Plan 9.4 — observability, SLOs and operations

**Contract.** OpenTelemetry-compatible request-to-outcome correlation uses
bounded IDs/attributes. Logs are structured/redacted; metrics avoid tenant/user/
resource high-cardinality labels. SLOs come from product importance and observed
staging baselines.

### S9-P04A — telemetry architecture and context propagation

```text
Write telemetry ADR and instrument edge/BFF/API/services, DB, jobs/workers,
LLM/tools/agents, notebooks, connectors/outbox/actions with W3C trace context and
request/client/workspace-hash/resource IDs under a bounded policy. Define span/
event names and sampling. Prevent credentials, prompts, raw data/provider bodies,
signed URLs and arbitrary user labels. Instrument MLflow adapter calls,
reconciliation, feature validation, safe package load and drift calculation as
OpenTelemetry spans/metrics with bounded DCLab IDs/status only; do not use MLflow
as the operations tracing backend. Add propagation/redaction tests.
Instrument DataScan operation/template version, engine enum, input-size bucket,
Arrow batch/row/byte buckets, duration, cancellation, limit/denial reason and
cleanup outcome; never label metrics/logs with SQL, paths, column values or raw
resource IDs.
Deploy the same OpenTelemetry semantic schema and collector configuration on
EKS and GKE, with private exporter overlays for CloudWatch/X-Ray and Google
Cloud Logging/Monitoring/Trace where approved. Exporter failure cannot change
product behavior or audit truth.
```

### S9-P04B — metrics and structured logs

```text
Implement RED/USE plus domain metrics for auth/session, API, DB/pool, queue/lease,
ML/model-release/batch-prediction/drift, agent/eval/budget,
investigation proposal/citation/tool/gateway/budget/terminal reason, notebook/sandbox,
connector/freshness and action/outcome.
Include tracking sync pending/degraded/retry/age, package verification/rejection,
feature-contract reason and drift-calculator status/version without provider
locator, feature values or unbounded column/model labels.
Include DataScan queue age, active connections, memory/temp high-water buckets,
input/output bound rejections, timeouts, cancellations, cleanup failures and
parity canary result with fixed low-cardinality dimensions.
Use enumerated low-cardinality dimensions and exemplars/trace links where safe.
Centralize JSON log schema with safe reason codes and stack detail only in
protected sink. Add cardinality budget and canary-secret scans.
Allow `deployment_provider=aws|gcp` and region class only as bounded operations
dimensions; dashboards and alerts retain identical names/units on both clouds.
```

### S9-P04C — SLO and error-budget definitions

```text
Define user-journey SLIs/SLOs for authenticated API availability/latency, durable
job acceptance/completion/queue age, agent response, connector freshness, action
delivery, batch prediction, monitoring-window freshness, Investigation Copilot
time-to-validated-proposal/cancellation when enabled, tracking reconciliation
freshness and critical recovery,
with exclusions and windows. Derive targets from
business impact plus staging evidence; distinguish asynchronous latency. Define
error-budget policy that pauses risky releases/autonomy. Review with owners.
Use the same SLI definitions and target policy on AWS and GCP while recording
provider baselines separately; a weaker provider target needs an approved,
visible capability limitation and cannot be hidden in aggregate availability.
```

### S9-P04D — dashboards, alerts and runbooks

```text
Create role-specific dashboards and symptom-based alerts for SLO burn, auth/
tenant anomaly, DB/pool/replication, queue stuck, object errors, provider breaker,
agent cost/safety eval, Investigation Copilot unsupported claim/citation failure/
budget exhaustion/stuck job, model-release invalidation, batch failure/age, stale drift
window, rollback failure, connector stale and action ambiguous. Every alert links
owner/runbook/query/rollback and avoids secret/customer content. Test alert rules
against synthetic signals and remove unactionable noise.
Add actionable MLflow availability/sync-age/backlog and unsafe-package rejection
alerts; Investigation alerts are required when that MVP worker is deployed.
Provision equivalent AWS/GCP alert routes from the same normalized rules and
test provider delivery, deduplication and escalation without embedding customer
data or cloud resource names in product events.
```

### S9-P04E — observability and on-call gate

```text
Run a staging game day injecting API error/latency, DB pool exhaustion, stuck
worker, object/provider/secret outage, evaluation regression, Investigation
Copilot gateway/read-tool/runtime-store outage, stale connector and
ambiguous action. Verify alert detection, trace/log diagnosis, redaction,
escalation and runbook recovery within recorded time. Publish achieved SLO
baselines and unresolved telemetry gaps before pilot.
Always inject MLflow outage and reconciliation backlog plus feature-validator/
drift-calculator failure. Also inject DataScan timeout, configured-memory denial,
cancellation, temp cleanup failure and adapter disable; verify a stable error,
actionable alert and documented rollback without unbounded fallback. Inject all
three Deep Investigation mode failures in the production-MVP release.
Run the game day on AWS and GCP staging, including loss of workload identity,
RDS/Cloud SQL, S3/GCS and each cloud telemetry exporter. Record detection and
recovery separately and repair shared defects in the portable layer.
```

## Plan 9.5 — CI/CD and supply-chain security

**Contract.** Protected CI produces tested signed artifacts and provenance;
environments promote the same digest. Production changes require reviewed plan,
gates, approval, canary and automated/manual rollback.

### S9-P05A — required CI graph and branch protection contract

```text
Define required checks for formatting/lint/types, backend/SDK/CLI/MCP tests,
frontend component/build/browser, PostgreSQL migrations/integrity, OpenAPI/parity,
agent and Deep Investigation evals/isolation assertions plus the S2-P13F OpenAI
runtime decision/clean-disable assertions; add S1-P12F Jev calibration/privacy/
abstention/clean-disable and S2-P14H NOOA model-facade/isolation/recovery/clean-
disable assertions; add
Plan 3.0 ML-platform adapter/isolation/safe-package/feature/drift contract tests
and S0-P09 DataScan parity/security/resource/cleanup tests,
IaC validate/policy,
container/package scans and truth drift. Separate
trusted secret jobs from untrusted PRs and pin actions by digest. Document branch/
review/owner requirements and test no critical job is silently optional.
Require OpenTofu validation/plan/policy for both provider roots, Kustomize render
and Kubernetes policy checks for both overlays, S3/GCS adapter conformance and
static cloud-SDK import boundaries in network-free PR CI.
```

### S9-P05B — dependency, secret, vulnerability and license gates

```text
Generate lockfiles/SBOMs for Python/Node/images/IaC, scan dependencies/images/code/
IaC/secrets/licenses and define severity/exploitability/license policy with owner/
expiry for exceptions. Block known test canary secrets and unreviewed dependency
changes. Make scans reproducible/bounded and upload protected reports. Do not
auto-fix dependencies in release jobs. Treat LangGraph/checkpointer upgrades as
runtime migrations: require compatibility tests against persisted fixtures,
checkpoint-schema review and confirmation that no prohibited second agent
framework entered the API or worker-agent dependency graph. Deep Agents and its
LangChain/LangGraph transitive dependencies may exist only in the separately
locked worker-investigation image. Treat their upgrades as separate harness
migrations requiring replay/adversarial tests, compatibility proof, SBOM review
and independent rollback; never synchronize upgrades by importing one worker
into the other.
NOOA and `typesafe-sdk` may exist only in their separately reviewed dependency
groups/images. Pin exact package and model versions, verify official license and
transitive dependencies, and require replay, adversarial isolation, public-
contract compatibility, SBOM and clean-removal evidence for upgrades. Reject a
change that imports either provider into domain contracts or spreads provider
credentials into a general worker.
Pin MLflow/Pandera/Evidently/skops/native-runtime upgrades only in the ML-worker
or private tracking dependency groups. Require adapter/golden/serialization/
reconciliation and SBOM review for each upgrade. Reject W&B, Optuna, OpenLineage
and a second registry/monitoring service from MVP dependency graphs unless a
later Scope 10 decision explicitly activates one.
Pin DuckDB/PyArrow in the scan/ML-worker group and require template-parity,
Arrow-compatibility, resource/security and rollback evidence for upgrades.
Reject Polars and any DuckDB server/CLI/extension package from MVP images.
Pin and scan the AWS/Google/OpenTofu/Kubernetes providers, modules and CLIs;
provider upgrades require both plan diffs, compatibility evidence and an
independent rollback. Cloud SDKs may exist only in their approved adapter image.
```

### S9-P05C — build signing and provenance

```text
Build web/API/worker/notebook images and Python packages once in protected CI,
attach checksums/SBOM/provenance, sign with short-lived identity and verify before
deployment. Bind artifact to source/ref/workflow/dependency locks and prevent
fork/untrusted publication. Add signature/policy verification tests and compromised
key/build response runbook.
Publish the identical verified image digest to ECR and Artifact Registry using
short-lived CI federation. Record registry digests and prove admission rejects
unsigned, wrong-source or mutable-tag images on EKS and GKE.
```

### S9-P05D — environment promotion pipeline

```text
Promote the exact signed digest dev -> staging -> production with environment-
specific config references, migration preflight, smoke/e2e/eval/security/SLO gates
and required approval. Record deployment, release/policy/graph versions and
LangGraph/checkpointer versions plus the separate Deep Agents/harness/runtime-
state versions when included, plus MLflow service/client, tracking-schema,
Pandera compiler, Evidently calculator and model-loader policy versions and
rollback target. Prevent concurrent
incompatible product/checkpointer deploy/migration and direct mutable production
apply. Test dry run and failed gate.
Promote AWS and GCP independently from the same application release. Each
provider has isolated state, approval, config references and deployment record;
failure of one cannot authorize bypassing the other's gate or rebuilding code.
```

### S9-P05E — canary and rollback automation

```text
Implement bounded production canary by traffic/workspace/feature with automatic
monitoring of error budget, auth/tenant, scientific, agent/Investigation Copilot
safety, queue and cost
signals when those components are enabled, plus MLflow tracking-sync/package/
feature-validation/drift health. Roll back image/config/release flags in compatible order; migrations use
forward repair unless verified reversible. Exercise bad image/config/policy and
stuck canary. Preserve deployment evidence.
Run the same canary assertions through AWS and GCP edges, with provider-specific
infrastructure rollback and common application/config/feature rollback. Never
shift traffic across clouds automatically or without the cross-cloud runbook.
```

### S9-P05F — delivery pipeline gate

```text
Run a release candidate from clean source through all required checks, signing,
staging promotion, canary simulation and rollback without developer credentials.
Verify artifact digest remains identical, approvals/audit are complete, no secret
enters logs/artifacts and bypass paths are blocked. Publish release checklist,
owners and observed timing before first production deploy.
Complete this rehearsal for AWS and GCP with short-lived federation and no
developer cloud credentials. Record provider-specific duration/cost and ensure
destroy/rollback targets only the exact disposable or canary environment.
```

## Plan 9.6 — security, privacy and incident readiness

**Contract.** Validate application, agent, connector/action, notebook and cloud
boundaries together. Privacy lifecycle covers database, objects, caches, logs,
evaluations/backups and derived artifacts with documented legal holds.

### S9-P06A — production threat-model reconciliation

```text
Reconcile all scope threat models with deployed data/network/identity flows and
inventory assets, principals, trust boundaries, entry/egress, abuse cases and
kill switches. Map each risk to control/evidence/owner/residual rating. Include
tenant/auth, LLM/tool/delegation, sandbox, OAuth/MCP, connector secrets/SSRF,
outbox/approval and supply chain. Include checkpoint forgery, product/runtime
state divergence, replay after interrupt, nested-loop amplification and
framework dependency compromise. Include accidental raw-graph/Deep-Agent
invocation, shared checkpoint or identity, host filesystem/shell exposure and
poisoned read-tool output. Block launch on unowned critical risk.
Include MLflow endpoint/credential exposure, cross-tenant tracking metadata,
artifact-prefix confusion, forged tracking reconciliation, unsafe model
deserialization and compromised validation/drift dependencies. Verify these
cannot change DCLab lifecycle, promotion, approval or audit authority.
Include SQL/template injection, artifact-path substitution, symlink/path escape,
extension auto-install/load, external-access bypass, malicious Parquet metadata,
decompression/memory/temp exhaustion, cross-tenant artifact substitution and
cancel/cleanup races at DataScanPort. Prove the DuckDB process has no independent
identity/egress/store and normalized results cannot bypass deterministic policy.
Include AWS/GCP identity confusion, metadata credential theft, public data
plane, cross-account/project access, provider-ID leakage, IaC state compromise,
overlay drift and unsafe cross-cloud recovery. Map each control to both clouds
or record a provider-specific residual risk.
```

### S9-P06B — application/cloud security verification

```text
Run SAST/dependency/image/IaC/secret scans plus DAST and manual tests for auth/
session/CSRF, access control/tenant ID substitution, injection, SSRF, uploads,
OAuth/MCP, rate/resource abuse, agent tools and action approvals. Validate cloud
public access/IAM/network/KMS/logging. Track findings with severity, owner, SLA
and retest; no unresolved P0/P1 at launch.
Run the same security matrix against EKS/RDS/S3/Secrets Manager/KMS and GKE/
Cloud SQL/GCS/Secret Manager/Cloud KMS. Validate short-lived workload identity,
metadata denial, private data planes and admission/network policies on both.
```

### S9-P06C — privacy retention and deletion implementation

```text
Complete policy-driven retention/deletion across user/session, datasets/columns,
objects/quarantine, runs/artifacts, prompts/context/messages/tool results,
Investigation Copilot proposals and short-retention working/runtime state,
notebooks, connector/action/outcome/eval records, provider-neutral tracking
references, corresponding MLflow metadata/artifacts, telemetry and backups.
Delete external tracking data through an idempotent outbox/reconciliation job;
retain only the policy-required DCLab tombstone/digest and never report complete
while a required external deletion is unresolved. Respect
holds/immutable audit through tombstone/minimal skeleton where required. Use
idempotent durable jobs, reconciliation and evidence. Test partial failure,
recreate/delete race and two workspaces.
Exercise equivalent deletion and legal-hold/retention outcomes in S3 and GCS,
RDS and Cloud SQL backups, cloud logs and secret versions. Provider retention
limits must be explicit; no cloud may silently report deletion complete.
```

### S9-P06D — backup/restore and disaster exercise

```text
Execute production-shaped loss scenarios for DB, object metadata/body, secrets/
KMS, region/service and corrupted deployment. Restore into isolated environment,
reconcile DCLab-to-MLflow references plus digests/lineage, rotate compromised
credentials and resume safely. Restore ordering must keep builds unverified/
unpromotable until tracking/package reconciliation passes.
Measure RPO/RTO and data/effect ambiguity, including outbox/actions. Update runbooks
from observed gaps; never use production writes in drills without approval.
Run same-provider restore on AWS and GCP and the controlled AWS-to-GCP or GCP-to-
AWS portability drill defined by S9-P08E. Recreate destination secrets/keys and
verify IDs, versions, digests and evidence rather than copying credentials.
```

### S9-P06E — incident response and evidence handling

```text
Define severity/on-call/escalation, containment flags, credential/session revoke,
tenant notification decision, forensic audit preservation, privacy/regulatory
timelines and post-incident review. Create playbooks for cross-tenant exposure,
provider/secret compromise, sandbox escape, runaway agent cost, duplicate action
and supply-chain compromise. Include Investigation Copilot credential/runtime-
state compromise, runaway cost and emergency isolation without disabling the
authoritative agent or ML path. Include
MLflow credential/service compromise, isolation, credential rotation, tracking
disable and safe reconciliation without surrendering DCLab authority. Run
tabletop plus one technical drill.
Include AWS-account and GCP-project compromise, IaC-state loss, workload-identity
abuse and accidental cross-cloud cutover. Each playbook names provider contacts,
evidence sources, containment controls and the safe non-automatic alternative.
```

### S9-P06F — independent readiness gate

```text
Obtain independent security/privacy review or penetration test for the supported
pilot boundary, remediate/retest launch blockers and document accepted residual
risks with accountable owner/expiry. Verify deletion and restore evidence,
incident contacts, kill switches and audit export. Publish a go/no-go artifact;
do not broaden scope to avoid a failed control.
Issue separate AWS and GCP findings/evidence and a combined portability verdict.
One provider can remain blocked without weakening controls or mislabeling it as
supported.
```

## Plan 9.7 — allowlisted pilot and release decision

**Contract.** Pilot has named Data Scientist and ML Engineer participants,
workspaces, use case, quotas, support and success/safety metrics. It exercises
the Core ML lifecycle through connector ingest, specialist/Deep Investigation,
isolated Python, hosted MCP, batch release/monitoring/rollback and SDK/CLI. The
S2-P13 OpenAI adapter is exercised only if its recorded decision permits a
canary; clean disable is always exercised. Jev and NOOA are likewise exercised
only for an exact S1-P12F/S2-P14H allow decision; when included, their recorded
decision and clean-disable evidence are reviewed, otherwise absence is proven.
Business action and outcome are
optional and included only when the charter names them. The
pilot is reversible and does not imply general availability or higher autonomy.
The human pilot may use one approved primary cloud, but the identical release
must complete the automated golden-path canary on the second cloud. Plan 9.8,
not pilot success alone, certifies AWS/GCP production support.

### S9-P07A — pilot charter and eligibility

```text
Define separate Data Scientist and ML Engineer jobs, use case, included/excluded
scopes, primary cloud/region, second-cloud conformance region, data residency,
maximum users/workspaces/datasets/size/jobs/agent cost/notebook/action value,
onboarding/offboarding, consent/support and stop criteria. Set measurable workflow,
time-to-valid-baseline, Investigation Copilot usefulness/citation correctness/
unsupported-claim rate/cost, constraint satisfaction,
reproducibility, batch release/rollback, quality, reliability, safety and
business-learning goals with owner/window. Include separate measures for all
three Deep Investigation modes, specialist value/ablation, connector freshness/
schema drift, sandbox escape/resource control and hosted MCP authority/parity.
Always include tracking-sync
completeness/latency, safe-package rejection, feature-contract correctness and
drift-alert usefulness without exposing provider internals.
Configure server-side allowlists/flags/quotas; no marketing availability claim.
Define separate optional success thresholds for Jev calibration/abstention/
latency/cost and NOOA proposal quality/citation/isolation/recovery. Do not count
either optional integration as required workflow success or permit a provider
failure to be hidden by aggregate agent metrics.
Define provider-neutral success thresholds plus separately reported AWS/GCP
latency, capacity, egress and cost; do not average away a provider failure.
```

### S9-P07B — production onboarding and data readiness

```text
Onboard pilot identity/membership/service credentials through supported flows,
verify workspace isolation/classification/retention and provider least privilege,
and run a non-destructive connectivity/upload/sync readiness check. Train users
on approvals, limitations, reporting and support. Record no raw secrets/customer
data in planning evidence. Validate offboarding/revoke/delete path before use.
Validate the deployment profile, workload identities, private data planes,
object/version semantics and support contacts for both providers. No pilot data
is copied across clouds unless its residency/consent policy explicitly permits it.
```

### S9-P07C — full supported workflow pilot

```text
Have a data scientist complete connector/upload ingest -> lifecycle -> goal/business constraints
-> profile/investigation -> plan -> approved build -> compare/improve -> decision
-> evidence/implementation/reproduction. Have an ML engineer complete the same
resource path through SDK/CLI, then verified model registration -> batch prediction
-> monitoring investigation -> rollback, using no DB intervention. Each persona
uses the same DCLab resources while MLflow remains private and provider-neutral;
exercise tracking outage/recovery, strict feature validation and one safe
rollback. Each persona runs the applicable dataset_scientific,
experiment_model and operations_drift Deep Investigation modes, reviews their
claim-cited proposals and proves acceptance creates a decision or separate
canonical command rather than an automatic effect. Exercise one isolated Python
cell in the signed sandbox and one hosted MCP read journey plus an exact-approved
low-risk write when the MCP write manifest permits it. Exercise S3/GCS object, SQL,
selected CRM and Snowflake connector conformance with synthetic or approved
pilot data. If S2-P13F permits a canary, run its named purpose and prove
one-runtime ownership/required-action mediation; otherwise prove it is absent
and core behavior is unchanged. If S1-P12F permits a pilot Jev purpose, run only
its pinned question/model/data-policy release and compare the recorded
probability/confidence/abstention with the deterministic baseline and eventual
reviewed outcome. If S2-P14H permits a NOOA canary, run only the released agent
class through `worker-nooa`, verify private DCLab model/tool facades and review
the validated proposal without automatic apply or notebook execution. Otherwise
prove both are absent and the same workflow remains healthy. Exercise all
three synchronized views and reconstruct project decisions. Add recommendation
-> approved business action -> outcome only when included by the charter. Capture
telemetry/evaluation/support issues and user feedback under policy. Do not
manually hide failed states.
During ingest/investigation, exercise schema/profile and an aggregate slice over
production-shaped Parquet/Arrow through the public typed interface. During
monitoring, exercise bounded drift-window preparation. Record latency/memory/
throughput and verify users/agents submit no SQL, DuckDB remains ephemeral,
limits/cancellation are visible, results are cited, and Polars is absent.
Run the complete human journey on the chartered primary cloud and an automated
synthetic golden-path journey with the same release/config contract on the
second cloud. A failure is recorded as a provider blocker, not patched with
cloud-specific product behavior.
```

### S9-P07D — soak, support and operational review

```text
Operate for the charter window, reviewing SLO/error budget, incidents, auth/
tenant anomalies, scientific/eval safety, Deep Investigation citation/value/
cost/cancellation/isolation, cost/quota, connector freshness, action
ambiguity, outcome coverage and support burden. Exercise one rollback/kill switch
and one restore/recovery drill. Include lifecycle reconstruction, decision-memory
correctness, batch feature skew and drift-alert usefulness. Triage defects by
severity and pause on stop
criteria. Record denominator/context for metrics.
When enabled, review Jev calibration drift, confidence/abstention, malformed/
timeout fallback and data-policy compliance plus NOOA proposal value, generated-
code isolation, model/tool budget, duplicate/crash recovery and clean disable.
Review all three Deep Investigation modes, specialist ablations, connector-pack
freshness/schema events, isolated-Python safety and hosted-MCP authority. Always review MLflow
sync backlog/availability, package-verification failures, feature-contract
rejections and Evidently-calculator status through DCLab/OpenTelemetry views.
Also review DataScan parity, p95 duration, limit/timeout/cancel/cleanup rates,
worker memory/temp high-water marks and explicit pandas rollback readiness.
Compare AWS/GCP staging SLO, resource, identity, object, database and cost
evidence and keep provider-specific incidents visible. Exercise independent
provider rollback; do not test unapproved automatic traffic/data failover.
```

### S9-P07E — production go/no-go and rollback

```text
Compare observed pilot evidence to every charter and Scope 9 gate. List blockers,
accepted limitations, capacity/cost, residual risks, owners and next milestone.
Require explicit sign-off from the Data Scientist and ML Engineer workflow owners;
neither infrastructure-only success nor optional business-side completion can
hide a failure in the Core ML path.
Require S2-P12H, S4-P07E, S6-P05E and S7-P06E evidence plus the recorded S2-P13F
decision. For each pilot-included Jev/NOOA purpose, require S1-P12F/S2-P14H;
otherwise record it `NOT_APPLICABLE` and prove it is absent. Activation is
optional, but any allowed purpose must have passed its exact gate and every
disabled/rejected path must be cleanly absent. Verify no initial feature depends
on SHAP and no runtime nesting,
Airbyte/Openflow control plane or Snowflake product-state authority was added.
Approve continue/expand/pause/rollback explicitly; expansion names a bounded new
population, never automatic GA. Publish immutable release/evidence versions and
execute offboarding/rollback if no-go.
This is the user-pilot decision. Do not claim dual-cloud production readiness
until S9-P08F records both provider verdicts and the cross-cloud restore result.
```

## Plan 9.8 — AWS/GCP portability certification and release

**Contract.** Certify AWS and Google Cloud as independent production targets for
the same DCLab application release. Reuse Plans 9.1–9.7 and repair portability
defects through their existing owners; do not create a second product stack.
Each provider has isolated accounts/projects, IaC state, identities, secrets,
keys, data and release status. The gate proves same-contract operation and
controlled cross-cloud restoration, not active-active writes or automatic
failover.

Use planned homes `infra/tofu/modules/aws/`, `infra/tofu/modules/gcp/`,
`infra/tofu/stacks/{aws,gcp}/`, `infra/kubernetes/base/`,
`infra/kubernetes/overlays/{aws,gcp}/`, `tests/infra/`,
`apps/api/tests/test_object_storage_lineage.py` and
`docs/verification/cloud/`. Locate and reuse their actual replacements if
earlier prompts selected different reviewed names.

### S9-P08A — portability inventory and platform-contract freeze

```text
Read AWS_GCP_DEPLOYMENT_ARCHITECTURE.md,
EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md and the RT7/RT8 supplement. Inspect
every application cloud SDK,
storage/secret/config adapter, Dockerfile, Compose service, IaC module, manifest,
CI workflow, deployment runbook and provider-shaped database/API field. Produce
one versioned CloudCapabilityMatrix covering web/BFF, API, PostgreSQL jobs,
ML/DataScan, MLflow, agent, Deep Investigation, optional OpenAI adapter,
connectors, isolated Python, MCP, migrations, observability, backup/deletion and
edge behavior. For each capability record its provider-neutral owner, AWS
implementation, GCP implementation, security/consistency/limit differences,
test/evidence owner, feature flag, kill switch and release state. Freeze an
internal typed DeploymentProfile and opaque ObjectVersionRef/SecretRef/KeyRef/
ImageReference contract; do not add cloud fields to public domain resources.
Add static dependency rules forbidding boto3/google-cloud/Kubernetes imports
outside approved adapters/infra and rejecting ARN, google resource-name, bucket
URL or cluster-name fields in domain/OpenAPI/SDK models. Record exact provider,
Kubernetes and module pins to be verified, not guessed. This prompt performs no
cloud apply, migration or data copy. Maximum change: contract, ADR/matrix and
static-test skeleton under 600 hand-edited lines.
Keep the AWS/GCP home capability matrix distinct from the external execution
readiness matrix in RT8-F. Extend import rules to reject Runpod/Railway/Lambda/
Vast/Nebius SDK or HTTP client usage outside private compute adapters and to keep
all provider resource identifiers out of public/domain contracts.
```

### S9-P08B — offline IaC, manifest and adapter parity gate

```text
Implement a network-free conformance harness under tests/infra that runs
OpenTofu format/validate and produces plan JSON for every AWS/GCP environment
using non-secret fixtures; verifies provider/module locks and encrypted isolated
state configuration; renders the shared Kubernetes base with both overlays; and
validates schemas/security policies. Assert equivalent workload families,
immutable image digests, service accounts, probes, resources, disruption/drain,
network intent, secret references, public/private exposure and rollback labels.
Permit provider-only resources but map them to the same capability ID and policy
assertion. Extend S3/GCS, secret/KMS and safe-provider-error adapter tests for
version/precondition, digest, retention, signed-access expiry, credential
absence/rotation and outage behavior. Add import scans proving cloud SDKs occur
only in approved images. Fail on plan apply, live credentials, public database/
bucket, wildcard identity, mutable tag, host privilege, default service-token
mount or capability missing from one overlay. Document one refresh/review path
for intentional plan snapshots. Keep generated plan output untracked. Maximum
change: one conformance harness and fixtures under approximately 900 non-
generated lines; split adapter expansions into a separate PR if needed.
```

### S9-P08C — AWS staging and production-target gate

```text
Using a protected pipeline and short-lived federation, apply only the explicitly
approved AWS staging/disposable stack. Verify AWS account/region/state isolation,
EKS control/data planes, per-workload EKS Pod Identity or justified IRSA,
metadata denial, RDS PostgreSQL Multi-AZ, private S3/versioning/retention/KMS,
Secrets Manager version/rotation, ECR digest/admission, private MLflow, edge TLS/
WAF/proxy behavior, OpenTelemetry export, budgets/tags and audit logs. Deploy the
signed release digest, run migrations, two-workspace/security tests, DS/ML
synthetic golden path, connector S3/GCS reachability policy, agent/runtime
isolation, batch release/rollback, hosted MCP and sandbox conformance. The
arbitrary-Python profile must use the approved Fargate/equivalent isolation and
cannot inherit node credentials or fall back. Exercise zonal/node/pod/identity/
RDS/S3/secret/egress outage, drain, canary and rollback; measure SLO/capacity/
cost/RTO/RPO. Capture redacted evidence and destroy only the exact disposable
stack when its plan and approval say so. Record `AWS_READY` or `AWS_BLOCKED` with
owners, limitations and rollback. Do not alter product contracts to pass AWS.
```

### S9-P08D — GCP staging and production-target gate

```text
Using a protected pipeline and Workload Identity Federation, apply only the
explicitly approved GCP staging/disposable stack. Verify project/region/state
isolation, GKE control/data planes, per-workload Workload Identity Federation,
metadata denial, Cloud SQL PostgreSQL HA, private GCS/versioning/retention/Cloud
KMS, Secret Manager version/rotation, Artifact Registry digest/admission,
private MLflow, edge TLS/Cloud Armor/proxy behavior, OpenTelemetry export,
budgets/labels and audit logs. Deploy the same signed release digest used by
AWS, then run the identical migrations, tenant/security and DS/ML synthetic
golden-path suite, connectors, agent/runtime isolation, batch/rollback, MCP and
sandbox tests. Arbitrary Python must run with the verified GKE Sandbox/gVisor
profile, no service-account token and no ordinary-worker fallback. Exercise
zonal/node/pod/identity/Cloud-SQL/GCS/secret/egress outage, drain, canary and
rollback; measure the same SLO/capacity/cost/RTO/RPO fields. Capture redacted
evidence and destroy only the exact approved disposable stack. Record
`GCP_READY` or `GCP_BLOCKED` with owners, limitations and rollback. Do not alter
product contracts to pass GCP.
```

### S9-P08E — bidirectional cross-cloud restore drill

```text
Create synthetic/de-identified projects containing memberships, decision
memory, dataset/object versions, model build/package, MLflow references, agent
and Deep Investigation history, connector checkpoints, notebook artifacts,
batch prediction, monitoring and audit evidence. In isolated non-production
environments, run AWS-to-GCP and GCP-to-AWS restore using a standard PostgreSQL
logical export, explicit Alembic/schema manifest and digest-verified immutable
object manifest. Quiesce the source, capture a bound, copy no secret plaintext
or KMS key material, create new destination SecretRef/KeyRef mappings, preserve
DCLab IDs/versions/content digests and translate only private deployment
references. Reconcile row/object counts, evidence locks, MLflow references,
checkpoints, pending jobs/outbox, connector cursors, retention/legal holds and
model-package load policy before enabling writes. Run read-only smoke, then
approved write/idempotency/cancel/rollback tests; prove no duplicate/lost
publication or cross-tenant access. Measure export/copy/restore/reconciliation/
DNS-cutover estimates and achieved portable RPO/RTO. Test corrupt/missing
object, partial import, incompatible extension, interrupted copy and reversal.
Publish a manual cutover/reversal runbook and cleanup manifest. Do not touch
production DNS/data or claim automatic/zero-RPO failover.
```

### S9-P08F — combined dual-cloud release decision

```text
Aggregate S9-P08A–E with all Scope 9 security, privacy, load, deletion, backup,
pilot and supply-chain evidence. Compare exact application/image/schema/tool/
policy releases and require both clouds to satisfy the same tenant, scientific,
agent, connector, sandbox, MCP, model-release, observability and rollback hard
gates. Review provider-specific service limits, quotas, region availability,
costs, residual risks and operational ownership without averaging results.
Record independent `AWS_READY|AWS_BLOCKED` and `GCP_READY|GCP_BLOCKED` states and
one `DUAL_CLOUD_READY` state only when both are ready and the bidirectional
restore drill passes. A blocked cloud remains unadvertised and disabled; never
weaken a control, add a provider condition to product logic or call one-cloud
success dual-cloud support. Exercise independent provider kill switch and
rollback while the other environment remains healthy. Publish immutable
capability matrix, evidence digests, dashboards, alerts, runbooks, support
owners, re-certification triggers and next review date. This prompt may repair
bounded release-tooling defects but performs no unapproved production apply,
data movement or DNS cutover.
```
