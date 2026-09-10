# S0-P02A browser sessions / BFF

**Status:** CURRENT  
**Plan/prompt:** S0-P02A  
**Alembic head:** `0058_simulation_workspace`  
**ADR:** [0001-browser-session-bff.md](../adr/0001-browser-session-bff.md)

Replaces the JavaScript-readable `dclab_token` bearer cookie with an opaque
HttpOnly `dclab_session` issued by FastAPI, copied onto the Next.js origin by
`/api/backend/*`, and stored as `SHA-256(token)` in `auth_sessions`.

API / non-browser clients use `POST /auth/tokens` (JWT, no session cookie).
Authorization loads `User` from PostgreSQL; JWT `role` is not used.

## Evidence

```text
Plan/prompt ID: S0-P02A
Claim: Browser no longer reads/decodes/attaches a long-lived bearer; sessions are hashed, HttpOnly, rotatable, and revocable; production boot fails closed
Status: VERIFIED (API + source + drift); browser E2E NOT_TESTED locally (helpers updated for CI)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: alembic upgrade 0054 → 0055; pytest historical Alembic fresh and 0028→head catalogs
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.check_truth_drift
  .venv/bin/python -m scripts.check_truth_drift --alembic-check
  cd apps/web && npx tsc --noEmit && npm run lint
Expected result: session tests green; drift clean at 0055 / 65 tables / 160 ops; tsc clean
Observed result: 980 passed, 3 skipped, 21 warnings, 555.45s; drift all [clean] including alembic_metadata; tsc and lint succeeded (3 pre-existing hook warnings)
Artifact/log/dashboard link: docs/adr/0001-browser-session-bff.md
Security and tenant checks: hashed token_hash only; auth_sessions is identity-plane (user_id, not workspace_id); API still authorizes from DB User + membership
Rollback/kill switch: alembic downgrade 0054_execution_needs_input; revert this tree; production process refuses to boot on default JWT or non-Secure cookies
Known limitations: no CSRF/CSP/throttling (S0-P02B); API JWT not revocable except user disable; UA hash stored but not enforced; concurrent sessions allowed
Reviewer/date: S0-P02A / 2026-09-10
```

## Contracts (intentional)

- **Breaking:** `POST /auth/login` and `POST /auth/register` JSON no longer include `access_token`.
- **Additive:** `POST /auth/tokens`, `POST /auth/logout`, `GET /auth/session`; table `auth_sessions`.
- Snapshots refreshed in the same change (`contracts/`).

## Next prompt

**S0-P02B** — session, CSRF, CSP, and abuse verification.
