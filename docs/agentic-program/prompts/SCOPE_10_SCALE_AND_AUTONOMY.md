# Scope 10 execution prompts — measured scale and higher autonomy

Start only after production telemetry and outcome evidence. Apply `README.md`
and `EXECUTION_STANDARD.md`. Scope 10 is conditional: each capability needs a
measured trigger, ADR, cost/security model, compatibility/rollback and owner.
Do not install every listed technology.

## Scope implementation boundary

Reuse the production application contracts. Scaling can change deployment/storage
internals only behind those contracts. Enterprise and autonomy features use the
same workspace, policy, approval, evidence and audit boundaries; no premium path
may weaken tenancy or scientific correctness. Scale the same pinned LangGraph
runtime and DCLab product/checkpoint boundary; preserve the separate S2-P12
Deep Investigation and S2-P13 OpenAI Agents adapter boundaries. Measured load is
not permission to add a second graph engine, cross-runtime invocation or
framework-owned authorization. The production-MVP specialist roster, three
investigation modes, isolated Python, hosted MCP and initial connector pack are
already baseline capabilities; Scope 10 expands them only from measured demand.
Preserve the Plan 3.0 ML-platform ownership boundary. MLflow remains the MVP
tracking adapter; Pandera/Evidently/model formats stay behind DCLab ports; DCLab
remains lifecycle/decision/registry/release/monitoring authority. W&B,
OpenLineage and Optuna require separate measured decisions and cannot introduce
provider types into domain/API/SDK/CLI/MCP contracts or a second control plane.
Preserve S0-P09 DataScanPort as the only tabular-scan contract. First tune
DuckDB/templates/file layout/batching and worker limits. Polars or another engine
is not an additive MVP library: it can be evaluated only after measured evidence,
behind the same port, and must replace or be mutually exclusive with DuckDB for
each operation. Neither engine may become public SQL/dataframe API or storage.

## AWS/GCP portability requirements

Scale decisions use normalized workload/SLO/cost evidence from both supported
clouds. A provider-native optimization may be adopted behind an existing port
only with an equivalent implementation or an explicit provider capability
status; it cannot leak into domain/public contracts or silently make one cloud
second-class. Broker, GPU, replica, regional-data-plane, identity and autonomy
changes require AWS/GCP cost/security/limit comparisons, same workload digests,
independent rollback and dual-cloud conformance. Cross-cloud active-active
remains a separate measured program, not an implied Scope 10 default.

## Plan 10.1 — capacity, cardinality and cost governance

**Contract.** Versioned workload evidence drives decisions. Forecasts state
assumptions/range; triggers connect an observed limit to a specific architectural
option and rollback, not a vague “scale” claim.

### S10-P01A — production workload and cost baseline

```text
Define a versioned measurement job/dashboard for users/workspaces/projects,
datasets/rows/bytes, runs/stages/candidates, agent tasks/tokens/cost, notebook
resources, connector rates/freshness, actions/outcomes, DB/storage/queue cardinality
and p50/p95/p99 latency/error/saturation. Break down cost by bounded service/
resource class without high-cardinality tenant metrics. Record sampling, retention,
uncertainty and current capacity envelope.
Include DataScan operation/template, format, size/row/column buckets, Arrow batch
counts, p50/p95/p99 duration, peak memory/temp, timeout/cancel/limit/cleanup rate,
worker saturation and bounded pandas-rollback frequency without tenant/column
cardinality labels.
```

### S10-P01B — workload forecasts and trigger registry

```text
Create low/base/high forecasts from product commitments and observed growth with
time horizon, confidence and assumptions. For DB, queue/compute, retrieval,
regional/identity, connector/action and autonomy options, define trigger metric/
threshold/window, diagnosis confirming root cause, expected benefit, cost/risk,
prerequisites, rollback and owner. Review quarterly and after material incidents.
```

### S10-P01C — capacity experiments and cost controls

