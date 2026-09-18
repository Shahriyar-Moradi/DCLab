# DCLab core product concept

**Status:** canonical product north star

**Primary users:** data scientists and ML engineers

**Relationship to the implementation roadmap:** this document defines what
DCLab must become and how product decisions are evaluated. The
[`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md) remains the
ordered implementation program. Neither document authorizes bypassing verified
code, tests, security controls or scope gates.

**Canonical operating architecture:**
[`AGENT_FIRST_MVP_ARCHITECTURE.md`](AGENT_FIRST_MVP_ARCHITECTURE.md) defines
the agreed agent-runtime, data-integration, notebook, MCP and autonomy
boundaries used to deliver this product concept.

## 1. Product thesis

DCLab is the **project-centric operating environment for the machine-learning
lifecycle**. A useful shorthand is “Cursor for machine learning,” with one
essential distinction: the primary unit of work is not a source file or
notebook cell. It is the connected, versioned ML lifecycle.

DCLab begins software-directed and agent-assisted, then graduates capability by
capability toward an agent-directed, policy-enforced product. The end goal is
for agents to operate most supported ML and MLOps workflow steps while DCLab
continues to authorize, validate, execute, record, stop and roll back them.

DCLab helps a user understand, propose, execute and review changes to datasets,
schemas, preparation, features, splits, experiment plans, candidates,
hyperparameters, metrics, artifacts, model releases, batch predictions and
monitoring configuration while preserving their dependencies and evidence.

Code remains inspectable and exportable, but it is not the only or primary
product model. The deterministic DCLab services—not an LLM and not a notebook
kernel—remain authoritative for scientific validation, execution, lineage,
authorization and side effects.

## 2. First-user jobs

### Data scientist MVP jobs

A data scientist must be able to:

- understand an unfamiliar dataset and its quality, classification and drift;
- define the target, entity, prediction moment, label window, business objective
  and constraints;
- detect leakage, invalid validation, imbalance and unsuitable metrics;
- generate, inspect and revise a reproducible experiment plan;
- run and compare bounded model candidates and improvement attempts;
- inspect feature transformations, exclusions, metrics and uncertainty;
- accept, reject, compare, modify or supersede a proposal with recorded reasons;
- reproduce and export a selected result with its code/environment lineage.

### ML engineer MVP jobs

An ML engineer must be able to:

- automate the supported lifecycle through the Python SDK and CLI;
- inspect data, feature, code, environment, job and artifact lineage;
- monitor jobs, resource use, cost, failures and recovery;
- register and promote a verified model package safely;
- run bounded batch inference against an authorized dataset version;
- detect input, prediction and—with labels—performance drift;
- inspect agent-generated investigation proposals and roll back a release.

These personas are release authorities. A scope is not product-complete merely
because its infrastructure works; the applicable user job must pass through a
supported UI or developer interface without database intervention.

Dataset understanding is fast because DCLab can scan approved Parquet/Arrow
artifacts without loading an entire frame, not because it exposes a database.
Every profile, aggregate slice, leakage-candidate statistic and drift-window
preparation follows `Authorized DatasetArtifact → DCLab DataScanPort →
ephemeral DuckDB → bounded Arrow batches → deterministic DCLab services`.
Users and agents select typed DCLab operations; they never submit SQL.

## 3. The canonical ML lifecycle

The product exposes one typed, immutable, project-scoped lifecycle projection:

```text
Project
  -> DatasetVersion
  -> Schema/Profile
  -> ProblemSpec
  -> PreparationPlan
  -> FeatureSetVersion
  -> Split/ValidationPlan
  -> ExperimentPlan
  -> CandidateRuns
  -> Evaluation/Selection
  -> ModelVersion
  -> ModelRelease
  -> BatchPredictionRun
  -> MonitoringWindow
  -> Investigation/RetrainingProposal
```

This graph is a domain projection over existing authoritative resources and
their version/digest relationships. It is not the LangGraph execution graph and
not a generic user-editable graph database. Existing foreign keys and lineage
records are reused. A dedicated lifecycle-link record is added only when an
important relationship cannot be derived or enforced from an existing owner.

Every lifecycle node exposes, as applicable:

- stable resource type, ID, version, digest and workspace/project lineage;
- state, freshness, verification and staleness reason;
- parents, children and deterministic impact of a proposed change;
- producing run, environment, code and artifact references;
- relevant metrics, cost and bounded audience-safe status;
- associated decisions, proposals, approvals and citations.

Changing an upstream version creates a new version and invalidates or marks
affected downstream nodes stale according to deterministic rules. It never
silently rewrites historical results.

## 4. Three synchronized product views

Every important project resource is reachable through three synchronized views
that use the same IDs, versions, permissions and server state:

1. **Conversation and actions:** ask, investigate, propose, approve, run,
   compare, cancel and explain.
2. **ML workflow:** inspect the lifecycle graph, versions, dependencies,
   findings, experiment branches, selected model and operational status.
3. **Implementation and infrastructure:** inspect the generated/reused Python,
   SQL or configuration representation, environment/image, artifacts, jobs,
   logs and deployment evidence when authorized.

Agent Studio, the agent operations view and notebooks may remain separate
routes or components. “Synchronized” means they resolve to the same canonical
project/lifecycle resources and deep-link to one another; it does not require a
single monolithic page.

The implementation view is read-only by default. Any accepted edit becomes a
typed proposal or command, creates a new immutable version and passes the same
scientific, authorization and approval services as every other client.

## 5. Durable project decision memory

Agent messages and runtime checkpoints are not project memory. DCLab stores
important decisions as immutable, searchable `ProjectDecisionRecord` resources.

A decision record includes:

- workspace/project and decision type;
- state: `proposed`, `accepted`, `rejected` or `superseded`;
- subject resource type, ID, version and digest;
- actor type/ID and source agent/proposal/tool versions where applicable;
- bounded rationale separated from observed facts and hypotheses;
- evidence citations and alternatives considered;
- business and scientific objectives, constraints and trade-offs;
- resulting resource/version links;
- policy version, event time and recorded time;
- supersession link and safe reason.

Examples include why recall was preferred to accuracy, why a leakage candidate
was excluded, why one candidate was selected, why an experiment was rejected,
why a model version was replaced or why a release was rolled back.

Decision memory follows these rules:

- the record never grants authority and is re-authorized when read;
- LLM-generated rationale is untrusted and labeled until reviewed;
- acceptance does not itself execute an ML change;
- a material correction creates a superseding record;
- retrieval is project- and purpose-scoped, bounded and citation-backed;
- hidden chain-of-thought, secrets, raw rows and unrestricted logs are excluded;
- vector retrieval, if later justified, is an index—not the source of truth.

## 6. Core ML production-MVP release slice

This slice is a product acceptance path across existing scopes, not a parallel
implementation or a replacement numbering system. Existing plan dependencies
and hard gates remain in force.

The supported journey is:

1. secure foundation, tenant/data controls and bounded analytical scanning;
2. first-class immutable ML lifecycle projection and durable decision memory;
3. a reusable data-integration plane with upload, object-store, SQL, CRM and
   Snowflake read-only sources publishing immutable DatasetVersions;
4. goal, prediction semantics, business constraint and metric contract;
5. deterministic dataset investigation and leakage/validation findings;
6. reviewable experiment proposal and canonical approved model-build command;
7. a bounded baseline and candidate portfolio;
8. experiment comparison, explanation, cost and reproducibility;
9. read-only/proposal/audit specialists plus the three isolated Deep
   Investigation modes;
10. one bounded autonomous improvement loop;
11. synchronized conversation/workflow/implementation views;
12. Python SDK and CLI automation for the supported path;
13. isolated Python and hosted MCP through their security gates;
14. immutable model registration and one batch-prediction release path;
15. initial input/prediction/performance-drift monitoring and rollback; and
16. an allowlisted production pilot with data scientists and ML engineers.

The initial deployment capability is deliberately narrow: verified model
package plus environment and feature contract, an immutable release, authorized
batch inference, monitoring windows, agent-generated investigation proposals
and rollback. Online REST serving, streaming, scheduled/edge inference,
multi-cloud provisioning and autonomous retraining remain later measured
capabilities.

### ML platform reuse foundation

DCLab does not rebuild commodity columnar analytics, experiment telemetry,
dataframe-schema execution, statistical drift calculations or observability
export. It follows
[`ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`](ML_PLATFORM_INTEGRATION_ARCHITECTURE.md):

- MLflow is the single production-MVP experiment tracker and model-package
  metadata service; DCLab remains the only lifecycle, registry, approval,
  promotion, rollback, tenancy and decision authority;
- a DCLab-owned `DataScanPort` uses a fresh resource-bounded DuckDB connection
  and code-owned templates to read an authorized, digest-verified artifact;
  bounded Arrow batches feed deterministic DCLab profiling, slice, leakage-
  candidate and drift-window services, while pandas remains the modeling
  compatibility layer;
- Pandera executes strict schemas compiled from DCLab-owned versioned feature
  contracts rather than defining a competing contract;
- Evidently calculates bounded reference/current reports while DCLab owns
  monitoring windows, thresholds, alerts, investigations and remediation state;
- skops or reviewed native model formats replace unsafe general-purpose model
  deserialization where supported;
- OpenTelemetry exports redacted operational traces and metrics; it does not
  replace product audit or scientific evidence; and
- `dlt` OSS supplies bounded extraction/incremental-loading mechanics behind a
  DCLab-owned ConnectorPort while DCLab owns authorization, credentials, sync
  state, schema policy, checkpoints, publication and audit;
- Polars, W&B, Optuna, OpenLineage, feature stores and distributed ML platforms remain
  deferred until customer or measured scale evidence justifies them.

Snowflake is an optional read-only source/destination adapter, not a DCLab
control database or default runtime dependency. Airbyte and Snowflake Openflow
may interoperate through an external replication boundary for customers that
already operate them; neither becomes DCLab's connector control plane.

SHAP is explicitly excluded from the production MVP. Initial model
understanding uses deterministic metrics, calibration/confusion results,
threshold trade-offs, residual/error slices, feature statistics and clearly
labeled native or permutation importance where scientifically valid.

External providers are reached only through narrow DCLab adapters. Their IDs,
objects, tags, permissions, UI state and memory never cross the public product
boundary or become authority. A missing tracking record blocks verification or
promotion but cannot corrupt the canonical DCLab run.

AWS and Google Cloud are equal supported deployment targets under
[`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md).
The ML lifecycle, identifiers, scientific evidence and user experience are
identical. Provider-specific compute, PostgreSQL, object storage, secrets, KMS,
network and telemetry live behind private adapters and infrastructure modules.
One installation uses one provider at a time; the MVP proves controlled cross-
cloud restore rather than active-active dual-cloud writes.

