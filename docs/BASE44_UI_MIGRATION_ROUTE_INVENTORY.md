# Base44 UI migration — route inventory

Captured from the live `apps/web/app` tree. No visual changes were made for this inventory.

Source of truth for URLs: 45 `page.tsx` files. Next.js `next build` emitted the same 45 app routes plus `/_not-found`.

Access is taken from `apps/web/middleware.ts` (JWT cookie `dclab_token`) plus in-page write gates. Middleware matcher: `/admin/:path*`, `/business/:path*`, `/app/:path*`, `/lab/:path*`. Unauthenticated visitors to those prefixes are redirected to `/login?next=…`.

Role keys:

- Platform: `dclab_admin`, `dclab_developer`
- Business administration: `business_admin`, `business_developer`, `workspace_owner`, `workspace_admin`, `ml_engineer`, `viewer`
- Customer app: any authenticated role, including `client_user`

Migration status is **not started** on every page (Phase 0 baseline).

## Summary

| Bucket | Page files | Notes |
| --- | --- | --- |
| Public marketing / login / scratch | 9 | Header/footer via `RouteShell` |
| Authenticated `/app` + `/lab` | 9 | Left `AppShell` |
| Platform `/admin` | 21 | Left `AppShell`; non-platform roles get HTML 403 |
| Business `/business` | 7 | 6 of 7 re-export the matching admin page |
| **Total** | **45** | 39 unique implementations + 6 re-exports |

Shared product chrome: `RouteShell` → `AppShell` for `/app`, `/lab`, `/admin`, `/business`; marketing `SiteHeader` / `SiteMain` / `SiteFooter` otherwise.

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

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Migration |
| --- | --- | --- | --- | --- | --- | --- |
| `/` | `apps/web/app/page.tsx` | Public | Marketing home. `Hero` also calls `useOverviewSnapshot` (authenticated `/app/opportunities` + `/app/decisions`) so anonymous visitors can see a failed snapshot. | `useOverviewSnapshot` | No | not started |
| `/login` | `apps/web/app/login/page.tsx` | Public | Sign in; demo account shortcuts. | `useLogin` → `POST /auth/login`; `useSession` | No | not started |
| `/platform` | `apps/web/app/platform/page.tsx` | Public | Marketing platform story. | None (static) | No | not started |
| `/solutions` | `apps/web/app/solutions/page.tsx` | Public | Marketing solutions. | None (static) | No | not started |
| `/industries` | `apps/web/app/industries/page.tsx` | Public | Marketing industries. | None (static) | No | not started |
| `/pricing` | `apps/web/app/pricing/page.tsx` | Public | Marketing pricing. | None (static) | No | not started |
| `/company` | `apps/web/app/company/page.tsx` | Public | Marketing company. | None (static) | No | not started |
| `/resources` | `apps/web/app/resources/page.tsx` | Public | Marketing resources. | None (static) | No | not started |
| `/showcase` | `apps/web/app/showcase/page.tsx` | Public | Internal primitive scratch page (buttons, badges, table). Hard-coded UI only. | None | No | not started |

## Authenticated customer / Labs routes

Middleware: any valid session role.

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Migration |
| --- | --- | --- | --- | --- | --- | --- |
| `/app/dashboards` | `apps/web/app/app/dashboards/page.tsx` | Authenticated | Workspace dashboard: opportunity/decision totals and ledger. | `useOverviewSnapshot` → `GET /app/opportunities`, `GET /app/decisions` | No | not started |
| `/app/insights` | `apps/web/app/app/insights/page.tsx` | Authenticated | Translated insight cards. | `useInsights` → `GET /app/insights` | No | not started |
| `/app/opportunities` | `apps/web/app/app/opportunities/page.tsx` | Authenticated | Opportunity list with filters. | `useOpportunities` → `GET /app/opportunities` | No | not started |
| `/app/opportunities/upload` | `apps/web/app/app/opportunities/upload/page.tsx` | Authenticated | CSV upload of opportunities. | `useUploadOpportunities` → `POST /app/opportunities/upload` | No | not started |
| `/app/opportunities/[id]` | `apps/web/app/app/opportunities/[id]/page.tsx` | Authenticated | Opportunity detail + generate decision. | `useOpportunity` → `GET /app/opportunities/:id`; `useDecisions`; `useGenerateDecision` → `POST /app/decisions/generate` | No | not started |
| `/app/decisions` | `apps/web/app/app/decisions/page.tsx` | Authenticated | Decision ledger list. | `useDecisions` → `GET /app/decisions` | No | not started |
| `/app/decisions/[id]` | `apps/web/app/app/decisions/[id]/page.tsx` | Authenticated | Single decision. | `useDecision` → `GET /app/decisions/:id` | No | not started |
| `/app/labs` | `apps/web/app/app/labs/page.tsx` | Authenticated | Client Labs: catalog trials + CSV auto-train uploads. | `useLabProblems` → `GET /app/labs/problems`; `useLabQuota` → `GET /app/labs/problems/:useCase/quota`; `useRunLabTrial` → `POST /app/labs/runs`; `useLabUploads` → `GET /app/labs/uploads`; `useUploadLabFile` → `POST /app/labs/uploads` | No | not started |
| `/lab/runs/[run_id]` | `apps/web/app/lab/runs/[run_id]/page.tsx` | Authenticated | Client-facing upload/run progress and download. | `useLabUpload` → `GET /app/labs/uploads/:id` (polls while queued/processing); `downloadLabPredictions` → `GET /app/labs/uploads/:id/predictions.csv` | No | not started |

