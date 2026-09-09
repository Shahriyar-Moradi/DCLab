# Base44 → DCLab feature coverage matrix

Base44 (`https://convivial-decide-with-clarity.base44.app`) is a **visual/UX reference**. DCLab FastAPI, Postgres, React Query hooks, and Next routes are the functional source of truth.

Phase 14 is a **contract audit**, not a visual redesign. Backend paths come from FastAPI decorators in `apps/api/app/api/` and mounts in `apps/api/app/main.py`, not from product docs.

## How to read classifications

| Class | Meaning |
| --- | --- |
| **REAL** | FastAPI route exists, is mounted, and is backed by a service (and usually a table). Connect the UI to it. Do not invent a second API. |
| **FRONTEND-ONLY** | Chrome, layout, or client state. No API. |
| **FUTURE-BACKEND** | Reference UI implies persistence or a job that **does not exist** on FastAPI today. Honest empty/omitted UI only; do not fake writes or metrics. |
| **NOT-APPLICABLE** | Base44-platform or demo-product concept. Do not copy. |

`Authorization` is the **API** guard. Next `middleware.ts` additionally requires a JWT cookie for `/app`, `/lab`, `/admin`, `/business`.

`/app` tree: `require_client` → workspace read on GET, workspace write (`require_workspace_admin`) on mutating methods (`apps/api/app/api/deps.py`).  
`/admin` tree: `require_admin` → platform read on GET, `dclab_admin` on writes.  
`/business` tree: `require_business_administration` + `require_workspace_read` (and per-route capability checks).

The UI does **not** send `X-Workspace-Id`. Platform members on `/app` resolve to `DEFAULT_WORKSPACE_ID`. Business members resolve via membership / `users.workspace_id`. A workspace switcher is FUTURE-BACKEND.

---

## Phase 14 audit method (2026-09-05)

### 14.1 Frontend API inventory

All product HTTP goes through `apps/web/lib/infrastructure/api-client.ts`:

- `apiGet` / `apiPost` / `apiPostForm` / `uploadFile` (XHR `FormData` field **`file`**) / `apiDownload`
- `Authorization: Bearer` from cookie `dclab_token` (`readToken()`)
- 401 clears the cookie
- Success bodies parsed with Zod (`safeParse`; mismatch surfaces as `ApiError`)

Hooks: `apps/web/lib/application/hooks.ts`. Session: cookie JWT decode (`useSession` / `readSessionUser`). **`GET /auth/me` is unused by the UI** (REAL, API-only).

Page-local `apiGet` (not a named hook): dataset GET + profile GET on `/admin/lab/datasets/[id]`.

Query defaults (`query-provider.tsx`): `staleTime: 15_000`, `retry: 1`, `refetchOnWindowFocus: false`. `useHealth` polls 15s, `retry: 0`.

### 14.2 Backend router inventory

Mounted in `apps/api/app/main.py`: `/auth`, `/workspaces` (+ explorer/reproducibility), `/app`, `/admin`, `/business`, `/health`.

Every UI call was checked against the FastAPI decorator (method, path, params, body/form, authz, response model). No invented routes.

### 14.3 Persistence (mutations)

| UI mutation | Route | Service commit | Read-back |
| --- | --- | --- | --- |
| Login | `POST /auth/login` | none (auth only) | JWT + `UserRead` |
| Opportunity CSV | `POST /app/opportunities/upload` | `ingest_opportunities_csv` then **route** `db.commit()` | `OpportunityUploadResult` counts |
| Decision generate | `POST /app/decisions/generate` | `generate_service.generate_one` `db.commit()` + `db.refresh` | `DecisionGenerateResponse` |
| Labs trial | `POST /app/labs/runs` | `client_lab_service` commit (success + `ClientLabRunAudit`) | `ClientLabRunRead` |
| Labs upload | `POST /app/labs/uploads` | `save_upload` `db.commit()` then enqueue train | `ClientLabUploadRead` (queued) |
| Dataset upload | `POST /admin/datasets/upload` | `ingest_dataset` + `profile_dataset` commit | `DatasetRead` |
| Sample workbook | `POST /admin/datasets/sample-workbook` | `ingest_sample_workbook` → ingest + profile commit | `DatasetRead` |
| Train use case | `POST /admin/datasets/{id}/use-cases/{slug}/train` | `create_experiment` + `execute_experiment` commit | `experiment_payload` |
| Task from YAML | `POST /admin/tasks/from-config` | `upsert_task` commit + refresh | `TaskRead` |
| Deep audit | `POST .../verification/deep` | `request_pipeline_verification` commit + refresh | `VerificationAttemptResponse` |