## 7. Autonomous investigation and improvement

DCLab owns a bounded, durable loop:

```text
objective and constraints
  -> deterministic investigation
  -> evidence-backed hypothesis
  -> typed experiment change
  -> scientific validation and budget reservation
  -> approved execution
  -> comparison and decision record
  -> stop, ask, accept or propose one next attempt
```

The loop must investigate applicable checks such as missingness, duplicates,
imbalance, leakage, split contamination, temporal/group structure, feature
shift, calibration, overfitting and subgroup performance. Deterministic checks
produce facts; agents prioritize, explain and propose. The loop stops on target
satisfaction, budget/depth/time limits, insufficient improvement, instability,
scientific risk, user cancellation or policy revocation. Holdout evidence is
never reused for tuning.

### Deep Investigation

DCLab provides one explicitly requested, proposal-only **Deep Investigation
worker** with three modes: dataset/leakage/validation, experiment/model/cost and
batch/drift/performance/release investigation. It drafts a typed investigation,
experiment or remediation proposal with resource/version citations,
alternatives, confidence and unresolved questions.

The approved Deep Agents integration is defined in
[`DEEP_AGENTS_INVESTIGATION_COPILOT.md`](DEEP_AGENTS_INVESTIGATION_COPILOT.md).
It runs in a separate `worker-investigation` process as a bounded consumer of
read-only DCLab SDK/API tools. It is not embedded in the authoritative raw
LangGraph graph, cannot call that graph, and cannot be called by one of its
nodes or specialists. It has no database, object-store, provider-secret, host-
filesystem, shell, command, approval or deployment authority. DCLab validates
its structured result; an authorized review may then create a
`ProjectDecisionRecord` and a separate canonical command.

