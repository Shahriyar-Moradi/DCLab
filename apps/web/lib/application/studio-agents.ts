/**
 * P4.8-UI: copy-paste connection material for the Agents & tools page. Pure data (no React, no
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
    for check in api.experiments.findings(run.id).checks:
        print(check.check, check.status, check.message)
`;

export const CLI_SHELL = `dclab-cli login --api-url ${API_URL_PLACEHOLDER}   # token from stdin or prompt
dclab-cli whoami
dclab-cli projects list
dclab-cli data upload FILE --project ID
dclab-cli experiments run --project P --dataset D --target C
dclab-cli experiments compare ID ID --json
dclab-cli experiments code ID -o reproduce.py
dclab-cli decisions list --project P
`;

export const SCOPE_HELP: Array<{ scope: string; help: string }> = [
  { scope: "read", help: "Read projects, datasets, experiments, models and decisions. Never raw rows, never final test set values." },
  { scope: "projects:write", help: "Create projects and problem specs (drafts)." },
  { scope: "datasets:write", help: "Upload datasets." },
  { scope: "experiments:write", help: "Start, branch and cancel experiments." },
  { scope: "decisions:propose", help: "Propose decisions. A token only proposes; a person accepts in Studio." },
];

export const V1_CONVENTIONS: Array<{ key: string; label: string; value: string }> = [
  { key: "auth", label: "Authentication", value: "Authorization: Bearer <service token>. A token acts in one workspace, within its scopes and your current role." },
  { key: "idem", label: "Idempotency-Key", value: "Send one key per command. A retry with the same key replays the first result instead of starting a second run." },
  { key: "ifmatch", label: "ETag / If-Match", value: "Reads return an ETag. Changes to refs (for example the champion) send it back in If-Match; a stale value answers 412." },
  { key: "errors", label: "Error envelope", value: "Errors are JSON {error: {code, message, request_id, details}}. Quote request_id when you ask for help." },
  { key: "cursors", label: "Cursors", value: "List responses carry an opaque next cursor. Pass it back unchanged; do not parse it." },
];

export const WRITE_SWITCH_TEXT =
  "Two switches decide what an agent can change. The token's scopes are enforced by the API. In the MCP server, DCLAB_MCP_WRITE_ENABLED lists the write tools at all; it is off by default, so a connected agent starts read-only.";

/** True when a snippet carries something that looks like a real token (placeholder only is fine). */
export function containsRealToken(text: string): boolean {
  return /dclab_st_[A-Za-z0-9_-]{8,}/.test(text);
}