HTTP 200 alone was not treated as proof; the commit/refresh path was traced.

### 14.4 React Query

| Behavior | Finding |
| --- | --- |
| Query keys | Resource-scoped (`opportunities`, `decisions`, `client-labs`, `lab`, `admin`, `platform`/`business`) |
| `enabled` | Detail hooks require ids; quota requires `useCase`; overview requires signed-in hero |
| Polling | Lab upload 1s while `queued`/`processing` or `progress=looking`; admin client-upload 1s on stored in-progress stages; pipeline monitor 1s until completed/failed/skipped; health 15s |
| Invalidation | Generate, uploads, trials, dataset write, train, task-from-config (fixed this phase) |
| Pagination | Opportunities/decisions `limit`/`offset` (API max 100). Overview walks decision pages up to 500 |
| Loading / errors | Pages use `isPending` / `ErrorState` / mutation `isError` |

### 14.5 Fixes applied this phase (genuine mismatches only)

| Issue | Fix |
| --- | --- |
| `OrganizationSummary.id` is a **workspace** id. Demo `DEFAULT_WORKSPACE_ID` (`00000000-…-0001`) fails Zod `z.uuid()`. Same bug already documented for `AdminClientUploadSummary.workspace_id`. | `OrganizationSummarySchema.id` → `z.guid()` |
| `POST /admin/tasks/from-config` did not invalidate `["lab","tasks"]` | `useCreateLabTaskFromConfig` + create page uses it |

No backend contracts were redesigned. Missing backends stay FUTURE-BACKEND.

---

## Feature coverage

Columns: Feature · UI route · Component · Classification · Endpoint · Method · Hook · Backend route file · Service · Database model/table · Authorization · Verified · Issue

### Authentication, session, health

| Feature | UI route | Component | Classification | Endpoint | Method | Hook | Backend route file | Service | Database model/table | Authorization | Verified | Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Email/password login | `/login` | `app/login/page.tsx` | REAL | `/auth/login` | POST | `useLogin` | `apps/api/app/api/auth.py` | `auth_service.authenticate` + `create_access_token` | `users` | Public | YES | Body `{email,password}`; Zod `LoginResponseSchema` (`user.id`/`workspace_id` as strings so sentinel workspace ids parse) |
| Session display | product shell, `/app/settings` | `AppSidebar`, `app/app/settings/page.tsx` | REAL (read from JWT) | `/auth/me` exists; UI does not call it | GET | `useSession` | `apps/api/app/api/auth.py` | `user_from_token` | `users` | Bearer | YES | Cookie JWT claims. `/me` is API-only |
| Sign out | product shell | `AppSidebar` | FRONTEND-ONLY | — | — | `useSession.signOut` | — | — | — | — | YES | Clears cookie; no logout route |
| Register | **no page** | none | REAL (API-only) | `/auth/register` | POST | none | `auth.py` | `register_customer` + commit | `users` | Public | YES | No Next page; do not invent one here |
| Health | marketing footer | `HealthPill` | REAL | `/health` | GET | `useHealth` | `apps/api/app/main.py` | DB `SELECT 1` | Postgres ping | Public | YES | Poll 15s; 503 → unreachable |
| Password reset / Google SSO | none | **omitted** | FUTURE-BACKEND / NOT-APPLICABLE | none | — | none | — | — | — | — | YES | Not in `auth.py` |
| Workspace header switcher | none | **omitted** | FUTURE-BACKEND | optional `X-Workspace-Id` | — | none | `deps.py` / `authorization_service.resolve_workspace_access` | — | `workspace_memberships` | Bearer | YES | UI does not send the header. Multi-workspace select is not built |

### Opportunities, decisions, insights

