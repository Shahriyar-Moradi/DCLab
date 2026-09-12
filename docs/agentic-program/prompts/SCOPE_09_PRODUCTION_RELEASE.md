# Scope 9 execution prompts — production platform and measured pilot

Start when the product scopes included in the pilot have passed their gates.
Apply `README.md` and `EXECUTION_STANDARD.md`. Choose cloud/platform/region from
approved product, security, residency, skills and cost requirements; do not
invent an infrastructure vendor. All infrastructure changes require reviewed
plans and environment approval.

## Scope implementation boundary

Create a reviewed `infra/` layout only after the platform ADR. Separate dev, CI,
staging and production accounts/projects, state, identities and secrets. Deploy
web, API and handler-allowlisted workers independently. Notebook compute remains
isolated or disabled. Managed PostgreSQL/object/secrets/telemetry are private.
Run the pinned LangGraph runtime only inside `worker-agent`; its dedicated
PostgreSQL checkpointer schema uses a least-privilege identity and is not public.
Do not deploy LangGraph Agent Server or a second agent-facing API for the MVP.

## Plan 9.1 — environment and infrastructure architecture

**Contract.** One IaC tool/provider/version is selected and pinned. Environments
have separate state/credentials and least-privilege workload identities. Network,
DNS/TLS/WAF and configuration are code-reviewed and reproducible.

### S9-P01A — requirements and platform decision ADR

```text
Collect target regions/residency, availability/RTO/RPO, workload/storage/egress,
isolation, managed-service, team-skill, compliance and cost constraints. Compare
viable platforms and choose provider/region/IaC tool/state/locking with rejected
alternatives, ownership and exit cost. Define production release boundary and
which optional scopes are excluded. Stop for stakeholder decision where business
requirements are absent; do not assume Kubernetes.
```

### S9-P01B — account/project and environment bootstrap

```text
Create IaC modules/stacks for separate dev/CI/staging/production accounts/projects,
remote encrypted locked state, CI federation, break-glass role and least-privilege
operators. Pin providers/modules and validate tags/owners/budgets/regions. No
long-lived cloud key in repository/CI. Add format/validate/policy/plan tests and
document bootstrap/recovery without applying production in an unapproved prompt.
```

### S9-P01C — network, DNS, TLS and edge controls

```text
Define private database/object/secrets networks/endpoints, controlled worker
egress, public web/API/MCP ingress, load balancer/WAF/rate limits, trusted proxy
headers, DNS and managed TLS rotation. Separate notebook and connector egress
policies. Deny public management/data planes by default. Add IaC policy tests for
open CIDRs/public buckets/plain HTTP/metadata and diagram flows.
```

### S9-P01D — workload identity and configuration contract

```text
Create distinct workload identities for web, API, ML, agent, integration,
notebook-control and migrations with only required DB/schema/object/secret/queue/
telemetry permissions. Define typed environment config source, validation,
versioning and rollout; no shared admin credential. Test forbidden cross-role
access in staging and production-default fail closed. Document rotation/break-glass.
```

### S9-P01E — environment architecture gate

```text
Run IaC validate/plan/policy/security/cost checks for every environment; review
state isolation, network reachability, identities, DNS/TLS and disaster boundary.
Perform staging bootstrap/destroy of a disposable environment where approved and
record observed outputs. Publish architecture/owners/risks/rollback. Do not apply
production until subsequent data/deployment/security gates.
```

## Plan 9.2 — managed data, object and secret operations

**Contract.** PostgreSQL is authoritative; object storage is private/immutable-
aware; secrets/KMS are managed. Migrations run as a separate controlled identity.
Backup/restore and object/row reconciliation are tested, not assumed.

### S9-P02A — managed PostgreSQL and access roles

```text
Provision encrypted private managed PostgreSQL at the supported version with
multi-zone/maintenance/backups/logging settings based on RTO/RPO and cost. Create
separate owner/migrator/application/read-operator roles, TLS enforcement and
PgBouncer/pooling configuration when required. Deny public/network and superuser
application access. Test connectivity/permission matrix and failover behavior in
staging.
```

### S9-P02B — controlled migration pipeline

```text
Build a CI/CD migration job using migrator identity, exact image/SHA and Alembic
head verification. Run preflight backup/space/lock/long-transaction checks,
expand/backfill/enforce phases, compatibility smoke and post-head truth check.
Require approval for production and one migrator lock. Test empty/N-1/staging
snapshot/failed revision/forward repair; application startup never auto-migrates.
```

