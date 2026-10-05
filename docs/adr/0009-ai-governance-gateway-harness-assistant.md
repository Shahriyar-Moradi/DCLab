# ADR 0009 — AI governance, gateway, harness and the in-app assistant

**Status:** Accepted (founder, 2026-10-04; decisions recorded in § Founder decisions)  
**Date:** 2026-10-04  
**Prompt:** P6.1-A (design only; absorbs Track A's A1-A)  
**Depends on:** [0008-hybrid-ai-decision-model.md](0008-hybrid-ai-decision-model.md),
[0006-ml-state-graph.md](0006-ml-state-graph.md) §5/§7, [0005-upload-policy.md](0005-upload-policy.md)
item 7, live Alembic head `0070_investigation_findings` (verified 2026-10-04)  
**Consumed by:** P6.2-A (tables), P6.2-B (gateway), P6.10-A (harness, tool catalog),
P6.3-A (runtimes), P6.3-B (lead loop, threads API), P6.6-A (proposals), P6.7-A (Jev),
P6.10-B (test harness), P6.11-A (governance API), A2-UI, A3-UI, A4-A, A8-A

### Revision 1 (review fixes, 2026-10-04)

Changes after the security and ML-correctness reviews of the first draft
(status unchanged: Proposed).

- **Holdout in the service layer (§5.1, §6, §8).** `strip_holdout`, `cv_only`,
  `cv_record`, `withhold_holdout_code` and the model-card withholding move into
  `app/agents/tools/shaping.py`; every shaper and the context builder apply
  them whenever the consumer is an agent run, whatever the principal; shapers
  declare `data_class`, `outcome_scope` and source datasets/columns; the
  gateway redacts tool results in the transcript too.
- **Authority (§3).** `ai_policy:approve` is not a `workspace_capability_service`
  flag (those are workspace-wide presentation hints); it is the role-derived
  check `can_approve_ai_policy` next to `can_manage_workspace_members`
  (unsuspended owner/admin membership, no client fallback); platform staff may
  seed platform rows and switch off, never approve customer policies.
- **Policy document trimmed (§3).** `data.mark_untrusted`,
  `retention.raw_prompts`, `approvals.*` are code constants; caps only narrow;
  `incidents.*` only stricter; the data-exposure auto-demotion cannot be
  disabled; proposals carry `base_version` (409 on head mismatch); policy load
  or validation failure is a refusal (fail closed); `AI_ENABLED` defaults
  false and the fake provider is forbidden in production.
- **Kill switches get their own table (§2.11, §4).** `ai_switches` chains per
  switch key outside the policy document; off always succeeds; a policy accept
  never supersedes a switch; re-enable needs an approver; an incident's
  `switch_off` clears only on resolution; a runtime platform switch exists
  besides the environment flag.
- **Tenancy (§2).** Cache digests include workspace, data class and the dated
  model id; lookups filter by workspace; CHECKs make subject ids imply
  `project_id`; platform rows use plain FKs with chain triggers on
  `workspace_id IS NOT DISTINCT FROM`; NULLS NOT DISTINCT uniques.
- **Proposals and accepts (§2.3, §7.4).** Conditional UPDATE from `proposed`
  in the command's transaction (0 rows → 409); transition-table trigger; tool
  validator re-run at accept; `If-Match` for ref moves; revert needs the
  command's authority; tokens refused; idempotency keys store request digest
  and creator; human-only `rationale`.
- **Budgets (§4).** `held_micros` per run; settle per call keyed by
  invocation id; refuse a call whose worst case exceeds the remaining hold;
  ordered locks; `ON CONFLICT DO NOTHING`; CHECK `reserved_micros ≥ 0`; P8.4
  `usage_records` derive from this ledger, never a second counter.
- **Harness and runtimes (§5).** Replay never re-runs live tools (results are
  digest + preview) and write tools are stubbed; `DCLAB_`-prefixed provider
  keys read only in `gateway/providers`; `nooa_runtime` asserts adapter and
  strategy at runtime; import-linter bans NOOA CodeAct/sandbox modules;
  worker egress allowlist; `agents.run` payload = `agent_run_id` only.
- **Assistant (§7).** All write tools L1 (no `job` effect); posting needs
  ML-write plus per-user rate and concurrency limits; only the creator reads or
  posts a thread; every turn re-checks membership on the thread's workspace;
  SSE through the existing `bff-proxy.ts`; disconnect handling; Markdown subset
  without raw HTML/images; internal citation links only.
- **Data exposure (§8).** ADR 0005 `deny` = contributes nothing;
  `metadata_only` and `aggregate_only` are the existing labels; column labels
  join the min(); unknown source → most restrictive; `data.user_text_to_jev`
  flag (default off); sample values exclude identifiers and
  `sensitivity = restricted` columns.
- **Retention (§2.2, §2.10).** `agent_events` and `llm_invocations` get a
  retention-aware trigger (`prevent_mutation_except_retention`) instead of the
  unconditional `prevent_canonical_row_mutation`, so the 365-day job and the
  workspace cascade can work; `llm_invocations` identity columns frozen.
- ARCHITECTURE.md §3.4 provider-key sentence aligned with the assistant turn
  running in the API process (founder Q1); founder questions updated.

### Revision 2 (re-review fixes, 2026-10-04)

- **No holdout, ever (§5.1, §6, §7.6).** The champion `holdout_report_only`
  exception of ADR 0008 is gone; the champion's final evaluation is a
  deterministic *Champion final evaluation* template rendered outside any
  model context; the property test reads "no holdout, ever".
- **Chain triggers (§2.9, §2.11).** `ai_incidents.action_level_policy_id`,
  `ai_incidents.action_switch_id` and `ai_switches.incident_id` get the same
  `workspace_id IS NOT DISTINCT FROM` trigger as the supersede chains.
- **Settle arithmetic (§4).** Settle adds the actual cost to `spent` and
  subtracts only the remaining hold from `reserved`, so a janitor release
  before a late settle cannot violate `reserved_micros ≥ 0`.
- **Platform switches (§2.11).** A `dclab_admin` turns a platform switch back
  on through `dclab governance switch on <key>` (audited row); workspace
  switches need `can_approve_ai_policy`.
- **Audit notice (§7.2).** The composer states that workspace approvers can
  read the thread for audit and platform roles can view replays.
- **Retention hardening (§2.2, §2.10).** The retention GUC is `SET LOCAL`
  only; the trigger checks `OLD.created_at < now() - horizon` (horizon from a
  second GUC) or a deletion GUC equal to `OLD.workspace_id`, never a boolean;
  the existing `ON DELETE CASCADE` from `experiments` / `workflow_runs` into
  `llm_invocations` is only satisfiable with the deletion GUC set, so the
  workspace- and experiment-deletion paths set it and test teardown uses
  `TRUNCATE … CASCADE` (row triggers do not fire).

## Context

ROADMAP § "AI governance, gateway and harness" fixes three layers above the
agents: governance (policy as data), the gateway (the only door to any model)
and the harness (the only way to run an agent). `AGENTS_NOOA_JEV.md` §3, §5b
and §6 sketch their modules and tables; `prompts/ASSISTANT.md` fixes seven rules
for the in-app assistant, which is the lead agent of ADR 0008 §6. This ADR
turns the sketches into schemas, contracts and lifecycles precise enough to
implement in order P6.2-A → P6.2-B → P6.10-A → P6.3-A → P6.7-A → P6.3-B.

Constraints verified in code on 2026-10-04:

- `llm_invocations` already has `project_id`, `agent_run_id` (no FK yet),
  `provider_kind` (`llm_provider` / `semantic_decision` / `agent_runtime` /
  `deterministic_fallback`), `input_evidence_digest`, `redaction_summary`,
  `prompt_version`, token and cost columns, and a `purpose` CHECK limited to the
  six legacy purposes. Four writers exist: `lab_decision_ledger.py`
  (`observability_service.create_llm_invocation` + `finalize_llm_invocation`,
  which UPDATEs status, verdict, outputs and timings), `pipeline_audit_service.py`,
  and the provider code they call (`engine/lab/llm_client.py` raw `httpx` to
  OpenAI; `services/openai_provider.py`).
- `project_decision_records.actor_agent_run_id` exists without FK (ADR 0006 §5);
  `actor_service_token_id` has its CFK (P3.2-A).
- Every tenant table follows `UNIQUE(workspace_id, id)` + composite FKs
  (`db/integrity.py`), append-only ledgers use the unconditional
  `prevent_canonical_row_mutation()` (which also blocks `ON DELETE CASCADE`
  from `workspaces`), column freezes use `prevent_canonical_column_mutation()`,
  bounded JSONB uses `octet_length` and forbidden-key CHECKs (ADR 0006 §5/§7).
- Authority is role-derived in `authorization_service.py`
  (`can_manage_workspace_members`: platform `dclab_admin`, else an active
  `workspace_owner` / `workspace_admin` membership; `can_execute_workspace_ml`
  for ML writes). `workspace_capability_service.py` returns presentation hints
  ("never credentials or authorization claims"). Service-token scopes are
  `read`, `projects:write`, `datasets:write`, `experiments:write`,
  `decisions:propose`.
- Final-holdout values are withheld only for service-token principals, at the
  route layer (`api/v1_agent_views.py`) and in the MCP server
  (`packages/dclab_mcp/server.py` `cv_only` / `cv_record` /
  `withhold_holdout_code`); `shaping.py` bounds and marks untrusted text.
  `packages/dclab_mcp` cannot import `app` (it talks to `/v1` through the SDK).
- ADR 0005 labels every published upload and column with
  `llm_exposure_policy = deny` and `sensitivity = restricted`; the label
  vocabulary (`domain/privacy_audit.py` `DATA_USE_POLICIES`) is `allow`,
  `deny`, `aggregate_only`, `metadata_only`; production refuses to start with
  the two LLM flags on until "the Phase 6 gateway checks `llm_exposure_policy`".
- The BFF proxy (`apps/web/lib/infrastructure/bff-proxy.ts`) forwards the
  session cookie, CSRF header and `Origin`, never a bearer token, and caps
  request and response sizes (`DEFAULT_MAX_UPLOAD_BYTES`,
  `DEFAULT_MAX_DOWNLOAD_BYTES`).

## Decision

### Summary (ten lines)

1. Ten new tables and two additive FKs in **one expand-only migration**
   (`0071_agents_governance`, P6.2-A); all tenant-scoped with composite FKs;
   events and answers append-only (retention-aware); policies, levels and
   switches are append-only version chains with derived "effective".
2. **Governance is data**: `AiPolicyV1` (Pydantic) per workspace on top of a
   code-owned platform default with caps; `effective_policy(workspace)` is the
   only reader and fails closed.
3. **Gateway**: `complete(request)` for LLMs, `decide(request)` for Jev; typed
   refusals, never exceptions to callers; one `llm_invocations` row per call,
   cache hits included; switch precedence global → workspace → agent →
   purpose → provider breaker.
4. **Redaction is structural**: every envelope and tool-result field carries a
   data class, an outcome scope and its source; fields above the effective
   class or scope are dropped before any provider sees them; user text and
   column names travel as `{"untrusted_text": …}`.
5. **Budgets** are reserve → settle on locked counter rows with a per-run hold;
   over budget is a typed refusal and the rule path.
6. **Harness**: `AgentService.run(spec)` is the only entry; fixed lifecycle,
   five hooks with closed effect sets, immutable run header, agent consumer
   mode (holdout-blind), replay that asserts tool-sequence and output-digest
   equality without re-running tools.
7. **One tool catalog** (`app/agents/tools/`) exported to
   `contracts/agent_tools.json`, consumed by MCP, the lead agent and Studio;
   read tools run, write tools propose; a forbidden-operation list is tested.
8. **Assistant = lead agent**: `AssistantStep` schema, threads under
   `/v1/assistant/threads…`, SSE through the existing BFF proxy, human session
   with ML-write only, confirm = `POST /v1/proposals/{id}/accept`,
   deterministic templates when the LLM is off.
9. **Data exposure** ties to ADR 0005: effective class = min(workspace policy,
   point, every involved dataset **and column** label); `deny` contributes
   nothing; raw rows have no code path.
10. **Pins** (verified 2026-10-04): `nooa==0.0.10`, `litellm==1.104.0`,
    `typesafe-sdk==0.7.2`, Jev `jev-1.13.0`; all in an optional `agents` extra.

### 1. Module layout (owners)

```
apps/api/app/agents/
  contracts.py            ContextEnvelope (fields tagged data_class/outcome_scope/source), AgentRunSpec,
                          AssistantStep, proposal payload types, Citation
  governance/
    platform_default.py   code-owned AiPolicyV1 default + CAPS + code constants (never editable at runtime)
    policy.py             GovernancePolicyService.effective_policy(workspace) -> EffectivePolicy (fail closed)
    decision_points.py    registry (ADR 0008 §1) + effective_level(workspace, key, answer)
    switches.py           ai_switches chains: effective_switches(workspace); flip_off / re_enable
    incidents.py          open / auto_demote / resolve (ADR 0008 §4)
    console.py            read model for GET /v1/governance (P6.11-A)
  gateway/                the only door to any model
    contract.py router.py redaction.py budget.py cache.py limits.py ledger.py service.py
    providers/            openai.py typesafe_jev.py fake.py  (ONLY SDK imports; ONLY readers of DCLAB_*_API_KEY;
                          litellm.py only if a non-OpenAI provider is ever allowlisted — none in the MVP)
  harness/
    service.py context.py hooks.py validation.py recorder.py replay.py
  tools/                  catalog.py + definitions/<tool>.py + shaping.py (strip_holdout, cv_only, cv_record,
                          withhold_holdout_code, bound, untrusted); export to contracts/agent_tools.json
  runtime/                base.py fake_runtime.py nooa_runtime.py lead_runtime.py
  semantic/               port.py deterministic.py releases.py policy.py  (Jev purposes; SDK via gateway)
  classes/                experiment_critic.py dataset_investigator.py experiment_planner.py
                          improvement_hypothesis.py lead_agent.py ops_agent.py
  prompts/<agent_key>/v<N>.md    code-owned prompt text; digest → prompt_releases
  templates/              deterministic LLM-off answers (pure functions over read models)
```

CI rules (import-linter contracts in `pyproject.toml`, P6.2-B/P6.10-A): (a)
`openai`, `litellm`, `nooa.unifiedllm`, `typesafe_sdk`, `anthropic` and
`httpx` calls to provider hosts are allowed only under
`app.agents.gateway.providers`; (b) `nooa` only under
`app.agents.runtime.nooa_runtime`, and `nooa.strategies.codeact`,
`nooa.runtime.sandbox` and `nooa.cli` are banned everywhere; (c)
`app.agents.runtime.*` and `app.agents.classes.*` are imported only by
`app.agents.harness`; (d) the job handler `agents.run` and the assistant
service are the only callers of `AgentService.run` besides tests; (e) provider
secrets are `DCLAB_<PROVIDER>_API_KEY` environment variables read by
`providers/*.py` only (a grep test fails on any other reader; the legacy
`decision_agent_api_key` / `pipeline_llm_verifier_api_key` settings are retired
in P6.2-B). The legacy `engine/lab/llm_client.py` HTTP code moves into
`gateway/providers/openai.py`; the module keeps its evidence dataclasses and
result models. Worker and API egress is an allowlist of provider hosts
(deployment rule, P8.1), and the `agents.run` job payload carries only
`agent_run_id`; the handler re-authorizes from the run row.

### 2. Persistence (migration `0071_agents_governance`, P6.2-A)

Conventions for every table below: `id uuid PK`; `workspace_id uuid NOT NULL
FK workspaces(id) ON DELETE CASCADE` plus `UNIQUE(workspace_id, id)`; every
reference to another tenant row is a composite FK `(workspace_id, x_id) →
parent(workspace_id, id)`; `created_at timestamptz NOT NULL DEFAULT now()`;
JSONB columns are objects (or arrays where stated) bounded by `octet_length`
and the ADR 0006 forbidden-key CHECK; varchar keys use
`~ '^[a-z][a-z0-9_.:-]{0,63}$'`; FK columns get indexes. **Platform rows**
(`workspace_id IS NULL`, only on `ai_policies`, `decision_point_policies`,
`ai_switches`, `ai_incidents`) use a plain `workspace_id uuid NULL FK` (no
`UNIQUE(workspace_id, id)` participation), uniques declared `NULLS NOT
DISTINCT`, and a chain trigger that requires `NEW.workspace_id IS NOT DISTINCT
FROM (SELECT workspace_id FROM <table> WHERE id = NEW.supersedes_id)`.

**Subject CFKs with nullable `project_id`.** Where a table has three-column
subject FKs `(workspace_id, project_id, x_id)` and a nullable `project_id`
(`agent_runs`, `semantic_decision_answers`), MATCH SIMPLE would skip the FK
when `project_id` is NULL; a CHECK `num_nonnulls(experiment_id, dataset_id,
problem_spec_id, split_plan_id, model_version_id) = 0 OR project_id IS NOT
NULL` closes that gap.

#### 2.1 `agent_runs`

| Column | Type | Notes |
| --- | --- | --- |
| `project_id` | uuid null | CFK `projects`; NOT NULL unless `kind = 'ops'` with a workspace subject; CHECK above |
| `kind` | varchar(16) | CHECK IN (`assistant`, `lead`, `specialist`, `ops`) |
| `agent_key` | varchar(64) | registry key (`lead`, `experiment_critic`, …); for `assistant` always `lead` |
| `agent_version` | varchar(32) | class version |
| `prompt_release_id` | uuid null | FK `prompt_releases(id)`; NULL only for `fake` runtime |
| `runtime` | varchar(16) | CHECK IN (`fake`, `nooa_predict`, `lead_loop`) |
| `runtime_version` | varchar(64) | e.g. `nooa==0.0.10` |
| `provider`, `model` | varchar(32), varchar(128) null | resolved by the router at start; dated model id; may change per call (ledger has the truth) |
| `purpose` | varchar(64) | gateway purpose / decision point key for specialists; `assistant.turn` for threads |
| `decision_point_key` | varchar(64) null | ADR 0008 key when the run serves one point |
| `subject_kind` | varchar(32) null | ADR 0006 subject kinds + `thread`, `monitoring_window` |
| `experiment_id`, `dataset_id`, `problem_spec_id`, `split_plan_id`, `model_version_id` | uuid null | three-column CFKs with `project_id` exactly as `project_decision_records`; `ck_agent_runs_subject_matches_kind` |
| `parent_run_id` | uuid null | CFK `agent_runs`; child runs (`request_agent_review`) |
| `context_digest` | char(64) null | sha256 of the redacted envelope |
| `policy_digest` | char(64) | digest of the effective policy + levels + switches snapshot taken at claim |
| `tool_catalog_digest` | char(64) | sha256 of `contracts/agent_tools.json` in use |
| `data_class` | varchar(16) | effective class (§8) |
| `outcome_scope` | varchar(8) | CHECK IN (`none`, `cv`) — never `holdout` (ADR 0008 §2b) |
| `status` | varchar(24) | CHECK IN (`queued`, `running`, `waiting_user`, `completed`, `failed`, `rejected_by_validator`, `over_budget`, `timed_out`, `cancelled`, `closed`) |
| `limits` | jsonb | `RunLimits` (steps, tokens, wall_s, cost_micros, tool_calls) ≤ 2 KB |
| `usage` | jsonb | `RunUsage` (steps, calls, tokens_in/out, wall_ms, tool_calls, cache_hits) ≤ 2 KB |
| `held_micros`, `cost_micros`, `currency` | bigint, bigint, char(3) | budget hold for the run; settled cost; `USD` |
| `error_code` | varchar(64) null | typed refusal/failure code |
| `idempotency_key`, `idempotency_digest` | varchar(128) null, char(64) null | `UNIQUE(workspace_id, idempotency_key)` partial; replay with a different digest or creator → 422 |
| `created_by_user_id` | uuid null | FK users NO ACTION; the thread owner for `assistant` |
| `created_by_service_token_id` | uuid null | CFK `service_tokens` |
| `title` | varchar(200) null | assistant threads (untrusted text) |
| `page_context` | jsonb null | last hint the client sent (route, node kind/id) ≤ 1 KB; a hint, re-resolved server-side |
| `last_activity_at`, `started_at`, `finished_at` | timestamptz | |

Indexes: `(workspace_id, project_id, created_at DESC)`, `(workspace_id, kind,
status)`, `(agent_key, created_at DESC)`, partial `(created_by_user_id,
last_activity_at DESC) WHERE kind = 'assistant'`. Partial unique
`(workspace_id, agent_key, subject_kind, experiment_id, dataset_id,
model_version_id) WHERE status IN ('queued','running') AND kind <> 'assistant'`
(one active run per subject and agent). Mutable columns: `status`, `usage`,
`cost_micros`, `error_code`, `provider`, `model`, `title`, `page_context`,
`last_activity_at`, `started_at`, `finished_at`; `prevent_canonical_column_mutation`
freezes the rest (precedent: `service_tokens`).

#### 2.2 `agent_events` (append-only, retention-aware)

| Column | Type | Notes |
| --- | --- | --- |
| `run_id` | uuid | CFK `agent_runs`; `UNIQUE(run_id, seq)` |
| `seq` | integer | 1-based per run, assigned under the run row lock |
| `type` | varchar(48) | CHECK IN the list of §5.4 |
| `payload` | jsonb | bounded ≤ 16 KB; redacted; forbidden keys; tool results stored as digest + bounded preview (never the full result) |
| `payload_digest` | char(64) | sha256 of canonical payload (replay) |
| `llm_invocation_id` | uuid null | CFK `llm_invocations` for call events |
| `created_at` | timestamptz | |

Trigger `agent_events_append_only` BEFORE UPDATE OR DELETE → new helper
`prevent_mutation_except_retention()` in `db/integrity.py`: UPDATE always
refused; DELETE allowed only when **either** `OLD.created_at < now() -
current_setting('dclab.retention_horizon')::interval` (the retention job,
P8.7) **or** `current_setting('dclab.deleting_workspace', true)::uuid =
OLD.workspace_id` (the workspace-deletion path) — never a plain boolean flag.
Both GUCs are set with `SET LOCAL` inside the job's transaction, so they
cannot leak to another request on a pooled connection. This is the only
difference from `prevent_canonical_row_mutation()` and it is what makes the
365-day retention and the `workspaces` cascade workable. Test teardown uses
`TRUNCATE … CASCADE` (row-level triggers do not fire) or sets the deletion
GUC. Index `(run_id, seq)`, `(workspace_id, created_at DESC)`.

#### 2.3 `agent_proposals`

| Column | Type | Notes |
| --- | --- | --- |
| `project_id` | uuid | CFK `projects` |
| `run_id` | uuid | CFK `agent_runs` |
| `decision_point_key` | varchar(64) | ADR 0008 key |
| `level_at_proposal`, `answer_ceiling` | smallint | CHECK 0..3; ceiling from ADR 0008 §1b |
| `proposal_type` | varchar(64) | `ExperimentPlanProposal`, `ExperimentReviewProposal`, `DatasetInvestigationProposal`, `ImprovementActionProposal`, `ToolCallProposal` (lead write tools), `ReleaseProposal` (Phase 7) |
| `schema_version` | integer | |
| `payload` | jsonb | validated proposal ≤ 32 KB; immutable |
| `payload_digest` | char(64) | |
| `rule_answer` | jsonb null | the deterministic answer at proposal time ≤ 8 KB |
| `citations` | jsonb | array ≤ 64 of `{kind, id, metric?}`; validated against project nodes; CV scope only |
| `validator_verdict` | varchar(16) | CHECK IN (`accepted`, `rejected`) |
| `validator_reasons` | jsonb | array of typed reasons ≤ 4 KB |
| `status` | varchar(24) | CHECK IN (`shadow`, `proposed`, `rejected_by_validator`, `accepted`, `rejected`, `applied`, `reverted`, `superseded`, `expired`) |
| `supersede_reason` | varchar(32) null | `plan_exists`, `results_exist`, `newer_proposal` (ADR 0008 §2) |
| `subject_kind` + subject id columns | as `agent_runs` | the node the proposal is about |
| `tool_name`, `tool_arguments` | varchar(64) null, jsonb null | for `ToolCallProposal`: the catalog tool and its validated arguments ≤ 8 KB (rendered first on confirm cards) |
| `proposed_rationale` | varchar(4000) null | the agent's text; always untrusted |
| `estimated_cost_micros`, `estimated_duration_s` | bigint null, integer null | shown on confirm cards |
| `expires_at` | timestamptz null | default policy `proposals.ttl_days` (7) |
| `decided_by_user_id`, `decided_at` | uuid null, timestamptz null | |
| `decision_record_id` | uuid null | CFK `project_decision_records` (`proposal_accepted/rejected/reverted`) |
| `applied_decision_record_id` | uuid null | the `decision_point_resolved` row that applied it (L2/L3) |
| `idempotency_key`, `idempotency_digest` | varchar(128) null, char(64) null | `UNIQUE(workspace_id, idempotency_key)` partial; digest + creator must match on replay |

State machine (service-enforced; a **transition-table trigger** allows UPDATE
only of `status`, `supersede_reason`, `decided_*`, `decision_record_id`,
`applied_decision_record_id`, `expires_at`, and only along the arrows):

```
validator rejects ──► rejected_by_validator (terminal)
L0 ──► shadow (terminal; evaluation only)
L1 ──► proposed ──accept──► accepted ──(command executed)──► applied
                 ──reject──► rejected        ──revert──► reverted
                 ──ttl──► expired            ──(plan/results exist, newer proposal)──► superseded
L2/L3 ──► applied ──revert──► reverted
```

`accept` and `revert` go through `/v1/proposals/{id}/accept|revert` (P6.6-A):
the service runs `UPDATE agent_proposals SET status = 'accepted' … WHERE id = :id
AND status = 'proposed' AND (expires_at IS NULL OR expires_at > now())` **in
the same transaction** as the command service call (0 rows → 409
`proposal_not_open`), re-runs the tool's argument validator against the
current graph, requires the authority the command itself needs
(`can_execute_workspace_ml`; `If-Match` on the ref ETag for ref moves; the
original command authority again for revert), refuses service tokens, uses
the proposal id as Idempotency-Key salt, and writes the decision record. The
proposal row is never the authority. Indexes: `(workspace_id, project_id,
status, created_at DESC)`, `(decision_point_key, status)`, `(run_id)`.

#### 2.4 `semantic_decision_answers` (append-only except labels)

| Column | Type | Notes |
| --- | --- | --- |
| `project_id`, `experiment_id`, `dataset_id` | uuid null | CFKs (three-column with project where set; CHECK of §2 preamble) |
| `llm_invocation_id` | uuid | CFK `llm_invocations` (the Jev call or cache hit) |
| `agent_run_id` | uuid null | CFK when asked by an agent run |
| `decision_point_key` | varchar(64) | |
| `purpose`, `release_version` | varchar(64), varchar(32) | `releases.py` |
| `model_id` | varchar(64) | dated provider model id (`jev-1.13.0`) |
| `question_key` | varchar(200) | stable per subject (column name as bounded untrusted string, or `message`) |
| `question_digest` | char(64) | sha256 of (workspace, purpose, release, model id, data class, state, question) = the cache key |
| `data_class`, `evidence_partition` | varchar(16), varchar(8) | class used; `metadata` / `train` |
| `primitive` | varchar(8) | CHECK IN (`noul`, `choice`, `score`) |
| `answer` | jsonb | typed value ≤ 2 KB |
| `probabilities` | jsonb null | choice distribution ≤ 4 KB |
| `confidence` | numeric(5,4) null | |
| `in_acting_band` | boolean | |
| `rule_answer` | jsonb null | |
| `agreement` | varchar(16) | CHECK IN (`agree`, `disagree`, `abstain`, `unavailable`) |
| `level` | smallint | effective level at the time |
| `policy_outcome` | varchar(16) | CHECK IN (`rule`, `ai`, `review`, `human`) |
| `value_used` | jsonb null | |
| `cache_hit` | boolean | the first non-cached row per `(workspace_id, question_digest)` is the cache entry and is retained for replay |
| `latency_ms` | integer null | |
| `ground_truth` | jsonb null | label; `labeled_by_user_id uuid null`, `labeled_at timestamptz null`, `label_source varchar(16)` CHECK IN (`blind`, `benchmark`, `user_decision_exposed`); label versions are new rows (`labels_version smallint`), never overwrites |

Trigger: UPDATE refused (labels are appended as versioned rows referencing
`supersedes_label_id`); DELETE only under the retention GUC. Indexes
`(purpose, release_version, model_id, created_at DESC)`,
`(workspace_id, question_digest)`, `(workspace_id, decision_point_key)`.
Writing labels needs `can_execute_workspace_ml`, is rate-limited per user,
and the labeler may not be the proposal's author (checked against
`agent_runs.created_by_user_id`).

