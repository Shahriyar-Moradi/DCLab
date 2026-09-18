# Execution-grade coding-agent prompt standard

This standard applies to every prompt in the Scope 0–10 program. A scope file
defines the plan-specific contract; this file defines how a coding agent must
turn that contract into a small, predictable change.

Every prompt also preserves
[`AGENT_FIRST_MVP_ARCHITECTURE.md`](../AGENT_FIRST_MVP_ARCHITECTURE.md): DCLab
begins software-directed/agent-assisted and graduates toward agent-directed/
policy-enforced operation without changing deterministic ownership.
Every prompt also preserves
[`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](../AWS_GCP_DEPLOYMENT_ARCHITECTURE.md):
AWS and GCP are equal supported targets behind one application contract.
Runtime, training, serving and compute infrastructure work also reads
[`EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md`](../EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md):
external CPU/GPU targets are separate from the AWS/GCP platform home.

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
| Bounded dataset scans (S0-P09+) | `services/dataset_materialization.py`, `services/artifact_store.py`, `services/lab_service.py`, `engine/data/loaders.py`, `engine/schema/profiler.py`, `engine/modeling/leakage_auditor.py`, `engine/leakage/detector.py` | Extend one provider-neutral `DataScanPort` over authorized digest-verified artifacts. DuckDB is a fresh ephemeral in-process adapter with code-owned templates and bounded Arrow output; retain deterministic DCLab semantics and pandas modeling compatibility. Never add a durable DuckDB catalog, direct database/object-store/network access, arbitrary SQL or Polars. |
| ML platform adapters (S3-P00+) | Approved architecture is `docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`; planned owners are narrow tracking, dataframe-validation, package and drift ports beside existing experiment/artifact/reproducibility services | MLflow records detailed run telemetry/package metadata; Pandera and Evidently run as worker libraries; safe native/skops formats load only in workers. DCLab remains lifecycle/tenant/approval/release/audit authority. Never expose vendor objects publicly or add W&B beside MLflow. |
| Core ML lifecycle | `Project`, `ProblemSpec`, `Dataset`, `DatasetProfile`, `FeatureSetVersion`, `FeatureTransformation`, `FeatureLineage`, `WorkflowRun`, `PipelineRun`, `ExperimentCandidate`, `ModelVersion`, `RuntimeEnvironment`, `CodeSnapshot` and current lineage/reproducibility services | Expose one typed project lifecycle projection over these owners. Add a lifecycle link only for a relationship the current schema cannot derive; never create a parallel generic graph as product truth. |
| Project decision memory | Planned in S1-P00; existing agent messages, events and checkpoints are not substitutes | Store immutable, tenant-scoped `ProjectDecisionRecord` facts/rationale/citations and supersession. Memory never authorizes, executes a change or stores hidden reasoning. |
| Authoritative lifecycle supervision (Scope 1+) | Not implemented until S1-P01A; planned owner is a pinned raw LangGraph `StateGraph` runtime invoked by `agent.turn.v1` | LangGraph is the sole DCLab supervisor graph and owns graph routing/private checkpoint execution only. DCLab owns product state, authorization, tools, budgets, events and recovery policy. Never nest another loop inside it; separately selected whole-run runtimes do not become supervisor graphs. |
| Deep Investigation (S2-P12+) | Approved architecture is `docs/agentic-program/DEEP_AGENTS_INVESTIGATION_COPILOT.md`; no implementation exists before S2-P12 | Deep Agents runs only in a separate `worker-investigation` dependency/process/runtime namespace from an explicit DCLab job. Its dataset/scientific, experiment/model and operations/drift modes use versioned read-only DCLab SDK/API bundles and return validated proposals; it cannot call or be called by raw LangGraph, own product state/memory, or execute commands. |
| OpenAI integration | `services/openai_provider.py`, `openai_smoke.py`, existing `LlmInvocation` model | Put the official OpenAI SDK/Responses API behind the DCLab gateway with explicit retention, structured output and tool policy. S2-P13 may add the hosted Agents API only as a whole-run adapter with isolated provider sessions and DCLab-mediated required actions; it never wraps/is wrapped by LangGraph or Deep Agents. Ordinary Pydantic is the default. PydanticAI requires an ADR and is limited to a no-tool, single-response typed leaf. |
| Data integration (Scope 7, early MVP slice) | Existing DataSource/DataAccess/IngestionRun/Dataset/Artifact owners plus the planned ConnectorPort/Runner | Use pinned `dlt` OSS only as a bounded extraction/load engine. DCLab owns connection/config/secret/sync/cursor/schema/publication state. No Airbyte/Openflow control plane, provider SDK or `dlt` state becomes product authority. |
| AWS/GCP deployment (all scopes; implemented in Scope 9) | Existing `apps/api/app/storage/` S3/GCS adapters plus planned `infra/tofu/`, `infra/kubernetes/` and private cloud adapter packages | Keep application/public contracts cloud-neutral. Use one OpenTofu/Kubernetes contract with AWS and GCP modules, short-lived workload identity, opaque resource references and equal conformance gates. Never import cloud SDKs into domain/application/public clients or treat Kubernetes/cloud state as product truth. |
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
   agent work begins—the pinned LangGraph/checkpointer versions and schemas;
   for S2-P12, also both dependency graphs, worker identities/handler allowlists,
   network edges and runtime-state namespaces; for ML-platform work, also the
   installed/absent provider packages, external IDs, object ownership, endpoint,
   credential, database/schema and dependency-image boundaries; for bounded
   scan work, also current full-frame call sites, parity corpus, artifact
   authorization/materialization flow, template registry, Arrow batch schema,
   input/output/memory/thread/temp/time bounds and pandas rollback path; for
   S2-P13, also the OpenAI Agents API/SDK maturity and retention contract,
   provider-session mapping, environment, required-action and webhook edges;
   for connector work, also the `dlt` pin/license/dependency location, source
   API/version/scopes, engine-state/cursor ownership, staging format, schema-
   drift rules and full resource/egress budgets; for every change, also record
   whether cloud resources are involved, the provider-neutral owner, AWS and
   GCP implementations or explicit not-applicable proof, provider identifiers,
   parity tests, staging evidence and independent rollback.
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
8. **Core-product impact:** applicable Data Scientist/ML Engineer job, lifecycle
   nodes/edges and synchronized conversation/workflow/implementation views; or
   an explicit statement that the prompt is infrastructure-only.

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
- AWS and GCP are equal supported production targets. Domain/application/API/
  SDK/CLI/agent packages cannot branch on cloud or import cloud SDKs. Use opaque
  `DeploymentProfile`, `ObjectVersionRef`, `SecretRef`, `KeyRef` and private
  adapters. When a prompt touches infrastructure, storage, identity, secrets,
  networking, sandboxing, telemetry or deployment, implement/test both provider
  contracts or record why the concern is genuinely provider-independent.
- Production uses one AWS/GCP home provider per environment. Approved Runpod,
  Railway, Lambda GPU Cloud, Vast.ai and Nebius execution targets may be used
  through separately gated adapters, grants, quotes and transfer manifests. Keep
  provider keys in the trusted home provisioner and reuse runtime/job/usage owners.
  A third-party API key requirement does not permit static AWS/GCP keys or keys in
  user code. Unknown security/region/hardware capabilities fail closed.
  Do not implement dual-writer
  PostgreSQL/object storage, automatic cross-cloud failover, SQS plus Pub/Sub,
  or provider-native workflow truth for the MVP. Cross-cloud portability is
  proven by controlled backup/restore and same-release conformance. Scoped external
  staging is not a second authoritative object store. External readiness is scoped
  by backend, lane, region/profile, data policy and home-cloud transport evidence.
- LLM output is untrusted input. Validate typed output, re-authorize every tool
  call, bound all context/results, and never use the LLM as an authorization or
  scientific-verification authority.
- There is one authoritative DCLab agent loop. Raw LangGraph may select the next
  code-owned graph node; it may not authorize, choose arbitrary worker handler
  keys, execute private services, or replace DCLab product records. Use ordinary
  Pydantic models for graph state and structured output; do not nest PydanticAI,
  `pydantic-graph`, LangChain `create_agent`, Deep Agents or another autonomous
  loop inside a node. S2-P12's separate Deep Agents worker is not a node,
  specialist or tool of this graph and the graph is not one of its tools.
  S2-P13's OpenAI-hosted harness is also a separate whole-run adapter. Each
  AgentRun records exactly one runtime kind/version; no runtime can invoke,
  resume, checkpoint, tool-call or authorize another. PydanticAI, if separately
  approved, performs one no-tool structured response and has no loop authority.
- LangGraph checkpoint rows are private runtime reconstruction data in a
  dedicated PostgreSQL schema. Public APIs, UI and SDK read DCLab-owned
  AgentRun/Step/Event/ToolCall/Citation records, never checkpoint internals.
- One `agent.turn.v1` job may traverse bounded pure nodes but performs at most
  one provider or tool operation, persists its DCLab result and checkpoint, and
  stops before scheduling the next turn. All replayable operations are
  idempotent and budget-settled exactly once.
- The ML lifecycle graph is a DCLab domain projection and remains distinct from
  LangGraph runtime topology, multi-agent task DAGs and notebook cell graphs.
  All surfaces use the same resource IDs, versions, digests and permissions.
- Project memory consists of immutable decision records with observed facts,
  hypotheses, rationale, alternatives, constraints, citations and resulting
  versions. Conversation history may provide evidence but is not silently
  promoted into memory. Accepted memory cannot bypass a typed command.
- Code is inspectable but not the primary interface. An implementation view may
  expose authorized reproduction source, formulas, configuration, environment,
  artifacts, jobs and safe logs; edits create reviewed immutable versions.
- Investigation Copilot external/product tools are read-only DCLab SDK/API calls
  re-authorized on every request. Its internal messages, todos, StateBackend
  virtual files, summaries,
  checkpoints and memory are short-lived working state, never lifecycle,
  decision, authorization or public API truth. The initial release has no Deep
  Agents subagent, persistent memory, host filesystem, shell, arbitrary HTTP or
  code execution.
- OpenAI Responses requests explicitly set storage/retention behavior and expose
  only approved DCLab function schemas. Built-in web/computer/shell/MCP tools
  are disabled in authoritative workers. OpenAI Agents `required_actions` are
  untrusted pending requests passed through ToolRunner authorization, approval,
  idempotency and reconciliation; provider session/history never becomes
  lifecycle, memory or command truth.
- Connector runs accept a server-owned ConnectorDefinition/ConfigVersion,
  scoped secret reference, immutable source selection and bounded sync plan.
  A pinned `dlt` adapter may perform REST/SQL/filesystem extraction and maintain
  opaque engine state, but DCLab owns the durable cursor contract, job,
  cancellation, schema review, quarantine, Arrow/Parquet staging and atomic
  DatasetVersion publication. MVP sources are read-only; no arbitrary SQL,
  reverse ETL, Airbyte/Openflow control plane or connector-owned agent loop.
- Bounded tabular analytics accept only an authorized immutable DatasetArtifact
  plus a registered operation and typed arguments. A fresh in-memory DuckDB
  connection executes a code-owned template with locked memory/thread/input/
  output/temp/time limits, extension install/load and external access disabled,
  and deterministic cleanup on success, denial, timeout or cancellation.
  Results cross the port as bounded Arrow batches and are normalized by DCLab
  services. SQL strings, filesystem paths, URLs, DuckDB/provider types and
  configuration statements never cross agent, notebook, `/v1`, SDK, CLI or MCP
  contracts. No persistent catalog, silent unbounded pandas fallback or Polars.
- MLflow, Pandera, Evidently, skops/native formats and OpenTelemetry are adapters
  or libraries, not domain authorities. Provider SDK classes, run objects,
  exceptions, registry states, report objects and URIs never cross `/v1`, public
  SDK/CLI/MCP or agent contracts. DCLab stores provider-neutral references,
  digests, canonical final metrics and lifecycle state; external correlation
  tags are never authorization.
- MLflow is the single production-MVP experiment tracker. It uses a private
  endpoint, separate backend database/schema identity and dedicated object-
  storage boundary. External writes occur outside DCLab transactions and use
  bounded idempotent reconciliation. Tracking-degraded runs cannot be verified
  or promoted. There is no production local-filesystem fallback and no MLflow-
  side DCLab promotion/deployment/webhook authority.
- A DCLab `FeatureContract` is canonical and deterministically compiles to a
  strict Pandera validator. Evidently only calculates versioned monitoring
  reports; DCLab selects thresholds, persists `MonitoringWindow`, alerts and
  remediation state. The API never loads model artifacts; workers verify
  format, type allowlist, signature/digest and environment before safe loading.
- Existing business-side routes remain supported and the Scope 8 plans remain in
  force. The Core ML MVP release gate is independent unless a selected pilot
  explicitly includes a business action/outcome journey.

## 4. Prompt size and change budget

Each prompt should produce one reviewable pull request. Default limits are:

- one primary architectural concern;
- zero or one additive migration (a deliberate expand/backfill pair may use two);
- no more than one new public resource family;
- no more than one worker handler family;
- no drive-by formatting, renaming, dependency upgrade, or unrelated cleanup;
- no additional agent/orchestration framework without an approved replacement
  ADR, dependency/supply-chain review and migration plan; the only existing
  exceptions are the separately isolated S2-P12 Deep Investigation contract and
  the whole-run S2-P13 OpenAI Agents adapter; neither may enter worker-agent;
- no second experiment tracker, model registry, monitoring store, feature
  contract or observability authority; a new external library requires the
  approved Plan 3.0 port, optional dependency/image boundary, contract tests and
  an independent kill switch;
- no second dataframe/query engine beside the exclusive `DataScanPort` adapter;
  adding Polars or another engine requires measured Scope 10 evidence, a
  replacement/selection ADR, semantic parity, independent rollback and proof
  that one operation cannot execute through two engines;
- no second data-integration control plane: `dlt` remains a private library
  behind ConnectorPort, and Airbyte/Snowflake Openflow integration requires an
  external-replication ADR rather than deployment inside the default stack;
- no SHAP dependency, computation, persistence, job, public route or UI in the
  production MVP; an existing enum/placeholder is not implementation evidence;
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
