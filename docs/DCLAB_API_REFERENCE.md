# DCLab API Reference

## Basis and notation

This reference is generated from the 94 operations in the runtime FastAPI
OpenAPI document (`app.main:app`) and then augmented by inspecting the parent
router dependencies, handler dependencies, authorization services, and
workspace-scoped queries. It documents the backend as implemented, not intended
UI behavior.

- Roles: `DA` = `dclab_admin`, `DD` = `dclab_developer`, `BA` =
  `business_admin`, `BD` = `business_developer`.
- All protected routes require a bearer token. `DA/DD` are platform
  memberships; `BA/BD` are workspace memberships.
- **Validated context** means `X-Workspace-Id` is validated by `require_client`;
  an authorized primary/single workspace may be inferred. Platform users may
  select any existing workspace and otherwise default to the default workspace.
- On `/admin`, reads allow `DA/DD`; unsafe methods allow only `DA`.
- On `/app`, reads allow `DA/DD/BA/BD` after workspace resolution; unsafe
  methods allow only `DA/BA`. Legacy `client_user` remains compatible on `/app`.
- The parent-mounted `/business/observatory` tree requires
  `require_business_administration` plus `require_workspace_read`, so
  `client_user` receives `403`. Reads allow `DA/DD/BA/BD`; the unsafe deep-audit
  path is not on this parent router.
- On the direct `/business/workspaces` router, all four modern roles enter, but
  each service call applies platform visibility or workspace membership. Its
  unsafe deep-audit operation separately requires workspace write authority.
- Capability flags constrain modern business roles and fail closed. Platform
  roles bypass capability flags. “None” means no capability check exists.
- `Response` means FastAPI's OpenAPI has no typed 200 schema for that operation;
  the media/body stated here comes from the handler.
- Grouping below is functional documentation grouping; it does not add routes
  or change router prefixes.

## Platform administration

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `POST /auth/login` | Public with valid credentials | None | None | `LoginResponse` | Authenticates and signs a bearer token; no persisted write |
| `GET /auth/me` | Any authenticated role | None | None | `UserRead` | None |
| `GET /health` | Public | None | None | OpenAPI untyped object | Executes `SELECT 1`; no write |
| `POST /admin/environments/dogfood` | `DA` | None | None | `EnvironmentRead` | Seeds/upserts the dogfood environment |
| `GET /admin/environments` | `DA/DD` | None | None | `list[EnvironmentRead]` | None |
| `GET /admin/organizations` | `DA/DD` | None; returns platform-wide summaries | None | `list[OrganizationSummary]` | None |
| `GET /admin/organizations/{workspace_id}` | `DA/DD` | Path identifies a workspace; platform-wide access | None | `OrganizationDetail` | None |
| `GET /admin/monitoring` | `DA/DD` | None; platform-wide aggregation | None | `MonitoringOverview` | None |
| `GET /admin/businesses` | `DA/DD` | None; all workspaces | None | `list[BusinessSummaryRead]` | None |
| `GET /admin/businesses/{workspace_id}` | `DA/DD` | Path identifies any workspace | None | `BusinessDetailRead` | None |
| `GET /admin/businesses/{workspace_id}/domains/{domain_id}` | `DA/DD` | Workspace/object pair is checked by the service | None | `DomainDetailRead` | None |

