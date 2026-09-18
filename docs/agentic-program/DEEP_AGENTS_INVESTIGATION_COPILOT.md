# Deep Agents multi-mode Investigation architecture

**Status:** approved production-MVP workstream; implementation may begin at
S2-P12A after S2-P11F in parallel with Scope 3, and its release gate is required
before the production-MVP go/no-go

**Product authority:** [`DCLAB_CORE_CONCEPT.md`](DCLAB_CORE_CONCEPT.md)

**Implementation authority:**
[`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md) and
[`prompts/SCOPE_02_AGENTIC_OPERATING_SYSTEM.md`](prompts/SCOPE_02_AGENTIC_OPERATING_SYSTEM.md)

**Deployment authority:**
[`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md)

## 1. Decision

DCLab may use the LangChain `deepagents` package for one bounded feature named
**Deep Investigation**. It is a separately deployed, non-authoritative agent
harness for long-horizon, read-oriented investigation, audit and proposal drafting.
It is not DCLab's workflow engine, scientific authority, project memory, command
runner, supervisor implementation or deployment controller.

The canonical DCLab agent operating system continues to use a pinned, raw
LangGraph `StateGraph` with code-owned topology. Deep Agents is itself an
opinionated harness over LangChain `create_agent` and the LangGraph runtime, so
it must live in a different package, process, dependency lock, handler allowlist
and runtime-state namespace. Neither runtime may be embedded in or invoked as a
node, tool or subagent of the other.

The two workers pin and upgrade their LangGraph-related dependency graphs
independently. Their compatibility contract is the versioned DCLab HTTP/worker
transport and product schemas—not a shared Python ABI or synchronized package
version. A Deep Agents requirement must never force an upgrade of the
authoritative `worker-agent` runtime.

The same signed `worker-investigation` image and DCLab HTTP/worker contract run
on EKS and GKE. Cloud workload identity, networking, telemetry and short-lived
runtime storage are deployment adapters; no AWS/GCP SDK, account/project ID,
bucket locator or cluster API becomes model-visible or product state.

Official architecture references:

- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents architecture](https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md)
- [Deep Agents customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Deep Agents backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents)

## 2. Product responsibility

The production-MVP release serves three code-owned modes:

- `dataset_scientific` for dataset, schema, profile, lineage, leakage and
  validation investigation;
- `experiment_model` for experiment, candidate, metric, cost and model-version
  comparison; and
- `operations_drift` for batch, monitoring, drift, performance, latency,
  release and rollback investigation after Scope 3.8.

Within those modes it helps a data scientist or ML engineer:

- understand an unfamiliar dataset from authorized metadata and profiles;
- examine schemas, quality, lifecycle lineage and immutable versions;
- surface deterministic leakage/validation findings and label additional
  suspicions as unverified hypotheses;
- inspect validation strategy and applicable business/scientific constraints;
- compare experiments, candidates, selected models and their costs;
- after Scope 3.8, investigate input/prediction/performance drift, batch failure,
  latency/resource regression or another supported operational regression;
- draft a cited experiment, investigation, remediation, retraining or rollback
  proposal; and
- state alternatives, confidence and unresolved questions without inventing
  evidence.

It does not replace Dataset Steward, scientific critics or the Scope 2
supervisor. Those predictable product workflows produce typed findings through
the authoritative raw LangGraph runtime. Deep Investigation is an explicitly
requested exploratory synthesis over existing DCLab resources and findings.
When a deterministic query or existing code-owned graph is sufficient, DCLab
uses that path instead.

## 3. Non-conflicting topology

```text
User / API / SDK
  -> DCLab InvestigationService
  -> ExecutionRequest + MlJob(investigation.copilot.v1)
  -> immutable InvestigationContextBundle ID/version/digest
  -> API-side InvestigationWorkerLeaseService
  -> worker-investigation (Deep Agents; separate process/image)
       -> DCLab LLM gateway adapter
       -> allowlisted read-only DCLab SDK/API tools
       -> isolated job-scoped working state
  -> validated InvestigationProposal
  -> DCLab AgentProposal/AgentRun/Event/Citation projections
  -> authorized user review
  -> ProjectDecisionRecord
  -> optional canonical command executed by DCLab services
```

