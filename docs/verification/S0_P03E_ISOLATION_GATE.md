# S0-P03E workspace isolation and operational gate

**Status:** CURRENT — locally verified; exact-SHA CI pending  
**Baseline SHA:** `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4` (dirty Scope 0 worktree preserved)  
**Database head at this gate:** `0059_auth_session_constraints`; no migration  
**Owner:** API authorization / browser session and workspace-state maintainers  
**Runbook:** [`workspace-isolation-invalidation.md`](../runbooks/workspace-isolation-invalidation.md)

## Implementation packet and claim

This gate changes no product, scientific, API or database contract. The shared API dependencies remain the authority for current membership/capability checks. `/auth/workspace` remains a selector mutation, not an authorization grant. New low-cardinality, process-local `denial`/`selection` counters and structured safe audit records reuse the request ID and current server-validated UUIDs; no URL, raw selector, token, email or response body is logged. There are no jobs, cloud resources, new routes, feature flags or schema changes. AWS/GCP behavior is identical because this is provider-neutral HTTP/PostgreSQL logic.

The live OpenAPI inventory has 160 paths and 168 operations: `/v1` 13, `/app` 17, `/business` 15, `/development` 1, `/workspaces` 31, `/admin` 76, `/auth` 13 and `/health` 2 operations. The test derives the tenant GET route set from OpenAPI rather than a static list and asserts that a bearer without an explicit workspace cannot read any of them. Identity discovery, workspace creation and platform-wide admin inventories have their separately documented authority boundaries.

The two-workspace matrix covers separate project lists, cross-workspace project/resource/path 404, mutation/path mismatch 404, artifact download 404, event polling 404, malformed/unauthorized selector 400/403, and committed membership suspension taking effect on the next request while the other workspace remains accessible. Existing S0-P03B tests cover same-transaction flush invalidation for role, suspension, entitlement and feature rows. Browser tests cover the slow concurrent workspace switch and forged BFF selector. Safe audit records are correlated by request ID, while metric keys contain only fixed reason codes.

## Observed local evidence (2026-09-23)

| Check | Observed result |
| --- | --- |
| Disposable PostgreSQL 15 cluster, E2E seed `--recreate` to migration head | Succeeded; isolated port 55433 and guarded E2E database |
| `.venv/bin/pytest -q --tb=short apps/api/tests/test_workspace_isolation_gate.py apps/api/tests/test_central_capability_authority.py apps/api/tests/test_workspace_selection.py apps/api/tests/test_access_control.py` | 239 passed, 1 existing Starlette deprecation warning, 224.92 s |
| `apps/web: ./node_modules/.bin/playwright test e2e/session-security.spec.ts` with disposable database and isolated API/web ports | 14 passed, 2.4 min; includes slow-switch no-flash and BFF selector spoof denial |
| `DEVELOPER_DIR=/Library/Developer/CommandLineTools DATABASE_URL=<disposable PostgreSQL 16 URL> .venv/bin/pytest -q --tb=short --maxfail=1 apps/api/tests packages/dclab_client/tests` | **1,102 passed, 3 skipped, 20 warnings in 496.11 s** |
| `.venv/bin/python -m scripts.generate_truth_artifacts --check`, `--verify-idempotent`, `.venv/bin/python -m scripts.check_truth_drift` | Clean; two generations byte-identical |

The first local PostgreSQL invocation was blocked by the filesystem/network sandbox, not by application tests; it was rerun with local-loopback permission. One initial test case used a nonexistent POST route and received 405; that test was corrected to the registered Business POST route, after which the focused gate passed. The complete regression suite initially exposed an order-dependent log-capture assertion in the new test; capture was moved to the logger call boundary. Its generated inventory was then refreshed. A PostgreSQL 15 `EXPLAIN` test selected a different valid index; the repository documents PostgreSQL 16 for local development, and the same index test plus the complete suite passed there. `/usr/bin/git` was blocked by the host's unaccepted Xcode license; setting `DEVELOPER_DIR` to the installed Command Line Tools resolved the Git-guard tests without changing repository code. E2E build emitted the existing Next.js dual-lockfile/trace-root warning; the synthetic E2E JWT secret emitted a short-key warning. None affected the supported-version final gate.

## Limitations and next gate

The counter helper is process-local and not a durable cross-worker exporter; production operations must aggregate the structured log until the planned observability exporter exists. In-flight work is not retroactively cancelled by a membership change; subsequent authorization boundaries must recheck. This is local evidence from a dirty working tree, not a clean-image or exact-SHA CI certification. Plan 0.3 should be considered locally verified, with exact-SHA CI/release closure pending.

Rollback is deployment/code rollback of the metrics/UI changes or removal of an unsafe workspace feature flag, never a fallback to global access. The runbook details containment. The next dependency-eligible prompt is **S0-P04A**, subject to the complete Plan 0.3 exact-SHA gate.
