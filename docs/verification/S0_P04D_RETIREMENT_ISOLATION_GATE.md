# S0-P04D retirement and isolation gate

**Status:** CURRENT — re-verified locally on head 0061 (2026-10-01); exact-SHA CI pending, so Plan 0.4 is not formally closed  
**Baseline:** `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4`, existing dirty Scope 0 checkout preserved  
**Alembic head at this gate:** `0059_auth_session_constraints`  
**Re-verification head:** `0061_ingestion_publication`  
**Decision:** [ADR 0004](../adr/0004-simulation-insights-tenancy.md)

## Pre-edit execution card

1. **Goal/cut line.** Close only legacy simulation, insight, event and derived
   admin audience/tenant exposure from Plan 0.4. Preserve deterministic ML and
   historical rows. S0-P04A/B/C already supplied the lineage and projection;
   this work unit may repair their defects and add gate fixtures, not create a
   new customer feature, model, endpoint or migration. Review slice: tests,
   guarded E2E seed, runbook and evidence; code changes only if the gate fails.
2. **Live source map.** `api/simulations.py`, `api/insights.py`,
   `api/observability.py`, `api/admin_model_registry.py`,
   `api/admin_monitoring.py`, `services/simulation_run_service.py`,
   `services/insight_query.py`, `services/observatory_query_service.py`,
   `services/audience_projection.py`, `apps/web/app/app/insights/page.tsx`,
   `apps/web/lib/application/hooks.ts`, the BFF route and
   `packages/dclab_client` are reused, not replaced. Initial `rg` inventory
   found four direct `SimulationRun` readers: simulation service, insight
   query, registry and monitoring. The SDK has no simulation/insight method.
3. **Persistence.** No new migration. ADR 0004's additive simulation revision
   added nullable `workspace_id` and `project_id`, a workspace FK, composite
   workspace/project FK, unique `(workspace_id,id)`, project/workspace check
   and workspace/use-case/created index. Null-owner rows remain a read-denied
   archive; no backfill or evidence rewrite. Test previous-head → head and
   exact unchanged archived payload. No downgrade into a global reader.
4. **HTTP/permissions.** `/app/insights` requires current application/workspace
   access and returns 200 with empty category arrays when no owned row exists.
   `/admin/simulations/run` requires platform write plus workspace read;
   `/admin/simulations/runs[/{id}[/decisions/{external_id}]]`, `/admin/models`,
   `/admin/models/client-trials/{id}` and `/admin/monitoring` require platform
   read plus workspace read. Admin and business observability routes require
   their named capability and selected workspace before record lookup. No
   credentials → 401; missing capability/foreign selector → 403; authorized
   foreign/absent resource → indistinguishable 404; malformed selector → 400.
   Existing POST validation may return 400/422. 201/202/204, 409/412, 429,
   503, cursor and ETag are N/A to these legacy GET contracts. `/v1` event/SDK
   shapes are checked for audience leakage, not redesigned.
5. **Service/state.** `persist_simulation_run` binds the current workspace and
   validates same-workspace project before insert; `list/get_workspace_...`
   and `latest_runs_by_use_case` never serve null/foreign rows. Equal timestamp
   tie is `created_at DESC,id DESC`. No new transition authority. Existing
   simulation POST remains non-idempotent; adding a command lifecycle is
   outside this isolation gate.
6. **Jobs/events/effects.** No new job, provider call, external action, secret
   or object operation. Existing event polling must filter workspace before
   output and project a bounded, safe audience response. Concurrent creation
   and event sequence/replay are tested in the existing suites.
7. **Frontend/audience.** `useInsights` has a workspace-keyed query; the page
   handles loading, error, empty and result states. A synthetic browser fixture
   must prove two workspaces return distinct subjects through the BFF, an
   unowned subject stays hidden, and an old in-flight response cannot flash
   after switch. Admin raw simulation output is retained only behind platform
   capability and workspace selection; customer/developer projections omit
   path, handler, storage key, prompt, provider body and cross-tenant count.
