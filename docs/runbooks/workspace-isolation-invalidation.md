# Workspace isolation and capability invalidation

**Prompt:** S0-P03E  
**Authority:** PostgreSQL membership, platform role, entitlement and feature rows. A selected workspace, browser capability hint, JWT role claim or cached UI result is never permission.

## Invalidation bound

- `effective_capability_matrix` caches only in the current SQLAlchemy transaction. A flush of a user, membership, workspace, entitlement or workspace-feature row clears that transaction's cached matrix; commit/rollback ends reuse.
- Every tenant request resolves current server membership and capabilities. After a committed removal, suspension, role or entitlement change, the **next request** must reflect it. An already authorized in-flight request is not retroactively cancelled; long-running operations must recheck at their own execution boundary.
- `auth_sessions.selected_workspace_id` is a remembered selector. A stale selection is ignored for identity display, and an explicit stale/unauthorized header is denied. Do not use the session row or `/v1/me` display hints as an authorization cache.
- Browser tenant queries are keyed by active workspace and cleared/cancelled on switch. BFF forwards one untrusted `X-Workspace-Id`; the API rechecks membership. Bearer callers must explicitly send that header on tenant routes.

## Detection and triage

`dclab.workspace_access` emits `workspace_audit` JSON with fixed `family` and `reason` values, request ID, and UUID actor/workspace references when known. It never logs a raw selector, token, email, path or response body. The same bounded family/reason pairs have process-local counters through `workspace_access_metric_counts()`. Aggregate the structured log in production; do not treat process-local counts as durable or globally complete. Keep the audit sink access-controlled and retention-bound.

Watch for a sudden rise in `denial.unauthorized_selector`, `denial.path_mismatch`, `denial.capability_denied`, or `denial.malformed_selector`; compare by deployment and time, **not** by user/workspace as metric labels. `selection.selected`, `selection.cleared` and `denial.selection_denied` distinguish normal switches from denied attempts. A spike is an investigation signal, not proof of an attack.

1. Correlate one safe request ID with the API response and structured audit event. Do not copy cookies, bearer tokens, raw object paths or customer data into a ticket.
2. Check current `workspace_memberships`/`platform_memberships`, suspension, role, entitlement and workspace-feature state in the authoritative database. Check the request's explicit selector and resource's workspace independently.
3. Verify a fresh request after the committed change: permitted workspace succeeds; removed/suspended workspace returns 403; cross-workspace resource/path remains 404. If this fails, contain traffic and investigate the server resolver/cache before changing UI.
4. Check both browser tabs and bearer clients. A browser switch should cancel/clear old tenant queries; a bearer client must supply its own selector per request.

## Containment and rollback

- If the selector UI or query invalidation regresses, roll back that frontend feature/deployment or temporarily disable affected tenant operations **fail closed**. Keep API membership checks, explicit bearer scoping, resource-workspace matching and safe 403/404 behavior enabled.
- If a capability feature is unsafe, disable its allowlisted `workspace_capabilities` flag for the affected workspace. This removes a feature; it must never broaden access or reactivate a global/default-workspace fallback.
- If API authorization itself regresses, stop affected tenant traffic and restore the last verified server release. Do **not** revert to trusting JWT role, selected session workspace, BFF headers, browser cache, or global `/admin` privileges for tenant routes.
- No schema rollback or object cleanup is needed for S0-P03E. Re-run the PostgreSQL isolation matrix and browser slow-switch/BFF-spoof tests before reopening traffic.

## Verification commands

Use an isolated disposable PostgreSQL database; the backend test fixture recreates its `decisionai_test` schema. The browser seed script accepts only a database name containing `verify` or `e2e` and `--recreate` drops that exact database. Validate the target first.

```bash
DATABASE_URL=<disposable-postgres-url> .venv/bin/pytest -q --tb=short \
  apps/api/tests/test_workspace_isolation_gate.py \
  apps/api/tests/test_central_capability_authority.py \
  apps/api/tests/test_workspace_selection.py \
  apps/api/tests/test_access_control.py
```

Run `apps/web/e2e/session-security.spec.ts` against a separate disposable E2E database with `DCLAB_E2E_DATABASE_URL`, `DCLAB_E2E_API_URL` and `DCLAB_E2E_WEB_URL`. Its slow-response switch and BFF-spoof cases are required for this gate.
