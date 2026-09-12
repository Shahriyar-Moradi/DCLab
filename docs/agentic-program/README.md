# DCLab agentic program documentation

This directory is the executable program of work for evolving DCLab from the
verified deterministic platform at product commit `3d54994e83283d34665f9589687108627284ab3f`
and verified truth/checkout baseline `91986b9b39bb54c907d50274e94de2febecffaa0`; see
[`../verification/S0_P01A_CURRENT_TRUTH.md`](../verification/S0_P01A_CURRENT_TRUTH.md)
into a secure, scalable, agentic decision-intelligence product whose primary
unit of work is the versioned machine-learning lifecycle.

## Start here

1. Read [`DCLAB_CORE_CONCEPT.md`](DCLAB_CORE_CONCEPT.md), the canonical product
   north star for data scientists and ML engineers.
2. Read [`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md).
3. Read the [`execution standard`](prompts/EXECUTION_STANDARD.md), then open the
   active scope under the [`437-prompt index`](prompts/README.md).
4. Execute plan IDs in order. Do not start a later plan when its dependency or
   scope gate is incomplete.
5. Use one coding-agent conversation and one pull request per prompt unless the
   prompt explicitly states otherwise.
6. Record verification with the evidence template in the master plan.

## Document roles

- `DCLAB_CORE_CONCEPT.md` is the canonical product identity, first-user job
  contract, ML lifecycle model and Core ML MVP acceptance path.
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

## Agent runtime decision

Scope 1 and later agent execution use one orchestration runtime: a pinned
LangGraph `StateGraph` release. Ordinary Pydantic models define DCLab-owned
domain, state, tool and structured-output contracts; a provider-neutral DCLab
gateway wraps the official OpenAI SDK as its first adapter. DCLab services and
PostgreSQL remain authoritative for tenancy, authorization, product run state,
tools, budgets, citations, audit and scientific behavior.

The production MVP does not use PydanticAI, `pydantic-graph`, LangChain
`create_agent`, or another nested agent loop. LangGraph checkpoints are private
execution state, never API authority or proof of access. S1-P01A must record the
exact package/checkpointer versions and compatibility policy before any runtime
dependency or agent table is added.
