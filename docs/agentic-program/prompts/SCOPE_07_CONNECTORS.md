# Scope 7 execution prompts — data-integration plane and connector pack

Every prompt inherits the plan-level outcome and mandatory live-checkout
execution card in [the remaining-scope map](REMAINING_SCOPE_EXECUTION_MAP.md).
Complete one reviewable lettered work unit at a time.

Start Plan 7.1 after Scope 0. Plans 7.2–7.4 may join the early Core ML MVP slice
after S1-P00H and the required machine-identity/secret foundations; public API
packaging still requires Scope 5 and agent integration requires Scope 2. Apply
`README.md` and `EXECUTION_STANDARD.md`. Provider/source selection happens
through evidence, not invented credentials or assumed API behavior.

## Scope implementation boundary

Reuse DataSource, DataAccess, IngestionRun, DatasetAsset/Dataset, Artifact,
ingestion/materialization, object storage and jobs. Add cohesive connector domain,
services/adapters/workers and `api/v1_connectors.py`. Adapters receive a narrow
context and secret reference; no DB session, global object store or raw secret.
Use pinned Apache-2.0 `dlt` OSS as a private extraction/loading engine behind a
DCLab-owned ConnectorPort/Runner. DCLab—not `dlt`—owns connector/config versions,
authorization, credential references, SyncRun/job state, cursor contract,
schema policy, quarantine, publication, lineage and audit. `dlt` state is an
opaque per-run engine checkpoint and never enters `/v1` or determines product
completion by itself.
Agent access is only through versioned DCLab ToolRunner/application-service
adapters. LangGraph never receives connector credentials or invokes provider
SDKs directly, and connectors do not host their own agent loop.
Deep Investigation/OpenAI Agents/NOOA likewise receive no connector SDK, engine
checkpoint, source credential or arbitrary discovery/query surface. They may
read only bounded DCLab connector/sync/dataset projections through an allowed
tool. Jev cannot select credentials, source resources, schema policy or whether
a quarantined dataset is published.
Published source/dataset versions appear as canonical project lifecycle nodes;
connector run graphs and cursors never become a competing lifecycle authority.
MLflow is an internal Plan 3.0 tracking adapter, not a Scope 7 inbound data
connector. W&B is deferred to a measured, one-way Scope 10 adapter decision and
must not be added beside MLflow or become a lifecycle/registry authority.
All supported tabular profile/classification work reuses S0-P09 DataScanPort
after staging authorization and digest verification. Connectors never call
DuckDB, submit SQL, select templates, open DCLab artifact paths or receive Arrow
readers; Polars is not added. DataScan remains a deterministic DCLab service,
not a connector or a new storage/database tier.

The production-MVP connector pack is direct CSV/Parquet, AWS S3 and Google
Cloud Storage object sources, read-only PostgreSQL/general SQL, one pilot-selected HubSpot or
Salesforce CRM, and read-only Snowflake. Airbyte or Snowflake Openflow may be
customer-operated upstream replication behind an `ExternalReplicationPort`;
neither is deployed as DCLab's control plane. MVP connectors are read-only and
do not provide CDC, reverse ETL, arbitrary user/agent SQL or provider actions.

## AWS/GCP portability requirements

Connector control, cursors, publication and public contracts are cloud-neutral.
The object-source family must support AWS S3 and Google Cloud Storage through
the existing storage/credential boundaries with one normalized object-version,
digest, prefix, pagination and error contract. Connector workers use EKS/GKE
workload identity and private secret adapters rather than static keys. Egress,
rate/cost limits, cancellation, schema quarantine and DatasetVersion atomicity
must pass both provider contract suites and staging canaries. A DCLab deployment
may ingest from either object provider regardless of its hosting cloud when an
explicit cross-cloud egress/residency policy permits it.

## Plan 7.1 — direct upload and asynchronous ingest

**Contract.** Upload streams directly to private quarantine/staging with declared
size/type and digest; API memory/disk is bounded. Dataset publication occurs only
after scan/classification/profile gates and an atomic metadata transition.

### S7-P01A — upload ADR and resource contract

