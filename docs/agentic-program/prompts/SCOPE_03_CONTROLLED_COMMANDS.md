# Scope 3 execution prompts — controlled commands and agent-directed builds

Every prompt inherits the plan-level outcome and mandatory live-checkout
execution card in [the remaining-scope map](REMAINING_SCOPE_EXECUTION_MAP.md).
Complete one reviewable lettered work unit at a time.

Start after the authoritative S2-P11F gate; S2-P12/S2-P13/S2-P14 may run in parallel
and do not block safe Scope 3 development. S2-P12H and the S2-P13F runtime
decision must close before production-MVP go/no-go. Apply `README.md` and
`EXECUTION_STANDARD.md`. Activate only
typed application commands. No arbitrary SQL, Python, filesystem, provider,
prompt promotion or external action tool.

## Scope implementation boundary

Reuse ExecutionRequest, MlJob, WorkflowRun, PipelineRun, ProblemSpec,
`model_build_service.py`, workflow/job handlers, evidence locks and Scope 2
proposals plus the Scope 1 lifecycle and ProjectDecisionService. Add cohesive
command/approval services and a router such as
`api/v1_model_builds.py`; web, SDK and agent tools call the same services.
LangGraph nodes may request only versioned ToolRunner operations; they never
execute commands directly, hold approval authority or introduce a second tool/
agent loop. The S2-P12 Deep Investigation worker, S2-P13 OpenAI Agents adapter
and S2-P14 NOOA worker remain separate whole-run runtimes and proposal-only by default; they
receive no raw LangGraph or worker-dispatch tool. Any allowed command must still
enter the same ToolRunner/ApprovalService boundary. PydanticAI cannot provide a
framework-native command tool.
Jev may rank or classify a proposal only through an S1-P12F-released purpose;
its probabilities/confidence cannot satisfy authorization, approval, resource
preconditions or deterministic scientific verification. NOOA cannot consume an
approval or apply a proposal; a reviewed user/system action creates a new typed
DCLab command under the same policies as every other client.

Read `docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`. MLflow is
the single production-MVP detailed experiment tracker/model-package metadata
service; Pandera, Evidently and safe model formats are bounded worker libraries;
OpenTelemetry carries operational signals. DCLab remains authoritative for
tenancy, lifecycle, scientific summaries, decisions, approvals, releases,
rollback and audit. Do not add W&B, expose provider-native objects publicly or
let an agent access any ML-platform service directly.
Reuse the verified S0-P09 `DataScanPort` for authorized profile/slice/leakage/
drift preparation. DuckDB remains its ephemeral, resource-bounded worker
adapter; it is not MLflow storage, a public query service or an agent tool.
All inputs use registered DCLab operations and bounded Arrow batches; no SQL,
direct artifact path, extension/network authority or Polars dependency.

## AWS/GCP portability requirements

Commands, approvals, builds, MLflow references, model packages, batch releases
and monitoring windows use DCLab IDs plus opaque storage/runtime references.
ML jobs declare portable CPU/memory/ephemeral-disk/accelerator classes rather
than AWS/GCP machine types. S3 and GCS package/artifact adapters must preserve
the same digest, immutability, conditional-write and rollback semantics. The
same model/environment package and OCI worker digest must build, verify, infer,
monitor and roll back on EKS and GKE without provider fields in `/v1`.

## Plan 3.0 — ML platform reuse foundation

**Contract.** Reuse `Experiment`/PipelineRun, ExperimentCandidate, ModelAsset,
ModelVersion, RuntimeEnvironment, CodeSnapshot, Dataset/DatasetProfile,
Artifact, `model_build_service.py`, `workflow_execution_service.py`,
`artifact_service.py`, `artifact_store.py`, `reproducibility_service.py`,
`lineage_service.py`, `admin_model_registry_service.py`,
`admin_monitoring_service.py`, `observability_service.py`, `config.py`, the root
`pyproject.toml`, current PostgreSQL job boundary and object-store adapters.
Also reuse the S0-P09 DataScan domain/service/adapter/template registry and its
parity, authorization, resource-limit, cancellation and cleanup evidence.
Introduce only narrow provider-neutral ports and references that those owners
cannot already express. MLflow/Pandera/Evidently/skops dependencies stay in the
ML worker's optional lock/image; no new customer-facing platform, tracker UI,
registry authority or public provider contract is created. Use focused test
homes `apps/api/tests/test_ml_platform_contract.py`,
`test_experiment_tracking_persistence.py`, `test_mlflow_tracking_adapter.py`,
`test_model_package_policy.py`, `test_feature_contract_validation.py` and
`test_drift_calculator.py`; extend a proven equivalent and record the
substitution instead of duplicating suites.

SHAP is outside this scope and the production MVP. Do not install it, add SHAP
jobs/artifacts/routes/UI or treat a legacy enum/visualization placeholder as a
released capability. Use the approved deterministic diagnostic bundle.

### S3-P00A — ML platform ownership ADR and dependency decision

```text
Read docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md and inspect the
root `pyproject.toml`, `docker-compose.yml`, `Dockerfile`,
`apps/api/app/config.py`, `apps/api/app/db/models.py`, the
experiment runner/model-build/workflow services, artifact/reproducibility/
lineage services, model-registry/monitoring services,
`apps/api/app/domain/scientific_plane.py`, its frozen migration/fixtures and
their tests. Record an
ADR assigning exactly one owner to detailed run telemetry, canonical lifecycle,
model package, feature contract, drift calculation, operational telemetry and
audit. Record DuckDB/Arrow as the already-approved bounded scan mechanism behind
DataScanPort—not a new owner—and approve MLflow Tracking/Model format, Pandera, Evidently, skops/reviewed
native model formats and OpenTelemetry for only the bounded roles in the
canonical architecture. Explicitly reject W&B beside MLflow, MLflow-owned DCLab
promotion/tenancy, an Evidently service/UI/store, hand-maintained duplicate
Pandera schemas, API-side model loading, production filesystem mlruns fallback,
Optuna/OpenLineage/feature-store/distributed-platform installation and direct
agent access. Explicitly reject Polars, persistent DuckDB, arbitrary SQL and
direct MLflow/Evidently/Pandera artifact-store access. Define internal topology, trust/data flows, private endpoint and
separate MLflow database/schema/object-prefix identities, dependency/image
placement, configuration/kill switches, retention/backup, degraded behavior and
rollback. Inventory exact existing columns/contracts to reuse and list only
proven gaps; do not install packages, migrate data, deploy a service or implement
an adapter. Add static contract-test assertions and a maximum-PR change map.
Preserve the existing `optuna` scientific source enum value as a dormant
backward-compatible marker; do not remove it or interpret it as an enabled
optimizer/runtime.
Maximum change: ADR, diagram and test skeleton under 500 hand-edited lines.
```

