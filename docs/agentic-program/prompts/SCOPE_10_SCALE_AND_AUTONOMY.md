# Scope 10 prompts — measured scale, enterprise controls, and higher autonomy

Use the common preamble. These prompts are conditional. Execute one only after
its measured trigger is recorded and reviewed.

## Plan 10.1 — measurement and capacity governance

### S10-P01A — establish workload/cardinality/cost baselines

```text
From production telemetry, record tenants/users/projects/datasets/columns,
artifact bytes, requests, jobs/events/agent steps/checkpoints/LLM calls,
connector rows/pages, outbox/actions/outcomes and notebook usage by percentile
and growth. Measure query plans, pool waits, queue age, worker resources,
provider limits, object operations, latency, errors and unit cost. Create
reproducible load/soak profiles and forecasts without customer-sensitive
fixtures. Define saturation and release thresholds.
```

### S10-P01B — architecture trigger registry

```text
Create a quarterly reviewed registry mapping each scale option—index/query
tuning, pooler, replica, partition/archive, broker, distributed/GPU compute,
vector retrieval, regional plane—to the measured signal, threshold duration,
expected benefit, cost, risk, owner, rollback and verification. Reject changes
whose trigger is not met. Add dashboards and post-change comparison so unused
complexity can be removed.
```

## Plan 10.2 — database evolution

### S10-P02A — query/index/pooling and RLS proof

```text
Tune measured hot queries first; promote queried JSON fields to typed columns
and add only justified indexes. Size pools/PgBouncer from DB limits. If defense-
in-depth RLS is triggered, prototype transaction-local workspace/principal
settings, guaranteed pool reset, app/worker/migration/ops roles and bypass
controls while preserving composite FKs/application auth. Test every tenant
path, connection reuse, failover and query-plan regression. Do not ship RLS if
transaction hygiene cannot be proven.
```

### S10-P02B — replicas, partitioning and archive only at threshold

```text
If measured read contention justifies it, route explicitly stale-safe queries
to replicas and test lag/failover/read-after-write. If high-volume append tables
cross documented size/query/retention thresholds, design partition keys,
uniqueness/FKs, online migration, pruning, retention/archive and restore before
partitioning events/steps/tool calls/invocations/outbox/receipts. Benchmark
before/after and preserve canonical IDs/cursors/audit. Roll back back if SLO/cost
does not improve.
```

## Plan 10.3 — compute and queue evolution

### S10-P03A — resource-class workers and distributed compute

```text
When workload evidence requires, split CPU/memory/GPU/profile/report/agent/
connector/action/notebook worker pools with typed resource request, placement,
fairness, tenant quotas, image/environment digest, cancellation and cost. Add a
distributed training backend only behind existing ModelBuild services and
scientific lineage; preserve plans, seeds, folds, selection and artifacts.
Test duplicate/retry/preemption/partial worker loss and numerical/scientific
parity.
```

### S10-P03B — broker decision and migration

```text
If PostgreSQL queue throughput/fan-out/isolation cannot meet measured SLOs,
write an ADR for a broker while PostgreSQL remains the domain source of truth.
Use transactional outbox/inbox, stable operation identity, deduplication,
backpressure, replay/dead-letter and dual-read/cutover/rollback. Prove no lost/
duplicate domain effect under broker/database outage. Do not replace durable
AgentRun/ExecutionRequest/MlJob records with ephemeral messages.
```

## Plan 10.4 — retrieval evolution

### S10-P04A — benchmark structured, full-text and semantic retrieval

```text
Define a real approved corpus/use case and versioned recall/citation/latency/
cost/privacy benchmark. Compare exact IDs, relational filters, typed metadata,
PostgreSQL full text and pgvector before a separate service. Store chunks with
workspace/source/version/location/digest/classification/policy and embedding
model version; never forbidden raw values. Authorize before candidate retrieval
and again before return.
```

### S10-P04B — poisoning, deletion and service decision

```text
Test cross-tenant retrieval, indirect injection/data poisoning, rare-sensitive
content, stale source, reclassification, source deletion, embedding migration,
index rebuild and citation location. Ship semantic retrieval only if it improves
predeclared quality within latency/cost and passes hard safety. Choose a separate
vector service only if PostgreSQL cannot meet measured scale/isolation; include
dual-write/rebuild/restore/rollback and deletion proof.
```

## Plan 10.5 — enterprise identity and regional controls

### S10-P05A — SSO/SCIM and organization governance

```text
From contracted demand, add standards-based enterprise SSO, domain/organization
binding, SCIM provisioning/deprovisioning, group-to-role mapping, MFA/step-up,
session policy, service-account governance and break-glass audit. Preserve
Workspace as data tenant or document a deliberate hierarchy migration; never
let IdP groups bypass DCLab capability/resource/data/action policy. Test
revocation latency, conflicting group maps and tenant takeover.
```

### S10-P05B — regional data planes, CMK and audit export

```text
If contracts require, implement region-pinned control/data planes, tenant
placement/migration policy, provider-region enforcement, customer-managed key
option, backup/restore and restricted audit export. Prevent cross-region data/
prompt/artifact/secret movement outside policy. Test failover without silent
residency breach, key revoke/rotate, restore, deletion and operational access.
Document features unavailable during region isolation.
```

## Plan 10.6 — additional adapters, agents and schedules

### S10-P06A — expand connectors/actions through proven contracts

```text
Prioritize the next provider/action from measured demand. Reuse the shared
connector/action contract, secrets, egress, cursor, drift, outbox,
reconciliation, privacy and staging suites; do not fork business logic. Add
provider-specific limits/compensation/evidence and require sandbox proof. Track
maintenance owner and version compatibility. Marketplace packaging comes only
after multiple adapters demonstrate a stable contract.
```

### S10-P06B — reusable agents/templates and proactive scheduling

```text
Add specialized agent versions or reusable templates only when Scope 2
ablations show a need. Scheduled/proactive agents require explicit owner,
workspace/project/objective, trigger identity, tool/data/model/budget policy,
expiry, deduplication, quiet hours, notification and cancel/disable. No agent
spawns an unbounded team or silently gains memory/authority. Evaluate each
template against golden/adversarial cases and cost before promotion.
```

## Plan 10.7 — higher autonomy and model operations

### S10-P07A — model serving, drift and champion/challenger

```text
If product evidence requires serving, add versioned deployment targets,
traffic/eligibility policy, online/offline feature consistency, monitoring,
rollback, drift/calibration/performance/outcome evidence and champion/challenger
commands through the same approval/lineage boundary. Agents may diagnose and
propose promotion/retraining; deterministic validators and policy decide and
high-risk promotion requires approval. Never silently retrain/deploy from
feedback.
```

### S10-P07B — controlled L3/L4 autonomy progression

```text
For one narrowly defined action class, progress through offline -> shadow ->
human approval -> canary -> explicitly policy-approved autonomy using measured
quality, cost, false-action, outcome and incident evidence. Bound population,
volume, value, time, provider and rollback/compensation; retain do-nothing and
real-time kill switch. Separate proposer/evaluator/policy authority. High-impact
or irreversible actions remain exact-approved. Revert automatically on error-
budget, drift, policy or outcome regression.
```
