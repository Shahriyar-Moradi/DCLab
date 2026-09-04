# DCLab Business Administration

## Tenant boundary

The Business administration plane uses `Workspace` as the tenant and the same
`BusinessDomain`, `WorkspaceDomain`, `MlWorkflow`, `WorkflowRun`, `Experiment`,
`ModelAsset`, and `ModelVersion` records as platform administration. It does not
maintain a second business registry or observability store.

Business routes include the workspace identifier for navigation, but backend
membership checks remain authoritative. A route parameter or
`X-Workspace-Id` header is only a selector. Cross-tenant object substitution is
answered with `404` after a workspace-scoped query.

The UI begins at `/business` and lists only membership workspaces. Each workspace
exposes Overview, enabled Domains, Workflows, Runs, and Models. Domain navigation
is driven by enabled `WorkspaceDomain` rows and therefore has no fixed domain
list.

## Roles

- `business_admin`: read/write within an authorized workspace.
- `business_developer`: the same permitted visibility, with all side-effecting
  routes rejected by the backend.
- DCLab platform roles may inspect the same business endpoints and bypass tenant
  capability flags. Platform write policy remains unchanged.

## Capability policy

Business technical access fails closed: a missing or disabled
`WorkspaceCapability` row means the feature is unavailable. Supported keys are:

- `pipeline_monitor`
- `cv_fold_details`
- `semantic_llm_audit`
- `openai_pipeline_audit`
- `raw_pipeline_debug`
- `decision_ledger`
- `prediction_download`
- `model_management`
- `deep_audit`

`pipeline_monitor` gates the shared monitor endpoint. More specific flags filter
fold events, semantic LLM records, OpenAI audit records, raw event payloads,
bounded debug evidence, and decision-ledger sections at the API boundary. The UI
also hides or disables unavailable features, but UI state is never the security
control.

Prediction downloads require `prediction_download` for modern business roles on
both the Business URL and the older `/app/labs` URL, preventing a route bypass.
The legacy `client_user` role retains its existing translated client download
behavior during the compatibility window.

Deep audit requires all of:

1. membership in the selected workspace;
2. `business_admin` write authority;
3. `openai_pipeline_audit`;
4. `deep_audit`;
5. a run belonging to that same workspace.

The resulting attempt continues to use the production verification service and
the existing `ml_run_verifications` and `llm_invocations` ledgers.