## Platform `/admin` routes

Middleware: `dclab_admin` or `dclab_developer`. Other signed-in roles get 403. Several pages additionally disable writes unless `user.role === "dclab_admin"`.

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Migration |
| --- | --- | --- | --- | --- | --- | --- |
| `/admin/businesses` | `apps/web/app/admin/businesses/page.tsx` | Platform | List workspaces/businesses. | `usePlatformBusinesses` → `GET /admin/businesses` | Implementation shared with business detail pages via pathname, not this list | not started |
| `/admin/businesses/[businessId]` | `apps/web/app/admin/businesses/[businessId]/page.tsx` | Platform on this URL | Workspace explorer: domains, workflows, runs, models. | `usePlatformBusiness` → `GET /admin/businesses/:id` (or `/business/workspaces/:id` in business mode) | Yes (business re-export) | not started |
| `/admin/businesses/[businessId]/domains/[domainId]` | `apps/web/app/admin/businesses/[businessId]/domains/[domainId]/page.tsx` | Platform on this URL | Domain detail. | `usePlatformDomain` → `GET /admin/businesses/:id/domains/:domainId` | Yes | not started |
| `/admin/businesses/[businessId]/workflows/[workflowId]` | `apps/web/app/admin/businesses/[businessId]/workflows/[workflowId]/page.tsx` | Platform on this URL | Workflow definition detail. | `usePlatformWorkflow` → `GET /admin/businesses/:id/workflows/:workflowId` | Yes | not started |
| `/admin/businesses/[businessId]/workflow-runs/[runId]` | `apps/web/app/admin/businesses/[businessId]/workflow-runs/[runId]/page.tsx` | Platform on this URL | Workflow run + child pipelines. | `usePlatformWorkflowRun` → `GET /admin/businesses/:id/workflow-runs/:runId` | Yes | not started |
| `/admin/businesses/[businessId]/models/[modelId]` | `apps/web/app/admin/businesses/[businessId]/models/[modelId]/page.tsx` | Platform on this URL | Model asset versions. | `usePlatformModel` → `GET /admin/businesses/:id/models/:modelId` | Yes | not started |
| `/admin/pipeline-runs/[pipelineId]/monitor` | `apps/web/app/admin/pipeline-runs/[pipelineId]/monitor/page.tsx` | Platform on this URL | Live pipeline monitor + optional deep audit. | `usePipelineMonitor` → `GET /admin/pipeline-runs/:id/monitor` (polls until terminal); `useBusinessDeepAudit` → `POST …/lab-runs/:runId/verification/deep` | Yes (business monitor re-export) | not started |
| `/admin/lab` | `apps/web/app/admin/lab/page.tsx` | Platform | Lab home: environments, datasets, tasks, experiments, client uploads. | `useLabEnvironments` → `GET /admin/environments`; `useLabDatasets` → `GET /admin/datasets`; `useLabTasks` → `GET /admin/tasks`; `useLabExperiments` → `GET /admin/experiments`; `useAdminClientUploads` → `GET /admin/client-uploads` | No | not started |
| `/admin/lab/datasets` | `apps/web/app/admin/lab/datasets/page.tsx` | Platform; upload write = `dclab_admin` | Dataset list, upload, sample workbook. | `useLabDatasets`; `useUploadLabDataset` → `POST /admin/datasets/upload`; `useCreateLabWorkbook` → `POST /admin/datasets/sample-workbook` | No | not started |
| `/admin/lab/datasets/[id]` | `apps/web/app/admin/lab/datasets/[id]/page.tsx` | Platform; train write = `dclab_admin` | Dataset profile + use-case train. | Direct `apiGet` `GET /admin/datasets/:id`, `GET /admin/datasets/:id/profile`; `useLabUseCasePlan` → `GET /admin/datasets/:id/use-cases`; `useTrainLabUseCase` → `POST /admin/datasets/:id/use-cases/:slug/train` | No | not started |
| `/admin/lab/tasks` | `apps/web/app/admin/lab/tasks/page.tsx` | Platform | Task catalog. | `useLabTasks` → `GET /admin/tasks` | No | not started |
| `/admin/lab/tasks/create` | `apps/web/app/admin/lab/tasks/create/page.tsx` | Platform; write = `dclab_admin` | Load YAML task spec from repo path. | Direct `apiPost` `POST /admin/tasks/from-config?path=` | No | not started |
| `/admin/lab/experiments` | `apps/web/app/admin/lab/experiments/page.tsx` | Platform | Experiment list. | `useLabExperiments` → `GET /admin/experiments` | No | not started |
| `/admin/lab/experiments/[id]` | `apps/web/app/admin/lab/experiments/[id]/page.tsx` | Platform | Experiment detail, report, candidates, comparison. | `useLabExperiment` → `GET /admin/experiments/:id`; `useLabReport` → `GET /admin/experiments/:id/report`; `useLabCandidates` → `GET /admin/experiments/:id/candidates`; `useLabComparison` → `GET /admin/experiments/:id/comparison` | No | not started |
| `/admin/models` | `apps/web/app/admin/models/page.tsx` | Platform | Model registry + trial/upload indexes. | `useAdminModelRegistry` → `GET /admin/models` | No | not started |
| `/admin/models/client-uploads/[id]` | `apps/web/app/admin/models/client-uploads/[id]/page.tsx` | Platform | Full Labs upload technical trail. | `useAdminClientUpload` → `GET /admin/client-uploads/:id`; `downloadAdminRunPredictions` → `GET /admin/client-uploads/:id/predictions.csv` | No | not started |
| `/admin/models/client-trials/[id]` | `apps/web/app/admin/models/client-trials/[id]/page.tsx` | Platform | Catalog trial audit payload. | `useAdminClientTrialAudit` → `GET /admin/models/client-trials/:id` | No | not started |
| `/admin/monitoring` | `apps/web/app/admin/monitoring/page.tsx` | Platform | Platform monitoring overview. | `useAdminMonitoring` → `GET /admin/monitoring` | No | not started |
| `/admin/organizations` | `apps/web/app/admin/organizations/page.tsx` | Platform | Organization list (legacy admin surface). | `useAdminOrganizations` → `GET /admin/organizations` | No | not started |
| `/admin/organizations/[id]` | `apps/web/app/admin/organizations/[id]/page.tsx` | Platform | Organization detail. | `useAdminOrganization` → `GET /admin/organizations/:id` | No | not started |

