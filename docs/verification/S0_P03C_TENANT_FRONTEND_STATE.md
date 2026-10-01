# S0-P03C tenant-aware frontend state and navigation

**Status:** CURRENT — locally verified  
**Plan/prompt:** S0-P03C  
**Canonical mechanical facts:** [`truth_baseline.json`](../../contracts/truth_baseline.json)

React Query resource keys are prefixed with the active workspace ID. Workspace
change aborts the previous request generation, cancels and removes tenant
queries, clears mutation state, hides the previous main content, and loads the
server-confirmed selected workspace and its capability map. The selector
announces success or failure with a live region. A switch keeps only a safe
collection route; an object detail route returns to a capability-allowed home.
The server checks membership on every tenant request and remains the denial
authority.

Verification observed on the 2026-09-22 working tree:

```text
cd apps/web && npm run test:components
  5 passed, including zero/one/many selector presentations and request abort
cd apps/web && ./node_modules/.bin/tsc --noEmit
  passed
cd apps/web && npm run lint
  passed with three existing model-build hook warnings
cd apps/web && npm run build
  passed with the same warnings
cd apps/web && npx playwright test e2e/session-security.spec.ts
  13 passed using the disposable local E2E database and isolated ports
```

The browser race test delays an old-workspace opportunity response while
switching to another workspace. Only the new workspace sentinel appears,
including after the delayed response would have arrived. The browser test also
checks the accessible workspace-change announcement. Auth identity requests
are independent of tenant cancellation, so `/auth/me` cannot be aborted by a
workspace transition. Login and logout passed through the HttpOnly BFF path.

No database migration was required. Rollback is a code revert of the browser
cache, routing and presentation changes. Exact-SHA CI has not run for the
uncommitted working tree.