## Business administration

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /business/workspaces` | `DA/DD`: all; `BA/BD`: memberships only | No header required; service derives visible workspaces | None | `list[BusinessWorkspaceSummaryRead]` | None |
| `GET /business/workspaces/{workspace_id}` | `DA/DD` or workspace member | Path workspace must be readable | None | `BusinessWorkspaceDetailRead` | None |
| `GET /business/workspaces/{workspace_id}/domains/{domain_id}` | `DA/DD` or workspace member | Path workspace membership and enabled-domain scope | None | `DomainDetailRead` | None |

## Client/app

Every route in this section uses the validated workspace context. Reads allow
`DA/DD/BA/BD`; posts allow `DA/BA`.

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `POST /app/opportunities/upload` | `DA/BA` | Validated context; rows written into it | None | `OpportunityUploadResult` | Parses CSV and inserts opportunities |
| `GET /app/opportunities` | `DA/DD/BA/BD` | Validated context and query filter | None | `OpportunityListResponse` | None |
| `GET /app/opportunities/{opportunity_id}` | `DA/DD/BA/BD` | Object looked up inside validated context | None | `OpportunityRead` | None |
| `POST /app/decisions/generate` | `DA/BA` | Validated context | None | `DecisionGenerateResponse \| list[DecisionGenerateResponse]` | Generates and persists decisions/predictions for one or all opportunities |
| `GET /app/decisions` | `DA/DD/BA/BD` | Validated context and query filter | None | `DecisionListResponse` | None |
| `GET /app/decisions/{decision_id}` | `DA/DD/BA/BD` | Object looked up inside validated context | None | `DecisionRead` | None |
| `GET /app/insights` | `DA/DD/BA/BD` | Context is validated by parent guard, but handler query is not workspace-filtered | None | `InsightListResponse` | None |
| `GET /app/labs/problems` | `DA/DD/BA/BD` | Context is validated but catalog is global | None | `list[ClientLabProblem]` | None |
| `GET /app/labs/problems/{use_case}/quota` | `DA/DD/BA/BD` | Validated context passed to quota service | None | `ClientLabQuotaRead` | None |
| `POST /app/labs/runs` | `DA/BA` | Validated context | None | `ClientLabRunRead` | Runs and persists a bounded trial, consuming quota |
| `GET /app/labs/runs` | `DA/DD/BA/BD` | Validated context and service filter | None | `list[ClientLabRunRead]` | None |
| `GET /app/labs/runs/{run_id}` | `DA/DD/BA/BD` | Object looked up inside validated context | None | `ClientLabRunRead` | None |
| `POST /app/labs/uploads` | `DA/BA` | Validated context | None | `ClientLabUploadRead` | Persists file/upload metadata and enqueues auto-training |
| `GET /app/labs/uploads` | `DA/DD/BA/BD` | Validated context and service filter | None | `list[ClientLabUploadRead]` | None |
| `GET /app/labs/uploads/{upload_id}` | `DA/DD/BA/BD` | Object looked up inside validated context | None | `ClientLabUploadRead` | None |

## Pipeline observatory

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/observatory/pipeline-runs/{experiment_id}/summary` | `DA/DD` | Optional `workspace_id` query filter; otherwise platform-wide lookup | None | `PipelineSummaryRead` | None |
| `GET /admin/observatory/pipeline-runs/{experiment_id}/events` | `DA/DD` | Optional `workspace_id` query parameter | None | `list[MlRunEventRead]` | None |
| `GET /admin/observatory/pipeline-runs/{experiment_id}/events/incremental` | `DA/DD` | Optional `workspace_id` query parameter | None | `list[MlRunEventRead]` | None |
| `GET /admin/observatory/pipeline-runs/{experiment_id}/llm-invocations` | `DA/DD` | Optional `workspace_id` query filter | None | `list[LlmInvocationRead]` | None |
| `GET /admin/observatory/llm-invocations/{invocation_id}` | `DA/DD` | Optional `workspace_id` query filter | None | `LlmInvocationRead` | None |
| `GET /admin/observatory/workflow-runs/{workflow_run_id}/pipelines` | `DA/DD` | Optional `workspace_id` query filter | None | `list[WorkflowRunPipelineRead]` | None |
| `GET /admin/pipeline-runs/{experiment_id}/monitor` | `DA/DD` | Platform-wide experiment lookup | None | `PipelineMonitorRead` | None |
| `GET /business/observatory/pipeline-runs/{experiment_id}/summary` | `DA/DD/BA/BD` | Validated context; experiment filtered by workspace | `pipeline_monitor`; semantic/OpenAI counts additionally masked by their flags | `PipelineSummaryRead` | None |
| `GET /business/observatory/pipeline-runs/{experiment_id}/events` | `DA/DD/BA/BD` | Validated context; experiment filtered by workspace | `pipeline_monitor` + `raw_pipeline_debug` | `list[MlRunEventRead]` | None |
| `GET /business/observatory/pipeline-runs/{experiment_id}/events/incremental` | `DA/DD/BA/BD` | Validated context; experiment filtered by workspace | `pipeline_monitor` + `raw_pipeline_debug` | `list[MlRunEventRead]` | None |
| `GET /business/observatory/pipeline-runs/{experiment_id}/llm-invocations` | `DA/DD/BA/BD` | Validated context | `pipeline_monitor`; rows filtered by `semantic_llm_audit` and `openai_pipeline_audit` | `list[LlmInvocationRead]` | None |
| `GET /business/observatory/llm-invocations/{invocation_id}` | `DA/DD/BA/BD` | Validated context | `pipeline_monitor`, plus `semantic_llm_audit` or `openai_pipeline_audit` according to purpose | `LlmInvocationRead` | None |
| `GET /business/observatory/workflow-runs/{workflow_run_id}/pipelines` | `DA/DD/BA/BD` | Validated context | `pipeline_monitor` | `list[WorkflowRunPipelineRead]` | None |
| `GET /business/workspaces/{workspace_id}/pipeline-runs/{experiment_id}/monitor` | `DA/DD` or workspace member | Path workspace must be readable; experiment must belong to it | `pipeline_monitor`; response sections filtered by `cv_fold_details`, `raw_pipeline_debug`, `semantic_llm_audit`, `openai_pipeline_audit`, `decision_ledger` | `PipelineMonitorRead` | None |