```text
Build reproducible load/soak experiments for representative API lists, job claims,
ML/agent/notebook workers, connector/action traffic and object throughput. Include
noisy tenant, burst and dependency throttling. Validate quotas/backpressure before
adding capacity. Add budgets/alerts for cloud, provider and model spend with
safe degradation/disable. Store fixtures/results/environment digests.
Exercise CSV and partitioned/unpartitioned Parquet/Arrow scan templates at low/
base/high bounds, cancellation and noisy-tenancy. Tune code-owned projections,
predicate/column pruning, row-group sizing and Arrow batch size before proposing
a second engine.
```

### S10-P01D — architecture review gate

```text
Run the review using current telemetry/forecast/experiment evidence. Mark each
candidate NOT_TRIGGERED, INVESTIGATE, APPROVED or REJECTED with rationale and
owner. Approve only the smallest change addressing measured root cause; require
an ADR and later plan prompt before implementation. Publish current limits and
avoid procurement/installation from forecast alone.
```

## Plan 10.2 — database evolution

**Contract.** Optimize query/schema/pooling first. Replicas, partition/archive
and RLS changes occur only after trigger evidence and tenant/integrity/restore
proof. PostgreSQL remains authoritative.

### S10-P02A — query, index and pooling diagnosis

```text
Capture slow query fingerprints/plans, row/cardinality/index/bloat/lock/vacuum/
WAL, pool wait and connection demand for canonical API/job/event/agent/connector
queries in a production-like copy. Reproduce with versioned fixtures. Identify
N+1, missing/wrong index, overfetch, cursor/order or pool issues before proposing
topology. Redact literals/tenant data and record baseline.
```

### S10-P02B — targeted query/index/schema remediation

```text
Implement one measured remediation per prompt: query shape, bounded projection,
covering/partial index, statistic, denormalized read projection or safe constraint
change. Analyze write/storage/lock cost; use concurrent/expand migration where
supported and exact rollback. Add plan/performance regression plus correctness/
two-workspace tests. Do not force planner hints or weaken constraints.
```

### S10-P02C — RLS and defense-in-depth proof

```text
If the RLS trigger/ADR is approved, implement on a bounded tenant-table slice
using transaction-local workspace/principal context, separate owner/migrator/
application roles and deny-by-default policies. Preserve service authorization
and composite FKs. Test pool context reset, background workers, admin/break-glass,
migrations, prepared statements and bypass attempts under load before expansion.
```

### S10-P02D — replicas and read consistency

```text
If read saturation is proven, add replica routing only for explicitly stale-
tolerant queries with lag measurement/ceiling and primary fallback. Commands,
authorization, approvals, job claims, fresh status and read-after-write remain on
primary. Propagate consistency requirement through query services, not route
guessing. Test lag, failover, stale membership and overloaded primary/replica.
```

### S10-P02E — partition/archive and restore gate

```text
If table/maintenance evidence triggers it, partition/archive one append-heavy
resource such as events/usage by a proven key/time policy. Preserve unique/FK/
cursor/audit semantics, retention holds, query compatibility and online migration.
Test boundary rows, old/new application, deletion, backup/PITR and archive restore.
Publish measured improvement/cost and rollback before further tables.
```

## Plan 10.3 — compute and queue evolution

**Contract.** Split worker resource classes and improve existing PostgreSQL queue
before adding a broker. Distributed CPU/GPU/notebook capacity preserves command,
lease, idempotency, cancellation, artifact and evidence semantics.

### S10-P03A — workload/resource-class diagnosis

```text
Measure queue age/claim/lease/retry, DB load, CPU/memory/GPU/disk/network, task
duration variance, cold start and tenant fairness by handler/resource class.
Identify whether bottleneck is capacity, head-of-line blocking, dependency limit,
job granularity or queue database. Define SLO/cost/operational requirements and
approved remediation. Do not assume a message broker solves compute saturation.
For DataScan, distinguish inefficient templates/file layout, absent pruning,
batch conversion, spill/temp pressure, worker concurrency and DuckDB limits from
an actual engine limitation. A replacement proposal needs a reproducible corpus,
semantic parity target, measured SLO/cost gap and explicit non-additive migration.
```