| Feature | UI route | Component | Classification | Endpoint | Method | Hook | Backend route file | Service | Database model/table | Authorization | Verified | Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Opportunity list | `/app/opportunities` | `app/app/opportunities/page.tsx` | REAL | `/app/opportunities` | GET | `useOpportunities` | `apps/api/app/api/opportunities.py` | `opportunity_query.list_opportunities` | `opportunities` | `/app` workspace read | YES | Query `limit`/`offset`/`stage`/`sort`/`order` match FastAPI |
| Opportunity detail | `/app/opportunities/[id]` | `app/app/opportunities/[id]/page.tsx` | REAL | `/app/opportunities/{opportunity_id}` | GET | `useOpportunity` | `opportunities.py` | `get_opportunity` (UUID **or** `external_id`) | `opportunities` | workspace read | YES | List links use `external_id` |
| Opportunity upload | `/app/opportunities/upload` | `app/app/opportunities/upload/page.tsx` | REAL | `/app/opportunities/upload` | POST multipart `file` | `useUploadOpportunities` | `opportunities.py` | `ingest_opportunities_csv` + route commit | `opportunities` | workspace write | YES | Invalidates opportunities + overview |
| Decision list | `/app/decisions` | `app/app/decisions/page.tsx` | REAL | `/app/decisions` | GET | `useDecisions` | `apps/api/app/api/decisions.py` | `decision_query.list_decisions` | `decisions` | workspace read | YES | Query `action` aliases `recommended_action`; page-local sort/filter FRONTEND-ONLY |
| Decision detail | `/app/decisions/[id]` | `app/app/decisions/[id]/page.tsx` | REAL | `/app/decisions/{decision_id}` | GET | `useDecision` | `decisions.py` | `get_decision` + `serialize_decision` | `decisions` | workspace read | YES | Path is UUID |
| Decision generate | opportunity detail | same | REAL | `/app/decisions/generate` | POST `{opportunity_id}` | `useGenerateDecision` | `decisions.py` | `generate_service.generate_decisions` commit | `decisions`, `predictions` | workspace write | YES | `opportunity_id` may be external_id; response `opportunity_id` is external_id |
| Dashboard snapshot | `/app/dashboards`, marketing hero | dashboards page, `app/page.tsx` | REAL data + FRONTEND-ONLY chrome | `/app/opportunities`, `/app/decisions` | GET | `useOverviewSnapshot` | opportunities + decisions | list queries | `opportunities`, `decisions` | workspace read | YES | Decision pages capped at 500; truncated flag |
| Insights | `/app/insights` | `app/app/insights/page.tsx` | REAL | `/app/insights` | GET | `useInsights` | `apps/api/app/api/insights.py` | `insight_query.list_client_insights` | `simulation_runs` (translated) | workspace read (guard); query is **not** workspace-filtered | YES | Backend fact: latest simulation per use case. Do not invent a client write API |
| Decision approval PATCH | none | **omitted** | FUTURE-BACKEND | none | — | none | — | — | — | — | YES | Generate/list/detail only |
| Outcomes predicted vs actual | none | **omitted** | FUTURE-BACKEND | none | — | none | — | — | — | — | YES | |

### Client Labs (problems, quota, trials, uploads, download)

| Feature | UI route | Component | Classification | Endpoint | Method | Hook | Backend route file | Service | Database model/table | Authorization | Verified | Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Labs problems catalog | `/app/labs` | `ProblemWorkspace` | REAL | `/app/labs/problems` | GET | `useLabProblems` | `apps/api/app/api/client_labs.py` | `client_lab_service.list_problems` | in-code catalog | workspace read | YES | Eight fixed use cases |
| Labs quota | `/app/labs` | `ProblemWorkspace` | REAL | `/app/labs/problems/{use_case}/quota` | GET | `useLabQuota` | `client_labs.py` | `get_quota` | `client_lab_runs` | workspace read | YES | `enabled: Boolean(useCase)` |
| Labs trial list | `/app/labs` | `ProblemWorkspace` | REAL | `/app/labs/runs` | GET `use_case` | `useLabRuns` | `client_labs.py` | `list_runs` | `client_lab_runs` | workspace read | YES | |
| Labs trial create | `/app/labs` | `ProblemWorkspace` | REAL | `/app/labs/runs` | POST form `use_case` + optional `file` | `useRunLabTrial` | `client_labs.py` | `run_trial` commit | `client_lab_runs`, `client_lab_run_audits` | workspace write | YES | Invalidates runs + quota |
| Labs trial detail | **no dedicated page** | none | REAL (API-only) | `/app/labs/runs/{run_id}` | GET | `useLabRun` | `client_labs.py` | `get_run` | `client_lab_runs` | workspace read | YES | Hook exists; UI uses upload run pages for custom-box jobs |
| Labs uploads list | `/app/labs` | `OpenDatasetPanel` | REAL | `/app/labs/uploads` | GET `category` | `useLabUploads` | `client_labs.py` | `list_uploads` | `client_lab_uploads` | workspace read | YES | |
| Labs upload create | `/app/labs` | `OpenDatasetPanel` | REAL | `/app/labs/uploads` | POST form `category`,`file`, optional `target_column` | `useUploadLabFile` | `client_labs.py` | `save_upload` commit + enqueue | `client_lab_uploads`, datasets, lineage | workspace write | YES | Optional `project_id`/`problem_spec_id` unused by UI (API-only extras) |
| Labs upload poll | `/lab/runs/[run_id]` | `app/lab/runs/[run_id]/page.tsx` | REAL | `/app/labs/uploads/{upload_id}` | GET | `useLabUpload` | `client_labs.py` | `get_upload` (id **or** `run_id`) | `client_lab_uploads` | workspace read | YES | 1s poll while queued/processing/looking |
| Predictions download (client) | `/lab/runs/[run_id]` | same | REAL | `/app/labs/uploads/{upload_id}/predictions.csv` | GET | `downloadLabPredictions` | `client_labs.py` | `predictions_download` | upload outcome / predictions | workspace read + `PREDICTION_DOWNLOAD` | YES | UI downloads `run.run_id`; service accepts id or run_id |
| Custom NLP prediction | none | none | NOT-APPLICABLE | none | — | none | — | — | — | — | YES | |