## Verification

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/lab/runs/{run_id}/verification` | `DA/DD` | Platform-wide run lookup | None | `VerificationAttemptResponse` | None |
| `GET /admin/lab/runs/{run_id}/verifications` | `DA/DD` | Platform-wide run lookup | None | `list[VerificationAttemptResponse]` | None |
| `POST /admin/lab/runs/{run_id}/verification` | `DA` | Platform-wide run lookup | None | `VerificationAttemptResponse` | Runs/persists a routine verification attempt and possible LLM ledger entry |
| `POST /admin/lab/runs/{run_id}/verification/deep` | `DA` | Platform-wide run lookup | None | `VerificationAttemptResponse` | Runs/persists a deep verification attempt and possible LLM ledger entry |
| `GET /admin/lab/runs/{run_id}/report` | `DA/DD` | Platform-wide run lookup | None | `dict` | None |
| `GET /admin/lab/runs/{run_id}/report.docx` | `DA/DD` | Platform-wide run lookup | None | `Response` (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`) | Renders a DOCX in memory; no persisted write |
| `POST /business/workspaces/{workspace_id}/lab-runs/{run_id}/verification/deep` | `DA/BA`; `DD/BD` denied even with flags | Path workspace must be writable; run must belong to it | `openai_pipeline_audit` + `deep_audit` | `VerificationAttemptResponse` | Runs/persists deep verification and LLM ledger entry |

## Models

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/models` | `DA/DD` | None; registry is platform-wide | None | `list[RegisteredModel]` | None |
| `GET /admin/models/client-trials/{audit_id}` | `DA/DD` | Platform-wide audit lookup | None | `ClientTrialAuditDetail` | None |
| `GET /admin/businesses/{workspace_id}/models/{model_id}` | `DA/DD` | Workspace/model pair checked by service | None | `ModelDetailRead` | None |
| `GET /business/workspaces/{workspace_id}/models/{model_id}` | `DA/DD` or workspace member | Workspace/model pair and enabled domain checked | `model_management` | `BusinessModelDetailRead` | None |
| `GET /admin/experiments/{experiment_id}/candidates` | `DA/DD` | Platform-wide experiment lookup | None | `list[dict]` | None |
| `GET /admin/experiments/{experiment_id}/metrics` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/experiments/{experiment_id}/models` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/experiments/{experiment_id}/ensemble` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/experiments/{experiment_id}/report` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | Reads persisted result and optional artifact markdown |
| `GET /admin/experiments/{experiment_id}/feature-importance` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/experiments/{experiment_id}/feature-groups` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/experiments/{experiment_id}/errors` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/experiments/{experiment_id}/comparison` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |

## Workflows

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/businesses/{workspace_id}/workflows/{workflow_id}` | `DA/DD` | Workspace/workflow pair checked by service | None | `WorkflowDetailRead` | None |
| `GET /admin/businesses/{workspace_id}/workflow-runs/{run_id}` | `DA/DD` | Workspace/run pair checked by service | None | `WorkflowRunDetailRead` | None |
| `GET /business/workspaces/{workspace_id}/workflows/{workflow_id}` | `DA/DD` or workspace member | Workspace/workflow pair and enabled domain checked | None | `WorkflowDetailRead` | None |
| `GET /business/workspaces/{workspace_id}/workflow-runs/{run_id}` | `DA/DD` or workspace member | Workspace/run pair and enabled domain checked | None | `BusinessWorkflowRunDetailRead` | None |
| `GET /admin/tasks` | `DA/DD` | None; platform-wide | None | `list[TaskRead]` | None |
| `GET /admin/tasks/{task_id}` | `DA/DD` | None; platform-wide | None | `TaskRead` | None |
| `POST /admin/tasks/from-config` | `DA` | None | None | `TaskRead` | Reads a server-local YAML path and upserts a task/environment |
| `GET /admin/experiments` | `DA/DD` | None; latest 50 platform-wide | None | `list[ExperimentRead]` | None |
| `POST /admin/experiments` | `DA` | Dataset/task selected platform-wide | None | `ExperimentRead` | Creates an experiment; does not run it |
| `GET /admin/experiments/{experiment_id}` | `DA/DD` | Platform-wide experiment lookup | None | `ExperimentRead` | None |
| `POST /admin/experiments/{experiment_id}/run` | `DA` | Platform-wide experiment lookup | None | `ExperimentRead` | Executes experiment and persists results/artifacts |
| `POST /admin/simulations/run` | `DA` | None; simulation store is global | None | OpenAPI untyped; handler returns `SimulationRunRead` or `SimulationRunListResponse` | Executes simulations and persists run rows |
| `GET /admin/simulations/runs` | `DA/DD` | None; global store | None | `SimulationRunListResponse` | None |
| `GET /admin/simulations/runs/{run_id}` | `DA/DD` | None; global run lookup | None | `SimulationRunRead` | None |
| `GET /admin/simulations/runs/{run_id}/decisions/{external_id}` | `DA/DD` | None; global persisted simulation payload | None | `SimulationDecisionResponse` | None |