### S3-P00B — provider-neutral tracking references and state contract

```text
Inspect Experiment/PipelineRun, ModelVersion, Artifact, lifecycle events and
idempotency helpers before adding persistence. Define ordinary DCLab contracts
for TrackingReference and TrackingSyncResult without importing MLflow types.
Bind each reference to a concrete tenant-safe DCLab Experiment/PipelineRun and,
when applicable, ModelVersion/package; include provider=mlflow, provider-instance
digest, bounded external run/logged-model ID, internal-only logical locator,
sync state pending/active/synced/tracking_degraded/failed, canonical content
digest, safe failure code, created/last-attempt/synchronized timestamps and
optimistic version. External IDs become immutable once synced. Prefer additive
fields on a concrete current owner; if a table is required, use real composite
workspace/project/resource foreign keys and never a generic resource_type/id
relationship without referential integrity. Enforce one mapping for each DCLab
resource/provider-instance and prevent external-ID reuse. Define pending ->
active -> synced plus bounded degraded/reconcile and terminal failure transitions;
tracking state cannot change scientific result or run status. Discover the live
Alembic head, add at most one expand revision, and test empty/live-head upgrade,
two-workspace substitution, concurrent create, duplicate/changed digest,
external-ID collision, immutable completion, degraded recovery, retention and
forward repair in PostgreSQL. Provider locators/credentials never enter `/v1`,
events or ordinary logs. Maximum change: one migration and approximately 700
non-generated lines.
```

### S3-P00C — MLflow tracking port, adapter and reconciliation

```text
Add a small DCLab-owned ExperimentTrackingPort beside existing experiment/
artifact services with provider-neutral inputs/results for ensure_run,
log_parameters, log_metrics, log_artifact_references, finalize_run,
get_status and reconcile. Provide a deterministic faithful fake/no-op for CI and
one MlflowTrackingAdapter in an ML-worker-only package/dependency group. Use the
official pinned MLflow client against a private configured URI; no MLflow class,
exception, registry state or URI crosses domain, job, `/v1`, SDK, CLI, MCP or
agent schemas. Ensure one external run under concurrency by locking/resolving the
DCLab tracking reference and using the DCLab run UUID/digest as internal
correlation tags; tags never authorize. Log bounded sanitized parameters,
canonical metric names/steps/timestamps, dataset/artifact IDs+digests and model-
package reference, never raw rows, secrets, prompts, signed URLs or arbitrary
user-controlled tags/files. External calls occur outside the DCLab transaction
with connect/read/write deadlines, retry only for classified unambiguous safe
operations and no unbounded local spool. Timeout/ambiguous create sets
tracking_degraded and schedules one code-owned idempotent reconciliation job;
reconciliation attaches the unique run or records bounded operator action and
never rewrites scientific evidence. Add typed settings and safe defaults:
DCLAB_MLFLOW_TRACKING_ENABLED=false, empty DCLAB_MLFLOW_TRACKING_URI and
DCLAB_MLFLOW_REQUIRED_FOR_PROMOTION=true; production rejects enabled public/
invalid endpoints, missing workload authentication/TLS policy, unbounded
timeouts/payloads or filesystem fallback. Test fake success, live-container
contract when available, disabled mode, redaction, concurrent ensure, 401/403,
429/5xx, timeout before/after create, reconciliation, kill switch and proof that
degraded/missing required tracking cannot verify or promote. Add adapter health/
reconcile metrics and a runbook. Maximum change: one adapter family, one handler
and approximately 800 non-generated lines.
```

### S3-P00D — immutable model-package and safe-load policy

```text
Inventory engine/serving/artifacts.py, ml/predict.py, current joblib/pickle
writers/loaders, boosting extras, Artifact/ModelAsset/ModelVersion,
RuntimeEnvironment, CodeSnapshot and reproducibility verification. Add one
versioned ModelPackageManifest and format registry binding model family/format,
model/preprocessor artifacts and SHA-256 digests, MLflow run/logged-model
reference, ordered FeatureContract digest, training Dataset/version, code
snapshot, dependency/environment lock digest, Python/library/format versions,
input/output signatures, build verifier and creation time. Approve skops only
for supported scikit-learn objects after get_untrusted_types/explicit reviewed
type allowlisting; use reviewed native non-Python-executable CatBoost, XGBoost
and LightGBM formats for the selected families. Treat pickle/joblib/cloudpickle
as legacy_quarantined unless a separate trusted-internal compatibility policy
pins producer, digest and isolated worker; never dynamically import arbitrary
package code. Implement verify-before-load and a worker-only loader interface;
the API, web, SDK, MCP and agents receive manifest projections, never bytes or
load functions. Validate artifact authorization, MIME/extension/size/digest,
signature, manifest schema, library compatibility, environment and feature-
contract digest before construction. Add frozen round-trip/prediction tests per
supported family plus tampered zip/path traversal, unknown type, unsafe pickle,
version mismatch, wrong feature digest, oversized/decompression-bomb and cross-
workspace cases. Existing packages remain readable only under explicit legacy
state and cannot promote. Add format kill switch and forward-repair/rollback
procedure. Maximum change: one manifest/loader family and approximately 800
non-generated lines.
```

### S3-P00E — DCLab FeatureContract and deterministic Pandera compiler

