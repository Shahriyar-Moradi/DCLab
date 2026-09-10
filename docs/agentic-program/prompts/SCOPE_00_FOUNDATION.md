# Scope 0 prompts — foundation closure

Run these prompts in order after using the required preamble in `README.md`.
Scope 0 changes the platform foundation only; it does not add a user-visible
agent. Preserve the verified deterministic ML behavior.

## Plan 0.1 — current truth package

### S0-P01A — regenerate the repository truth baseline

```text
Audit the current DCLab checkout and replace stale future-planning facts with a
reproducible baseline. Record the exact SHA, branch/remote state, Alembic heads,
SQLAlchemy table count, migration count, runtime OpenAPI path/operation counts,
/v1 operation list, backend/frontend/package/test inventories, and latest CI
result. Re-run backend/SDK tests, Python static checks that exist, web lint,
standalone typecheck, production build, and existing browser E2E or reference
the exact same-SHA required CI evidence when local E2E would duplicate it.

Create a current verification report and update docs indexes so old reports are
clearly historical rather than silently wrong. Reconcile the 0053-era audit
with current head 0054 and the canonical target/needs_input changes. Classify
every claim VERIFIED, IMPLEMENTED, PARTIAL, PLANNED, BLOCKED, or NOT_TESTED.
Do not change application behavior in this prompt. Add a reproducible command
script only if the repository lacks one, and keep it read-only by default.
```

### S0-P01B — automate truth drift detection

```text
Turn the baseline's mechanically verifiable facts into CI checks. Detect
multiple Alembic heads, model/migration drift, duplicate revisions, unexpected
public API breaking changes, SDK route/type drift, stale generated API facts,
broken docs links, tracked object-store/Playwright output, production-default
secrets, and contradictory current-status documents. Generate/check a /v1
OpenAPI snapshot deterministically. Add tests proving the checks fail on small
synthetic violations and pass on the current tree. Keep historical reports
immutable; mark their status in an index instead of rewriting their evidence.
Document how to refresh snapshots and how reviewers distinguish an intentional
contract change from accidental drift.
```

## Plan 0.2 — secure browser session

### S0-P02A — implement server-issued sessions/BFF

```text
Replace the JavaScript-readable dclab_token bearer-cookie flow with a
server-issued browser session or BFF contract. Write an ADR covering session
storage, access/refresh lifetime, rotation, revocation, logout, credential
binding, and failure behavior. Implement HttpOnly, Secure in deployed modes,
appropriate SameSite, narrow Path/Domain, idle and absolute expiry, rotation on
login/privilege change, and server-side revocation. The browser must no longer
read, decode, or attach a long-lived bearer token. Preserve an API bearer flow
for non-browser clients behind a distinct authentication path. Add production
boot validation so unsafe cookie/secret configuration fails closed.

Update login/register/me/logout contracts and the web request layer. Do not use
the token's role for authorization; server membership remains authoritative.
Add database records only if required by the ADR, with hashed identifiers and
retention/cleanup behavior. Never store raw session/refresh values in logs or
ordinary database columns.
```

### S0-P02B — session, CSRF, CSP, and abuse verification

```text
Harden and verify the browser session end to end. Add CSRF protection for every
cookie-authenticated mutation, trusted-origin checks, security headers/CSP,
output-safe rendering, login throttling, uniform credential errors, session
revocation, concurrent-session policy, password-reset/email-verification hooks,
and account recovery states appropriate to the chosen identity design. Test
cookie flags, no token access from client JavaScript, fixation prevention,
rotation, expiry with an injected clock, logout, revoked/suspended membership,
CSRF from an untrusted origin, XSS-sensitive sinks, proxy/TLS configuration,
and redaction from logs/errors. Add browser E2E for login, refresh, logout, and
revocation. Record limitations if full email or external identity delivery is
deferred, but keep insecure fallbacks disabled in production.
```

## Plan 0.3 — authoritative workspace and capabilities

### S0-P03A — implement workspace selection contract

```text
Implement one authoritative active-workspace contract for browser and API
requests. Cover users with zero, one, and multiple memberships. Add a visible
selector, selected-workspace persistence in the server session or a validated
X-Workspace-Id propagation path, and clearing/re-keying of every workspace
local query cache when switching. Display the active workspace on every
mutation and approval. The selector is never proof of access: resolve current
membership and return a safe denial/not-found result for unauthorized
resources. Update /v1/me and workspace reads as needed, with stable response
schemas and request IDs. Ensure the public Python client retains explicit
workspace selection without inheriting the browser session design.
```

