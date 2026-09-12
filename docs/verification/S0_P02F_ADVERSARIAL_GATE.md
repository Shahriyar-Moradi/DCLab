# S0-P02F adversarial completion gate

**Status:** CURRENT  
**Plan/prompt:** S0-P02F  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**Depends on:** [ADR 0001](../adr/0001-browser-session-bff.md),
[ADR 0002](../adr/0002-session-csrf-csp-abuse.md)

Plan 0.2 closes with a two-user/two-workspace PostgreSQL matrix plus browser
assertions. No new auth stack, migration, or cookie name. Failures in this
prompt repair Plan 0.2 defects only.

## Matrix

| Case | Result |
| --- | --- |
| Fixation (forged cookie and planted foreign cookie) | New session issued; planted cookie revoked; `rotated_from_id` stays null across users |
| Theft / replay after rotation | Stolen `X-DCLab-Session` authenticates as the victim until the same cookie is presented at login; then 401 |
| Two-workspace tenancy | Victim cookie + `X-Workspace-Id` of the other owner's workspace is 403 `not authorized` |
| CSRF | Trusted Origin wins; evil Origin is 403 even with a trusted Host |
| Origin / Host confusion | `Host` and `X-Forwarded-Host` are not substitutes for Origin; missing Origin + evil Referer is 403 |
| XSS token access | `session.ts` never reads `document.cookie`; Playwright expects HttpOnly `dclab_session` absent from `document.cookie` |
| Concurrent logout | Two users' tokens revoke together without error |
| Password / reset enumeration | Unknown and wrong password are both 401 `invalid email or password`; reset request is 204 empty for known and unknown |
| Expired cleanup | Stale identity-plane row deletes; the other user's live session remains |
| Server restart | New TestClient + same database still authenticates the cookie; bearer works with cookies cleared |
| Bearer without cookies | `POST /auth/tokens` then `Authorization: Bearer` is 200 after `cookies.clear()` |
| Kill switch + theft runbook (production-shaped) | `DCLAB_ENV=production` with non-default JWT/hash/CSRF, Secure cookies, CORS; dual-verify of unkeyed SHA-256; `logout-all`; kill switch 503/401; tokens stay 200; email delivery remains false |

Commandable owner: `apps/api/tests/test_session_adversarial_gate.py`. Browser extras:
`apps/web/e2e/session-security.spec.ts` (`S0-P02F adversarial completion`).

## Evidence

```text
Plan/prompt ID: S0-P02F
Claim: Two-user/two-workspace session matrix holds; API bearers work without cookies; kill switch and theft runbook hold under production-shaped settings
Status: VERIFIED (API PostgreSQL matrix; local Playwright NOT_TESTED this prompt)
Commit/image digest: uncommitted working tree on top of 91986b9
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: none (no Alembic change)
Commands:
  .venv/bin/pytest -q --tb=line apps/api/tests/test_session_adversarial_gate.py
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.generate_truth_artifacts
  .venv/bin/python -m scripts.check_truth_drift
  ./apps/web/node_modules/.bin/tsc --noEmit -p apps/web/tsconfig.json
Expected result: matrix 9 passed; full pytest green after artifact refresh; drift clean; tsc clean; no Plan 0.2 product repair required
Observed result: adversarial matrix 9 passed in 14.42s; Plan 0.2 session files 91 passed in 57.77s; complete local pytest 1084 passed, 1 failed (truth drift before artifact refresh), 3 skipped, 20 warnings, 694.75s; after generate_truth_artifacts, check_truth_drift all [clean] and test_truth_drift.py 27 passed in 7.80s (equivalent clean gate 1085 passed / 3 skipped); tsc clean; local Playwright NOT_TESTED
Artifact/log/dashboard link: docs/runbooks/session-theft.md
Security and tenant checks: stolen cookie is the victim until rotation or logout-all; foreign X-Workspace-Id is 403; Host is not Origin; auth tables stay identity-plane
Rollback/kill switch: AUTH_BROWSER_SESSIONS_ENABLED=false; revert tests/docs only (no schema)
Known limitations: login throttle is still in-process; SMTP/IdP absent; local Playwright not run in this agent; TestClient is HTTP so production Secure cookies are exercised via X-DCLab-Session and explicit CSRF Cookie headers
Reviewer/date: S0-P02F / 2026-09-12
```

## Next prompt

**S0-P03B** — central capability authority. **S0-P03A** and **S0-P04A** already
exist in this working tree. Do not start them as new work from this prompt.
