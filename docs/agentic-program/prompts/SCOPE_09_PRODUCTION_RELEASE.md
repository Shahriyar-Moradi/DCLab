# Scope 9 prompts — production platform and measured pilot

Use the common preamble. Provider/cloud choices require an ADR based on the
actual target environment. Infrastructure code must be reviewed separately from
application feature work.

## Plan 9.1 — environments and infrastructure architecture

### S9-P01A — choose production platform and codify environments

```text
Write the cloud/platform ADR and implement modular IaC for isolated dev/CI/
staging/production accounts/projects, network/subnets/firewalls/egress, DNS/TLS,
edge/load balancer/WAF as appropriate, service identities, container registry,
managed PostgreSQL, object storage, secret/KMS, telemetry and deployment units.
Staging mirrors production controls at smaller scale with separate keys/domains/
providers. No production data/credential fallback enters dev. Parameterize
regions and record non-secret configuration digest.
```

### S9-P01B — bootstrap, policy and disaster boundary verification

```text
Validate IaC format/plan/policy/security/cost in CI; require protected approval+apply
with environment approval. Test network isolation, private data services,
service least privilege, egress deny, public bucket prevention, TLS/origin,
separate keys and production boot fail-closed. Document state-backend recovery,
credential bootstrap/break-glass, region/failure-domain assumptions and full
environment teardown safeguards without using broad destructive targets.
```

## Plan 9.2 — database, object storage and secrets operations

### S9-P02A — managed PostgreSQL and migration pipeline

```text
Provision supported managed PostgreSQL with private TLS, separate application/
migration/read-only-ops/backup roles, rotated credentials, PgBouncer or chosen
pooler, bounded pools, statement/lock/idle timeouts, autovacuum/storage/slow-
query monitoring and migration advisory lock. Deploy expand-and-contract
migrations as a separate approved job; no app auto-create. Test empty,
previous-release, populated and N-1 app compatibility; define online index and
failed-migration recovery.
```

### S9-P02B — PITR, object consistency and secret/KMS operations

```text
Enable encrypted PITR/snapshots across failure domains, private versioned
object storage, lifecycle for multipart/quarantine/temp/expired artifacts,
inventory/orphan reconciliation and managed secret/KMS rotation. Restore DB and
matching object versions into isolation; verify tenant access, counts, critical
lineage/digests and pending job/agent/action disposition; measure RPO/RTO.
Exercise object permission drift, missing version and key rotation/revocation.
A backup is not VERIFIED until restored.
```

## Plan 9.3 — service and worker deployments

### S9-P03A — production deployment units and identities

```text
Deploy web, API, worker-ml, worker-agent and worker-integration separately, plus
notebook sandbox pool only if Scope 4 Python is included. Use immutable signed
images, non-root/read-only filesystem where practical, health/readiness,
graceful shutdown, workload identity, resource limits and disjoint handler/
secret/egress allowlists. API is stateless and does no large parsing/fitting.
Workers claim durable DB work and survive rolling restarts.
```

### S9-P03B — autoscaling, fairness and degraded modes

```text
Configure scaling from route latency/load for API and oldest-runnable age plus
resource saturation for workers, with global/per-workspace concurrency and DB/
provider protection ceilings. Test overload, pool exhaustion, retry storm,
lease churn, provider outage, object-store timeout, telemetry outage and rolling
restart. LLM kill switches must preserve deterministic workflows; connector/
action/notebook isolation failures must not take down read APIs. Record capacity
and graceful-load-shed thresholds.
```

## Plan 9.4 — observability and SLOs

### S9-P04A — end-to-end telemetry

```text
Instrument OpenTelemetry across edge/BFF, API, auth/policy, application service,
DB/object calls, job claim/handler, agent/task/LLM/tool/checkpoint, notebook,
connector, outbox/action and outcome. Propagate request/trace/workspace/
principal/execution/job/workflow/pipeline/agent/task/tool/connector/outbox/action
IDs. Use structured logs with stable codes and logger-boundary truncation/
redaction. Never label metrics with sensitive values/high-cardinality free text
or capture prompt/rows/secrets/signed URLs by default.
```