### S9-P02C — private object storage and lifecycle

```text
Provision per-environment private object storage with encryption/KMS, public-
access block, versioning/retention as required, lifecycle for quarantine/temp/
outputs and narrowly scoped workload identities. Validate upload/download digest,
multipart cleanup and event/log policy. Test API/worker/notebook/connector access
matrix, wrong-tenant key prevention and recovery from deleted/changed object.
```

### S9-P02D — secrets, KMS and rotation operations

```text
Provision managed secrets/KMS policies for application/session/provider/connector
credentials with versioning, workload-specific access, audit and rotation. IaC/
state/logs contain references only. Implement dual-read/safe rotation where the
application contract requires it and emergency revoke. Test each workload denial,
rotation during work, unavailable manager and no local-production fallback.
```

### S9-P02E — PITR, restore and reconciliation gate

```text
Execute timed PostgreSQL PITR to isolated staging, object version/backup restore,
secret/KMS recovery and row-object reconciliation using documented runbooks.
Verify tenant/integrity/evidence locks, Alembic head, digests and application
read-only smoke after restore. Record achieved RPO/RTO, data gaps and owners;
repair procedures must not overwrite production during the drill.
```

## Plan 9.3 — service and worker deployments

**Contract.** Deploy separate web/API/ML/agent/integration/notebook-control units
with distinct identity, handler allowlist, resources, scaling and health. One
image may be shared initially but runtime authority is not.

### S9-P03A — immutable images and deployment units

```text
Create minimal non-root immutable images for web/API/worker roles with pinned
dependencies, read-only filesystem where practical, health/readiness and graceful
shutdown. Configure each worker with an explicit code-owned handler allowlist and
identity. Keep migrations/admin/debug tools out of runtime entrypoints. Build,
scan, sign and test images in CI; tag deployments by digest, not mutable tag.
The agent-worker image contains the reviewed LangGraph/checkpointer pins and no
PydanticAI, high-level LangChain agent or unused alternate graph runtime.
The ML worker allowlist includes only the reviewed model-build, batch-prediction
and model-monitor handlers; release activation remains an API/service command,
not a worker-selected action.
```

### S9-P03B — deployment manifests and safe rollout

```text
Codify replicas/resources/ports/network/service accounts/config/secrets/probes/
disruption for each unit in selected IaC/platform. Use rolling/canary settings
that preserve API/worker schema compatibility; drain workers and leases on
termination. Define smoke, rollback and feature-flag ordering. Add manifest policy
tests for privilege, host mounts, public exposure and mutable images. Order
private checkpointer-schema compatibility before agent-worker rollout and keep
runtime rollback compatible with persisted checkpoints or use documented
forward repair.
```

### S9-P03C — autoscaling, quotas and tenant fairness

```text
Configure initial min/max capacity and scale signals from measured CPU/memory/
request latency/queue age by worker class. Enforce workspace/user concurrency,
job/resource quotas and backpressure before autoscaling. Protect PostgreSQL/
provider/object downstream connection limits. Load-test noisy tenant versus normal
tenant and cold scale behavior; record supported envelope and cost.
```

### S9-P03D — graceful degradation and dependency failure

```text
Define behavior when provider, object store, secrets, telemetry, connector,
agent or notebook service is unavailable. Core authorized reads should degrade
honestly where safe; writes that cannot preserve invariants fail closed. Implement
timeouts/breakers/readiness that do not create restart storms. Test partial
dependency outages, region network loss, worker drain and recovery order.
```

### S9-P03E — deployment/capacity gate

```text
Deploy to staging via protected pipeline, run migration then full smoke and
representative load/soak/worker-restart/node-loss tests. Verify handler/identity/
network separation, no secret in image/env output, bounded scaling and graceful
rollback to compatible image. Exercise verified model release, batch prediction,
monitoring window and rollback with immutable artifacts. Publish capacity, costs, flags and runbooks before
production canary.
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
signed URLs and arbitrary user labels. Add propagation/redaction tests.
```

### S9-P04B — metrics and structured logs

```text
Implement RED/USE plus domain metrics for auth/session, API, DB/pool, queue/lease,
ML/model-release/batch-prediction/drift, agent/eval/budget, notebook/sandbox,
connector/freshness and action/outcome.
Use enumerated low-cardinality dimensions and exemplars/trace links where safe.
Centralize JSON log schema with safe reason codes and stack detail only in
protected sink. Add cardinality budget and canary-secret scans.
```

