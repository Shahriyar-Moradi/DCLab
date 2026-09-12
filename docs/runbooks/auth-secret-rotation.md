# Auth secret rotation

**Prompt:** S0-P02E  
**Secrets:** `JWT_SECRET`, `AUTH_TOKEN_HASH_SECRET`, `AUTH_TOKEN_HASH_SECRET_PREVIOUS`,
`AUTH_CSRF_SECRET`. CI must keep hash/CSRF secrets **empty** so tests use
unkeyed SHA-256 and the development JWT default.

## Browser session / recovery hashes

Stored values are 64-char lowercase hex. Production stores
`HMAC-SHA256(AUTH_TOKEN_HASH_SECRET, raw)`. Lookups also accept:

- HMAC with `AUTH_TOKEN_HASH_SECRET_PREVIOUS`
- unkeyed `SHA-256(raw)` from pre-HMAC rows

### Roll HMAC in

1. Set `AUTH_TOKEN_HASH_SECRET` to a new random value. Leave previous empty.
2. Restart API. Existing cookies still authenticate (unkeyed candidate).
3. New logins store HMAC. Old rows remain until logout, expiry, or cleanup.

### Replace an HMAC secret

1. Copy the current `AUTH_TOKEN_HASH_SECRET` to `AUTH_TOKEN_HASH_SECRET_PREVIOUS`.
2. Set a new `AUTH_TOKEN_HASH_SECRET`.
3. Restart API. Dual-verify covers both HMACs plus unkeyed SHA-256.
4. After idle+absolute TTL (defaults 12h / 7d) plus retention, clear
   `AUTH_TOKEN_HASH_SECRET_PREVIOUS`.
5. Optionally mass-revoke (`session-mass-revocation.md`) to force re-login now.

## CSRF HMAC

`AUTH_CSRF_SECRET` (required in production; development falls back to
`JWT_SECRET`). Rotation invalidates `dclab_csrf` until `GET /auth/csrf` or a
new login. There is no previous-CSRF dual-verify; users refresh the page.

## JWT (`POST /auth/tokens`)

Bearer tokens are not session rows. Rotating `JWT_SECRET` invalidates every
API JWT immediately. Browser cookies are unaffected if hash secrets are
unchanged.

## Production boot

`DCLAB_ENV=production` refuses to start when JWT, hash, or CSRF secrets are
missing or equal to the development default, or when cookies are not Secure.
Never commit production values. `.env.example` documents names only.
