# Base44 → DCLab feature coverage matrix (Phase 1)

Base44 (`https://convivial-decide-with-clarity.base44.app`) is a **visual/UX reference**. DCLab FastAPI, Postgres, React Query hooks, and Next routes are the functional source of truth.

No page redesigns were made for this audit. Backend paths below come from FastAPI decorators in `apps/api/app/api/` and mounts in `apps/api/app/main.py`, not from product docs.

## How to read classifications

| Class | Meaning |
| --- | --- |
| **REAL** | FastAPI route exists, is mounted, and is backed by a service (and usually a table). Connect the UI to it. Do not invent a second API. |
| **FRONTEND-ONLY** | Chrome, layout, or client state. No API. |
| **FUTURE-BACKEND** | Reference UI implies persistence or a job that **does not exist** on FastAPI today. You may scaffold honest empty UI later; you may not fake writes or metrics. |
| **NOT-APPLICABLE** | Base44-platform or demo-product concept. Do not copy. |

`Authorization` is the **API** guard. Next `middleware.ts` additionally requires a JWT cookie for `/app`, `/lab`, `/admin`, `/business`.

`/app` tree: `require_client` → workspace read on GET, workspace write on mutating methods (`apps/api/app/api/deps.py`).  
`/admin` tree: `require_admin` → platform read on GET, `dclab_admin` on writes.  
`/business` tree: `require_business_administration` + `require_workspace_read` (and per-route capability checks).

`Frontend hook`: existing export in `apps/web/lib/application/hooks.ts` unless noted **none**.

---

## Snapshot

| Class | What it covers in this matrix |
| --- | --- |
| REAL, UI already wired | Login, opportunities, decisions generate/list/detail, insights, client labs, admin lab/registry/monitoring/organizations, platform + business explorers, pipeline monitor, uploads/predictions download |
| REAL, API only (no Next page) | `POST /auth/register`, `GET /auth/me`, workspace/project/problem-spec CRUD, technical explorer, reproducibility/artifacts, admin simulations, observatory event/LLM routes, several lab experiment subresources |
| FRONTEND-ONLY | Sidebar collapse, search ⌘K chrome, glass topbar, chip filters, marketing layout |
| FUTURE-BACKEND | Password reset, Google SSO if ever desired, decision **approval** workflow, predicted-vs-actual outcomes loop, model **deploy**, CRM connector admin UI, command palette, billing/seats UI, register/create-workspace **pages** |
| NOT-APPLICABLE | Base44 entity auth, demo customer 360 IDs, NLP custom prediction, six AI agents, fabricated dashboard KPIs, Base44 “Demo Company / Production” switcher |

---

## Shell, navigation, marketing, auth