### S0-P03B — centralize capability authority and prove isolation

```text
Make effective role and capabilities come from current PlatformMembership and
WorkspaceMembership state on all backend and frontend paths. Define bounded
caching and invalidation for role changes, suspension, capability changes, and
workspace suspension. Remove routing decisions that trust stale JWT role data;
the UI may render server-returned capabilities but the API must independently
authorize every operation. Build a route-operation matrix for platform admin,
platform developer, workspace owner/admin, ML engineer, viewer, legacy client,
suspended and anonymous principals. Add two-workspace negative tests across all
active route families, workspace switching browser E2E, similarly named
resources, cross-tenant IDs, and cache invalidation. Standardize when a safe
403 versus tenant-hiding 404 is returned and audit sensitive denials.
```

## Plan 0.4 — legacy tenant and raw-surface closure

### S0-P04A — retire or tenant-scope SimulationRun and Insights

```text
Inventory every SimulationRun creation, read, translation, CLI, seed and UI
path. Choose and document one honest outcome: add Workspace/Project lineage
with composite constraints and a provable ownership backfill, or remove the
legacy simulation/insights route from customer production navigation and deny
ambiguous historical rows. Never assign unknown legacy rows to a convenient
default workspace. If migrating, use expand/backfill/validate/non-null steps,
cover delete behavior, and update all workspace-scoped queries. Add two-
workspace tests proving /app/insights and every derivative cannot expose the
other tenant. Preserve historical evidence or archive it according to policy.
```

### S0-P04B — close capability and audience leakage

```text
Audit every admin, business, development, /app and /v1 response for effective
capability and audience filtering. Fix the documented raw observatory gap so
CV-fold, semantic-LLM, provider-audit, model-management, prediction-download,
decision-ledger and raw-debug details each require their own current capability.
Ensure platform developers and business developers remain read-only. Run the
static banned-terms scan plus live not-sampled route crawls for client,
business, platform, anonymous, suspended and cross-workspace identities. Add
coverage-drift checks so a new route fails CI until classified. Do not hide
fields only in React; shape them in transport-neutral query/translation
services and test serialized responses.
```

## Plan 0.5 — classification, quarantine, retention foundation

### S0-P05A — implement fail-closed dataset policy bootstrap

```text
Populate effective DatasetColumn sensitivity_class, classification_source,
model_use_policy and llm_exposure_policy during ingest with deterministic
rules. Add a versioned append-only policy-decision history and a review state
for unknown/high-risk columns if that slice is compatible with Scope 1's final
schema. Null or unknown must deny external LLM values and samples. Detect
credentials, direct identifiers, quasi-identifiers, sensitive domains, free
text, URLs/markup/control characters and rare categories. Automated detection
may tighten a decision but cannot make a column safer without policy. Add an
admin/user review workflow with supersession lineage, current snapshot update,
and two-workspace authorization. Keep the external LLM feature disabled until
all existing relevant columns have an effective decision.
```

### S0-P05B — quarantine and deletion skeleton

```text
Add production-shaped upload quarantine states and bounded validation before a
source becomes an accepted Dataset: expected/observed size, digest, MIME/file
signature, parser safety, archive expansion limits, decompression-bomb checks,
malware adapter contract, failure cleanup and accepted publication. Define
retention classes for source objects, quarantine, derived profiles, LLM
metadata, future agent state and audit. Implement an idempotent dry-run-first
retention/deletion planner that can locate database rows and object bodies by
lineage, revoke access, report legal/evidence exceptions and produce non-content
completion evidence. Full agent deletion arrives later, but Scope 0 tests must
prove quarantine expiry and object/database reconciliation without deleting
the wrong workspace.
```

## Plan 0.6 — stable `/v1` foundation

### S0-P06A — standard errors, cursors, correlation and compatibility

