# Agent-first MLOps and data-integration architecture

**Status:** canonical architecture conclusion for the production MVP

**Reviewed:** 2026-09-13

**Product authority:** [`DCLAB_CORE_CONCEPT.md`](DCLAB_CORE_CONCEPT.md)

**Execution authority:**
[`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md) and
[`prompts/EXECUTION_STANDARD.md`](prompts/EXECUTION_STANDARD.md)

**Deployment authority:**
[`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md)

**External compute authority:**
[`EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md`](EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md)

This decision refines delivery order and component ownership. It does not
renumber completed plans, replace verified code, remove the existing business
product, or authorize an agent to bypass deterministic DCLab services.

## 1. Product conclusion

DCLab is a policy-enforced, agent-first operating system for ML development and
MLOps. Its primary users are data scientists and ML engineers, and its primary
unit of work is the connected, immutable ML lifecycle—not a chat, notebook,
agent run, source file, provider session or experiment-tracker object.

The autonomy direction is deliberate:

```text
production MVP: software-directed and agent-assisted
    -> agents observe, explain, investigate, propose and audit
    -> agents request bounded, typed, reversible commands
    -> agents execute one measured improvement loop inside explicit limits
target product: agent-directed and policy-enforced
    -> agents handle most supported workflow steps
    -> DCLab still authorizes, validates, executes, records and can stop/roll back
```

Agent-first never means LLM-authoritative. Even at higher autonomy, agents use
the same versioned services, capabilities, budgets, approvals, evidence,
idempotency, cancellation, reconciliation and rollback as UI, SDK, CLI and MCP
clients.

## 2. One owner per concern

| Concern | Canonical owner | Boundary |
| --- | --- | --- |
| ML lifecycle and project state | DCLab PostgreSQL domain/services | No agent, notebook, MLflow or connector state becomes lifecycle truth. |
| Scientific execution | Deterministic DCLab ML services and jobs | Agents propose or request; code-owned services validate and execute. |
| CPU/GPU placement | DCLab compute-placement policy over existing runtime, training and serving ports | AWS/GCP is the platform home; an external provider runs only an approved epoch, attempt or revision and never owns product state. |
| DCLab lifecycle supervision | Pinned raw LangGraph `StateGraph` | Sole authoritative DCLab supervisor graph/checkpoint runtime; separate whole-run adapters never become product-state authority. |
| Typed contracts | Ordinary Pydantic models | PydanticAI is optional only for a no-tool, single-response typed leaf after an ADR; it never owns tools, retries, sessions or checkpoints. |
| Open-ended investigation | Isolated Deep Investigation worker | Deep Agents runs separately and returns cited proposals; it never nests in or calls the raw graph. |
| Hosted provider agent runtime | Optional OpenAI Agents adapter | One `AgentRun` selects this runtime instead of LangGraph/Deep Agents; runtimes are never nested. |
| Typed semantic judgment | Optional TypeSafe Jev adapter behind DCLab `SemanticDecisionPort` | Atomic typed probabilities/confidence are advisory evidence; DCLab owns releases, calibration, thresholds and all authority. |
| Object-oriented proposal runtime | Optional isolated NVIDIA NOOA worker | One `AgentRun` selects a reviewed agent-class release; generated Python is hostile code and outputs are proposals only. |
| Model access | DCLab `ModelPort`/LLM gateway | Provider SDK objects and session state remain private adapter details. |
| Tool execution | DCLab `ToolRunner` and application services | Re-authorize, validate, budget, audit and reconcile every call. |
| Project memory | Immutable `ProjectDecisionRecord` | Chat, model and framework memory are evidence sources, never canonical memory. |
| Experiment telemetry/package metadata | MLflow behind DCLab ports | DCLab owns tenant, model, promotion, release and rollback state. |
| Data ingestion mechanics | `dlt` OSS behind DCLab connector ports | DCLab owns credentials, runs, checkpoints, schema policy and publication. |
| Bounded analytical scans | DuckDB + Arrow behind `DataScanPort` | Fresh ephemeral connection and code-owned query templates; no user/agent SQL. |
| Product control database | PostgreSQL | DuckDB, MLflow and Snowflake do not store DCLab product truth. |
| Dataset/model bodies | Private immutable object storage | PostgreSQL stores metadata, IDs, digests, lineage and safe summaries. |
| External warehouse | Snowflake connector | Supported production-MVP connector, optional per deployment; never a required DCLab infrastructure tier. |
| External agent access | DCLab hosted MCP facade | Same `/v1` capabilities and approvals; no alternate tool authority. |