```text
Inventory current admin/client/open-ingest upload paths and object adapters.
Write an ADR for direct multipart or pre-authorized object upload, UploadIntent/
Part/Completion, maximum object/part/count/time, MIME/archive policy, checksum,
quarantine/staging/publish lifecycle, authorization and cleanup. Define local/S3/
GCS parity and compatibility with existing uploads. Do not select public buckets
or pass storage credentials to the browser.
```

### S7-P01B — upload-intent and streaming implementation

```text
Implement tenant-scoped upload intent with source/project, expected name/size/
media/digest, object key generated server-side, expiry/state and idempotency.
Stream or issue narrowly scoped short-lived upload parts according to ADR; bound
API memory/temp files, validate completion metadata and never trust filename/MIME.
Add abort/expiry cleanup and tests for duplicate parts, changed size/digest,
disconnect and cross-workspace intent.
```

### S7-P01C — quarantine, scan and classification pipeline

```text
On completion atomically create/advance DataSource/DataAccess/IngestionRun and a
code-owned async job. Validate magic/MIME, archive nesting/count/uncompressed size,
malware integration result, encoding/schema bounds and Scope 0 classification.
Keep unknown/failed items quarantined and block preview/profile/LLM/publication.
Use immutable staged objects/digests. Add malicious file/archive and worker-loss
tests with a safe scanner fake.
```

### S7-P01D — profile and atomic dataset publication

```text
Profile approved staged content asynchronously through the registered
`dataset_profile.v1` DataScan operation using an ephemeral hardened DuckDB
adapter and bounded Arrow batches. The service—not connector input—selects the
code-owned template and enforced memory/thread/input/output/temp/time limits.
Persist normalized quality/schema/classification lineage, then publish DatasetAsset/Dataset
metadata atomically and mark the immutable object active. Never expose a partial
dataset or advance on missing/mismatched object. Reject SQL/path/extension/
network controls before job creation. Add idempotent replay, pandas-semantic
parity, profile failure, digest mismatch, bound violation, duplicate completion,
cancellation/temp cleanup and two-workspace tests.
```

### S7-P01E — upload/ingest operational gate

```text
Run local/S3/GCS adapter contracts, large-object memory bounds, malformed/
archive/malware, cancellation/recovery, orphan cleanup, classification and
two-workspace E2E through API/SDK/web upload. Add bytes/rate/quarantine/profile/
publish/failure metrics, backpressure and cleanup runbooks. Record tested size/
throughput limits and keep unsafe bypass disabled. Verify no persistent DuckDB
catalog/runtime artifact and no Polars dependency; exercise DataScan and ingest
kill switches plus the bounded pandas rollback path independently.
```

## Plan 7.2 — connector control-plane and secret references

**Contract.** Connector definitions/config versions are safe metadata; secrets
live only in managed secret storage and records carry opaque references/version.
Sync/checkpoint/schema/webhook history is durable and tenant-scoped.

### S7-P02A — connector ADR and typed schemas

```text
Define ConnectorDefinition, ConnectorConfigVersion, SyncPlan/Run, Checkpoint,
SchemaSnapshot, MappingVersion and WebhookReceipt state/schema. Specify provider
key/capabilities, config JSON Schema version, schedule/mode, cursor semantics,
snapshot bounds, status/failure, idempotency, drift and dataset publication.
Separate secret fields from ordinary config. Add pure state/canonicalization tests.
```

### S7-P02B — persistence and tenant integrity

```text
Add models/migration for connector/config/sync/checkpoint/schema/mapping/webhook
records with workspace/project/DataSource/DataAccess/IngestionRun/Dataset lineage.
Enforce immutable config/schema/mapping versions, unique active connector key,
same-tenant composite FKs, run attempt/idempotency and checkpoint monotonicity as
applicable. Index runnable schedules/status/freshness/unresolved drift. Add empty/
previous-head and concurrency tests.
```

### S7-P02C — managed secret-reference service

```text
Define SecretRef/provider interface for create/version/resolve-for-worker/rotate/
revoke/delete-metadata without returning raw secret to API/UI/agent/logs. Persist
only provider/key/version/status/fingerprint/rotation metadata. Use workload-
scoped resolution at execution and zero/overwrite best effort in memory. Add a
development fake and contract tests for wrong worker/workspace, rotation during
run, revocation and provider outage.
```