```text
Create one public error envelope with stable code, safe message, retryable,
request_id and bounded details. Convert /v1 validation, auth, not-found,
idempotency and state errors without leaking stack/database/provider bodies.
Return X-Request-Id on success and every failure and propagate it into
ExecutionRequest, MlJob and events. Replace raw/unbounded list responses with
{items,next_cursor}; use a signed/opaque cursor containing stable sort key and
ID tie-breaker, never trusted tenant authority. Add list endpoints currently
missing, especially execution requests and model builds, with filters and
limits. Check in an OpenAPI snapshot and breaking-change gate. Update
dclab_client types/errors and route-coverage tests in the same change.
```

### S0-P06B — lifecycle, artifact, cancel and retry skeleton

```text
Add application-service and /v1 contracts for ExecutionRequest events,
cooperative cancel, and child retry without resetting parent evidence. Define
state preconditions, idempotency replay/conflict, cancellation timestamps,
retry lineage, safe terminal errors, cursor ordering and accepted-versus-
completed semantics. Add artifact metadata and short-lived download-
authorization contracts that recheck workspace, capability, type, retention,
classification and digest; never persist or return long-lived signed URLs.
Wire the Python client. This prompt may establish lifecycle primitives used by
Scope 3 but must not expose a model-build create command until its atomic
service exists. Verify concurrent cancel/claim, retry, cross-workspace IDs,
expired authorization and missing/corrupt object behavior with PostgreSQL.
```

## Plan 0.7 — development and CI parity

### S0-P07A — one-command full development topology

```text
Evolve local Compose into a reproducible synthetic stack with migration, API,
web, worker-ml, placeholder worker-agent/integration profiles, private local
object storage and health checks. Use fake providers by default and no
production credential fallback. Seed two workspaces, role variants, successful/
failed/cancelled runs and sensitive/unknown columns. Separate handler allowlists
and shared storage correctly. Provide up/down/reset/smoke commands that do not
silently erase a developer database. Verify empty-checkout startup, migrations,
API/web health, worker claim, persisted artifacts and shutdown/cleanup. Align
Dockerfile and lockfiles with CI Python/Node versions and production boot
validation.
```

### S0-P07B — CI and frontend quality gates

```text
Split CI into explicit static-policy, Python lint/type, migration-empty,
migration-upgrade, backend-postgres, scientific-correctness, SDK-contract,
web-lint-type-build, web-component, whole-system E2E, container-build/scan and
docs-link jobs while preserving required coverage. Add a frontend component
test runner for session, workspace headers/context, schemas, async empty/error/
cancel states and accessible alerts. Migrate deprecated next lint usage to the
supported ESLint CLI and fix the current React hook dependency warnings. Avoid
parallel commands that race on .next. Add secret/dependency/license/container
checks with pinned outputs, and document which expensive staging tests remain
scheduled rather than PR gates.
```

## Plan 0.8 — ADRs, database graph, and scale baseline

### S0-P08A — lock architecture decisions

```text
Create reviewed ADRs for: one /v1 public boundary; ExecutionRequest versus
MlJob; PostgreSQL queue for initial agent jobs; provider-neutral LLM gateway;
null-equals-deny data exposure; DCLab-owned durable agent records; managed-cell
notebook before code; isolated code plane; SDK-first CLI and MCP;
transactional outbox; structured retrieval before vectors; BFF/session design;
and the first production identity/provider/region assumptions that must be
decided now. Each ADR includes context, decision, alternatives, consequences,
security/tenancy impact, migration/rollback and revisit trigger. Update doc
navigation and mark superseded conflicting claims.
```

### S0-P08B — analyze the relational cycle and record scale thresholds

```text
Investigate the SQLAlchemy/Alembic sort warning involving datasets,
execution_requests, experiments and ingestion_runs. Draw the actual FK graph,
identify which use_alter/deferred relationships are intentional, and prove
empty/upgrade migrations plus deletes. Repair only if a simpler safe direction
preserves canonical lineage; otherwise document and add a regression guard so
a future library upgrade cannot turn the warning into failure. Refresh table
cardinalities, indexes, slow-query baseline, queue claim plans, object growth,
connection limits, backup assumptions and thresholds for PgBouncer, read
replicas, partitioning, a separate broker, distributed compute and vector
retrieval. Do not implement scale components without a triggered threshold.
```

## Scope 0 completion prompt

After S0-P08B, run one final scope review that maps every Scope 0 exit criterion
to L0–L5 evidence, lists any waiver with owner/expiry, and prevents enabling the
Scope 1 feature flag when a P0 item is unresolved.