```text
Inspect DatasetColumn/DatasetProfile, feature/transformation/lineage models,
model-build schemas, prediction coercion and existing validation tests. Define
one immutable versioned DCLab FeatureContract containing workspace/project,
ordered feature name/role, logical+physical dtype, nullable/default/category/
unknown policy, safe coercion, prediction-time availability, target exclusion,
transformation+source lineage, compatibility mode, producer and digest. Reuse an
existing persisted contract/manifest if it satisfies these fields; otherwise add
the minimum concrete tenant-safe model in the later model-operations migration,
not a second generic schema store in this prompt. Implement a deterministic
compiler and PanderaFeatureValidator in the ML-worker dependency group. Strict
validation rejects missing required, unexpected, reordered where order matters,
unsafe coercion, null/category/range/uniqueness and target/leakage violations;
it never silently filters columns or fills values absent from the contract.
Translate library exceptions into bounded stable DCLab reason codes/field
details and record compiler/Pandera versions plus schema digest in the package;
Pandera objects never persist or cross product APIs. Preserve DCLab's separate
prediction-moment, temporal/group split, leakage and scientific verifiers as
precedent. For large tabular inputs, accept only bounded Arrow batches supplied
by the authorized DataScan service; the validator neither opens paths nor runs
DuckDB/SQL, and failures retain stable row/batch context without raw values.
Add deterministic compile/idempotence, CSV/Parquet/Arrow, exact valid,
multi-error, malicious column name, order/type/category/null, target included,
schema evolution compatibility, two-workspace and large-bounded fixture tests.
Document supported types/non-goals and kill-switch behavior; production model
release cannot bypass this validation. Maximum change: one compiler/validator
family and approximately 750 non-generated lines.
```

### S3-P00F — Evidently drift calculator behind DCLab monitoring policy

```text
Inspect admin_monitoring_service.py, domain/admin_monitoring.py, existing
pipeline/metric observability and planned MonitoringWindow contract. Add one
provider-neutral DriftCalculationRequest/Result and DriftCalculator port with a
deterministic fake plus an EvidentlyDriftCalculator in the ML-worker dependency
group. Inputs contain authorized bounded reference/current dataset projections,
FeatureContract, prediction/optional label columns, sample/population counts,
time window and a versioned DCLab method/threshold/multiple-testing policy.
Prepare those projections through registered `drift_window_prepare.v1`
DataScan templates over digest-verified artifacts, with deterministic column/
row ordering, sampling and bounded Arrow batches. Evidently receives only the
normalized bounded projection; it cannot open artifacts, invoke DuckDB or
change scan limits.
Outputs contain normalized per-feature/aggregate statistics, method/version,
sample counts, sufficient-data state, warnings and detailed-report artifact
bytes/reference; no Evidently Report/Metric object crosses the adapter. DCLab—not
Evidently—decides no_data/insufficient/stable/drift/degraded/infrastructure_error,
persists MonitoringWindow, raises alerts and authorizes investigations. Do not
deploy Evidently UI/service/database, accept its default threshold silently or
send it raw unbounded datasets. Use deterministic sampling/order, memory/time/
row/column/output limits and no network. Store detailed JSON/HTML only through
ArtifactService after sanitization; persist normalized facts and digests in
PostgreSQL. Add stable/shifted/null/category/schema, low-volume, stale baseline,
constant/high-cardinality feature, delayed/corrected label, deterministic rerun,
oversize/cancel, malicious label/report rendering and two-workspace tests.
Configure DCLAB_EVIDENTLY_ENABLED=false until Plan 3.8 monitoring release and
emit low-cardinality duration/status/failure metrics. Maximum change: one
calculator family and approximately 750 non-generated lines.
```

### S3-P00G — ML platform isolation and foundation gate

```text
Run the complete Plan 3.0 contract using the deterministic fakes in PR CI and a
private production-shaped MLflow service in staging. Pin MLflow, Pandera,
Evidently, skops, DuckDB/PyArrow and OpenTelemetry packages in a dedicated
ML-worker optional
dependency lock/image; generate an SBOM/license/vulnerability report. Add static
dependency/import tests proving W&B is absent and provider SDK/native objects do
not enter API, worker-agent, worker-investigation, public SDK, CLI or MCP
packages; prove Polars is absent as well. Re-run S0-P09 DataScan conformance to
show connections are fresh/in-memory, extension install/load and external access
are disabled, arbitrary SQL/paths are unreachable, resource/cancel/cleanup
bounds hold, pandas rollback works and no persistent DuckDB artifact exists.
Prove MLflow uses a separate database/schema identity and object-store
prefix with no DCLab Alembic ownership, public ingress, browser credential or
agent access. Exercise concurrent run creation, outage/timeout/ambiguous response,
bounded reconciliation, tracking-degraded promotion denial, package tamper/
unsafe deserialization, strict Pandera validation, Evidently stable/drift/
insufficient fixtures and adapter kill switches. Verify no raw rows, prompts,
secrets, signed URLs, provider locators or high-cardinality resource IDs appear
in logs/metrics/public schemas. Add OpenTelemetry spans and low-cardinality
adapter health/degraded/reconcile/validation/calculation metrics with redaction
tests; do not make telemetry an audit authority. Document local disabled/no-op
and optional MLflow profiles, production configuration, backup/restore,
dependency upgrade, incident, reconciliation and rollback runbooks. Run backend,
SDK, OpenAPI, worker and relevant scientific regressions; publish commands,
durations, image/package digests, known limits and evidence. Mark 3.1 eligible
only when two consecutive regenerations/tests are clean and disabling/removing
the external adapters leaves canonical deterministic DCLab behavior healthy.
```

## Plan 3.1 — canonical atomic model-build command

**Contract.** Reuse the verified Plan 3.0 ports. Same actor/workspace/idempotency key/canonical payload digest
returns the same build. A conflicting digest is 409. Intent, lineage, job,
audit/event and initial budget reservation become durable atomically.

### S3-P01A — command ADR and request contract