### S7-P02D — config, sync and checkpoint services

```text
Implement create/version/enable/pause/revoke connector, start/cancel sync and
commit checkpoint services with current authorization, config schema, secret
status, idempotency and events. Snapshot config/secret version/schema bounds at
run start; checkpoint advances only in the atomic publication transaction.
Concurrent scheduled/manual runs obey one policy. Test stale config, duplicate
run and partial publication.
```

### S7-P02E — control-plane migration/security gate

```text
Run migration, two-workspace, immutable-version, schedule race, checkpoint,
secret-reference and retention/deletion tests. Scan API/events/logs/database
ordinary fields for canary secrets. Reconstruct connector/run/publication lineage
and verify revoked connector cannot resolve secret or start work. Add config/run/
secret audit metrics and compromise/rotation/cleanup runbooks.
```

## Plan 7.3 — adapter, egress and faithful fake

**Contract.** Code-owned adapters implement validate config, test connection,
discover, inspect schema, read page and resolve checkpoint through a narrow
ConnectorContext. A `DltExtractionEngine` may implement bounded REST/SQL/
filesystem mechanics but cannot own product state or receive unrestricted
configuration. All endpoints/egress are reviewed and bounded.

### S7-P03A — adapter interface and registry

```text
Create typed provider adapter protocol, capability descriptor and code-owned
registry. ConnectorContext contains run/config/secret handles, deadline, page/
row/byte budgets, cancellation, safe logger/metrics and restricted HTTP transport;
it contains no DB session/global store. Validate config JSON Schema/version and
reject unknown handler/provider keys. Define a separate ConnectorEngine protocol
with normalized Arrow batch/page and opaque bounded engine-state outputs; provider
adapters may delegate only reviewed extraction mechanics to pinned `dlt` and may
not pass raw user config, SQL or secrets as source code. Add shared interface,
import-boundary and type tests.
```

### S7-P03B — restricted HTTP and egress policy

```text
Implement a transport with configured HTTPS provider hosts/ports/path rules,
connect/read/total timeout, response/header/body limits, safe redirect policy,
proxy rules, TLS validation, rate/Retry-After and redaction. Normalize URL and
resolve DNS safely; block loopback/private/link-local/metadata/IPv6/encoded and
DNS-rebinding/redirect escape. Adapter cannot override transport. Add SSRF tests.
```

### S7-P03C — paging/checkpoint normalization

```text
Define normalized Page with records/artifact stream, provider request ID, lower/
upper/snapshot bounds, next checkpoint, deletion/tombstone markers and schema
digest. Enforce max pages/rows/bytes/time and cancellation; detect repeated cursor
and non-progress loops. Checkpoints are opaque provider data validated/bounded and
never logged raw when sensitive. Add property tests for monotonic/overlap modes.
```

### S7-P03D — faithful fake provider

```text
Build a deterministic fake covering auth/rotation, empty/one/many pages, cursor/
timestamp/ID checkpoints, duplicate/late/deleted/out-of-order records, rate limit,
transient/permanent/ambiguous timeout, schema drift, malicious content/errors and
reconciliation. Script behavior from fixtures, not sleeps/network. Use canary
secrets to assert redaction and cancellation/resource bounds.
```

### S7-P03E — shared adapter contract gate

```text
Run every adapter/fake through config/discovery/schema/page/checkpoint, egress,
pagination progress, retry/cancel, secret rotation, malicious response, bounds
and redaction contracts. Verify no adapter imports database/global store or opens
an unrestricted client. Add per-provider bounded metrics and egress/credential/
rate-limit runbooks. Assert `dlt` and provider packages exist only in the
connector worker dependency/image, and no Airbyte/Openflow service or control-
plane dependency is present. Freeze interface version before connector-pack work.
```

## Plan 7.4 — production-MVP connector pack