### S9-P04C — SLO and error-budget definitions

```text
Define user-journey SLIs/SLOs for authenticated API availability/latency, durable
job acceptance/completion/queue age, agent response, connector freshness, action
delivery, batch prediction, monitoring-window freshness and critical recovery,
with exclusions and windows. Derive targets from
business impact plus staging evidence; distinguish asynchronous latency. Define
error-budget policy that pauses risky releases/autonomy. Review with owners.
```

### S9-P04D — dashboards, alerts and runbooks

```text
Create role-specific dashboards and symptom-based alerts for SLO burn, auth/
tenant anomaly, DB/pool/replication, queue stuck, object errors, provider breaker,
agent cost/safety eval, model-release invalidation, batch failure/age, stale drift
window, rollback failure, connector stale and action ambiguous. Every alert links
owner/runbook/query/rollback and avoids secret/customer content. Test alert rules
against synthetic signals and remove unactionable noise.
```

### S9-P04E — observability and on-call gate

```text
Run a staging game day injecting API error/latency, DB pool exhaustion, stuck
worker, object/provider/secret outage, evaluation regression, stale connector and
ambiguous action. Verify alert detection, trace/log diagnosis, redaction,
escalation and runbook recovery within recorded time. Publish achieved SLO
baselines and unresolved telemetry gaps before pilot.
```

## Plan 9.5 — CI/CD and supply-chain security

**Contract.** Protected CI produces tested signed artifacts and provenance;
environments promote the same digest. Production changes require reviewed plan,
gates, approval, canary and automated/manual rollback.

### S9-P05A — required CI graph and branch protection contract

```text
Define required checks for formatting/lint/types, backend/SDK/CLI/MCP tests,
frontend component/build/browser, PostgreSQL migrations/integrity, OpenAPI/parity,
agent evals, IaC validate/policy, container/package scans and truth drift. Separate
trusted secret jobs from untrusted PRs and pin actions by digest. Document branch/
review/owner requirements and test no critical job is silently optional.
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
framework entered the dependency graph.
```

### S9-P05C — build signing and provenance

```text
Build web/API/worker/notebook images and Python packages once in protected CI,
attach checksums/SBOM/provenance, sign with short-lived identity and verify before
deployment. Bind artifact to source/ref/workflow/dependency locks and prevent
fork/untrusted publication. Add signature/policy verification tests and compromised
key/build response runbook.
```

### S9-P05D — environment promotion pipeline

```text
Promote the exact signed digest dev -> staging -> production with environment-
specific config references, migration preflight, smoke/e2e/eval/security/SLO gates
and required approval. Record deployment, release/policy/graph versions and
LangGraph/checkpointer versions plus rollback target. Prevent concurrent
incompatible product/checkpointer deploy/migration and direct mutable production
apply. Test dry run and failed gate.
```

### S9-P05E — canary and rollback automation

```text
Implement bounded production canary by traffic/workspace/feature with automatic
monitoring of error budget, auth/tenant, scientific, agent safety, queue and cost
signals. Roll back image/config/release flags in compatible order; migrations use
forward repair unless verified reversible. Exercise bad image/config/policy and
stuck canary. Preserve deployment evidence.
```

### S9-P05F — delivery pipeline gate

```text
Run a release candidate from clean source through all required checks, signing,
staging promotion, canary simulation and rollback without developer credentials.
Verify artifact digest remains identical, approvals/audit are complete, no secret
enters logs/artifacts and bypass paths are blocked. Publish release checklist,
owners and observed timing before first production deploy.
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
framework dependency compromise. Block launch on unowned critical risk.
```

### S9-P06B — application/cloud security verification

```text
Run SAST/dependency/image/IaC/secret scans plus DAST and manual tests for auth/
session/CSRF, access control/tenant ID substitution, injection, SSRF, uploads,
OAuth/MCP, rate/resource abuse, agent tools and action approvals. Validate cloud
public access/IAM/network/KMS/logging. Track findings with severity, owner, SLA
and retest; no unresolved P0/P1 at launch.
```

### S9-P06C — privacy retention and deletion implementation