Deep Agent messages, todos, temporary files, summaries, checkpoints and memory
remain short-lived working state. They never become lifecycle truth or durable
project memory. The first release disables Deep Agents subagents, persistent
memory and direct code execution. Breadth comes from typed modes and versioned
read-tool bundles, not nested agents. Plan 2.12 may progress in parallel after
the authoritative S2-P11F gate and never blocks deterministic development, but
its three-mode release gate is required for the production-MVP go/no-go.

## 8. Conflict-resolution rules

| Potential conflict | Required resolution |
| --- | --- |
| ML lifecycle graph vs LangGraph | The lifecycle graph is authoritative product lineage; LangGraph is private execution routing only. |
| Project memory vs agent checkpoint/message history | `ProjectDecisionRecord` is curated product memory; checkpoints only resume work and messages remain conversation history. |
| Project-centric product vs notebooks | The lifecycle is canonical; notebooks are synchronized investigation and implementation views. |
| Code-hidden UX vs developer control | Complexity may be summarized, but implementation, environment and evidence remain inspectable by authorized users. |
| Multi-agent breadth vs first-user value | Keep the full multi-agent roadmap, but a specialist is promoted only when evaluation proves value; the core release is judged by the end-to-end ML journey. |
| Raw LangGraph operating system vs Deep Agents | Raw LangGraph remains DCLab's sole authoritative lifecycle-supervisor graph. Deep Agents owns only a separately requested, proposal-only InvestigationRun; neither runtime invokes, embeds, checkpoints or authorizes the other, and neither replaces DCLab product-state authority. |
| Scope 2 specialists vs Deep Investigation | Specialists run predictable code-owned lifecycle workflows. The investigation worker performs exploratory synthesis over persisted findings and authorized resources; it does not reimplement or delegate to them. |
| OpenAI Agents API vs LangGraph/Deep Agents | Each AgentRun selects exactly one runtime. The OpenAI-hosted harness is a replaceable beta adapter for allowlisted sandbox/audit work and never runs inside or calls another agent loop. |
| PydanticAI vs authoritative orchestration | Ordinary Pydantic is the default contract layer. PydanticAI may be approved only as a no-tool, single-response typed leaf and cannot own tools, retries, sessions, checkpoints or routing. |
| DCLab lifecycle vs MLflow | MLflow records detailed run telemetry and package metadata through a private adapter. DCLab remains authoritative for project/run identity, scientific summaries, model versions, approval, promotion, rollback, tenancy and audit. |
| MLflow vs W&B | MLflow is the single MVP tracker. W&B is absent from the core deployment and may later be a one-way customer connector, never a second tracker for the same run. |
| DCLab/PostgreSQL vs DuckDB | PostgreSQL and object storage remain product truth. DuckDB is a per-operation in-memory scan adapter with no durable catalog, migrations, authority, network access or public surface. |
| DCLab ConnectorPort vs `dlt` | DCLab owns connector definitions, secrets, jobs, schema policy, cursor contract and DatasetVersion publication. `dlt` is a bounded extraction/loading engine with opaque per-run state. |
| DCLab vs Airbyte/Snowflake Openflow | Customer-operated replication may feed an authorized DCLab source, but it never becomes DCLab job, credential, lifecycle or publication authority. |
| Agents/notebooks vs DuckDB | Agents and managed query cells call typed DCLab scan operations over authorized artifact IDs. Code-owned templates generate queries; arbitrary SQL, paths, URLs, pragmas and extension operations are rejected before the adapter. |
| DuckDB/Arrow/pandas vs Polars | DuckDB performs bounded columnar scans, Arrow carries bounded batches, and pandas supports existing modeling code. Polars is not installed simultaneously; a later replacement requires measured evidence and a separate ADR. |
| DCLab FeatureContract/MonitoringWindow vs Pandera/Evidently | DCLab stores the versioned contract, method, policy and state. Pandera and Evidently are replaceable execution libraries whose native objects never become public or authoritative records. |
| Early SDK/CLI vs later public platform | Ship a bounded internal/allowlisted Core ML client over the same `/v1`; Scope 5 hardens identity, completeness, compatibility and public packaging without creating a second client. |
| Early batch release vs Scope 10 serving | Implement one safe batch path in the Core ML MVP; Scope 10 extends it to online/streaming, advanced monitoring and higher autonomy when evidence triggers it. |
| Core ML focus vs existing business product | Preserve current business behavior and future Scope 8 plans. Business actions/outcomes are not required for the first Core ML MVP gate and cannot block its pilot. |
| AWS vs Google Cloud implementation | Maintain one product/application/Kubernetes contract and two provider modules. Neither cloud is the fallback or reference-only target; both pass the same security, restore, workload and golden-path gates before being advertised. |

