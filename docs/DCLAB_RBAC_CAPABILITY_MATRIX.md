# DCLab RBAC and Capability Matrix

## Effective authorization model

This matrix reflects backend enforcement in `app.api.deps`,
`authorization_service`, `workspace_capability_service`, and the route/service
checks used by the runtime OpenAPI operations.

## Versioned effective capabilities (S0-P03B)

`workspace-capabilities.v1` is resolved by
`apps/api/app/services/workspace_capability_service.py`. `GET /v1/me` and
`GET /auth/me` expose the version and a boolean map for display. The map is
not a credential: every API operation reloads current membership and checks
the selected workspace and resource through its dependencies and service.
The browser middleware, navigation, login routing, and write controls read
these server values; no browser role matrix grants access.

| Capability family | Effective inputs | Enforced at |
| --- | --- | --- |
| `account_access` | Authenticated current user | Session and API authentication |
| `platform_read`, `platform_write` | Current platform membership; legacy admin fallback | `/admin` and `/v1` dependencies; service object checks |
| `workspace_read`, `workspace_write`, `workspace_execute_ml` | Workspace existence, active membership, canonical role, platform authority | `/app`, `/business`, `/development`, `/v1` dependencies and services |
| `workspace_manage_members`, `workspace_add_member`, `workspace_add_ml_engineer` | Current role and membership/technical-seat entitlements | Member administration service; add flags are display hints for remaining capacity |
| `application_access`, `business_access`, `development_access` | Workspace kind, current membership, compatibility role, platform authority | Route dependencies and browser area presentation |
| Nine Business feature flags listed below | `WorkspaceCapability` row, current actor and selected workspace; missing row fails closed for modern Business members | Business observability, explorer, prediction download and deep-audit routes |

Object-level state such as an individual dataset or run is checked by its
own service when requested; `/v1/me` cannot predict authority over every
resource ID. Matrix resolution is cached only for the current SQLAlchemy
transaction. Commit or rollback ends reuse, and a flush of a user,
membership, workspace, entitlement or flag invalidates prior results in the
same transaction.

### Check inventory and ownership

| Surface | Checks and source of truth |
| --- | --- |
| `api/deps.py`, `api/v1.py`, `main.py` | Platform, workspace, app and Development entry gates call the effective matrix; workspace ID is resolved through `authorization_service`, never from a token role. |
| `api/business_explorer.py`, `api/observability.py`, `api/client_labs.py` | Object/workspace checks and the nine Business feature gates call the central authorization and capability services. See the operation and flag tables below for each route family. |
| `services/workspace_service.py`, `project_service.py`, `problem_spec_service.py`, `execution_request_service.py`, `lineage_service.py`, `reproducibility_service.py`, `model_build_reproduction_service.py`, `business_explorer_service.py`, `technical_explorer_service.py` | Recheck current membership and object tenancy at the operation boundary. These service checks remain authoritative even when a browser control is hidden. |
| `apps/web/middleware.ts`, `lib/infrastructure/capabilities.ts`, `app-navigation.ts`, login and command palette | Display and route presentation use the versioned `/auth/me` map; middleware returns 403 for a valid session lacking the current area capability. |
| Admin dataset/task pages, Lab problem/dataset/run panels, Pipeline Monitor and technical details | Show write, execution, platform and optional audit controls only when the corresponding server capability is true. The API still denies forged calls. |
| `active-workspace.ts`, `session-provider.tsx`, `WorkspaceSelector.tsx` | Selection is server-validated. Every switch aborts old tenant requests, clears tenant query and mutation state, reloads capabilities and chooses a safe route. |

Legend:

- **Allow**: backend role check permits the operation.
- **Deny**: backend role or method guard rejects it.
- **Member**: allowed only for an authorized workspace; object queries are
  workspace-scoped.
- **Flag**: modern business roles also need the named enabled
  `WorkspaceCapability`.