### S10-P03B — resource-class worker pools

```text
Add code-owned resource class to job/handler policy and deploy separate bounded
ML-light/ML-heavy/agent/integration/notebook pools with identities/allowlists,
min/max resources, concurrency and workspace quotas. Scheduler validates requested
class and prevents user/LLM arbitrary escalation. Test fairness, cancellation,
drain, lost worker and wrong-handler/class; compare queue/SLO/cost evidence.
Keep DuckDB connections per-operation and in-process only; enforce aggregate
worker/workspace memory, threads, temp and connection concurrency so parallel
scans cannot multiply beyond container or tenant budgets.
```

### S10-P03C — distributed CPU/GPU and notebook pool

```text
If approved, add one narrow compute backend interface carrying immutable input/
environment/command manifests and returning artifact/result digests. Preserve
MlJob/ExecutionRequest as authoritative intent, idempotent dispatch, checkpoint,
cancel, quotas and reconciliation. Enforce GPU/image/network/tenant isolation and
cost bounds. Add faithful fake plus staging backend failure/restart tests.
If the measured change is a DataScan engine replacement, implement the candidate
behind the unchanged port and code-owned templates, run shadow parity/performance
on the immutable corpus, and select exactly one engine per operation by a server-
owned flag. Never import both in a shared hot path, return engine-native objects,
expose SQL/dataframe expressions or silently fail over after partial execution.
Removing DuckDB or adopting Polars requires supply-chain/security review,
complete parity, canary, rollback and proof that no durable state is stranded.
```

### S10-P03D — broker decision and compatibility design

```text
Only if PostgreSQL queue limits are measured, compare broker options against
ordering, delay, visibility/lease, redelivery, priority/fairness, transactions/
outbox, operations, region, security and cost. Write ADR and dual-publish/read
migration with stable job IDs/idempotency/reconciliation and rollback. Do not
implement a broker in the decision prompt.
```

### S10-P03E — queue migration and release gate

```text
Implement approved broker behind queue interface in expand/shadow/compare/canary
phases, retaining database intent/state and transactional outbox. Inject duplicate,
reorder, loss, partition, poison, broker outage and rollback; assert one effect,
terminal recovery and fair quotas. Run load/soak/cost comparison and remove old
path only after compatibility window/evidence.
```

## Plan 10.4 — retrieval evolution

**Contract.** Benchmark structured queries/full text first, then pgvector if a
semantic use case has measurable value. Retrieval respects workspace/data policy,
version/digest/deletion and prompt-injection controls.

### S10-P04A — retrieval use case and benchmark corpus

```text
Define exact user/agent retrieval questions, relevance labels, latency/freshness/
cost targets, data classes and citation requirements. Create versioned synthetic/
authorized corpus with workspace/project/resource/version/digest and malicious/
poisoned/deleted cases. Compare current structured queries and PostgreSQL full
text as baselines. Do not introduce embeddings without incremental-value evidence.
```

### S10-P04B — PostgreSQL full-text candidate

```text
If baseline warrants, add a bounded search projection/tsvector/index for approved
metadata/report content with deterministic normalization, language policy,
workspace filter and version/deletion updates. Return authorized resource IDs/
snippets/citations, not raw hidden content. Test rank, pagination, injection/
highlight XSS, cross-tenant, stale/deleted and write/index cost.
```

### S10-P04C — pgvector semantic candidate

```text
If semantic value remains, define embedding model/version/purpose/data/region/
retention policy, chunk schema/source digest and re-embedding/deletion lifecycle.
Store vectors in PostgreSQL pgvector initially and filter tenant/policy before
return. Treat retrieved text as untrusted. Test model/version mixing, poisoning,
revoked source, deletion, tenant isolation, recall/latency/cost and fallback.
```

### S10-P04D — hybrid retrieval and agent integration

