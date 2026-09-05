# Base44 UI migration — Phase 0 baseline

No visual changes were made in this phase. This records the repository as it stood when Phase 0 ran.

## Git

| Field | Value |
| --- | --- |
| Branch | `main` (tracks `origin/main`) |
| HEAD commit | `a9c33137cc72d80bb6a6b4835cf53d86c91d1f60` |
| HEAD subject | `model building fixed 0.1` |
| HEAD date | 2026-09-04 20:59:14 +0400 |
| Working tree | **Dirty.** Canonical-domain backend work (Alembic `0029`–`0036`, workspace/project APIs, object storage, frontend shell/layout files, docs) is uncommitted. Phase 0 did not commit or revert it. |

## Commands and results

### Frontend lint

```bash
cd apps/web
npm run lint
```

Result: **pass**. `next lint` — `No ESLint warnings or errors`.

### Frontend build

```bash
cd apps/web
npm run build
```

Result: **pass** (Next.js 14.2.18). Types checked. 45 app routes + `/_not-found`.

**PRE-EXISTING WARNING (not a failing test):** compiled with Edge Runtime warnings from `jose` (`CompressionStream` / `DecompressionStream`) imported through `apps/web/middleware.ts`. Build still succeeded.

`package.json` has no `npm test` unit-test script. Frontend checks at baseline are lint, build, and Playwright E2E.

### Backend tests

Repository workflow: root `Makefile` target `test` → `pytest --cov=app --cov-report=term-missing`.

Baseline command actually run (same suite, explicit path, no coverage gate):

```bash
.venv/bin/python -m pytest apps/api/tests --tb=line
```

Result: **690 passed, 3 skipped, 0 failed** in 4m 51s.

Skipped (environment, not product failures):

| Test | Why skipped |
| --- | --- |
| `test_legacy_tenant_lineage_migration.py` | Isolated Postgres on `localhost:55432` unavailable |
| `test_ml_run_events_postgres_enforcement.py` | Same 55432-style isolated Postgres (skip when that host is down) |
| `test_llm_client.py` (one case) | `@pytest.mark.skipif` when live LLM credentials are absent |

### E2E

Defined as:

```bash
cd apps/web
npm run e2e
# = npm run e2e:prepare && playwright test
```

`e2e:prepare` seeds `DCLAB_E2E_DATABASE_URL` defaulting to `postgresql://localhost:55432/dclab_e2e_verify`.

Probe:

```bash
pg_isready -h localhost -p 55432
# localhost:55432 - no response

DATABASE_URL=postgresql://localhost:55432/dclab_e2e_verify \
  .venv/bin/python scripts/seed_e2e_verification.py --recreate
```

Result: **not run**. Seed failed with `psycopg2.OperationalError: Connection refused` on port 55432. Playwright was not started.

This is **environment unavailable**, not a PRE-EXISTING FAILURE of the Playwright spec. Local Postgres on **5432** is up (`decisionai` / `decisionai_test`); the E2E database port is a separate instance that was not running.

## Existing failures

None attributed to this redesign. Nothing in lint/build/pytest failed.

Recorded separately:

1. **jose Edge Runtime warning** during `next build` (pre-existing middleware dependency).
2. **E2E database not listening on 55432** — cannot certify `apps/web/e2e/whole-system.spec.ts` at this baseline.
3. **Three pytest skips** that depend on 55432 or LLM keys.

## Route count

From `apps/web/app/**/page.tsx` and confirmed by `next build`:

- **45** page files / app URLs
- **39** unique page implementations
- **6** business URLs that re-export admin pages
- **9** public (marketing, login, showcase)
- **9** `/app` + `/lab`
- **21** `/admin`
- **7** `/business`

See `docs/BASE44_UI_MIGRATION_ROUTE_INVENTORY.md`.

## Shared UI components (current)

Under `apps/web/app/components/`:

| Area | Files |
| --- | --- |
| Layout | `RouteShell`, `AppShell`, `AppSidebar`, `AppMobileDrawer`, `app-navigation`, `SiteHeader`, `SiteFooter`, `SiteMain`, `HealthPill` |
| Product | `ProductPrimitives` (`ProductPageHeader`, `ObjectBreadcrumbs`, `GlassPanel`, `MetricCard`, …) |
| Workspace | `PageIntro` |
| UI primitives | `Button`, `Table`, `Badge`, `Card`, `EmptyState`, `ErrorState`, `Skeleton`, `ConfidenceBar` |
| Brand | `BrandLogo` |
| Marketing | `marketing/primitives`, `marketing/sections` |
| Insights / decisions / overview | `InsightCard`, `categoryMeta`, `DecisionLedgerEntry`, `ActionChart` |

Application layer: `lib/application/hooks.ts`, `query-provider.tsx`, `session-provider.tsx`. Infrastructure: `api-client.ts`, `session.ts`. Domain: `lib/domain/schemas.ts` (Zod), `format.ts`, `signals.ts`.

## Notable technical debt (do not “fix” as a visual redesign)

- Working tree on `main` already contains the canonical Workspace/Project/lineage backend and a partial product shell (`AppShell`, `globals.css` tokens, `ProductPrimitives`). Later phases must restyle against this tree, not against HEAD-only files.
- Backend already exposes register/workspaces/projects/problem specs/technical explorer/reproducibility. **No Next routes** consume those APIs yet. Do not fake them; connect only when a phase says REAL.
- Home `/` calls `useOverviewSnapshot` (client APIs) while remaining a public marketing page.
- `/admin/organizations` sits beside `/admin/businesses` (legacy vs workspace explorer).
- `/showcase` is a scratch primitives page, not a product surface.
- Middleware verifies JWT with `jose` on the Edge runtime (build warning).
- `dclab_developer` is platform-visible and write-blocked in API/UI; keep that split.
- Client Labs polling lives in `useLabUpload` / `usePipelineMonitor` (`refetchInterval`). Preserve it.
- No nested App Router layouts; product chrome is client-side `RouteShell`.

## Backend snapshot (inspected, not modified in Phase 0)

- FastAPI app: `apps/api/app/main.py` — `/auth`, `/workspaces`, reproducibility + technical explorer, `/admin/*`, `/business/*`, `/app/*`, `/health`.
- API modules: 19 files under `apps/api/app/api/`.
- Services: 47 modules under `apps/api/app/services/` plus `apps/api/app/storage/`.
- Alembic: `0001`–`0036`; head **`0036_legacy_import_projects`**.
- Domain/DB: `apps/api/app/db/models.py` remains authoritative.

## Phase 0 stop

Baseline is recorded. Do not start visual migration until a later phase explicitly says so.