| Feature | Base44 location | DCLab route | DCLab frontend | Class | Endpoint | HTTP | Hook | DB | Authz | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Marketing landing | `/` + hash sections | `/` | `app/page.tsx`, `SiteHeader` | FRONTEND-ONLY | — | — | `useOverviewSnapshot` on hero if signed in | Partial | Public | Marketing copy is static. Hero may call opportunity/decision list when a session exists. Do not fetch Base44. |
| Marketing platform / solutions / pricing / company / resources | `/` anchors | `/platform`, `/solutions`, `/industries`, `/pricing`, `/company`, `/resources` | matching `page.tsx` | FRONTEND-ONLY | — | — | none | NO | Public | DCLab uses separate routes; Base44 uses one page. |
| Case studies (marketing) | `/case-studies`, `/case-studies/:caseId` | `/resources` (nearest) | `app/resources/page.tsx` | FRONTEND-ONLY | — | — | none | NO | Public | Demo walkthroughs in Base44 are not DCLab case-study findings docs. |
| Product sidebar + groups | Authenticated shell | `/app/*`, `/lab/*`, `/admin/*`, `/business/*` | `AppShell`, `AppSidebar`, `app-navigation.ts` | FRONTEND-ONLY | — | — | `useSession` | NO | Cookie JWT | IA differs: DCLab groups Workspace / ML Workspace / Platform / Business. |
| Sidebar collapse 240↔68 | Collapse control | none as toggle | `AppSidebar` (no collapse today) | FRONTEND-ONLY | — | — | none | NO | — | Layout-only. |
| Active nav state | Path prefix match | all product routes | `app-nav-item-active` | FRONTEND-ONLY | — | — | none | NO | — | |
| Account name/role | Topbar avatar | all product routes | `AppSidebar` account + `roleLabel` | REAL | `GET /auth/me` exists; UI uses JWT cookie user | GET | `useSession` | YES | Bearer | Session is cookie `dclab_token`. Hook does not have to call `/me` if JWT already has claims. |
| Workspace crumb “Demo Company / Production” | Topbar | none | none | NOT-APPLICABLE | — | — | none | NO | — | Do not fake a tenant switcher. Real workspace create is API-only today. |
| Command palette Search ⌘K | Topbar | none | none | FUTURE-BACKEND | none | — | none | NO | — | No search index API. A local filter on a loaded list is FRONTEND-ONLY if added later. |
| Notification bell | Topbar icon | none | none | FUTURE-BACKEND | none | — | none | NO | — | No notifications table/API. |
| Mobile marketing menu | Header accordion `<lg` | marketing pages | `SiteHeader` | FRONTEND-ONLY | — | — | none | NO | Public | |
| Product mobile nav | Not identified as a drawer in Base44 JS | product routes | `AppMobileDrawer` | FRONTEND-ONLY | — | — | none | NO | Cookie JWT | DCLab already has a drawer; Base44 product shell was a persistent aside. |
| Email/password login | `/login` | `/login` | `app/login/page.tsx` | REAL | `/auth/login` | POST | `useLogin` | YES | Public | Users table. Demo shortcuts on the login page are local fixtures. |
| Google SSO | `/login`, `/register` | none | none | NOT-APPLICABLE | none | — | none | NO | — | No OAuth routes in FastAPI. |
| Register | `/register` | **no page** | none | REAL | `/auth/register` | POST | none | YES | Public | Creates `workspace_owner` with no workspace (`auth.py`). UI missing. |
| Current user | Base44 `User/me` (ignore) | none dedicated | `useSession` | REAL | `/auth/me` | GET | none (cookie) | YES | Bearer | Do not call Base44 entities. |
| Forgot / reset password | `/forgot-password`, `/reset-password` | none | none | FUTURE-BACKEND | none | — | none | NO | — | Not in `auth.py`. |
| Sign out | implied | `/login` after clear | `AppSidebar` `signOut` | FRONTEND-ONLY | — | — | `useSession` | NO | — | Clears cookie; no logout endpoint required. |
| Health | — | unused in chrome | optional | REAL | `/health` | GET | `useHealth` | YES (DB ping) | Public | Not a Base44 screen. |

---

## Intelligence plane (Base44 left-nav)