### S9-P04B — SLO, dashboards, alerts and runbooks

```text
Measure and approve SLOs for API availability/read latency/command acceptance,
queue age, agent first progress/completion, connector start/freshness, outbox
first attempt, audit capture and backup RPO/RTO. Create service and dependency
error budgets, role-restricted dashboards and actionable alerts with owner,
severity, resource, diagnostic, mitigation and escalation. Exercise runbooks for
API/DB/object/job/LLM/connector/outbox/notebook/audit/security/privacy failures
in staging and track corrective tests.
```

## Plan 9.5 — CI/CD and supply chain

### S9-P05A — complete required CI graph

```text
Make required jobs cover static policy, Python lint/type, web lint/type/build/
components, migration empty/upgrade, backend PostgreSQL, scientific correctness,
SDK contract, deterministic/adversarial agent eval, browser E2E, CLI contract,
MCP conformance, connector/action contract where released, container build/scan,
docs links and IaC policy. Use path filters only when proven safe; auth/schema/
policy/dependency changes trigger all affected gates. Scheduled suites add live
synthetic provider, larger stochastic/performance, sandbox and restore tests but
do not replace critical PR gates.
```

### S9-P05B — signed promotion, canary and rollback

```text
Build immutable images from locks, generate SBOM/provenance, scan/sign, deploy
by digest to staging, run smoke/migration/rollback/restore/synthetic workflows,
then require production approval and canary/rolling promotion. Halt/rollback on
defined health, policy, eval or cost regression. Prove application rollback is
schema-compatible and flags can independently disable LLM, agent/tool class,
MCP, connector, notebook code and action delivery without deployment.
```

## Plan 9.6 — security, privacy and incident readiness

### S9-P06A — production security verification

```text
Complete current threat models and run dependency/container/secret/static/
dynamic scans, API schema/limit fuzzing, auth/two-workspace matrix, browser
headers/CSP/CSRF/session, MCP OAuth/Origin/confused deputy, agent injection/
exfiltration, connector SSRF/webhook/secret isolation, notebook sandbox escape,
approval/outbox concurrency and audit-integrity tests. Commission an independent
penetration test before broad external release; track severity, owner, due date,
verification and expiring waivers.
```

### S9-P06B — privacy lifecycle and incident exercises

```text
Implement and test data access/export/retention/deletion across users,
workspaces, projects, datasets/source objects, derived profiles/context,
agent sessions/checkpoints/memory, eval/search embeddings, notebooks/exports,
connectors/webhooks, encrypted action bodies/outcomes and backups. Revoke access
immediately, honor lawful evidence exceptions and preserve non-content
completion proof. Exercise cross-tenant incident, leaked credential, injection/
exfiltration, connector compromise, sandbox signal, audit gap and action
ambiguity with communication/escalation/rollback.
```

## Plan 9.7 — allowlisted pilot and go/no-go

### S9-P07A — run the full supported pilot

```text
Define supported personas/workflow/provider/data/size/autonomy/quotas and preview
limitations; enable only allowlisted workspaces with support/incident channel.
Run real user flow: ingest/sync -> classify/profile -> objective/ProblemSpec ->
agent plan/critique -> exact-approved model build -> immutable evidence ->
recommendation/do-nothing -> exact-approved low-risk action -> reconciled
delivery -> observed outcome/impact explanation. Require no direct database
intervention and capture quality/latency/cost/policy/security/business metrics.
```

### S9-P07B — release evidence and go/no-go decision

```text
Assemble L0-L6 evidence for every included scope: migrations/rollback/restore,
full CI/staging E2E, capacity/soak/chaos, security/privacy, agent evaluations,
provider sandboxes, dashboards/alerts/runbooks, support readiness and current
known issues. Obtain named owners' acceptance. Block release for any open P0/P1
or unverified hard gate. Document rollback and what remains preview. Update the
readiness audit to exact commit/image/deployment digests.
```

