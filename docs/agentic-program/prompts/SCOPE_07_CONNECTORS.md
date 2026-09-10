# Scope 7 prompts — scalable data plane and one real inbound connector

Use the common preamble. Select the real provider from pilot demand; do not
invent a broad connector marketplace before one adapter is proven.

## Plan 7.1 — direct upload and asynchronous ingest

### S7-P01A — direct multipart upload contract

```text
Implement /v1 upload-session create/get/parts/complete/abort over private object
storage. Record workspace/project/DataSource/DataAccess, expected size/type/name,
idempotency and expiry. Issue short-lived method/object/size/type-bound multipart
instructions after current authorization; never proxy large production bodies
through FastAPI or persist signed URLs. Complete verifies parts, size, digest,
MIME/signature and transitions quarantine before enqueueing inspection/profile.
Abort/expiry cleans remnants. Add SDK streaming helpers and web progress/errors.
```

### S7-P01B — quarantine, async profile and large-object tests

```text
Implement dataset.inspect.v1/profile.v1 jobs that stream/sample safely, enforce
compressed/expanded size, archive/path/decompression-bomb, parser, row/column/
nesting and malware adapter checks; then atomically publish an immutable
Dataset version with profile/classification/quality evidence. Never load a
promised large file in an API process or full pandas frame by default. Test
multipart duplicate/expiry, wrong digest/MIME, memory bounds, worker loss,
quarantine cleanup, orphan reconciliation and two-workspace object-key attacks.
```

## Plan 7.2 — connector schema and secret references

### S7-P02A — connector control-plane schema

```text
Add versioned ConnectorDefinition/capability manifest, workspace connector
instance/config revision, ConnectorSyncPlan/SyncRun, append-only Checkpoint,
SourceSchemaSnapshot, mapping version and WebhookReceipt while reusing
DataSource, DataAccess, IngestionRun, DatasetAsset/Dataset, Artifact and
DataAccessEvent. Store provider/auth/sync/config schema versions, selected
objects, schedule/timezone, full/incremental mode, mapping/drift policy, limits,
states, bounds, current checkpoint and lineage. Enforce tenant FKs, bounded
safe JSON, idempotency and indexes.
```

### S7-P02B — managed secret reference lifecycle

```text
Integrate SecretResolver with a managed/local-fake secret provider. PostgreSQL
stores only opaque reference, provider/key/version/status metadata. Resolve a
short-lived credential only inside the assigned connector worker; never job,
event, log, artifact, LLM, MCP or API output. Support create/reference, rotate,
revoke, disconnect and last-success metadata with separate use/admin
capabilities. Test wrong worker/connector/workspace, rotation during work,
revocation, provider error redaction and production no-local-secret fallback.
```

## Plan 7.3 — adapter, egress and fake contract

### S7-P03A — narrow provider adapter interfaces

```text
Implement registry and typed capabilities for validate config, test connection,
discover, inspect schema, read page and resolve checkpoint. Adapters receive a
narrow ConnectorContext, not DB session/global store/all secrets. Validate
configuration JSON Schema/version and bounds. Restrict endpoints to reviewed
provider configuration; normalize URL, resolve DNS safely, block private/
loopback/link-local/metadata/encoded/redirect escape and use egress allowlists.
Handler keys are code-owned and cannot come from an LLM/client.
```

### S7-P03B — faithful fake and shared adapter suite

```text
Build a non-sensitive fake provider covering auth failure/rotation, empty/one/
multiple pages, cursors/timestamps, duplicate/late/deleted/out-of-order records,
rate limit/Retry-After, transient/permanent failure, ambiguous timeout, schema
drift and malicious content/errors. Run a shared contract for every adapter and
SSRF/redirect/DNS cases. Prove page/output/row/byte/time bounds, cancellation,
worker recovery and redaction. No live production credential runs in PR CI.
```

## Plan 7.4 — first real inbound connector

### S7-P04A — discovery, full and incremental sync

```text
Choose the first pilot provider using value, sandbox, incremental API,
identifiers/timestamps, quotas, scoped auth, reconciliation and region criteria.
Implement connection test/discovery/schema plus full and strongest available
incremental sync: change token, cursor, monotonic ID, overlapping timestamp
watermark or bounded comparison. Capture lower/upper/snapshot bounds; stream
immutable pages to tenant staging with digests; validate/normalize/map; publish
Dataset atomically; advance checkpoint only after publication.
```

### S7-P04B — mapping, classification and provider sandbox E2E

```text
Preserve provider-native immutable data/schema and a separate deterministic,
versioned project mapping. Cover rename/select/cast/time/category/tokenize/join/
target/outcome rules with explicit failure. An agent may propose a mapping but
validator/user activates it. In the provider sandbox test full -> incremental ->
late/tombstone -> retry after worker death -> exact Dataset lineage, quality,
classification, freshness and checkpoint. Prove duplicate intervals cannot
publish corrupt/duplicate state.
```

## Plan 7.5 — scheduler, webhook, drift and recovery

### S7-P05A — scheduling, webhook and backpressure

```text
Implement deterministic schedule-occurrence identity, due-job creation,
workspace/provider concurrency, priority fairness, page/row/byte/runtime limits,
Retry-After/backoff, visible rate-limit wait and pause/resume/revoke. Webhook
routes identify configured tenant uniformly, verify signature/timestamp/replay
before deep parse, bound/store digest/receipt, acknowledge quickly, deduplicate,
enqueue after commit and tolerate out-of-order delivery. Periodic reconciliation
must not depend solely on webhooks.
```

### S7-P05B — schema drift, reconciliation and cleanup

```text
Detect added/removed field, type/nullability/category/nesting/identifier/source
replacement and measured distribution drift. Apply versioned accept-compatible,
quarantine, drop-approved-unknown or fail policy. Breaking drift pauses
publication and shows old/new schema, mappings and affected workflows; never
silently edits mapping/cursor. Add operator resolve/replay, stuck-sync, abandoned
staging, cursor/dataset reconciliation and disconnect cleanup. Test concurrent
sync, drift during pagination and recovery at each checkpoint.
```

## Plan 7.6 — product and operations integration

### S7-P06A — API, SDK/CLI, UI and agent tools

```text
Expose definition/source/access/test/discover/sync-plan/run/events/pause/resume/
revoke/drift-review resources through /v1, SDK and released CLI. Add UI for
source config without secret display, schema/mapping review, schedule, freshness,
quality, progress, throttling, drift, retry and disconnect. Give agents typed
read/propose/start-sync tools only under policy/budget; no arbitrary URL or
secret. Use standard errors/cursors/idempotency/ETag/request IDs.
```

### S7-P06B — operational gate

```text
Add metrics/dashboards/alerts for freshness, rows/bytes/pages, duration/outcome,
throttles/credential errors, drift age, staging backlog, cursor/publish mismatch
and cleanup. Write rotate/revoke, provider outage, stuck/replay, drift resolve,
orphan cleanup and privacy deletion runbooks. Load/soak and failure-test the
staging adapter. Scope passes only when a real sandbox/staging provider recovers
without database intervention or secret exposure.
```
