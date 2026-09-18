# Scope 0 execution prompts — foundation closure

Use the common preamble in `README.md` and every rule in
`EXECUTION_STANDARD.md`. Execute plans in order. For already-implemented work,
inspect the current diff and evidence first; verify or repair it instead of
creating a second implementation.

## Scope boundary

- Preserve the deterministic ML and evidence behavior.
- Do not introduce a user-visible agent, new LLM authority, connector, or
  external side effect.
- The expected working-tree database head is already beyond the original 0054
  baseline. Always discover the live head before adding a revision.
- Existing evidence IDs `S0-P01A`, `S0-P01B`, `S0-P02A`, `S0-P02B`, and
  `S0-P03A` retain their original meaning.
- Scope 0 does not install an agent framework. S1-P01A later pins LangGraph and
  its PostgreSQL checkpointer; nested agent loops remain prohibited. Ordinary
  Pydantic is the default contract layer, and a no-tool single-response
  PydanticAI leaf or any alternate runtime requires its later explicit ADR.
  The separately isolated Deep Investigation and OpenAI Agents adapter plans
  begin in Scope 2 and do not alter any Scope 0 implementation.
- Scope 0 records the approved ML-platform boundary in
  `ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`: DCLab remains authoritative. The
  bounded DuckDB `DataScanPort` begins in Plan 0.9; private MLflow, Pandera,
  Evidently and safe model formats begin in Plan 3.0. Do not add Polars, W&B, a
  second database/registry, deployment path, experiment tracker, `dlt`
  connector runtime or SHAP capability here.

## AWS/GCP portability requirements

Scope 0 establishes portable primitives even though it deploys no cloud
infrastructure. Preserve the existing `ObjectStorage` contract and both S3/GCS
adapters; authentication/session, workspace, PostgreSQL, jobs, evidence and
DataScan behavior cannot depend on a cloud SDK or resource name. Configuration
uses opaque object/secret/key references and fails closed for unsupported
providers. Add or retain equal S3/GCS adapter contract tests for digest,
version/precondition, signed-access expiry, denial and safe errors. Local/CI
fakes must exercise the same semantics required later on EKS and GKE.

## Plan 0.1 — current truth package

**Contract.** Own current-state truth in `scripts/record_repo_truth.py`,
`scripts/check_truth_drift.py`, `.github/workflows/ci.yml`, `contracts/`, and
`docs/verification/`. Generated facts include Git/Alembic/model/OpenAPI/test/web
inventories and must be deterministic. This plan changes no product behavior.

### S0-P01A — regenerate the repository truth baseline

```text
Inspect the current checkout and run the repository truth workflow. Record the
exact product SHA, documentation SHA, dirty state, remote relationship, Alembic
heads/revision count, SQLAlchemy table count, OpenAPI paths/operations and /v1
surface, source/test inventories, and same-SHA CI evidence. Run the supported
backend, SDK, frontend lint/type/build and browser checks, separating observed
results from historical reports. Update only CURRENT verification/index facts;
keep old evidence immutable and labeled HISTORICAL. Do not change application
behavior. If scripts already exist, repair them instead of adding alternatives.
```

### S0-P01B — automate truth drift detection

```text
Make CI fail deterministically for multiple Alembic heads, duplicate revisions,
model/migration mismatch, unreviewed /v1 breakage, SDK contract drift, stale
generated facts, broken current-doc links, tracked runtime artifacts, unsafe
production defaults, or contradictory CURRENT claims. Add synthetic tests that
prove each detector fails for a bounded fixture and passes on the real tree.
Snapshots must have a documented refresh command and intentional-change review
path. Do not rewrite historical evidence or depend on network access in PR CI.
```

### S0-P01C — reconcile truth ownership and generated artifacts

```text
Audit README, docs indexes, API/database/ERD/migration documents, Make targets,
and CI so each mechanically derived fact has exactly one generator and one
canonical checked artifact. Remove duplicated hand-maintained CURRENT counts or
replace them with links to the generated source. Add a manifest containing
generator version, source SHA and artifact digest. Test idempotent regeneration
and a clean git diff after two consecutive runs. Do not redesign any API/schema.
```

### S0-P01D — close the baseline gate

```text
Run the complete truth, migration, backend, SDK, web and browser gate from a
clean environment. Verify no secret/object-store/Playwright output is tracked,
the documented commands work, and CI on the exact SHA agrees with local facts.
Create or refresh the S0-P01 evidence record with observed commands, durations,
known warnings and owners. Repair only truth-tooling defects. Mark the plan
VERIFIED only when regeneration produces no unexplained diff.
```

## Plan 0.2 — secure browser session and BFF

**Contract.** Reuse `api/auth.py`, `api/deps.py`, `services/session_service.py`,
`csrf_service.py`, `login_throttle.py`, `recovery_service.py`, `config.py`,
`middleware/security.py`, the `auth_sessions` migration lineage, web BFF route,
`session.ts`, and `session-provider.tsx`. Browser auth uses an opaque HttpOnly
cookie; non-browser API tokens remain a separate explicit flow. Session states
are `active`, `revoked`, and `expired`; rotation invalidates the predecessor.

### S0-P02A — server-issued session and BFF foundation

