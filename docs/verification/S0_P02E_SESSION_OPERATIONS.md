# S0-P02E session operations, configuration, and incident controls

**Status:** CURRENT  
**Plan/prompt:** S0-P02E  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**Depends on:** [ADR 0001](../adr/0001-browser-session-bff.md),
[ADR 0002](../adr/0002-session-csrf-csp-abuse.md)

Typed settings cover cookie name/security, idle and absolute TTL, CSRF names,
trusted origins, session/recovery hashing secrets, throttle bounds, and the
emergency browser-session kill switch. Production boot fails closed. Login,
session, CSRF, and recovery events are bounded `family` + `reason` codes.
CI keeps hashing secrets empty (unkeyed SHA-256).

## Settings

Documented in [`.env.example`](../../.env.example). Production requires
`JWT_SECRET`, `AUTH_TOKEN_HASH_SECRET`, and `AUTH_CSRF_SECRET` that are not the
development default, plus Secure cookies and at least one `CORS_ORIGINS` entry.

`AUTH_BROWSER_SESSIONS_ENABLED=false` returns 503 on browser login/register and
401 on cookie/`X-DCLab-Session` lookup. `POST /auth/tokens` keeps working.

## Runbooks

- [session-cleanup.md](../runbooks/session-cleanup.md)
- [session-theft.md](../runbooks/session-theft.md)
- [auth-secret-rotation.md](../runbooks/auth-secret-rotation.md)
- [session-mass-revocation.md](../runbooks/session-mass-revocation.md)

## Evidence

```text
Plan/prompt ID: S0-P02E
Claim: Production boot requires hash/CSRF secrets; CI does not; kill switch disables cookies not bearer; metrics/logs are reason codes only
Status: VERIFIED (API + config + redaction; local Playwright NOT_TESTED this prompt)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: none (no Alembic change)
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.check_truth_drift
Expected result: production rejects empty hash/CSRF secrets; CI default hashes remain SHA-256; kill switch 503/401; no emails or tokens in auth_event logs
Observed result: 1076 passed, 3 skipped, 20 warnings, 577.90s; drift clean after inventory refresh; local Playwright NOT_TESTED
Artifact/log/dashboard link: docs/runbooks/session-theft.md
Security and tenant checks: auth_sessions/auth_recovery_tokens remain identity-plane; dual-verify HMAC + unkeyed SHA-256; no production secrets in CI
Rollback/kill switch: AUTH_BROWSER_SESSIONS_ENABLED=false; revert config defaults; alembic unchanged
Known limitations: login throttle is still in-process; SMTP/IdP absent; HMAC previous-secret window is lookup-only
Reviewer/date: S0-P02E / 2026-09-12
```

## Next prompt

**S0-P03B** — central capability authority. Plan 0.2 is complete in this
working tree. **S0-P03A** and **S0-P04A** already exist here.