#### 2.5 `ai_policies` (append-only version chain)

| Column | Type | Notes |
| --- | --- | --- |
| `workspace_id` | uuid **null** | NULL = platform default row (seeded from `platform_default.py`, `change_kind = 'seed'`) |
| `version` | integer | `UNIQUE(workspace_id, version) NULLS NOT DISTINCT` |
| `base_version` | integer null | the head the proposer saw; accept requires it to still be the head, else 409 `policy_conflict` |
| `state` | varchar(16) | CHECK IN (`proposed`, `accepted`, `rejected`) — `superseded` derived like ADR 0006 §5 |
| `policy` | jsonb | `AiPolicyV1` validated ≤ 32 KB |
| `policy_digest` | char(64) | |
| `schema_version` | integer | |
| `change_kind` | varchar(16) | CHECK IN (`policy`, `budget`, `seed`) — switches are §2.11 |
| `rationale` | varchar(4000) | |
| `evidence` | jsonb | array of `{kind: r3_run|incident|url|decision, id}` ≤ 64 |
| `proposed_by_user_id`, `decided_by_user_id` | uuid null | approver on `accepted`/`rejected`; `ck_ai_policies_actor`: `proposed` ⇒ proposer set; `accepted` with `change_kind <> 'seed'` ⇒ decider set; `self_approved boolean` is computed under the row lock (decider = proposer and the workspace had exactly one approver at that moment) and shown in the console |
| `supersedes_id` | uuid null | FK; unique partial index (linear chain); chain trigger on `workspace_id IS NOT DISTINCT FROM` |

