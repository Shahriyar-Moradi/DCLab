# S0-P02D browser/BFF contract completion

**Status:** CURRENT  
**Plan/prompt:** S0-P02D  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**Depends on:** [ADR 0001](../adr/0001-browser-session-bff.md),
[ADR 0002](../adr/0002-session-csrf-csp-abuse.md)

The Next.js `/api/backend/*` route is a cookie-copying reverse proxy, not an
authorization layer. Browser JavaScript does not parse bearers, read the
HttpOnly session cookie, or treat `/auth/me` role as API authority.

## Contract

| Concern | Behavior |
| --- | --- |
| Cookies | Copy `dclab_session` (HttpOnly) and `dclab_csrf` (readable) from FastAPI `Set-Cookie`. Names follow `DCLAB_SESSION_COOKIE` / `DCLAB_CSRF_COOKIE`. |
| Authorization | Never forwarded. `POST /auth/tokens` through the BFF still returns a JWT; attaching it on a later BFF call does not authenticate. |
| Request ID | Generate `X-Request-Id` when missing; forward and echo. |
| Cache | `Cache-Control: no-store` on BFF responses. |
| Upload | Reject when `Content-Length` exceeds `DCLAB_BFF_MAX_UPLOAD_BYTES` (default 256 MiB); otherwise stream (`duplex: half`) or buffer with the same cap. |
| Download | Stream the upstream body. Reject when declared `Content-Length` exceeds `DCLAB_BFF_MAX_DOWNLOAD_BYTES` (default 512 MiB). |
| Upstream down | HTTP 502 `{"detail":"backend unavailable"}`. No body or credential logs. |
| Methods | GET, HEAD, POST, PUT, PATCH, DELETE. |
| Middleware | Session probe via `GET /auth/me`. Area 403 HTML is presentation; FastAPI authorizes. |

## Evidence

```text
Plan/prompt ID: S0-P02D
Claim: The BFF forwards cookies, CSRF, workspace, and request IDs without becoming an authorization layer; anonymous/invalid sessions are 401; unreachable API is 502; JavaScript has no bearer or session-cookie helpers
Status: VERIFIED (API + source + tsc; local Playwright NOT_TESTED this prompt)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: none (no Alembic change)
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.generate_truth_artifacts
  .venv/bin/python -m scripts.check_truth_drift
  ./apps/web/node_modules/.bin/tsc --noEmit -p apps/web/tsconfig.json
Expected result: BFF source contract; no jose; kill switch does not apply to this prompt; drift clean; tsc clean
Observed result: 1076 passed, 3 skipped, 20 warnings, 577.90s; tsc clean; drift clean after inventory refresh; local Playwright NOT_TESTED
Artifact/log/dashboard link: docs/adr/0001-browser-session-bff.md
Security and tenant checks: BFF does not inspect role; API membership/capability remain the authority; auth tables stay identity-plane
Rollback/kill switch: revert the BFF route/helper; AUTH_BROWSER_SESSIONS_ENABLED is S0-P02E
Known limitations: unknown-length downloads are streamed without a byte counter; middleware area 403 is UX not API authz; local Playwright not run in this agent
Reviewer/date: S0-P02D / 2026-09-12
```

## Next prompt

**S0-P02E** is implemented in this same working tree. Plan 0.2 closes at
**S0-P02F** (adversarial completion gate), also recorded in this tree.
**S0-P03A** and **S0-P04A** already exist here.
