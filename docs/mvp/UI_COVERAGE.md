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
| Members and roles (read only) | `GET /v1/workspaces/{workspace_id}/members` (people only; owners and administrators; 403 `members_not_permitted`) | — | `/settings` (Members and roles; adding people is not in the screen) | done V7-B6 |
| Service tokens | `GET/POST /v1/service-tokens`, `POST …/{id}/revoke` | — | `/agents` → Service tokens tab (also `/app/settings`) | exists |
| Connect an agent (MCP/CLI) | — | — | `/agents` → Connect tab (copy `.mcp.json`, SDK, CLI, `/v1` conventions) | exists |
| Projects list / create / detail | `GET/POST /v1/projects`, `GET /v1/projects/{id}` | `inspect_project` | `/projects`, `/projects/[id]`; Home Projects table reads the list `summary` (goal, champion CV metrics, latest run; P4.15-A) | planned P4.1-A; list summary backend P4.15-A, screen P4.15-UI |
| Upload, list, inspect datasets | `GET/POST /v1/datasets`, `GET /v1/datasets/{id}` | `inspect_dataset` | New-project wizard step 1 (upload response carries the column profile); Data page `/projects/[id]/data` Versions tab (list, digest, used by) and Policy & access tab (`GET /v1/datasets/{id}` `policy`) | wizard step: done P4.1-B; Data page done P4.1-C |
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
| Lineage graph + staleness | `GET /v1/projects/{id}/graph` | `inspect_project` | Graph page `/projects/[id]/graph`: layered lineage (★ refs, stale marked orange + "stale", AI decision-point markers from `GET /v1/projects/{id}/decisions?decision_type=decision_point_resolved`), "As a list" fallback, older experiment windows via `cursor` | done P4.2-A |
| Impact of changing a node | `GET /v1/nodes/{kind}/{id}/impact` | — | Graph page node drawer "What becomes stale" | done P4.2-A |
| Refs (current spec/data/split/champion) | `GET /v1/projects/{id}/refs`, `GET/POST /v1/projects/{id}/refs/{kind}` (POST = If-Match move) | — | project header ref badges; Data page "Make current" (dataset ref); "Make champion" | dataset ref move done P4.1-C; champion planned P4.4-A |
| Decisions timeline | `GET/POST /v1/projects/{id}/decisions`, `GET /v1/decisions/{id}` | `list_decisions`, `record_decision` | Decisions tab | planned P4.4-A |
| Accept / reject / supersede | `POST /v1/decisions/{id}/accept|reject|supersede` | `accept_proposal` | decision card actions | planned P4.4-A |
| Model version | `GET /v1/model-versions/{id}` | `get_model` | Models tab, model page | planned P4.11-UI; `saved_format` (`package_v1` / `legacy_quarantined`, P7.1-A) is API-only until the P7.5-A Package & integrity tab; `release` (approval of the model in use, P7.2-A) is shown on the model page Summary ("Approved for scoring" line, V7-B2-UI) |
| Approved for scoring (model releases) | `GET /v1/projects/{project_id}/releases` (read only; written by `POST /v1/projects/{project_id}/refs/{ref_kind}` with `champion_model` = "Put this model in use"; tokens get no approver id) | `list_releases` | `/projects/[id]/models` → "Approved for scoring" tab (list newest first, "Approve for scoring", "Go back to this model", both through `POST /v1/projects/{project_id}/refs/{ref_kind}` with `champion_model`); model page Summary "Approved for scoring" line (`GET /v1/model-versions/{model_version_id}` `release`); Predictions "Scored with" (`GET /v1/predictions/{prediction_id}` `release`) | API + MCP done P7.2-A; screen done V7-B2-UI |

## New in Phase 4 (use and trust the model)