Effective policy for a workspace = `platform(accepted, max version) ⊕
workspace(accepted, max version)` with caps applied (§3). Trigger
`ai_policies_append_only` (`prevent_canonical_row_mutation`; policies are not
retained away).

#### 2.6 `decision_point_policies` (append-only version chain)

| Column | Type | Notes |
| --- | --- | --- |
| `workspace_id` | uuid **null** | NULL = platform level written by P6.8-A |
| `decision_point_key` | varchar(64) | must exist in the registry (service check) |
| `level` | smallint | CHECK 0..3 |
| `cap_level` | smallint | copied from code at write; CHECK `level <= cap_level` |
| `prompt_release_id`, `model_id` | uuid null, varchar(64) null | the pair the evidence covers; a different pair in production is L0 (ADR 0008 §3) |
| `state` | varchar(16) | `proposed` / `accepted` / `rejected` |
| `actor_kind` | varchar(8) | CHECK IN (`human`, `rule`); `rule` = `governance.auto_demote.v1` / `r3.release.v1` |
| `actor_user_id`, `actor_rule`, `decided_by_user_id` | | `ck_dpp_actor` as ADR 0006; **`ck_dpp_rule_only_down`**: `actor_kind = 'rule' AND state = 'accepted'` ⇒ `level <` the superseded row's level (enforced by the chain trigger); a promotion row has `decided_by_user_id` set — a workspace approver for workspace rows, a platform admin for platform rows |
| `rationale`, `evidence` | varchar(4000), jsonb | R3 run ids, incident ids, acceptance stats |
| `supersedes_id` | uuid null | linear chain per (workspace, key); chain trigger as above |

