# Base44 UI migration — route inventory

Current scan of `apps/web/app` (2026-09-05). This file is the live route inventory, not a Phase 0 baseline.

Source of truth for URLs: **46** `page.tsx` files. Next.js `next build` emits those app routes plus `/_not-found`.

Access is taken from `apps/web/middleware.ts` (JWT cookie `dclab_token`, signature verified) plus in-page write gates. Middleware matcher: `/admin/:path*`, `/business/:path*`, `/app/:path*`, `/lab/:path*`. Unauthenticated visitors to those prefixes are redirected to `/login?next=…`.

Role keys:

- Platform: `dclab_admin`, `dclab_developer` — only these pass `/admin/*`
- Business administration: `business_admin`, `business_developer`, `workspace_owner`, `workspace_admin`, `ml_engineer`, `viewer` — plus platform roles on `/business/*`
- Customer app (`/app`, `/lab`): any authenticated role, including `client_user`

Every product and marketing page uses the migrated Geist/glass system (`AppShell`, `AuthShell`, or `MarketingShell` via `RouteShell`). There are **no remaining “not started” Next pages**.

## Status legend

| Status | Meaning |
| --- | --- |
| **migrated + verified** | Page uses the current UI, and Playwright (or an equivalent automated browser assertion) exercises the route’s primary purpose. |
| **migrated + partially verified** | Page uses the current UI. Coverage is limited to navigation/chrome, a documented HTML load, a source scan, or backing API tests — not a full browser workflow. |
| **not migrated** | Old UI still shipped. **None.** |
| **intentionally unchanged** | Kept as a non-product surface by design. |
| **API-only / no frontend page** | FastAPI route exists; no matching `page.tsx`. |

“Verified” is **not** claimed from the Phase 14 feature-matrix contract audit alone.

## Verification sources

| Source | What it proves |
| --- | --- |
| `apps/web/e2e/whole-system.spec.ts` (12 Playwright tests) | Login, role shells, `client_user` 403s, dashboard/insights/labs/opportunities/decisions flows, classification + regression Labs runs, prediction CSV **Download results**, platform + business pipeline monitors, capability fail-closed, tenant isolation |
| `apps/api/tests/test_adaptive_modeling_production_e2e.py::test_monitor_page_exposes_required_scientific_panels` | Monitor **source** still renders required scientific panel titles |
| Phase 16 documented HTML **200** deep links (`docs/BASE44_UI_MIGRATION_FINAL_REPORT.md` §16.4) | One-time authenticated load of lab lists and nested explorer URLs — not a regression suite |
| `apps/api/tests/test_access_control.py` and related API tests | Backend authorization; **not** page verification |

## Summary

| Bucket | Page files | Unique implementations | Status mix |
| --- | --- | --- | --- |
| Public marketing / login / scratch | 9 | 9 | 1 verified, 7 partial, 1 intentionally unchanged |
| Authenticated `/app` + `/lab` | 10 | 10 | 9 verified, 1 partial |
| Platform `/admin` | 20 | 20 | 2 verified, 18 partial |
| Business `/business` | 7 | 1 unique + 6 re-exports | 3 verified, 4 partial |
| **Total** | **46** | **40 unique + 6 re-exports** | **15 verified, 30 partial, 1 intentionally unchanged, 0 not migrated** |

Shared chrome: `RouteShell` → `AppShell` for `/app`, `/lab`, `/admin`, `/business`; `AuthShell` for `/login`; marketing `SiteHeader` / `SiteMain` / `SiteFooter` otherwise.

## Re-exports (shared implementations)

These URLs are first-class routes. The page file only re-exports the admin implementation. The shared page switches API prefix with `usePathname().startsWith("/business/")`.

| URL | Page file | Canonical implementation |
| --- | --- | --- |
| `/business/workspaces/[businessId]` | `apps/web/app/business/workspaces/[businessId]/page.tsx` | `apps/web/app/admin/businesses/[businessId]/page.tsx` |
| `/business/workspaces/[businessId]/domains/[domainId]` | `apps/web/app/business/workspaces/[businessId]/domains/[domainId]/page.tsx` | `apps/web/app/admin/businesses/[businessId]/domains/[domainId]/page.tsx` |
| `/business/workspaces/[businessId]/workflows/[workflowId]` | `apps/web/app/business/workspaces/[businessId]/workflows/[workflowId]/page.tsx` | `apps/web/app/admin/businesses/[businessId]/workflows/[workflowId]/page.tsx` |
| `/business/workspaces/[businessId]/workflow-runs/[runId]` | `apps/web/app/business/workspaces/[businessId]/workflow-runs/[runId]/page.tsx` | `apps/web/app/admin/businesses/[businessId]/workflow-runs/[runId]/page.tsx` |
| `/business/workspaces/[businessId]/models/[modelId]` | `apps/web/app/business/workspaces/[businessId]/models/[modelId]/page.tsx` | `apps/web/app/admin/businesses/[businessId]/models/[modelId]/page.tsx` |
| `/business/workspaces/[businessId]/pipeline-runs/[pipelineId]/monitor` | `apps/web/app/business/workspaces/[businessId]/pipeline-runs/[pipelineId]/monitor/page.tsx` | `apps/web/app/admin/pipeline-runs/[pipelineId]/monitor/page.tsx` |

