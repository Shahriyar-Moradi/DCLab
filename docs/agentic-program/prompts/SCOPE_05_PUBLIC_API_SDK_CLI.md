# Scope 5 execution prompts — public API, SDK and customer CLI

Start after Scope 3 and applicable resource scopes. Apply `README.md` and
`EXECUTION_STANDARD.md`. Public clients use `/v1` over HTTP; they never import
API internals, open the database, read storage keys or inherit browser cookies.

## Scope implementation boundary

Extend resource routers/application services, `packages/dclab_client`, and add a
separate package such as `packages/dclab_cli`. Stable contracts use explicit
machine identity, workspace, scopes, request/client IDs, bounded pages/streams,
safe retries and versioned OpenAPI compatibility. Agent resources expose only
DCLab-owned sessions/runs/steps/events/tool calls/citations; LangGraph checkpoint
rows, graph-private state and framework types never enter OpenAPI, SDK or CLI.

## Plan 5.1 — machine identity and scoped credentials

**Contract.** Service accounts are workspace/organization principals with
explicit scopes; tokens are shown once, hashed at rest, rotatable/revocable and
audited. OAuth/device flow is used where interactive identity is required.

### S5-P01A — identity ADR and scope model

```text
Inventory current bearer/browser identity, memberships and capability services.
Write an ADR for ServiceAccount, API credential and interactive CLI OAuth/device
flow. Define principal/workspace/org binding, scope-to-capability mapping,
credential prefix/hash, expiration, rotation overlap, revocation, last-use,
rate/quota and separation from browser sessions. Define anti-enumeration and
bootstrap/recovery. Do not implement SSO/SCIM here.
```

### S5-P01B — service-account and token persistence/services

```text
Add tenant-scoped service account and credential records with name/status/scopes,
creator, hash/prefix, issued/expires/revoked/last-used and rotation lineage.
Enforce same-tenant principal, unique active prefix/hash and immutable secret.
Implement create/list/get/rotate/revoke/authenticate through services; reveal raw
token once and update last-use asynchronously/bounded. Test hash, race, expiry,
revocation and cross-workspace use.
```

### S5-P01C — OAuth/device authorization integration

```text
Define provider-neutral authorization-code+PKCE and device-flow interfaces,
issuer/audience/scope/state/nonce/device-code expiry and polling bounds. Integrate
one configured identity path only if production provider requirements are known;
otherwise ship a disabled interface/fake and documented decision. Map external
identity to current user/membership each request. Test phishing/code substitution,
poll abuse, revoked membership and token audience.
```

### S5-P01D — identity API, audit and operational controls

```text
Add protected `/v1/service-accounts` and credential create/rotate/revoke endpoints
with exact one-time secret response, ETag/idempotency and distinct capabilities.
Emit safe audit and low-cardinality auth/scope/rate metrics; never log token,
authorization code or device secret. Add compromise, rotation, lost-device and
mass-revocation runbooks plus emergency machine-auth disable.
```

### S5-P01E — machine-identity adversarial gate

```text
Test stolen/revoked/expired token, prefix enumeration, hash timing assumptions,
scope/workspace/audience substitution, concurrent rotation/use, suspended owner,
device-code phishing/polling and log/error leakage. Verify service account cannot
gain browser/admin capability and every request resolves current entitlements.
Run migration/restore and record evidence before public surface expansion.
```

## Plan 5.2 — complete stable `/v1`

**Contract.** `/v1` becomes the one public resource/command boundary. Legacy
routes may remain temporarily but cannot define separate semantics. OpenAPI and
client parity are CI-enforced.

### S5-P02A — public resource/operation inventory

```text
Map supported product workflows to projects, ProblemSpecs, data sources/access,
ingestions/datasets/profiles, workflows/runs/builds/stages/events, models,
artifacts, agents/proposals/approvals and notebooks. For every operation list
method/path, capability/scope, request/response/page, states, ETag/idempotency,
rate/quota and legacy owner. Mark unsupported/private surfaces. Approve the
inventory before adding routes.
```

### S5-P02B — core resource reads and pages

```text
Implement missing get/list resources in cohesive routers over application query
services. Use explicit workspace, opaque cursor, bounded limit/filter/sort and
audience-safe representation. Enforce resource tenant lineage before lookup and
consistent 404/403 policy. Add OpenAPI examples and contract tests for empty,
tampered cursor, wrong tenant, deleted/quarantined and large collections.
```

### S5-P02C — commands, lifecycle and concurrency

```text
Expose only existing canonical commands for ingestion, build, cancel/retry,
agent/proposal/approval and notebook execution. Require idempotency/canonical
digest, ETag/preconditions, capability/scope, quota/budget and current source
versions. Return durable resources/202 for async work. Add duplicate/conflict,
stale ETag, cancellation race and transaction-failure tests.
```

