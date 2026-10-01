# S0-P03D API/client workspace propagation and concurrency

**Status:** CURRENT — locally verified; exact-SHA CI pending  
**Baseline:** `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4`; dirty Scope 0 working tree preserved  
**Database head at this gate:** `0059_auth_session_constraints`; no migration  
**Next prompt:** S0-P03E

## Implementation packet and boundary

The existing `api/deps.py` and `authorization_service.py` remain the single
membership authority. `workspace_selection_service.py` owns remembered browser
selection; the BFF only transports a selector. The browser API client owns
per-request context and cancellation. The Python SDK is HTTP-only and
constructor-scoped. No new persistence, job, feature flag, cloud adapter, LLM,
scientific authority or external side effect was introduced. AWS/GCP behavior
is identical because the change is HTTP and PostgreSQL membership policy.

OpenAPI inventory at this working tree: `/v1` 13 operations, `/app` 17,
`/business` 15, `/development` 1, `/workspaces` 31, `/admin` 76. The audited
file/event paths include `/app/opportunities/upload`, `/app/labs/uploads`,
`/app/labs/uploads/{upload_id}/predictions.csv`, `/v1/model-builds/{id}/events`,
`/workspaces/{workspace_id}/artifacts/{id}/download`, and the business
observatory incremental events. Tenant routes resolve the selected workspace
through the shared dependency; resource services continue to query with that
workspace. Platform-wide `/admin` inventories intentionally remain global to
authorized platform members and are not reclassified as customer tenant routes.

## Contract

| Situation | Result |
| --- | --- |
| Bearer tenant call lacks `X-Workspace-Id` | 400; no default workspace inferred |
| Blank, malformed or duplicate workspace header | 400 |
| Header names a workspace without current membership | 403 |
| Selected workspace is authorized, resource ID belongs elsewhere | 404 |
| Path workspace differs from selected/header workspace | 404 before resource lookup |
| Browser header differs from remembered session selection | Header wins for that request, after membership lookup; session remains unchanged |
| Membership is suspended or removed between requests | Next request is denied; no token, cookie, SDK or cache grants authority |

`/v1/me`, `/v1/workspaces` and `/auth/me` are identity/discovery calls, not
tenant resource reads. They do not require a bearer selector. A syntactically
valid but unauthorized selector on the identity read is not adopted: the
displayed effective workspace falls back to an authorized selection. Tenant
resource calls reject that same selector with 403.

The SDK permits unscoped identity/discovery (`/v1/me`, `/v1/workspaces`) but
requires a valid constructor `workspace_id` for workspace resources. The web
client captures the workspace ID and abort signal before any asynchronous CSRF
work; an old request cannot inherit the newly selected workspace after a tab
switch. The BFF does not forward browser `Authorization` or
`X-DCLab-Session`; a forged `X-Workspace-Id` still reaches the server only as
an untrusted selector. Upload, download and polling paths retain their
existing body, artifact and event semantics.

## Verification and operations

Tests cover two workspace headers with one browser session, immediate
membership suspension, bearer missing/invalid/duplicate/unauthorized values,
legacy upload denial, artifact/path conflict, SDK preflight and a browser BFF
spoof attempt. Existing request IDs and safe 400/403/404 errors are retained;
this prompt adds no new metric or alert. S0-P03E owns the isolation route
inventory, denial metrics, audit and operational gate. No feature flag weakens
membership checks. Rollback is a code revert of the explicit-bearer and
per-request-context changes after client coordination; no schema rollback or
object cleanup is needed. No clean-SHA CI is claimed for this dirty tree.

### Observed local gate (2026-09-23)

| Command / check | Observed result |
| --- | --- |
| `.venv/bin/pytest -q --tb=short --maxfail=3 apps/api/tests packages/dclab_client/tests` | 1,097 passed, 3 skipped, 20 warnings |
| `apps/web: ./node_modules/.bin/playwright test e2e/session-security.spec.ts` against a disposable PostgreSQL E2E database and local API/web servers | 14 passed, including BFF spoof denial and stale-switch race |
| `apps/web: npm run lint && npm run test:components && npm exec tsc -- --noEmit -p tsconfig.json` | Passed; 5 component tests; 3 pre-existing React hook lint warnings |
| `apps/web: npm run build` | Passed; 31 static pages generated; same hook warnings |
| `.venv/bin/python -m scripts.generate_truth_artifacts --check` and `--verify-idempotent` | Clean; two generations byte-identical |
| `.venv/bin/python -m scripts.check_truth_drift` and `git diff --check` | All detectors clean; no whitespace errors |

The disposable `dclab_e2e_verify_s0p03d_20260923` database was checked for
active connections, dropped after the browser suite, and confirmed absent;
the working application and test databases were not touched. The full Python
gate's warnings are from Starlette's deprecated portal alias,
SQLAlchemy's historical cyclic-table ordering, and expected scikit-learn
unknown-category transforms; none failed a test. The web build also warns
about the existing dual lockfiles/trace-root inference. Backend and web owners
should track these separately from S0-P03D. This is local evidence from a
dirty tree, not a clean-commit or deployment certification.