```text
Implement deterministic query planning for structured/full-text/semantic/hybrid
based on declared tool purpose, never raw LLM-selected SQL/index. Bound candidates,
reranking, context bytes and latency; validate current authorization/data policy
after retrieval. Return source/version/digest citations and safe snippets. Evaluate
supported-answer quality versus cost and injection/exfiltration.
```

### S10-P04E — separate vector service decision gate

```text
Only if pgvector misses recorded SLO/capacity/functionality, compare managed/self-
hosted vector services on tenant filters, consistency, deletion, backup/restore,
region, encryption, poisoning controls, cost and operations. Require dual-write/
reconcile/rollback ADR and isolation tests before implementation. Otherwise record
NOT_TRIGGERED and keep PostgreSQL.
```

## Plan 10.5 — enterprise identity and regional controls

**Contract.** Enterprise features are demand/contract-triggered. External identity
and provisioning map to current organizations/users/memberships; current server
authorization remains authoritative. Regional resources never silently replicate
restricted data.

### S10-P05A — enterprise requirement and identity ADR

```text
Collect customer IdP/protocol/domain/claim/group/session/MFA/provisioning/deprovision
requirements, organization hierarchy, admin delegation, residency, encryption key
and audit-export contracts. Choose OIDC/SAML and SCIM scope/provider-neutral
interfaces, account linking and break-glass. Threat-model takeover, domain/claim/
group spoof, IdP outage and lockout. Do not implement before signed requirements.
```

### S10-P05B — SSO and account-linking implementation

```text
Implement one pinned OIDC or SAML adapter with metadata/key rotation, issuer/
audience/destination/state/nonce/time/signature validation and exact tenant routing.
Link identities through verified immutable provider subject, never email alone,
and require safe admin flow for collisions. Re-resolve current membership/role at
session. Test mix-up, replay, key rotation, IdP outage and break-glass.
```

### S10-P05C — SCIM and organization governance

```text
Implement scoped SCIM Users/Groups provisioning with bearer/OAuth credential,
idempotent externalId mapping, pagination/filter limits, PATCH validation and
deprovision that promptly revokes sessions/tokens/memberships. Map groups through
an explicit reviewed role policy; unknown groups grant nothing. Add conformance,
replay, race, cross-org and bulk/rate tests plus audit.
```

### S10-P05D — regional data plane and CMK

```text
If contracted, define organization/workspace region pinning at creation, region-
aware DB/object/secrets/LLM/connector/telemetry routing, replication prohibitions
and metadata exceptions. Add customer-managed key reference/rotation/revoke and
crypto-shred semantics where provider supports it. Enforce region in services/jobs,
not UI only. Test cross-region dispatch, backup, failover and key unavailability.
```

### S10-P05E — audit export and enterprise gate

```text
Provide immutable bounded audit export with organization/workspace/time/type
filters, cursor/stream/digest/signature and audience redaction; delivery uses
approved destination/credentials and outbox if pushed. Run SSO/SCIM/region/CMK/
audit conformance, security, deletion/restore and tenant tests with pilot IdP.
Publish contractual limits, operations/incident owners and rollback.
```

## Plan 10.6 — additional adapters, agents and schedules

**Contract.** Expand only through proven connector/action/specialist/ML-platform
adapter contracts. This plan does not postpone the production-MVP connector
pack, specialist roster, three Deep Investigation modes, hosted MCP or isolated
Python; it covers additional providers/roles/subagents and proactive schedules.
Every addition has a maintenance owner, pinned provider/schema versions, shared
contract suite and measured user value. Scheduled agents have explicit authority
and expiry.

### S10-P06A — demand and contract-fit selection

