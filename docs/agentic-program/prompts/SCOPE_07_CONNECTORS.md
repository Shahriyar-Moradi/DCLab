# Scope 7 execution prompts — scalable data plane and inbound connector

Start Plan 7.1 after Scope 0; connector identity/API work also requires Scope 5
and agent integration requires Scope 2. Apply `README.md` and
`EXECUTION_STANDARD.md`. Provider selection happens through evidence, not by
inventing credentials or assuming an API.

## Scope implementation boundary

Reuse DataSource, DataAccess, IngestionRun, DatasetAsset/Dataset, Artifact,
ingestion/materialization, object storage and jobs. Add cohesive connector domain,
services/adapters/workers and `api/v1_connectors.py`. Adapters receive a narrow
context and secret reference; no DB session, global object store or raw secret.

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
Profile approved staged content asynchronously using bounded streaming/chunks,
persist quality/schema/classification lineage, then publish DatasetAsset/Dataset
metadata atomically and mark the immutable object active. Never expose a partial
dataset or advance on missing/mismatched object. Add idempotent replay, profile
failure, duplicate completion, cancellation and two-workspace tests.
```

### S7-P01E — upload/ingest operational gate

```text
Run local/S3-compatible adapter contracts, large-object memory bounds, malformed/
archive/malware, cancellation/recovery, orphan cleanup, classification and
two-workspace E2E through API/SDK/web upload. Add bytes/rate/quarantine/profile/
publish/failure metrics, backpressure and cleanup runbooks. Record tested size/
throughput limits and keep unsafe bypass disabled.
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
ConnectorContext. All endpoints/egress are reviewed and bounded.

### S7-P03A — adapter interface and registry

```text
Create typed provider adapter protocol, capability descriptor and code-owned
registry. ConnectorContext contains run/config/secret handles, deadline, page/
row/byte budgets, cancellation, safe logger/metrics and restricted HTTP transport;
it contains no DB session/global store. Validate config JSON Schema/version and
reject unknown handler/provider keys. Add shared interface/type tests.
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
rate-limit runbooks. Freeze interface version before real provider work.
```

## Plan 7.4 — first real inbound connector

**Contract.** Select one provider only after a scored pilot decision. Implement
the strongest reliable incremental semantics its official API supports and make
limitations explicit; never pretend timestamps are exact change tokens.

### S7-P04A — provider selection and API contract record

```text
Score candidate pilot providers on user value, official sandbox, stable IDs,
updated/deleted semantics, incremental cursor/token, snapshot consistency,
pagination, schema discovery, scoped auth, quotas/webhooks, reconciliation,
region/retention and testability. Choose one with product owner and write an ADR
pinning official API/version and known limits. Stop for user/product decision if
no candidate meets minimum safety/reliability.
```

### S7-P04B — config/auth/connection/discovery adapter

```text
Implement provider config schema without secret fields, secret credential shape,
connection validation, capability discovery, object/table/entity discovery and
schema inspection using restricted transport. Map provider auth scopes to least
privilege and safe errors. Add official sandbox/mocked contract cases for invalid/
expired/revoked credentials, quota, empty discovery and malicious names.
```

### S7-P04C — full sync and immutable staging

```text
Implement bounded full read with captured snapshot/lower/upper bounds where
supported, deterministic page order, streamed immutable page artifacts/digests
and resume after checkpointed page. Normalize/map into tenant staging without
publishing partial data. Handle duplicate IDs and provider pagination anomalies.
Add fake plus official sandbox tests and memory/size/cancel bounds.
```

### S7-P04D — incremental/deletion/late-data sync

```text
Implement the strongest supported change token/cursor/monotonic ID/overlapping
timestamp watermark strategy. Record overlap/dedup key, high-water snapshot,
late/out-of-order/deleted behavior and reconciliation requirement. Do not advance
durable checkpoint until atomic dataset publication. Test duplicate, missing,
late, update, delete, clock skew, repeated cursor and restart.
```

### S7-P04E — mapping, classification and atomic publish

```text
Version source-to-DCLab mapping and schema snapshot; validate types/required IDs,
quality/classification/residency and schema compatibility. Publish a new immutable
Dataset version plus IngestionRun/Artifact/mapping/checkpoint in one recoverable
transaction; drift/unsafe classification blocks for review. Add rollback via
prior dataset version and object reconciliation tests.
```

### S7-P04F — provider sandbox end-to-end gate

```text
Run connection/discovery/full/incremental/restart/rotation/rate/schema-drift/
delete/late-data/cancel and two-workspace paths against the faithful fake and
official sandbox using synthetic data. Verify secret/egress/redaction, exact
publication/checkpoint lineage and no duplicates/loss within documented provider
limits. Record API/version/quota/limitations before staging canary.
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
and resume workflow for drift through new versions. Test missing/late/deleted
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
Have an allowlisted user configure least-privilege sandbox credentials, discover,
map, full sync, incremental sync, observe freshness, handle drift/rotation and
publish a dataset without DB intervention. Run API/SDK/CLI/UI/agent, two-workspace,
secret scan, load/recovery and accessibility gates. Publish provider limits,
support/runbooks, flags and rollback before broader use.
```
