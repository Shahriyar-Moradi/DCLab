# ADR 0004 — SimulationRun and Insights tenancy (S0-P04A)

**Status:** Accepted  
**Date:** 2026-09-10  
**Prompt:** S0-P04A  
**Depends on:** [0003-workspace-selection.md](0003-workspace-selection.md)

## Pre-0058 inventory (creation, read, translation, CLI, seed, UI)

| Path | Role before 0058 | Tenant signal before 0058 |
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

No `user_id`, `workspace_id`, `project_id`, or other ownership column existed on pre-0058 `simulation_runs`. Those historical rows cannot be attributed from their stored fields.

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
- At the S0-P04A decision point, platform monitoring/registry still listed experiments and client-trial audits globally; S0-P04B scopes those derivatives without changing canonical models.
- Capability-matrix / JWT-role UI routing remains S0-P03B.

## S0-P04A as-built reconciliation (2026-09-23)

This section audited the decision against the 2026-09-23 checkout; the inventory above records the **pre-migration** problem. The later S0-P04B enforcement update below is authoritative for the admin derivatives. No new migration or product change was authorized by this ADR update. The live Alembic head was `0059_auth_session_constraints`; `0058_simulation_workspace` was already in the lineage.

| Current surface and caller | Decision | Evidence / remaining boundary |
| --- | --- | --- |
| `POST /admin/simulations/run` → `api/simulations.py` → `simulation_run_service.persist_simulation_run` → `sim/runner.py` | **Tenant-scope** each new run to the currently authorized workspace; optional `project_id` must belong to that workspace. | Admin router checks platform write; simulation router additionally checks workspace read. Service writes `workspace_id` and validates project before insert. `run_all` creates separate owned rows. No automatic inference from payload `use_case`, hero or external ID. |
| `GET /admin/simulations/runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/decisions/{external_id}` | **Tenant-scope** by selected workspace. A foreign or unowned run ID is 404. | `list_workspace_simulation_runs` filters `workspace_id`; `get_workspace_simulation_run` compares the row's owner before exposing payload or decision. Platform role grants access to a workspace only through current server selection, not a global run catalog. |
| `GET /app/insights` → `insight_query.latest_runs_by_use_case` and translation | **Tenant-scope**; keep the route and return an empty category list when no owned run exists. | Latest-run selection filters workspace before grouping by `use_case`. No historical null-owner row is translated; the route remains under `/app` workspace authorization. |
| `GET /admin/models` and `GET /admin/monitoring` simulation branches | **Tenant-scope** their `SimulationRun` rows. | `_simulation_models` and `list_retrain_events` filter those rows by workspace. Their *Experiment* and *ClientLabRunAudit* branches are still global legacy admin surfaces; they are explicitly deferred to S0-P04B, not evidence that all raw admin data is tenant-clean. `GET /admin/models/client-trials/{audit_id}` also remains a separate global trial-audit concern. |
| `api/observability.py` incremental/event routes and `services/observatory_query_service.py` | **No SimulationRun reader or writer** in this inventory. | Their experiment/pipeline/workflow event ownership must be checked by S0-P04B; the absence of a SimulationRun import is not a tenancy certification for those records. |
| Client Labs and custom upload insights (`client_lab_service.py`, `client_upload_insights.py`) | **Keep separate canonical lineage**; do not import into `SimulationRun` by convenience. | Client trials and uploads have their own workspace-linked rows. Their translated UI output is not evidence that a historical simulation belongs to that workspace. |
| Browser `/app/insights`, `useInsights`, navigation, marketing card | **Keep the customer page** on the tenant-scoped API and show an honest empty state. | `useInsights` uses a workspace-prefixed query key; the page calls only `/app/insights`. Navigation and marketing links are not data-plane authority. S0-P03C cancels/clears tenant state on switch. |
| Python SDK, CLI, synthetic seed and test fixtures | **No public simulation/insights SDK or CLI surface**. | `dclab_client` is `/v1`-only; the CLI has no `SimulationRun` command. E2E seed creates workspaces/users, not simulation rows. Tests insert both owned and null-owner fixtures to prove non-attribution and 404 behavior. |

The exact in-repository caller and fixture map for this decision is:

- **Storage/schema:** `apps/api/app/db/models.py` (`SimulationRun`),
  `apps/api/alembic/versions/0003_simulation_runs.py` (original unowned table),
  `0058_simulation_workspace.py` (additive lineage), and
  `apps/api/app/domain/simulation.py` (request/read contracts).
- **Direct write/read paths:** `apps/api/app/api/simulations.py` calls
  `simulation_run_service.py` for persistence, list, and lookup;
  `apps/api/app/api/insights.py` calls `insight_query.py` for the translated
  customer read. `sim/runner.py` generates deterministic payloads but does not
  decide ownership. `admin_model_registry_service.py` and
  `admin_monitoring_service.py` are the two other direct `SimulationRun`
  readers. No other runtime module imports that model in this checkout.