```text
Inventory every current model-build creation path in lab/admin/services/jobs.
Write an ADR selecting one ModelBuildCommandService and typed request containing
workspace, project, ProblemSpec/dataset/plan versions, resource policy,
idempotency key, the DCLab tracking-policy version and optional validated Scope 2
proposal. Define canonical digest, preconditions, readiness, capability,
quota/budget, provider-neutral TrackingReference links, response, state and
errors. DCLab owns command and scientific lifecycle; MLflow receives bounded
run metadata only through the Plan 3.0 port. List deprecated adapters and
non-goals; do not implement routes or expose an MLflow run ID publicly.
```

### S3-P01B — transaction and idempotency implementation

```text
Implement the command transaction using existing models: authorize current
membership/capability, lock/validate source versions and readiness, reserve
quota/budget, create or replay ExecutionRequest, WorkflowRun, PipelineRun and
MlJob plus one pending provider-neutral TrackingReference, then append audit/
event and links. Do not call MLflow inside the database transaction: the worker
claims the durable intent and reconciles it through TrackingPort using the same
canonical DCLab identifiers and digests. Use one code-owned handler/payload of
IDs and safe options. Same digest replays; different digest conflicts. Inject
failure at every write and at the later tracking sync; prove no orphan,
duplicate external run or false DCLab success remains.
```

### S3-P01C — plan/proposal validation and scientific preconditions

```text
Validate ProblemSpec, dataset/access/classification, scientific-plan version,
metric direction, validation/holdout and resource bounds through existing
deterministic services. A Scope 2 proposal is input evidence only and must match
current base/version/digest plus approved patch schema. Re-run validation inside
the transaction; never trust UI/agent readiness. Validate the pinned tracking
policy and package/FeatureContract prerequisites without treating MLflow as a
scientific validator. Test stale, locked, quarantined, cross-tenant,
holdout-weakened and disabled/degraded-tracking inputs.
```

### S3-P01D — `POST /v1/model-builds` and read resources

```text
Add create/get/list endpoints with Scope 0 error/page/request-ID conventions,
Idempotency-Key, explicit workspace, typed response links and safe status. Define
201 first create, 200 replay, 409 digest/precondition conflict, 422 validation,
429 quota and anti-enumeration behavior. Routes call only the command/query
services. Update deterministic OpenAPI and add full negative contract tests.
```

### S3-P01E — Python client and legacy adapters

```text
Add typed SDK create/get/list model-build methods and idempotency support. Route
existing web/lab/admin creation paths through the same service or a documented
compatibility adapter; remove duplicate transaction logic only after parity
tests. The client remains HTTP-only. Add operation/client parity plus regression
tests proving identical resource IDs/state/events across web, SDK and service.
```

### S3-P01F — command completion gate

```text
Run PostgreSQL concurrency with many identical/conflicting requests, failure
injection, two-workspace/resource substitution, readiness/quota denial, worker
pickup, MLflow timeout/replay/reconciliation and full existing scientific
regression. Verify one intent/run/job/audit/tracking lineage, no early holdout
access and no public/provider-type leakage. A tracking-degraded build may retain
diagnostic artifacts but cannot become verified or promotable until bounded
reconciliation succeeds. Add create/replay/conflict/tracking-sync/latency
metrics, runbook and feature flag; record evidence before agent tool activation.
```

## Plan 3.2 — cancellation, retry and recovery

**Contract.** Cancellation is cooperative and terminal evidence is preserved.
Retry/repair creates a child attempt/branch with explicit change and parent
citations; it never resets or overwrites a completed run.

### S3-P02A — lifecycle and child-lineage contract

```text
Define allowed cancellation and retry/branch transitions across ExecutionRequest,
WorkflowRun, PipelineRun, stage runs and MlJob. Specify propagation order,
terminal/no-op/conflict behavior, cancellable external operations, child attempt
number, change digest, parent evidence, inherited immutable plan and new budget.
Map every state to API/event/audience-safe status. Add pure transition tests.
```

### S3-P02B — cooperative cancellation implementation

```text
Implement request_cancel through one application service with authorization,
ETag/idempotency, cancellation timestamp/actor/reason and event. Workers check
before claim/expensive stage and after external/compute return, stop scheduling
new stages, release reservations and terminalize consistently. Quarantine partial
unverified outputs rather than publish. Test concurrent finish/cancel, repeated
cancel, lost worker and non-cancellable section.
```

### S3-P02C — retry/branch command implementation

```text
Implement retry/branch as a new child ExecutionRequest/PipelineRun/MlJob with
parent ID, attempt, hypothesis/change schema/digest and cited failure/evidence.
Reuse immutable valid inputs/artifacts by reference only after digest/access
validation; never alter parent rows. Enforce maximum attempts/portfolio budget
and same-key replay. Test changed payload conflict, parent wrong state, cross-
workspace parent and concurrent retry.
```

### S3-P02D — lifecycle API, SDK and aggregation

```text
Add cancel/retry endpoints and SDK helpers with 202 pending, 200 replay/no-op and
stable conflict/denial responses. Aggregate parent/child status/events without
hiding individual terminal reasons. Use opaque event cursor and bounded tree
depth/page. Update web callers only after contract tests. Add OpenAPI examples
for cancel requested, cancelled, retry child and ambiguous recovery.
```

### S3-P02E — recovery and artifact gate

```text
Inject failures at cancellation propagation, stage completion, artifact upload,
event append and child enqueue. Reconcile orphan jobs/runs, ambiguous partial
objects and stuck cancel requests deterministically. Prove verified parent
artifacts/evidence remain immutable and quarantined outputs cannot be consumed.
Add lifecycle/recovery metrics and runbook; record end-to-end evidence.
```

## Plan 3.3 — exact approvals and risk policy

**Contract.** Approval binds actor, workspace, action schema/version, canonical
payload digest, risk, expiry and one-time use. It never expands authentication,
membership, data policy or tool authority.

### S3-P03A — approval/risk ADR and schemas