### S5-P02D — upload/download and event streaming

```text
Define direct upload/multipart initiation where implemented, bounded streaming
download with digest/media/disposition, and event polling/stream semantics using
opaque cursor, heartbeat/reconnect and backpressure. Authorize at open and where
needed during stream; never expose storage keys/signed URLs outside approved
design. Test disconnect, range, changed/deleted artifact and slow client.
```

### S5-P02E — rate, quota, ETag and compatibility enforcement

```text
Apply principal/workspace/operation rate and concurrency limits with safe
Retry-After; distinguish quota/budget denial. Complete ETag/If-Match and
deprecation/version headers. Enhance OpenAPI diff to reject breaking path/schema/
enum/security/error changes unless an approved version strategy exists. Test
multi-instance limiter behavior or document its supported topology honestly.
```

### S5-P02F — public API release gate

```text
Run operation inventory parity, OpenAPI diff, two-workspace/scope, pagination,
upload/download, event reconnect, idempotency/concurrency, rate/quota and redaction
tests against live PostgreSQL/object storage. Measure representative latency and
payload bounds. Publish support/deprecation/runbook and exact supported surface;
do not label legacy/private routes public.
```

## Plan 5.3 — public Python SDK

**Contract.** Provide sync/async parity, typed resources, iterators, waiters,
streaming and auth providers over one bounded HTTP transport. No hidden infinite
retry, implicit workspace or credential logging.

### S5-P03A — SDK architecture and transport split

```text
Write the SDK ADR and refactor `packages/dclab_client` into transport, auth,
errors, models and cohesive resource clients without breaking supported imports.
Define sync/async transport interfaces, timeout phases, body limits, user agent,
request/client IDs, workspace precedence and redaction. Preserve HTTP-only
boundary. Add compatibility tests for existing client API.
```

### S5-P03B — authentication providers and retry policy

```text
Implement static token, rotating credential callback and optional OAuth/device
provider interfaces with in-memory secret handling and explicit persistence hook.
Retry only safe idempotent requests or commands with idempotency key; honor
Retry-After, deadline, cancellation and jitter with maximum attempts. Never retry
ambiguous non-idempotent operations. Add mocked timeout/429/5xx/HTML/huge-body
and redaction tests.
```

### S5-P03C — typed resource clients and parity generation

```text
Implement sync/async methods for the approved `/v1` inventory, grouped by
resource, with validated request/response models and stable error mapping. Use a
generated or declarative operation manifest and CI parity; do not hand-duplicate
paths inconsistently. Model unknown additive response fields compatibly while
rejecting invalid required data. Add per-operation mocked and live tests.
```

### S5-P03D — iterators, waiters and streaming

```text
Implement lazy bounded page iterators, event iterators/reconnect, async/sync
waiters with timeout/poll bounds, and context-managed upload/download streaming
with digest verification and cleanup. Expose cancellation and server retry hints.
Test early iterator exit, network interruption, tampered cursor, slow stream,
wrong digest, user cancellation and resource terminal/error states.
```

### S5-P03E — SDK compatibility and release gate

```text
Run supported Python/version/OS matrix, type checking, unit/live API parity,
two-workspace/scope, timeout/retry, streaming and backward-compatibility tests.
Generate API docs/examples from real models, scan wheels for secrets/internal
imports, and record performance/package size. Publish only traceable artifacts
after Plan 5.6; until then mark candidate.
```

## Plan 5.4 — customer CLI core

**Contract.** Add a separate package using only the public SDK. Commands map
one-to-one to public resources; interactive convenience never changes server
authorization or approval.

### S5-P04A — CLI ADR, package and command tree

```text
Choose the existing-project-compatible CLI framework and create
`packages/dclab_cli` with entry point, dependency on public SDK, version and
test harness. Define command groups auth/config/workspace/project/dataset/build/
artifact/agent/approval/notebook, global profile/workspace/output/timeout flags
and stable help. Explicitly prohibit API internal/database imports. Add startup/
help/version tests.
```

### S5-P04B — profile, auth and secure credential storage

```text
Implement named profiles containing endpoint, workspace and non-secret defaults;
store credentials through OS keychain where available or an explicit injected
provider. Any fallback requires restrictive permissions, warning and opt-in;
never store tokens in project files/history/logs. Implement login/device/status/
logout and token rotation/revocation mapping. Add fake keychain and permission/
redaction tests.
```

### S5-P04C — read/list/watch/download commands

```text
Implement workspace/project/dataset/build/model/artifact/agent/notebook reads and
bounded lists using SDK iterators. Add watch/wait with signal/timeout and artifact
download with safe destination, overwrite confirmation, temp file, digest and
atomic rename. Human output is concise; structured output follows Plan 5.5.
Test empty, pagination, interrupted download and denied scope.
```