## 9. Core-product definition of done

The core concept is implemented only when both primary personas can complete the
supported journey with no database intervention and the following evidence is
recorded:

- time to first valid baseline and time to constraint-satisfying result;
- deterministic leakage/validation findings and false-positive review;
- schema/profile, aggregate-slice, leakage-candidate and drift-window results
  demonstrate pandas-semantic parity, bounded Arrow output, cancellation and
  enforced memory/thread/input/output/temp/time limits without arbitrary SQL,
  persistent DuckDB state, extension/network access or Polars;
- complete lifecycle and decision reconstruction from immutable records;
- exact accepted/rejected changes, costs, metrics, artifacts and reasons;
- reproducible model package and batch predictions from pinned inputs;
- private MLflow run/package reconciliation, strict generated feature-contract
  validation and safe model-format loading without a second registry;
- drift detection, investigation proposal and release rollback exercise;
- Investigation Copilot citation, budget, cancellation and proposal-only proof;
- all three Deep Investigation modes and exactly-one-runtime conformance proof;
- upload, object-store, SQL, CRM and Snowflake connector conformance, schema-
  drift quarantine, immutable publication and secret/egress evidence;
- isolated Python escape/egress/resource/reproducibility evidence and hosted
  MCP OAuth, tenant, protocol, approval and kill-switch evidence;
- OpenAI Agents adapter record/replay, retention, required-action mediation and
  clean-disable evidence when that provider runtime is enabled;
- UI, SDK and CLI identity/state parity;
- tenant, privacy, budget, cancellation, crash-recovery and accessibility gates;
- identical-release AWS/GCP staging, workload-identity, RDS/Cloud SQL, S3/GCS,
  secret/KMS, sandbox, rollback and bidirectional controlled-restore evidence;
- user evidence from data scientists and ML engineers that the workflow reduces
  manual lifecycle work without hiding control.

The bounded specialist roster, three-mode Deep Investigation worker, isolated
Python, hosted MCP and initial connector pack are production-MVP workstreams.
Additional providers, Deep Agents subagents, unrestricted standing autonomy,
online/streaming serving and advanced distributed infrastructure remain
measured extensions. The existing business-side roadmap remains intact and
does not redefine or block the Core ML pilot unless explicitly selected.
