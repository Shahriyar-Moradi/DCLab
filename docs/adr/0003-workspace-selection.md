# ADR 0003 — Authoritative active workspace (S0-P03A)

**Status:** Accepted  
**Date:** 2026-09-10  
**Prompt:** S0-P03A  
**Depends on:** [0001-browser-session-bff.md](0001-browser-session-bff.md),
[0002-session-csrf-csp-abuse.md](0002-session-csrf-csp-abuse.md)

## Context

Workspace is the tenant boundary. API authorization already treats
`X-Workspace-Id` as a **selector, never proof**:
`resolve_workspace_access` reloads current membership (or platform role)
on every request. The browser did not send that header, had no visible
selector, and React Query keys were not workspace-scoped, so switching
tenants could show another workspace's cached lists.

`users.workspace_id` is the account **home** workspace, not the active
selector. `/v1/me` returned only that home id.

The public Python client (`packages/dclab_client`) already takes an
explicit `workspace_id` constructor argument and sets `X-Workspace-Id`.
It must not grow browser cookies, `/auth/workspace`, or session selection.

## Decision

### One contract, two transports

1. **Browser** — persist `auth_sessions.selected_workspace_id` (nullable FK
   to `workspaces.id`, `ON DELETE SET NULL`). Identity-plane: the session
   row is **not** tenant-scoped. The column is a remembered selector.
   `PUT /auth/workspace` (CSRF + trusted Origin) writes it after
   membership is re-checked. The web client also sends `X-Workspace-Id`
   on every tenant call (BFF already forwards it).
2. **API / SDK** — no session selection. Bearer clients pass
   `X-Workspace-Id` on each request (or rely on existing single-membership
   / home / platform-default fallbacks). `PUT /auth/workspace` returns
   HTTP 400 for bearer credentials.

Header, when present, always wins over the session column. An unauthorized
header is still denied. A stale session selection that is no longer an
active membership is ignored; resolution falls through to home, sole
membership, or HTTP 400 “select an authorized workspace”.

### Zero, one, and many memberships

| Active memberships | Login default | Tenant routes without selector |
| --- | --- | --- |
| 0 | `selected_workspace_id` null | 403 `not authorized for a workspace` |
| 1 | that workspace | that workspace |
| N | home workspace if it is still active; else null | home if active; else 400 until header or `PUT /auth/workspace` |

Platform members may select any **existing** workspace. Missing workspace
id → 404. The UI selector lists workspaces the server returns; it never
authorizes.

Unauthorized or cross-tenant **resource** ids keep the existing safe
not-found (404) behavior. Unauthorized **selector** values keep 403
`not authorized for this workspace`.

### `/v1/me` and workspace reads

- `workspace_id` remains the home/legacy field (stable).
- Additive: `active_workspace_id`, `workspaces[]` (`id`, `slug`, `name`,
  `kind`, `role` or null for platform-visible rows), `request_id`.
- `GET /v1/workspaces` stays `list[WorkspaceRead]` (stable list schema).
- Every API response echoes `X-Request-Id` (incoming value, or a new UUID).

### Browser cache

React Query keys for tenant data are prefixed with the active workspace
id. Switching calls `PUT /auth/workspace`, updates the in-memory selector
used by the API client, and **removes** workspace-prefixed (and other
non-health) queries so lists cannot leak across tenants.

Mutation and approval surfaces display the active workspace name and id.
That chrome is not an access check.

### Python client

`DCLabClient(workspace_id=...)` remains the only selection mechanism.
The package still refuses non-`/v1` paths (including `/auth/workspace`)
and never sets `Cookie` / `X-DCLab-Session`.

## Alternatives considered

1. **Header only, no session column.** A refresh would lose the choice
   unless localStorage stored it; localStorage is not an authority and
   would still need server membership checks. Session persistence matches
   the BFF cookie model.
2. **JWT claim for selected workspace.** Repeats the S0-P02A mistake of
   treating a client-held token as authorization. Rejected.
3. **Inherit browser session into `dclab_client`.** Would couple SDK
   users to CSRF cookies. Rejected.

## Consequences

- Alembic `0057_session_workspace` is additive.
- Browser mutations that select a workspace require CSRF (ADR 0002).
- Capability matrices and 403-vs-404 standardization for every route
  family remain S0-P03B.

## Rollback

`alembic downgrade 0056_auth_hardening`. Revert this tree. Browser clients
that send `X-Workspace-Id` keep working against the previous resolver;
the session column is simply dropped.
