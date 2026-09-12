# Suspected session theft

**Prompt:** S0-P02E  
**Cookie:** HttpOnly `dclab_session` (name configurable). JavaScript cannot read it.

A stolen cookie is a live session until idle/absolute expiry or revocation.
Treat UA hash as telemetry, not a binding.

## Immediate

1. Revoke the affected user's sessions: `POST /auth/logout-all` as that user,
   or from an operator session after identity is confirmed.
2. If the cookie may still be replayed across many accounts, enable the kill
   switch:

```bash
AUTH_BROWSER_SESSIONS_ENABLED=false
```

   Restart API processes. Browser login/register return 503. Existing cookies
   return 401. `POST /auth/tokens` keeps working for operators/scripts.
3. Rotate `AUTH_TOKEN_HASH_SECRET` and `AUTH_CSRF_SECRET` if you believe the
   hashing or CSRF HMAC key leaked. See `auth-secret-rotation.md`.
4. Keep `AUTH_EMAIL_DELIVERY_ENABLED=false` until a mail provider exists. Do
   not invent a reset-token JSON fallback.

## After containment

1. Turn the kill switch back on only after revocation/rotation.
2. Users sign in again. Dual-verify still accepts unkeyed SHA-256 rows until
   they expire or rotate on login.
3. Queue session cleanup (`session-cleanup.md`).

## What not to do

- Do not paste raw cookies, `Authorization` headers, or recovery tokens into
  tickets or logs.
- Do not grep logs for user ids; auth events are `family` + `reason` only.
- Do not add a second authorization layer in the Next.js BFF.
