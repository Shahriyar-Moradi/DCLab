# Remaining-scope execution map and prompt acceptance contract

This document is normative for **S0-P04D onward, all S1–S10 prompts, and the
applicable RT1–RT8 runtime work packages**. Read it with the complete scope file,
the individual prompt and `EXECUTION_STANDARD.md`. It refines implementation
instructions; it does not authorize executing all plans at once, change an
accepted architecture, or claim that future code already exists.

No static prompt can guarantee a correct implementation or know the exact
repository state months later. The way to make a work order predictable is to
require a checked, prompt-specific execution card against the **live** tree,
then prove its acceptance criteria in code and tests. A coding agent must stop
at a design decision if it cannot make that card unambiguous. It must not fill
unknown fields with guessed table names, cloud behavior, endpoint shapes or
test results.

## Mandatory execution card for **each** remaining prompt

Before changing code, write this card in the prompt's evidence document or PR
description. Review/repair existing implementation first. Every numbered item
must contain concrete facts or `N/A — reason`; “as appropriate” is not an
answer. This is a precondition, not a retrospective completion report.

1. **Goal and cut line.** State the one observable outcome, the current and
   expected behavior, actors/audiences, prerequisite prompt IDs and excluded
   later work. Name the primary owner module and maximum reviewable PR slice.
2. **Live source map.** Record branch/SHA, dirty paths, remote relationship when
   relevant, live Alembic current/head, exact files/classes/functions/routes,
   relevant tests, callers and generated artifacts. Use `rg` to resolve any
   planned path that moved. Mark each file `reuse`, `edit`, `new`, `generated`
   or `untouched`; do not create a parallel service because a planned name is
   stale.
3. **Persistence contract.** List each table/column/type/nullability/default,
   PK/FK/composite workspace FK, unique/check/index, ownership and immutable
   relationship affected. Show one-row examples for old/new/null/ambiguous
   states. Specify expand → backfill/quarantine → enforce → contract phases,
   previous-head and empty upgrade tests, lock/index risk, downgrade or safe
   forward repair. State explicitly when there is no migration.
4. **HTTP/client contract.** For each touched route, give method/path,
   principal/capability/workspace check **before lookup**, request and response
   JSON examples, status/error code for 200/201/202/204, 400/401/403/404,
   409/412, 422, 429 and 503 as applicable, cursor/ETag/idempotency rules,
   body/stream bounds and OpenAPI/SDK/CLI compatibility. Distinguish 403
   capability denial from 404 foreign/absent-resource anti-enumeration. Mark
   every omitted status `N/A — reason`.
5. **Service/state contract.** Name public service functions and typed input/
   output/exception contracts. Draw allowed state transitions and transaction
   boundary; identify the single authorization, scientific, policy, budget and
   lifecycle authorities. Specify optimistic concurrency, audit identity and
   replay behavior. No route, UI, SDK or agent may become a second authority.
6. **Jobs/events/external effects.** Name the code-owned handler, persisted
   intent, ID-only payload schema, event names/versions, ordering, idempotency
   key/digest, lease/heartbeat, timeout, retry/backoff, cancellation point,
   ambiguous-result reconciliation and compensation. Name the network/secret/
   object-store boundary. If synchronous/read-only, state why these do not
   apply. A provider call must never occur inside an uncommitted product DB
   transaction.
7. **Frontend/audience contract.** List exact routes/components/hooks/BFF
   calls, workspace-keyed cache, loading/empty/error/denied/stale/switch states,
   accessible labels/focus/announcements and mobile/responsive behavior. Give
   an audience field matrix: client, developer, operator/admin; forbid paths,
   handler keys, prompts, stack traces, raw provider bodies, storage keys and
   cross-tenant counts from public projections. If no UI changes, identify the
   existing consumer and prove its shape remains compatible.
8. **Tests and evidence.** Name exact test files, fixtures and commands before
   editing. Include happy path, invalid input, no permission, two workspaces,
   direct API bypass, concurrency/replay, cancellation/recovery, old/new schema,
   provider failure and accessibility/browser cases as applicable. State
   expected assertions, not merely “add tests”; run PostgreSQL for relational
   claims and a faithful fake plus staging for cloud/provider claims. Record
   observed exit codes, duration, environment and same-SHA CI separately.