### S5-P04D — command/approval operations

```text
Implement supported create/cancel/retry/message/review commands with explicit
workspace, idempotency and exact input from flags or validated JSON/file. Show
the server approval summary/digest/risk and require interactive confirmation
unless a previously exact-approved noninteractive flow is used. Never prompt in
machine mode. Test duplicate, stale ETag, policy denial and SIGINT.
```

### S5-P04E — CLI integration and isolation gate

```text
Run CLI through a live API for auth profile, workspace selection, resource list,
build/agent lifecycle, approval and artifact download across two workspaces.
Inspect dependency/import graph and filesystem outputs for API internals/secrets.
Test offline/backend error, narrow terminal, shell quoting and accessibility of
help/errors. Record command coverage and unsupported operations.
```

## Plan 5.5 — stable automation contract

**Contract.** `--output json|jsonl` is versioned machine output; stdout contains
data only, stderr diagnostics only, and exit codes have documented stable meaning.

### S5-P05A — output schemas and exit taxonomy

```text
Define versioned JSON resource/error/operation envelope, JSONL item/event format,
timestamp/ID/null conventions and stable exit codes for success, usage, auth,
denied, not found, conflict, validation, rate/quota, timeout/cancel, server and
partial. Human formatting is separate. Add golden fixtures and prohibit secrets,
ANSI/progress or localized prose in machine stdout.
```

### S5-P05B — noninteractive input and signal semantics

```text
Define precedence for flags/env/profile/stdin/file, with secrets excluded from
unsafe flags where possible. Machine mode never prompts; destructive/approval
operations require explicit exact input. Handle SIGINT/SIGTERM by requesting
server cancellation when applicable, closing streams/temp files and returning
documented exit. Add pipe-closed, timeout, invalid JSON and concurrent signal tests.
```

### S5-P05C — completion and secrecy behavior

```text
Generate shell completion from the static command tree without network/secret
access. Redact credentials, auth headers, URLs with secrets, recovery/device
codes and provider bodies from errors/debug output. Provide an explicit safe
debug mode showing request IDs/method/path/status only. Add snapshot scans for
known canary secrets across stdout/stderr/config/temp files.
```

### S5-P05D — automation compatibility gate

```text
Run golden JSON/JSONL/exit/signal tests across supported OS/shell/Python matrix
and live API failure modes. Verify one item per JSONL line, no stdout pollution,
stable field/exit compatibility and bounded memory for large lists/streams.
Version intentional breaking changes. Publish an automation contract and example
CI scripts using synthetic data only.
```

## Plan 5.6 — package and release supply chain

**Contract.** SDK and CLI artifacts are reproducible, scanned, signed and
traceable to protected source/CI. Release never builds from a developer laptop.

### S5-P06A — packaging metadata and supported matrix

```text
Finalize independent package names/versions, Python range, dependencies/extras,
licenses, README/security links and CLI entry point. Pin build tooling in CI and
define API/package compatibility policy. Build sdist/wheel in isolated environment
and test installation/import/help in clean supported Python images. No runtime
dependency on repository/API internals.
```

### S5-P06B — reproducible build, SBOM and scans

```text
Make artifacts reproducible where tooling supports it; emit checksums, SBOM,
dependency/license/vulnerability/secret scans and build provenance. Fail on
unapproved severity/license or generated-file drift. Inspect wheel contents for
tests, secrets, credentials, internal source and nonportable paths. Document
exception ownership/expiry rather than silently ignoring findings.
```

### S5-P06C — signing and protected publication workflow

```text
Create protected CI release workflow triggered by reviewed tag/release, verifies
full gates and version consistency, signs artifacts/provenance with short-lived
identity, publishes first to a test registry and requires environment approval
for production. Use trusted publishing rather than long-lived tokens when
available. Add dry-run tests and no-op on forks/untrusted PRs.
```

### S5-P06D — canary, compatibility and rollback drill

```text
Install candidate artifacts in clean environments and run smoke/live API contract
against supported server versions. Verify upgrade/downgrade/config preservation,
revoked credential and previous-version compatibility. Exercise yank/deprecate/
rollback and compromised release response; immutable versions are never silently
replaced. Record recovery time and owner.
```

### S5-P06E — Scope 5 release gate

```text
Run public API, SDK, CLI, automation, package matrix, supply-chain and two-
workspace E2E. Publish exact operation/command/version support, checksums,
signatures, SBOM/provenance and known limitations. Confirm no internal imports,
plaintext credentials or unbounded operations. Release only after protected CI
evidence; otherwise keep artifacts internal candidates.
```