**Contract.** Implement one shared DCLab connector contract and pinned `dlt` OSS
engine across AWS S3/Google Cloud Storage files, SQL, one pilot-selected CRM and Snowflake.
Direct upload remains Plan 7.1. Source credentials are least-privilege/read-only;
outputs are bounded Arrow batches and immutable staged Parquet artifacts.
Every source documents its strongest reliable incremental semantics; timestamps
are never presented as exact change tokens. Each prompt is one reviewable PR and
no connector can introduce its own scheduler, product state, secret store,
agent loop, arbitrary SQL or publication path.

### S7-P04A — connector-pack and `dlt` dependency ADR

```text
Read AGENT_FIRST_MVP_ARCHITECTURE.md and current official `dlt` OSS, SQL,
filesystem, REST/incremental and verified HubSpot/Salesforce source documentation.
Inventory Python/dependency images, DataSource/DataAccess/IngestionRun/Dataset,
Artifact/ObjectStore, ConnectorPort/fakes and current upload/database/cloud
adapters. Write an ADR pinning `dlt` version/license/Python support and assigning
DCLab versus `dlt` ownership for config, secrets, pipeline/engine state, cursors,
schema evolution, normalization, loading, staging and publication. Define the
pack: AWS S3 and Google Cloud Storage object files, read-only SQL with PostgreSQL conformance, one
pilot-selected HubSpot or Salesforce CRM and read-only Snowflake. Score the CRM
choice on pilot value, official sandbox, OAuth/private-app scopes, stable IDs,
updated/deleted semantics, quotas, schema discovery, region/retention and testability;
stop if neither meets minimum evidence. Define connector-worker-only dependency
placement, Arrow/Parquet boundary, resource/egress budgets, feature flags, per-
connector kill switches and removal/upgrade plan. Explicitly reject Airbyte/
Openflow as an embedded control plane, arbitrary generated REST config/SQL,
reverse ETL, CDC/Kafka/Debezium, product-DB writes and `dlt` completion as DCLab
publication authority. Add architecture/import assertions and exact source/API
reference links. Do not install, connect, migrate or create credentials. Maximum
change: ADR, decision matrix and test skeleton under 500 hand-edited lines.
```

### S7-P04B — bounded `dlt` extraction engine

```text
Add pinned `dlt` only to the connector-worker dependency group, lock and image and
implement `DltExtractionEngine` behind the S7-P03 ConnectorEngine protocol.
Accept only a code-owned source key/version, sanitized config-version ID,
short-lived SecretRef resolution handle, selected resource/table/object allowlist,
immutable lower/upper bounds, page/row/byte/time limits, cancellation and trace
context. Instantiate reviewed Python source factories; never evaluate generated
source, accept arbitrary module/function names, SQL strings or serialize raw
credentials/state to jobs/logs. Return bounded normalized Arrow RecordBatches or
immutable Parquet page artifacts plus opaque size-limited engine state and
schema/load metadata. DCLab validates and atomically commits the durable
checkpoint only with DatasetVersion publication. Disable `dlt` telemetry/cloud
features and local durable pipeline directories unless the ADR specifies an
isolated per-attempt temp path with guaranteed cleanup. Map library exceptions
to stable DCLab reason codes. Add a deterministic fake and tests for empty/
multi-batch, schema evolution, duplicate range, cancellation, timeout, memory/
disk/output limits, corrupted state, secret redaction, cleanup and dependency/
import boundaries. No real provider or public API in this PR; keep under
approximately 800 non-generated lines and 20 hand-edited files.
```

### S7-P04C — AWS S3 and Google Cloud Storage object-file sources