9. **Operations and safe rollout.** Name typed configuration variables and
   fail-closed defaults, secret location, metrics (bounded labels), safe audit
   events/log fields, alert thresholds, dashboard/runbook, feature flag and
   kill switch. Give enablement order, canary, rollback and data repair. A
   rollback cannot restore global reads, raw output, unsafe auth or unapproved
   side effects. Name owner and retention/deletion implications.
10. **Completion ledger.** Link changed files and generated snapshots; compare
    actual behavior to each acceptance assertion; mark `VERIFIED`, `PARTIAL`,
    `BLOCKED` or `NOT_TESTED` honestly. Record unresolved risks and only the
    next dependency-eligible prompt. Do not claim a full plan/scope complete
    from a passing unit test or a dirty worktree without exact-SHA CI.

The card must stay within one primary concern, normally ≤800 non-generated
changed lines and ≤20 hand-edited files. If it cannot, split the prompt at a
transaction/contract boundary while preserving its ID and dependency order;
do not absorb a later prompt. An ADR-only prompt stops after its decision and
verification of current facts. A gate prompt repairs defects in its own plan
but does not silently introduce an unrelated capability.

## Type-specific definition of done

| Prompt type | Required concrete proof before `VERIFIED` |
| --- | --- |
| ADR/contract | Current-tree inventory, rejected options, authoritative owner, exact schema/API/state/event draft, security/cost trade-off, migration/rollback, unresolved choice and implementation prompt dependency. No speculative implementation. |
| Persistence | Additive migration, model parity, tenant composite relationships, null/legacy handling, previous-head and empty upgrade, constraint/race tests, lock/restore notes; never silently assign ownership or rewrite immutable evidence. |
| Service/job | Typed interface, transition table, transaction/claim boundaries, duplicate and crash-after-side-effect tests, cancellation/reconciliation, bounded payload and safe logs; provider fake proves the same failure modes. |
| API/SDK/CLI/MCP | Exact route/schema/status/auth matrix, OpenAPI and client contract snapshots, cross-workspace and direct-call denial, bounded pagination/stream, retry/idempotency and no provider/internal types. |
| UI/notebook | Existing route/component reuse, server capability authority, tenant cache isolation, complete UI states, keyboard/screen-reader tests and browser proof for slow concurrent workspace switching. |
| Infrastructure/provider | One cloud-neutral app contract, isolated credentials, AWS and GCP parity or explicit N/A proof, IaC plan review, staging smoke, cost/egress limits, secret rotation, backup/restore and independent rollback. |
| Verification/release | Exact-SHA commands and artifacts, adversarial matrix, performance/cost baseline, known warnings/owners, rollout and kill switch, signed go/no-go; a historical report is not current evidence. |

## Remaining plan outcomes

Each row is a **required plan-level acceptance outcome** inherited by every
prompt in that plan. The scope file owns the detailed prompt sequence. A prompt
implements only its assigned slice of the row; the final plan gate proves the
whole row. Existing evidence IDs through S0-P04C retain their meanings.

### Scope 0 — foundation closure

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 0.4 (remaining P04D) | Retire or tenant-scope legacy simulation/insight/event customer reads using current route/service owners and ADR 0004. | Every legacy route, SDK and browser consumer has a two-workspace/role matrix; quarantine is honest; no global customer read or raw diagnostic remains; compatibility and forward repair are documented. |
| 0.5 | Dataset/column classification, quarantine and retention policy in existing lineage and materialization/artifact services. | Unknown/nullable policy denies preview, LLM and download; transitions and holds survive duplicate jobs and object failure; no evidence is silently erased. |
| 0.6 | Shared `/v1` error, page, lifecycle and artifact contracts in `api/v1.py`, application services and Python client. | OpenAPI/client parity, tenant anti-enumeration, idempotency/cancel semantics and bounded streams pass live PostgreSQL tests without a new parallel API. |
| 0.7 | Reproducible local API/web/worker/PostgreSQL/object topology and matching CI commands. | Cold bootstrap, migration, seed, job→worker→artifact, web and browser checks work; cleanup cannot target production or unresolved paths. |
| 0.8 | Accepted ADRs, actual FK-cycle disposition and measured capacity/risk baseline. | Decisions match code, owners/rollback are named and no speculative broker/vector/cloud control plane is installed. |
| 0.9 | Exclusive `DataScanPort` with code-owned templates and isolated ephemeral DuckDB over authorized immutable artifacts. | Scientific parity, limits/cancellation/temp cleanup, malicious SQL/path denial, low/base/high benchmark and pandas rollback are observed. |
| 0.10 | One cloud-neutral immutable object contract with private S3 and GCS adapters. | Digest/version/stream parity, wrong-tenant and changed-object denial, AWS/GCP staging evidence and independent adapter rollback pass. |