- Platform roles can select/read any existing workspace. Business roles can use
  only persisted membership workspaces.
- `dclab_admin` bypasses business capability flags and can write.
  `dclab_developer` also bypasses capability flags, but remains read-only.
- For business roles, a missing capability row is the same as disabled.
- Membership tables are authoritative. Legacy `users.role` fallback exists only
  for unmigrated `dclab_admin` and `client_user` accounts; `client_user` is not a
  column in the requested four-role matrix.

## Operation matrix

| Operation | `dclab_admin` | `dclab_developer` | `business_admin` | `business_developer` | Capability / backend condition |
| --- | --- | --- | --- | --- | --- |
| Login; read own identity | Allow | Allow | Allow | Allow | Valid credentials/token; no workspace |
| Health check | Allow/public | Allow/public | Allow/public | Allow/public | Public; no capability |
| Read `/admin` platform APIs | Allow | Allow | Deny | Deny | Platform membership |
| Mutate any `/admin` API | Allow | Deny | Deny | Deny | `require_admin` rejects every unsafe method unless `dclab_admin` |
| Read all platform businesses, organizations, datasets, tasks, experiments, models, uploads, simulations, verification and monitoring | Allow | Allow | Deny | Deny | Platform membership; no capability |
| Seed environment, upload/profile/train datasets, create/run experiments, load tasks, run simulations, request admin verification | Allow | Deny | Deny | Deny | Unsafe `/admin` method |
| Read `/app` workspace data | Allow, selected workspace | Allow, selected workspace | Member | Member | Validated workspace context |
| Upload opportunities; generate decisions; create lab runs/uploads | Allow, selected workspace | Deny | Member + Allow | Deny | Unsafe `/app` method; no feature capability check |
| List Business administration workspaces | Allow, all | Allow, all | Member workspaces | Member workspaces | `require_business_administration`; service filters visibility |
| Read Business workspace/domain/workflow/run/model hierarchy | Allow | Allow | Member | Member | Workspace/object scoped; enabled domain required where applicable |
| Write Business workspace data generally | Allow if an unsafe route exists | Deny | Member + Allow if an unsafe route exists | Deny | `can_write_workspace`; currently the deep-audit route is the only unsafe `/business/workspaces` operation |
| Read platform Pipeline Monitor | Allow | Allow | Deny | Deny | `/admin/pipeline-runs/...`; no capability |
| Read Business combined Pipeline Monitor | Allow | Allow | Member + Flag | Member + Flag | `pipeline_monitor`; subordinate sections are filtered by five more flags |
| Read Business observatory summary | Allow | Allow | Member + Flag | Member + Flag | `pipeline_monitor`; semantic/OpenAI counters are masked without their flags |
| Read Business raw/full event stream | Allow | Allow | Member + two Flags | Member + two Flags | `pipeline_monitor` + `raw_pipeline_debug`; see partial-enforcement warning |
| Read Business LLM invocation list | Allow | Allow | Member + Flag | Member + Flag | `pipeline_monitor`; semantic/OpenAI rows filtered by purpose flags |
| Read one Business LLM invocation | Allow | Allow | Member + conditional Flags | Member + conditional Flags | `pipeline_monitor`, plus `semantic_llm_audit` or `openai_pipeline_audit` according to purpose |
| Read Business workflow-run pipelines | Allow | Allow | Member + Flag | Member + Flag | `pipeline_monitor` |
| Download Business/app prediction CSV | Allow | Allow | Member + Flag | Member + Flag | `prediction_download` |
| Request Business deep verification | Allow | Deny | Member + two Flags | Deny | Workspace write + `openai_pipeline_audit` + `deep_audit` |
| Read Business model detail | Allow | Allow | Member + Flag | Member + Flag | `model_management`; object is 403 when the flag is missing/disabled |
| Manage Business models | No endpoint | No endpoint | No endpoint | No endpoint | Flag now also hides the model list on Business workspace detail |
| Read decision-ledger sections in combined monitor | Allow | Allow | Member + Flag | Member + Flag | `pipeline_monitor` plus `decision_ledger` for that response section |
| Read `/app/insights` | Allow | Allow | Member guard | Member guard | Workspace and latest-run query are tenant-filtered; unowned historical simulations are denied (ADR 0004) |