## 3. Runtime-selection rule

Every durable agent run records one code-owned runtime kind and version:

```text
langgraph              predictable DCLab lifecycle supervision
deep_investigation     long-horizon cited investigation in a separate worker
openai_agents          explicitly allowlisted hosted/sandbox task
nooa_proposal          separately isolated object-oriented proposal task
```

One run has exactly one loop owner. A LangGraph node cannot start or resume a
Deep Agents, OpenAI Agents or NOOA loop. A Deep Agents/NOOA tool cannot invoke
another runtime. An OpenAI required action cannot invoke another agent runtime.
Cross-runtime work is a new, separately authorized DCLab request with its own
run, budget, citations and terminal state.

The authoritative LangGraph path performs at most one provider or tool operation
per durable turn, commits DCLab product state plus its private checkpoint, and
yields. Framework checkpoints are resumability data, not authorization or
public product state.

## 4. Agent roles and authority progression

The production MVP implements a bounded supervisor topology, not an all-to-all
agent swarm:

- Dataset Investigator;
- Problem and Experiment Planner;
- Leakage and Validation Critic;
- Experiment and Metric Critic;
- Artifact and Provenance Auditor;
- MLOps Release Auditor; and
- Operations/Drift Investigator.

Each specialist receives a code-owned input bundle and produces a typed,
citation-backed artifact. It has no peer-to-peer messaging, authority
amplification or direct provider/data-plane credential.

Capability promotion follows measured levels:

| Level | Agent authority | MVP use |
| --- | --- | --- |
| L0 | Observe and explain | Required |
| L1 | Propose and audit | Required |
| L2 | Simulate/preflight and request an exact command | Required |
| L3 | Execute an allowlisted reversible command with policy/approval | Narrow MVP subset |
| L4 | Run a bounded autonomous experiment loop | One measured MVP loop |
| L5 | Operate production workflows within standing policy | Target product; promote per capability after production evidence |

Higher autonomy changes who initiates a command, not who validates or executes
it. Promotion is per tool/capability and can be rolled back independently.

## 5. Deep Investigation worker

Deep Agents is an isolated implementation detail of one
`DeepInvestigationWorker` with three production-MVP modes:

1. dataset/schema/profile/lineage/leakage/validation investigation;
2. experiment/candidate/model/version/cost investigation; and
3. batch/drift/performance/latency/release investigation.

It uses read-only DCLab SDK/API tools, immutable resource versions and bounded
context. Every deterministic fact or cited observation must resolve to a
DCLab-owned resource/version/digest. Its result is a proposal, audit or
investigation report. Review may create a decision record or a new command
request; completion never executes one implicitly.

The MVP does not need Deep Agents subagents, persistent behavioral memory,
direct shell, arbitrary HTTP or host filesystem access. Breadth comes from the
three typed investigation modes and their tool bundles, not nested agents.

## 6. OpenAI integration

The default production path uses the official OpenAI SDK and Responses API
inside the provider-neutral DCLab gateway. Requests use strict structured
outputs, server-selected model/prompt releases, DCLab-enforced function-call
budgets, `parallel_tool_calls=false`, no built-in web/computer/shell/MCP tool in
the authoritative worker, and `store=false` when the approved retention policy
requires stateless processing. The API documents that stored Responses default
to retention when `store` is omitted, so the setting must be explicit.

The new OpenAI Agents API is a separate, allowlisted adapter because it owns an
OpenAI-hosted model/tool harness and managed session. It must not run inside
LangGraph. DCLab stores only a provider-neutral external-session reference,
usage, state reconciliation and result digest. OpenAI function
`required_actions` route through DCLab `ToolRunner`; the pending call itself is
never treated as authorization.

Initial allowed uses are isolated notebook/code investigation or a bounded
asynchronous audit where the hosted harness adds measured value. Use
`environment.type=none` for no-compute analysis and a purpose-built isolated
self-hosted/OpenAI-hosted sandbox only when code/files are required. Restrict
egress, keep product/provider credentials outside the environment and broker
third-party access. Disable built-in tools that bypass DCLab capability policy.