```text
Preserve or complete the accepted browser-session ADR. Implement one opaque,
hashed-at-rest server session with idle/absolute expiry, rotation, revocation,
logout and safe lookup. Issue it only in an HttpOnly cookie with deployed-mode
Secure, explicit SameSite and narrow path/domain. Route browser API access
through `apps/web/app/api/backend/[...path]/route.ts`; JavaScript must never read
or attach a long-lived bearer. Keep `POST /auth/tokens` or its current equivalent
for non-browser clients. Add fail-closed production config checks and focused
PostgreSQL/service/API tests. Do not add social login or SSO.
```

### S0-P02B — session, CSRF, CSP, abuse and recovery hardening

```text
Preserve or complete the accepted hardening ADR. Require CSRF token plus
trusted-origin validation on every cookie-authenticated mutation, apply CSP and
security headers, throttle credential endpoints with uniform errors, and model
bounded recovery/verification tokens without revealing account existence. Test
fixation, rotation, injected-clock expiry, logout, revocation, suspended users,
CSRF, hostile Origin, proxy/TLS behavior, XSS-sensitive rendering and redaction.
Keep incomplete email delivery disabled and documented rather than faking it.
```

### S0-P02C — persistence, constraints and cleanup reconciliation

```text
Review the live auth-session/recovery migrations and models. Ensure raw tokens
are never stored, hashes are unique, user/session lineage is enforced, expiry
and revocation queries are indexed, rotation lineage cannot cross users, and
cleanup is bounded and race-safe. Add an idempotent cleanup service/job using a
code-owned handler only if durable cleanup is absent. Test empty and previous-
head upgrades, concurrent rotation/logout, expired cleanup and forward repair.
Do not introduce Redis merely for sessions.
```

### S0-P02D — browser/BFF contract completion

```text
Audit every web request path, middleware branch and login/logout flow. Remove
remaining bearer-token parsing, localStorage/cookie helpers and client-side role
authority. Define BFF error/cookie forwarding, request-ID propagation, upload/
download streaming bounds and cache-control. Test anonymous, authenticated,
expired, revoked and backend-unavailable behavior plus login/logout/reload E2E.
The BFF must not become a second authorization layer or log credentials/bodies.
```

### S0-P02E — operations, configuration and incident controls

```text
Add typed settings and `.env.example` entries for cookie name/security, idle and
absolute TTL, CSRF, trusted origins, session/recovery hashing secrets, throttle
bounds and emergency browser-session disable. Validate production settings at
boot. Emit bounded login/session/CSRF metrics and safe audit events with reason
codes, never identifiers or tokens at unsafe cardinality. Add cleanup, suspected
theft, secret rotation and mass-revocation runbooks. Unit-test configuration and
redaction; do not depend on production secrets in CI.
```

### S0-P02F — adversarial completion gate

```text
Run a two-user/two-workspace browser and PostgreSQL matrix covering fixation,
cookie theft/replay after rotation, CSRF, Origin/Host confusion, XSS token
access, concurrent logout, password/reset enumeration, expired cleanup and
server restart. Confirm API bearer clients still work without browser cookies.
Verify the kill switch and recovery runbook in a production-shaped environment.
Repair only Plan 0.2 defects and record exact evidence before marking complete.
```

## Plan 0.3 — authoritative workspace and capabilities

**Contract.** Reuse `workspace_selection_service.py`, authorization/capability/
entitlement services, `api/deps.py`, `api/workspaces.py`, current session row,
the BFF, `active-workspace.ts`, `api-client.ts`, `WorkspaceSelector.tsx`, and
query/session providers. Selection is presentation context; current membership
and server-side capability resolution authorize every operation.

### S0-P03A — active-workspace selection contract

```text
Preserve or complete the accepted workspace-selection ADR. Support zero, one
and multiple memberships; persist a selected workspace only after current
membership validation; propagate it through the BFF as `X-Workspace-Id`; and
keep the Python client explicitly scoped. Add a visible selector and active-
workspace notice on mutations. Switching must clear or re-key tenant caches.
Unauthorized/stale selections return the stable safe error and never prove
access. Test membership removal and session restoration.
```

### S0-P03B — central capability authority

```text
Inventory every route/service/UI capability check and define one versioned
server capability matrix based on current membership, role, entitlement,
resource state and optional feature flag. Implement resolution/caching with a
bounded invalidation strategy on membership/role/entitlement change. `/v1/me`
returns displayable effective capabilities, not an authorization token. Remove
business decisions based on decoded token role. Test revoked/suspended members,
role change during a session and direct API calls that bypass hidden UI.
```

### S0-P03C — tenant-aware frontend state and navigation

```text
Key every React query/cache/resource URL and optimistic state by active
workspace. On switch, cancel in-flight tenant requests, clear sensitive state,
reload capabilities, route safely, and announce the change accessibly. Hide or
disable navigation from server capabilities while preserving server denial as
authority. Add component tests for zero/one/many workspaces and E2E proving no
old-workspace data flashes after a slow concurrent switch. Avoid a second role
matrix in TypeScript.
```

### S0-P03D — API/client propagation and concurrency

```text
Audit all `/v1`, legacy API, upload/download, event-polling and SDK calls for
explicit workspace context. Centralize validation in dependencies/services;
resource workspace and selected/header workspace must agree. Define behavior
for missing, malformed, unauthorized and conflicting workspace IDs. Test two
tabs selecting different workspaces, concurrent membership revocation, bearer
client explicit scoping and BFF header spoof attempts. Never trust a browser-
supplied workspace without membership lookup.
```