```text
Define risk tiers for sensitive read, compute, internal mutation, publish/export
and external action, with required capability, approver separation, expiry and
reauthentication. Define ApprovalRequest/Decision/Consumption and exact summary
schemas, canonicalization/versioning and states requested/approved/rejected/
expired/consumed/revoked. Specify no-approval and always-approval classes. Add
digest golden vectors and pure policy tests; no action execution yet.
```

### S3-P03B — approval persistence and constraints

```text
Add tenant-scoped approval request/decision/consumption records with target
action type/schema/payload digest, requester/approver, risk/policy version,
expiry, reason and timestamps. Enforce immutable decisions, one terminal decision,
one consumption and same-workspace principals/resources. Store only bounded safe
summary, not secrets/raw payload. Add migration, race and cross-tenant tests.
```

### S3-P03C — ApprovalService and atomic consumption

```text
Implement request/review/revoke/expire/validate_and_consume services. Recompute
current action digest and policy, re-authorize executor/approver and source
versions at execution, then consume in the same transaction as command intent.
Any field/version/policy/auth change invalidates approval. Test concurrent
consumption, stale membership, self-approval, clock expiry, replay and transaction
rollback without consumed-orphan state.
```

### S3-P03D — approval API and safe presentation contract

```text
Add bounded `/v1/approvals` list/get/request/review/revoke resources with ETag,
idempotency, explicit workspace and capability. Response shows exact human-
reviewable summary, risk, expected effect/cost, expiry and changed-since warning,
without secrets/internal payload. Add SDK types and negative tests for resource
substitution, stale decision and unauthorized reviewer. No external actions yet.
```

### S3-P03E — adversarial approval gate

```text
Test canonicalization ambiguity, Unicode/numeric/order changes, one-field
substitution, reused approval for another workspace/action/version, self-review,
concurrent consume, expired/revoked account and crash around consumption/command.
Verify logs/events provide nonrepudiable IDs/digests but no sensitive body. Add
pending/expiry/denial/consume metrics, cleanup/runbook and emergency mutation
disable before tools use approvals.
```

## Plan 3.4 — activate bounded agent command tools

**Contract.** Tool handlers adapt validated Scope 2 proposals to the same
command services used by API/web. Initial tools are enumerated and independently
kill-switchable. No generic update, SQL, code, connector or external-action tool.

### S3-P04A — write-tool policy and catalog release

```text
Define exact versioned tools for draft ProblemSpec version, validate readiness,
create/cancel/retry model build, request approved export and create corrective
child proposal. For each specify input schema, resource/version preconditions,
capability, risk/approval, idempotency, budgets, allowed states and result schema.
Publish a candidate tool release disabled by default. Reject arbitrary patch
paths/unknown fields in contract tests.
```

### S3-P04B — ProblemSpec/readiness command adapters

```text
Implement draft-version and readiness tools over existing ProblemSpec/target/
data/scientific services. Agent provides a typed proposal ID/base digest; service
re-authorizes and creates an immutable draft child version or returns validation.
No in-place edit or self-approval. Record tool call, command/resource IDs and
citations, append the corresponding project decision and refresh lifecycle
impact. The decision record never substitutes for the command. Test stale base,
changed dataset, invalid target and cross-workspace.
```

### S3-P04C — model-build lifecycle command adapters

```text
Implement create/cancel/retry adapters over Plan 3.1/3.2 services with exact
payload digest, current policy, budget and approval where required. Return
durable resource/status links, not a fabricated completion. Re-check tool release
and membership at dispatch. Test duplicate calls, policy revoked after planning,
approval substitution, cancellation race and worker failure.
```

### S3-P04D — export request boundary

```text
Define export request as a durable typed command referencing authorized immutable
artifacts/data policy, intended audience/format/expiry and exact approval when
required. Implement request/metadata only using existing artifact service and
job boundary; delivery remains private/bounded. Prevent agent-chosen storage
paths, arbitrary URLs or raw-row export. Test classification, deleted source,
changed artifact and duplicate request.
```

### S3-P04E — tool execution security and release gate

```text
Run the shared tool contract plus two-workspace, revoked membership, stale
proposal, unknown tool/version, malformed patch, budget, approval, duplicate job,
provider injection and cancellation tests. Prove every mutation maps to one
application command/audit/event and agents cannot call private services. Add
per-tool success/denial/latency/cost metrics, runbook and staged allowlist.
```

## Plan 3.5 — bounded experiment iteration

**Contract.** A child experiment changes one declared hypothesis/plan dimension,
inherits immutable parent evidence, has a separate budget, and stops before
holdout-driven tuning or unbounded search. MVP selection is DCLab policy-owned;
do not add Optuna or another optimization service in this plan.

### S3-P05A — iteration policy and hypothesis schema

```text
Define allowed change classes (data/preparation/feature/candidate/metric/resource)
and prohibited changes (target leakage, holdout tuning, evidence rewrite, code).
Child proposal records hypothesis, one canonical typed diff, parent citations,
expected signal, validation, estimated budget, maximum depth/portfolio and stop
rule. Add pure tests for one-change discipline, equivalent digest, holdout and
budget policy.
```

### S3-P05B — portfolio budget and selection service

```text
Implement atomic portfolio accounting across parent and all descendants for
runs, candidates, CPU/GPU time, wall time, tokens/cost and concurrent children.
Require a valid proposed diff and deterministic scientific validation before
reservation. Selection of next proposal is code-owned/ranked by policy, not an
unbounded agent loop. Test concurrent children, cancellation, failure settlement
and exhausted global cap.
Candidate selection must remain the existing deterministic bounded portfolio
policy. Record an explicit deferred decision for Optuna; preserve the existing
dormant scientific source enum for compatibility but do not add its sampler,
study database, dashboard, provider-specific fields or execution path in Scope 3.
```

### S3-P05C — child experiment command and lineage

```text
Extend model-build command to create an immutable child plan/run from a validated
iteration proposal. Bind parent run/evidence, base/new plan digests, hypothesis,
budget and agent/tool versions. Never copy mutable result rows or reveal holdout
metrics before the allowed gate. Add same-key replay, stale parent, invalid plan
and cross-workspace tests.
```