## Datasets

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/datasets` | `DA/DD` | None; platform-wide | None | `list[DatasetRead]` | None |
| `GET /admin/datasets/{dataset_id}` | `DA/DD` | None; platform-wide lookup | None | `DatasetRead` | None |
| `POST /admin/datasets/upload` | `DA` | None | None | `DatasetRead` | Writes uploaded file under `data/uploads`, persists dataset, and profiles it |
| `POST /admin/datasets/sample-workbook` | `DA` | None | None | `DatasetRead` | Generates/ingests sample workbook and persists dataset metadata |
| `GET /admin/datasets/{dataset_id}/use-cases` | `DA/DD` | None; platform-wide dataset lookup | None | `UseCasePlanRead` | None |
| `POST /admin/datasets/{dataset_id}/use-cases/{slug}/train` | `DA` | None; platform-wide dataset lookup | None | `ExperimentRead` | Trains one use case and persists experiment/results/artifacts |
| `POST /admin/datasets/{dataset_id}/train` | `DA` | None; platform-wide dataset lookup | None | `list[ExperimentRead]` | Trains selected/all use cases and persists results/artifacts |
| `POST /admin/datasets/{dataset_id}/profile` | `DA` | None; platform-wide dataset lookup | None | `dict` | Computes and persists a new dataset profile |
| `GET /admin/datasets/{dataset_id}/profile` | `DA/DD` | None; platform-wide dataset lookup | None | `dict` | None |

## Predictions

| Method and path | Roles | Workspace | Capability | Response model | Side effect |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/experiments/{experiment_id}/predictions` | `DA/DD` | Platform-wide experiment lookup | None | `dict` | None |
| `GET /admin/client-uploads` | `DA/DD` | None; platform-wide uploads | None | `list[AdminClientUploadSummary]` | None |
| `GET /admin/client-uploads/{upload_id}` | `DA/DD` | Platform-wide upload lookup | None | `AdminClientUploadDetail` | None |
| `GET /admin/client-uploads/{upload_id}/predictions.csv` | `DA/DD` | Platform-wide upload lookup | None | `Response` (`text/csv`) | Returns generated/stored CSV bytes; no write |
| `GET /admin/client-uploads/{upload_id}/report.docx` | `DA/DD` | Platform-wide upload lookup | None | `Response` (DOCX) | Returns technical report bytes; no write |
| `GET /business/workspaces/{workspace_id}/client-uploads/{upload_id}/predictions.csv` | `DA/DD` or workspace member | Workspace must be readable and upload must belong to it | `prediction_download` | `Response` (`text/csv`) | Returns prediction CSV; no write |
| `GET /app/labs/uploads/{upload_id}/predictions.csv` | `DA/DD/BA/BD` | Validated context; upload filtered by workspace | `prediction_download` for modern business roles; platform bypass | `Response` (`text/csv`) | Returns prediction CSV; no write |

## Material implementation discrepancies

1. `model_management` now gates Business model detail and strips models from
   Business workspace detail. There is still no create/update/delete model API
   for the flag to govern.
2. Business raw event endpoints require `pipeline_monitor` and
   `raw_pipeline_debug`, then return `_events(...)` directly. They do not apply
   the `cv_fold_details`, `semantic_llm_audit`, or `openai_pipeline_audit`
   filters used by the combined monitor response. Those specific flags therefore
   do not fully isolate information once raw event access is enabled.
3. `GET /app/insights` inherits workspace authentication/resolution, but its
   query reads the latest global `SimulationRun` per use case and accepts no
   workspace ID. It is not tenant-filtered.
4. OpenAPI leaves successful response bodies untyped for binary downloads,
   `/health`, and `POST /admin/simulations/run`; clients cannot derive those
   response shapes solely from the schema.