### S0-P03E — isolation and operational gate

```text
Run a route inventory and two-workspace matrix across reads, mutations, files,
events, errors, metrics and caches. Verify current membership changes take
effect within the documented invalidation bound. Emit low-cardinality denial/
selection metrics and safe audit events; add a cache/invalidation runbook and
feature rollback that does not re-enable global access. Record observed E2E and
PostgreSQL evidence before closing Plan 0.3.
```

## Plan 0.4 — legacy tenant and raw-surface closure

**Contract.** Inspect `SimulationRun` and related models/migrations,
`domain/simulation.py`, `services/insight_query.py`, `api/simulations.py`,
`api/insights.py`, business/client routes, observability/event endpoints, and
the SDK. Every customer-visible record must resolve one workspace or be removed
from customer surfaces.

### S0-P04A — legacy lineage decision and migration design

```text
Inventory every global SimulationRun/Insight read and write, caller, fixture and
UI route. Write an ADR choosing tenant-scope, import-to-canonical lineage, or
retirement for each legacy surface. Define deterministic backfill evidence,
ambiguous-row quarantine, compatibility window and rollback. Do not guess a
workspace for historical rows and do not rewrite immutable scientific evidence.
Stop after the ADR if ownership cannot be proven.
```

### S0-P04B — enforce tenant lineage and remove global reads

```text
Implement the accepted additive migration/service changes. Add workspace and
composite constraints or disable/remove the customer route as decided; backfill
only provable rows and quarantine ambiguity. Replace “latest globally” queries
with authorized workspace/resource queries. Keep internal admin access behind a
named capability and audit. Test previous-head upgrade, cross-workspace IDs,
empty history and concurrent creation. Do not create parallel canonical models.
```

### S0-P04C — raw event and client audience closure

```text
Inventory legacy events, observability responses, exception details, SDK types
and business UI payloads. Create audience-safe projections that omit internal
paths, handler keys, stack traces, storage keys, prompts, raw provider bodies
and cross-tenant cardinality. Enforce capability and workspace before lookup,
including 404/403 anti-enumeration policy. Add snapshot/contract tests for admin,
developer and client audiences. Preserve operator detail in protected telemetry.
```

### S0-P04D — retirement and isolation gate

```text
Run migration, API inventory, two-workspace and browser tests against all legacy
simulation/insight/event routes. Prove no global customer query remains and no
SDK/UI path exposes a retired capability. Add deprecation/removal notes and a
forward-repair runbook for quarantined rows. Remove feature flags only after the
compatibility window; record evidence and known retained admin-only surfaces.
```

## Plan 0.5 — classification, quarantine and retention foundation

**Contract.** Reuse Dataset/DatasetColumn/DataSource/DataAccess/IngestionRun and
Artifact lineage, `dataset_column_service.py`, ingestion/materialization and
artifact services. Unknown classification or LLM exposure is deny. Large files
remain private objects; quarantine is a state, not a public bucket.

### S0-P05A — policy schema and fail-closed bootstrap

```text
Define versioned enums/schemas for sensitivity, LLM exposure, retention class,
residency and classification source/confidence. Inventory nullable DatasetColumn
policy fields and write an expand/backfill/enforce plan: existing null/unknown
becomes denied, never safe. Add database checks/indexes and service-level policy
resolution with dataset defaults plus stricter column override. Test mixed
classes and cross-workspace references. Do not send data to an LLM in this plan.
```

### S0-P05B — quarantine and publish state machine

```text
Define and implement upload/ingestion states received -> quarantined -> scanned
-> classified -> publishable -> published, plus rejected/expired. Only typed
services can transition state and every transition records actor/reason/policy
version/digest. Block profile, preview, artifact download and LLM use before the
required gate. Test invalid transitions, duplicate scans, changed objects,
malicious filenames/MIME/archive metadata and worker loss. Malware integration
may be a safe stub, but production bypass must fail closed.
```

### S0-P05C — retention and deletion skeleton

```text
Add retention-policy resolution, deletion request/tombstone and object-cleanup
work intent without claiming full privacy deletion. Bind dataset versions,
artifacts, derived runs and legal/immutability holds; define what can be deleted,
anonymized, retained or made unavailable. Use ID-only jobs and idempotent object
deletion/reconciliation. Test hold precedence, repeated deletion, missing object
and partial failure. Do not physically erase evidence required by an active hold.
```

### S0-P05D — production LLM boot and egress guard

```text
Add configuration validation so any external LLM path requires an explicit
provider, allowed data classes, retention/training policy, region, timeout,
budget and emergency disable. Unknown provider capability or nullable exposure
blocks startup/feature activation. Ensure existing narrow OpenAI integration
uses the same guard and redacts payloads/errors. Add config matrix tests and a
synthetic smoke path; never use customer content or live credentials in CI.
```

### S0-P05E — policy, quarantine and deletion gate

```text
Run PostgreSQL and service adversarial tests for null policies, stricter
overrides, malicious metadata, cross-workspace data, concurrent transitions,
retention holds and object-store failure. Verify denied content cannot reach
preview, LLM, download, logs or metrics. Add low-cardinality policy/quarantine/
deletion metrics, operator queries and runbooks. Record exact limitations of
the skeleton and keep downstream LLM features disabled until Scope 1 policy.
```

## Plan 0.6 — stable `/v1` foundation