### Scope 1 — read-only agent foundation

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 1.0 | Canonical ML lifecycle projection plus immutable `ProjectDecisionRecord` memory over existing project/dataset/run/model lineage. | IDs/versions/digests agree across views; supersession is explicit; memory cannot authorize or mutate. |
| 1.1 | Agent/policy typed contracts and authority map in domain/services, without an LLM-driven permission path. | Every proposed action has a DCLab-owned principal, workspace, policy release, budget and allowed tool class. |
| 1.2 | Tenant-constrained AgentRun/Step/Event/ToolCall/Citation persistence, private checkpoint boundary. | Composite integrity, append-only audit, replay and concurrent workspace denial pass migrations and PostgreSQL tests. |
| 1.3 | Versioned policy releases and settled usage ledger. | Reservation/settlement is idempotent; over-budget, stale/revoked policy and crash recovery fail closed. |
| 1.4 | Provider-neutral LLM gateway behind data policy, budget and approved model registry. | Schema validation, token/cost settlement, timeout/retention/egress controls and provider fault isolation pass fake and staging tests. |
| 1.5 | Immutable, audience-safe context envelope built from authorized evidence only. | Prompt injection, secret/raw-row leakage, stale membership, citation integrity and truncation bounds are tested. |
| 1.6 | Application services create/read/cancel agent turns and durable product records. | Request idempotency, current authorization, state transitions, duplicate callback and recoverable failure are proved. |
| 1.7 | One pinned raw LangGraph supervisor in the worker, with DCLab-owned product state. | One bounded provider/tool operation per durable turn, private checkpoints, resume/replay and no nested runtime authority. |
| 1.8 | Versioned read-only tool catalog over typed DCLab services. | Tool inputs/outputs are bounded, reauthorized per call, tenant-isolated and free of arbitrary SQL/files/network. |
| 1.9 | Agent `/v1` and Python client expose only DCLab-owned safe projections. | Live OpenAPI/SDK parity, pagination, authorization and no checkpoint/provider internals. |
| 1.10 | Agent Studio in existing web shell with workspace-keyed state. | Loading/empty/denied/cancel/stream and accessible navigation pass component and browser tests; server remains authority. |
| 1.11 | Evaluation, runbooks, cost and internal read-only release gate. | Golden/adversarial corpus, quality/cost limits, kill switch, exact-SHA CI and operator recovery support go/no-go. |
| 1.12 | Optional Jev typed-decision adapter behind DCLab semantic-decision policy. | Shadow cost/quality/abstention comparison and clean disable prove no Jev authorization, scientific or runtime authority. |