| Capability | `/v1` operation(s) | MCP tool | Studio screen | Status |
| --- | --- | --- | --- | --- |
| Score new data with a model | `POST /v1/model-versions/{id}/predictions`, `GET /v1/predictions/{id}`, `…/download` | `predict` | model page "Score new data" + download | shipped P4.9-A, P4.9-UI |
| Weekly scheduled scoring | `GET /v1/projects/{project_id}/scoring-schedule` (schedule or null, status on / off / paused_by_platform, newest 10 weeks), `PUT /v1/projects/{project_id}/scoring-schedule` (people with ML-write only; weekday + hour in UTC, `enabled`; writes a `scoring_schedule_changed` History entry), `GET /v1/projects/{project_id}/scoring-schedule/runs` (cursor); `GET /v1/predictions/{prediction_id}` carries `scheduled` / `scheduled_for` | `get_scoring_schedule` (read-only) | Predictions screen "weekly schedule; this week's list" (the list is the existing prediction download) | backend done (migration 0079, worker tick); Predictions screen "Weekly scoring" card exists (read, turn on / change / turn off with one confirm step, last 10 weeks, scheduled vs by-hand labels in the table) |
| Core trust checks (5) | `GET /v1/experiments/{id}/findings` | `get_findings` | findings panel on every experiment | planned P4.10-A / P4.10-UI |
| Model card | `GET /v1/model-versions/{id}/card` | `get_model_card` | model page "Card" tab, printable | planned P4.11-UI (API + MCP shipped in P4.11-A) |
| Dataset column profile | `GET /v1/datasets/{id}/profile` | `inspect_dataset` | Data page `/projects/[id]/data`: Columns & roles (rule role vs role used, training-row statistics), Leakage audit (with `GET /v1/experiments/{id}/findings`); New run target dropdown | done P4.1-C |
| Activity feed | `GET /v1/activity` (workspace or `?project_id=`; decision records + runs queued/finished + specialist/ops agent runs; typed one-line summaries) | — (SDK `client.activity.list`, CLI `dclab-cli activity`; MCP tool is a follow-up) | Home "Activity" | backend P4.15-A; screen planned P4.15-UI |
| Inbox | `GET /v1/inbox` (`tab=needs_decision|applied_automatically|done`, `?project_id=`; proposed decision records, agent / Jev / assistant proposals with assistant tool calls for their owner and approvers only, runs waiting for an answer, finished runs; each item names the existing accept / reject / supersede / revert / answer route and whether the caller may use it), `GET /v1/inbox/counts` | — (SDK `client.inbox.list` / `client.inbox.counts`, CLI `dclab-cli inbox list|counts`; proposals also through `list_proposals`; an inbox MCP tool is a follow-up) | `/inbox`, sidebar badge (`GET /v1/inbox/counts` `needs_decision`), Home preview | backend P4.16-A; screens planned P4.16-UI, P4.15-UI |
| Pipeline evidence | `GET /v1/model-builds/{id}`, `/events`, `/artifacts` | — | `/projects/[id]/pipeline/[experimentId]` | done P4.17-UI |
| In-app assistant | `GET/POST /v1/assistant/threads`, `GET /v1/assistant/threads/{id}`, `POST …/messages` (SSE) | — (external agents use MCP itself) | Lab page + assistant panel on every project page | backend P6.3-B; screen planned A3-UI |
| Assistant and agent proposals | `GET /v1/proposals`, `GET /v1/proposals/{id}`, `POST /v1/proposals/{id}/accept|reject|revert` (human only; one proposal model) | `list_proposals` | confirm cards in the Lab/panel, Inbox | backend P6.6-A; screens planned A4-A, P4.16-UI |
| Agent runs and review requests | `GET /v1/agent-runs`, `GET /v1/agent-runs/{id}`, `POST /v1/agent-reviews` | `request_agent_review` | Inbox "Ask for a review", agent run detail | backend P6.6-A; screens planned P4.16-UI |
| Governance console | `GET /v1/governance`, `POST /v1/governance/policy`, `POST /v1/governance/policy/{id}/accept` (owner/admin; body: reviewed `policy_digest`, consent acknowledgement), `POST /v1/governance/switches` (workspace kill switches), `POST /v1/agent-runs/{id}/replay` (human only) | `inspect_governance` (read-only) | `/governance`: Policy, Decision points & trust levels (R3 links), Budgets & spend, Kill switches, Incidents, Audit & replay | backend P6.11-A; screen planned P6.11-UI |

## Later phases (screen ships in the same phase as the backend)