```text
Rank proposed connector/action/specialist/template/schedule by measured requests,
workflow value, contract reuse, provider/test support, risk, maintenance and cost.
Select one bounded addition and document official API/schema, owner, support/SLO,
data/secret/egress/action risk and deprecation. Reject items requiring bypass of
shared services or unbounded generic HTTP/code tools. If considering a Deep
Agents Investigation Copilot subagent, require S2-P12/S9 production evidence,
measured context-quality benefit over the single-harness baseline, a fixed named
role, read-only tools, typed output and proof it remains inside
worker-investigation rather than the authoritative raw LangGraph graph.
For ML-platform demand, begin from observed Plan 3.0 gaps. A W&B option is an
outbound, one-way collaboration/reporting adapter over normalized DCLab evidence,
never a second build-path tracker or registry; OpenLineage is a one-way lineage
event adapter, never DCLab lifecycle truth. Select at most one bounded adapter in
a release, require customer/workflow evidence, and reject it if MLflow plus DCLab
already satisfies the job. Record exact fields, redaction, tenant routing,
idempotency, deletion, outage, cost and disable/removal behavior.
```

### S10-P06B — adapter/specialist implementation

```text
Implement the selected addition behind the existing code-owned interface and
versioned registry. Reuse secret/egress/cursor/drift/outbox/reconciliation or
agent context/tool/budget/review contracts exactly; provider/specialist-specific
logic stays in its adapter. New authoritative workflow specialists compile as
code-owned subgraphs in the pinned raw LangGraph runtime and use its existing
gateway/ToolRunner/checkpointer boundary. A selected Deep Agents Copilot
subagent instead stays only in worker-investigation, is statically configured,
inherits an explicitly narrower read-only SDK tool/budget/data scope, has typed
output, depth/fan-out/concurrency limits and cannot call/be called by raw
LangGraph. Never move Deep Agents dependencies or state into worker-agent and
never let either kind of specialist spawn the other. Add faithful fake/golden
fixtures and shared contract, tenant, injection, rate/bounds/recovery and
cross-runtime conformance tests. No forked business command.
For an approved W&B/OpenLineage adapter, consume only immutable normalized DCLab
events through an outbox; map external IDs in a private adapter table and never
write provider state back as scientific/release authority. Do not install the
provider SDK in API/agent/ML execution paths. Prove same event/digest replay,
deletion/revocation, outage recovery, cross-tenant denial, redaction and that the
adapter can be disabled/removed while MLflow tracking and DCLab workflows remain
healthy. Never activate W&B tracking for a run already tracked by MLflow.
```

### S10-P06C — reusable template contract

```text
Define immutable template version containing supported objective, input schema,
graph/agent/tool/data/model/budget releases, expected outputs, required user
decisions, evaluation suite and stop conditions. Parameters cannot widen authority
or select arbitrary prompt/tool/model. Bind the template to a code-owned
LangGraph topology key/digest or, only for an approved Copilot template, the
isolated Deep Agents harness/subagent release digest; never executable generated
graph code and never both runtimes in one template. Add
compatibility/canonical-digest tests and publish only after value/safety
evaluation against non-template baseline.
```

### S10-P06D — proactive schedule and notification

```text
For approved proactive use, require owner, workspace/project/objective/template,
trigger identity, timezone/quiet hours, deduplication, expiry, tool/data/model/
budget policy, maximum frequency/concurrency and cancel/disable. Notifications
contain safe summary/link and respect audience/preferences. Test DST, duplicate
trigger, inactive owner, revoked membership, budget and notification failure.
```

### S10-P06E — expansion canary gate

```text
Run shared plus provider/template/schedule E2E, failure/recovery, tenant/security,
cost and operational ownership tests. Compare measured value/support burden with
selection case, canary to bounded workspaces and exercise independent disable.
For a Copilot subagent, re-run dependency/image/identity/network/runtime-state
separation and prove depth zero after disabling the feature without affecting
worker-agent.
Marketplace/catalog publication requires multiple stable adapters and versioned
compatibility; otherwise keep internal.
```

## Plan 10.7 — advanced model operations and controlled L3/L4 autonomy