Effective level = `min(workspace accepted head, platform accepted head, code
cap, ADR 0008 §1b ceiling)`; absent rows mean L0.

#### 2.7 `workspace_llm_budgets` (mutable counters)

| Column | Type | Notes |
| --- | --- | --- |
| `scope` | varchar(16) | CHECK IN (`workspace`, `project`, `run_kind`) |
| `project_id` | uuid null | CFK; required for `project` |
| `run_kind` | varchar(16) null | `assistant_turn`, `assistant_thread`, `specialist`, `jev` for per-run caps |
| `period` | varchar(8) | CHECK IN (`month`, `day`, `run`) |
| `period_start` | date | rolled forward by the service on first touch of a new period |
| `limit_micros`, `currency` | bigint, char(3) | |
| `alert_fraction` | numeric(3,2) | default 0.80 |
| `hard_stop` | boolean | default true (false = alert only) |
| `reserved_micros`, `spent_micros`, `calls` | bigint, bigint, integer | counters for the current period; CHECK `reserved_micros >= 0 AND spent_micros >= 0` |
| `updated_at` | timestamptz | |

`UNIQUE(workspace_id, scope, project_id, run_kind, period) NULLS NOT
DISTINCT`; missing rows are created with `INSERT … ON CONFLICT DO NOTHING`
before locking. History is in `llm_invocations` (sum of `cost_micros` by
period must equal `spent_micros`; a nightly check reconciles and opens an
incident on drift). P8.4 `usage_records` are **derived from this ledger**
(ROADMAP P8.4 "from existing ledgers"); no second counter.

#### 2.8 `prompt_releases` (platform, no workspace)

`agent_key varchar(64)`, `version integer`, `prompt_digest char(64)` (sha256 of
the prompt file), `output_schema_digest char(64)` (JSON schema of the typed
output), `model_hint varchar(128) null`, `status` CHECK IN (`draft`,
`released`, `retired`), `released_at`, `notes varchar(2000)`;
`UNIQUE(agent_key, version)`. Rows are created by `dclab agents sync-prompts`
(also at API startup in dev) from `app/agents/prompts/`; CI fails if a released
row's digest differs from the file. A prompt text change is a new version; the
old row becomes `retired` (never deleted — events reference it). Jev purpose
releases (`releases.py`) use the same table with `agent_key = 'jev:<purpose>'`.

#### 2.9 `ai_incidents`

`workspace_id uuid null` (platform incidents), `kind` CHECK IN
(`validator_rejections`, `eval_failure`, `budget_exhausted`,
`provider_failure`, `data_exposure`, `revert_rate`, `drift`, `replay_mismatch`,
`reconciliation`, `manual`), `subject_kind` CHECK IN (`decision_point`,
`agent`, `purpose`, `provider`, `workspace`), `subject_key varchar(64)`,
`evidence jsonb` ≤ 16 KB, `action` CHECK IN (`none`, `auto_demote`,
`switch_off`, `breaker_open`), `action_level_policy_id uuid null` (FK
`decision_point_policies`), `action_switch_id uuid null` (FK `ai_switches`),
`status` CHECK IN (`open`, `resolved`, `closed`), `opened_at`,
`resolved_by_user_id`, `resolved_at`, `resolution varchar(2000)`. Trigger:
only `status`, `resolved_*`, `resolution` mutable; a second trigger requires
`workspace_id IS NOT DISTINCT FROM` the referenced row's `workspace_id` for
`action_level_policy_id` and `action_switch_id` (platform rows may only
reference platform rows). An incident whose action was `switch_off` keeps the
switch off until `resolved` by an approver (§2.11).

**Why governance changes are not `project_decision_records` rows.** That
ledger is project-scoped by construction (`project_id NOT NULL`, seven
three-column subject FKs, `ck_pdr_subject_matches_kind`); widening it to
workspace scope would touch every CFK and reader. The versioned policy, level
and switch rows carry the same facts a decision record carries (actor,
rationale, evidence, state, supersession) and are append-only, so they *are*
the workspace-level ledger. Project-scoped AI decisions (`decision_point_resolved`,
`proposal_*`) stay in `project_decision_records` (ADR 0008 §7).

#### 2.10 Additive changes to existing tables

- `llm_invocations`: `agent_run_id` gets CFK `(workspace_id, agent_run_id) →
  agent_runs` ON DELETE SET NULL; add `prompt_release_id uuid null FK
  prompt_releases`, `cache_hit boolean NOT NULL DEFAULT false`, `cost_micros
  bigint null`, `currency char(3) null`, `data_class varchar(16) null`,
  `outcome_scope varchar(8) null`, `agent_role varchar(16) null`,
  `decision_point_key varchar(64) null`, `refusal_code varchar(64) null`,
  `budget_reservation_id uuid null`, `budget_settled boolean NOT NULL DEFAULT
  false`, `provider_request_id varchar(128) null`; replace
  `ck_llm_invocations_purpose` by the key regex CHECK (the six legacy values
  still match); `provider_kind` CHECK unchanged. `estimated_cost` (float) stays
  for old rows; new rows fill both until a later contract step. Freeze by
  `prevent_canonical_column_mutation`: `workspace_id`, `project_id`,
  `agent_run_id`, `purpose`, `prompt_release_id`, `input_evidence_digest`,
  `data_class`, `outcome_scope`, `started_at` (the existing
  `finalize_llm_invocation` still updates status, verdict, outputs, timings);
  DELETE and the nulling of `safe_output` only under the retention conditions
  of §2.2 (horizon or deletion GUC). The existing `ON DELETE CASCADE` from
  `workflow_runs` and `experiments` into `llm_invocations` therefore succeeds
  only when the deleting path has `SET LOCAL dclab.deleting_workspace`; the
  experiment- and workspace-deletion services set it, and a migration test
  deletes an experiment with and without the GUC.
- `project_decision_records.actor_agent_run_id`: CFK `(workspace_id,
  actor_agent_run_id) → agent_runs(workspace_id, id)` NO ACTION; new
  `decision_type` values `decision_point_resolved`, `proposal_reverted`
  (ADR 0008 §7).
- `db/integrity.py`: new `prevent_mutation_except_retention()` helper (§2.2),
  mirrored in the migration literal and `create_all`, covered by the gate test.
- Truth artifacts regenerate (new tables, new operations).

#### 2.11 `ai_switches` (append-only chain per switch key)