| Feature | Base44 location | DCLab route | DCLab frontend | Class | Endpoint | HTTP | Hook | DB | Authz | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Dashboard layout / metric cards | `/dashboard` | `/app/dashboards` | `app/app/dashboards/page.tsx` | FRONTEND-ONLY + REAL data | `/app/opportunities`, `/app/decisions` | GET | `useOverviewSnapshot` | YES | `/app` workspace read | **Do not copy Base44 KPI copy** (“12,482 customers…”). Wire counts from opportunities/decisions only. |
| Fabricated CRM KPIs / drift alerts | `/dashboard` | — | — | NOT-APPLICABLE | none | — | none | NO | — | Admin monitoring explicitly reports drift as **unimplemented** (`admin_monitoring_service.py`). |
| Intelligence / prediction layers | `/intelligence`, `/intelligence/predictions/:id` | `/app/insights` | `app/app/insights/page.tsx` | REAL | `/app/insights` | GET | `useInsights` | YES | `/app` workspace read | Insights are translated latest **simulation runs** per use case (`insight_query.py`), not a live CRM prediction store. |
| Recommendations list + chips | `/recommendations`, `/:id` | `/app/decisions` | `app/app/decisions/page.tsx` | REAL | `/app/decisions` | GET | `useDecisions` | YES | `/app` workspace read | Closest object is a **decision**, not a separate recommendation entity. Chip filters = FRONTEND-ONLY on query params (`status`, `action`). |
| Approve / track recommended actions | Decisions subtitle in Base44 | `/app/decisions` | same | FUTURE-BACKEND | none | — | none | NO | — | `GET`/`POST generate` only. No PATCH approve. |
| Decision summary view | `/decisions/summary` | none | none | FRONTEND-ONLY or FUTURE-BACKEND | none extra | — | could reuse `useDecisions` | YES if list | — | A summary layout over existing decisions is FRONTEND-ONLY. New persistence is not. |
| Generate a decision | (implicit) | `/app/opportunities/[id]`, decisions pages | those pages | REAL | `/app/decisions/generate` | POST | `useGenerateDecision` | YES | `/app` workspace write | |
| Decision detail | recommendation/outcome detail vibe | `/app/decisions/[id]` | `app/app/decisions/[id]/page.tsx` | REAL | `/app/decisions/{id}` | GET | `useDecision` | YES | `/app` workspace read | |
| Counterfactual simulation UI | `/simulation`, `/guided-simulation`, `/walkthrough/churn` | none customer | none | FUTURE-BACKEND (customer) / REAL (admin API) | `/admin/simulations/run`, `/admin/simulations/runs`, `/admin/simulations/runs/{id}`, `/admin/simulations/runs/{id}/decisions/{external_id}` | POST/GET | none | YES (`simulation_runs`) | Platform (`/admin`) | Customer “configure actions and simulate” is not an `/app` API. Admin harness exists; no Next page. Do not expose raw engine on `/app`. |
| Outcomes predicted vs actual | `/outcomes`, `/outcomes/:id` | none | none | FUTURE-BACKEND | none | — | none | NO | — | No outcome-loop table/API. Monitoring uses real consecutive retrain **metric deltas**, which is a different concept (`GET /admin/monitoring`). |
| Charts on intelligence pages | dashboard / intelligence | dashboards / monitoring | page-local | FRONTEND-ONLY | only if series come from REAL endpoints | — | existing list hooks only | — | — | Chart chrome is frontend. Series must be real fields (e.g. decision expected_revenue), never invented. |

---

## Data, models, custom prediction, customers

| Feature | Base44 location | DCLab route | DCLab frontend | Class | Endpoint | HTTP | Hook | DB | Authz | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Connected data sources (CRM, billing, …) | `/data`, `/data/sources/:id` | none | none | FUTURE-BACKEND | none mounted | — | none | YES table `data_sources` | — | `data_source_service.py` exists. **No router in `main.py`.** Labs ingest creates sources internally. Do not invent Salesforce APIs. |
| Feature catalog | `/data/features` | experiment feature-importance | admin experiment detail | REAL (lab) | `/admin/experiments/{id}/feature-importance`, `/feature-groups` | GET | none (detail page uses report/candidates/comparison) | YES | Platform read | Not a customer “feature store” UI. |
| CSV / file upload (client) | Data / custom prediction vibe | `/app/labs`, `/app/opportunities/upload` | `app/app/labs/page.tsx`, `opportunities/upload/page.tsx` | REAL | `/app/labs/uploads`, `/app/opportunities/upload` | POST | `useUploadLabFile`, `useUploadOpportunities` | YES | `/app` workspace write | Two different objects: Labs datasets vs opportunity CSV. |
| Models gallery | `/models`, `/models/:id` | `/admin/models`; `/admin/businesses/.../models/{id}`; `/business/workspaces/.../models/{id}` | registry + explorer pages | REAL | `/admin/models`; `/admin/businesses/{id}/models/{id}`; `/business/workspaces/{id}/models/{id}` | GET | `useAdminModelRegistry`, `usePlatformModel` | YES | Platform or business | Base44 “ensemble layers” copy is demo. DCLab model assets/versions are lineage-backed. |
| Client trial audit | — | `/admin/models/client-trials/[id]` | that page | REAL | `/admin/models/client-trials/{audit_id}` | GET | `useAdminClientTrialAudit` | YES | Platform read | No Base44 equivalent. |
| Deploy model | implied by “production” crumb / models | none | none | FUTURE-BACKEND | none | — | none | NO | — | No deploy/serving API. |
| Custom prediction (plain language) | `/custom-predictions` | none | none | NOT-APPLICABLE | none | — | none | NO | — | NLP/open-ended problem configuration is out of scope. |
| Bounded catalog trial | nearest: custom prediction | `/app/labs` | `app/app/labs/page.tsx` | REAL | `/app/labs/problems`, `/problems/{useCase}/quota`, `/app/labs/runs` | GET/POST | `useLabProblems`, `useLabQuota`, `useRunLabTrial`, `useLabRuns` | Catalog NO (in-code); quota/runs YES | `/app` | Eight fixed use cases. |
| Labs upload + poll + CSV | — | `/app/labs`, `/lab/runs/[run_id]` | those pages | REAL | `/app/labs/uploads`, `/uploads/{id}`, `/uploads/{id}/predictions.csv` | GET/POST | `useLabUploads`, `useLabUpload`, `downloadLabPredictions` | YES | `/app`; download may need `PREDICTION_DOWNLOAD` capability | |
| Customer 360 CRM | `/customers`, `/customers/:id` | none | none | NOT-APPLICABLE | none | — | none | NO | — | Demo `cus-*` routes. Do not build a fake CRM. |
| Opportunity list/detail | nearest operational object | `/app/opportunities`, `/app/opportunities/[id]` | those pages | REAL | `/app/opportunities`, `/app/opportunities/{id}` | GET | `useOpportunities`, `useOpportunity` | YES | `/app` workspace read | |

