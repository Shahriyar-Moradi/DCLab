# S0-P02A browser sessions / BFF

**Status:** CURRENT  
**Plan/prompt:** S0-P02A  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**ADR:** [0001-browser-session-bff.md](../adr/0001-browser-session-bff.md)

Replaces the JavaScript-readable `dclab_token` bearer cookie with an opaque
HttpOnly `dclab_session` issued by FastAPI, copied onto the Next.js origin by
`/api/backend/*`, and stored as `SHA-256(token)` in `auth_sessions`.

API / non-browser clients use `POST /auth/tokens` (JWT, no session cookie).
Authorization loads `User` from PostgreSQL; JWT `role` is not used.

## Evidence

### 2026-09-11 S0-P02A repair verification

The accepted ADR and live session, authentication, dependency, migration,
configuration, BFF, frontend-session, and browser-test paths were inspected
before editing. The product implementation already satisfied S0-P02A, so this
repair did not add a second session mechanism, change an API, or add a
migration. It strengthened the PostgreSQL proof that rotation revokes the
predecessor and links the successor through `rotated_from_id`, added a safe
unknown-session rejection check, and corrected stale BFF setup documentation.

Observed on a fresh PostgreSQL database migrated through the canonical head
named by [`truth_baseline.json`](../../contracts/truth_baseline.json):

- `pytest apps/api/tests/test_browser_sessions.py apps/api/tests/test_browser_hardening.py -q --tb=short`: **41 passed**, one dependency deprecation warning, 23.81s.

Historical measurement used Alembic `0058_simulation_workspace` as head before
S0-P02C added session-constraint reconciliation.
- `npx tsc --noEmit`: passed with no diagnostics.
- `npm run lint`: passed with three pre-existing React hook warnings.
- `playwright test e2e/session-security.spec.ts`: **5 passed**, 25.5s; its web server performed a successful production build before the tests.
- `scripts.generate_truth_artifacts --verify-idempotent`: two generations were byte-identical.
- `scripts.generate_truth_artifacts --check` and `scripts.check_truth_drift`: all detectors clean.

The broader exact-SHA baseline and complete regression evidence remains in
[S0-P01D](S0_P01D_BASELINE_GATE.md); this prompt changed only tests,
documentation, and generated truth facts on top of that verified source.

The exercised state contract is: an active row has no `revoked_at` and is
inside both expiry bounds; logout/rotation sets `revoked_at`; and an idle- or
absolute-expired row is rejected. Only the hash is stored. Browser cookies are
HttpOnly with explicit SameSite/path and deployed-mode Secure; browser code
uses `/api/backend/*`, while `POST /auth/tokens` remains the explicit
non-browser bearer flow.

The evidence block below preserves the original prompt measurement; its
mechanical counts are historical, not a second CURRENT inventory.

```text
Plan/prompt ID: S0-P02A
Claim: Browser no longer reads/decodes/attaches a long-lived bearer; sessions are hashed, HttpOnly, rotatable, and revocable; production boot fails closed
Status: VERIFIED (API + source + drift + local browser E2E)
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

**S0-P02C** — persistence, constraints, and cleanup reconciliation. S0-P02B is
already recorded as verified.
