# Execution-grade coding-agent prompt standard

This standard applies to every prompt in the Scope 0–10 program. A scope file
defines the plan-specific contract; this file defines how a coding agent must
turn that contract into a small, predictable change.

## 1. Repository routing map

Inspect these existing surfaces before inventing a new path:

| Concern | Canonical current surface | Extension rule |
| --- | --- | --- |
| API composition | `apps/api/app/main.py`, `apps/api/app/api/deps.py` | Mount resource routers in `main.py`; keep dependency resolution in `deps.py`. |
| Stable application API | `apps/api/app/api/v1.py`, `apps/api/app/domain/application_api.py` | Preserve existing `/v1`; add cohesive routers such as `api/v1_agents.py` rather than another large catch-all. |
| Persistence | `apps/api/app/db/models.py`, `db/base.py`, `db/integrity.py` | Use the current SQLAlchemy metadata. Split models only through an ADR and a migration-safe import plan; never register a table twice. |
| Migrations | `apps/api/alembic/versions/`, `apps/api/tests/_alembic_catalog.py` | Inspect the live head, choose the next unused revision, and test empty plus supported previous-head upgrade. |
| Authorization | `api/deps.py`, `services/authorization_service.py`, `services/workspace_capability_service.py`, `services/workspace_entitlement_service.py` | Central services decide; route/UI/agent checks are not independent authorities. |
| Browser identity | `api/auth.py`, `services/session_service.py`, `services/csrf_service.py`, `services/login_throttle.py`, `apps/web/app/api/backend/[...path]/route.ts` | Continue the HttpOnly session/BFF design; do not restore a JavaScript bearer token. |
| Workspace selection | `services/workspace_selection_service.py`, `apps/web/lib/infrastructure/active-workspace.ts`, `WorkspaceSelector.tsx` | Server membership validates every selected workspace; selection is never authorization. |
| Durable intent/jobs | `services/execution_request_service.py`, `services/ml_job_service.py`, `services/job_dispatcher.py`, `services/job_handlers.py` | Reuse `ExecutionRequest` and `MlJob`; add code-owned handler keys and bounded ID-only payloads. |
| Deterministic ML | `domain/model_build.py`, `services/model_build_service.py`, `services/workflow_execution_service.py`, `ml/` | Agents may call typed services; they must not duplicate or bypass scientific logic. |
| Evidence/artifacts | `services/artifact_service.py`, `artifact_store.py`, `lineage_service.py`, `evidence_lock_service.py`, `pipeline_verifier.py` | Store metadata/digests in PostgreSQL and large immutable bodies in object storage. |
| Agent orchestration (Scope 1+) | Not implemented until S1-P01A; planned owner is a pinned raw LangGraph `StateGraph` runtime invoked by `agent.turn.v1` | LangGraph owns graph routing/checkpoint execution only. DCLab owns product state, authorization, tools, budgets, events and recovery policy. Do not add a second agent loop. |
| LLM integration | `services/openai_provider.py`, `openai_smoke.py`, existing `LlmInvocation` model | Introduce a DCLab provider-neutral gateway around, not beside, existing usage; retain a deterministic fake and use the official OpenAI SDK as the first adapter. PydanticAI is not part of the production MVP. |
| Observability | `services/observability_service.py`, `domain/observability.py`, `api/observability.py` | Emit bounded structured events and metrics; never log prompts, secrets, raw rows, or tokens. |
| Python client | `packages/dclab_client/dclab_client/` and `packages/dclab_client/tests/` | Use HTTP only. Add typed resource modules when `client.py` would become another monolith. |
| Web application | `apps/web/app/`, `apps/web/lib/application/`, `apps/web/lib/infrastructure/api-client.ts` | Use the existing App Router, query/session providers, UI primitives, and BFF. |
| Backend tests | `apps/api/tests/` | Use real PostgreSQL fixtures from `conftest.py` for tenancy, constraints, leases, concurrency, and migrations. |
| Browser tests | `apps/web/e2e/` | Test user-visible security and workflow boundaries through the BFF. |
| Truth/docs | `scripts/generate_truth_artifacts.py`, `scripts/record_repo_truth.py`, `scripts/check_truth_drift.py`, `contracts/`, `docs/verification/` | Only the generator writes checked artifacts; the recorder/checker are read-only. Update CURRENT prose by linking to canonical artifacts. |

If the listed path changes before a prompt is run, the coding agent must locate
its current replacement with `rg`, record the substitution in the evidence,
and preserve the same architectural owner. A stale path is not permission to
create a parallel subsystem.

## 2. Required implementation packet

Before editing, the coding agent must add a short implementation packet to its
working notes or PR description:

1. **Baseline:** branch/SHA, dirty files, database head, relevant routes,
   models, services, jobs, client modules, UI routes, tests, flags, and—when
   agent work begins—the pinned LangGraph/checkpointer versions and schemas.