### Admin Lab, registry, monitoring, organizations

| Feature | UI route | Component | Classification | Endpoint | Method | Hook | Backend route file | Service | Database model/table | Authorization | Verified | Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Admin lab home | `/admin/lab` | `app/admin/lab/page.tsx` | REAL | `/admin/environments`, `/admin/datasets`, `/admin/tasks`, `/admin/experiments`, `/admin/client-uploads` | GET | `useLabEnvironments`, `useLabDatasets`, `useLabTasks`, `useLabExperiments`, `useAdminClientUploads` | `lab.py`, `admin_client_uploads.py` | list queries | `environments`, `datasets`, `prediction_tasks`, `experiments`, `client_lab_uploads` | platform read | YES | `POST /admin/environments/dogfood` unused by UI |
| Dataset upload | `/admin/lab/datasets` | datasets page | REAL | `/admin/datasets/upload` | POST `file` + query `name` | `useUploadLabDataset` | `lab.py` | `ingest_dataset` + `profile_dataset` | `datasets`, `dataset_profiles` | `dclab_admin` | YES | |
| Sample workbook | `/admin/lab/datasets` | datasets page | REAL | `/admin/datasets/sample-workbook` | POST | `useCreateLabWorkbook` | `lab.py` | `ingest_sample_workbook` | `datasets` | `dclab_admin` | YES | Empty JSON `{}` ignored |
| Dataset detail + profile | `/admin/lab/datasets/[id]` | dataset page | REAL | `GET /admin/datasets/{id}`, `GET /admin/datasets/{id}/profile` | GET | page `useQuery` | `lab.py` | `db.get(Dataset)`, latest `DatasetProfile` | `datasets`, `dataset_profiles` | platform read | YES | Profile 404 rendered as “no profile yet”. `POST .../profile` unused |
| Use-case plan + train | same | same | REAL | `GET .../use-cases`, `POST .../use-cases/{slug}/train` | GET/POST `{max_models:5}` | `useLabUseCasePlan`, `useTrainLabUseCase` | `lab.py` | `plan_dataset_use_cases`, `train_dataset_use_case` | `experiments`, tasks | read / `dclab_admin` | YES | Train-all loops per-slug; `POST .../train` (batch) unused |
| Task list | `/admin/lab/tasks` | tasks page | REAL | `/admin/tasks` | GET | `useLabTasks` | `lab.py` | query `PredictionTask` | `prediction_tasks` | platform read | YES | `GET /admin/tasks/{id}` unused |
| Task from YAML | `/admin/lab/tasks/create` | create page | REAL | `/admin/tasks/from-config?path=` | POST | `useCreateLabTaskFromConfig` | `lab.py` | `upsert_task` commit | `prediction_tasks` | `dclab_admin` | YES | Invalidation added this phase |
| Experiment list/detail | `/admin/lab/experiments`, `/[id]` | those pages | REAL | `/admin/experiments`, `/{id}`, `/report`, `/candidates`, `/comparison` | GET | `useLabExperiments`, `useLabExperiment`, `useLabReport`, `useLabCandidates`, `useLabComparison` | `lab.py` | `experiment_payload` + result slices | `experiments`, `experiment_candidates` | platform read | YES | Extra REAL subresources unused: metrics/models/ensemble/feature-*/predictions/errors; `POST /experiments`, `POST /experiments/{id}/run` |
| Model registry | `/admin/models` | models page | REAL | `/admin/models` | GET | `useAdminModelRegistry` | `admin_model_registry.py` | `list_registered_models` | experiments, simulations, client trial audits | platform read | YES | |
| Client trial audit | `/admin/models/client-trials/[id]` | that page | REAL | `/admin/models/client-trials/{audit_id}` | GET | `useAdminClientTrialAudit` | `admin_model_registry.py` | `get_client_trial_audit` | `client_lab_run_audits` | platform read | YES | |
| Client upload trail | `/admin/models/client-uploads/[id]` | that page | REAL | `/admin/client-uploads/{id}` | GET | `useAdminClientUpload` | `admin_client_uploads.py` | `get_client_upload` | `client_lab_uploads` | platform read | YES | Polls stored fine-grained `pipeline_status` |
| Admin predictions CSV | same | same | REAL | `/admin/client-uploads/{upload_id}/predictions.csv` | GET | `downloadAdminRunPredictions` | `admin_client_uploads.py` | `predictions_download` (`db.get` by upload **id**) | experiment test predictions | platform read | YES | UI passes `upload.id` |
| Admin report.docx | none | none | REAL (API-only) | `/admin/client-uploads/{id}/report.docx` | GET | none | `admin_client_uploads.py` | `technical_report_download` | verification/report artifacts | platform read | YES | No Next download button |
| Monitoring | `/admin/monitoring` | monitoring page | REAL | `/admin/monitoring` | GET | `useAdminMonitoring` | `admin_monitoring.py` | `get_monitoring_overview` | experiments, datasets, retrains | platform read | YES | Drift note is honest-absent, not faked |
| Organizations list/detail | `/admin/organizations`, `/[id]` | those pages | REAL | `/admin/organizations`, `/{workspace_id}` | GET | `useAdminOrganizations`, `useAdminOrganization` | `admin_organizations.py` | `list_organizations` / `get_organization` | `workspaces` + counts | platform read | YES | Zod `id` is `z.guid()` (workspace PK / sentinel). Fixed this phase |