## Capability-by-capability enforcement

| Capability | What is actually enforced | `DA` / `DD` | `BA` / `BD` when disabled or missing | Coverage status |
| --- | --- | --- | --- | --- |
| `pipeline_monitor` | Gates Business combined monitor, observatory summary, raw events, LLM views, and workflow-run pipeline list | Bypass | `403` | Enforced on current Business observability entry points |
| `cv_fold_details` | Removes `cv_fold_*` events and fold fields from the combined monitor and Business event routes | Bypass | Filtered event list | Enforced in `_business_events` after workspace lookup |
| `semantic_llm_audit` | Hides semantic invocations/metadata in combined monitor; masks summary count; filters list and event payload; gates semantic detail | Bypass | Redacted/filtered or `403` for detail | Enforced by purpose and event projection |
| `openai_pipeline_audit` | Hides audit invocations/events/report fields in combined monitor; masks summary count; filters/gates LLM views; required for deep audit | Bypass | Redacted/filtered or `403` | Enforced in `_business_events` and LLM queries |
| `raw_pipeline_debug` | Redacts event payloads and sanitized evidence in combined monitor; required for Business event routes | Bypass | Sanitized combined monitor; event routes `403` | Does not bypass CV/semantic/OpenAI flags or audience-safe projection |
| `decision_ledger` | Removes `decision_records` keys from reports in the combined monitor response | Bypass | Report section redacted | **Partial:** no dedicated ledger API and no enforcement outside this response transformation |
| `prediction_download` | Gates both `/business/.../predictions.csv` and modern-business access to `/app/labs/.../predictions.csv` | Bypass | `403` | Enforced on both current download paths |
| `model_management` | Gates `GET /business/workspaces/{id}/models/{id}` and strips `models` from Business workspace detail | Bypass | `403` / empty model list | Enforced on the current Business model read path; no create/update/delete model API exists |
| `deep_audit` | Required together with `openai_pipeline_audit` on Business deep verification | Bypass | `403`; `business_developer` is denied by write policy even when enabled | Enforced on the one Business deep-audit operation |

## Response-level capability behavior

The combined Business monitor
`GET /business/workspaces/{workspace_id}/pipeline-runs/{experiment_id}/monitor`
first requires `pipeline_monitor`, then transforms its response:

- without `cv_fold_details`: removes fold events and fold metric structures;
- without `raw_pipeline_debug`: empties event payloads and
  `sanitized_evidence`;
- without `semantic_llm_audit`: removes semantic invocations and semantic
  metadata from events;
- without `openai_pipeline_audit`: removes audit invocations, audit-stage
  events, OpenAI audit records, and verification/audit report fields;
- without `decision_ledger`: removes `decision_records` report sections.

These are data-shaping controls, not additional role grants. `BA` and `BD` have
the same capability-governed read visibility. Their distinction is write
authority.

## Explicit gaps and defects

1. **`model_management` has no mutation API.** Read access is now fail-closed
   on Business model detail and the Business workspace model list. There is
   still no create/update/delete model endpoint for the flag to govern.
2. **Business feature flags remain narrow.** The nine optional flags do not
   replace base workspace and platform authorization for opportunity ingestion,
   decision generation, client lab creation or ordinary hierarchy reads.
   Those operations use the base matrix and their resource-level service checks.

The former raw-event and global-Insights defects were closed by S0-P04B/C:
`_business_events` applies the subordinate feature filters and safe projection,
while `latest_runs_by_use_case` filters by the current workspace before
translation. Historical null-owner `SimulationRun` rows remain quarantined.