### S3-P05D — stop/evaluation rules and agent feedback

```text
Implement stop on budget/depth/count/time, repeated-equivalent proposal,
insufficient improvement, instability, safety failure, user cancel or policy
revocation. Feed only CV/allowed evidence into the next proposal; final holdout
is evaluation, not tuning input. Persist decision reasons/citations through
ProjectDecisionService and expose a safe summary to the supervisor. Record why
an attempt continued/stopped, which constraint was met or missed and which
candidate was selected/rejected. Test ties, noisy gains and late results.
```

### S3-P05E — scientific/economic iteration gate

```text
Run synthetic portfolios covering useful correction, no improvement, oscillation,
duplicate hypothesis, failed child, concurrent proposals, budget exhaustion and
tempting holdout leakage. Assert bounded termination, immutable lineage and
honest cost/quality accounting. Compare to single deterministic build; add
portfolio dashboards/runbook/kill switch and enable only for allowlisted plans.
```

## Plan 3.6 — unified command and approval experience

**Contract.** Web, SDK, CLI and agent display/request the same command, approval,
lifecycle and project-decision resources. The three product views resolve the
same IDs/versions. UI never infers approval validity or command completion.

### S3-P06A — shared command/approval client models

```text
Complete Python and TypeScript models for build command, lifecycle, proposal,
approval, project decision, comparison, budget/cost, denial and event projections. Generate or manually map
from OpenAPI with parity checks. Include idempotency, ETag and exact resource
links; exclude internal handler/storage/provider detail. Add schema fixtures for
all terminal and pending states.
```

### S3-P06B — build and iteration web workflow

```text
Route existing lab/model-build creation through the canonical `/v1` command.
Show active workspace, immutable inputs/versions, validation, estimated bound,
idempotent submission, status/events, cancel/retry and child lineage. Handle
409/422/429, reload, duplicate click and race with completion. No optimistic
“running/succeeded” state before server confirmation. Reuse lifecycle and
implementation deep links so feature/formula/config/environment evidence is
inspectable without making code the primary workflow.
```

### S3-P06C — exact approval review experience

```text
Build requester/reviewer views for exact summary/digest version, risk, expected
effect/cost, evidence, expiry and changed-since status. Require capability,
separation and explicit confirm/reject reason; use ETag and refresh on conflict.
Never display secrets/raw payload or allow approval by hidden default. Add
keyboard/accessibility and concurrent-review component tests.
```

### S3-P06D — agent proposal-to-command experience

```text
In Agent Studio, show typed proposal diff, deterministic validation, citations,
budget and whether approval is required. Provide Accept, Reject, Compare and
Modify: accept requests the existing command where applicable; reject records a
decision only; compare uses immutable runs; modify creates a new typed proposal.
No control executes a client-side patch. Display durable command/run/decision
lineage and policy denial. Test stale proposal, altered resource, revoked
membership, approval expiry and workspace switch without leaking prior state.
```

### S3-P06E — cross-surface E2E gate

```text
Run equivalent web, SDK, CLI preview and agent journeys for create/replay/conflict, approval,
cancel, retry, child iteration, denial and recovery. Assert identical resource
IDs/state/events/audit/decisions, lifecycle impact and audience-safe outputs.
Deep-link conversation/workflow/implementation views. Run accessibility and DOM/
network leakage scans. Document support/operator workflow, flags and rollback
before controlled-write preview.
```

### S3-P06F — early ML engineer SDK/CLI preview

```text
Inspect `packages/dclab_client` and the Scope 5 CLI package plan. Extend the same
HTTP-only client with the bounded Core ML path: inspect project lifecycle and
decisions, validate/propose/build, list/watch/cancel/retry, compare candidates,
download verified reproduction artifacts and inspect cost. Add the eventual
`packages/dclab_cli` skeleton only if absent, with `project`, `lifecycle`,
`decision`, `build` and `model compare` commands over that SDK; never import API
internals or create a temporary second CLI. Use the explicit non-browser token
flow for an internal allowlist only; no public machine-identity claim before
Scope 5. Define JSON/JSONL, exit codes, timeout/signal/idempotency, explicit
workspace and redaction now so Scope 5 can extend compatibly. Add mocked/live API,
two-workspace, cancellation, output-golden and secret-scan tests. Mark packages
preview/private and publish nothing before Scope 5 supply-chain gates.
```

## Plan 3.7 — controlled-write release gate

**Contract.** Scope 3 passes only with existing scientific tests plus new
authorization, approval, idempotency, recovery and agent-mutation campaigns.

### S3-P07A — scientific regression campaign

```text
Run all preparation/leakage/validation/CV-selection/holdout/reproducibility/
evidence-lock tests plus new command and child iteration paths. Add cases proving
agents cannot alter locked plans/results, tune from holdout, bypass readiness or
create unsupported candidates. Compare outputs for unchanged deterministic
inputs and investigate every drift before proceeding.
```

### S3-P07B — concurrency and failure-injection campaign

```text
Stress duplicate/conflicting creates, concurrent approvals/consumption, cancel/
finish race, retry race, child budget allocation, worker loss and failures around
each transaction boundary. Prove exactly one durable intent/consumption/effect,
terminal reconciliation and no orphan budget/job/artifact. Use real PostgreSQL
and production-shaped worker processes.
```

### S3-P07C — authorization and mutation adversarial campaign

```text
Attempt cross-workspace/resource substitution, stale membership, self-approval,
payload canonicalization tricks, forged proposal/citation/tool/version, prompt
injection, direct private route/service use and unsupported patch paths. Assert
fail-closed stable errors, safe audit and zero unauthorized mutation. One failure
blocks release regardless of other quality results.
```

### S3-P07D — load, quota and operational recovery

```text
Measure command latency, job queue/fairness, cancellation observation, approval
age and bounded iteration under recorded workloads. Validate quotas/backpressure,
worker drain/restart, stuck intent reconciliation, artifact quarantine and every
kill switch. Establish initial alerts from observed limits and execute runbooks
with named owners.
```