### Business + platform explorer, pipeline monitor

| Feature | UI route | Component | Classification | Endpoint | Method | Hook | Backend route file | Service | Database model/table | Authorization | Verified | Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Platform businesses list | `/admin/businesses` | `WorkspaceList` | REAL | `/admin/businesses` | GET | `usePlatformBusinesses` | `platform_explorer.py` | `list_businesses` | `workspaces` + lineage counts | platform read | YES | Workspace id `z.guid()` |
| Platform business detail | `/admin/businesses/[businessId]` | `WorkspaceExplorer` | REAL | `/admin/businesses/{workspace_id}` | GET | `usePlatformBusiness` | `platform_explorer.py` | `get_business` | workspace + domains/workflows/models/runs/memberships | platform read | YES | |
| Domain / workflow / run / model | nested admin routes | explorer components | REAL | `/admin/businesses/{id}/domains\|workflows\|workflow-runs\|models/{id}` | GET | `usePlatformDomain`, `usePlatformWorkflow`, `usePlatformWorkflowRun`, `usePlatformModel` | `platform_explorer.py` | matching getters | lineage tables | platform read | YES | |
| Platform pipeline monitor | `/admin/pipeline-runs/[pipelineId]/monitor` | `PipelineMonitorView` | REAL | `/admin/pipeline-runs/{experiment_id}/monitor` | GET | `usePipelineMonitor` | `platform_explorer.py` | `get_pipeline_monitor` | pipeline runs, events, candidates | platform read | YES | Poll until terminal |
| Business workspaces list | `/business` | `business/page.tsx` | REAL | `/business/workspaces` | GET | `useBusinessWorkspaces` | `business_explorer.py` | `list_workspaces` | memberships + workspaces | business administration | YES | |
| Business nested explorer | `/business/workspaces/[businessId]/...` | re-exports of admin explorer pages | REAL | `/business/workspaces/{id}/...` | GET | same hooks with `businessMode` | `business_explorer.py` | `business_explorer_service` | same lineage | business admin + workspace read; model needs `MODEL_MANAGEMENT` | YES | `/business/workspaces/...` pages re-export `/admin/businesses/...` |
| Business pipeline monitor | `/business/workspaces/[businessId]/pipeline-runs/[pipelineId]/monitor` | same monitor page | REAL | `/business/workspaces/{id}/pipeline-runs/{experiment_id}/monitor` | GET | `usePipelineMonitor(id, businessId)` | `business_explorer.py` | `get_pipeline_monitor` | same | + `PIPELINE_MONITOR` | YES | |
| Deep audit | monitor page | `PipelineMonitorView` | REAL | `/admin/lab/runs/{run_id}/verification/deep` or `/business/workspaces/{id}/lab-runs/{run_id}/verification/deep` | POST `{}` | `useBusinessDeepAudit` | `admin_ml_verifications.py` / `business_explorer.py` | `request_pipeline_verification` commit | `ml_run_verifications`, `client_lab_uploads` | admin write / business write + `OPENAI_PIPELINE_AUDIT` + `DEEP_AUDIT` | YES | `run_id` is `hierarchy.source_upload.id`; lookup by upload id or `run_id`. Page refetches monitor on success |
| Business predictions.csv | none dedicated | none | REAL (API-only) | `/business/workspaces/{id}/client-uploads/{upload_id}/predictions.csv` | GET | none | `business_explorer.py` | `predictions_download` | same | + `PREDICTION_DOWNLOAD` | YES | Client UI uses `/app/labs/uploads/...` instead |
| Model deploy | none | **omitted** | FUTURE-BACKEND | none | — | none | — | — | — | — | YES | |
| Technical explorer / artifacts / observatory raw | **no page** | none | REAL (API-only) | `/workspaces/{id}/explorer/*`, `/admin/explorer/*`, reproducibility, `/admin/observatory/*`, `/business/observatory/*` | GET | none | `technical_explorer.py`, `reproducibility.py`, `observability.py` | matching services | lineage / artifacts / events | Bearer + membership / platform / capabilities | YES | Monitor aggregates observatory; do not invent a second event API |
| Workspace/project/problem-spec CRUD | **no page** | none | REAL (API-only) | `/workspaces/...` | GET/POST | none | `workspaces.py` | workspace services | `workspaces`, `projects`, `problem_specs` | Bearer + identity | YES | Not wired; identity-sensitive |
| Admin simulations | **no page** | none | REAL (API-only) | `/admin/simulations/run`, `/runs`, `/runs/{id}`, `.../decisions/{external_id}` | POST/GET | none | `simulations.py` | simulation service | `simulation_runs` | platform | YES | Feeds `/app/insights`. No customer simulation wizard |
| CRM connectors | none | **omitted** | FUTURE-BACKEND | none mounted | — | none | — | `data_source_service` (internal ingest) | `data_sources` | — | YES | Table exists; **no router in `main.py`** |
| Notifications / billing / record search index | none | **omitted** | FUTURE-BACKEND / NOT-APPLICABLE | none | — | none | — | — | — | — | YES | |