8. **Tests/evidence.** Reuse `test_simulation_insights_tenancy.py`,
   `test_simulation_lineage_gate.py`, `test_legacy_audience_projection.py`,
   `test_pipeline_observability.py`, `test_workspace_isolation_gate.py` and
   SDK `test_http_contract.py`. Run these with PostgreSQL 16; run a two-workspace
   browser test against a guarded disposable E2E database; check OpenAPI/SDK
   drift, full backend/SDK and current documentation links. Record exit codes,
   durations and skips honestly. Exact-SHA CI cannot be claimed on a dirty
   checkout.
9. **Operations/rollout.** No new setting, flag or production resource.
   Existing fixed-reason denial/selection metrics and authorized admin audit
   remain the signals; no tenant or subject labels in metrics. Retain additive
   schema. If a leak is found, disable the affected route or deploy a repaired
   tenant-aware reader; never restore global or raw customer access. Archive
   recovery requires independent ownership proof and a future reviewed work
   order. The [archive runbook](../runbooks/simulation-archive-forward-repair.md)
   owns operator steps.
10. **Completion ledger.** The local browser, PostgreSQL backend/SDK, web
    type/component/lint, migration and truth-drift checks below pass. The
    pre-existing dirty checkout has no exact-SHA CI run. S0-P05A is the next
    candidate only after review and same-SHA CI close Plan 0.4.

## Route and audience matrix

| Surface | Selected workspace/authority | Absence and foreign ID | Audience |
| --- | --- | --- | --- |
| `POST /admin/simulations/run` | platform write + workspace read; optional project belongs to workspace | foreign project 404 | platform-only raw simulation |
| `GET /admin/simulations/runs`, `/{run_id}`, `/{run_id}/decisions/{external_id}` | platform read + workspace read | empty list 200; foreign, unowned or absent run 404 | platform-only raw simulation |
| `GET /app/insights` | application access + current workspace | owned-empty 200; foreign selector 403 | translated client insight only |
| `GET /admin/models`, `/client-trials/{audit_id}`, `/admin/monitoring` | platform read + workspace read | selected-workspace list; foreign detail 404 | platform projection |
| `GET /admin/observatory/*` | platform read + workspace read; query selector agrees | foreign/absent record 404 | safe legacy read projection; protected telemetry retains operator detail |
| `GET /business/observatory/*` | business administration + workspace read + route-specific monitor/audit/debug capability | foreign/absent record 404 | capability-filtered safe business projection |
| `/v1` events and SDK | current bearer/session workspace and resource authority | foreign/absent resource 404 | safe typed public projection |

## Observed local checks (2026-09-28)

All database tests used a newly initialized, disposable PostgreSQL 16 cluster
bound to `127.0.0.1:55432`. The E2E seed's `--recreate` guard targeted only
`dclab_e2e_verify` inside that cluster. No production or shared database was
opened. The cluster was stopped and its `/private/tmp/dclab-s0p04d-pg.xjijvh`
test files were removed after verification; the guarded seed can recreate
them. The live migration graph has the single
`0059_auth_session_constraints` head; the prior-head simulation test upgraded
through the tenant-expand revision to head and preserved the unowned row.

| Check | Observed result |
| --- | --- |
| `alembic heads` | One head, `0059_auth_session_constraints`; no S0-P04D revision |
| PostgreSQL `test_simulation_insights_tenancy.py`, `test_simulation_lineage_gate.py`, `test_legacy_audience_projection.py`, `test_pipeline_observability.py`, `test_workspace_isolation_gate.py`, SDK `test_http_contract.py` | **39 passed**, 1 existing Starlette warning, 31.16 s before the extra revocation/absent-ID assertions |
| PostgreSQL `test_simulation_insights_tenancy.py` after extra assertions | **10 passed**, 1 existing Starlette warning, 10.74 s |
| Migration-then-admin-audit reproduction after Alembic logging repair | **2 passed**, 1 existing Starlette warning, 6.32 s |
| Full PostgreSQL backend + Python SDK: `.venv/bin/pytest -q --tb=short --maxfail=1 apps/api/tests packages/dclab_client/tests` | **1,118 passed, 1 skipped, 20 warnings in 499.97 s** |
| Real-data BFF Insights slow-switch browser case alone | **1 passed in 24.6 s** |
| Full `session-security.spec.ts` + `whole-system.spec.ts` in installed Chrome | **27 passed in 1.9 min**; includes real BFF A/B subjects, no archived subject and no old-workspace flash |
| Web component tests and TypeScript | **5 passed**; `tsc --noEmit` exit 0 |
| Web lint and production build | `next lint` exit 0 with 3 pre-existing hook-dependency warnings; production build succeeded as Playwright webServer prerequisite |
| Generated truth, OpenAPI/SDK, links and artifact guard | Two generations byte-identical; `check_truth_drift` reported all detectors clean |

