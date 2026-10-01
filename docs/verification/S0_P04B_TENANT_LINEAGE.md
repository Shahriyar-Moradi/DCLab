# S0-P04B legacy tenant lineage and global-read closure

**Status:** CURRENT — locally verified; exact-SHA CI pending  
**Baseline SHA:** `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4` (existing dirty worktree preserved)  
**Database head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)  
**Decision:** [ADR 0004](../adr/0004-simulation-insights-tenancy.md)

## Implementation packet and contract

The accepted simulation migration is already in the current lineage. Its
nullable workspace/project columns, composite project relationship, indexes,
and no-backfill decision were reviewed and retained; no revision was added.
Historical rows without provable ownership stay `workspace_id IS NULL` and
cannot reach workspace-scoped reads. New run writes validate the selected
workspace and optional same-workspace project. No immutable payload was
rewritten or assigned to a default workspace.

The changed owners are `insight_query.py` for deterministic latest-run
selection; the existing simulation, admin registry, and admin monitoring
routes/services for selected-workspace reads; and one safe structured audit
helper for those internal routes. Insight selection partitions within the
workspace and breaks equal-timestamp ties by run ID. Registry and monitoring
now scope Experiment, Dataset, SimulationRun, and trial-audit derivatives;
trial-audit detail derives ownership through its required ClientLabRun and
returns 404 on a foreign ID. Task metadata and candidate counts are fetched
only for selected experiments.

The admin routes explicitly require the existing named `platform_read` or
`platform_write` capability plus `workspace_read`. Successful authorization
attempts log a bounded action, request ID, actor ID and server-validated
workspace ID; they omit payloads, external subjects and arbitrary paths.
Shared dependencies audit capability/selector denials. No new public API,
event, SDK, state machine, job, cloud resource, configuration variable,
feature flag or parallel canonical model was added. AWS/GCP behavior is
identical because the change is provider-neutral HTTP/PostgreSQL logic.

## Observed verification (2026-09-24)

| Check | Observed result |
| --- | --- |
| `.venv/bin/alembic heads` | `0059_auth_session_constraints (head)`; no migration added |
| `.venv/bin/pytest -q --tb=short apps/api/tests/test_simulation_lineage_gate.py apps/api/tests/test_simulation_insights_tenancy.py apps/api/tests/test_admin_surfaces.py apps/api/tests/test_simulation.py apps/api/tests/test_translation_layer.py apps/api/tests/test_custom_prediction_admin_trail.py` | 76 passed, 1 existing Starlette deprecation warning, 26.36 s on disposable PostgreSQL 16 at localhost:55432 |
| `.venv/bin/pytest -q --tb=short apps/api/tests/test_access_control.py apps/api/tests/test_workspace_isolation_gate.py` | 219 passed, 1 existing Starlette deprecation warning, 62.43 s on the same cluster |

The migration test starts at the prior simulation head, inserts an unowned
row, upgrades to the live head, verifies the row remains unassigned with
unchanged scientific payload, and checks the workspace/project constraints.
The focused suite also exercises same-use-case two-workspace reads, foreign
and unowned IDs, empty history, concurrent writes from separate sessions,
equal-timestamp selection, registry/monitoring tenant filtering, direct
trial-audit 404, and authorized-only admin audit records. Counts above are
separate runs and must not be added as a unique-test total.

The first sandboxed test attempt could not connect to localhost because the
filesystem/network sandbox denied loopback. The tests were rerun with local
loopback permission against the disposable cluster and passed. The database
was not a production or shared environment; the cluster was stopped and its
temporary directory removed after verification.

## Rollout, rollback, and remaining gate

Deploy the additive schema before any tenant-aware service version; never
serve the pre-filter global reader during rollout. A selected workspace now
sees only its rows, while route paths and response shapes remain stable. There
is no global-read fallback flag. On a fault, retain the additive columns and
restore the last verified tenant-aware build or disable affected routes;
do not downgrade into a global reader. The ADR owns the full fail-closed
rollback procedure.

No fresh browser or full backend/SDK suite was run for this slice, and no
exact-SHA CI result exists for this dirty worktree. S0-P04C remains responsible
for raw-event audience projections and unrelated business response payloads.
The next eligible prompt is **S0-P04C** after this code receives the normal
review and exact-SHA gate.
