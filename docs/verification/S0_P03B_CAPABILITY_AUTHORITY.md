# S0-P03B central capability authority

**Status:** CURRENT — locally verified  
**Plan/prompt:** S0-P03B  
**Canonical mechanical facts:** [`truth_baseline.json`](../../contracts/truth_baseline.json)  
**Matrix inventory:** [`DCLAB_RBAC_CAPABILITY_MATRIX.md`](../DCLAB_RBAC_CAPABILITY_MATRIX.md)

The server resolves `workspace-capabilities.v1` from current platform and
workspace memberships, canonical roles, suspension, workspace kind, seat
entitlements and optional Business feature flags. `GET /v1/me` and
`GET /auth/me` return the version and effective booleans as display hints.
The BFF and browser never treat that response as an authorization token.
Routes and resource services continue checking current database authority.

The cache is transaction-local and copies results to callers. Commit/rollback
ends reuse. An ORM flush of membership, user, workspace, entitlement or flag
invalidates same-transaction results. A missing optional Business flag is
false for modern Business members. The legacy `client_user` retains its
existing application compatibility but cannot enter Business administration.

Verification observed on the 2026-09-22 working tree:

```text
.venv/bin/pytest -q apps/api/tests/test_central_capability_authority.py
  6 passed; one existing Starlette deprecation warning
.venv/bin/pytest -q apps/api/tests/test_central_capability_authority.py apps/api/tests/test_browser_hardening.py apps/api/tests/test_api.py
  47 passed before the final legacy-client regression case was added
cd apps/web && npm run build
  production build passed; three existing model-build hook warnings
cd apps/web && npx playwright test e2e/whole-system.spec.ts --grep "client_user can use app routes"
  1 passed using the disposable local E2E database and isolated ports
```

The tests cover a stale JWT role after promotion, suspended membership,
same-transaction role/suspension and entitlement/flag changes, and a forged
direct Business API call despite hidden navigation. No migration or external
provider side effect was introduced. Rollback is a code revert of the matrix
projection and consumers; existing membership and feature rows are unchanged.

This is local evidence. Exact-SHA CI has not run for the uncommitted working
tree.