**Contract.** Reuse `api/v1.py`, `domain/application_api.py`, request-ID
middleware, authorization services, artifact service, ExecutionRequest/MlJob,
and `packages/dclab_client`. Establish shared contracts before adding broad
resource coverage in Scope 5.

### S0-P06A — common errors, requests and page contracts

```text
Define typed `/v1` schemas for error, field violation, opaque cursor page,
request metadata and resource reference. Map domain exceptions centrally to
stable status/code/message/request_id without FastAPI `detail` leakage. Enforce
bounded limit/default/order and cursor scope/digest/expiry. Update existing /v1
list routes and SDK parsing compatibly. Test empty pages, invalid/tampered cursor,
wrong workspace and internal exception redaction. Do not expand all legacy APIs.
```

### S0-P06B — lifecycle, cancellation and retry resource skeleton

```text
Define shared states and transition responses for ExecutionRequest, job-backed
operations and child attempts. Add authorized cancel and retry application
service boundaries with idempotency and immutable parent/child lineage; expose
only routes already backed by correct behavior. Cancellation is requested then
observed, not an immediate false success. Test terminal/no-op/conflict states,
duplicate delivery and cross-workspace access. Do not implement agent commands.
```

### S0-P06C — artifact authorization and streaming contract

```text
Centralize artifact metadata/download authorization by workspace, parent
resource, audience, classification and retention state. Return a bounded stream
or short-lived transport decided by ADR without exposing storage keys. Validate
digest, size, content disposition and safe media type; support range only if the
storage adapter proves it. Add SDK streaming with cleanup and tests for missing,
quarantined, expired, wrong-tenant and changed-object cases.
```

### S0-P06D — correlation, ETag and compatibility policy

```text
Propagate one validated/generated request ID through BFF, API, services, jobs,
events and safe response headers. Define ETag/version semantics for mutable
resources and If-Match conflicts. Add an OpenAPI compatibility classifier for
breaking versus additive changes and document supported deprecation headers/
window. Test malformed IDs, duplicate headers, stale writes and deterministic
schema generation. Avoid trace IDs as authentication or database keys.
```

### S0-P06E — SDK parity and negative-contract suite

```text
Refactor the Python client only enough to share typed page/error/artifact/
lifecycle behavior. Verify explicit workspace, request/client IDs, bounded
timeouts, safe retry only for idempotent operations, response-body limits and
stream cleanup. Add live API contract tests for every current /v1 operation and
negative tests for HTML/provider/internal error bodies. Do not expose API
internals or open a database connection from the client.
```

### S0-P06F — `/v1` foundation release gate

```text
Run OpenAPI drift, SDK live-contract, two-workspace, pagination, artifact,
idempotency, cancellation/retry and redaction suites in PostgreSQL. Measure
representative list and event polling behavior with recorded fixtures. Add
request/error/rate metrics and an API compatibility runbook. Repair only shared
contract defects, then freeze the baseline snapshot used by Scope 1 and 5.
```

## Plan 0.7 — development and CI parity

**Contract.** Own `compose.yaml`, `Makefile`, `.env.example`, web env/example,
CI workflows, test configuration and safe seed scripts. Local topology must
exercise API, web, worker, PostgreSQL and object storage without production
credentials.

### S0-P07A — one-command development topology

```text
Define one documented bootstrap target that starts PostgreSQL, API, web, at
least one durable worker and provider-neutral object storage using an S3-
compatible local emulator, runs migrations
and a safe deterministic seed, then exposes health checks. Pin images, use
named isolated volumes and non-production credentials, and make restart safe.
Do not add Kubernetes or a production cloud dependency. Add smoke assertions
for API->job->worker->object flow and useful failure messages.
Keep separate network-free S3/GCS adapter contract tests; local development does
not need to emulate both providers simultaneously.
```

### S0-P07B — CI quality and migration graph

```text
Build a required CI graph for Python format/lint/type checks that the project
actually supports, backend/SDK tests, migration empty/previous-head checks,
truth drift, frontend lint/type/build, component tests, browser E2E and artifact
guards. Cache safely by lockfile and prevent concurrent generated-output races.
Use deterministic service health gates and upload bounded failure artifacts.
Do not hide flaky failures with unconditional retry.
```

### S0-P07C — frontend component-test and lint modernization

```text
Choose and configure the smallest supported component/unit test runner for the
Next.js version, with DOM accessibility helpers and deterministic network mocks.
Migrate deprecated `next lint` to the supported ESLint command and resolve the
known hook dependency warnings through correct dependencies or documented
stable callbacks. Add tests for session/workspace primitives. Avoid snapshot-
only tests and broad frontend rewrites.
```

### S0-P07D — deterministic seed and developer safety

```text
Audit seed scripts for fixed identities, two-workspace isolation, known object
digests and idempotent reruns. Separate development/browser fixtures from any
production bootstrap. Add explicit environment guards against destructive seed
or local-storage cleanup outside named development resources. Document reset
and recovery using exact validated targets. No script may recursively delete an
unresolved path or use production credentials.
```

### S0-P07E — parity and cold-start gate

```text
From a fresh checkout-equivalent environment, execute documented bootstrap,
empty migration, seed, backend/SDK/web/component/browser tests, worker restart
and object download. Compare local and CI commands and eliminate silent skips.
Record durations, resource needs, known platform differences and owners. Verify
shutdown/restart preserves intended data and cleanup affects only named local
resources before closing the plan.
```

