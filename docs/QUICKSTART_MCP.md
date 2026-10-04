# Quickstart: DCLab from Claude Code (MCP)

Claude Code talks to DCLab through the stdio MCP server in `packages/dclab_mcp`. It is
just another `/v1` client: it holds a **service token**, never a user session.

## Authority rules

- Agents **propose, humans accept.** A service token can never accept a decision or
  move a project ref. `record_decision` and `propose_problem_spec` create proposals;
  `accept_proposal` performs no write, it only tells you where to accept in Studio.
- Agents **never see final-holdout values** (the API withholds them from tokens; the
  MCP tools also strip them). Only the current champion's holdout is reported, for
  reporting, never for selection.
- **Write tools are opt-in.** Without `DCLAB_MCP_WRITE_ENABLED=1` only the read tools
  are listed. Dataset rows are never returned by any tool.

## Setup

1. Start the stack (the worker must run, it does the training):
   `docker compose -f docker-compose.base44.yml up -d --build`
2. Log into Studio (`http://localhost:3000/login`) as a user with the ML-write role.
3. Settings -> Service tokens -> create a token. Pick the scopes you need
   (`read`, plus `projects:write`, `datasets:write`, `experiments:write`,
   `decisions:propose` for the walk-through), a lifetime (max 90 days) and confirm
   your password. The secret `dclab_st_...` is **shown once**; copy it now.
4. Install the server (a separate lock, the API image does not carry `mcp`):
   `pip install -r requirements-mcp.lock && pip install -e packages/dclab_client -e packages/dclab_mcp`
5. `cp .mcp.json.example .mcp.json` and fill in `DCLAB_WORKSPACE`. `.mcp.json` is
   git-ignored; keep the token out of it and export it instead:
   `export DCLAB_TOKEN=dclab_st_...` (Claude Code expands `${DCLAB_TOKEN}`).
   `DCLAB_API_URL` must point at the **API itself** (`http://localhost:8001`), not the
   web BFF on port 3000, which never forwards `Authorization`.
6. To let the agent write (spec proposals, runs, branches, decision proposals), set
   `"DCLAB_MCP_WRITE_ENABLED": "1"` in `.mcp.json`, then restart Claude Code.
7. Start Claude Code in this repo and run `/mcp`: `dclab` should be connected and list
   its tools (10 read tools, plus 6 write tools when enabled).

## Phase 3 exit flow

Ask Claude Code, step by step (write tools enabled, a project with an uploaded dataset):

1. **Inspect.** "Inspect my DCLab project and its dataset." -> `inspect_project`,
   `inspect_dataset` (counts and schema only).
2. **Propose a problem spec.** "Propose a binary classification spec for target
   `churned`." -> `propose_problem_spec` (locks a version and proposes it).
3. **Run.** "Run a baseline experiment." -> `run_experiment`, then `get_experiment`
   until `completed`. The worker trains; the token never sets refs.
4. **Evidence and code.** "Show the evidence and the reproduction script." ->
   `get_evidence`, `get_experiment_code` (CV metrics only). "Can I trust this run?" ->
   `get_findings` (leakage, overfit gap, duplicates, class imbalance, too-good score:
   pass / warning / fail with a plain-language message).
5. **Branch.** "Branch without xgboost." -> `branch_experiment` (reuses the parent's
   split plan and holdout).
6. **Compare.** "Compare the two runs." -> `compare_experiments` (CV only).
7. **Propose a decision.** "Propose accepting the branch." -> `record_decision`
   (state `proposed`, actor `agent`), then `accept_proposal` for the hand-off.
8. **Human accepts.** In Studio open the project -> Decisions and accept or reject
   the proposals (spec, bootstrap refs, decision). Only then do refs move
   (`POST /v1/projects/{id}/refs/{kind}` with `proposal_id`, human session only).

## Scoring new data (batch predictions)

Upload the rows to score as a scoring dataset (no target column), e.g.
`dclab-cli data upload new.csv --project P --purpose scoring`, then ask "Score that
dataset with the model." -> `predict` (write tool; `experiments:write` scope), then
`get_prediction` until `completed` or `failed`. The agent sees status, row counts and
the feature-contract check (required / missing / ignored columns), never the predicted
rows; download the file yourself with `dclab-cli predict download ID -o FILE`.

## Replaying the golden scenarios

`python -m dclab_mcp.golden --scenario classification|regression|branch_compare`
runs the same fixed tool sequence the tests record (`apps/api/tests/golden/mcp/`)
against your live stack using the environment above (it creates a project and uploads
a small synthetic CSV). Regenerate the recorded transcripts after an intentional
change with `DCLAB_UPDATE_GOLDEN=1 pytest apps/api/tests/test_mcp_golden_transcripts.py`.

Tool reference and error shapes: `packages/dclab_mcp/README.md`.