### S3-P07E — controlled-build go/no-go

```text
Run migrations, backend/SDK/web/browser/full-system suites in a production-shaped
environment and complete one allowlisted proposal -> approval -> build -> cancel/
retry or child -> verified result flow without DB intervention. Publish exact
release/tool/policy versions, evidence and limitations. Enable only enumerated
commands for the allowlist; external actions and arbitrary code remain off.
Proceed to Plan 3.8 only after this controlled-build gate passes.
```

## Plan 3.8 — model registration, batch prediction and monitoring MVP

**Contract.** Reuse the verified Plan 3.0 ports plus `ModelAsset`, `ModelVersion`, `PredictionTask`, `Prediction`,
`RuntimeEnvironment`, `CodeSnapshot`, artifact/reproducibility/evidence services,
`admin_model_registry_service.py`, `admin_monitoring_service.py`, `ml/predict.py`,
ExecutionRequest/MlJob and the lifecycle/decision services. Deliver one narrow
batch path; do not add online REST/streaming/edge serving, arbitrary model code,
automatic retraining, multi-cloud provisioning or a second registry.
MLflow stores detailed tracking/package metadata behind TrackingPort; DCLab owns
release, promotion, rollback, batch, monitoring, authorization and audit state.
Use focused test homes `apps/api/tests/test_model_release_contract.py`,
`test_model_release_persistence.py`, `test_model_release_service.py`,
`test_batch_prediction_service.py`, `test_model_monitoring_service.py`,
`test_model_operations_api.py`, `packages/dclab_client/tests/test_model_operations.py`
and `apps/web/e2e/core-ml-model-operations.spec.ts`; extend a proven equivalent
owner and document the substitution rather than adding duplicate suites.

### S3-P08A — batch-release architecture and contracts

```text
Inventory existing model registry, prediction, artifact, runtime/code snapshot,
monitoring and `/v1` behavior. Write an ADR defining ModelRelease,
BatchPredictionRun, FeatureContract and MonitoringWindow using existing records
where sufficient and referencing the provider-neutral Plan 3.0
TrackingReference/ModelPackageManifest. Define release
draft/validating/ready/active/rolled_back/failed
and batch queued/running/succeeded/failed/cancelled states; exact ModelVersion,
model/preprocessor/feature manifest/environment/code digests; input dataset
version; output artifact; baseline/current windows; permissions, idempotency,
approval, quotas and rollback. Choose one supported CPU batch format/use case.
Define fail-closed typed settings and `.env.example` entries including
`DCLAB_MODEL_RELEASE_ENABLED=false`, `DCLAB_BATCH_PREDICTION_ENABLED=false`,
`DCLAB_MODEL_MONITORING_ENABLED=false` and explicit maximum input rows/bytes,
output bytes, execution seconds and concurrent runs; production cannot boot with
the features enabled and missing bounds. Reuse the private MLflow URI/credential,
separate backend/object-prefix and tracking-required settings defined by Plan
3.0; never add a browser-visible MLflow URL or filesystem `mlruns` fallback.
Map every lifecycle edge and rejected alternative. Add pure contracts/transitions
and compatibility tests only; do not create a parallel model registry.
```

### S3-P08B — persistence and tenant integrity

```text
Discover the live Alembic head and first prove whether current ModelVersion,
PredictionTask/Prediction and observability rows can carry the contract. Add only
missing tenant-scoped ModelRelease, BatchPredictionRun, FeatureContract and
MonitoringWindow/metric records, with immutable version/digest references,
ExecutionRequest/MlJob/artifact links, state/timing/usage/safe failure, rollback
lineage, provider-neutral tracking reference/package-manifest IDs and current-
release uniqueness as designed. Tracking references store bounded DCLab-owned
identity/digest/state only—not MLflow parameters, metrics or artifact payloads.
Enforce composite workspace/
project/model/dataset relationships, terminal immutability, one idempotency digest
and indexes for active release, batch queue/history and unevaluated windows. Test
empty/live-head migration, cross-tenant IDs, concurrent activation/rollback,
retention and forward repair. Do not copy model or prediction bodies into JSON.
```

### S3-P08C — immutable package, environment and feature-contract verifier

```text
Implement a verification service over artifact_service, reproducibility_service,
evidence locks, ModelVersion, RuntimeEnvironment, CodeSnapshot and feature
manifest/lineage. Resolve the Plan 3.0 ModelPackageManifest and reconciled
TrackingReference through application ports, then verify local artifact digests
against the authorized object store. A release is ready only when model/
preprocessor/feature artifacts exist, digests/MIME/size match, environment/code
are pinned, deterministic build verification passed and the DCLab FeatureContract
defines ordered names, logical/physical types, nullable/category/unknown/range
handling and prediction-time availability. Compile that stored contract to
Pandera at the worker boundary and record compiler/library versions; never store
Pandera schemas. Load only skops-supported trusted types or the explicitly
allowlisted native CatBoost/XGBoost/LightGBM formats. Quarantine pickle/joblib,
unknown code and unsupported packages; never dynamically import an unverified
artifact or trust an MLflow flavor loader to bypass policy. Return typed DCLab
reason codes and append lifecycle/audit events. Test missing/tampered artifacts,
incompatible environment, reordered/type-drifted features, unsafe serialization,
untrusted skops types, stale tracking sync, leakage-only features, stale evidence
and two workspaces.
```

### S3-P08D — release and rollback command services

```text
Implement create/validate/activate/rollback through one ModelReleaseService using
current authorization, exact ModelVersion/feature/environment digests, policy,
idempotency, ETag and approval when required. Activation atomically selects one
eligible release for the supported batch target and appends audit/event plus a
ProjectDecisionRecord explaining promotion; it never mutates ModelVersion.
Rollback creates/activates a new release transition referencing the prior safe
release and records why. Add transaction failure injection, same/different digest
replay, stale approval, revoked membership, concurrent promotion and no-safe-
rollback tests. Provide independent batch-release kill switch.
```

