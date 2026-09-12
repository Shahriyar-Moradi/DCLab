# S0-P02C session persistence, constraints, and cleanup

**Status:** CURRENT  
**Plan/prompt:** S0-P02C  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**Depends on:** [ADR 0001](../adr/0001-browser-session-bff.md),
[ADR 0002](../adr/0002-session-csrf-csp-abuse.md)

Reconciles hashed `auth_sessions` / `auth_recovery_tokens`: unique SHA-256
hex, same-user rotation lineage, indexed expiry/revocation, bounded
`FOR UPDATE SKIP LOCKED` cleanup, and an idempotent `auth.session_cleanup`
worker handler. No Redis. Email delivery stays disabled.

## Evidence

Review found S0-P02B behavior already present (CSRF, CSP, throttle, recovery
hooks). This prompt added persistence constraints and durable cleanup, plus
the remaining adversarial P02B tests (fixation, CSRF HMAC of the header
session, Referer origin, trusted `X-Forwarded-For`, HSTS).

The evidence block below records the measured gate. Historical previous-head
IDs used in the migration path sit after this opening.

## Contracts (intentional, additive)

- Unique `(id, user_id)` and composite `fk_auth_sessions_rotated_from_user`
  with column-specific `ON DELETE SET NULL (rotated_from_id)`
- CHECK token hashes are 64-char lowercase hex; rotation cannot point at self
- Indexes on `absolute_expires_at`, partial `revoked_at`, partial recovery
  `consumed_at`
- Worker handler `auth.session_cleanup` / job type `auth_cleanup`
- Settings `SESSION_CLEANUP_BATCH_SIZE`, `SESSION_CLEANUP_MAX_BATCHES`

```text
Plan/prompt ID: S0-P02C
Claim: Raw session/recovery secrets are never stored; hashes are unique; rotation cannot cross users; cleanup is bounded, race-safe, and available as a code-owned handler
Status: VERIFIED (API + source + drift; local Playwright NOT_TESTED this prompt)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: empty database and previous simulation-tenancy head through 0059_auth_session_constraints; historical Alembic catalogs
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.generate_truth_artifacts
  .venv/bin/python -m scripts.check_truth_drift
  cd apps/web && ./node_modules/.bin/tsc --noEmit
Expected result: unique hashes; cross-user rotation rejected; bounded cleanup; empty and previous-head upgrades; drift clean at 0059 / 66 tables
Observed result: 1057 passed, 3 skipped, 20 warnings, 1635.95s; drift all [clean]; tsc clean; local Playwright NOT_TESTED
Artifact/log/dashboard link: docs/adr/0002-session-csrf-csp-abuse.md
Security and tenant checks: composite FK SET NULL (rotated_from_id) only; auth tables remain identity-plane; cleanup payload is empty
Rollback/kill switch: alembic downgrade to the previous simulation-tenancy revision; opportunistic cleanup still runs on issue/lookup
Known limitations: in-process login throttle; SMTP/IdP still absent; UA hash stored but not enforced; ml_jobs.workspace_id is required queue plumbing and is ignored by the cleanup handler
Reviewer/date: S0-P02C / 2026-09-12
```

## Next prompt

**S0-P02D** and **S0-P02E** are recorded separately. Plan 0.2 closes at
**S0-P02F**. Workspace selection (**S0-P03A**) already exists in this working
tree.
