# S0-P02B session, CSRF, CSP, and abuse verification

**Status:** CURRENT  
**Plan/prompt:** S0-P02B  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**ADR:** [0002-session-csrf-csp-abuse.md](../adr/0002-session-csrf-csp-abuse.md)

Hardens the S0-P02A HttpOnly BFF session: CSRF + trusted Origin on cookie
mutations, API and Next security headers/CSP, login throttling, uniform
credential errors, concurrent-session cap, membership `suspended_at`, and
hashed password-reset / email-verification hooks without SMTP.

## Evidence

### 2026-09-11 local browser re-verification

Cookie continuity, reload persistence, logout, logout-all, and CSRF rejection
all pass through the real browser/BFF path (5/5 focused). The complete browser
acceptance suite passes 18/18, including role-aware routing, capability
fail-closed behavior, and tenant substitution rejection. The full backend/SDK
regression passes 1,031 tests with one live-OpenAI skip.

The evidence block below preserves the original prompt measurement; its
mechanical counts are historical, not a second CURRENT inventory.

```text
Plan/prompt ID: S0-P02B
Claim: Cookie-authenticated mutations require CSRF and a trusted origin; production refuses email-delivery without a provider; recovery tokens are hashed and never returned
Status: VERIFIED (API + source + drift + local browser E2E)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: alembic upgrade head through 0058_simulation_workspace; historical Alembic catalogs
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.check_truth_drift
  .venv/bin/python -m scripts.check_truth_drift --alembic-check
  cd apps/web && npx tsc --noEmit
Expected result: hardening tests green; drift clean at 0057 / 66 tables
Observed result: 1003 passed, 3 skipped, 21 warnings, 565.49s; drift all [clean] including alembic_metadata; tsc and lint succeeded (3 pre-existing hook warnings)
Artifact/log/dashboard link: docs/adr/0002-session-csrf-csp-abuse.md
Security and tenant checks: CSRF HMAC of session secret; recovery and session hashes only; auth_recovery_tokens is identity-plane; suspended membership does not fall through to legacy client_user
Rollback/kill switch: alembic downgrade to previous session revision; AUTH_EMAIL_DELIVERY_ENABLED cannot be true
Known limitations: no SMTP/IdP; in-process throttle; Next CSP allows 'unsafe-inline'; API JWT still not revocable except user disable; UA hash stored but not enforced
Reviewer/date: S0-P02B / 2026-09-10
```

## Contracts (intentional, additive)

- `GET /auth/csrf`
- `POST /auth/logout-all`
- `POST /auth/password-reset/request` and `/confirm`
- `POST /auth/email-verification/request` and `/confirm`
- Table `auth_recovery_tokens`; columns `users.email_verified_at`,
  `workspace_memberships.suspended_at`
- `UserRead.email_verified_at` optional

Browser mutations must send `X-CSRF-Token` and a trusted `Origin`.
`POST /auth/tokens` remains CSRF-exempt (API bearer).

## Next prompt

**S0-P02C** was the persistence/cleanup reconciliation gate and is recorded
separately. **S0-P02D** / **S0-P02E** complete the BFF contract and operations
controls. Plan 0.2 closes at **S0-P02F**.