### S3-P08E — bounded batch inference job

```text
Register one code-owned `batch_prediction.run.v1` handler. The command binds
active release, authorized immutable input Dataset/Artifact version, feature
contract, output schema, resource budget, idempotency and optional label column
excluded from features; atomically create intent/run/job/event. Worker streams
bounded input through `batch_inference_read.v1` on DataScanPort, verifies digest
and each Arrow batch against the compiled Pandera schema, loads only the
verified supported package through ModelPackageLoader (never generic MLflow
model loading),
uses `ml/predict.py`/current serving artifact contract, writes predictions as a
private immutable artifact and persists safe counts/distribution/usage—not raw
values in events/logs. Support cooperative cancel, deadline, retry/reconciliation
without duplicate outputs. Test exact predictions on frozen fixtures, column
order/type/category failures, large streaming bounds, worker loss, duplicate job,
quarantined input/output and cross-workspace access.
```

### S3-P08F — monitoring windows and investigation proposals

```text
Implement deterministic reference/current MonitoringWindow evaluation for input
schema/quality/drift and prediction distribution; add performance/calibration
only when delayed labels and metric contracts are valid. Version every method,
threshold, population, time window and multiple-testing policy; distinguish no
data, insufficient volume, drift, degradation and infrastructure failure. Route
bounded approved columns through the Plan 3.0 DriftCalculator; Evidently is the
in-process implementation and returns only normalized DCLab result types. Do not
deploy an Evidently service/UI/database or accept library-default thresholds.
Create its reference/current inputs through the registered S0-P09
`drift_window_prepare.v1` scan operation. Neither the API, agent nor Evidently
supplies SQL or artifact paths; DataScan records template/version, artifact
digest, limits, batch counts and deterministic result digest for lineage.
An alert appends DCLab evidence and exposes an authorized Investigate action.
When the applicable S2-P12H mode is VERIFIED, an explicit user/SDK request may
start operations_drift Deep Investigation; before that gate the same
deterministic report and human review flow completes this plan. Extend only its existing
versioned read tool bundle with inspect_model_package, get_batch_run_metrics and
get_drift_report over these application query services.
Batch metrics expose bounded stage/queue/execution latency, throughput and
resource-usage evidence so the Copilot can investigate regressions without log,
host or telemetry-backend access.
The Copilot returns a typed, cited retraining/remediation/rollback proposal for
deterministic validation and review; it never receives register/activate/batch/
rollback/approval/worker-dispatch tools and never triggers automatic retraining
or deployment. Test stable/shifted/missing/late/corrected-label, false-alarm,
low-volume, stale baseline, cross-workspace IDs, revoked read scope and policy-
revocation cases. Emit bounded metrics/alerts and add independent monitoring-
investigation disable/recompute runbooks.
```

### S3-P08G — `/v1`, SDK/CLI and synchronized UI

```text
Add cohesive `/v1/model-releases`, batch-prediction runs, monitoring windows,
investigations and rollback resources using standard pages/errors/ETag/
Idempotency-Key/status links. Extend the existing Python client and S3-P06F CLI,
not parallel packages, with register/validate/activate/batch/watch/monitor/rollback.
Extend current model registry/monitoring pages and project workspace with release,
batch and monitoring lifecycle nodes plus Conversation/Workflow/Implementation
deep links. Show exact model/feature/environment/input/output versions, status,
metrics, costs, decisions and rollback target; no storage paths/raw rows/client-
inferred success. Add OpenAPI parity, component/accessibility and equivalent
web/SDK/CLI/agent two-workspace journeys. Define 201 first create, 200 read/
idempotent replay, 202 accepted async validation/batch/monitor/rollback, 409
digest/state/precondition conflict, 422 feature/input validation and 429 quota,
with anti-enumerating 403/404 behavior. Extend the S2-P12 operations_drift
UI/result schema and SDK tool manifest only when its gate is VERIFIED; never make
that independently disableable component a route/schema dependency for model operations. Public
API/SDK/CLI/UI contracts expose DCLab identifiers and normalized evidence only:
no MLflow run URL/type/storage locator, Pandera schema/error object, Evidently
report object or backend credential. Display an explicit Investigate action when
enabled and show model/release/batch/window citations; accepting its proposal
still creates only a ProjectDecisionRecord or separate canonical command request
through existing review/approval services.
```

### S3-P08H — Core ML MVP model-operations gate

```text
Run migrations, deterministic package verification, batch prediction golden
fixtures, idempotency/concurrency, cancellation/restart, artifact tamper, feature
skew, drift/late-label, authorization, approval, rollback and full scientific
regressions in a production-shaped environment. Have one data scientist promote
a verified result and inspect/compare its rationale; have one ML engineer perform
the same release, batch, monitor and rollback path through SDK/CLI without DB
access. Reconstruct lifecycle and decision history exactly, scan logs/artifacts/
clients for secrets/raw rows and measure batch latency/memory/cost. Add dashboards,
alerts, runbooks and independent release/inference/monitor flags. Publish the Core
ML evidence and limitations. Prove private MLflow outage/replay/reconciliation,
strict generated Pandera validation, safe-format rejection, normalized Evidently
stable/drift/insufficient/error results and clean provider-boundary tests. Also
prove profiling/leakage/slice/drift scan parity, Arrow batch/output bounds,
timeout/cancellation cleanup, malicious SQL/path/extension rejection, no
persistent DuckDB catalog and no Polars dependency under production settings.
Scope 3 development passes without Plan 2.12, but the production-MVP go/no-go
requires S2-P12H. When it is VERIFIED, additionally include one explicit
operations_drift run and prove read-only
SDK access, deterministic-finding precedence, claim-level citations, bounded
cost/cancellation and proposal-only completion with no raw-LangGraph, command,
approval or deployment invocation. Verify the release contains no SHAP
dependency, job, artifact, API or UI and that any legacy SHAP enum remains
clearly unsupported. Online serving,
automatic retraining, business actions and arbitrary code remain disabled and
do not block this gate.
```
