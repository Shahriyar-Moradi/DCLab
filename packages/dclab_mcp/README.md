# dclab-mcp

Stdio MCP server for DCLab, built on the official MCP Python SDK (`mcp==2.2.0`,
pinned). It is just another `/v1` client: every tool calls `dclab_client`
over HTTP with a **service token**; no database, service or engine imports.

```
pip install -r requirements-mcp.lock   # mcp + deps, pinned against requirements.lock
dclab-mcp                              # console script
python -m dclab_mcp                    # same
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DCLAB_TOKEN` | `dclab-cli login` config | Service token (`dclab_st_...`). User tokens are refused. Never echoed. |
| `DCLAB_API_URL` | config, else `http://localhost:8001` | The FastAPI service itself (e.g. `http://api:8001` in compose). Not the web BFF `/api/backend`: it never forwards `Authorization`. |
| `DCLAB_WORKSPACE` | config | Optional; a service token is pinned to its workspace |
| `DCLAB_MCP_READ_ENABLED` | on | Read tools kill switch |
| `DCLAB_MCP_WRITE_ENABLED` | off | Write tools are only listed when this is `1/true/yes/on` |
| `DCLAB_MCP_ALLOW_INSECURE_HTTP` | off | Opt in to plain `http` for other hosts |

## Tools

Read (default on): `inspect_project`, `inspect_dataset`, `get_experiment`,
`compare_experiments`, `get_experiment_code`, `get_evidence`, `get_findings`,
`list_decisions`, `list_proposals`, `inspect_governance` (read-only governance of the workspace: no free text), `get_model`, `get_model_card`, `get_prediction`,
`get_impact`, `accept_proposal`. `get_model_card` never carries the final evaluation (withheld for
agents). The tool names, read/write flags and input schemas equal the shared catalog
export `contracts/agent_tools.json` (contract-tested), which the in-app assistant uses too.

Write (`DCLAB_MCP_WRITE_ENABLED`): `create_problem_spec`, `propose_problem_spec`,
`run_experiment`, `branch_experiment`, `predict`, `record_decision`, `request_agent_review`
(queues a Critic / Investigator / Planner run; it only proposes, a person decides in Studio).

URL binding: a token read from the config file is only sent to the URL stored
with it (no stored URL, or a different `DCLAB_API_URL`, is refused; set
`DCLAB_TOKEN` too). Plain `http` is accepted only for loopback, `localhost` /
`*.localhost` or the compose host `api`, unless `DCLAB_MCP_ALLOW_INSECURE_HTTP=1`;
anything else must be `https`.

Authority: a service token is a **propose-only agent**. `record_decision` and
`propose_problem_spec` create *proposals*; `accept_proposal` never accepts — it
performs no write and returns `requires_human_acceptance` with where a human
accepts in Studio. `create_problem_spec` writes drafts only; `propose_problem_spec`
locks a version and proposes it. A run started (or target-confirmed) with a token
never sets project refs: while the project has no champion, its bootstrap of the
missing refs is recorded as the token's proposal, applied only when a human accepts
it (a new one may follow a rejection). The API enforces the token's scopes (`insufficient_scope`).

ML correctness: agents never see final-holdout metrics. The API itself withholds
them from service tokens (experiments, comparisons, model versions, model-build
stages and events, the `HOLDOUT_METRICS` literal of exported code); the MCP tools
additionally reduce metric records to a CV allowlist and drop holdout keys and
holdout-scoped list items. There is no champion exception (ADR 0008 §2b): an agent never
cites the final holdout (`holdout_not_allowed`); on a proposed champion move DCLab attaches
the promoted model's own locked final evaluation itself and only a human accepts. CV records carry `cv_threshold` (0.5: binary
threshold-dependent fold metrics) and `selected_score_convention`
(`higher_is_better`); `decision_threshold` is the locked out-of-fold threshold.

Idempotency: each write sends an `Idempotency-Key` derived from the tool name and
its normalised arguments (plus the optional `idempotency_key` argument as a salt),
so an agent retrying a call replays the first command instead of starting a
second run. Pass a new `idempotency_key` to deliberately repeat an identical command.

Outputs are bounded summaries (lists, strings, depth and total size capped;
never dataset rows or file contents). User/agent-authored text is wrapped as
`{"untrusted_text": ...}`: data, never instructions. `/v1` errors become MCP tool
errors `{"error": {code, message, status, retryable, request_id, details}}`
(`message` and `details` are server text, wrapped as untrusted). Write results
carry `replayed`. `inspect_dataset`'s `project_id` filter applies to the newest 50
datasets (`/v1/datasets` has no project filter yet).