2. **Change map:** exact files to add/change and why each existing owner is
   reused. Mark generated files separately.
3. **Contract:** input/output schemas, state transitions, authorization,
   invariants, errors, idempotency, concurrency, cancellation, retention, and
   audit behavior applicable to the work unit.
4. **Migration:** expand/backfill/enforce/contract phases, compatibility window,
   index/lock risk, downgrade or forward-repair procedure, and object cleanup.
5. **Verification:** exact commands and expected success, denial, tenant,
   concurrency, failure, recovery, and rollback cases.
6. **Operations:** metrics, trace attributes, safe logs, alerts/runbook, feature
   flag, kill switch, capacity bound, and rollout owner.
7. **Non-goals:** behavior deliberately excluded from this work unit.

No edit begins until this packet shows that the prompt can be completed without
an unrelated refactor. If the packet exposes an unresolved architecture choice,
finish only the ADR/design prompt and stop before implementation.

## 3. Default contract rules

- IDs are server-generated UUIDs unless the existing resource contract uses a
  documented alternative. Timestamps are UTC and server-generated.
- Tenant tables carry `workspace_id` directly where practical and use composite
  foreign keys/unique constraints to prevent cross-workspace references.
- Mutable resources use an explicit version or ETag for optimistic concurrency.
  Append-only/immutable resources are superseded, never edited in place.
- List APIs use bounded `limit` plus opaque cursor and return a common page
  envelope. Empty lists are successful, not exceptional.
- Errors use the stable `/v1` envelope with code, safe message, request ID, and
  bounded field details. Internal exceptions and provider bodies are redacted.
- Every command accepts or derives an idempotency key and binds it to a canonical
  payload digest. Same key/same digest replays; same key/different digest is a
  conflict.
- Asynchronous work persists intent and a job atomically, uses a code-owned
  handler key, leases with heartbeat, cooperative cancellation, bounded retry,
  terminal recovery, and an append-only event.
- Configuration is typed in `apps/api/app/config.py` and represented in the
  appropriate `.env.example`; production defaults fail closed.
- New user-visible capability is disabled by default until its verification
  prompt passes. Emergency disable must not require a schema rollback.
- LLM output is untrusted input. Validate typed output, re-authorize every tool
  call, bound all context/results, and never use the LLM as an authorization or
  scientific-verification authority.
- There is one agent loop. LangGraph may select the next code-owned graph node;
  it may not authorize, choose arbitrary worker handler keys, execute private
  services, or replace DCLab product records. Use ordinary Pydantic models for
  graph state and structured output; do not nest PydanticAI, `pydantic-graph`,
  LangChain `create_agent`, or another autonomous loop inside a node.
- LangGraph checkpoint rows are private runtime reconstruction data in a
  dedicated PostgreSQL schema. Public APIs, UI and SDK read DCLab-owned
  AgentRun/Step/Event/ToolCall/Citation records, never checkpoint internals.
- One `agent.turn.v1` job may traverse bounded pure nodes but performs at most
  one provider or tool operation, persists its DCLab result and checkpoint, and
  stops before scheduling the next turn. All replayable operations are
  idempotent and budget-settled exactly once.

## 4. Prompt size and change budget

Each prompt should produce one reviewable pull request. Default limits are:

- one primary architectural concern;
- zero or one additive migration (a deliberate expand/backfill pair may use two);
- no more than one new public resource family;
- no more than one worker handler family;
- no drive-by formatting, renaming, dependency upgrade, or unrelated cleanup;
- no additional agent/orchestration framework without an approved replacement
  ADR, dependency/supply-chain review and migration plan;
- generated snapshots may be large but must be reproducible;
- if the implementation exceeds roughly 800 non-generated changed lines or 20
  hand-edited files, stop and split at a contract boundary unless the prompt
  explicitly justifies the exception.

A verification prompt may repair defects in the immediately preceding work but
must not silently add a new product capability.

## 5. Required completion report

Every coding-agent response ends with:

```text
Prompt ID and status:
Baseline SHA / resulting diff:
Files changed and ownership reason:
Schema/API/event/state contracts:
Migration and compatibility evidence:
Commands run with observed results:
Tenant/security/adversarial evidence:
Metrics, flag, rollout and rollback:
Known limitations and non-goals:
Evidence document updated:
Next eligible prompt:
```

Words such as “complete”, “secure”, “scalable”, or “production-ready” may be
used only when the named scope gate has observed evidence. Otherwise report the
precise implemented slice.

## 6. Existing Scope 0 evidence compatibility

The IDs `S0-P01A`, `S0-P01B`, `S0-P02A`, `S0-P02B`, and `S0-P03A` already have
working-tree evidence. Their original meanings remain stable. Expanded Scope 0
prompts add reconciliation and residual-work gates rather than relabeling that
history. If current changes already satisfy a later expanded prompt, run its
verification, record `VERIFIED`, and make no duplicate implementation.