---

## Operate: monitoring, settings, members

| Feature | Base44 location | DCLab route | DCLab frontend | Class | Endpoint | HTTP | Hook | DB | Authz | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Platform monitoring table | `/monitoring` | `/admin/monitoring` | `app/admin/monitoring/page.tsx` | REAL | `/admin/monitoring` | GET | `useAdminMonitoring` | YES | Platform read | Retrains, metric deltas, dataset profile health. Drift is reported absent, not faked. |
| Settings workspace card | `/settings` | none | none | FUTURE-BACKEND | `POST /workspaces/personal`, `POST /workspaces/business` | POST | none | YES | Bearer + identity | No settings page. APIs exist. |
| Settings account card | `/settings` | none dedicated | sidebar account | REAL (read) | `/auth/me` | GET | `useSession` | YES | Bearer | Display only. Profile PATCH does not exist. |
| Settings “intelligence architecture” demo stats | `/settings` | — | — | NOT-APPLICABLE | none | — | none | NO | — | Hard-coded “1,284 features” etc. in the reference bundle. |
| Invite members / seats | settings-ish | none | none | REAL API / no UI | `POST /workspaces/{id}/members` | POST | none | YES | Bearer; workspace identity rules | Entitlements in `workspace_entitlement_service.py`. No billing API. |
| Billing / plan | labels in demo customer fields | `/pricing` marketing | `app/pricing/page.tsx` | NOT-APPLICABLE as product billing | none | — | none | NO | Public marketing | |

---

## DCLab surfaces Base44 does not have (still in scope for later UI)