## Plan 0.8 — architecture decisions, DB graph and scale baseline

**Contract.** Store accepted decisions in `docs/adr/`, measured DB/API facts in
`docs/verification/`, and reproducible benchmarks in `scripts/`. Decisions must
name triggers and reversibility; this plan does not install speculative scale
components.

### S0-P08A — lock foundation architecture decisions

```text
Complete ADRs for browser identity/BFF, workspace authority, application-service
boundary, PostgreSQL job queue, object/secret placement, agent boundary,
versioning/immutability, the core product/lifecycle authority and environment
topology. Include one explicit ML-platform ownership ADR consistent with
`ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`: DCLab owns tenant/project/scientific/
decision/release/audit state; MLflow is a private tracking/package-detail
adapter; Pandera and Evidently are worker libraries behind DCLab contracts; safe
model loading is allowlisted. Confirm that the future ML lifecycle projection will reuse existing
Project/Dataset/FeatureSet/WorkflowRun/PipelineRun/ModelVersion lineage and stay
distinct from agent/notebook graphs. Each ADR records context,
decision, rejected alternatives, security/tenant consequences, compatibility,
operational owner, measurable revisit trigger and rollback. Link code/evidence
and mark superseded decisions explicitly. Do not claim decisions already made
if the current implementation disagrees.
Reject W&B beside MLflow for MVP, external-library domain/API types, MLflow-owned
promotion, an Evidently service/UI/database and production filesystem `mlruns`.
```

### S0-P08B — relational cycle and integrity analysis

```text
Reproduce the Alembic/SQLAlchemy dependency-cycle warning among datasets,
execution_requests, experiments and ingestion_runs. Draw the actual FK graph,
identify whether it affects create/drop/migration ordering, and benchmark the
canonical query paths. Prefer documentation or targeted constraint changes over
a disruptive model rewrite. If repair is justified, produce a separate
expand/backfill/enforce design and tests; do not edit the schema in this prompt.
```

### S0-P08C — capacity and risk baseline

```text
Measure representative tenant/resource cardinalities, job claim latency, event
polling, key list queries, artifact throughput and local/CI resource use with
versioned fixtures. Record query plans and index coverage without inventing
production SLOs. Create a risk register for tenant, auth, migration, queue,
object, LLM and operational boundaries with owner, detection, mitigation and
scope. Define evidence thresholds that would trigger Scope 10 changes.
```

### S0-P08D — Scope 0 architecture gate

```text
Review all Scope 0 ADRs against the implemented tree and current verification
facts. Resolve contradictory docs, missing owners, untestable rollback or
undefined production defaults. Run DB integrity and canonical-query benchmarks,
record the cycle disposition, and confirm no speculative broker/vector/Kubernetes
dependency or ML-platform runtime was added. Verify the future Plan 3.0 ownership
boundary has one canonical decision, explicit degraded/rollback behavior and no
conflict with the product/agent/notebook graphs. Publish the Scope 0 go/no-go
record with every failed or deferred item explicitly assigned. Plan 0.9 remains
required before Scope 1 begins.
```

## Plan 0.9 — bounded analytical scan foundation

**Contract.** Reuse `apps/api/app/services/dataset_materialization.py`,
`apps/api/app/storage/`, `apps/api/app/engine/data/loaders.py`,
`apps/api/app/engine/schema/profiler.py`,
`apps/api/app/services/lab_service.py`,
`apps/api/app/services/dataset_column_service.py`,
`apps/api/app/engine/modeling/leakage_auditor.py`, existing Dataset/Artifact/
DatasetProfile lineage, PyArrow, pandas and PostgreSQL jobs. Introduce one
internal DCLab `DataScanPort` and an ephemeral DuckDB adapter for authorized,
bounded analytical reads. DuckDB is never product storage, lifecycle truth,
user/agent SQL, a network client or a public contract. Training and scientific
decision services remain authoritative and continue to receive their existing
typed pandas/NumPy inputs where required. Do not add Polars. Use focused test
homes `apps/api/tests/test_data_scan_contract.py`,
`apps/api/tests/test_duckdb_data_scan.py`,
`apps/api/tests/test_dataset_profile_scan.py`,
`apps/api/tests/test_dataset_materialization.py` and
`apps/api/tests/engine/test_profile_leakage_splits.py`; extend a proven equivalent
and document the substitution instead of creating duplicate suites.

### S0-P09A — current scan inventory, benchmark and ADR

```text
Read docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md and inspect
pyproject.toml, apps/api/app/config.py,
apps/api/app/services/dataset_materialization.py,
apps/api/app/engine/data/loaders.py,
apps/api/app/engine/schema/profiler.py,
apps/api/app/services/lab_service.py,
apps/api/app/services/dataset_column_service.py,
apps/api/app/services/auto_train_service.py,
apps/api/app/engine/experiments/runner.py,
apps/api/app/engine/modeling/leakage_auditor.py and all direct load_table/
profile_frame callers. Measure frozen small/medium/large CSV and Parquet fixtures:
peak RSS, bytes read, wall/CPU time, rows/columns and current profile/result
digests. Write an ADR choosing one pinned DuckDB Python release as an in-process
DataScanPort adapter over authorized materialized files/Arrow, with pandas as the
temporary parity/rollback adapter. Define supported MVP formats (CSV/TSV,
Parquet and Arrow), unsupported Excel/JSON behavior, ownership, trust boundary,
typed query-template catalog, determinism, cancellation, resource limits,
observability, dependency placement and measurable activation/rollback criteria.
Explicitly reject persistent DuckDB catalogs, direct PostgreSQL/S3/HTTP access,
extension installation/autoload, arbitrary SQL, agent/model-generated queries,
API-side scans, Polars and replacing pandas/scikit-learn training. List exact
call sites to migrate or retain and define semantic parity for schema, missing,
cardinality, distributions, duplicates and numeric statistics. Do not add a
dependency or change runtime behavior. Maximum change: ADR, benchmark harness
and test skeleton under 600 hand-edited lines.
```