```text
Implement one read-only cloud-object source family using the existing
Artifact/ObjectStore contract and `apps/api/app/storage/s3.py`/`gcs.py`; use a
code-owned `dlt` filesystem factory only where it adds value. Provider-neutral
config contains a source kind, opaque credential/config version, approved
region/location and tenant-owned bucket/prefix plus format/compression/resource
allowlists. AWS/GCP resource names and credentials remain private adapter
configuration resolved in the connector worker. Normalize S3 version ID/ETag
and GCS generation/metageneration into an opaque ObjectVersionRef while retaining
private source metadata needed for safe conditional access. List stable pages
and snapshot object key/version/size/checksum. Accept CSV, Parquet and explicitly
approved JSONL within file/count/byte/row/decompression/time limits; reject
archives, traversal, wildcard escape, missing version/precondition, unknown
codecs and provider URLs supplied by agents. Stream to immutable quarantine,
verify content digest, produce schema snapshot and never publish a partial
listing. Checkpoint the deterministic object-version tuple and reconcile
delete/change through a new SyncRun. Extend the shared adapter suite with MinIO/
S3 and GCS fake/emulator fixtures for empty, pagination, changed/deleted/versioned
objects, duplicate listing, multipart/resumable upload residue, precondition
races, credential rotation, denied prefix, cross-cloud egress policy, cancel/
restart, safe provider errors and cleanup. Add documented protected live-canary
commands for both clouds but no network in PR CI. Expose no new route; give S3
and GCS separate feature/egress kill switches. Maximum change: the common source
plus two thin adapters/tests under approximately 900 non-generated lines; split
provider adapters into sequential PRs if that limit cannot be met.
```

### S7-P04D — read-only SQL/PostgreSQL source

```text
Implement the generic SQL source with PostgreSQL as the conformance database.
Use a least-privilege read-only account, TLS policy, allowlisted database/schema/
table/view and selected columns discovered server-side. Use the reviewed `dlt`
SQL source/SQLAlchemy construction; users and agents never provide SQL, WHERE,
join, expression, connection string or driver option. The server selects a
code-owned extraction template and quoted identifiers from discovery. Support
bounded full snapshot plus one declared cursor column with stable primary/dedup
key, deterministic ordering, optional overlapping watermark and captured upper
bound. Reject missing/non-monotonic/nullable cursor contracts unless a reviewed
full-snapshot policy applies. Deletions require a source tombstone/version or a
bounded reconciliation plan; do not claim CDC. Add real PostgreSQL tests for
read-only enforcement, discovery allowlist, malicious identifiers, concurrent
updates, duplicate cursor value, late row, clock skew, restart, schema/type
drift, connection loss, statement timeout, cancel and cross-workspace secret.
Record query/row/byte/time limits and source-index guidance without modifying the
customer database.
```

### S7-P04E — pilot CRM source

```text
Implement only the HubSpot or Salesforce CRM selected by S7-P04A using its
official API/auth flow and a pinned reviewed `dlt` verified/REST source copied or
wrapped in the connector-worker package according to its license/update model.
Pin object/entity allowlist, API version, required read scopes, pagination,
property selection, stable ID and updated/deleted semantics. Config contains no
token; SecretRef supports rotation/revocation and the restricted transport
allowlists exact provider hosts. Capture initial and incremental bounds, use an
overlap window/dedup key when timestamps are the strongest available cursor,
and record unsupported deletions/formulas/attachments/custom fields honestly.
Normalize contacts/companies/deals or the pilot-approved minimal objects into
versioned mappings without assuming business meaning; classification can only
tighten exposure. Add deterministic recorded fixtures plus official sandbox
tests for OAuth/private-app failure, pagination, rate/Retry-After, custom fields,
large property sets, duplicate/late/update/delete behavior, schema drift,
malicious labels, cancel/restart and credential rotation. Do not add CRM writes,
webhooks or generalized arbitrary REST configuration in this prompt.
```

### S7-P04F — read-only Snowflake source

```text
Implement Snowflake as a deployable read-only warehouse source behind the same SQL
connector contract, not as DCLab PostgreSQL, object storage, job queue, lifecycle
or connector control plane. Use a dedicated least-privilege role/warehouse,
key-pair or approved workload authentication, TLS and allowlisted account/
database/schema/table/view/columns. Credentials and private keys remain in the
secret manager. Build only code-owned quoted SELECT/range templates from
authorized discovery; reject client/agent SQL, session parameters, stored
procedures, stages, external functions and writes. Bound warehouse size/timeout,
statement count, rows/bytes/batches, concurrent runs and cost; cancel the remote
query on DCLab cancellation/deadline. Produce Arrow/Parquet through the common
engine and snapshot query/source metadata needed for reproducibility without
exposing query IDs or account locators publicly. Add mocked/official trial or
pilot-account synthetic tests as available for discovery, role denial, cursor/
snapshot, schema drift, large result, timeout/cancel, auth rotation, cost limit
and safe errors. Document that Snowflake Openflow may populate upstream tables
for an existing customer but is not deployed or controlled by DCLab.
```