| Capability | Backend prompt | Screen prompt |
| --- | --- | --- |
| All investigation checks | P5.1-A | renders in the P4.10 panel (no new screen) |
| Operating point (threshold) | P5.2-A: `GET /v1/experiments/{id}/operating-points` (out-of-fold curve, Pareto points, locked and chosen point; MCP `get_operating_points`), `POST /v1/experiments/{id}/operating-point` (people only; records an `operating_point_chosen` decision) | P5.2-UI (experiment page "Per-fold and threshold") |
| Improve loop | P5.4-A | P5.5-A (+ assistant `improve` tool, A5) |
| Improve: comparison verdict and test-set look count (ADR 0007 option B) | P5.5-B-read: `GET /v1/experiments/{id}/verdict` (`?against={id}`, default the parent; verdict from locked per-fold CV scores, Nadeau-Bengio margin at T = max(1, `new_candidates`), `comparisons_on_plan`, `test_set_looks`; SDK `experiments.verdict`, no MCP tool) | P5.5-B (guided Improve screen) |
| Agent runs and proposals | P6.4-A, P6.6-A, P6.10-A | A2-UI (Agent runs, Tool registry), A4-A, P4.16-UI (+ assistant skills, A6) |
| Jev answers at decision points | P6.7-A, P6.9-A | P6.7-UI (development role only), P4.17-UI decision points |
| Agent trust levels (R3) | P6.8-A | P6.8-UI (Agents & tools → Agent catalog) |
| Governance | P6.11-A (shipped) | P6.11-UI (`/governance`) |
| Business Outcomes | P7.6-A (done): `GET /v1/projects/{project_id}/outcomes` (any workspace reader, business users included; the model in use, plain sentences from its single final test (service tokens: `withheld`), how it was built in three sentences, the latest completed list (download through `GET /v1/predictions/{prediction_id}/download`), limits, boundary; SDK `projects.outcomes`; no MCP tool), `GET /v1/outcomes/projects` (projects with a model in use: id and name only, at most 50; SDK `projects.outcome_projects`) | P7.6-A (`/outcomes`, business users land there) |
| Per-row reasons | P7.7-A (done, API-only): `reason_1`..`reason_3` appended to the file of `GET /v1/predictions/{prediction_id}/download`; `reasons` record (`available`, `method`, `additive`, `top_k`, why unavailable) on `GET /v1/predictions/{prediction_id}`; no MCP change (agents never get the file) | Outcomes list, Models → Batch predictions (planned) |
| Notifications, webhooks | P8.8-A | Settings → Notifications, Integrations |
| Releases, batch prediction | P7.2-A | P7.5-A |
| MLflow mirror | P7.3-A | P7.3-A (link + `tracking_degraded` badge on model page) |
| Drift windows | P7.4-A (done, API + SDK + MCP; migration 0080): `GET /v1/projects/{project_id}/monitoring` (model in use: latest status ok / watch / alert / not_enough_data, last 12 weekly windows, code-owned thresholds, the alert finding), `GET /v1/projects/{project_id}/monitoring/windows/{window_start}` (one week: PSI per feature with plain sentences, score drift, outcomes), `GET /v1/projects/{project_id}/monitoring/outcomes` (linked outcome files; tokens get no user id); SDK `client.monitoring.get|window|outcomes` | MCP `get_monitoring` (read-only; codes and numbers, no column names); P7.5-A (done, web; verified against stubbed reads): `/projects/{project_id}/monitoring` (sidebar and flow bar "Monitoring") reads `GET /v1/projects/{project_id}/monitoring` (status headline, weekly checks table) and `GET /v1/projects/{project_id}/monitoring/windows/{window_start}` (one week: top columns as an inline bar chart of PSI with the read-only watch / alert lines, plain column sentences, date/ID caveat, score movement, real-world results); on an alert, "Go back to the previous model" opens the Model screen's own confirm step (`POST /v1/projects/{project_id}/refs/champion_model`, no second write path) |
| Link real outcomes (monitoring) | P7.4-A (done, API + SDK): `POST /v1/projects/{project_id}/monitoring/outcomes` (people with ML-write only, `Idempotency-Key`; an uploaded file's key and outcome columns; writes a `monitoring_outcomes_linked` History entry; `409 already_linked`, `429 rate_limited` above 10 links per project and hour; the worker recomputes the recent weeks, debounced); SDK `client.monitoring.link_outcomes` (user token) | P7.5-A (done, web; verified against stubbed reads): Monitoring → "Link real outcomes" (upload with `POST /v1/datasets` or pick a file of the project; key and outcome columns from `GET /v1/datasets/{dataset_id}/profile`; one confirm step, then `POST /v1/projects/{project_id}/monitoring/outcomes`; buttons only with ML-write) and "Linked files" (`GET /v1/projects/{project_id}/monitoring/outcomes`); no MCP tool (tokens and agents cannot link) |
| Compute cost per run | P8.3-A | P8.3-A (cost column in Experiments tab) |
| Usage and quotas | P8.4-A | P8.4-A (usage page) |
| System health | P8.5-A | P8.5-UI (`/operator` console) |
| Quarantine review | P8.7-A | P8.7-A (operator review page) |

## API-only by design

| Operation | Reason |
| --- | --- |
| `GET /health` | infrastructure probe |
| Idempotency-Key / ETag headers | transport mechanics; the UI client sets them automatically |