## Public routes

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Status | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | `apps/web/app/page.tsx` | Public | Marketing home. Signed-in hero may call `useOverviewSnapshot`. | `useSession`; `useOverviewSnapshot` → `GET /app/opportunities`, `GET /app/decisions` (authenticated only) | No | migrated + partially verified | Playwright: marketing nav + footer on `/`. Does not assert hero metrics. |
| `/login` | `apps/web/app/login/page.tsx` | Public | Sign in. Local demo shortcuts when `NODE_ENV !== "production"`. | `useLogin` → `POST /auth/login`; `useSession` | No | migrated + verified | Playwright login success/failure, role landing, sign-out, `client_user` session. |
| `/platform` | `apps/web/app/platform/page.tsx` | Public | Marketing platform story. | None (static) | No | migrated + partially verified | No automated route test. |
| `/solutions` | `apps/web/app/solutions/page.tsx` | Public | Marketing solutions. | None (static) | No | migrated + partially verified | No automated route test. |
| `/industries` | `apps/web/app/industries/page.tsx` | Public | Marketing industries. | None (static) | No | migrated + partially verified | No automated route test. |
| `/pricing` | `apps/web/app/pricing/page.tsx` | Public | Marketing pricing. | None (static) | No | migrated + partially verified | No automated route test. |
| `/company` | `apps/web/app/company/page.tsx` | Public | Marketing company. | None (static) | No | migrated + partially verified | No automated route test. |
| `/resources` | `apps/web/app/resources/page.tsx` | Public | Marketing resources. | None (static) | No | migrated + partially verified | No automated route test. |
| `/showcase` | `apps/web/app/showcase/page.tsx` | Public in development | Primitive gallery. **`notFound()` when `NODE_ENV === "production"`.** | None | No | intentionally unchanged | Dev-only scratch; not a product workflow. |

## Authenticated customer / Labs routes