### Scope 2 — proposal-only agentic operating system

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 2.1 | Specialist roles, delegation/proposal/review contracts and exact authority boundaries. | No specialist, provider or framework may directly issue a command or become a second supervisor. |
| 2.2 | Tenant-owned task/delegation/review/proposal records with immutable lineage. | Concurrent review, stale proposal, revocation, supersession and cross-tenant constraints pass. |
| 2.3 | Versioned prompts/models/data/tool/evaluation releases under gateway policy. | Release pinning, rollback, dataset exposure, token/cost attribution and shadow evaluation are reproducible. |
| 2.4 | Dataset Steward proposes bounded, cited dataset actions through existing data services. | Classification/quality/lineage proposals abstain on unknowns and cannot publish or read raw data without policy. |
| 2.5 | Problem and experiment-plan architects emit typed, cited plans. | Target/metric/validation/leakage constraints remain deterministic and unapproved plans cannot execute. |
| 2.6 | Preparation, feature, leakage and validation critics review existing scientific evidence. | Critics never override holdout/scientific locks; unsafe or contradictory proposals are rejected with reason codes. |
| 2.7 | Experiment Director and candidate/metric critic propose bounded iteration. | Budget, candidate eligibility, stop rules and final-test protection are deterministic and replayable. |
| 2.8 | Artifact/provenance audit and audience-safe reporting projections. | Digests/citations are verifiable; client/developer/operator views omit internal and cross-tenant detail. |
| 2.9 | One LangGraph supervisor coordinates proposals, not side effects. | Delegation/review state survives crash/replay with one runtime per run and no nested loops. |
| 2.10 | Synchronized operations UI for tasks, evidence and review. | Same resource IDs and permissions across conversation/workflow/implementation views; accessible stale/denied states. |
| 2.11 | Whole-pipeline shadow evaluation before activation. | Cost/quality/latency/abstention and scientific non-regression gates use frozen fixtures and exact-SHA evidence. |
| 2.12 | Isolated read-only Deep Investigation worker and three typed proposal modes. | Worker cannot invoke LangGraph/tools/commands or persist private memory as product truth; clean disable works. |
| 2.13 | Optional isolated OpenAI Agents whole-run adapter. | Provider required actions pass DCLab authorization/approval; provider session cannot own product state or call another runtime. |
| 2.14 | Optional isolated NOOA whole-run proposal adapter. | CodeAct isolation, credential/egress denial, token/cost/quality comparison and clean removal prove safe adoption. |

### Scope 3 — controlled commands and model operations

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 3.0 | Private MLflow/Pandera/Evidently/package ports with DCLab lifecycle authority. | Tracking degradation blocks promotion; formats load only in workers; provider objects never cross public contracts. |
| 3.1 | One atomic, idempotent model-build command over existing ExecutionRequest/job/scientific services. | Same key/digest replays, conflict rejects, tenant lineage is constrained and no duplicate canonical model appears. |
| 3.2 | Cooperative cancellation, retry and immutable child lineage. | Crash/retry/cancel races settle once; artifacts and status reconcile without false immediate cancellation. |
| 3.3 | Exact, version-bound approval and risk policy. | Approval cannot be reused for changed payload, principal, workspace, policy or expired state; consumption is atomic. |
| 3.4 | Bounded agent command tools call the same application services. | Reauthorization and approval precede effects; no agent-selected handler or direct private service bypass. |
| 3.5 | Budgeted experiment iteration with explicit hypotheses and stop rules. | Holdout stays locked; scientific/economic comparison and child lineage are reproducible. |
| 3.6 | One command/approval experience across API, SDK/CLI preview and web. | Same state/errors/IDs, optimistic updates, accessible review and cross-surface E2E. |
| 3.7 | Controlled-write release gate. | Scientific regression, adversarial mutation, failure recovery, load and cost evidence support go/no-go. |
| 3.8 | DCLab-owned model registration, safe batch prediction and monitoring MVP. | Package/feature-contract verification, rollback, bounded inference, drift windows and two-cloud ops evidence pass. |

### Scope 4 — agentic notebook and managed cells

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 4.1 | Tenant-owned immutable notebook/cell/version/output lineage over canonical project/run/artifact IDs. | Cell order/version conflicts, output digest, workspace FKs and recovery pass; notebook is not lifecycle truth. |
| 4.2 | Managed typed cell runtime for bounded DCLab operations, with Jupyter only behind an isolated kernel port. | No arbitrary host SQL/files/network; quota, timeout, cancel, restart and kernel loss reconcile. |
| 4.3 | Notebook `/v1`, jobs, SDK and export contracts. | HTTP/SDK parity, idempotent cell submission, bounded output/stream, export fidelity and tenant denial. |
| 4.4 | Agent proposes notebook edits/executions through reviewed typed services. | Diff, approval and pinned evidence precede execution; agent cannot obtain kernel credentials or hidden state. |
| 4.5 | Notebook UI in existing web shell. | Keyboard cell navigation, focus, autosave conflict, queued/running/cancelled/error/expired states and tenant switch pass E2E. |
| 4.6 | Optional isolated Python beta with hardened execution profile. | CPU/memory/time/egress/filesystem caps, package allowlist and malicious-code escape campaign pass before enablement. |
| 4.7 | Sandbox and notebook release gate. | Multi-tenant isolation, resource cleanup, crash recovery, abuse limits and operator kill switch are observed in staging. |
| 4.8 | Optional NOOA notebook collaborator as proposal-only whole-run runtime. | No direct Jupyter, DB or cloud access; proposals are validated/reviewed; cost/quality and removal gates pass. |

