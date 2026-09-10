# ADR 0004 — SimulationRun and Insights tenancy (S0-P04A)

**Status:** Accepted  
**Date:** 2026-09-10  
**Prompt:** S0-P04A  
**Depends on:** [0003-workspace-selection.md](0003-workspace-selection.md)

## Inventory (creation, read, translation, CLI, seed, UI)

| Path | Role today | Tenant signal |
| --- | --- | --- |
| `POST /admin/simulations/run` (`api/simulations.py`, `sim/runner.py`) | Platform admin creates `SimulationRun` from the canned eight-problem pack | None |
| `GET /admin/simulations/runs`, `GET .../runs/{id}`, `GET .../decisions/{external_id}` | Raw admin read of persisted payloads | Global `db.get` |
| `GET /app/insights` (`insight_query.list_client_insights`) | Customer translation of **latest global** run per `use_case` | Membership guard only; query unscoped |
| `admin_model_registry_service._simulation_models` | Platform model registry derivative | Global |
| `admin_monitoring_service.list_retrain_events` | Platform monitoring derivative | Global |
| Client Labs (`client_lab_service`, `client_lab_runs`) | Separate workspace-scoped trial path; **does not write `simulation_runs`** | `workspace_id` already required |
| Labs CSV auto-train (`client_upload_insights`) | Translates `Experiment`, not `SimulationRun` | Workspace on upload |
| Internal CLI (`app/cli/main.py`) | dataset/task/experiment/user seed; **no SimulationRun command** | n/a |
| Seeds | Tests and `POST /admin/simulations/run` only; E2E seed does not insert simulation rows | n/a |
| UI `/app/insights`, nav, `useInsights` | Customer production Insights page | Query key workspace-prefixed (S0-P03A); API was global |
| Marketing `/app/insights` link | Public marketing card | Not a data plane |
| `packages/dclab_client` | `/v1` only; no insights/simulations | Explicit constructor workspace; must not inherit this table |

No `user_id`, `workspace_id`, `project_id`, or other ownership column existed on `simulation_runs`. Rows cannot be attributed.

## Decision

**Tenant-scope new writes; honest empty backfill; deny unowned historical rows.**

This is the migrate option in S0-P04A, not “assign to default workspace” and not “delete the archive.”

1. **Expand** — nullable `workspace_id` and `project_id` on `simulation_runs`, unique `(workspace_id, id)`, composite FK `(workspace_id, project_id) → projects(workspace_id, id)` with column-specific `ON DELETE SET NULL (project_id)`, workspace FK `ON DELETE NO ACTION` (restrict), CHECK `project_id IS NULL OR workspace_id IS NOT NULL`.
2. **Backfill** — **zero rows.** There is no provable owner. Do not write `DEFAULT_WORKSPACE_ID` or any other convenient tenant.
3. **Validate** — customer `/app/insights` and workspace-scoped admin simulation/registry/monitoring **simulation** reads require `workspace_id = current workspace`. `workspace_id IS NULL` is archive, not a catalog.
4. **Non-null on insert** — application-enforced: `POST /admin/simulations/run` persists the resolved workspace (and optional project if it belongs to that workspace). Table-level `NOT NULL` on `workspace_id` is deferred while orphans exist.
5. **Delete** — deleting a workspace that still has scoped runs fails (restrict). Deleting a project clears `project_id` only and keeps `workspace_id`. Unowned archive rows are not deleted by this prompt.

`/app/insights` stays in customer navigation because the page is honest once it is workspace-filtered (empty until that workspace has a scoped run). Removing the route would hide a legitimate empty state. Unowned historical rows are **retired from serving**, not assigned and not dropped.

Unauthorized **selector** remains 403 (existing workspace resolver). Missing or cross-workspace **run ids** remain **404** (tenant-hiding). Ambiguous/unowned rows use the same 404 on admin get-by-id.

## Alternatives rejected

1. **Assign orphans to `DEFAULT_WORKSPACE_ID`.** Forbidden by the prompt; not provable ownership.
2. **Remove `/app/insights` from production navigation and leave the table global.** Would stop the customer leak but leave admin derivatives as a global dump and skip composite lineage for new writes.
3. **Table-level `NOT NULL` in this revision.** Would force a fake backfill or a delete of historical evidence.

## Consequences

- Alembic `0058_simulation_workspace` is additive expand; downgrade drops the new columns/constraints.
- Empty Insights is the correct customer view until an admin re-runs simulations into that workspace.
- Platform monitoring/registry still list experiments and client-trial audits globally; only **SimulationRun** rows in those lists are workspace-filtered. Broader raw-surface closure is S0-P04B.
- Capability-matrix / JWT-role UI routing remains S0-P03B.

## Rollback

`alembic downgrade 0057_session_workspace`. Revert this tree. Historical `simulation_runs` rows remain; the global-latest leak would return.
