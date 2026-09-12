# ADR 0002 — CSRF, CSP, throttling, and recovery (S0-P02B)

**Status:** Accepted  
**Date:** 2026-09-10  
**Prompt:** S0-P02B  
**Depends on:** [0001-browser-session-bff.md](0001-browser-session-bff.md)

## Context

S0-P02A replaced the JavaScript-readable bearer cookie with an opaque HttpOnly
`dclab_session` and a Next.js BFF. Cookie-authenticated mutations were still
cross-site request-forgery surfaces. The API had no Content-Security-Policy,
no login throttle, no concurrent-session cap, and no password-reset or
email-verification records. Production must not grow an insecure “return the
reset token in JSON” fallback while SMTP is deferred.

## Decision

### CSRF and trusted origin

1. **Unsafe methods** (`POST`, `PUT`, `PATCH`, `DELETE`) that authenticate
   with the session cookie or `X-DCLab-Session`, plus anonymous
   `login` / `register` / recovery posts, require CSRF.
2. **Bearer JWT** (`Authorization: Bearer` and `POST /auth/tokens`) is
   **exempt**. API clients are not browser cookie sessions.
3. **Session CSRF** is HMAC-SHA256(`AUTH_CSRF_SECRET` or `jwt_secret`,
   `"csrf-v1:" + raw_session`). Production requires `AUTH_CSRF_SECRET`.
   The value is issued as non-HttpOnly cookie `dclab_csrf` and must be sent
   as `X-CSRF-Token`. The raw session never appears in the CSRF cookie.
4. **Anonymous CSRF** is double-submit: `GET /auth/csrf` sets `dclab_csrf`;
   the client echoes it in `X-CSRF-Token`.
5. **Origin** (or Referer origin) must match `CORS_ORIGINS`. Missing Origin on
   an unsafe protected request is rejected (fail closed).
6. The BFF copies `dclab_csrf` onto the web origin (not HttpOnly), forwards
   `X-CSRF-Token`, `Origin`, and the CSRF cookie to FastAPI. Browser JavaScript
   may read **only** `dclab_csrf` (never `dclab_session`).

### Headers and CSP

- FastAPI: `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
  `X-Frame-Options: DENY`, `Permissions-Policy`, API CSP
  `default-src 'none'; frame-ancestors 'none'; base-uri 'none'`,
  `Cache-Control: no-store` on `/auth/*`. HSTS when `DCLAB_ENV` is production.
- Next.js: document CSP `default-src 'self'` with `'unsafe-inline'` for
  scripts and styles until a nonce pipeline exists (recorded limitation).
  `frame-ancestors 'none'`, `form-action 'self'`.

The process does not terminate TLS. Secure cookies in production assume TLS
at the reverse proxy. `AUTH_TRUST_FORWARDED` defaults **false**;
`X-Forwarded-For` is ignored for throttle keys unless that flag is on.

### Throttling and uniform errors

- In-process counter keyed by SHA-256(`email|ip`), default 5 failures / 15
  minutes, HTTP 429 `"too many attempts"`. Clock is injectable in tests.
- Credential failures (unknown user, wrong password, disabled account) all
  return HTTP 401 `"invalid email or password"`.
- Limitation: the counter is per process, not shared across workers.

### Concurrent sessions and revocation

- Default cap **5** active sessions per user. Issuing a sixth revokes the
  oldest active row(s).
- `POST /auth/logout` revokes the presented session.
- `POST /auth/logout-all` revokes every session for the user.
- Privilege change still revokes all sessions (ADR 0001).
- Password reset confirmation revokes all sessions.

### Membership suspension

Additive nullable `workspace_memberships.suspended_at`. When the user has
**any** membership rows, those rows are authoritative: a suspended membership
yields no workspace role and **does not** fall through to the legacy
`client_user` workspace_id fallback. Account disable (`users.is_active`) still
invalidates the session (401).

### Recovery hooks (email deferred)

Table `auth_recovery_tokens`: hashed token, purpose
`password_reset` | `email_verification`, expiry, `consumed_at`. Identity-plane
(`user_id` → `users`, not workspace-scoped).

- `POST /auth/password-reset/request` and `/auth/email-verification/request`
  always return **204**. They never return a raw token. Unknown emails are
  indistinguishable.
- Confirm endpoints consume the hash, set the new password or
  `users.email_verified_at`, and never log the raw secret.
- Unverified email does **not** block login (would break demo and E2E).
- `AUTH_EMAIL_DELIVERY_ENABLED` is **false**. Production boot **fails** if it
  is true: there is no mail provider yet, and JSON-token delivery is forbidden.

### Rendering

React text nodes only. No `dangerouslySetInnerHTML`. Auth JSON uses
`application/json`. Observability redaction includes CSRF cookie/header names.

## Cookie matrix

| Name | HttpOnly | Secure (prod) | SameSite | Who reads |
| --- | --- | --- | --- | --- |
| `dclab_session` | yes | yes | Lax | browser → BFF only |
| `dclab_csrf` | no | yes | Lax | page JS, header echo |

## Failure behavior

| Case | Result |
| --- | --- |
| Missing/wrong CSRF or untrusted Origin | 403, no mutation |
| Login throttle exceeded | 429 `"too many attempts"` |
| Bad/disabled credentials | 401 `"invalid email or password"` |
| Suspended workspace membership | 403 on that workspace; session may still be valid |
| Recovery confirm unknown/expired | 400 `"invalid or expired token"` |
| Production email-delivery flag on | process does not boot |
| Production JWT default / non-Secure cookies | process does not boot (ADR 0001) |

## Alternatives considered

1. **Synchronizer token stored on `auth_sessions`.** Extra column; HMAC of the
   already-secret session value is equivalent and rotates with the session.
2. **SameSite=Strict.** Breaks the BFF top-level login redirect more harshly
   than Lax; Origin + CSRF already cover cross-site POST.
3. **Return reset tokens in development JSON.** Convenient and an accidental
   production leak. Rejected.

## Consequences

- Browser and Playwright `page.request` mutations must send `X-CSRF-Token`
  and a trusted `Origin`.
- Alembic `0056_auth_hardening` is additive identity-plane state. S0-P02C
  `0059_auth_session_constraints` adds same-user rotation lineage, hash CHECKs,
  expiry/revocation indexes, and bounded cleanup. Current head is named by
  [`truth_baseline.json`](../../contracts/truth_baseline.json).
- Full SMTP / IdP is **deferred**. Recovery is hook + hashed row only.
- Durable cleanup uses the existing `ml_jobs` handler registry
  (`auth.session_cleanup`); it does not introduce Redis.
- S0-P02E adds `AUTH_BROWSER_SESSIONS_ENABLED`, `AUTH_TOKEN_HASH_SECRET`,
  `AUTH_CSRF_SECRET`, bounded `auth_event family=… reason=…` metrics, and
  incident runbooks under `docs/runbooks/`.

## Rollback

`alembic downgrade 0055_auth_sessions`. Revert this tree. Existing sessions
remain valid under ADR 0001 CSRF-off behavior only after rollback.