```text
Complete policy-driven retention/deletion across user/session, datasets/columns,
objects/quarantine, runs/artifacts, prompts/context/messages/tool results,
notebooks, connector/action/outcome/eval records, telemetry and backups. Respect
holds/immutable audit through tombstone/minimal skeleton where required. Use
idempotent durable jobs, reconciliation and evidence. Test partial failure,
recreate/delete race and two workspaces.
```

### S9-P06D — backup/restore and disaster exercise

```text
Execute production-shaped loss scenarios for DB, object metadata/body, secrets/
KMS, region/service and corrupted deployment. Restore into isolated environment,
reconcile digests/lineage, rotate compromised credentials and resume safely.
Measure RPO/RTO and data/effect ambiguity, including outbox/actions. Update runbooks
from observed gaps; never use production writes in drills without approval.
```

### S9-P06E — incident response and evidence handling

```text
Define severity/on-call/escalation, containment flags, credential/session revoke,
tenant notification decision, forensic audit preservation, privacy/regulatory
timelines and post-incident review. Create playbooks for cross-tenant exposure,
provider/secret compromise, sandbox escape, runaway agent cost, duplicate action
and supply-chain compromise. Run tabletop plus one technical drill.
```

### S9-P06F — independent readiness gate

```text
Obtain independent security/privacy review or penetration test for the supported
pilot boundary, remediate/retest launch blockers and document accepted residual
risks with accountable owner/expiry. Verify deletion and restore evidence,
incident contacts, kill switches and audit export. Publish a go/no-go artifact;
do not broaden scope to avoid a failed control.
```

## Plan 9.7 — allowlisted pilot and release decision

**Contract.** Pilot has named Data Scientist and ML Engineer participants,
workspaces, use case, quotas, support and success/safety metrics. It exercises
the Core ML lifecycle through batch release/monitoring/rollback. Business action
and outcome are optional and included only when the charter names them. The
pilot is reversible and does not imply general availability or higher autonomy.

### S9-P07A — pilot charter and eligibility

```text
Define separate Data Scientist and ML Engineer jobs, use case, included/excluded
scopes, data/provider/region,
maximum users/workspaces/datasets/size/jobs/agent cost/notebook/action value,
onboarding/offboarding, consent/support and stop criteria. Set measurable workflow,
time-to-valid-baseline, investigation usefulness, constraint satisfaction,
reproducibility, batch release/rollback, quality, reliability, safety and
business-learning goals with owner/window.
Configure server-side allowlists/flags/quotas; no marketing availability claim.
```

### S9-P07B — production onboarding and data readiness

```text
Onboard pilot identity/membership/service credentials through supported flows,
verify workspace isolation/classification/retention and provider least privilege,
and run a non-destructive connectivity/upload/sync readiness check. Train users
on approvals, limitations, reporting and support. Record no raw secrets/customer
data in planning evidence. Validate offboarding/revoke/delete path before use.
```

### S9-P07C — full supported workflow pilot

```text
Have a data scientist complete ingest -> lifecycle -> goal/business constraints
-> profile/investigation -> plan -> approved build -> compare/improve -> decision
-> evidence/implementation/reproduction. Have an ML engineer complete the same
resource path through SDK/CLI, then verified model registration -> batch prediction
-> monitoring investigation -> rollback, using no DB intervention. Exercise all
three synchronized views and reconstruct project decisions. Add recommendation
-> approved business action -> outcome only when included by the charter. Capture
telemetry/evaluation/support issues and user feedback under policy. Do not
manually hide failed states.
```

### S9-P07D — soak, support and operational review

```text
Operate for the charter window, reviewing SLO/error budget, incidents, auth/
tenant anomalies, scientific/eval safety, cost/quota, connector freshness, action
ambiguity, outcome coverage and support burden. Exercise one rollback/kill switch
and one restore/recovery drill. Include lifecycle reconstruction, decision-memory
correctness, batch feature skew and drift-alert usefulness. Triage defects by
severity and pause on stop
criteria. Record denominator/context for metrics.
```

### S9-P07E — production go/no-go and rollback

```text
Compare observed pilot evidence to every charter and Scope 9 gate. List blockers,
accepted limitations, capacity/cost, residual risks, owners and next milestone.
Require explicit sign-off from the Data Scientist and ML Engineer workflow owners;
neither infrastructure-only success nor optional business-side completion can
hide a failure in the Core ML path.
Approve continue/expand/pause/rollback explicitly; expansion names a bounded new
population, never automatic GA. Publish immutable release/evidence versions and
execute offboarding/rollback if no-go.
```
