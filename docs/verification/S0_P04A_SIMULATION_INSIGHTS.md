# S0-P04A SimulationRun and Insights tenancy

**Status:** CURRENT  
**Plan/prompt:** S0-P04A  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**ADR:** [0004-simulation-insights-tenancy.md](../adr/0004-simulation-insights-tenancy.md)

Tenant-scope `simulation_runs` with an honest empty backfill. Historical rows
stay unowned and are denied on customer Insights and workspace-scoped admin
derivatives. New admin runs persist the resolved workspace. The Python client
does not grow an insights/simulations API.

## Evidence

### 2026-09-11 local browser re-verification

The complete browser acceptance suite passed 18/18 against a fresh database at
the canonical Alembic head, including workspace Insights payloads, capability
fail-closed behavior, and cross-tenant identifier rejection. The full
backend/SDK regression passed 1,031 tests with one live-OpenAI skip.

The evidence block below preserves the original prompt measurement; its
mechanical counts are historical, not a second CURRENT inventory.

```text
Plan/prompt ID: S0-P04A
Claim: /app/insights and simulation derivatives cannot expose another tenant; unowned historical rows are not assigned to a default workspace
Status: VERIFIED (API + drift + tsc + local browser E2E)
Commit/image digest: uncommitted working tree on top of 49da76b
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: 0058_simulation_workspace; historical Alembic catalogs match
Commands:
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.generate_truth_artifacts
  cd apps/web && ./node_modules/.bin/tsc --noEmit && npm run lint
Expected result: two-workspace isolation; orphans 404; drift clean at 0058 / 66 tables / 167 ops
Observed result: 1025 passed, 3 skipped, 21 warnings, 615.09s; drift all [clean]; tsc clean; lint 3 pre-existing model-build hook warnings
Artifact/log/dashboard link: docs/adr/0004-simulation-insights-tenancy.md
Security and tenant checks: same use_case and subject_id across workspaces; cross-workspace run ids 404; unowned archive hidden
Rollback/kill switch: alembic downgrade to the previous session-workspace revision
Known limitations: experiment/client-trial rows on admin monitoring/registry remain global; S0-P03B capability matrix
Reviewer/date: S0-P04A / 2026-09-10
```

## Contracts (intentional, additive)

- Columns `simulation_runs.workspace_id`, `simulation_runs.project_id` (nullable)
- Composite FK to `projects(workspace_id, id)` with column-specific SET NULL
- Additive `SimulationRunRead.workspace_id` / `project_id`
- Optional `project_id` on `POST /admin/simulations/run`
- `GET /v1` unchanged

Rollback: `alembic downgrade 0057_session_workspace`. Revert this tree.

## Next prompt

**S0-P04B** — close capability and audience leakage.