The following interactions are forbidden:

```text
raw LangGraph node -> Deep Agent loop
Deep Agent tool -> raw LangGraph supervisor or agent.turn.v1
Deep Agent -> SQLAlchemy session, product PostgreSQL or object-store credential
Deep Agent -> DuckDB connection, SQL/expression, artifact path or Arrow reader
Deep Agent -> ToolRunner mutation, ApprovalService bypass or worker handler name
Deep Agent memory/checkpoint -> ProjectDecisionRecord or lifecycle truth
Deep Agent proposal -> automatic training, registration, promotion or rollback
```

The DCLab API creates the job from an explicit authorized user/SDK request and
retains the PostgreSQL queue connection. A non-public, workload-authenticated
worker transport claims/leases the job, heartbeats and submits terminal output
through DCLab application services; `worker-investigation` never opens product
PostgreSQL. Framework code does not schedule the other framework. The worker's
lease, gateway and completion clients are unavailable to the model. Its final
result is validated and completed by deterministic application code, not by a
model-visible completion tool.

`InvestigationRun` is the purpose-specific `/v1` projection of the same DCLab
`AgentRun` ID, state and events—not a second run table or identity. Likewise,
`InvestigationProposal` is the typed structured body of the existing
`AgentProposal` owner unless execution-time schema analysis proves an additive
gap.

Before the worker starts, DCLab creates an immutable, audience-safe
`InvestigationContextBundle` from authorized product services. It contains the
bounded schema/profile summary, target and prediction-moment contract,
deterministic leakage/validation findings, lineage references, experiment/model
comparisons, costs, applicable decisions and—when available—batch/drift/latency
summaries. It contains resource IDs, versions, freshness and digests, not raw
rows. The request pins its ID/version/digest. Tools may fetch a bounded cited
slice or refresh only by creating a new bundle/version after re-authorization;
the worker cannot assemble a hidden alternative snapshot.
Fresh profiles and aggregate slices are prepared by deterministic DCLab
services through `Authorized DatasetArtifact → DataScanPort → ephemeral DuckDB
→ bounded Arrow batches`. The context builder receives only normalized DCLab
results. It never forwards SQL, object paths, DuckDB/Arrow objects or scan
configuration to Deep Agents, and Polars is not a worker dependency.

## 4. First-release input and output

The versioned request contains IDs and bounded policy, never raw rows, secrets,
provider credentials or arbitrary instructions that widen authority:

```json
{
  "schema_version": "investigation-request.v1",
  "tenant_id": "tenant-123",
  "project_id": "project-456",
  "objective_version": 4,
  "context_bundle_id": "context-bundle-789",
  "context_bundle_version": 1,
  "context_bundle_digest": "sha256:...",
  "dataset_version_ids": ["dataset-version-18"],
  "allowed_operations": [
    "inspect_dataset",
    "inspect_lineage",
    "compare_experiments"
  ],
  "max_iterations": 12,
  "max_model_calls": 12,
  "max_tool_calls": 30,
  "max_tokens": 60000,
  "max_cost_microunits": 2500000,
  "deadline_seconds": 300
}
```

The ordinary Pydantic output contract uses claim-level evidence rather than one
proposal-level citation bucket and is equivalent to:

```python
class InvestigationClaim(BaseModel):
    claim_id: str
    claim_type: Literal[
        "deterministic_fact", "cited_observation", "hypothesis",
        "recommendation", "unknown"
    ]
    text: str
    evidence_citations: list[ResourceCitation]
    confidence: float | None

class InvestigationProposal(BaseModel):
    schema_version: Literal["investigation-proposal.v1"]
    summary: str
    claims: list[InvestigationClaim]
    leakage_risks: list[LeakageFinding]
    validation_findings: list[ValidationFinding]
    recommended_actions: list[ProposedAction]
    alternatives_considered: list[Alternative]
    confidence: float
    unresolved_questions: list[str]
```

