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

### 2026-09-23 ADR and source reconciliation

The current checkout is based on `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4`
with unrelated in-progress changes. At the time of this gate, the migration head was `0059_auth_session_constraints`, beyond the original additive simulation
expand migration. The [ADR](../adr/0004-simulation-insights-tenancy.md) now
separates the pre-0058 global inventory from the as-built route/service/UI/SDK
inventory, records why historical ownership cannot be inferred, and defines
logical quarantine plus a fail-closed compatibility and rollback procedure.
This reconciliation changes documentation only. It does not claim a new
PostgreSQL or browser run; the results below remain dated observations.

Observed on this checkout:

- Observed then: `.venv/bin/alembic heads` → `0059_auth_session_constraints (head)`.
- Targeted `test_source_contract_keeps_workspace_filter_and_no_cli_or_sdk_surface`
  → 1 passed, 1 dependency deprecation warning.
- `python -m scripts.generate_truth_artifacts --verify-idempotent` → two
  byte-identical generations; `python -m scripts.check_truth_drift` → all
  detectors clean; `git diff --check` → clean.
- No new migration, API/SDK change, fixture mutation, cloud resource, feature
  flag, or product behavior change. S0-P04B/C still own broader raw-event and
  global admin-derivative closure; this ADR is not their isolation evidence.

The previous downgrade instruction in the historical block below is preserved
as evidence of the original review, **not current operational guidance**.
Dropping the workspace columns or serving a pre-tenant-aware binary could
restore global reads. Keep the additive schema and restore a verified
tenant-aware release, or disable affected routes while repairing forward.
The ADR is the current rollback authority.

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

Current rollback: retain the additive workspace columns and use the
[ADR's fail-closed procedure](../adr/0004-simulation-insights-tenancy.md#rollback-and-forward-repair).
The original downgrade instruction above is historical and is unsafe for live
customer traffic.

## Follow-up

**S0-P04B** has a separate [local enforcement record](S0_P04B_TENANT_LINEAGE.md).
The 2026-09-10 measurement above remains historical; raw-event audience
closure remains S0-P04C.