- **Tests and fixtures:** `apps/api/tests/test_simulation_insights_tenancy.py`
  seeds owned and unowned rows and checks cross-workspace denial;
  `test_simulation.py`, `test_translation_layer.py`, and
  `test_admin_surfaces.py` seed/read owned rows;
  `test_database_foundation_gate.py` and `test_tenant_composite_set_null.py`
  cover schema constraints; `tests/conftest.py` truncates the table between
  database tests. `apps/web/e2e/whole-system.spec.ts` and
  `session-security.spec.ts` exercise the Insights page through the BFF.
- **UI callers:** `apps/web/app/app/insights/page.tsx` uses `useInsights` in
  `apps/web/lib/application/hooks.ts`; `app-navigation.ts` and the marketing
  `sections.tsx` link to the route. There is no direct simulation/insights
  method in `packages/dclab_client`, and no command in `app/cli/main.py`.

This map is a source inventory, not a claim that the separate Experiment,
ClientLabRunAudit, business, or observability event surfaces are tenant-safe.
Those require the S0-P04B/C route and audience review.

### Historical-row evidence and quarantine

The 0003 table stored `use_case`, model/policy versions, fusion, payload and timestamp, but no authoritative tenant or project relationship. The 0058 migration deliberately performs **no backfill**. Identical use cases, hero `external_id` values, payload labels or creation time can occur in multiple workspaces, so none is a deterministic owner key. Existing `workspace_id IS NULL` rows form a logical, read-denied archive: they remain physically present for restricted forensic review but are not listed, translated, assigned to the default workspace, or served by a workspace-scoped derivative. This quarantine does not rewrite immutable scientific evidence.

If a future import claims ownership, it needs external source-to-workspace evidence tied to each exact run ID, a reviewed mapping digest, independent two-person approval and a new additive migration/work order. Ambiguous rows remain null and hidden. Do not bulk-assign by `use_case`, subject, organization name, current user, or `DEFAULT_WORKSPACE_ID`. A separate archive/export or deletion policy must preserve retention and evidence rules; it is outside S0-P04A.

### Compatibility and deployment order

The `/admin/simulations` and `/app/insights` URLs and response shapes remain stable; a workspace with only historical unowned runs now legitimately sees an empty result. The compatibility window is the additive 0058 schema plus the tenant-aware service release. **Do not serve customer traffic from a pre-tenant-aware binary during or after migration:** that binary may ignore the new columns and restore the global-latest read. Migrate first, deploy the scoped readers/writers, then validate two-workspace and orphan fixtures before opening traffic. `/v1` and the SDK remain unchanged. Broader admin raw-surface closure is the separately bounded S0-P04B work.

## S0-P04B enforcement reconciliation (2026-09-24)

The accepted additive `0058_simulation_workspace` migration and nullable
historical archive are retained; no new schema, default assignment, data
rewrite, or parallel canonical model is introduced. New runs still persist the
server-authorized workspace and optional same-workspace project. Insights now
selects one run per workspace/use case with a deterministic
`created_at DESC, id DESC` tie-breaker, including when concurrent writes have
the same timestamp. Null-owner history remains invisible.

The existing admin model registry and monitoring projections now constrain
Experiment, Dataset, SimulationRun, and ClientLabRunAudit-derived rows to the
selected workspace. Trial-audit ownership is derived from its required
`client_lab_run_id` relationship; an audit ID from another workspace returns
404 before raw payload projection. Candidate counts and task names are read
only for the workspace's selected experiments. This removes their previous
global-list behavior without rewriting scientific evidence.

Each affected admin route has an explicit method-aware `platform_read` or
`platform_write` check plus `workspace_read`; the shared dependencies deny
invalid selection or capability before a record lookup. Authorized attempts
emit a fixed-action structured audit record with request, actor, and validated
workspace IDs, but no payload, subject ID, arbitrary path, or external ID.
Denied authorization is covered by the shared workspace-access audit. The
admin URLs and response schemas are stable; the intentional compatibility
change is that a selected workspace no longer sees other workspaces' rows.
There is no global-read fallback flag. Deployment rollback must retain the
tenant-aware queries or disable these routes while repairing forward.

Observability/event response audience projection and unrelated business raw
payloads remain S0-P04C, not an implied certification of those surfaces.

## Rollback and forward repair

**Never roll back to a global `SimulationRun` or `/app/insights` reader.** First disable/deny the affected customer and workspace-scoped derivative routes at the application/ingress boundary, or restore the last verified **tenant-aware** binary while keeping the additive 0058 columns. Preserve owned and null-owner rows as-is. Re-run the two-workspace/orphan and migration checks before restoring traffic.

`alembic downgrade 0057_session_workspace` is a schema reversal, **not a safe live feature rollback**: it drops tenant lineage and can expose an old global reader. It is permitted only for a disposable environment or after an explicit archive/migration of owned rows, a reviewed retention plan, and proof that all SimulationRun-serving routes remain disabled. Prefer a forward repair that retains workspace columns and filters. No historical row is reassigned or deleted to make a rollback succeed.