| Feature | Base44 location | DCLab route | DCLab frontend | Class | Endpoint | HTTP | Hook | DB | Authz | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Admin lab home | — | `/admin/lab` | `app/admin/lab/page.tsx` | REAL | `/admin/environments`, `/admin/datasets`, `/admin/tasks`, `/admin/experiments`, `/admin/client-uploads` | GET | `useLabEnvironments`, `useLabDatasets`, `useLabTasks`, `useLabExperiments`, `useAdminClientUploads` | YES | Platform read | `POST /admin/environments/dogfood` exists; not necessarily wired. |
| Dataset upload / workbook | — | `/admin/lab/datasets` | that page | REAL | `/admin/datasets/upload`, `/admin/datasets/sample-workbook` | POST | `useUploadLabDataset`, `useCreateLabWorkbook` | YES | Platform **write** (`dclab_admin`) | |
| Dataset profile + train | — | `/admin/lab/datasets/[id]` | that page | REAL | `GET /admin/datasets/{id}`, `GET/POST .../profile`, `GET .../use-cases`, `POST .../use-cases/{slug}/train`, `POST .../train` | GET/POST | `useLabUseCasePlan`, `useTrainLabUseCase` + direct `apiGet` | YES | Platform read/write | |
| Tasks + YAML import | — | `/admin/lab/tasks`, `/create` | those pages | REAL | `/admin/tasks`, `/admin/tasks/{id}`, `/admin/tasks/from-config` | GET/POST | `useLabTasks`; create uses `apiPost` | YES | Platform | |
| Experiment detail | — | `/admin/lab/experiments`, `/[id]` | those pages | REAL | `/admin/experiments`, `/{id}`, `/report`, `/candidates`, `/comparison` | GET | `useLabExperiment`, `useLabReport`, `useLabCandidates`, `useLabComparison` | YES | Platform read | Additional REAL, **no dedicated hooks/pages**: `/metrics`, `/models`, `/ensemble`, `/feature-importance`, `/feature-groups`, `/predictions`, `/errors`; `POST /experiments`, `POST /experiments/{id}/run`. |
| Client upload technical trail | — | `/admin/models/client-uploads/[id]` | that page | REAL | `/admin/client-uploads/{id}`, `/predictions.csv`, `/report.docx` | GET | `useAdminClientUpload`, `downloadAdminRunPredictions` | YES | Platform read | |
| Pipeline monitor | — | `/admin/pipeline-runs/[id]/monitor` (+ business re-export) | shared monitor page | REAL | `/admin/pipeline-runs/{id}/monitor`; `/business/workspaces/{id}/pipeline-runs/{id}/monitor` | GET | `usePipelineMonitor` | YES | Platform or business + `PIPELINE_MONITOR` | Polls until terminal. |
| Deep verification | — | same monitor page | same | REAL | `/admin/lab/runs/{run_id}/verification/deep`; `/business/workspaces/{id}/lab-runs/{run_id}/verification/deep` | POST | `useBusinessDeepAudit` | YES | Platform write / business + capability | Also GET latest/list verification, GET report + docx under `/admin/lab/runs/...`. |
| Platform businesses explorer | — | `/admin/businesses` and nested domain/workflow/run/model | admin business pages | REAL | `/admin/businesses`, `/{id}`, `/domains/{id}`, `/workflows/{id}`, `/workflow-runs/{id}`, `/models/{id}` | GET | `usePlatformBusinesses`, `usePlatformBusiness`, `usePlatformDomain`, `usePlatformWorkflow`, `usePlatformWorkflowRun`, `usePlatformModel` | YES | Platform read (`require_admin` on parent) | |
| Business admin explorer | — | `/business`, `/business/workspaces/[id]/...` | `business/page.tsx` + re-exports | REAL | `/business/workspaces`, nested same shapes, `.../client-uploads/{id}/predictions.csv` | GET | `useBusinessWorkspaces`, same platform hooks with `businessMode` | YES | Business administration + workspace read + capabilities | |
| Organizations (legacy admin) | — | `/admin/organizations`, `/[id]` | those pages | REAL | `/admin/organizations`, `/{workspace_id}` | GET | `useAdminOrganizations`, `useAdminOrganization` | YES | Platform read | Workspace-shaped “org” list. Prefer explorer for new UI. |
| Technical explorer | — | **no page** | none | REAL | `/workspaces/{id}/explorer/projects|workflows|pipeline-runs|candidates|model-versions|datasets` and `/admin/explorer/...` | GET | none | YES | Bearer + membership; admin explorer `require_platform_read` | Mounted in `main.py`. |
| Reproducibility / artifacts | — | **no page** | none | REAL | `/workspaces/{id}/model-versions/{id}/reproducibility`, `/artifacts`, `/artifacts/{id}`, `/download`, `/signed-url`; `/admin/model-versions/...`, `/admin/artifacts/...` | GET | none | YES | Bearer + membership | |
| Observatory events / LLM | used inside monitor conceptually | monitor page (summary only via explorer monitor) | monitor page | REAL | `/admin/observatory/pipeline-runs/{id}/summary|events|events/incremental|llm-invocations`; `/admin/observatory/llm-invocations/{id}`; `/admin/observatory/workflow-runs/{id}/pipelines`; matching `/business/observatory/...` | GET | none except monitor aggregate | YES | Platform / business + observatory capabilities | Do not invent a second event API. |
| Projects / problem specs | — | **no page** | none | REAL | `/workspaces/{id}/projects` GET/POST, `/{project_id}` GET, `/problem-specs` GET/POST, `/{spec_id}` GET | GET/POST | none | YES | Bearer + identity | |
| Showcase primitives | — | `/showcase` | `app/showcase/page.tsx` | FRONTEND-ONLY | — | — | none | NO | Public | Internal scratch; not Base44. |

---

## Backend family audit (decorators, not docs)

Mounted in `apps/api/app/main.py`:

| Family | Prefix | Verified routes | Persistence | UI today |
| --- | --- | --- | --- | --- |
| Auth | `/auth` | `POST /register`, `POST /login`, `GET /me` | `users` | login only |
| Workspaces | `/workspaces` | `POST /personal`, `POST /business`, `POST /{id}/members`, projects + problem-specs | workspaces, memberships, projects, problem_specs | none |
| Technical explorer (workspace) | `/workspaces/{id}/explorer/*` | projects, workflows, pipeline-runs, candidates, model-versions, datasets | canonical lineage tables | none |
| Reproducibility (workspace) | `/workspaces/{id}/model-versions/*`, `/artifacts/*` | reproducibility, artifacts, download, signed-url | artifacts / model versions | none |
| App opportunities | `/app/opportunities` | `POST /upload`, `GET ""`, `GET /{id}` | opportunities | yes |
| App decisions | `/app/decisions` | `POST /generate`, `GET ""`, `GET /{id}` | decisions | yes |
| App insights | `/app/insights` | `GET ""` | derived from `simulation_runs` | yes |
| Client labs | `/app/labs` | problems, quota, runs CRUD-ish, uploads + predictions.csv | lab runs/uploads; problems catalog in code | yes |
| Admin lab | `/admin` (no extra prefix on lab router) | environments, datasets upload/profile/train, tasks, experiments + subresources | lab tables / experiments | partial |
| Simulations | `/admin/simulations` | `POST /run`, `GET /runs`, `GET /runs/{id}`, decision subresource | `simulation_runs` | **no page** |
| Organizations | `/admin/organizations` | list + detail | workspace-backed | yes |
| Model registry | `/admin/models` | list, `GET /client-trials/{audit_id}` | registry + audits | yes |
| Monitoring | `/admin/monitoring` | `GET ""` | experiments, datasets, lab audits, simulations | yes |
| Client uploads (admin) | `/admin/client-uploads` | list, detail, predictions.csv, report.docx | uploads | yes |
| ML verifications | `/admin/lab/runs` | verification GET/POST/deep, report, report.docx | verification attempts | monitor uses deep POST |
| Observatory | `/admin/observatory`, `/business/observatory` | pipeline summary/events/llm, workflow pipelines | ml run events / llm invocations | monitor only (aggregate) |
| Platform explorer | `/admin/businesses`, `/admin/pipeline-runs/{id}/monitor` | hierarchy + monitor | lineage | yes |
| Business explorer | `/business/workspaces...` | same + predictions.csv + deep verification | lineage | yes |
| Admin explorer | `/admin/explorer/*` | workspaces/projects/workflows/pipeline-runs/model-versions/datasets | lineage | none |
| Admin reproducibility | `/admin/model-versions/*`, `/admin/artifacts/*` | same as workspace | artifacts | none |
| Health | `/health` | GET | DB ping | hook only |

**Not mounted as HTTP (do not invent UI that POSTs to them):** CRM connectors, model deploy, OAuth, password reset, decision approval, outcomes loop, notifications, billing. `DataSource` persistence is real for **Labs ingest lineage**, not for a Base44-style integrations page.

---

## Obvious mapping for the next visual phase (do not implement here)

| Base44 screen | DCLab destination | Data rule |
| --- | --- | --- |
| `/dashboard` | `/app/dashboards` | Only opportunity/decision (and later real) totals |
| `/intelligence` | `/app/insights` | Simulation-translated insights |
| `/recommendations` + `/decisions` | `/app/decisions` | Ledger + generate; no approve |
| `/simulation` | Do not put on `/app`. Optional later staff UI on admin simulations | Real `/admin/simulations/*` only |
| `/outcomes` | Omit or honest empty | No API |
| `/data` | Labs upload + admin datasets | No CRM connectors |
| `/models` | Registry + explorer model pages | Lineage models only |
| `/custom-predictions` | `/app/labs` | Catalog + CSV, not NLP |
| `/customers` | Omit or map to opportunities **without** fake 360 | |
| `/monitoring` | `/admin/monitoring` | Real deltas; no fake drift |
| `/settings` | Future workspace/account pages on existing workspace APIs | No demo architecture stats |
| `/login` | `/login` | Email/password only |

**STOP.** Phase 1 deliverable is this matrix plus `docs/BASE44_VISUAL_SYSTEM.md`. No major visual implementation.
