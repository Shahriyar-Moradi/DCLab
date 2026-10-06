# UI coverage — every capability has a screen

Rule (EXECUTION_GUIDE.md §4): a user-facing capability is not done until it is
reachable in Studio, or listed here as **API-only by design** with a reason.
Every `/v1` operation and every MCP tool has one row. `P4.0-A` adds a test that
fails when `contracts/v1_openapi.json` gains an operation without a row here.

Status: **exists** · **planned** (prompt that ships it) · **API-only** (reason).
Screen design: [design/STUDIO_DESIGN.md](design/STUDIO_DESIGN.md) §4 maps every
prototype screen to its route; this file tracks each backend operation.
Snapshot: 2026-10-04, `main` at d221407 (end of Phase 3).

## Phases 0–3 (backend done, UI catch-up in Phase 4)

| Capability | `/v1` operation(s) | MCP tool | Studio screen | Status |
| --- | --- | --- | --- | --- |
| Who am I, workspaces | `GET /v1/me`, `GET /v1/workspaces` | — | header workspace switcher | exists |
| Service tokens | `GET/POST /v1/service-tokens`, `POST …/{id}/revoke` | — | `/app/settings` → tokens panel | exists |
| Connect an agent (MCP/CLI) | — | — | Settings → "Connect Claude Code" (copy `.mcp.json`, CLI login) | planned P4.8-UI |
| Projects list / create / detail | `GET/POST /v1/projects`, `GET /v1/projects/{id}` | `inspect_project` | `/projects`, `/projects/[id]` | planned P4.1-A |
| Upload, list, inspect datasets | `GET/POST /v1/datasets`, `GET /v1/datasets/{id}` | `inspect_dataset` | New-project wizard step 1 (upload response carries the column profile); Data tab | wizard step: done P4.1-B; Data tab planned P4.1-C |
| Problem spec (propose / create) | `POST /v1/projects/{id}/problem-specs` | `propose_problem_spec`, `create_problem_spec` | New-project wizard steps 2–3 (target, task, objective, constraints) | done P4.1-B |
| Target / split confirmation | `POST /v1/execution-requests/{id}/target-confirmation|split-confirmation` | — | experiment page "needs your answer" panel (rule beside AI suggestion) | done P4.1-B |
| Run an experiment | `POST /v1/experiments`, `POST /v1/execution-requests`, `GET …/{id}` | `run_experiment` | wizard "Train" + Experiments tab "New run" | done P4.1-B |
| Live run progress | `GET /v1/model-builds/{id}`, `…/events`, `…/artifacts`, `…/visualizations` | — | experiment page progress + charts | exists (legacy `/lab/runs/[run_id]`), moved in P4.1-A |
| Cancel a run | `POST /v1/experiments/{id}/cancel` | — | experiment page "Cancel" | planned P4.4-A |
| Experiments list / detail | `GET /v1/experiments`, `GET /v1/experiments/{id}` | `get_experiment` | Experiments tab, experiment page | planned P4.1-A, P4.3-A |
| Evidence (CV metrics, plan, leakage) | `GET /v1/experiments/{id}` | `get_evidence` | experiment inspector "Evidence" | planned P4.3-A |
| Reproducible code | `GET /v1/experiments/{id}/code` | `get_experiment_code` | experiment inspector "Code" (copy/download) | planned P4.3-A |
| Compare experiments | `GET /v1/experiments/compare` | `compare_experiments` | compare view | planned P4.4-A |
| Branch with a change set | `POST /v1/experiments/{id}/branches` | `branch_experiment` | "Branch" form on experiment | planned P4.4-A |
| Lineage graph + staleness | `GET /v1/projects/{id}/graph` | `inspect_project` | Graph tab | planned P4.2-A |
| Impact of changing a node | `GET /v1/nodes/{kind}/{id}/impact` | — | node inspector "What becomes stale" | planned P4.2-A |
| Refs (current spec/data/split/champion) | `GET /v1/projects/{id}/refs`, `GET/POST …/refs/{kind}` (POST = If-Match move) | — | project header ref badges; "Make champion" | planned P4.4-A |
| Decisions timeline | `GET/POST /v1/projects/{id}/decisions`, `GET /v1/decisions/{id}` | `list_decisions`, `record_decision` | Decisions tab | planned P4.4-A |
| Accept / reject / supersede | `POST /v1/decisions/{id}/accept|reject|supersede` | `accept_proposal` | decision card actions | planned P4.4-A |
| Model version | `GET /v1/model-versions/{id}` | `get_model` | Models tab, model page | planned P4.11-UI |

## New in Phase 4 (use and trust the model)

