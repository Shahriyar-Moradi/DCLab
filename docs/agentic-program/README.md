# DCLab agentic program documentation

> **PAUSED (2026-10-01).** This document is design reference only. The active plan, order and status live in [`docs/mvp/`](../mvp/README.md). Do not execute prompts from this program unless a `docs/mvp` prompt cites them.

This directory is the executable program of work for evolving DCLab from the
verified deterministic platform at product commit `3d54994e83283d34665f9589687108627284ab3f`
and verified truth/checkout baseline `91986b9b39bb54c907d50274e94de2febecffaa0`; see
[`../verification/S0_P01A_CURRENT_TRUTH.md`](../verification/S0_P01A_CURRENT_TRUTH.md)
into a secure, scalable, agentic decision-intelligence product whose primary
unit of work is the versioned machine-learning lifecycle.

## Start here

1. Read [`DCLAB_CORE_CONCEPT.md`](DCLAB_CORE_CONCEPT.md), the canonical product
   north star for data scientists and ML engineers.
2. Read the canonical
   [`agent-first MLOps and data-integration architecture`](AGENT_FIRST_MVP_ARCHITECTURE.md).
3. Read the canonical
   [`AWS and Google Cloud deployment architecture`](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md).
4. Read [`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md).
5. Before Plan 0.9, Plan 3.0, or any data-scan/ML-platform integration, read
   [`ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`](ML_PLATFORM_INTEGRATION_ARCHITECTURE.md).
6. Before S2-P12, read the
   [`Deep Investigation worker architecture`](DEEP_AGENTS_INVESTIGATION_COPILOT.md).
7. Before S1-P12, S2-P14 or S4-P08, read the
   [`Jev and NOOA integration architecture`](JEV_NOOA_INTEGRATION_ARCHITECTURE.md).
8. Read the [`execution standard`](prompts/EXECUTION_STANDARD.md), then open the
   active scope under the prompt index in [`prompts/README.md`](prompts/README.md).
   For S0-P04D onward, use the
   [`remaining-scope execution map`](prompts/REMAINING_SCOPE_EXECUTION_MAP.md)
   to resolve the plan outcome and complete the live-checkout execution card
   for each selected prompt before editing.
9. Execute dependency-required plan IDs in order. Plan 2.12 may progress in
   parallel after S2-P11F and does not block Scope 3, but it is required before
   the production-MVP release gate. Plan 2.13 is a separately gated OpenAI
   Agents adapter and never becomes an inner or outer loop around another runtime.
   Plans 1.12 and 2.14 are optional Jev/NOOA gates; implementation never implies
   activation. Plan 4.8 exists only when S2-P14H allows the notebook purpose.
10. Use one coding-agent conversation and one pull request per prompt unless the
   prompt explicitly states otherwise.
11. Record verification with the evidence template in the master plan.

## Document roles

- `DCLAB_CORE_CONCEPT.md` is the canonical product identity, first-user job
  contract, ML lifecycle model and Core ML MVP acceptance path.
- `AGENT_FIRST_MVP_ARCHITECTURE.md` is the canonical conclusion for autonomy,
  runtime selection, OpenAI integration, data integration, notebook/MCP
  security, initial connector pack and the no-SHAP MVP decision.
- `AWS_GCP_DEPLOYMENT_ARCHITECTURE.md` is the canonical portability contract,
  service mapping, workload/data/sandbox boundary and two-provider release gate.
- [`EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md`](EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md)
  adds the requested Runpod, Railway, Lambda GPU Cloud, Vast.ai and Nebius CPU/GPU
  execution targets without moving the AWS/GCP platform home or product database.
- [`JUPYTER_RUNTIME_MVP_BLUEPRINT.md`](JUPYTER_RUNTIME_MVP_BLUEPRINT.md) and
  [`JUPYTER_RUNTIME_MVP_PROMPTS.md`](JUPYTER_RUNTIME_MVP_PROMPTS.md) define the proposed
  stateful notebook, training/inference and external-compute implementation slices;
  their presence is not implementation or provider-readiness evidence.
- `DEEP_AGENTS_INVESTIGATION_COPILOT.md` is the approved, non-conflicting
  architecture boundary for the proposal-only three-mode investigation worker.
- [`JEV_NOOA_INTEGRATION_ARCHITECTURE.md`](JEV_NOOA_INTEGRATION_ARCHITECTURE.md)
  defines Jev as a governed structured-decision provider and NOOA as a separate
  proposal runtime, including their software, database, isolation, evaluation
  and notebook-integration boundaries.
- `ML_PLATFORM_INTEGRATION_ARCHITECTURE.md` is the approved one-owner-per-
  concern decision for the bounded DuckDB/Arrow `DataScanPort`, MLflow,
  Pandera, Evidently, safe model formats, OpenTelemetry and deferred
  Polars/W&B/Optuna/OpenLineage integration.
- `MASTER_SCOPE_0_TO_10_PLAN.md` is the canonical sequence, dependency model,
  architecture boundary, status baseline, and definition of done.
- `prompts/README.md` and `prompts/EXECUTION_STANDARD.md` define the coding-agent
  execution protocol, repository routing, change budget, and evidence contract.
- `prompts/SCOPE_*.md` files contain four to eight bounded prompts per plan:
  design, data/domain, services/jobs, transports/UI, verification, and release
  work as applicable.
- [`../adr/`](../adr/) holds accepted architecture decisions (S0-P02A:
  [0001-browser-session-bff.md](../adr/0001-browser-session-bff.md); S0-P02B:
  [0002-session-csrf-csp-abuse.md](../adr/0002-session-csrf-csp-abuse.md);
  S0-P03A: [0003-workspace-selection.md](../adr/0003-workspace-selection.md);
  S0-P04A: [0004-simulation-insights-tenancy.md](../adr/0004-simulation-insights-tenancy.md)).

## Authority and change control

The current repository and verified tests override stale counts or statuses in
older reports. Product and architecture decisions in the master plan govern new
work. Existing scientific, tenant, and evidence invariants remain mandatory.
If implementation discovers a conflict, stop that plan, add an ADR, update the
master plan, and obtain review before broadening behavior.

The core concept and roadmap are complementary authorities. The core concept
defines the product outcome; the roadmap defines the safe delivery order. A
plan may refine how the outcome is implemented but may not replace the ML
lifecycle with an agent graph, notebook document, code-generation surface or
business-only workflow. Existing business behavior and its future scope remain
intact; it is not a prerequisite for the first Core ML MVP release.

The bounded specialist roster, three-mode Deep Investigation worker, isolated
Python, hosted MCP and initial upload/S3/GCS/SQL/CRM/Snowflake connector pack are
inside the production-MVP program. They pass independent safety gates so the
deterministic ML golden path remains usable while a workstream is disabled or
being repaired.

AWS and Google Cloud are equal production deployment targets. DCLab uses one
provider-neutral application and Kubernetes workload contract with reviewed AWS
and GCP infrastructure/adapters. A feature that needs cloud infrastructure is
not dual-cloud complete until the same release passes both provider gates. The
MVP does not require active-active cross-cloud data or automatic failover; see
[`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md).