| Column | Type | Notes |
| --- | --- | --- |
| `workspace_id` | uuid **null** | NULL = platform switch (`global_ai`, `provider:<p>`) |
| `switch_key` | varchar(64) | `all_ai`, `agent:<key>`, `purpose:<key>`, `provider:<p>`, `global_ai` |
| `state` | varchar(4) | CHECK IN (`on`, `off`) |
| `changed_by_user_id` | uuid null | NULL only for `actor_rule` rows |
| `actor_rule` | varchar(128) null | `incidents.auto_switch_off.v1`, `limits.breaker.v1` |
| `reason` | varchar(2000) | |
| `incident_id` | uuid null | FK `ai_incidents` (same `workspace_id IS NOT DISTINCT FROM` trigger); an `off` row with an incident stays effective until the incident is resolved, even if a later `on` row exists |
| `supersedes_id` | uuid null | chain per (workspace, key); chain trigger on `workspace_id IS NOT DISTINCT FROM` |

Effective state = newest row per (workspace, key), with the incident rule
above. **Off always succeeds**: any `workspace_owner` / `workspace_admin` (or
`dclab_admin` for platform keys) writes an `off` row immediately, no approval,
no `base_version`. **On** needs `can_approve_ai_policy` for workspace keys;
platform keys (`global_ai`, `provider:<p>`) are turned back on only by a
`dclab_admin` through `dclab governance switch on <key> --reason …` (an
audited row with `changed_by_user_id`; no HTTP route). A policy accept never
touches switches (they are outside the policy document), so a racing policy
change cannot re-enable anything.

### 3. Governance policy (`AiPolicyV1`)

```json
{
  "schema_version": 1,
  "models": {"roles": {"lead": {"default": "gpt-6.1-sol", "allowed": ["gpt-6.1-sol", "gpt-6-luna"], "fallback": "rule_path"},
                       "specialist": {"default": "gpt-6.1-sol", "allowed": ["gpt-6.1-sol", "gpt-6-luna"], "fallback": "rule_answer",
                                      "per_agent": {"dataset_investigator": "gpt-6-luna"}},
                       "legacy_decision": {"default": "gpt-6-luna", "allowed": ["gpt-6-luna"], "fallback": "rule_answer"},
                       "verifier": {"default": "gpt-6-luna", "allowed": ["gpt-6-luna", "gpt-6.1-sol"], "fallback": "rule_answer"},
                       "jev": {"default": "jev-1.13.0", "allowed": ["jev-1.13.0"], "fallback": "rule_answer"}}},
  "data": {"max_class": "aggregates", "sample_values_per_column": 0, "user_text_to_jev": false},
  "autonomy": {"ops": {"auto_retrain_per_week": 2, "auto_release": false,
                       "rollback_rule": {"enabled": false, "metric": "precision", "drop": 0.05, "windows": 2}}},
  "budgets": {"workspace_month_micros": 25000000, "project_month_micros": 12000000,
              "assistant_turn_micros": 250000, "assistant_thread_micros": 1000000, "specialist_run_micros": 100000,
              "alert_fraction": 0.8, "hard_stop": true},
  "limits": {"assistant_turn": {"steps": 8, "tokens": 60000, "wall_s": 120, "tool_calls": 20},
             "assistant_thread": {"steps": 40, "tokens": 300000, "wall_s": 900},
             "assistant_user": {"turns_per_hour": 60, "concurrent_turns": 2},
             "specialist": {"calls": 2, "tokens": 24000, "wall_s": 60}, "jev": {"timeout_ms": 1000, "batch": 50}},
  "proposals": {"ttl_days": 7},
  "retention": {"prompts_days": 365},
  "incidents": {"validator_rejections_24h": 5, "revert_rate_30d": 0.10}
}
```

Code constants, **not** policy (`platform_default.py`): untrusted-text marking
is always on; raw prompts are never stored; approvers are the role-derived
authority below; L1 acceptors need `can_execute_workspace_ml`; the
data-exposure auto-demotion and switch-off cannot be disabled; the fake
provider is refused when `ENVIRONMENT = production`; `AI_ENABLED` (platform
environment flag) defaults **false** and production requires it to be set
explicitly in addition to the platform `global_ai` switch row being `on`.

**Caps** (`platform_default.CAPS`, applied by `effective_policy`, violations are
422 at write; **caps only narrow**): `data.max_class ≤ aggregates` unless the
platform operator allows `sample_values` for the workspace (founder Q3);
`raw_rows` is not a valid value anywhere; `models.*.allowed ⊆` platform
allowlist of dated model ids; `limits.* ≤` platform limits; `budgets.* ≤` plan
entitlement (`workspace_entitlement_service`); `autonomy.ops.auto_release` is
`false` in the MVP; `incidents.*` may only be made **stricter** than the
platform values. **Fail closed:** if the effective policy cannot be loaded or
does not validate, every gateway call returns `Refusal(policy_unavailable)`
and callers take the rule path; the gateway never raises to a caller.

**Who may change what.**

| Change | Authority | How |
| --- | --- | --- |
| policy, budgets, levels (up) | `can_approve_ai_policy(db, user, workspace)`: an unsuspended `WorkspaceMembership` in {`workspace_owner`, `workspace_admin`} of that workspace; no client-user fallback; platform staff **not** included (they may seed platform rows from code and switch off) | `proposed` row with `base_version` → `accepted` row by an approver (409 if the head moved); self-approval only when the workspace has one approver, counted under the lock and flagged |
| levels (down), switches off | `workspace_owner` / `workspace_admin` immediately; rules (`governance.auto_demote.v1`, incidents) | one accepted row, no second approver |
| switches on | `can_approve_ai_policy` | one row; refused while a `switch_off` incident is open |
| platform default, caps, platform allowlist | code review (ADR note + PR) | `seed` rows |

### 4. Gateway contract (P6.2-B)

```python
class CompletionRequest(BaseModel):
    agent_role: Literal["lead", "specialist", "legacy_decision", "verifier"]
    agent_key: str; purpose: str; decision_point_key: str | None
    workspace_id: UUID; project_id: UUID | None; agent_run_id: UUID | None
    experiment_id: UUID | None; workflow_run_id: UUID | None      # attribution (ck_llm_invocations_attributed)
    prompt_release_id: UUID                                        # system prompt = the release text
    envelope: ContextEnvelope                                      # fields tagged data_class / outcome_scope / source
    transcript: list[TranscriptItem]                               # prior turns and tool results, same tagging
    user_text: list[Untrusted]                                     # already wrapped
    output_schema: type[BaseModel]; max_output_tokens: int; temperature: float = 0.0
    budget: BudgetReservation                                      # from budget.reserve(...)
    timeout_s: float; cache: bool = True; idempotency_key: str | None

class CompletionResponse(BaseModel):
    ok: bool; output: BaseModel | None; refusal: Refusal | None
    provider: str; model: str; usage: Usage; cost_micros: int; latency_ms: int
    cache_hit: bool; invocation_id: UUID; prompt_release_id: UUID; data_class: DataClass; outcome_scope: OutcomeScope

class SemanticDecisionRequest(BaseModel):
    purpose: str; release_version: str; decision_point_key: str
    workspace_id: UUID; project_id: UUID | None; experiment_id: UUID | None; dataset_id: UUID | None
    agent_run_id: UUID | None; state: dict[str, Any]; questions: list[SemanticQuestion]  # ≤ 50
    source_datasets: list[UUID]; source_columns: list[UUID]; budget: BudgetReservation; timeout_ms: int = 1000

class SemanticDecisionResponse(BaseModel):
    ok: bool; answers: list[SemanticAnswer]; refusal: Refusal | None
    cache_hit: bool; invocation_id: UUID; latency_ms: int

class Refusal(BaseModel):
    code: Literal["kill_switch", "policy_unavailable", "model_not_allowed", "data_class_exceeded",
                  "outcome_scope_exceeded", "budget_exhausted", "rate_limited", "breaker_open",
                  "provider_error", "timeout", "invalid_output", "policy_denied"]
    scope: str | None; message: str; retry_after_s: int | None
```

Pipeline of `complete()` / `decide()` (same order, in `gateway/service.py`;
any exception inside becomes `Refusal(provider_error)` plus a ledger row —
callers never see an exception):

1. **policy** — `effective_policy(workspace)`; failure → `policy_unavailable`.
2. **switches** — precedence platform `global_ai` (row + `AI_ENABLED`) →
   `workspace all_ai` → `agent:<key>` / `provider:<p>` → `purpose:<p>`; the
   first `off` wins → `Refusal(kill_switch, scope)`.
3. **router** — model from `models.roles[agent_role]` (request may name a
   model only from `allowed`; dated ids only); provider from the model;
   `model_not_allowed` otherwise.
4. **redaction** — compute the effective data class and outcome scope (§8,
   ADR 0008 §2b) from the envelope's and transcript's declared sources; drop
   fields above either; verify every free-text field is `Untrusted`; fields
   with no recorded source get the most restrictive class; add the untrusted
   notice to the system prompt; record `redaction_summary`.
5. **limits** — per-workspace and per-provider token buckets (defaults 60 calls
   / min / workspace, provider-specific RPS); circuit breaker keyed by
   (provider, purpose) **and** by (provider, workspace): counts only provider
   5xx and timeouts (never 4xx, validation or policy refusals), opens after 5
   in 60 s, half-open after 15 min.