| Capability | `/v1` operation(s) | MCP tool | Studio screen | Status |
| --- | --- | --- | --- | --- |
| Score new data with a model | `POST /v1/model-versions/{id}/predictions`, `GET /v1/predictions/{id}`, `…/download` | `predict` | model page "Score new data" + download | planned P4.9-A / P4.9-UI |
| Core trust checks (5) | `GET /v1/experiments/{id}/findings` | `get_findings` | findings panel on every experiment | planned P4.10-A / P4.10-UI |
| Model card | `GET /v1/model-versions/{id}/card` | `get_model_card` | model page "Card" tab, printable | planned P4.11-UI (API + MCP shipped in P4.11-A) |
| Dataset column profile | `GET /v1/datasets/{id}/profile` | `inspect_dataset` | Data page: Columns & roles, Policy | planned P4.1-C |
| Activity feed | `GET /v1/activity` | — | Home "Activity" | planned P4.15-A / P4.15-UI |
| Inbox | `GET /v1/inbox`, counts | `list_proposals` (P6.6-A) | `/inbox`, sidebar badge, Home preview | planned P4.16-A / P4.16-UI |
| Pipeline evidence | `GET /v1/model-builds/{id}`, `/events`, `/artifacts` | — | `/projects/[id]/pipeline/[experimentId]` | planned P4.17-UI |
| In-app assistant | `GET/POST /v1/assistant/threads`, `GET /v1/assistant/threads/{id}`, `POST …/messages` (SSE) | — (external agents use MCP itself) | Lab page + assistant panel on every project page | backend P6.3-B; screen planned A3-UI |
| Assistant and agent proposals | `GET /v1/proposals`, `GET /v1/proposals/{id}`, `POST /v1/proposals/{id}/accept|reject|revert` (human only; one proposal model) | `list_proposals` | confirm cards in the Lab/panel, Inbox | backend P6.6-A; screens planned A4-A, P4.16-UI |
| Agent runs and review requests | `GET /v1/agent-runs`, `GET /v1/agent-runs/{id}`, `POST /v1/agent-reviews` | `request_agent_review` | Inbox "Ask for a review", agent run detail | backend P6.6-A; screens planned P4.16-UI |
| Governance console | `GET /v1/governance`, `POST /v1/governance/policy`, `POST /v1/governance/policy/{id}/accept` (owner/admin; body: reviewed `policy_digest`, consent acknowledgement), `POST /v1/governance/switches` (workspace kill switches), `POST /v1/agent-runs/{id}/replay` (human only) | `inspect_governance` (read-only) | `/governance`: Policy, Decision points & trust levels (R3 links), Budgets & spend, Kill switches, Incidents, Audit & replay | backend P6.11-A; screen planned P6.11-UI |

## Later phases (screen ships in the same phase as the backend)

| Capability | Backend prompt | Screen prompt |
| --- | --- | --- |
| All investigation checks | P5.1-A | renders in the P4.10 panel (no new screen) |
| Operating point (threshold) | P5.2-A | P5.2-UI |
| Improve loop | P5.4-A | P5.5-A (+ assistant `improve` tool, A5) |
| Agent runs and proposals | P6.4-A, P6.6-A, P6.10-A | A2-UI (Agent runs, Tool registry), A4-A, P4.16-UI (+ assistant skills, A6) |
| Jev answers at decision points | P6.7-A, P6.9-A | P6.7-UI (development role only), P4.17-UI decision points |
| Agent trust levels (R3) | P6.8-A | P6.8-UI (Agents & tools → Agent catalog) |
| Governance | P6.11-A (shipped) | P6.11-UI (`/governance`) |
| Business Outcomes | P7.6-A | P7.6-A (`/outcomes`) |
| Per-row reasons | P7.7-A | Outcomes list, Models → Batch predictions |
| Notifications, webhooks | P8.8-A | Settings → Notifications, Integrations |
| Releases, batch prediction | P7.2-A | P7.5-A |
| MLflow mirror | P7.3-A | P7.3-A (link + `tracking_degraded` badge on model page) |
| Drift windows | P7.4-A | P7.5-A |
| Compute cost per run | P8.3-A | P8.3-A (cost column in Experiments tab) |
| Usage and quotas | P8.4-A | P8.4-A (usage page) |
| System health | P8.5-A | P8.5-UI (`/operator` console) |
| Quarantine review | P8.7-A | P8.7-A (operator review page) |

## API-only by design

| Operation | Reason |
| --- | --- |
| `GET /health` | infrastructure probe |
| Idempotency-Key / ETag headers | transport mechanics; the UI client sets them automatically |