### Scope 5 — public API, SDK and CLI

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 5.1 | Workspace-bound machine identity and one-time hashed credentials, distinct from browser sessions. | Scope/capability mapping, rotation/revocation, device-flow abuse and current membership are tested. |
| 5.2 | One complete stable `/v1` resource/command surface over application services. | OpenAPI compatibility, status/error/page/ETag/idempotency contract and no legacy semantic fork. |
| 5.3 | Public HTTP-only Python SDK in `packages/dclab_client`. | Typed parity, bounded retries/streams, explicit workspace and no API-internal/database/cloud imports. |
| 5.4 | Customer CLI over the SDK with safe auth and machine-readable output. | Exit-code and JSON contracts, secret-safe config, cancellation and cross-platform package tests. |
| 5.5 | Stable automation examples and client conformance. | Same action across curl/SDK/CLI has identical IDs, permissions, errors and retry semantics. |
| 5.6 | Signed/reproducible package and release supply chain. | Provenance, compatibility matrix, dependency scanning and clean rollback before publication. |

### Scope 6 — MCP

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 6.1 | Pin protocol/SDK and map DCLab resources/tools/prompts to reviewed scopes. | Threat model and package boundary prevent MCP from becoming an authorization or memory authority. |
| 6.2 | Local read-only stdio MCP server over DCLab HTTP client. | Framing, bounded output, current workspace/capability checks and malicious content boundaries pass. |
| 6.3 | Hosted Streamable HTTP with OAuth/resource metadata and network hardening. | Audience/origin/host/session, replay, token revocation, scale and recovery conformance pass. |
| 6.4 | Controlled MCP writes through exact DCLab command/approval services. | No MCP-specific mutation path; idempotency, approval, cancellation and ambiguous delivery are proved. |
| 6.5 | Protocol, client, abuse and operational release gate. | Pinned-client matrix, fuzz/security, rate/load and incident runbooks support go/no-go. |

### Scope 7 — connectors and ingestion

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 7.1 | Direct upload intents, quarantine, scan/classification and atomic DatasetVersion publication. | Duplicate/changing objects, malware stub fail-closed, worker loss, tenant lineage and download denial pass. |
| 7.2 | Connector config/control records and managed secret references in DCLab. | Rotation, cursor ownership, scope/tenant constraints and no plaintext secret in DB/job/log pass. |
| 7.3 | Restricted adapter/egress port and faithful fake provider. | SSRF, paging, cursor replay, throttling, schema error and cancellation contracts pass without live provider in CI. |
| 7.4 | Small production-MVP read-only connector pack behind pinned `dlt`. | S3/GCS/SQL/CRM/Snowflake source parity, bounded Arrow/Parquet staging, mapping and atomic publication pass staging canaries. |
| 7.5 | Scheduler, webhook, backpressure and reconciliation on durable DCLab jobs. | Duplicate/out-of-order webhook, provider rate, cursor crash, ambiguous result and pause/resume settle once. |
| 7.6 | Connector `/v1`, SDK, UI, agent reads and operations projection. | Same IDs/state across surfaces, secret-safe UI, freshness/quality states, two-workspace E2E and runbook gate. |

