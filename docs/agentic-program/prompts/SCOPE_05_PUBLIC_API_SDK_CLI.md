# Scope 5 prompts — public API, SDK, and customer CLI

Use the common preamble. The existing `app.cli` remains an internal operator
CLI; the customer CLI must be a separate SDK client.

## Plan 5.1 — machine identity

### S5-P01A — service accounts and scoped tokens

```text
Add workspace-bound ServiceAccount and ApiToken records/services with owner,
purpose, status, optional project restriction, scopes, public prefix, slow hash,
family/version, issue/expiry/last-use/revoke and rotation-parent metadata. Show
the secret once; never store/log it plaintext. Define maximum lifetime, overlap
rotation and immediate revocation. Authenticate machine principals distinctly
from users, validate audience and intersect scopes with workspace capabilities,
resource state, data/tool policy, budget and approval. Add create/list/issue/
rotate/revoke /v1 APIs and audit.
```

### S5-P01B — OAuth/device flow and credential security tests

```text
Integrate an established identity provider for interactive CLI device
authorization and hosted MCP prerequisites; do not build a custom OAuth server
just for convenience. Test device success/denial/expiry/poll interval, issuer/
audience/signature/expiry/scope, token rotation overlap, revoked service account,
project restriction, two workspaces, last-use update and rate limits. Prove raw
tokens do not appear in database dumps, object repr, CLI args, shell examples,
errors, logs or traces. Document CI secret-store and workload-identity options.
```

## Plan 5.2 — complete `/v1`

### S5-P02A — resource and command closure

```text
Inventory every web/agent/notebook/connector-needed capability and expose a
coherent resource-oriented /v1 contract through application services. Complete
identity/workspace/project/ProblemSpec, dataset/profile/columns/lineage/upload,
execution/build/events, visualizations/artifacts, agent, approval and notebook
resources available at this phase. Standardize names, 202 async semantics,
opaque cursors, ETags, Idempotency-Key, request IDs, rate/quota headers and safe
errors. Keep legacy routes as measured compatibility adapters, not a second
implementation.
```

### S5-P02B — compatibility, fuzz and live API gate

```text
Generate/check an OpenAPI snapshot and breaking-change report; maintain a
deprecation window and supported server version metadata. Test every operation
for auth, scope/role/capability, two workspaces, suspended/revoked principal,
schema/body/count limits, cursor tampering, ETag conflict, idempotency race,
accepted/completed distinction, rate/quota and redaction. Fuzz schemas and safe
errors. Run contract tests against a live PostgreSQL API and confirm audit/client
name/version/request identity.
```

## Plan 5.3 — public Python SDK

### S5-P03A — sync/async SDK architecture

```text
Evolve packages/dclab_client into an independently versioned SDK with sync and
async clients; injected/device/service auth providers; workspace context;
typed resources; stable exception hierarchy; safe retry only for read or
idempotent commands; idempotency helpers; cursor iterators; event resume;
explicit waiters with timeout versus remote cancellation; streaming upload/
download; artifact digest verification; request/client version headers; and
supported API range. It must import no app/SQLAlchemy/engine modules.
```

### S5-P03B — SDK parity and transport-failure suite

```text
Test sync/async parity, refresh/revocation, two-workspace context, schema/error
mapping, Retry-After, idempotency conflict, cursor early-stop/tamper, event
reconnect, waiter timeout, cancellation, streaming without full buffering,
digest mismatch and redaction of token/cookie/signed URL. Run unit transport
failures and live API contract tests. Build wheel/sdist in clean supported
Python environments and validate metadata/types.
```

## Plan 5.4 — customer CLI core

### S5-P04A — create the separate CLI package

```text
Create packages/dclab_cli with a dclab executable that depends only on the
public SDK. Implement auth login/status/logout; config/profile; workspace
list/use/show; project list/show/create; dataset list/show/upload/profile;
build create/list/show/events/wait/cancel/retry; artifact list/download/verify;
agent run/list/show/events/cancel/approve; notebook list/show/export; and
version/help for features currently released. Admin-only operations belong
under an explicit admin group or separate package. Print exact workspace before
mutations and never make async acceptance look complete.
```

### S5-P04B — auth profiles and command mapping verification

```text
Store refresh credentials in the OS keychain; profile files contain only base
URL, workspace, credential reference, timeout and output preference. Support
non-interactive service-account credentials from a secret source. Refuse
secrets as ordinary CLI flags where safer channels exist. Test every command's
SDK mapping, capability/two-workspace behavior, device-flow states, missing
keychain, profile switch, token revocation, idempotency and request-ID display.
Use true executable tests in supported OS/Python matrix.
```

## Plan 5.5 — CLI automation contract

### S5-P05A — stable output and exit semantics

```text
Add human, --json, --jsonl, --quiet and --no-color output. Keep progress on
stderr and data on stdout. Version machine schemas. Implement documented exit
codes 0 success, 1 unclassified, 2 usage, 3 auth, 4 authorization/policy, 5
conflict, 6 remote failed terminal state, 7 local timeout while remote may run,
8 rate/quota. Add shell completion. Handle Ctrl-C by requesting remote cancel
only when supported and clearly report remaining state.
```

### S5-P05B — snapshot, signal and secrecy tests

```text
Test JSON/JSONL schema snapshots, stdout/stderr separation, non-TTY color,
pagination, empty/large lists, progress, timeout without implicit cancellation,
Ctrl-C before/after acceptance, failed/cancelled remote states, download digest,
completion scripts and every exit code. Scan process args, fixtures, help,
tracebacks and recorded output for credentials/signed URLs. Make automation
errors include stable DCLab code and request ID.
```

## Plan 5.6 — packaging and release

### S5-P06A — reproducible signed artifacts

```text
Create protected CI workflows that build SDK/CLI wheels and sdists from locked
dependencies, test installation, generate hashes/SBOM/provenance, scan license/
vulnerability/secrets, sign artifacts and produce a compatibility table and
release notes. Pin the CLI's supported SDK range without coupling server, SDK
and CLI release cadence. Prevent publication from unreviewed branches.
```

### S5-P06B — release/canary and rollback drill

```text
Publish to a private/test index, install in clean environments, authenticate to
staging, run the full read and controlled-build flow, revoke credentials and
verify behavior. Test server N/N-1 compatibility and unsupported-major failure.
Exercise package rollback/yank guidance and compromised-token/package incident
runbook. Promote to customer preview only with current provenance and contract
evidence.
```

