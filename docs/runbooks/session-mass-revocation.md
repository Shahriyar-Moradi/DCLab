# Mass session revocation

**Prompt:** S0-P02E  
**API:** `POST /auth/logout-all` revokes every active session for the
authenticated user and clears cookies. CSRF + trusted Origin required for
cookie clients.

## One user

Prefer `POST /auth/logout-all` (browser BFF or bearer). Privilege changes
already revoke that user's sessions.

## Every browser session

Use the kill switch first if theft is ongoing (`AUTH_BROWSER_SESSIONS_ENABLED=false`).
Then, on a disposable operator connection to the same database:

```sql
UPDATE auth_sessions
SET revoked_at = NOW()
WHERE revoked_at IS NULL;
```

Do not print `token_hash` in psql output you will paste. Hashes are not raw
cookies, but they are still secrets-at-rest.

Restart API processes if you also rotated hashing secrets. Users sign in
again; `POST /auth/tokens` is unchanged.

## Cleanup

Queue `auth.session_cleanup` after revocation so retained rows age out under
`SESSION_RETENTION_DAYS`. See `session-cleanup.md`.

## Audit

The application emits `auth_event family=session reason=logout_all` or
`kill_switch` without user or session identifiers.