### Scope 8 — actions and outcomes

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 8.1 | Immutable recommendation versions linked to a decision case and evidence. | Deterministic facts, uncertainty/causal language and authorized audience projection pass. |
| 8.2 | Action proposal, exact approval and transactional outbox. | One approved payload produces at most one durable intent; changed/expired approval and crash races fail closed. |
| 8.3 | One allowlisted outbound provider action behind restricted adapter. | Sandbox canary, target validation, idempotent send, ambiguous delivery reconciliation and compensation pass. |
| 8.4 | Outcome/correction lineage and bounded impact attribution. | Immutable corrections, source provenance, counterfactual caution and no unsupported causal claim. |
| 8.5 | One API/SDK/CLI/UI/agent projection over decisions, actions and outcomes. | Capability/tenant parity, approval state, accessible review and no provider-native leakage. |
| 8.6 | Complete-loop pilot gate. | Proposal→approval→outbox→provider→outcome→correction replay, abuse and operator rollback pass. |

### Scope 9 — production release and portability

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 9.1 | One OpenTofu/Kubernetes environment contract with AWS and GCP modules. | Reviewed plans, workload identity, network isolation, drift detection and teardown parity pass. |
| 9.2 | Managed PostgreSQL/object/secret operations with backup and key rotation. | PITR/restore, digest/version parity, migration order, access logs and fail-closed secret handling pass both clouds. |
| 9.3 | API/web/worker deployment, autoscaling and job isolation. | Rollout/rollback, drain, lease recovery, quotas and runtime separation pass production-shaped staging. |
| 9.4 | SLOs, traces, metrics, logs, alerting and incident runbooks. | Low-cardinality safe telemetry, burn alerts, synthetic checks and recovery drills have named owners. |
| 9.5 | CI/CD, artifacts, provenance and environment promotion. | Exact-SHA build, SBOM/signing/scanning, migration gate, approvals and rollback prevent unreviewed deploy. |
| 9.6 | Security, privacy and incident readiness. | Threat model, penetration/abuse campaign, data retention/deletion and tabletop evidence support release. |
| 9.7 | Allowlisted pilot with explicit release decision. | Real-user workflow, quality/cost/SLO/privacy gates, support ownership and kill-switch rehearsal produce signed go/no-go. |
| 9.8 | AWS/GCP portability certification and release. | Same release/workload contracts pass independent restore/deploy/recovery on each home cloud; no active-active claim. |

### Scope 10 — measured scale and higher autonomy

| Plan | Required outcome and principal owner | Plan gate must prove |
| --- | --- | --- |
| 10.1 | Versioned workload/cost evidence and threshold registry. | Observed saturation, forecast assumptions and smallest-remediation ADR precede procurement. |
| 10.2 | PostgreSQL query/index/pool optimization before RLS/replica/partition. | Query plans, tenant correctness, lag/restore and online-migration proof justify each chosen change. |
| 10.3 | Worker/queue evolution only from measured bottleneck. | Handler fairness, idempotency, lease/cancel/recovery and cost parity survive scaling or broker change. |
| 10.4 | Retrieval improvement behind existing context/data ports. | Relevance, citation, tenant filtering, freshness, privacy, cost and clean rollback improve measured baseline. |
| 10.5 | Enterprise identity/regional controls without tenant-authority fork. | SSO/SCIM/revocation, residency, break-glass and audit parity pass both clouds. |
| 10.6 | Additional adapters/specialists/schedules only with demand and ownership. | New provider/runtime has isolated credentials, measured benefit, contract tests and independent disable. |
| 10.7 | Advanced model ops and controlled L3/L4 autonomy only after outcome evidence. | Policy simulation, exact approvals, scientific locks, bounded blast radius and independent human override pass. |

### RT1–RT8 supplemental runtime packages

The 44 `RT-*` prompts in `JUPYTER_RUNTIME_MVP_PROMPTS.md` inherit the same
execution card. Their owner and gate are: RT1 notebook content/version lineage;
RT2 session/quota/execution state; RT3 isolated Jupyter kernel/compute adapter;
RT4 manual API/SDK/web cells; RT5 reviewed agent proposals; RT6 training and
inference operations; RT7 provider-neutral placement/transfer/metering; RT8
Runpod, Railway, Lambda GPU Cloud, Vast.ai and Nebius adapters with separate
readiness, cost/egress and clean-disable evidence. AWS or GCP remains the one
home cloud per environment. An external compute provider is never the product
database, policy authority, object-store source of truth or default runtime.
