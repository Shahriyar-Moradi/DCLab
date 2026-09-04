# DCLab RBAC and Capability Matrix

## Effective authorization model

This matrix reflects backend enforcement in `app.api.deps`,
`authorization_service`, `workspace_capability_service`, and the route/service
checks used by the runtime OpenAPI operations.

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
| Read `/app/insights` | Allow | Allow | Member guard | Member guard | Workspace is validated, but the underlying simulation query is global rather than tenant-filtered |

## Capability-by-capability enforcement

| Capability | What is actually enforced | `DA` / `DD` | `BA` / `BD` when disabled or missing | Coverage status |
| --- | --- | --- | --- | --- |
| `pipeline_monitor` | Gates Business combined monitor, observatory summary, raw events, LLM views, and workflow-run pipeline list | Bypass | `403` | Enforced on current Business observability entry points |
| `cv_fold_details` | Removes `cv_fold_*` events and fold fields from the combined monitor response | Bypass | Combined monitor is redacted | **Partial:** raw event routes do not apply this filter after `raw_pipeline_debug` is granted |
| `semantic_llm_audit` | Hides semantic invocations/metadata in combined monitor; masks summary count; filters list; gates semantic detail | Bypass | Redacted/filtered or `403` for detail | **Partial:** raw event routes return `_events` directly and do not apply semantic filtering |
| `openai_pipeline_audit` | Hides audit invocations/events/report fields in combined monitor; masks summary count; filters/gates LLM views; required for deep audit | Bypass | Redacted/filtered or `403` | **Partial:** raw event routes do not apply the OpenAI event filter |
| `raw_pipeline_debug` | Redacts event payloads and sanitized evidence in combined monitor; required for raw event routes | Bypass | Sanitized combined monitor; raw routes `403` | Enforced for raw payload access, but granting it also exposes events not independently filtered by three specific flags |
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
2. **Raw events weaken specific capability separation.** The Business full and
   incremental event handlers check only `pipeline_monitor` and
   `raw_pipeline_debug`, then return the unfiltered event list. They do not call
   the helper that filters CV, semantic, and OpenAI events. A workspace granted
   raw debug can therefore receive those event types without the corresponding
   three flags.
3. **Insights are not tenant-scoped.** `/app/insights` validates workspace
   membership at the parent router, but `list_client_insights` reads global
   simulation runs without a workspace predicate.
4. **Capabilities are not broad product entitlements.** No capability gates
   opportunity ingestion, decision generation, client lab creation, dataset
   administration, platform model reads, or ordinary Business hierarchy reads.
   The current flags apply only at the specific checks listed above.