**Contract.** Extend, do not replace, the Scope 3 verified model-registration,
batch-prediction, monitoring and rollback path. Online/streaming serving,
retraining and autonomous action are separate capabilities.
Agents diagnose/propose; deterministic validators and policy decide. High-impact,
irreversible or legally sensitive actions remain exact-approved.
SHAP is not inherited from the MVP. Any future proposal requires its own
measured ADR, supported-model/resource/privacy contract and release gate.
Optuna is not assumed: it may implement one bounded search-strategy port only
after measured evidence shows the deterministic Scope 3 portfolio is inadequate.

### S10-P07A — serving and autonomy eligibility ADR

```text
Inventory Scope 3 ModelRelease/FeatureContract/BatchPredictionRun/MonitoringWindow
owners and the deterministic experiment-selection baseline first. Define exact online or streaming prediction use case,
latency/availability/freshness, feature
consistency, population/region, risk/action class and outcome evidence. Separately
define L0–L4 autonomy levels and eligibility thresholds for quality, calibration,
false action, cost, outcome, incident and reversibility. Record prohibited classes,
approver separation, do-nothing and rollback. No serving/autonomy implementation.
If proposing Optuna, include measured baseline gap, supported candidate families,
fixed search space, maximum trials/time/cost/concurrency, seed/sampler/pruner
versions, failure/cancel/resume semantics and proof it cannot read final holdout,
change DCLab budgets or promote a model. Reject a separate Optuna service,
dashboard, database or new provider-native public type; the existing dormant
scientific source enum may become active only through this approved adapter.
```

### S10-P07B — versioned model deployment and prediction service

```text
If triggered, extend the existing ModelRelease with DeploymentTarget/TrafficPolicy
for exact ModelVersion, environment, eligibility, feature contract, image/runtime
digest, canary and state; do not create a second release/registry model.
Implement prediction through authorized typed service with request/feature digest,
bounded batch/latency, monitoring and audience-safe result; no arbitrary pickle/
code. Test offline/online feature parity, tenant, load, rollback and unavailable
features.
```

### S10-P07C — drift, calibration and outcome monitoring

```text
Extend the existing versioned reference/current monitoring windows and
deterministic metrics for input/
prediction/label drift, data quality, calibration, performance, fairness only when
defined, and downstream outcome. Handle delayed/missing/corrected labels and
multiple testing honestly. Alert creates investigation/proposal, not automatic
retrain. Test synthetic shifts, false alarms, low volume and source corrections.
```

### S10-P07D — champion/challenger and retraining commands

```text
Implement retraining/evaluation/deployment proposals through existing model-build,
scientific, approval and release services. Challenger uses immutable data/problem/
plan versions and evaluation; promotion requires predefined gates/separation and
creates new deployment release. Agents may diagnose/propose only. Test holdout
reuse, feedback leakage, stale data, concurrent promotion and rollback.
Only when the S10-P07A Optuna trigger is approved, implement it behind the
DCLab SearchStrategy port inside the ML worker. Persist trials as ordinary DCLab
child ExperimentCandidate/build lineage and mirror bounded details through the
existing MLflow adapter; store no authoritative Optuna study state. Enforce the
same portfolio reservation/stop/cancel/idempotency policy and deterministic seed.
Add fake/real-adapter parity, interrupted-resume-without-duplicate-trial,
budget/holdout/tenant and removal-to-baseline tests.
```

### S10-P07E — narrow autonomous action policy

```text
For one approved reversible low-impact action class, define deterministic
eligibility by workspace/population/provider/value/volume/time, evidence/model/
recommendation versions, confidence/uncertainty, budget, error budget and recent
incident/drift. Policy—not agent—may waive per-action human approval only within
this exact class. Persist rationale/digest and retain real-time global/workspace
kill switch. Add pure boundary tests.
```

### S10-P07F — offline-to-autonomy progression gate

```text
Progress offline replay -> shadow -> human exact approval -> tiny canary ->
policy-approved autonomy only after each window meets predefined safety/quality/
cost/outcome/error-budget thresholds. Inject drift, bad model, provider ambiguity,
duplicate effect, delayed outcome and kill switch; auto-revert on regression.
Publish immutable decision/evidence/owner. Never generalize approval to another
action/population without a new full progression.
```
