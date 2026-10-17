/**
 * P4.8-UI: copy-paste connection material for the Connect page. Pure data (no React, no
 * secrets): a token is always the placeholder `dclab_st_…`, never a real value. Text comes from
 * packages/dclab_mcp and packages/dclab_client READMEs.
 */
export const TOKEN_PLACEHOLDER = "dclab_st_…";
/** The FastAPI service itself, never the web address: the web proxy does not forward Authorization. */
export const API_URL_PLACEHOLDER = "https://<your-dclab-api-host>";
export const OPENAPI_HREF = "/api/backend/openapi.json";

export const MCP_JSON = `{
  "mcpServers": {
    "dclab": {
      "command": "python",
      "args": ["-m", "dclab_mcp"],
      "env": {
        "DCLAB_API_URL": "${API_URL_PLACEHOLDER}",
        "DCLAB_TOKEN": "${TOKEN_PLACEHOLDER}",
        "DCLAB_MCP_WRITE_ENABLED": "0"
      }
    }
  }
}
`;

export const MCP_INSTALL = `pip install -r requirements-mcp.lock
python -m dclab_mcp   # stdio server; your MCP client starts it from .mcp.json`;

export const SDK_PYTHON = `import os
from dclab_client import DCLabClient

with DCLabClient(os.environ["DCLAB_API_URL"], token=os.environ["DCLAB_TOKEN"]) as api:
    project = api.projects.create(name="Churn")
    upload = api.datasets.upload(project.id, "rows.csv")
    run = api.experiments.create(project_id=project.id, dataset_id=upload.id, target_column="label")
    # once the run is completed: the trust checks, then the model card
    for check in api.experiments.findings(run.id).checks:
        print(check.check, check.status, check.message)
    print(api.model_versions.card("<model version id>").markdown)
    # later: score new rows (scoring runs in the background; check the status before downloading)
    rows = api.datasets.upload(project.id, "new_rows.csv", purpose="scoring")
    pred = api.predictions.create(model_version_id="<model version id>", dataset_id=rows.id)
    if api.predictions.get(pred.id).status == "completed":
        api.predictions.download(pred.id, to="predictions.csv")
`;

export const CLI_SHELL = `dclab-cli login --api-url ${API_URL_PLACEHOLDER}   # token from stdin or prompt
dclab-cli whoami
dclab-cli projects list
dclab-cli data upload FILE --project ID
dclab-cli experiments run --project P --dataset D --target C
dclab-cli experiments compare ID ID --json
dclab-cli experiments findings ID
dclab-cli experiments code ID -o reproduce.py
dclab-cli models card MV
dclab-cli predict create --model-version MV --dataset D --wait
dclab-cli decisions list --project P
`;

/** What each permission of an access token lets it do, in plain words. Closed table: an unknown permission is shown as given, cleaned. */
const ABILITY: Record<string, { label: string; help: string }> = {
  read: { label: "Read", help: "Look at projects, runs, models, decisions and the code of a run, and download prediction files (these contain the values of each scored row)." },
  "projects:write": { label: "Create projects and goals", help: "Create projects and draft goals." },
  "datasets:write": { label: "Upload data", help: "Upload data files." },
  "experiments:write": { label: "Run experiments and scoring", help: "Start, branch and cancel runs, answer a run's target question, score data with a model, and queue AI agent reviews (these spend AI budget)." },
  "decisions:propose": { label: "Propose decisions", help: "Suggest a decision. A person approves it in the Inbox." },
};

export function abilityLabel(scope: string): string {
  return Object.hasOwn(ABILITY, scope) ? ABILITY[scope].label : scope.replace(/[^\w:.-]/g, " ").replaceAll("_", " ").slice(0, 40);
}
export function abilityHelp(scope: string): string | null {
  return Object.hasOwn(ABILITY, scope) ? ABILITY[scope].help : null;
}
export const ABILITY_SCOPES = Object.keys(ABILITY);

/** The limit that holds for every access token, whatever it can do. */
export const TOKEN_LIMIT_TEXT =
  "Whatever it is allowed to do, an access token cannot accept a decision or choose a model for use: that stays with people. Decisions it proposes land in your Inbox.";

export const V1_CONVENTIONS: Array<{ key: string; label: string; value: string }> = [
  { key: "auth", label: "Authentication", value: "Authorization: Bearer <access token>. A token acts in one workspace, within what it was allowed to do and your current role." },
  { key: "idem", label: "Idempotency-Key", value: "Send one key per command. A retry with the same key replays the first result instead of starting a second run." },
  { key: "ifmatch", label: "ETag / If-Match", value: "Reads return an ETag. A change to which version is in use (for example the model in use) sends it back in If-Match; a stale value answers 412." },
  { key: "errors", label: "Errors", value: "Errors are JSON {error: {code, message, request_id, details}}. Quote request_id when you ask for help." },
  { key: "cursors", label: "Paging", value: "List responses carry an opaque next cursor. Pass it back unchanged; do not parse it." },
];

export const WRITE_SWITCH_TEXT =
  "Two things decide what a connected tool can change. The access token's permissions are enforced by DCLab. In the MCP server, tools that change things are listed only when DCLAB_MCP_WRITE_ENABLED is 1; it is off by default, so a connected tool starts read-only.";

/** True when a snippet carries something that looks like a real token (placeholder only is fine). */
export function containsRealToken(text: string): boolean {
  return /dclab_st_[A-Za-z0-9_-]{8,}/.test(text);
}