The executable PostgreSQL command used
`DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres` and
`MIGRATION_TEST_DATABASE_URL` with the same disposable host/port, plus
`DEVELOPER_DIR=/Library/Developer/CommandLineTools`. The browser command used
`DCLAB_E2E_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/dclab_e2e_verify`,
`DCLAB_E2E_API_URL=http://127.0.0.1:8001`,
`DCLAB_E2E_WEB_URL=http://127.0.0.1:3001`,
`DCLAB_E2E_BROWSER_CHANNEL=chrome` and
`./node_modules/.bin/playwright test e2e/session-security.spec.ts e2e/whole-system.spec.ts --reporter=line`
from `apps/web`. Chrome is an **opt-in local fallback** when Playwright's
bundled Chromium/ffmpeg are absent; default CI browser settings are unchanged.

The first sandboxed PostgreSQL run failed to connect to loopback; the same
focused tests passed with local-loopback access. The first browser attempt
could not launch because Playwright's bundled Chromium was absent; the next
attempt found bundled ffmpeg absent. The opt-in Chrome channel with video off
(screenshots and traces retained on failure) resolved both without a download.
An initial full backend run reached 961 passes then failed an admin-audit log
assertion: in-process Alembic `fileConfig` had disabled the application's
logger after an earlier migration test. Preserving existing loggers in
`alembic/env.py` made the migration-then-audit reproduction and complete rerun
pass. The broad browser run found three stale navigation assertions expecting
platform users to lack Business access; the server capability already grants
that access, so only those test expectations changed. No authorization grant
was added by S0-P04D.

The 1 skipped backend test is the previously unsupported live-provider case;
no live OpenAI or external cloud credential was used. Existing warnings include
Starlette's deprecated portal alias, the known SQLAlchemy FK-cycle warning,
scikit-learn unknown categories, Next.js's dual-lockfile root warning,
deprecated `next lint`, three hook-dependency warnings, Node's typeless-module
warning and the synthetic E2E JWT key-length warning. They are not converted
to new S0-P04D product defects; owners remain with their respective platform
and frontend follow-up plans.

## Re-verification on head 0061 (2026-10-01)

The checks above were observed on the `0059_auth_session_constraints` head
(observed then). The live migration graph has since advanced to
`0061_ingestion_publication`. P0.2-A re-runs the same gate on that head; a
cell reads `PENDING` until its result is recorded here. No result in this
table is inferred from the 2026-09-28 run.