## Business administration `/business` routes

Middleware: platform roles **or** business-administration / workspace roles listed above. `client_user` is 403.

| URL | Page file | Access | Primary purpose | Data hook / API | Shared | Migration |
| --- | --- | --- | --- | --- | --- | --- |
| `/business` | `apps/web/app/business/page.tsx` | Business admin area | List workspaces the actor can administer. | `useBusinessWorkspaces` → `GET /business/workspaces` | No | not started |
| `/business/workspaces/[businessId]` | see re-exports | Business admin area | Same UI as platform business detail. | `usePlatformBusiness(..., true)` → `GET /business/workspaces/:id` | Yes | not started |
| `/business/workspaces/[businessId]/domains/[domainId]` | see re-exports | Business admin area | Domain detail. | `GET /business/workspaces/:id/domains/:domainId` | Yes | not started |
| `/business/workspaces/[businessId]/workflows/[workflowId]` | see re-exports | Business admin area | Workflow detail. | `GET /business/workspaces/:id/workflows/:workflowId` | Yes | not started |
| `/business/workspaces/[businessId]/workflow-runs/[runId]` | see re-exports | Business admin area | Workflow run detail. | `GET /business/workspaces/:id/workflow-runs/:runId` | Yes | not started |
| `/business/workspaces/[businessId]/models/[modelId]` | see re-exports | Business admin area | Model detail. | `GET /business/workspaces/:id/models/:modelId` | Yes | not started |
| `/business/workspaces/[businessId]/pipeline-runs/[pipelineId]/monitor` | see re-exports | Business admin area | Pipeline monitor. | `GET /business/workspaces/:id/pipeline-runs/:pipelineId/monitor` | Yes | not started |

## Backend surfaces with no matching frontend page

These exist on FastAPI (`apps/api/app/main.py`) and must not be invented again. They are **not** current Next routes:

- `POST /auth/register`, `POST /workspaces/personal`, `POST /workspaces/business`, workspace members, projects, problem specs
- Workspace technical explorer (`/workspaces/:id/explorer/…`)
- Workspace / admin reproducibility routes
- Admin technical explorer (`/admin/explorer/…`)
- Simulations (`/admin` lab/simulations tree)

Treat those as future frontend work only when a later phase explicitly maps them. Do not add fake pages in Phase 0.

## Layout files

| File | Role |
| --- | --- |
| `apps/web/app/layout.tsx` | Root layout: fonts, `QueryProvider`, `RouteShell` |
| No nested `layout.tsx` | Product vs marketing split is client-side in `RouteShell` |
| No `loading.tsx` / `error.tsx` / `not-found.tsx` in `app/` | Next default `/_not-found` only |