Middleware: any valid session role.

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Status | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/app/dashboards` | `apps/web/app/app/dashboards/page.tsx` | Authenticated | Workspace dashboard: opportunity/decision totals and ledger. | `useOverviewSnapshot` → `GET /app/opportunities`, `GET /app/decisions` | No | migrated + verified | Playwright: loading/empty/error/populated; `client_user` access + refresh. |
| `/app/insights` | `apps/web/app/app/insights/page.tsx` | Authenticated | Translated insight cards. | `useInsights` → `GET /app/insights` | No | migrated + verified | Playwright: live insights payload; rejects fabricated KPI copy. |
| `/app/opportunities` | `apps/web/app/app/opportunities/page.tsx` | Authenticated | Opportunity list with filters. | `useOpportunities` → `GET /app/opportunities` | No | migrated + verified | Playwright: list after CSV upload. |
| `/app/opportunities/upload` | `apps/web/app/app/opportunities/upload/page.tsx` | Authenticated | CSV upload of opportunities. | `useUploadOpportunities` → `POST /app/opportunities/upload` | No | migrated + verified | Playwright: upload **200** then list. |
| `/app/opportunities/[id]` | `apps/web/app/app/opportunities/[id]/page.tsx` | Authenticated | Opportunity detail + generate decision. | `useOpportunity` → `GET /app/opportunities/:id`; `useDecisions`; `useGenerateDecision` → `POST /app/decisions/generate` | No | migrated + verified | Playwright: open detail, Generate. |
| `/app/decisions` | `apps/web/app/app/decisions/page.tsx` | Authenticated | Decision ledger list. | `useDecisions` → `GET /app/decisions` | No | migrated + verified | Playwright: list after generate (or empty state). |
| `/app/decisions/[id]` | `apps/web/app/app/decisions/[id]/page.tsx` | Authenticated | Single decision. | `useDecision` → `GET /app/decisions/:id` | No | migrated + verified | Playwright: open generated decision when one exists. |
| `/app/labs` | `apps/web/app/app/labs/page.tsx` | Authenticated | Client Labs: catalog trials + CSV auto-train uploads. | Page: `useLabProblems` → `GET /app/labs/problems`. Children: `useLabQuota` → `GET /app/labs/problems/:useCase/quota`; `useLabRuns` → `GET /app/labs/runs`; `useRunLabTrial` → `POST /app/labs/runs`; `useLabUploads` → `GET /app/labs/uploads`; `useUploadLabFile` → `POST /app/labs/uploads` | No | migrated + verified | Playwright: sample trial; classification + regression CSV upload; developer read-only file input. |
| `/app/settings` | `apps/web/app/app/settings/page.tsx` | Authenticated | Account: JWT name/email/role/workspace. No save/invite/billing. | `useSession` (cookie claims; does **not** call `GET /auth/me`) | No | migrated + partially verified | No automated visit. Sidebar/command palette link to this URL. |
| `/lab/runs/[run_id]` | `apps/web/app/lab/runs/[run_id]/page.tsx` | Authenticated | Upload/run progress and prediction download. | `useLabUpload` → `GET /app/labs/uploads/:id`; `downloadLabPredictions` → `GET /app/labs/uploads/:id/predictions.csv` | No | migrated + verified | Playwright: Completed run; **Download results** click + real CSV inspect; 403 UI when `prediction_download` is off. |

## Platform `/admin` routes

Middleware: `dclab_admin` or `dclab_developer`. Other signed-in roles get HTML **403**. Several pages additionally disable writes unless `user.role === "dclab_admin"`. Platform sidebar: `/admin/lab…` → Labs & Experiments; `/admin/models…` → Model Registry; `/admin/monitoring…` and `/admin/pipeline-runs/…/monitor` → Monitoring.

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Status | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/admin/businesses` | `apps/web/app/admin/businesses/page.tsx` | Platform | List workspaces/businesses. | `usePlatformBusinesses` → `GET /admin/businesses` | No | migrated + verified | Playwright: admin/developer see Business A and B; `client_user` and business roles get **403**. |
| `/admin/businesses/[businessId]` | `apps/web/app/admin/businesses/[businessId]/page.tsx` | Platform on this URL | Workspace explorer. | `usePlatformBusiness` → `GET /admin/businesses/:id` (or `/business/workspaces/:id` in business mode) | Yes | migrated + partially verified | Phase 16 documented HTML **200** for Business A. Playwright does not open this admin URL (it uses the list + Labs upload). |
| `/admin/businesses/[businessId]/domains/[domainId]` | `apps/web/app/admin/businesses/[businessId]/domains/[domainId]/page.tsx` | Platform on this URL | Domain detail. | `usePlatformDomain` → `GET /admin/businesses/:id/domains/:domainId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/admin/businesses/[businessId]/workflows/[workflowId]` | `apps/web/app/admin/businesses/[businessId]/workflows/[workflowId]/page.tsx` | Platform on this URL | Workflow definition detail. | `usePlatformWorkflow` → `GET /admin/businesses/:id/workflows/:workflowId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/admin/businesses/[businessId]/workflow-runs/[runId]` | `apps/web/app/admin/businesses/[businessId]/workflow-runs/[runId]/page.tsx` | Platform on this URL | Workflow run + child pipelines. | `usePlatformWorkflowRun` → `GET /admin/businesses/:id/workflow-runs/:runId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/admin/businesses/[businessId]/models/[modelId]` | `apps/web/app/admin/businesses/[businessId]/models/[modelId]/page.tsx` | Platform on this URL | Model asset versions. | `usePlatformModel` → `GET /admin/businesses/:id/models/:modelId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/admin/pipeline-runs/[pipelineId]/monitor` | `apps/web/app/admin/pipeline-runs/[pipelineId]/monitor/page.tsx` | Platform on this URL | Live pipeline monitor + optional deep audit. | `usePipelineMonitor` → `GET /admin/pipeline-runs/:id/monitor`; `useBusinessDeepAudit` → `POST /admin/lab/runs/:runId/verification/deep` | Yes | migrated + verified | Playwright: scientific panels, event replay, active nav **Monitoring** (not Labs & Experiments). Pytest source scan of `PipelineMonitorView`. |
| `/admin/lab` | `apps/web/app/admin/lab/page.tsx` | Platform | Lab home: environments, datasets, tasks, experiments, client uploads. | `useLabEnvironments` → `GET /admin/environments`; `useLabDatasets` → `GET /admin/datasets`; `useLabTasks` → `GET /admin/tasks`; `useLabExperiments` → `GET /admin/experiments`; `useAdminClientUploads` → `GET /admin/client-uploads` | No | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. `POST /admin/environments/dogfood` is unused by this page. |
| `/admin/lab/datasets` | `apps/web/app/admin/lab/datasets/page.tsx` | Platform; upload write = `dclab_admin` | Dataset list, upload, sample workbook. | `useLabDatasets`; `useUploadLabDataset` → `POST /admin/datasets/upload`; `useCreateLabWorkbook` → `POST /admin/datasets/sample-workbook` | No | migrated + partially verified | Phase 16 HTML **200**. Dataset upload **not** clicked in E2E. |
| `/admin/lab/datasets/[id]` | `apps/web/app/admin/lab/datasets/[id]/page.tsx` | Platform; train write = `dclab_admin` | Dataset profile + use-case train. | Page `apiGet` `GET /admin/datasets/:id`, `GET /admin/datasets/:id/profile`; `useLabUseCasePlan` → `GET /admin/datasets/:id/use-cases`; `useTrainLabUseCase` → `POST /admin/datasets/:id/use-cases/:slug/train` | No | migrated + partially verified | No automated page visit. |
| `/admin/lab/tasks` | `apps/web/app/admin/lab/tasks/page.tsx` | Platform | Task catalog. | `useLabTasks` → `GET /admin/tasks` | No | migrated + partially verified | Phase 16 HTML **200**. No Playwright. |
| `/admin/lab/tasks/create` | `apps/web/app/admin/lab/tasks/create/page.tsx` | Platform; write = `dclab_admin` | Load YAML task spec from repo path. | `useCreateLabTaskFromConfig` → `POST /admin/tasks/from-config?path=` | No | migrated + partially verified | YAML create **not** clicked in E2E. |
| `/admin/lab/experiments` | `apps/web/app/admin/lab/experiments/page.tsx` | Platform | Experiment list. | `useLabExperiments` → `GET /admin/experiments` | No | migrated + partially verified | Playwright: `goto` + Labs & Experiments `aria-current`. Does not assert experiment rows. |
| `/admin/lab/experiments/[id]` | `apps/web/app/admin/lab/experiments/[id]/page.tsx` | Platform | Experiment detail, report, candidates, comparison. | `useLabExperiment` → `GET /admin/experiments/:id`; `useLabReport` → `GET /admin/experiments/:id/report`; `useLabCandidates` → `GET /admin/experiments/:id/candidates`; `useLabComparison` → `GET /admin/experiments/:id/comparison` | No | migrated + partially verified | No automated page visit. |
| `/admin/models` | `apps/web/app/admin/models/page.tsx` | Platform | Model registry + trial/upload indexes. | `useAdminModelRegistry` → `GET /admin/models` | No | migrated + partially verified | Playwright: `goto` + Model Registry `aria-current`. Developer also lands Registry link. |
| `/admin/models/client-uploads/[id]` | `apps/web/app/admin/models/client-uploads/[id]/page.tsx` | Platform | Full Labs upload technical trail. | `useAdminClientUpload` → `GET /admin/client-uploads/:id`; `downloadAdminRunPredictions` → `GET /admin/client-uploads/:id/predictions.csv` | No | migrated + partially verified | Playwright visits this URL when a skipped run has no `experiment_id`. Happy-path trail and admin CSV download are not E2E-clicked. |
| `/admin/models/client-trials/[id]` | `apps/web/app/admin/models/client-trials/[id]/page.tsx` | Platform | Catalog trial audit payload. | `useAdminClientTrialAudit` → `GET /admin/models/client-trials/:id` | No | migrated + partially verified | API tests exist; no Playwright page visit. |
| `/admin/monitoring` | `apps/web/app/admin/monitoring/page.tsx` | Platform | Platform monitoring overview. | `useAdminMonitoring` → `GET /admin/monitoring` | No | migrated + partially verified | Playwright: `goto` + Monitoring `aria-current`. Overview widgets not asserted. |
| `/admin/organizations` | `apps/web/app/admin/organizations/page.tsx` | Platform | Workspace summaries (legacy IA beside Businesses). | `useAdminOrganizations` → `GET /admin/organizations` | No | migrated + partially verified | API tests for the endpoint; no Playwright page visit. Command palette still links here for platform roles. |
| `/admin/organizations/[id]` | `apps/web/app/admin/organizations/[id]/page.tsx` | Platform | Organization / workspace detail. | `useAdminOrganization` → `GET /admin/organizations/:id` | No | migrated + partially verified | API tests for the endpoint; no Playwright page visit. |