### S0-P09B — provider-neutral DataScan contracts and template registry

```text
Add ordinary DCLab contracts under apps/api/app/engine/data/ for
AuthorizedScanInput, DataScanRequest, ScanPurpose, QueryTemplateKey,
Projection, typed Filter/Group/Aggregation/Order, ScanBounds, ScanSchema,
ScanProfile, ScanBatchMetadata and DataScanResult. Input binds workspace,
project when present, Dataset/Artifact ID, version, content+schema digests,
media/size, classification/publish state and exact authorized local path or
Arrow source supplied by the application service. Request contains a code-owned
template key/version, declared columns and typed values, deterministic sample
seed/method, deadline/cancellation handle and maximum input/output rows/bytes,
groups/categories and batch size. No field accepts SQL, expression text, file/
URI/glob, extension, secret or database name. Add a DataScanPort Protocol and a
static registry for schema.v1, profile.v1, slice.v1,
leakage_candidate_stats.v1, comparison_slice.v1,
monitoring_window_prepare.v1 and managed_notebook_query.v1; each descriptor
declares input schema, allowed purposes/states, required partition, bounds and
output schema. Canonicalize column identifiers against the authorized schema and
compile only code-owned templates; unknown operators/functions/columns fail.
Keep Arrow/DuckDB objects out of domain persistence, jobs, events, /v1, SDK, CLI,
MCP and agent schemas. Add pure schema/canonical-digest tests for malicious
identifiers, SQL fragments, NaN/Infinity, ambiguous types, duplicate columns,
oversized lists and stable template versions. No migration or endpoint.
Maximum change: one contract/registry family and approximately 700 non-generated
lines.
```

### S0-P09C — hardened ephemeral DuckDB adapter

```text
Add apps/api/app/engine/data/duckdb_scan.py implementing DataScanPort and keep a
faithful pandas adapter for parity/rollback. The application service first
authorizes Dataset/Artifact/classification state, then uses
dataset_materialization.materialize_dataset to verify the digest and create one
read-only per-job input. Open only duckdb.connect(":memory:") inside an ML/data
worker; never create a .duckdb catalog. Register only the authorized exact file
or Arrow object, compile a registry-owned parameterized query and stream results
as bounded PyArrow RecordBatchReader batches. At connection creation disable
`autoinstall_known_extensions`, `autoload_known_extensions`,
`allow_community_extensions` and `allow_unsigned_extensions`; deny network/
environment-secret access; for file scans restrict `allowed_paths` to the exact
verified per-job input and bounded temp directory, and for registered Arrow
inputs disable external access entirely. Set memory/thread/temp-size/query/
output ceilings, then enable `lock_configuration`. Reject ATTACH, COPY, INSTALL,
LOAD, PRAGMA and every
unregistered statement regardless of caller. Implement deadline/cancel with
DuckDB interrupt, close connection/readers, delete only the named materialized/
temp files and return stable DCLab reason codes for unsupported format, invalid
schema, resource exhaustion, cancel, timeout and engine failure. Add a pinned
`data-scan` worker dependency group; DuckDB must not enter API/web/SDK/MCP/agent
or worker-investigation imports. Add typed settings with safe defaults:
DCLAB_DATA_SCAN_ENGINE=pandas, DCLAB_DUCKDB_ENABLED=false,
DCLAB_DUCKDB_MEMORY_LIMIT_MB=512, DCLAB_DUCKDB_THREADS=2,
DCLAB_DUCKDB_TIMEOUT_SECONDS=30, DCLAB_DUCKDB_MAX_INPUT_BYTES=536870912,
DCLAB_DUCKDB_MAX_OUTPUT_ROWS=10000,
DCLAB_DUCKDB_MAX_OUTPUT_BYTES=16777216 and
DCLAB_DUCKDB_MAX_TEMP_BYTES=1073741824,
DCLAB_DUCKDB_MAX_COLUMNS=512, DCLAB_DUCKDB_MAX_GROUPS=1000 and
DCLAB_DUCKDB_ARROW_BATCH_ROWS=4096. Production rejects DuckDB activation
without explicit positive bounds plus matching worker/container limits. Test
CSV/TSV/Parquet/Arrow, remote-like materialization, digest mismatch, path/glob/
symlink traversal, SQL injection fields, extension/network/filesystem attempts,
memory/temp/time/output exhaustion, cancellation, cleanup and no residual
catalog/secret/log leakage. Maximum change: one adapter family and approximately
900 non-generated lines.
```

### S0-P09D — schema and profile parity integration