6. **budget** — the reservation must be `held`; the call's **worst-case cost**
   (max output tokens at the model's price) must fit the remaining hold, else
   `budget_exhausted`.
7. **cache** — key = `sha256(workspace_id ‖ prompt_release_id ‖ model_id ‖
   data_class ‖ outcome_scope ‖ canonical_json(redacted envelope + transcript +
   user_text) ‖ output_schema_digest)` for LLMs; `sha256(workspace_id ‖ purpose
   ‖ release_version ‖ model_id ‖ data_class ‖ canonical_json(state) ‖
   question_key)` for Jev. Lookups always filter by `workspace_id`. Hit →
   ledger row with `cache_hit = true`, zero cost.
8. **provider** — `providers/<p>.py` only; structured output enforced
   (`response_format` / tool schema); one retry on `invalid_output` for
   specialists, none for Jev.
9. **validate** — parse into `output_schema`; failure → `invalid_output`.
10. **ledger** — exactly one `llm_invocations` row per call (incl. refusals and
    cache hits), `provider_kind` = `llm_provider` / `semantic_decision` /
    `deterministic_fallback` for refusals, `input_evidence_digest` = cache key,
    `safe_output` = bounded redacted output, `budget_reservation_id`.
11. **settle** — `budget.settle(invocation_id, actual_cost)`; the row's
    `cost_micros` is the truth.

**Budget semantics.** `reserve(workspace, project, run_kind, estimate) →
BudgetReservation(id, held_micros)` runs at run start (harness) or per
decision-point batch (Jev; one pre-reservation per run is allowed): it
`INSERT … ON CONFLICT DO NOTHING`s the counter rows, locks them in the fixed
order workspace → project → run_kind (`SELECT … FOR UPDATE`), rolls the period
if needed, refuses with `budget_exhausted` when `spent + reserved + estimate >
limit` and `hard_stop`, else adds to `reserved` and stores `held_micros` on
the run. Each call consumes from the hold (step 6) and `settle(invocation_id,
actual)` **adds `actual` to `spent` and subtracts `min(actual, remaining
hold)` from `reserved`** (never more than what is still held), idempotent by
`invocation_id` (`budget_settled` on the ledger row); a late settle after the
janitor released the hold therefore still charges `spent` without driving
`reserved` below zero. At run end the remainder of the hold is released; the
janitor releases holds of runs past their wall limit and logs it. Crossing
`alert_fraction` writes an Inbox notice once per period.

**Callers.** The harness (for agents), the decision-point hook (for Jev and
legacy purposes, through the semantic port) and nothing else. The four legacy
writers become gateway callers in P6.2-B with their purposes unchanged until
P6.9-A remaps them.

### 5. Harness (P6.10-A)

#### 5.1 Lifecycle of `AgentService.run(spec: AgentRunSpec) -> AgentRunResult`

```
1 authorize      actor (user or token) may read every subject node; ML-write for any run that may propose
2 create run     agent_runs row (status queued→running); limits from policy ⊓ spec; policy_digest, catalog digest
3 build context  context.py: ContextEnvelope from read-only services in AGENT CONSUMER MODE
                 (tools/shaping.py strip_holdout + cv_only on every field; outcome_scope ≤ the point's; metadata
                 and aggregates only); digest → run.context_digest
4 redact         gateway.redaction (shared code) to the effective data class; untrusted wrapping
5 reserve        budget.reserve(run_kind estimate) → held_micros on the run
6 run runtime    runtime.run(spec, envelope, tools, hooks) — fake | nooa_predict | lead_loop
   per LLM call  pre-call hooks → gateway.complete → post-call (event llm_call_*)
   per tool call registry lookup → capability check → pre-tool hooks → validator → service (agent consumer mode)
                 → shaping → post-tool hooks → event tool_call_*   (write tools: create proposal, never act)
7 validate       validation.py: Pydantic + deterministic validator → proposals status proposed|applied|
                 rejected_by_validator|shadow (per ADR 0008 level and §1b ceiling)
8 persist        proposals, decision records (decision_point_resolved), events
9 settle         budget.settle per call already done; release the hold; run.usage, cost_micros, status
10 eval sample   semantic answers / proposal rows flagged for R3 (always on)
```

Steps 1–5 and 7–10 run in the API/worker process; step 6 for `nooa_predict`
and `lead_loop` runs in the **worker** as job `agents.run` (`ml_jobs`; payload
= `agent_run_id` only, the handler re-authorizes from the run row), except the
assistant turn, which runs in the API process under its 120 s turn limit so
SSE can stream (founder Q1; the API then holds provider secrets through its
own gateway instance). One agent instance per run; nothing is reused across
runs or tenants; the runtime receives tools and an envelope, never a DB
session, storage client or provider key. `nooa_runtime` asserts at start that
the agent's LLM object is the gateway adapter and every generation method's
strategy is `PredictStrategy`, failing closed otherwise.

#### 5.2 Hooks and allowed effects

| Hook | Input | Allowed effects | Built-in hooks |
| --- | --- | --- | --- |
| `pre_run` | spec, envelope digest, limits | `observe`, `deny(reason)`, `narrow_limits` | capability check, switch check, budget reserve |
| `pre_call` | request (redacted) | `observe`, `deny`, `add_system_note` (bounded) | redaction verifier (class + scope), step/token/time counter |
| `pre_tool` | tool name, arguments | `observe`, `deny`, `modify_arguments` (only by the tool's validator), `downgrade_to_proposal` | capability check, decision-point level and ceiling, validator, forbidden-op guard |
| `post_tool` | result | `observe`, `modify_result` (shaping/bounding/holdout stripping only), `attach_citation` | shaper, citation builder, recorder |
| `post_run` | output, usage | `observe`, `deny_output(reason)` → `rejected_by_validator`, `attach_citation` | output validator, eval sampler, recorder, settle |

No hook may call a provider, write product state or widen limits. Hooks are
registered in code (`hooks.py`), ordered, and listed in the run's first event.

#### 5.3 Immutable per run

`agent_runs` header columns frozen by trigger (§2.1); the envelope digest; the
prompt release id; the limits; the policy digest; the tool catalog digest;
every event.

#### 5.4 Event types (`agent_events.type`)

`run_started`, `context_built`, `budget_reserved`, `user_message`,
`llm_call_started`, `llm_call_finished` (invocation id, cache hit, usage),
`step_validated`, `step_rejected`, `tool_call_requested`, `tool_call_denied`,
`tool_call_finished` (result digest, bounded preview), `proposal_created`,
`proposal_auto_applied`, `assistant_message`, `clarification_requested`,
`budget_exhausted`, `budget_settled`, `run_finished`, `run_failed`,
`thread_closed`, `replay_checked`. NOOA events map: `llm.request/response →
llm_call_*`, `strategy.step → step_validated`, `tool.call → tool_call_*`,
`error → run_failed`; NOOA's own tracing exporters are set to the recorder and
the `:5001` viewer is disabled.

#### 5.5 Replay

`replay.run(run_id) -> ReplayResult` re-executes the recorded run with the
**fake provider fed from the record** (each `llm_call_finished` payload holds
the output digest; the full redacted output is the ledger's `safe_output`).
Tool results are stored as digest + bounded preview, so **replay never
re-runs live tools**: read tools return a stub that carries the recorded
digest, write tools are stubbed (no proposals), and the runtime is checked
against the recorded sequence. **Equality** = identical ordered sequence of
`(tool name, argument digest)`, identical final output digest, identical
proposal payload digests. Timestamps, latency, cost and provider ids are
ignored. Replay writes nothing except a `replay_checked` event (and the
nightly job's report). Viewing a replay needs read authority on every subject
node of the run plus development-role access (`can_read_platform` or workspace
developer). A mismatch opens an `ai_incidents` row (`kind = replay_mismatch`).
Replay covers agent runs only; auto-train decision points are reproduced by
the Jev cache and inherited overrides (ADR 0008 §2c).

### 6. Tool catalog (P6.10-A)

`app/agents/tools/catalog.py` holds one `ToolDefinition` per tool:

| Field | Meaning |
| --- | --- |
| `name` | MCP name today (`inspect_project`, …) |
| `effect` | `read` (executes) or `proposal` (creates `agent_proposals`); there is no `job` effect in the MVP |
| `capability` | token scope / human authority required (`read`, `experiments:write`, `decisions:propose`, `decisions:accept` = humans only) |
| `decision_point_key` | ADR 0008 `lead.*` key for write tools |
| `input_schema` | Pydantic model (exported as JSON schema) |
| `validator` | deterministic argument validator (node exists in project, allowlists, same split plan, published dataset, …); re-run at proposal accept |
| `service` | the service function the `/v1` route calls (in-process path, invoked in agent consumer mode) and the `/v1` operation (SDK/MCP path) |
| `shaper` | output shaping in `app/agents/tools/shaping.py`: `strip_holdout` / `cv_only` / `cv_record` / `withhold_holdout_code` / model-card withholding (moved from `api/v1_agent_views.py` and `dclab_mcp/server.py`, which import or vendor them — a copy test keeps `dclab_mcp` equal), `bound`, `untrusted`; the shaper **declares** the result's `data_class`, `outcome_scope` (`none` or `cv`) and `source_datasets` / `source_columns` so gateway redaction can apply to tool results in the transcript |
| `surfaces` | `mcp`, `assistant`, `studio_forms` |

Initial catalog = the 14 MCP tools (+ `get_impact` over
`/v1/nodes/{kind}/{id}/impact`, `inspect_governance` in P6.11-A,
`list_proposals` / `request_agent_review` in P6.6-A). `accept_proposal` keeps
its MCP meaning (hand-off to a human, never accepts). Export:
`contracts/agent_tools.json` (generated by `scripts.generate_truth_artifacts`
alongside the other contracts; a contract test asserts `dclab_mcp`'s
registered tools equal the export in names, schemas and read/write flags).
**Forbidden operations** (asserted absent by name and by effect against the
registry and the service map): `read_rows`, `read_holdout`, `select_winner`,
`compute_metric`, `build_split`, `infer_entity_column`, `load_model`,
`execute_code`, `raw_sql`, `move_ref` without a human actor,
`promote_champion`. Property tests (P6.10-B) — **no holdout, ever**: for every
principal and every agent consumer, no tool output, envelope field or
transcript item contains a `holdout|final_test` key or scope (there is no
champion exception); nothing above the effective data class reaches
`complete()`.

### 7. The in-app assistant (P6.3-B; screens A3-UI/A4-A)

#### 7.1 `AssistantStep` (the only thing the lead model may return)

```python
class Citation(BaseModel):
    kind: Literal["experiment", "dataset_version", "problem_spec", "split_plan", "model_version",
                  "candidate", "finding", "decision", "proposal", "prediction"]
    id: UUID
class ToolCall(BaseModel):
    tool: str                      # catalog name; unknown → step_rejected
    arguments: dict[str, Any]      # validated by the tool's input_schema
    reason: str = Field(max_length=200)
class AssistantStep(BaseModel):
    kind: Literal["tool_calls", "answer", "clarify", "done"]
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=6)
    message: str | None = Field(default=None, max_length=4000)   # Markdown subset, see below
    citations: list[Citation] = Field(default_factory=list, max_length=32)
```

Validator (deterministic, `validation.py`): every `tool_calls[].tool` is in the
catalog and allowed for this user; every citation resolves to a node in the
thread's project (else `step_rejected` and one retry with the reasons
appended); an `answer` that states a metric value must cite the experiment or
model version whose **CV** evidence contains it (number-in-text check against
cited nodes' CV metrics; unmatched numbers, and any holdout number, reject
the step). The Markdown subset allows headings, lists, emphasis, code spans
and links **only** of the form `dclab://<kind>/<id>` to cited nodes (rendered
by the UI as internal links); raw HTML, images and external URLs are
rejected. Write tool calls become `ToolCallProposal` rows; the step continues
with the proposal id as the tool result ("pending your confirmation").

#### 7.2 Threads API (human session cookie only; service tokens 403)

| Operation | Behaviour |
| --- | --- |
| `POST /v1/assistant/threads` `{project_id, title?}` | needs `can_execute_workspace_ml` on the project's workspace; creates `agent_runs(kind=assistant, status=waiting_user, created_by_user_id=user)`; 201 |
| `GET /v1/assistant/threads?project_id=&cursor=` | the **creator's** threads of the project |
| `GET /v1/assistant/threads/{id}` | creator only; header + `usage`, `limits`, levels snapshot, tools available / needing approval |
| `GET /v1/assistant/threads/{id}/events?after_seq=&limit=` | creator only; events page (history; ETag). Approvers read threads through the governance audit (`GET /v1/agent-runs/{id}`, P6.11-A) |
| `POST /v1/assistant/threads/{id}/messages` `{text ≤ 4000, page_context?, dataset_ids?}` | creator only; runs one turn; response is `text/event-stream` (SSE) with one event per `agent_events` row (`id` = seq, `event` = type, `data` = bounded payload); ends with `turn_finished`; `Last-Event-ID` resumes from `…/events` |
| `POST /v1/assistant/threads/{id}/close` | status `closed`; no further turns |
| `GET /v1/assistant/quick-actions?project_id=&node_kind=&node_id=` | deterministic templates (§7.6); works with AI off |

Rules: every turn uses the **thread row's** `workspace_id` and re-checks the
user's membership and ML-write authority there (a changed `X-Workspace-Id`
header is a 404); one turn at a time per thread (409 `turn_in_progress`);
per-user limits from `limits.assistant_user` (turns per hour, concurrent
turns) → 429; `page_context` is a hint — the server resolves the node under
the thread's workspace and ignores unknown or foreign ids; `dataset_ids` must
be published datasets of the project (uploads happen through the normal
upload route first; there is no upload tool); every turn reserves
`assistant_turn` budget and checks the thread budget; a thread of workspace A
is a 404 from B. On client disconnect the turn runs to its end or limit,
settles and releases the turn lock; the client resumes from `…/events`. The
composer carries a permanent notice: "Workspace approvers can read this
conversation for audit; platform operators can replay agent runs; do not paste
values you may not share." The
BFF route `/api/backend/v1/assistant/threads/{id}/messages` goes through the
existing `bff-proxy.ts` (session cookie, CSRF header and `Origin` forwarded,
never a bearer; request size cap kept) with the upstream SSE body streamed
unbuffered (response-size cap not applied to `text/event-stream`, 150 s read
timeout) and `Last-Event-ID` forwarded.

#### 7.3 Turn algorithm (`lead_runtime.py`)

```
envelope = context.build(project, user, page_context)            # re-resolved every turn, agent consumer mode
transcript = last N=20 user/assistant/proposal/tool events (bounded, tagged)
for step in range(limits.steps):
    step_out = gateway.complete(AssistantStep, release=lead@vN, envelope, transcript, tools=catalog.visible(user))
    validate(step_out)  → on reject: retry once with reasons, then answer_with_template(reason)
    if kind in (answer, clarify, done): emit assistant_message; break
    for call in tool_calls (≤6): capability → validator → read: run (consumer mode) + shape | write: proposal
    budget/time check → budget_exhausted message + forms hint
```

#### 7.4 Confirm = proposal accept

Confirm cards show the typed `tool_arguments` first and the agent's
`proposed_rationale` below, labelled unverified. They call
`POST /v1/proposals/{id}/accept` (P6.6-A) with an Idempotency-Key; acceptance
follows §2.3 (conditional status UPDATE in the command's transaction, validator
re-run, command authority, `If-Match` for ref moves), executes through the
normal command service as the human actor and writes `proposal_accepted` with
`rationale` = the human's text or `accepted assistant proposal <id>`,
`details.proposed_by = "assistant"`, `details.proposed_rationale` (untrusted).
No assistant-only write route exists.

#### 7.5 Fixed rules 1–7 of `ASSISTANT.md`

All kept; refinements here are stricter: rule 2's write tools are all L1 in
this ADR (no auto-apply); rule 3's loop is the harness runtime with the bounds
of ADR 0008 §6; rule 4 adds `prompt_release_id` on every call and the catalog
digest on every run; rule 6 is why `lead.*` stays at L1; rule 7's citation
validator also rejects uncited numbers, holdout numbers and external links.

#### 7.6 LLM off

When the platform or workspace switch is off, the `agents` extra is missing or
the gateway refuses: `POST …/messages` still works and returns, as a single
`assistant_message` event with `llm_used: false`, the best deterministic
template for the message's Jev intent (if Jev is on and `user_text_to_jev`
allows it) or a menu of quick actions. Templates (`app/agents/templates/*.py`,
pure functions over API read models in agent consumer mode): *Explain this
experiment* (winner, CV metric in words, baseline margin, findings), *What is
stale* (graph stale reasons), *Compare with champion* (compare read model),
*Read the findings*, *Model card summary* (without the final evaluation), *Why
was this column excluded* (leakage finding / decision record), and *Champion
final evaluation* — the only place the Lab shows a holdout number: a template
rendered by the API for the signed-in human from the champion's single
`final_holdout` evaluation, **outside any model context** (it is never part of
an envelope, transcript or tool result; the lead agent can only link to it).
Every figure in a template comes from the same services the tools call.

### 8. Data exposure

Data classes, ordered: `metadata` (names as untrusted text, dtypes, counts,
flags, stage names, ids) < `aggregates` (bands, distributions, CV metrics,
findings numbers, model-card numbers) < `sample_values` (≤ N distinct values
per column, N from policy; never for columns that the rule **or any AI
answer** judged identifier / free text, never for `sensitivity = restricted`
columns) < `raw_rows` (no code path; not a valid policy value). Outcome scope
(ADR 0008 §2b) is a second, independent axis.

Every `ContextEnvelope`, transcript and tool-result field is tagged with its
class, scope and source (`dataset_id`, `column_id`) in `contracts.py`;
redaction drops by tag, never by regex; a field without a recorded source
gets the most restrictive class. **Effective class for a call** =
min(workspace `data.max_class`, the decision point's / purpose's allowed
class, the `llm_exposure_policy` of every source dataset **and column**):

| ADR 0005 label (`DATA_USE_POLICIES`) | Contribution to an AI call |
| --- | --- |
| `deny` (today's default for every upload and column) | **nothing**: the dataset/column is dropped from the envelope even by name; a decision point whose evidence needs it runs rule-only with `agreement: unavailable`; the lead agent sees only ids and counts of such datasets |
| `metadata_only` | names, dtypes, counts, flags |
| `aggregate_only` | plus bands, distributions, CV metrics, findings |
| `allow` | the workspace class (sample values only if the workspace allows them) |

Owners raise a dataset or column by an append-only policy revision
(`dataset_policy_revisions`); founder Q3 decides the default label for new
uploads. With the gateway enforcing this, `config.validate_runtime_settings`
drops the ADR 0005 item 7 production refusal in P6.2-B. Untrusted text (user
messages, column names, descriptions, rationales, generated code) always
travels wrapped and is never placed in the system prompt; column names in Jev
`state` are also wrapped. User free text goes to the lead agent's provider by
construction (it is the message); it goes to Jev only when
`data.user_text_to_jev` is on. "Raw rows never leave" is a statement about
dataset content; it does not cover values a user pastes into chat, and the
Lab says so under the composer. Retention: `llm_invocations.safe_output` and
`agent_events` payloads keep redacted, bounded content for
`retention.prompts_days` (default 365, Q4); raw prompts are never stored; the
retention job (P8.7) deletes events and nulls `safe_output` under the
retention GUC (§2.2).

### 9. Pins and external facts (accessed 2026-10-04)

| Component | Pin | Source | Notes |
| --- | --- | --- | --- |
| NOOA | `nooa==0.0.10` (PyPI 2026-09-04; requires Python ≥ 3.12, < 3.14; depends on `litellm>=1.97.0`) | https://pypi.org/project/nooa/ ; repo https://github.com/NVIDIA-NeMo/labs-OO-Agents (main `564a3401`, 2026-10-02, Apache-2.0, "research software") | `docs/concepts/strategies.md`: `CodeActStrategy` default, `PredictStrategy` = single structured call, no code execution; `src/nooa/config/strategy_config.py`: `CodeActConfig.max_iterations: int | None = None`, `execution_backend = "inprocess"` default; `docs/concepts/safety.md`: "The containment boundary is outside the Python process" — DCLab bans CodeAct in API/worker (import-linter + runtime assertion + CI test, P6.3-A) |
| LiteLLM | `litellm==1.104.0` (GitHub release 2026-10-03; `v1.105.0-rc.1` is pre-release) | https://github.com/BerriAI/litellm/releases ; https://pypi.org/project/litellm/ | review the advisory list before P6.3-A; the gateway may use LiteLLM only inside `providers/litellm.py` |
| TypeSafe Jev | model `jev-1.13.0`; `typesafe-sdk==0.7.2` (PyPI 2026-09-26; 0.5.7 → 0.7.2 in 15 days) | https://docs.typesafe.ai/models ; https://pypi.org/project/typesafe-sdk/ | aliases `jev-latest`, `jev-preview` → 1.13.0 (never used); $0.042 / 1M input tokens, output free; 100K tokens/s and 80 req/s "can change without notice"; 64k context, 32k for `state`; "not fine-tuned or LoRA-adapted with customer data", "not trained on customer requests"; ZDR enterprise only; timeout and region **not stated** on the page fetched → metadata by default |
| OpenAI models | `gpt-6.1-sol` (complex roles; $2 / 1M input, $10 / 1M output) and `gpt-6-luna` (simple roles; $0.10 / $0.50); founder decision Q1 | https://developers.openai.com/api/docs/models ; https://developers.openai.com/api/docs/models/gpt-6-luna ; https://openai.com/index/introducing-gpt-6-sol-and-luna/ | both 1.05M context, 128K max output, function calling and structured outputs; the models page lists **no dated snapshots**, so the bare ids are pinned and the ledger records the provider-reported resolved model per call (a change = model change, ADR 0008 § Founder decisions); structured output goes through the **Responses API** (Chat Completions supports function calling for `gpt-6-luna` only with `reasoning_effort = none`) |
| MCP SDK | `mcp==2.2.0` (existing) | — | unchanged |

Pins live in the `agents` optional extra of `pyproject.toml` (locked in
`requirements-agents.lock`, never in the API/worker image lock until P6.3-A
decides the worker image). The fake provider and fake runtime need none of
them; every CI test runs without the extra; the fake provider refuses to load
when `ENVIRONMENT = production`.

## Consequences

- One migration with ten tables and two FKs; ~45 CHECK/unique constraints;
  one new trigger helper; all additive. Truth artifacts change once.
- Every model call anywhere in DCLab produces a ledger row with a prompt
  release, a dated model id, a data class, an outcome scope and a cost in
  micro-units; spend per workspace is a sum over one table, reconciled against
  the counters nightly; P8.4 metering derives from it.
- Holdout blindness and data-class redaction become properties of the service
  layer and the tool shapers, shared by MCP, the Lab and Studio forms, and
  checkable by property tests.
- The four legacy LLM paths lose their direct `httpx` access and their
  production refusal; their behaviour is unchanged until P6.9-A.
- The assistant has no backend of its own: threads are agent runs, messages
  are events, actions are proposals, confirms are the P6.6 routes, streaming
  is a transport detail of one route through the existing BFF proxy.
- Governance has two ledgers (project-scoped decision records; workspace-scoped
  policy, level and switch chains); the console reads both. Accepted to keep
  ADR 0006's table untouched.
- The API process holds provider secrets for assistant turns (founder Q1);
  ARCHITECTURE.md §3.4 says so. Workers hold them for `agents.run` jobs. Both
  read them only in `gateway/providers`.
- Retention is possible without breaking append-only semantics because the
  two retained tables use the retention-aware trigger; all other ledgers keep
  the unconditional one.

## Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Separate assistant tables (`assistant_threads`, `assistant_messages`) | parallel owner of agent runs/events; the Lab's "N tool calls · cost · run id" is the agent run already |
| Keep holdout stripping per principal at the route layer | the lead agent is a human-session consumer calling services in-process; only a service-layer consumer mode is property-testable |
| `ai_policy:approve` as a `workspace_capability_service` entry | that module returns workspace-wide presentation hints that are true for every member, never per-user authority; role-derived checks are the existing pattern |
| Switches inside the policy document | a racing policy accept could re-enable a switch; off must always win and need no approver |
| Store spend only in `llm_invocations` (no counters) | reserve/settle needs a lockable row; summing the ledger per call is O(n) under concurrency |
| Budgets as a separate `llm_budget_reservations` table | more rows per call for the same guarantee; counter rows + a per-run hold + idempotent settle by invocation id suffice |
| Regex redaction of prompts | cannot know what a number is; tag-based structural redaction is checkable by a property test |
| Mutable `ai_policies` row per workspace | loses the audit chain the governance console must show; ADR 0006 pattern reused instead |
| Widen `project_decision_records` to workspace scope for governance changes | touches seven CFKs and every reader; the policy chain already carries the same facts |
| Unconditional `prevent_canonical_row_mutation` on `agent_events` | blocks the 365-day retention job and the `workspaces` cascade; a GUC-gated DELETE keeps UPDATE impossible |
| Replay that re-runs read tools live | the graph may have changed and tool results are stored as digests; replay compares digests instead |
| WebSocket for the assistant | SSE is one-directional, proxies through the BFF unchanged, and resumes by `Last-Event-ID` |
| `POST` returns a turn id and a separate `GET …/stream` | two round trips and a race on the first events; the POST-is-the-stream form plus the `events` page for resume is simpler |
| NOOA hosting the lead loop | ADR 0008 §6 (CodeAct default, in-process, unbounded) |
| Import-linter only for provider SDKs | the harness boundary (no runtime outside `AgentService.run`), the CodeAct ban and the secret readers need the same mechanism |

## Non-goals

No multi-file uploads or joins through chat; no workspace-wide (cross-project)
threads in the MVP; no per-user budgets (per workspace/project/run only, A8
adds plan metering); no shared threads (creator only; approvers audit through
agent runs); no online policy editor beyond the P6.11 propose/approve flow; no
vector memory; no provider failover across vendors mid-call (a refusal falls
back to the rule path); no `job` effect for assistant tools; no CodeAct lane
(Phase 9); no retention job before P8.7 (the columns and trigger exist); no
notifications channel before P8.8 (Inbox items only).

## Founder decisions (2026-10-04)

All recommendations below were accepted; ADR 0008 Q1 (models) was changed by
the founder, which narrows Q6 here.

1. **Assistant turns run in the API process** (SSE, 120 s cap; the API holds
   the OpenAI key through its own gateway instance).
2. **Budget defaults:** $25 / workspace / month, $12 / project / month, $1.00
   per assistant thread, $0.25 per turn, $0.10 per specialist run, alert at
   80 %, hard stop on; per user 60 turns / hour, 2 concurrent. (At
   `gpt-6.1-sol` prices a full 60k-token turn costs at most about $0.20, inside
   the turn cap.)
3. **No sample values in the MVP;** `data.max_class` capped at `aggregates`;
   new uploads default to `llm_exposure_policy = aggregate_only`; existing rows
   stay `deny` until their owner raises them.
4. **Retention 12 months** for redacted prompts and answers.
5. **Self-approval allowed** when a workspace has one approver, recorded and
   flagged in the console.
6. **Providers:** the gateway ships **one LLM provider adapter, OpenAI**
   (`gpt-6.1-sol`, `gpt-6-luna`), plus TypeSafe for Jev and the fake provider
   for tests. LiteLLM stays installed only because `nooa` depends on it; DCLab
   code never calls it (NOOA's LLM object is the gateway adapter). Adding a
   provider is an ADR note plus an allowlist entry, never a workspace setting.
7. **Kill switches:** off immediately by owners/admins; on needs an approver;
   an incident's switch-off holds until the incident is resolved.
8. **`data.user_text_to_jev = false`** by default.

## Open questions for the founder (answered above)

1. **Assistant turn location.** Run the turn in the API process (SSE streams
   directly, 120 s cap, the API holds provider secrets through its own gateway
   instance — recommended for the MVP; ARCHITECTURE §3.4 updated accordingly)
   or as a worker job with events relayed through Postgres `LISTEN/NOTIFY`
   (API stays secret-free, more moving parts).
2. **Budget defaults** (platform): $25 / workspace / month, $12 / project /
   month, $1.00 per assistant thread, $0.25 per turn, $0.10 per specialist run,
   alert at 80 %, hard stop on; per user 60 turns / hour, 2 concurrent.
   Confirm or change; A8 ties them to plans.
3. **May sample values ever leave?** Recommended: not in the MVP — `data.max_class`
   capped at `aggregates` for every workspace; new uploads default to
   `llm_exposure_policy = aggregate_only` (replacing ADR 0005's blanket `deny`
   for new rows only), existing rows stay `deny` (contribute nothing) until
   their owner raises them. Alternative: keep `deny` as the default for new
   uploads too (every project owner must opt in before any AI sees its data).
4. **Retention of redacted prompts/answers.** 12 months (recommended, matches
   the governance prototype) or 90 days.
5. **Self-approval.** When a workspace has one approver, allow the proposer to
   approve their own policy/level change (recorded and flagged in the console)
   — recommended for solo users — or require a platform operator as second
   approver (which this ADR otherwise forbids for customer policies).
6. **Platform allowlist of providers.** Confirm the MVP gateway ships two
   provider adapters (the current OpenAI path, migrated, plus the lead-agent
   provider chosen in ADR 0008 Q1) and TypeSafe for Jev; adding a provider is
   an ADR note plus an allowlist entry, never a workspace setting.
7. **Kill switches.** Confirm: owners/admins flip switches **off** immediately
   (audited, no approval); turning back **on** needs an approver; a switch
   turned off by an incident stays off until the incident is resolved.
8. **User text to Jev.** Confirm `data.user_text_to_jev = false` by default
   (intent routing then falls back to the lead agent's own step or the quick-
   action menu); turning it on is a workspace policy change.