## Business administration `/business` routes

Middleware: platform roles **or** the business-administration / workspace roles listed above. `client_user` is HTML **403**. Nested monitor URLs keep **Business Admin** as the active sidebar item (`/business` prefix).

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Status | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/business` | `apps/web/app/business/page.tsx` | Business admin area | List workspaces the actor can administer. | `useBusinessWorkspaces` → `GET /business/workspaces` | No | migrated + verified | Playwright: Business A only; `client_user` **403**. |
| `/business/workspaces/[businessId]` | see re-exports | Business admin area | Same UI as platform business detail. | `usePlatformBusiness(..., true)` → `GET /business/workspaces/:id` | Yes | migrated + verified | Playwright: Operations + Labs headings; `model_management` fail-closed; cross-tenant substitution denied. |
| `/business/workspaces/[businessId]/domains/[domainId]` | see re-exports | Business admin area | Domain detail. | `GET /business/workspaces/:id/domains/:domainId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/business/workspaces/[businessId]/workflows/[workflowId]` | see re-exports | Business admin area | Workflow detail. | `GET /business/workspaces/:id/workflows/:workflowId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/business/workspaces/[businessId]/workflow-runs/[runId]` | see re-exports | Business admin area | Workflow run detail. | `GET /business/workspaces/:id/workflow-runs/:runId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. No Playwright. |
| `/business/workspaces/[businessId]/models/[modelId]` | see re-exports | Business admin area | Model detail. | `GET /business/workspaces/:id/models/:modelId` | Yes | migrated + partially verified | Phase 16 documented HTML **200**. Model list is capability-gated in Playwright; this detail URL is not opened. |
| `/business/workspaces/[businessId]/pipeline-runs/[pipelineId]/monitor` | see re-exports | Business admin area | Pipeline monitor. | `usePipelineMonitor(id, businessId)` → `GET /business/workspaces/:id/pipeline-runs/:pipelineId/monitor`; deep audit `POST /business/workspaces/:id/lab-runs/:runId/verification/deep` | Yes | migrated + verified | Playwright: Pipeline Monitor heading; `pipeline_monitor` / section capabilities; developer read-only deep audit. |