```text
Refactor only the supported dataset schema/profile paths through a
DataScanService that re-authorizes the current workspace and immutable
Dataset/Artifact immediately before materialization and invokes DataScanPort.
For supported files, replace full-frame scans in lab_service.profile_dataset and
the equivalent DatasetColumn/profile owner; preserve engine/data/loaders.py and
profile_frame for training, small in-memory frames, unsupported formats and
rollback. Do not silently route oversized/failed DuckDB scans to pandas where
that could exceed a bound. Produce the existing DatasetProfile.stats and
DatasetColumn meanings: ordered names/dtypes, row/column/duplicate/missing/
unique counts, ratios, constant/near/high-cardinality/identifier/date signals,
deterministically tie-broken top values and normalized min/max/mean/median/std/
skew/quantiles. Record scan engine/version, template version, input/schema/
result digests, bounds and completed timestamp as bounded profile provenance;
reuse existing JSON/provenance fields when enforceable and add no migration
unless an ADR-backed integrity gap proves one necessary. Historical profiles
remain immutable. Run dual-engine shadow comparison on frozen null/category/
numeric/date/Unicode/duplicate/empty/wide fixtures; require exact discrete
results and documented finite tolerance/normalization for floating statistics.
Test two workspaces, deleted/quarantined/stale/tampered artifacts, cancellation,
unsupported format and idempotent repeated profile. Maximum change: one service
integration and approximately 800 non-generated lines.
```

### S0-P09E — bounded slices and scientific-consumer preparation

```text
Implement internal DataScanService methods for authorized dataset slices,
comparison slices, leakage-candidate statistics and paired drift-window
preparation using only the versioned registry contracts. A slice supports an
allowlisted projection, typed filters/grouping/aggregations, deterministic order
and hard output limit; it returns Arrow batches or normalized DCLab summaries
plus Dataset/Artifact/version/digest citations. Plain experiment metrics already
available through SQLAlchemy remain in their owning query service; use DuckDB
only when comparison requires bounded tabular slice computation. Leakage scans
must bind the already locked training partition/source-row evidence and target/
prediction-time contract; they may calculate neutral missing/cardinality/
correlation/category-purity candidate signals but never inspect final holdout,
exclude a feature or overrule modeling/leakage_auditor.py. Drift preparation
binds immutable authorized reference/current Dataset versions, FeatureContract
projection and deterministic sampling, and later feeds the Plan 3.0
DriftCalculator; it does not decide drift. Add parity tests against current
pandas/scikit calculations for classification/regression signals, group/time
partitions, stable ordering and seeded samples. Add explicit holdout-access,
cross-workspace, stale-contract, huge-cardinality, cancellation and bounded-
memory tests. Do not expose a generic scan endpoint or agent/notebook SQL tool.
Maximum change: one consumer-method family and approximately 800 non-generated
lines.
```

### S0-P09F — DataScan release, performance and rollback gate

```text
Run pinned DuckDB/fake-pandas adapter contracts, frozen profile/schema/leakage/
slice/window parity, PostgreSQL tenant authorization, local/remote-like object
materialization, cancellation/resource exhaustion, worker restart and security
campaigns. Verify no DuckDB/provider object, SQL, path, URI, secret, raw row or
unbounded result enters PostgreSQL events/audit, logs, traces, /v1, SDK, CLI, MCP
or agent state; verify no .duckdb/temp/result artifact remains after success or
failure. Benchmark representative CSV/Parquet inputs against the S0-P09A
baseline and record rows/bytes scanned, peak RSS, wall/CPU time, Arrow batches,
spill bytes and output size. Activate DCLAB_DATA_SCAN_ENGINE=duckdb only when
correctness is unchanged and at least one accepted memory/latency threshold is
met; otherwise keep pandas and mark the evidence honestly. Add bounded low-
cardinality scan purpose/template/engine/status/duration/rows/bytes metrics,
traces without column/customer IDs, alerts for timeout/exhaustion/error rate and
runbooks for disable, cancel, cleanup and dependency rollback. Prove switching
back to pandas preserves supported small-workload behavior and fails closed for
inputs above its safe bound rather than risking OOM. Run backend/scientific/
truth suites and two consecutive generation checks. Mark Scope 0 complete only
with a reviewed evidence record, pinned dependency/SBOM and clean regeneration.
Maximum change: tests, telemetry, runbooks and evidence under 900 hand-edited
lines; no product feature expansion.
```

## Plan 0.10 — cloud-portable immutable object-storage foundation

**Contract.** Repair and extend the existing `apps/api/app/storage/base.py`,
`s3.py`, `gcs.py`, `local.py`, artifact service/model and
`test_object_storage_lineage.py`; do not build a second storage layer. The
domain stores DCLab identity/content digest plus an opaque provider version and
precondition evidence. AWS/GCP SDK types, account/project names, raw provider
errors and signed URLs never become product identity or public metadata. Large
objects stream within explicit limits. New cloud writes are immutable-by-
default and conditional; destructive lifecycle remains an authorized DCLab
service operation.

### S0-P10A — existing storage audit and portability ADR