### S7-P04G — cross-source mapping, incremental and publication semantics

```text
Implement the shared path from connector batches/page artifacts through
versioned source schema and mapping, classification, quality checks, immutable
Parquet staging and atomic DatasetVersion publication. Define one normalized
checkpoint envelope containing connector/config/source/mapping/engine versions,
source lower/upper bounds, opaque engine state digest/size, dedup key/window,
schema digest, page/artifact digests and prior checkpoint. Validate engine state
as untrusted bounded data; never expose it publicly. Advance the DCLab checkpoint
only in the recoverable publication transaction after every artifact digest,
classification and schema rule passes. Compatible additive drift follows an
explicit policy; rename/type/delete/sensitivity/primary-key/cursor drift pauses
and quarantines for review. Backfill is a bounded new SyncRun with explicit
range and cannot overwrite history. Reconciliation detects missing/duplicate
pages, stale object/query snapshots and ambiguous engine completion before
retry. Add a connector conformance suite applied to object, SQL, CRM and
Snowflake fakes for full/incremental/overlap, late/update/delete, schema drift,
crash before/after artifact/publication/checkpoint, cancel, retry, idempotency,
rollback to prior DatasetVersion and deterministic lineage reconstruction.
Maximum change: shared publication/checkpoint services and tests only; do not
add UI, scheduling or another provider.
```

### S7-P04H — connector-pack staging and clean-disable gate

```text
Run pinned dependency/license/SBOM and import-boundary checks plus the shared
adapter/engine/publication suite for direct upload, AWS S3 and Google Cloud Storage files,
PostgreSQL SQL, the selected CRM and Snowflake. Use real PostgreSQL/MinIO and
official CRM/Snowflake synthetic sandboxes or recorded contract fixtures with a
documented AWS and GCP live canary commands; PR CI remains network-free. Prove two-workspace
isolation, least-privilege/read-only credentials, secret redaction/rotation/
revocation, source/egress allowlists, row/byte/time/cost/concurrency bounds,
schema quarantine, cancellation, restart, ambiguous reconciliation and no
duplicate/lost records within each source's documented semantics. Verify no
Airbyte/Openflow service, CDC broker, arbitrary SQL/REST generator, reverse ETL,
connector-owned agent loop or durable local `dlt` state exists. Publish the
exact connector/version/capability/limitation matrix, metrics, dashboards,
alerts, credential/schema/backfill/reconciliation runbooks, per-source flags and
kill switches. Demonstrate disabling `dlt` or any connector leaves direct
upload and existing deterministic datasets healthy. This gate is required for
the production-MVP connector-pack release; unsupported/unselected sources must
remain absent rather than simulated as production-ready.
```

## Plan 7.5 — scheduling, webhook, backpressure and recovery

**Contract.** Scheduling/webhooks create durable sync intent idempotently. Webhook
is a hint, not trusted data; schema drift pauses publication. Reconciliation
detects ambiguous or missed changes before checkpoint progression.

### S7-P05A — scheduler and concurrency policy

```text
Implement due-plan query/claim using database locks, timezone/DST-safe schedule,
next-run calculation, jitter, missed-run/coalescing and per-connector/workspace/
provider concurrency. Atomically create idempotent SyncRun/MlJob intent and advance
schedule. Manual and scheduled runs obey overlap policy. Test multi-scheduler
race, clock jump, pause/revoke and backlog fairness.
```

### S7-P05B — webhook ingress and receipt handling

```text
Add provider webhook endpoint with raw-body size/time limits, signature/key/
timestamp/replay validation, tenant/connector lookup without enumeration and
durable unique receipt before 2xx. Store bounded safe metadata/digest, not secret/
full payload unless policy permits protected artifact. Convert valid receipt to
sync hint; fetch truth through adapter. Test replay, forgery, rotation and flood.
```

### S7-P05C — provider backpressure and retry policy

