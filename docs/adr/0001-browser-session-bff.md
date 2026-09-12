# ADR 0001 — Browser sessions and BFF (S0-P02A)

**Status:** Accepted  
**Date:** 2026-09-10  
**Prompt:** S0-P02A  

## Context

The web app stored a long-lived JWT in `dclab_token`, a cookie JavaScript could
read (`document.cookie`), decode (`atob` of the payload, including `role`), and
attach as `Authorization: Bearer`. Next.js middleware verified that JWT with
`jose` and routed by the **token role claim**. The API already loaded `User`
from PostgreSQL after `sub`; the token role was not used for API authorization,
but the browser treated it as session state.

The browser origin (`:3001`) and API origin (`:8001`) are different. A `Set-Cookie`
from the API with `SameSite=Lax` is not sent on cross-site `fetch` from the
Next app. `SameSite=None; Secure` is not usable on local HTTP. S0-P02D completes
the BFF contract: generate/echo `X-Request-Id`, always `Cache-Control: no-store`,
bounded upload/download streaming, JSON `502 {"detail":"backend unavailable"}`
when FastAPI is unreachable, and never forward `Authorization`. The BFF is not
a second authorization layer and must not log bodies or credentials.

## Decision

1. **Browser authentication** is an opaque server-issued session, not a JWT.
2. **Next.js is a BFF.** The browser talks only to the web origin
   (`/api/backend/*`) with `credentials: "include"`. The BFF copies
   `dclab_session` onto `:3001` and forwards `X-DCLab-Session` to FastAPI.
   The browser never reads, decodes, or attaches a bearer token.
3. **API / non-browser clients** use a **distinct** path: `POST /auth/tokens`
   returns `{ access_token, token_type, user }` and sets **no** session cookie.
   Existing pytest, SDK-style scripts, and audit crawls use this path (or
   `create_access_token` in-process).
4. **Storage** is PostgreSQL table `auth_sessions`. The cookie value is a
   32-byte urlsafe secret. Only `SHA-256(token)` (hex, 64 chars) is stored.
   Raw session values are never written to logs or ordinary columns.
5. **No refresh-token family.** One opaque session has idle and absolute
   expiry. Idle slides on authenticated use. There is no second cookie.

### Cookie

| Attribute | Value |
| --- | --- |
| Name | `dclab_session` |
| HttpOnly | yes |
| Secure | yes when `DCLAB_ENV`/`APP_ENV`/`ENVIRONMENT` is `production`/`prod`, or `SESSION_COOKIE_SECURE=true`; no on local HTTP |
| SameSite | `Lax` (same-site after BFF; CSRF hardening is S0-P02B) |
| Path | `/` |
| Domain | unset (host-only) |
| Max-Age | absolute lifetime |

### Lifetimes

| Clock | Default | Behavior |
| --- | --- | --- |
| Idle | 12 hours | Sliding; `last_seen_at` / `idle_expires_at` update on use (throttled) |
| Absolute | 7 days | Hard cap from `created_at`; idle cannot extend past it |

API bearer JWT lifetime remains `access_token_minutes` (default 30 days) and is
**not** the browser session.

### Rotation

- Every successful `POST /auth/login` or `POST /auth/register` issues a **new**
  session row and cookie.
- If the request already carried a session cookie, that session is revoked
  (`rotated_from_id` records the predecessor when present).
- Changing `users.role` (privilege / seed refresh) revokes **all** sessions for
  that user. JWT API tokens are not session rows; they remain valid until expiry
  or `users.is_active` is false. Authorization always reloads `User` from the
  database and **never** trusts a JWT `role` claim.

### Revocation and logout

- `POST /auth/logout` sets `revoked_at` on the presented session and clears the
  cookie (`Max-Age=0`).
- Lookups reject missing, unknown hash, revoked, idle-expired, and
  absolute-expired sessions with HTTP 401.
- JWT logout cannot revoke the signed token; clients drop the bearer. Disabled
  users fail `user_from_token`.

### Credential binding

`user_agent_hash` may be stored (SHA-256 of the User-Agent). P02A does **not**
reject mismatched User-Agent (false positives on browser upgrades). Binding and
concurrent-session policy are S0-P02B.

### Authorization

- `get_current_user` accepts `Authorization: Bearer` **or** cookie /
  `X-DCLab-Session`.
- Effective role comes from the `users` row (and membership tables already used
  by `authorization_service`). The JWT payload `role` is ignored.
- Next middleware fetches `GET /auth/me` with `X-DCLab-Session` and uses the
  **server** role only to keep area 403 HTML. It does not verify JWTs. Workspace
  capability authority remains S0-P03.

### Failure behavior

| Case | Result |
| --- | --- |
| Bad email/password | 401, no cookie, no session row |
| Missing/invalid/expired/revoked session | 401; BFF returns 401; web client drops the signed-in user |
| Middleware cannot reach the API | Redirect to `/login` (fail closed) |
| Production JWT default or Secure cookie off | Process **does not boot** |
| CSRF / throttling / CSP | Not in this ADR (S0-P02B) |

### Retention

Rows with `absolute_expires_at` or `revoked_at` older than
`session_retention_days` (default 30) are deleted in bounded batches on
session issue and authenticate, and by the `auth.session_cleanup` worker
handler (S0-P02C). PostgreSQL is the session store; do not add Redis for
sessions.

### Tenancy

`auth_sessions` is identity-plane state: `user_id` → `users.id` ON DELETE
CASCADE. It is **not** workspace-scoped. Workspace authorization stays on
each request via membership + `X-Workspace-Id` (S0-P03).

## Alternatives considered

1. **API sets `SameSite=None; Secure` on `:8001`.** Fails local HTTP; still
   exposes a bearer-shaped cookie to the API origin; JS must not read it, but
   the browser still cross-origin credential-posts to the API (CSRF surface).
2. **JWT in HttpOnly cookie without a session table.** Cannot revoke before
   expiry; middleware would still decode claims; role-in-token remains tempting.
3. **Redis sessions.** Extra runtime for Scope 0; PostgreSQL already authoritative.

## Consequences

- `POST /auth/login` and `POST /auth/register` JSON **no longer** include
  `access_token`. That is an intentional breaking change for anyone who used
  those routes as an API token factory; they must call `POST /auth/tokens`.
- Next.js must proxy API calls; `NEXT_PUBLIC_API_URL` is not used from browser
  JavaScript. Cookie names on the BFF (`DCLAB_SESSION_COOKIE` /
  `DCLAB_CSRF_COOKIE`) must match the API. Upload/download caps are BFF process
  protection (`DCLAB_BFF_MAX_UPLOAD_BYTES` / `DCLAB_BFF_MAX_DOWNLOAD_BYTES`),
  not a product ingest policy.
- Emergency kill switch: `AUTH_BROWSER_SESSIONS_ENABLED=false` (S0-P02E).
- Alembic `0055_auth_sessions` adds one table.
- S0-P02B still owns CSRF tokens, CSP, login throttling, concurrent-session
  caps, and browser E2E for refresh/revocation abuse cases.

## Rollback

Revert the application revision and run `alembic downgrade 0054_execution_needs_input`.
Browser users sign in again. API clients that already use `/auth/tokens` keep
working. Kill switch: do not start the API if production boot validation fails.