Every assertion is classified as deterministic fact, cited observation,
hypothesis or recommendation. Missing or unauthorized evidence cannot be
replaced with model recollection. Deterministic validation rejects any fact
without a supporting current resource citation and any recommendation without
claim IDs that establish its evidence and assumptions. Invalid citations,
unknown resource versions, unbounded output, unsupported actions or schema
failure produce a safe terminal result or bounded retry; they never create a
domain proposal.
Every referenced dataset, schema/profile, feature contract, experiment, metric,
artifact, model version, release, batch run, monitoring window and project
decision carries its DCLab resource ID, version/digest and evidence citation.

## 5. Tool and authority boundary

The model's external/product-facing tool surface contains only versioned,
allowlisted DCLab SDK/API reads such as:

```text
inspect_dataset_version       get_dataset_profile
get_dataset_aggregate_slice
get_investigation_context_bundle
get_feature_contract          get_lineage_subgraph
get_project_constraints       search_project_decisions
compare_experiment_runs       inspect_model_package
get_batch_run_metrics         get_drift_report
```

Each tool:

- calls an application API rather than importing private services;
- uses a short-lived service identity narrowed to the initiating workspace,
  project, purpose, resources and read scopes;
- is re-authorized by DCLab on every call;
- accepts immutable IDs/versions and strict ordinary Pydantic schemas;
- returns bounded, audience-safe data with resource citations;
- applies pagination, timeout and response-size limits;
- is idempotent and read-only in the first release; and
- emits safe audit, trace, usage and cost events.

`get_dataset_aggregate_slice` accepts only the public DCLab typed grammar:
immutable dataset version/digest, registered operation/template version,
allowlisted columns/group/filter/aggregate enums and hard response/time bounds.
It cannot accept SQL, arbitrary expressions, paths, URLs, DuckDB settings,
extension names or raw-row output. The API re-authorizes and calls DataScanPort;
`worker-investigation` does not import DuckDB, PyArrow or pandas for scanning.

Tool output separates trusted DCLab metadata/policy fields from untrusted user,
dataset, label and artifact text. Column names, values, descriptions, experiment
names and previous agent output are never interpolated into system instructions.
The versioned capability manifest records every custom tool name/schema,
purpose, permitted resource types, response-byte limit and rollout state. The
Deep Agents filesystem-tool allowlist and DCLab custom-tool manifest are
independent controls and both deny by default.

Deep Agents may retain its built-in planning/todo capability and virtual
filesystem operations only when they are bound to the job-scoped `StateBackend`.
Those operations may organize already-authorized context inside the current
attempt, but cannot resolve a host path, network URL, DCLab resource or durable
memory namespace. Their content expires with the working-state retention policy.
The `task` delegation tool and `execute` tool are absent.

The worker has a non-model-visible operational client for claim/lease heartbeat,
cancellation polling, DCLab gateway calls and validated terminal completion.
These endpoints are private workload transports, are not tools, are not exposed
at public ingress and contain no general product query or command method. The
worker cannot create decision records, apply proposals, request approvals or
invoke commands.

## 6. State, memory and runtime isolation

Deep Agent working state is limited to messages, plan/todos, intermediate notes,
temporary files and context summaries for one investigation attempt. It is not
searchable project memory and has a short, documented retention period.

DCLab remains authoritative for users, projects, lifecycle resources,
scientific constraints, AgentRun/Step/Event/Citation/Proposal projections,
approvals, `ProjectDecisionRecord` and audit. A reviewed proposal creates an
accepted/rejected/superseded decision through DCLab services; the framework
cannot promote memory itself.

If restartable Deep Agent state is required after measured testing, it uses a
dedicated least-privilege runtime schema/store owned by `worker-investigation`,
with separate migrations, IDs, encryption, retention and deletion. It never
shares the raw LangGraph `worker-agent` checkpointer schema or exposes either
checkpoint format through `/v1`.

The production-MVP release does not require durable Deep Agents checkpoints. A lost or
failed attempt restarts from the immutable request and context-bundle digest
after re-authorization, with a new numbered attempt and bounded retry count.
Persistent runtime state is introduced only when observed task duration and
restart cost demonstrate that this simpler recovery policy is insufficient.

## 7. Model, subagent and code-execution policy

- All model calls pass through a LangChain-compatible adapter over the DCLab
  provider-neutral LLM gateway. Deep Agents receives no provider credential.
- The production-MVP release disables the general-purpose subagent and configures no
  custom or asynchronous subagents. Maximum subagent depth is therefore zero.
