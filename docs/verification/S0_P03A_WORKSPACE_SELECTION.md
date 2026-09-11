# S0-P03A workspace selection contract

**Status:** CURRENT  
**Plan/prompt:** S0-P03A  
**Alembic head:** `0058_simulation_workspace`  
**ADR:** [0003-workspace-selection.md](../adr/0003-workspace-selection.md)

One active-workspace contract for browser and API. The selector is never
proof of access: every tenant route still runs `resolve_workspace_access`.
The Python client keeps explicit `X-Workspace-Id` and does not inherit the
browser session.

## Evidence

### 2026-09-11 local browser re-verification

The complete browser acceptance suite passed 18/18 against a fresh database at
`0058_simulation_workspace`, including the visible active-workspace selector,
session persistence, role-aware routes, and foreign-workspace rejection. The
full backend/SDK regression passed 1,031 tests with one live-OpenAI skip.

```text
Plan/prompt ID: S0-P03A
Claim: Browser and API share one membership-checked active workspace; zero/one/many memberships; session persistence; query cache re-key; Python client stays explicit
Status: VERIFIED (API + source + drift + local browser E2E)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: 0058_simulation_workspace; historical Alembic catalogs still match
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.check_truth_drift
  cd apps/web && npx tsc --noEmit && npm run lint
Expected result: selection tests green; drift clean at 0057 / 66 tables / 167 ops
Observed result: 1015 passed, 3 skipped, 21 warnings, 560.97s; drift all [clean]; tsc clean; lint 3 pre-existing model-build hook warnings (session-provider fixed)
Artifact/log/dashboard link: docs/adr/0003-workspace-selection.md
Security and tenant checks: header cannot grant a foreign workspace; cross-workspace project ids stay 404; suspended memberships are not selectable
Rollback/kill switch: alembic downgrade to the previous session-hardening revision
Known limitations: capability matrix and 403-vs-404 standardization remain S0-P03B
Reviewer/date: S0-P03A / 2026-09-10
```

## Contracts (intentional, additive)

- Column `auth_sessions.selected_workspace_id` (nullable FK, ON DELETE SET NULL)
- `PUT /auth/workspace` `{ workspace_id }` — browser session + CSRF only
- Additive `/auth/me` and `/v1/me` fields: `active_workspace_id`, `workspaces`,
  `request_id`
- `GET /v1/workspaces` list schema unchanged
- Response header `X-Request-Id` on all API responses

Rollback: `alembic downgrade 0056_auth_hardening`. Revert this tree.

## Next prompt

**S0-P03B** — centralize capability authority and prove isolation.