```text
Inspect ObjectStorage, ObjectPutResult, ObjectMetadata, LocalStorage, S3Storage,
GCSStorage, artifact_service.py, Artifact columns/migration lineage, config,
optional dependencies and every storage test/call site. Record observed gaps:
version/generation and ETag/metageneration capture, conditional create/read/
delete, eager full-body buffering, multipart/resumable cleanup, signed-URL
authorization/expiry, raw provider exception normalization, retention/hold and
credential-chain behavior. Write an ADR defining opaque ObjectVersionRef,
WriteCondition, bounded streaming input, normalized error/retry taxonomy and
capability discovery. Map S3 VersionId/ETag/If-None-Match and GCS generation/
metageneration preconditions without pretending their semantics are identical.
Choose safe backward compatibility, expand/backfill/enforce order, provider
feature flags and removal path. Explicitly forbid public bucket URLs, fallback
unsigned URLs, static AWS keys/GCP service-account files, provider SDK imports
outside adapters and cloud writes in PR CI. Add contract-test skeletons only;
do not migrate or call a cloud. Maximum change: ADR/test design under 500 hand-
edited lines.
```

### S0-P10B — provider-neutral storage contracts and artifact persistence

```text
Extend storage/base.py with ordinary typed opaque version/precondition and
streaming contracts while preserving bounded compatibility adapters for current
callers. Define exact behavior for create-if-absent, read exact version, metadata,
digest verification, existence, authorized signed read and delete exact version;
unsupported preconditions fail closed with stable DCLab errors. Never accept a
provider URI as a key. Extend Artifact only if required with nullable bounded
`storage_version` and `storage_etag`-equivalent opaque fields plus storage-
contract version; inspect the live Alembic head and add one expand revision,
composite tenant-safe indexes/constraints and a forward-repair plan. Existing
immutable artifacts remain readable and are backfilled only from verified
metadata; never guess a version. New cloud writes capture version evidence
before artifact publication. Add pure-contract, local adapter, PostgreSQL empty/
previous-head/live-upgrade, legacy-read, tenant substitution, immutability and
failure-between-object/row tests. No S3/GCS SDK implementation in this prompt.
Maximum change: one contract/migration slice under approximately 800 non-
generated lines.
```

### S0-P10C — AWS S3 streaming/versioned adapter

```text
Implement the S3 side of the frozen contract only in the existing s3 adapter.
Use the default AWS credential chain; capture VersionId/ETag/checksum metadata;
use conditional create for immutable keys and bind exact-version reads/signed
URLs/deletes where policy requires it. Stream bounded inputs using reviewed
multipart behavior without `as_bytes` for large bodies; compute DCLab SHA-256
during transfer, abort incomplete multipart work on error/cancel/deadline and
verify returned size/digest/version before publication. Validate key, metadata,
content type, expiry and byte/part/time limits. Map not-found, precondition,
auth, throttle, timeout, integrity and ambiguous failures to stable errors;
reconcile ambiguous completion through exact metadata/version before retry.
Extend mocked/fake S3 tests for duplicate conditional put, concurrent writer,
versioned overwrite, exact read/delete, multipart abort, truncated stream,
credential absence, rotation, 403/404 anti-enumeration, 429/5xx, timeout and
signed expiry. Add a protected real-S3 canary command using workload identity;
PR CI stays network-free. No GCS, API/UI or infrastructure work. Keep under 700
non-generated lines and 15 hand-edited files.
```

### S0-P10D — Google Cloud Storage streaming/versioned adapter

```text
Implement the GCS side of the frozen contract only in the existing gcs adapter.
Use Application Default Credentials/Workload Identity Federation; capture
generation/metageneration/checksum metadata; use generation preconditions for
immutable create and exact generation for reads/signed URLs/deletes. Remove the
current fallback that constructs an unsigned storage.googleapis.com URL when
signing is unavailable: return a stable fail-closed capability/config error.
Stream bounded inputs with reviewed resumable behavior without `as_bytes` for
large bodies; compute DCLab SHA-256, cancel/clean incomplete work and verify
size/digest/generation before publication. Normalize not-found, precondition,
auth, throttle, timeout, integrity and ambiguous failures and reconcile exact
metadata/generation before retry. Extend mocked/fake/emulator tests matching the
S3 cases, including metageneration race, resumable interruption, credential
absence/rotation and signed expiry. Add a protected real-GCS canary command
using workload identity; PR CI stays network-free. No S3, API/UI or
infrastructure work. Keep under 700 non-generated lines and 15 hand-edited files.
```

### S0-P10E — storage service, parity and release gate

```text
Update artifact services—not routes or storage adapters—to authorize workspace/
project/artifact, classification, purpose and expiry before issuing a version-
bound signed URL or exact read/delete. New writes generate tenant-scoped opaque
keys, use create-if-absent, verify digest/version and atomically publish the row;
ambiguous object-without-row and row-without-object states reconcile through
bounded jobs without exposing cross-tenant existence. Run the identical local,
S3 and GCS conformance suite for small/large streaming, versions/preconditions,
integrity, cancellation, retries, concurrency, signed access, retention/hold,
delete, orphan cleanup and safe errors. Run real PostgreSQL service tests and
protected S3/GCS canaries. Add low-cardinality provider/operation/status/bytes/
duration/retry/cleanup metrics without bucket/key/version labels, alerts and
credential/integrity/orphan runbooks. Verify S3-only and GCS-only dependency
images, default-credential use, per-provider flags/kill switches and local
fallback disabled in production. Record separate adapter support evidence and
prove either provider can be disabled without changing stored DCLab IDs/digests
or the other adapter. This gate is required before Scope 1 production claims;
it provisions no cloud infrastructure. Maximum change: service/tests/operations
under approximately 900 hand-edited lines.
```