The adapter is beta-gated with pinned SDK/API versions, deterministic fakes,
record/replay tests, provider-session reconciliation, retention/deletion,
independent feature flag and kill switch. Failure or removal must leave the
authoritative LangGraph and deterministic ML paths healthy.

### 6.1 Optional Jev and NOOA optimizations

Jev is not an agent runtime. It may implement narrow `Choice`, `Score` or `Noul`
questions through DCLab's semantic-decision service after a purpose-specific
shadow gate. Store the immutable question/model/data-policy release, bounded
input digest, answer probabilities, confidence/abstention, latency/cost and
calibration evidence. Low-confidence, malformed or unavailable responses abstain
or follow the documented deterministic alternative; they never grant access,
approve work or replace scientific verification.

NOOA is not installed inside LangGraph, API or notebook-control processes. A
digest-pinned `worker-nooa` obtains an expiring DCLab run credential, reaches the
model only through the DCLab gateway and reaches product capabilities only
through reviewed typed facades. It has no provider/product-storage/cloud/Jupyter
credential. Its visible agent-class methods and strategy/limit policy are
versioned; CodeAct-generated Python is isolated as untrusted code. A successful
run stores a validated cited proposal for human/policy review, not an applied
change. The complete ownership, schema and release plan is
[`JEV_NOOA_INTEGRATION_ARCHITECTURE.md`](JEV_NOOA_INTEGRATION_ARCHITECTURE.md).

References reviewed for this decision:

- [OpenAI Agents API architecture](https://developers.openai.com/api/docs/guides/agents-api/architecture)
- [OpenAI Agents API function tools](https://developers.openai.com/api/docs/guides/agents-api/tools/functions)
- [OpenAI Agents API sandbox security](https://developers.openai.com/api/docs/guides/agents-api/environments/security)
- [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

## 7. Data-integration and data-engineering plane

DCLab adopts `dlt` OSS as the default embedded extraction/load library. It is a
replaceable engine behind the DCLab connector port, not another product or job
control plane:

```text
ConnectionDefinition + CredentialRef + SyncPlan
  -> DCLab ConnectorPort / ConnectorRunner
  -> bounded code-owned dlt source
  -> validated bounded Arrow batches
  -> immutable staged Parquet artifacts
  -> classification/schema/quality review
  -> atomic DatasetVersion publication + checkpoint
```

DCLab owns connection/config versions, tenant authorization, secret references,
sync intent/state, cursor contract, schedules, resource/API budgets,
idempotency, cancellation, retry, reconciliation, schema-drift policy,
quarantine, lineage, audit and publication. `dlt` pipeline state is an opaque
engine checkpoint tied to a DCLab SyncRun; it is not public or authoritative.

The production-MVP connector pack is:

1. CSV/Parquet direct upload;
2. AWS S3 and Google Cloud Storage object sources;
3. read-only PostgreSQL/general SQL extraction;
4. one CRM selected from HubSpot or Salesforce by pilot demand; and
5. read-only Snowflake extraction.

All sources are least-privilege and read-only in the MVP. Incremental polling,
bounded backfill, overlap/deduplication, schema discovery, incompatible-drift
pause/quarantine and immutable publication are required. CDC, reverse ETL and
arbitrary user/agent SQL are not initial capabilities.

Airbyte is not deployed inside the default DCLab stack because its control
plane would duplicate DCLab connection, job, credential and observability
authority. Keep an `ExternalReplicationPort` for a customer-owned Airbyte
installation when connector breadth justifies it. Snowflake Openflow is also an
enterprise interoperability option for an existing Snowflake customer, not a
DCLab runtime dependency or product database.

The object-source implementation and deployment storage port support both AWS
S3 and Google Cloud Storage with the same publication, digest, version,
authorization and recovery contract. Provider object IDs and signed URLs remain
private adapter details.

References reviewed for this decision:

- [`dlt` OSS and managed boundary](https://dlthub.com/docs/hub/getting-started/oss-and-dlthub)
- [`dlt` source catalog](https://dlthub.com/docs/dlt-ecosystem/verified-sources)
- [`dlt` cursor-based incremental loading](https://dlthub.com/docs/general-usage/incremental/cursor)
- [Airbyte documentation](https://docs.airbyte.com/)
- [Snowflake Openflow](https://docs.snowflake.com/en/user-guide/data-integration/openflow/about)
- [Snowflake Openflow connectors](https://docs.snowflake.com/en/user-guide/data-integration/openflow/connectors/about-openflow-connectors)

## 8. Notebook and hosted MCP boundaries

Arbitrary Python is permitted only inside an isolated, ephemeral, per-run
sandbox with pinned image/environment, unprivileged identity, CPU/memory/disk/
time/process/output limits, no product database access, disabled-by-default
egress and scoped artifact input/output. Source, environment, inputs, outputs,
usage and terminal reason are immutable and auditable. Notebook output does not
become a production feature/model/pipeline until imported through a separate
typed, reviewed, version-producing DCLab command.

DCLab's hosted MCP server is a thin public-SDK facade with tenant/audience-bound
identity, a versioned tool manifest, smaller-or-equal API bounds, read-only
defaults, exact approvals for selected writes, idempotency, audit, rate limits
and independent read/write kill switches. Internal agents call application
services through `ToolRunner`; they do not loop back through hosted MCP. An
OpenAI Agents session may reach only the DCLab-hosted MCP facade or equivalent
function-tool gateway when explicitly allowed; it cannot attach arbitrary
remote MCP servers.

## 9. Initial model understanding without SHAP

SHAP is not part of the production MVP. Do not install it, compute SHAP values,
add a SHAP job/API/UI, or imply that an existing enum/placeholder is supported.
Initial model understanding uses deterministic metrics, confusion and
calibration results, threshold trade-offs, residual/error slices, data/feature
statistics and clearly labeled native or permutation importance where valid.
Any future SHAP adoption requires a separate measured ADR covering supported
model families, resource/privacy limits, semantics, caching and user value.

## 10. Minimal delivery-order correction

Keep Scope 0–10 IDs and completed evidence. Use this cross-scope MVP order:

1. finish the secure Scope 0 foundation and bounded DataScan;
2. establish project lifecycle, decision memory and authority contracts;
3. implement direct upload plus connector control/adapter foundations and the
   initial `dlt` connector pack;
4. complete the deterministic data-scientist golden path;
5. add MLflow-backed tracking/package metadata and the canonical model build;
6. release LangGraph specialists in read-only, proposal and audit modes;
7. release all three Deep Investigation modes and evaluate the OpenAI Agents,
   Jev and NOOA adapters behind independent gates without nesting runtimes;
8. add supervised reversible tools and one bounded improvement loop;
9. release isolated Python and hosted MCP through their security gates;
10. release model registration, batch prediction, drift investigation and
    rollback;
11. prove SDK/CLI automation and the complete connector pack; and
12. run the production pilot before graduating additional L3–L5 authority.

The connector, Deep Investigation, hosted MCP, isolated Python and bounded
multi-agent work are production-MVP workstreams. They may progress in parallel
after their dependencies, but all applicable release gates must pass before
the production-MVP go/no-go. The deterministic golden path remains independently
usable while those workstreams are disabled or under repair.

## 11. Production-MVP acceptance

The release is not complete until evidence proves:

- the deterministic end-to-end ML path works with every agent runtime disabled;
- specialists and Deep Investigation improve measured user work without
  overriding deterministic findings;
- exactly one runtime owns each AgentRun and no cross-runtime invocation exists;
- the OpenAI adapter can be disabled without affecting core behavior;
- Jev and NOOA have recorded allow/disable/reject decisions; any enabled purpose
  is advisory/proposal-only and both can be cleanly disabled without affecting
  the deterministic or authoritative LangGraph paths;
- the connector pack publishes immutable DatasetVersions without secrets,
  duplicate/lost rows within documented source semantics, or silent schema
  drift;
- isolated Python cannot access another tenant, product databases, host paths,
  unrestricted network or unapproved artifacts;
- hosted MCP never has more authority than the same `/v1` principal;
- identical signed releases pass the AWS and GCP deployment contracts and
  controlled bidirectional restore without exposing cloud details to agents;
- every enabled external compute target has scoped capability, data-transfer,
  cost, cleanup and home-transport evidence for the advertised execution lane;
- the model package, batch prediction, monitoring and rollback path is
  reproducible and policy-enforced;
- no initial feature depends on SHAP; and
- one data scientist and one ML engineer complete the supported journey through
  UI and SDK/CLI without database intervention.