External compute is an independent choice: an approved run may use Runpod,
Railway, Lambda GPU Cloud, Vast.ai or Nebius while product state remains in its
AWS/GCP home. Admission depends on per-target capabilities, data-transfer policy,
quota and verified cleanup. Read the external compute architecture before runtime,
training, serving, placement, provider-credential or related infrastructure work.

External ML libraries implement bounded mechanics; they never become product
authority. DCLab owns tenant access, the immutable ML lifecycle, scientific
constraints, decisions, approvals, release state and audit. MLflow is the one
production-MVP experiment tracker and package-metadata service; W&B is deferred
to an optional future one-way connector. DuckDB is only an ephemeral,
resource-limited adapter behind DCLab's typed `DataScanPort`; it receives no
agent/user SQL, durable database role, unrestricted network/filesystem access
or public types. PyArrow carries bounded batches, pandas remains the modeling
compatibility layer, and Polars is not added for the MVP. See
[`ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`](ML_PLATFORM_INTEGRATION_ARCHITECTURE.md).

## Agent runtime decision

Scope 1 and later authoritative agent execution use one orchestration runtime:
a pinned raw LangGraph `StateGraph` release. Ordinary Pydantic models define
DCLab-owned domain, state, tool and structured-output contracts; a provider-
neutral DCLab gateway wraps the official OpenAI SDK as its first adapter. DCLab
services and PostgreSQL remain authoritative for tenancy, authorization,
product run state, tools, budgets, citations, audit and scientific behavior.

The production MVP does not use PydanticAI or `pydantic-graph`, and no high-
level agent loop may be nested in raw LangGraph. Ordinary Pydantic remains the
default; a future prompt may use PydanticAI only as a no-tool, single-response
typed leaf after an explicit ADR proves value and dependency compatibility.
Plan 2.12 is an approved separate LangChain `create_agent` runtime: Deep Agents runs as a separately
deployed, explicitly requested, proposal-only Investigation Copilot with read-
only DCLab SDK/API tools. It cannot invoke or be invoked by `worker-agent`, and
its temporary state is never product memory or authority. See
[`DEEP_AGENTS_INVESTIGATION_COPILOT.md`](DEEP_AGENTS_INVESTIGATION_COPILOT.md).
Plan 2.12 is independently releasable and is not part of the S2-P11F gate that
unblocks Scope 3; its own S2-P12H gate is required before production-MVP
go/no-go. Plan 2.13 may add the OpenAI-hosted Agents API only as a third,
explicit runtime choice for an entire AgentRun. It cannot wrap or be wrapped by
LangGraph or Deep Agents, and every required action is mediated by DCLab.
Plan 1.12 may add Jev as a sibling structured-decision provider behind DCLab
policy, data, budget and evaluation services. It is not an agent runtime and its
confidence never grants authority. Plan 2.14 may add NOOA only as a fourth,
separately deployed whole-run proposal runtime after an explicit release gate.
It cannot wrap or be wrapped by any other runtime. Plan 4.8 can use an allowed
NOOA release to propose notebook revisions but DCLab alone applies and executes
them through the existing Scope 4 services.
All runtime checkpoints remain private execution state, never API authority or
proof of access. S1-P01A and S2-P12A record exact package/checkpointer versions,
isolation and compatibility policy before their respective dependencies land.