```text
Coordinate token/rate/concurrency quotas by provider credential/workspace with
Retry-After, bounded exponential backoff, circuit breaker and deadline. Reserve
capacity before request and release/settle after; avoid retry storms after outage.
Distinguish auth/config/permanent/data/transient/ambiguous failures and pause when
operator action is required. Add deterministic clock/concurrency tests.
```

### S7-P05D — schema drift review and pause/resume

```text
Compare discovered/current schema and mapping versions before publish. Classify
additive compatible, narrowing/widening, rename/type/delete and sensitive-field
drift; only explicitly safe additive behavior can auto-continue. Otherwise pause,
persist diff/evidence and require authorized mapping/classification review. Resume
creates a new config/mapping version. Test concurrent drift and stale approval.
```

### S7-P05E — reconciliation and ambiguous recovery

```text
Implement bounded periodic/on-demand reconciliation using provider-supported
counts/checksums/change windows/ID samples without assuming perfect equivalence.
For ambiguous timeout, inspect durable page/publication/checkpoint/provider state
before retry. Repair through new SyncRun/version, never mutate history. Test lost
webhook, missed window, duplicate page, partial object, worker death and provider
inconsistency.
```

### S7-P05F — scheduler/webhook/recovery gate

```text
Run multi-instance scheduler, webhook attack/flood, rate outage/recovery, drift,
reconciliation, cancellation, restart and cleanup campaigns. Measure backlog age,
freshness, provider calls, duplicates/loss and storage growth under bounded load.
Add dashboards/alerts and pause/revoke/credential/schema/ambiguous-delivery
runbooks. Canary only after exact observed evidence.
```

## Plan 7.6 — product, client, agent and operations integration

**Contract.** API/UI/SDK/CLI expose safe connector configuration/status/discovery/
sync/drift without secrets. Agent tools read status and propose mapping/recovery;
they cannot resolve credentials or silently resume drift.

### S7-P06A — connector `/v1` and client resources

```text
Add connector/config-version/discovery/sync/schema/mapping/drift/webhook-status
resources and enable/pause/resume/revoke/test/start/cancel commands with scopes,
ETag/idempotency, opaque pages and safe errors. Secret creation accepts write-only
value or provider flow but never returns it. Add SDK/CLI typed methods and OpenAPI
parity/negative/two-workspace tests.
```

### S7-P06B — connector configuration and run UI

```text
Build workspace connector list/setup/detail, discovery/mapping, run timeline and
pause/revoke controls using server-provided provider schemas/capabilities. Secret
fields are write-only and never rehydrated; show fingerprint/status/rotation only.
Render rate/auth/drift errors safely and require exact confirmations. Add keyboard,
stale ETag, workspace switch and malicious provider label tests.
```

### S7-P06C — freshness, quality and drift experience

```text
Expose current dataset/source/config/mapping/checkpoint/freshness/quality/
classification lineage with bounded metrics and safe diffs. Distinguish last
attempt, last success, source high-water and dataset publication. Provide review
and resume workflow for drift through new versions, refresh lifecycle impact and
append reviewed mapping/drift rationale to ProjectDecisionService. Test missing/late/deleted
data and never claim “up to date” beyond provider semantics.
```

### S7-P06D — agent connector tools

```text
Register read tools for connector status/discovery/schema/freshness/failure and
proposal tools for mapping/drift/recovery. Use minimal context, current versions,
citations and Scope 2 proposal review. Do not expose secret values, raw provider
bodies, arbitrary endpoint or direct enable/resume. Test injection in provider
metadata/errors and stale/revoked connector.
```

### S7-P06E — staging product and operations gate

```text
Have allowlisted users configure least-privilege AWS S3, Google Cloud Storage,
PostgreSQL, selected
CRM and Snowflake credentials, discover, map, full sync, incremental sync,
observe freshness, handle drift/rotation and publish immutable datasets without
DB intervention. Run API/SDK/CLI/UI/agent, two-workspace, shared `dlt` engine,
secret/egress scan, load/recovery and accessibility gates. Publish every source/
API/library version, capability, limit, cost, support/runbook, flag and rollback
before broader use. Block the production-MVP connector gate if any advertised
source lacks its shared conformance and protected live-canary evidence on its
provider; disabling one source must not
degrade the others or direct upload.
```