## Backend surfaces with no matching frontend page

These exist on FastAPI (`apps/api/app/main.py`) and must not be invented as duplicate Next routes. Status: **API-only / no frontend page**.

| Surface | Notes |
| --- | --- |
| `POST /auth/register`, `GET /auth/me` | Login UI uses `POST /auth/login` + cookie JWT. `/me` unused by the app. |
| `POST /workspaces/personal`, `POST /workspaces/business`, members, projects, problem specs | Identity-sensitive; no workspace switcher page. |
| Workspace technical explorer `/workspaces/:id/explorer/…` | No Next page. |
| Workspace / admin reproducibility (`/workspaces/…/artifacts/…`, `/admin/artifacts/…`) | No Next page. |
| Admin technical explorer `/admin/explorer/…` | No Next page. |
| Raw observatory `/admin/observatory/…`, `/business/observatory/…` | Monitor aggregates events; no dedicated observatory page. |
| Simulations `/admin/simulations/…` | Feeds `/app/insights`; no customer wizard. |
| `GET /app/labs/runs/{run_id}` | Hook `useLabRun` exists; UI uses `/lab/runs/[run_id]` for **uploads**, not catalog trials. |
| `GET /business/workspaces/{id}/client-uploads/{id}/predictions.csv` | Client download uses `/app/labs/uploads/…/predictions.csv`. Playwright also 403s this business path when the capability is off. |
| `GET /admin/client-uploads/{id}/report.docx` and lab verification `report.docx` | No Next download button. |
| Extra admin lab POSTs (`/admin/datasets/{id}/train`, `/admin/datasets/{id}/profile`, `POST /admin/experiments`, `/run`, unused experiment subresources) | Dataset detail trains per use-case slug instead. |
| `GET /health` | Marketing `HealthPill` only; not a `page.tsx`. |

## Layout files

| File | Role |
| --- | --- |
| `apps/web/app/layout.tsx` | Root layout: Geist fonts, `QueryProvider`, `RouteShell` |
| No nested `layout.tsx` | Product vs auth vs marketing split is client-side in `RouteShell` |
| No `loading.tsx` / `error.tsx` / `not-found.tsx` in `app/` | Next default `/_not-found` only (`/showcase` calls `notFound()` in production) |
