# DCLab Access Architecture

## Scope

This document defines DCLab's multi-tenant identity and authorization foundation.
`Workspace` is the only business/tenant boundary. The design does not introduce
an Organization tenant and does not change the ML pipeline or implement Pipeline
Observatory.

## Role matrix

| Membership | Role | Platform visibility | Workspace visibility | Platform writes | Workspace writes |
| --- | --- | --- | --- | --- | --- |
| Platform | `dclab_admin` | All | All workspaces | Yes | Yes |
| Platform | `dclab_developer` | All | All workspaces | No | No |
| Workspace | `business_admin` | No | Membership workspaces only | No | Yes, in membership workspaces |
| Workspace | `business_developer` | No | Membership workspaces only | No | No |
| Legacy compatibility | `client_user` | No | `users.workspace_id` only | No | Yes, until migrated |

Read-only means every unsafe HTTP method is rejected by the backend guard. This
includes `POST`, `PUT`, `PATCH`, and `DELETE`, so developer roles cannot trigger
training, reruns, deep verification, configuration changes, or membership/user
management when those operations are exposed beneath the protected route trees.

## Tenant and persistence model

`workspaces` remains canonical. Business-scoped records reference a Workspace ID,
and access to those records is always filtered by that ID in backend queries.

- `business_profiles` is one-to-one with `workspaces` (`workspace_id` is both its
  primary key and cascading foreign key). It holds legal name, industry, and
  additional business metadata.
- `platform_memberships` grants one platform role per user. `user_id` is unique.
- `workspace_memberships` grants a user a business role in a workspace. The pair
  `(workspace_id, user_id)` is unique, allowing one user to belong to multiple
  businesses without duplicate authority rows.
- `workspace_capabilities` records workspace-level capability configuration. The
  pair `(workspace_id, capability)` is unique.

Foreign keys for the new dependent records use cascading deletes. Lookup indexes
cover membership role/user/workspace fields and capability workspace/key fields.
Opportunity external IDs are unique within a workspace, not globally, so separate
businesses may use the same source-system identifier.

## Authorization boundary

Authorization is centralized in `app.services.authorization_service` and exposed
to FastAPI through `app.api.deps`. The canonical primitives are:

- `require_platform_read`
- `require_platform_admin`
- `require_workspace_read`
- `require_workspace_admin`
- `can_write_platform`
- `can_write_workspace`

The `/admin` parent router uses a method-aware platform guard: platform developers
may read, while only platform admins may use unsafe methods. The `/app` parent
router applies the equivalent workspace guard. Because guards are attached to the
parent routers, newly mounted endpoints inherit the same policy.

For `/app`, the backend resolves one `WorkspaceAccess` context before the handler
runs. `X-Workspace-Id` is only a requested selector. It is parsed and checked
against persisted memberships; it is never accepted as evidence of authority.

The request flow is:

1. Verify the signed bearer token and reload the active user from the database.
2. Load authoritative platform/workspace membership rows from the database.
3. Validate the requested workspace against those rows.
4. Store the validated workspace context for the request.
5. Pass that workspace ID into workspace-scoped service queries and writes.

A business member selecting an unauthorized workspace receives `403`. A request
for an object that does not exist inside the already-authorized workspace receives
`404`, even if an object with that ID exists in another workspace. Platform members
may select any existing workspace; a nonexistent workspace receives `404`.

Users with several workspace memberships use their legacy primary
`users.workspace_id` when it is one of those memberships. If there is no primary
and more than one choice, the request must explicitly select an authorized
workspace. The frontend is not an authorization boundary; navigation visibility
and JWT role claims are only presentation hints.

## Legacy compatibility and authority precedence

Migration `0022_multi_tenant_identity` keeps `users.role` and expands its allowed
values so identity responses can represent the four new roles. It backfills:

- every existing `dclab_admin` into a `platform_memberships` `dclab_admin` row;
- every existing workspace-scoped `client_user` into a
  `workspace_memberships` `business_admin` row;
- one `BusinessProfile` for every existing Workspace.

An explicit membership is authoritative. `users.role` cannot add permissions on
top of it. For example, an account whose legacy role string is `dclab_admin` but
whose explicit platform membership is `dclab_developer` is read-only.

Compatibility fallback is deliberately narrow:

- a legacy `dclab_admin` with no platform membership retains admin access;
- a legacy `client_user` with no workspace memberships retains access only to
  `users.workspace_id`, with its historical write behavior.

The presence of any explicit workspace membership disables `client_user` fallback,
which prevents authority from being combined ambiguously. New users are created
with a matching membership row immediately.

### Eventual removal path

1. Deploy and complete the membership backfill.
2. Monitor for users exercising the legacy fallback and repair missing membership
   rows.
3. Change identity payloads and UI labeling to derive display roles from the active
   membership context rather than `users.role`.
4. Remove legacy fallback only after no active account depends on it.
5. In a later forward migration, remove `users.role` and, if no longer needed as a
   primary-workspace hint, `users.workspace_id`.

That removal is intentionally outside this foundation change.

## Security verification

Automated tests cover all four roles, legacy compatibility, explicit-membership
precedence, cross-tenant selectors, cross-tenant object IDs, platform visibility
across tenants, workspace write isolation, and generic denial of `POST`, `PATCH`,
and `DELETE` for both developer roles. Existing route-table tests continue to prove
that every `/admin` and `/app` operation inherits authentication.