| Check | Observed result |
| --- | --- |
| `alembic heads` is exactly `0061_ingestion_publication` | **One head**, `0061_ingestion_publication` (migrations 0060 and 0061 applied cleanly to the guarded E2E database) |
| PostgreSQL `test_simulation_insights_tenancy.py`, `test_simulation_lineage_gate.py`, `test_legacy_audience_projection.py`, `test_pipeline_observability.py`, `test_workspace_isolation_gate.py`, SDK `test_http_contract.py`, plus `test_legacy_tenant_lineage_migration.py` | **41 passed, 0 skipped** in 32.58 s on the disposable PostgreSQL 16.15 cluster (`127.0.0.1:55432`); on the main local cluster the same files gave 39 passed + 1 skipped (the isolated-cluster lineage test skips when 55432 is down) — no regression from 0060/0061 found, so no code repair was needed |
| Full PostgreSQL backend + Python SDK suite | `pytest apps/api/tests packages/dclab_client/tests` against 55432: **1,140 passed, 1 skipped** (live-OpenAI smoke, needs `DECISION_AGENT_LIVE=1`), **1 failed** in 556.63 s — `test_truth_drift` reported a stale generated `contracts/truth_manifest.json` after these doc edits; regenerated (byte-identical twice), see the truth-drift row |
| Playwright `session-security.spec.ts` + `whole-system.spec.ts` | **27 passed in 2.3 min** via the opt-in Chrome channel, real API :8001 + Next :3001 against `dclab_e2e_verify`; includes `S0-P04D legacy insights isolation gate › real BFF insights stay tenant-scoped across a slow workspace switch` |
| Web `tsc --noEmit`, lint and component tests | `tsc --noEmit` exit 0; `next lint` exit 0 with 0 errors and 4 warnings (unused `max` in `ModelComparison.tsx:43`, three hook-dependency warnings; none in S0-P04D code); component tests **5 passed, 0 failed** |
| Truth drift (`check_truth_drift --alembic-check`) | All detectors clean on the final tree against a freshly migrated 0061 database (generated artifacts regenerated, byte-identical twice); banned-terms scan not affected by this docs-only change |
| Exact-SHA CI on a clean commit | PENDING |

## Gate result and retained boundaries

No `SimulationRun` customer reader lacks a workspace predicate; registry and
monitoring derivatives are selected-workspace reads. Null-owner rows remain
quarantined. The browser seed inserts Business A, Business B and an unowned
synthetic row into only the guarded E2E database; the BFF test sees each owned
subject only in its workspace and never sees the archive subject. Current
membership suspension denies the next Insights request. Authorized foreign
and absent simulation IDs have the same 404 response. Admin raw simulation
detail remains behind named platform and workspace capabilities; Business
events retain their subordinate flags and audience-safe projection. No SDK or
CLI simulation/insight method was added, no feature flag was removed, and no
scientific evidence or production object was rewritten.

The local matrix is satisfied. Formal `VERIFIED` status requires review and
exact-SHA CI for a clean commit; this remains a dirty working tree on the
baseline SHA above. Do not treat historical CI or this local run as same-SHA
evidence. Subsequent S0-P05A local work does not constitute Plan 0.4 production sign-off.

## Quarantined-row forward repair and compatibility

An unowned `simulation_runs.workspace_id IS NULL` row is intentionally absent
from all workspace reads. The retained legacy archive surfaces (the legacy
`simulation_runs` table, `/admin/simulations/*`, `/app/insights` and the
translated Decision.ai opportunities, decisions, insights and dashboards
surfaces) follow the founder-decided compatibility window:

- **Frozen now (2026-10-01):** no new features; tenant filtering stays
  mandatory on every read.
- **Removal-or-rework decision:** made at the Phase 9 business decision layer
  rework ([roadmap Phase 9](../mvp/ROADMAP.md#phase-9--expansion-each-needs-a-trigger--adr)).
- **Hard deadline:** removed or formally re-homed no later than
  **2027-03-31**.

Until then their accepted compatibility contract is stable URLs/shapes with
mandatory tenant filtering. The global-read behavior ended with the
tenant-aware 0058 deployment and must never be restored, including during
removal or re-homing. Do not remove a protective feature flag merely because
this gate passes, and do not remove any feature flag guarding these surfaces
before the compatibility window closes; there is no Plan 0.4 global-read flag.

If a future operator can prove a row's owner, they must first preserve a
database backup and record the exact row IDs, source evidence and mapping
digest. A dry run must report zero ambiguous or cross-tenant mappings; two
independent reviewers must approve the mapping and retention/legal-hold
decision. A separate additive, idempotent work order then updates only proven
rows, audits actor/reason/before/after identity, re-runs two-workspace denial
and scientific-digest tests, and supports forward repair. Never infer owner
from use case, subject text, timestamp, current user or default workspace;
unproved rows stay null and hidden. Do not use `0058` downgrade as rollback.
The [operator runbook](../runbooks/simulation-archive-forward-repair.md)
provides the read-only inventory, incident containment and reviewed import
sequence. It authorizes no direct production mutation.