### FRONTEND-ONLY chrome (no API)

| Feature | UI route | Component | Classification | Endpoint | Method | Hook | Backend route file | Service | Database model/table | Authorization | Verified | Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Marketing site | `/`, `/platform`, `/solutions`, `/industries`, `/pricing`, `/company`, `/resources` | marketing pages | FRONTEND-ONLY | — | — | `useOverviewSnapshot` on `/` if signed in | — | — | — | Public | YES | |
| Sidebar collapse, ⌘K destinations, local search/sort | product shell / lists | `AppSidebar`, `CommandPalette`, `CollectionSearch` | FRONTEND-ONLY | — | — | none | — | — | — | Cookie JWT for product | YES | Palette is destinations only, not a record index |
| Account page | `/app/settings` | settings page | REAL read / FUTURE-BACKEND writes | JWT only | — | `useSession` | — | — | — | Cookie | YES | No Save / invite / billing |

### NOT-APPLICABLE (do not copy)

Customer 360 CRM, NLP custom prediction, six AI agents, fabricated dashboard KPIs, Base44 “Demo Company / Production” switcher, Google SSO, product billing.

---

## Backend family audit (decorators)

| Family | Prefix | UI today |
| --- | --- | --- |
| Auth | `/auth` | login + JWT session |
| Workspaces | `/workspaces` | none |
| App opportunities / decisions / insights / labs | `/app/...` | yes |
| Admin lab / simulations / orgs / models / monitoring / client-uploads / verifications / observatory / explorer | `/admin/...` | partial (simulations, raw observatory, extra lab POSTs unused) |
| Business explorer | `/business/workspaces...` | yes |
| Health | `/health` | `HealthPill` |

**Not mounted as HTTP:** CRM connectors, model deploy, OAuth, password reset, decision approval, outcomes loop, notifications, billing.

---

**STOP.** Phase 14 is complete. Do not start a later phase from this matrix update.