- Startup inspects the final model-visible tool surface and fails closed if
  `task`, `execute`, a mutation tool or an unregistered custom tool is present.
- Persistent Deep Agents memory and skills that can mutate runtime behavior are
  disabled. Reviewed static instructions are versioned with the
  DCLab agent/prompt release.
- Planning/todo and virtual file operations are limited to the current
  `StateBackend`; they have no host, object-store, product or durable-memory path.
- `FilesystemBackend` and `LocalShellBackend` are forbidden in production.
- The production-MVP release has no model-visible shell, Python, package installation,
  arbitrary HTTP or local filesystem tool.
- If a separately approved use requires code execution, it must use the Scope 4
  remote disposable sandbox control plane with a fresh sandbox per run, immutable
  image, no credentials, denied network by default, resource/time/output quotas,
  artifact scanning and TTL cleanup. The sandbox remains a tool target, not an
  agent or orchestration runtime.
- Any fixed internal specialist/subagent requires a separate measured ADR,
  dedicated typed output, narrower tools/budget, depth/fan-out limits and a new
  threat/evaluation gate. Recursive or dynamically invented roles remain
  prohibited.

## 8. Budgets, cancellation and recovery

The request binds maximum model calls, tool calls, tokens, cost, time, context
bytes, tool-result bytes and concurrent investigations. DCLab reserves and
settles budgets around the worker attempt. `budget_exhausted`, `deadline_exceeded`,
`cancelled`, `policy_revoked`, `source_stale` and `dependency_unavailable` are
explicit terminal reason codes.

Cancellation is cooperative but enforced at the job lease, model gateway and
tool API boundaries. A lost worker never causes an unbounded internal retry:
DCLab closes or starts a new numbered attempt from the immutable request after
re-authorizing inputs. Same idempotency key and request digest resolves to the
same InvestigationRun; a changed digest conflicts.

## 9. Release and evaluation policy

Rollout order is:

1. deterministic fake-model and fake-tool contract tests;
2. offline synthetic/de-identified historical replay;
3. shadow comparison against deterministic queries and the Scope 2 supervisor;
4. allowlisted proposal-only dataset/scientific mode with no shell, memory or
   subagents;
5. experiment/model and Scope 3.8 operations/drift read bundles after their own
   source-resource gates;
6. production-shaped isolated deployment of all three modes and pilot in Scope
   9; and
7. a sandbox or fixed subagent only after separate measured evidence and an
   independent security/evaluation gate.

Hard evaluation assertions cover tenant isolation, citations, deterministic-
fact precedence, leakage/holdout safety, unsupported claims, tool selection and
arguments, prompt injection, exfiltration, stale sources, malformed output,
cost/time bounds, cancellation, restart, dependency failure and kill switches.
One security, tenancy or scientific-authority violation fails promotion even
when average answer quality improves.

Required metrics include proposal usefulness, citation precision/recall,
unsupported-claim rate, deterministic-finding coverage, reviewer acceptance and
revision rates, false leakage-warning rate, abstention quality, time saved,
time to useful investigation, cost per accepted proposal, model/tool calls,
tokens, cost, cancellation latency and terminal-reason counts. Deep Agents and its transitive
LangChain/LangGraph dependencies are pinned, locked, SBOM-scanned and upgraded
only through compatibility, replay and rollback evidence.

If shadow/canary evidence does not show material incremental value over
deterministic reports and the authoritative raw-LangGraph workflow, keep the
feature disabled and remove its deployed runtime dependency without affecting
the Core ML release.

## 10. Non-goals

The Investigation Copilot does not:

- replace the raw LangGraph supervisor or any code-owned specialist;
- add a general autonomous assistant to every product request;
- own authorization, product state, checkpoints, scientific truth or memory;
- train, register, promote, deploy, roll back or call an external provider;
- access raw customer rows by default or retain hidden reasoning;
- submit SQL, open artifact paths, configure/use DuckDB directly, install
  extensions, access the network through a scan, or introduce Polars;
- execute on the API/agent/ML worker host;
- introduce PydanticAI, `pydantic-graph` or a third orchestration system; or
- make Deep Agents a dependency of the DCLab API, public SDK, core domain or
  canonical `worker-agent` image.
