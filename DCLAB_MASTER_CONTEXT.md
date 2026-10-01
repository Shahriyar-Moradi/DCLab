# DCLab Master Context and Product Constitution

> **PAUSED (2026-10-01).** This document is design reference only. The active plan, order and status live in [`docs/mvp/`](docs/mvp/README.md). Do not execute prompts from this program unless a `docs/mvp` prompt cites them.

**Document status:** Draft authoritative context assembled from the sources available on 18 September 2026  
**Intended owner:** DCLab founders  
**Primary implementation repository reviewed:** `Shahriyar-Moradi/DCLab`, local checkout `/Users/shahriar/Downloads/decision_ai`  
**Relationship to execution documents:** This file explains product truth and decision context. The ordered implementation authority remains [`docs/agentic-program/MASTER_SCOPE_0_TO_10_PLAN.md`](docs/agentic-program/MASTER_SCOPE_0_TO_10_PLAN.md), its prompt files, accepted ADRs, and current verification evidence.  
**Important limitation:** This is not a transcript and not a claim that every referenced idea is implemented. Sources that were inaccessible or only partially available are identified in Section 0.

## Status notation

- **[CONFIRMED]** — explicitly accepted by the founder or established by canonical repository documentation/evidence.
- **[INFERRED]** — strongly implied by multiple sources but not recorded as a final founder decision.
- **[PROPOSED]** — a recommendation or implementation design that has not yet passed its delivery gate.
- **[UNCERTAIN]** — evidence is incomplete, unavailable, contradictory, or too old to treat as current.
- **[CONFLICT]** — two or more sources disagree materially; the resolution or required founder decision is stated.
- **[SUPERSEDED]** — an older decision or framing replaced by a newer, stronger decision.

These labels apply to the nearest statement, row, or subsection. Unlabelled product statements in Sections 1–23 are recovered conclusions supported by the canonical repository documents and the latest founder direction.

---

## 0. Source Coverage and Confidence

### 0.1 Precedence used for reconciliation

When sources disagree, this document uses the following order:

1. Current founder instruction or explicit founder correction.
2. Current verified code, database migrations, API contracts, and tests.
3. Accepted ADRs and documents marked canonical or CURRENT.
4. The master Scope 0–10 implementation program and execution standard.
5. Newer architecture and product documents in `docs/agentic-program/`.
6. Older specifications and repository reports, which remain useful for rationale but may contain superseded counts or scope.
7. Assistant-proposed ideas from chats that the founder did not explicitly accept.

No chat response, agent framework, notebook, MLflow record, or external-provider object overrides DCLab's persisted domain state and verified evidence.

### 0.2 Conversations actually available

| Source | Source type | Availability | Topics contributed | Confidence | May contain newer decisions |
| --- | --- | --- | --- | --- | --- |
| `Create scopes 0 to 10 plan` | Current Codex task and full visible conversation | Available | Scope expansion, truth gates, sessions/BFF, UI direction, agentic notebook, AWS/GCP, external compute, business proposal, founder vision | High | Yes; newest source used |
| `DCLab concept summary` | ChatGPT conversation | Available | Technical critique, unsafe universal canonicalization, causal-inference limits, developer-first product, ML/DS agent, free-developer-to-business strategy | High | Yes |
| `Development side for ML engineers` | ChatGPT conversation | Available | Shared Core, Personal Development and Business products, “Cursor for ML/DS,” virtual senior ML team, core/customer separation | High | Yes |
| `Disrupt Colab Notebooks` | ChatGPT conversation | Available | Notebook disruption thesis, investigation-first workspace, notebook as a view rather than product truth | High | Yes |
| `Build Private DCLab Prototype` | ChatGPT conversation | Available | Small agentic Colab prototype, realistic investigation workspace, inspectable tools/code/results, prototype limitations | Medium-high | Yes |
| `Redesign DCLab UI` | ChatGPT conversation | Available | Base44 reference, backend-first UI migration, left sidebar, modern sans typography, liquid-glass design, route/role preservation | High | Yes |
| `AI Decision Intelligence Platform` | ChatGPT conversation | Available | Earlier business-intelligence vision, connector abstraction, canonical mapping, manual/assisted/autonomous operating modes | Medium | Some content is older |
| `AI SaaS Platform Design` | ChatGPT conversation | Available | Enterprise positioning and earlier navy/blue/white visual direction | Medium | Older than later UI decisions |
| `Remember Disruption Vision` | ChatGPT conversation | Available | DCLab naming, business validation, layered ML idea, prototype/lab priority, initial roadmap | Medium-high | Mixed dates; later turns matter more |
| `Customer generation based ML prediction` | ChatGPT conversation | Available | Synthetic ideal-customer/scenario generation and lead-discovery concept | Medium-low | Future speculative feature |
| `Review GitHub Repository` | ChatGPT conversation | Available | Earlier implementation audit, CI defects, future-infrastructure documentation request | Medium | Repository facts may be stale |
| `Generalize dataset upload pipeline` | Codex task | Available | Canonical lineage, observability, platform/business explorer, capability enforcement, large-upload request | High for intent; current code determines status | Yes |
| `Watch DCLab CI failures` | ChatGPT task | Available | Historical CI failure and UI-identity assertion | Low for current status | No |
| `ایده‌های نوآورانه DC Lab` | ChatGPT conversation | Partially available; content truncated | Influencer/campaign prediction, sparse-data ideas, future innovation | Low | Possibly |
| `Automate ML Workflow` | ChatGPT conversation | Listed but timed out when read | Title indicates relevant ML automation work | Unavailable | Unknown |

### 0.3 Repository files actually used

The following were read directly. “Canonical” describes their stated role, not a guarantee that every line reflects the live working tree.

| Source | Type/status | Topics contributed | Confidence | May contain newer decisions |
| --- | --- | --- | --- | --- |
| [`README.md`](README.md) | Current repository entry point | Current product boundary, local workflow, tests, exclusions | High | Yes |
| [`DCLAB_MASTER_IMPLEMENTATION_SPEC.md`](DCLAB_MASTER_IMPLEMENTATION_SPEC.md) | Large implementation specification | Generic data understanding, target inference, deterministic pipeline, historical gaps and E2E expectations | Medium-high | Parts predate newer agentic plan |
| [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) | Older master Decision.ai specification | Feature intelligence, vertical prediction/recommendation/outcome layers, simulation, decision engine | Medium | Mostly older/future vision |
| [`docs/PRODUCT_AND_SALES_THESIS.md`](docs/PRODUCT_AND_SALES_THESIS.md) | Product thesis | Buyer, wedge, proof of value, enterprise trust, claim limits | High | No |
| [`docs/PROJECT_RECAP.md`](docs/PROJECT_RECAP.md) | Historical recap | Implemented phases and benchmark framing | Medium | No |
| [`docs/PERSONAL_BUSINESS_SHARED_CORE_ARCHITECTURE.md`](docs/PERSONAL_BUSINESS_SHARED_CORE_ARCHITECTURE.md) | Architecture contract | Personal/Business/shared-core boundary, roles, `/development`, compatibility | High | Yes |
| [`docs/DCLAB_CANONICAL_DOMAIN_MODEL.md`](docs/DCLAB_CANONICAL_DOMAIN_MODEL.md) | Living core model | Canonical entities, one ML core, roles, lineage, release gate | High | Yes |
| [`docs/DCLAB_DATA_AND_MODEL_LINEAGE.md`](docs/DCLAB_DATA_AND_MODEL_LINEAGE.md) | Partial/older diagram | Data, workflow, experiment and model lineage; compatibility rules | High for described structures | Some newer entities omitted |
| [`docs/DCLAB_DATABASE_ARCHITECTURE.md`](docs/DCLAB_DATABASE_ARCHITECTURE.md) | Historical database freeze | Integrity, object storage, indexes, immutability and queue design | Medium-high | Head/counts are historical |
| [`docs/DCLAB_DATABASE_ERD.md`](docs/DCLAB_DATABASE_ERD.md) | Historical ERD | Entity relationships | Medium | Superseded head |
| [`docs/DCLAB_ACCESS_ARCHITECTURE.md`](docs/DCLAB_ACCESS_ARCHITECTURE.md) | Access architecture | Workspace tenancy, platform/workspace roles, authorization precedence | High | Canonical role names evolved later |
| [`docs/DCLAB_RBAC_CAPABILITY_MATRIX.md`](docs/DCLAB_RBAC_CAPABILITY_MATRIX.md) | Capability matrix | Effective role and capability checks | High | Yes |
| [`docs/PLATFORM_WORKFLOWS.md`](docs/PLATFORM_WORKFLOWS.md) | Workflow guide | Customer/admin/business flows and translation boundary | High | Yes |
| [`docs/DCLAB_PIPELINE_DEEP_DIVE.md`](docs/DCLAB_PIPELINE_DEEP_DIVE.md) | Pipeline reference | 90-step runtime/evidence behavior | High for deterministic path | Yes |
| [`docs/DCLAB_ADAPTIVE_MODEL_BUILDER.md`](docs/DCLAB_ADAPTIVE_MODEL_BUILDER.md) and correctness companion | ML architecture/correctness | Holdout, validation, leakage, plans, verifier, tests | High | Yes |
| [`docs/DCLAB_PIPELINE_OBSERVABILITY.md`](docs/DCLAB_PIPELINE_OBSERVABILITY.md) | Observability contract | Append-only run events and LLM ledger | High | Yes |
| [`docs/HOW_TO_USE_THE_PLATFORM.md`](docs/HOW_TO_USE_THE_PLATFORM.md) | Current operator/user guide | Existing route-level experience | High | Yes |
| [`docs/agentic-program/DCLAB_CORE_CONCEPT.md`](docs/agentic-program/DCLAB_CORE_CONCEPT.md) | Canonical product north star | Project-centric lifecycle, three views, decision memory, bounded autonomy | Very high | Yes |
| [`docs/agentic-program/AGENT_FIRST_MVP_ARCHITECTURE.md`](docs/agentic-program/AGENT_FIRST_MVP_ARCHITECTURE.md) | Canonical MVP architecture | Authority boundaries, runtimes, agents, connector plane, notebook/MCP | Very high | Yes |
| [`docs/agentic-program/MASTER_SCOPE_0_TO_10_PLAN.md`](docs/agentic-program/MASTER_SCOPE_0_TO_10_PLAN.md) | Ordered implementation authority | Scopes, dependencies, prompts, gates, security/evidence | Very high | Yes |
| `docs/agentic-program/prompts/*.md` | Execution prompt packages | Detailed plan-by-plan implementation requirements | High | Yes |
| [`docs/agentic-program/JUPYTER_RUNTIME_MVP_BLUEPRINT.md`](docs/agentic-program/JUPYTER_RUNTIME_MVP_BLUEPRINT.md) | Proposed implementation design | Stateful Jupyter runtime, session/epoch model, training/inference integration | High as design; not implementation evidence | Yes |
| [`docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md`](docs/agentic-program/ML_PLATFORM_INTEGRATION_ARCHITECTURE.md) | Approved roadmap architecture | MLflow, DuckDB/Arrow, Pandera, Evidently, safe model formats, OTel | High | Yes |
| [`docs/agentic-program/DEEP_AGENTS_INVESTIGATION_COPILOT.md`](docs/agentic-program/DEEP_AGENTS_INVESTIGATION_COPILOT.md) | Approved future workstream | Three investigation modes and non-authoritative Deep Agents boundary | High | Yes |
| [`docs/agentic-program/AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](docs/agentic-program/AWS_GCP_DEPLOYMENT_ARCHITECTURE.md) | Canonical deployment contract | Either AWS or GCP, same release, controlled restore, provider neutrality | Very high | Yes |
| [`docs/agentic-program/EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md`](docs/agentic-program/EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md) | Proposed external-compute architecture | Runpod, Railway, Lambda GPU Cloud, Vast.ai, Nebius adapters and readiness matrix | High as design; not implementation evidence | Yes |
| [`docs/agentic-program/DCLAB_BUSINESS_PROPOSAL_FOR_ALIREZA.md`](docs/agentic-program/DCLAB_BUSINESS_PROPOSAL_FOR_ALIREZA.md) | Founder/cofounder proposal | Plain-language product, business model, technical MVP, market test | High | Yes |
| [`contracts/truth_baseline.json`](contracts/truth_baseline.json), OpenAPI/table contracts | Generated current facts | Repository, schema and API inventory | High for current working tree | Yes |
| [`docs/verification/README.md`](docs/verification/README.md) and `S0_*` records | Verification ledger | CURRENT/HISTORICAL status, verified Scope 0 work | Very high for named SHA | Yes |
| `apps/api/app/db/models.py`, API/services and `apps/web` routes/components | Live code | Actual entities, routes, BFF, sidebar, UI tokens and missing agent/notebook code | Very high | Yes |

### 0.4 Other materials available in the current conversation

- Founder-selected UI concept images: clean Apple-like liquid glass, more white space, simple background, blue `#2596be`, green `#21883e`, supporting navy and restrained yellow.
- GitHub-workflow reference image: vertical status rail with explicit success/failure/pending markers and inspectable step details.
- Dashboard/Base44 reference: modern sans typography, rounded white/translucent cards, clear hierarchy, restrained density.
- Earlier generated DCLab architecture, runtime, business-proposal, and summary documents listed above.

### 0.5 Important missing or unavailable sources

- The full contents of `Automate ML Workflow` could not be retrieved because the conversation read timed out.
- The full `ایده‌های نوآورانه DC Lab` history was truncated.
- No complete export of all ChatGPT project conversations or saved memory store was available; only the listed accessible tasks were used.
- The private Sites prototype's source/state was not inspected in this task.
- The separate DCLab R&D/experiments repository at `/Users/shahriar/Desktop/Desktop/Work/MyStartUp/R&D` was not inspected. It is therefore not treated as main-product authority.
- No customer interview notes, signed pilot agreements, pricing research, production cloud account, live workload, or production database was available.
- [UNCERTAIN] Some historical docs refer to `Decision.ai`; their concepts are useful, but DCLab is the current company/product name.

---

## 1. Executive Definition

### 1.1 DCLab in one sentence

**DCLab is a project-centric, AI-assisted operating environment that helps data scientists and ML engineers move from a business or scientific question and governed data to a reproducible, verified, released, and monitored ML result while keeping code, evidence, decisions, cost, and control connected.**

### 1.2 DCLab in one paragraph

DCLab compresses the fragmented ML lifecycle into one versioned project. A user defines a problem, authorizes data, understands and validates it, plans experiments, builds and compares candidates, records why a result was accepted or rejected, packages a model, runs authorized predictions, and monitors or rolls back the release. AI agents help investigate, plan, explain, debug, and propose next work; deterministic DCLab services retain authority over identity, data access, validation, training, budgets, approvals, execution, evidence, release, cancellation, and rollback. A notebook, chat, workflow graph, SDK, CLI, MCP server, experiment tracker, or compute provider is a view or adapter around that project—not a competing source of truth.

### 1.3 Complete product thesis

[CONFIRMED] The primary users and initial adoption wedge are data scientists and ML engineers. The useful shorthand is **“Cursor for ML/DS,”** but the product is more than an ML coding assistant. Cursor compresses a code-editing loop; DCLab must compress and govern the complete ML lifecycle:

`Problem → Data → Understanding → Preparation → Features → Validation → Experiments → Evaluation → Decision → Package → Batch prediction → Monitoring → Investigation → Improvement or rollback`

[CONFIRMED] DCLab Core is not identical to the Personal Development product. It is the shared ML platform consumed by:

- **Personal Development:** an individual ML/DS workspace and product experience.
- **Business:** the same ML core plus team membership, administration, governance, collaboration, policy, monitoring, entitlements, and commercial controls.

[CONFIRMED] Business must extend the shared core, never fork or duplicate it. The existing translated business/client decision surface can remain, but it must not redefine the technical ML lifecycle or weaken the Development experience.

### 1.4 Central problem

ML work is fragmented across notebooks, scripts, local files, experiment trackers, chat, tickets, cloud jobs, deployment tools, dashboards, and human memory. The fragmentation causes repeated work, unreproducible results, scientific mistakes, unclear authority, expensive handoffs, and knowledge loss. General coding assistants can generate code but usually do not own a trustworthy project model or enforce the scientific and operational contract.

### 1.5 Why DCLab needs to exist

- Existing notebooks make state easy to create and difficult to govern.
- Experiment trackers record telemetry but not the complete objective, decisions, approvals, release, and recovery story.
- AutoML can search models but does not replace problem framing, temporal semantics, leakage control, evidence interpretation, or safe production ownership.
- Coding agents can edit files but do not automatically understand which dataset version, target moment, holdout, model release, or business constraint is authoritative.
- MLOps systems often expose infrastructure primitives without providing one coherent workflow for the scientist.

DCLab exists to make the connected lifecycle itself the product.

### 1.6 Fundamental differentiation

1. **One canonical project lifecycle:** resources and decisions are versioned and linked end to end.
2. **Deterministic scientific authority:** agents can reason and propose; tested services execute and verify.
3. **Three synchronized views:** conversation/actions, workflow/evidence, and implementation/infrastructure resolve to the same IDs and state.
4. **Durable decision memory:** important accepted, rejected, and superseded choices are searchable project records, not hidden in chat history.
5. **Safe autonomy progression:** authority is promoted per capability only after evaluation, with budget, approval, idempotency, cancellation, and rollback.
6. **Developer-first, business-legible output:** technical users gain leverage and produce evidence that business stakeholders can understand.
7. **Portable platform, optional compute:** one installation runs on AWS or GCP; reviewed external compute never owns product truth.

### 1.7 Intended long-term impact

The founder vision is for a small technical team to operate with the discipline and capability of a much larger ML organization. DCLab should make advanced ML work clearer, faster, safer, repeatable, and practical to operate from one connected system. The longer-term Decision Intelligence layer can connect predictions to recommendations, actions, outcomes, and measured impact after the Core ML lifecycle is trusted.

### 1.8 What DCLab must never become

- A chat shell that fabricates project state or metrics.
- A model-count competition where “100 models” is treated as evidence of quality.
- A universal automatic canonicalization promise that silently guesses business meaning or identity.
- A shared notebook server with weak tenant isolation.
- A second CRM, ERP, data warehouse, source-control system, or cloud control plane.
- A product in which MLflow, Jupyter, LangGraph, Deep Agents, MCP, an LLM provider, or a GPU vendor becomes the authority for DCLab state.
- An agent that can self-approve, silently retrain, deploy, or take an external action outside policy.
- A system that claims causality from biased observational data or hides uncertainty.
- A UI that looks functional while using fake data or missing backend authority.

---

## 2. Founder Vision and Product Philosophy

### 2.1 Founder vision

The founder wants DCLab to become the main working environment where an ML team moves from its first question and dataset to a deployed and monitored result without rebuilding context between tools. Conversation, visual workflow, notebook code cells, experiments, decisions, compute, releases, and monitoring should be connected views of the same project.

The founder wants agents to understand project history and help with investigation, planning, coding, experiments, and operations. Their authority must remain controlled. DCLab must own identity, permissions, budgets, approvals, evidence, cancellation, and rollback. Teams should be able to deploy DCLab on AWS or Google Cloud and use approved external CPU/GPU capacity without giving up control of project state, secrets, decisions, or audit history.

### 2.2 Beliefs behind the product

- **AI should increase expert leverage, not erase expert judgment.** The user should feel that a senior ML team is available, while retaining review and veto authority.
- **The ML problem is larger than code generation.** Correct target semantics, point-in-time data, leakage prevention, validation, metrics, cost, reproducibility, release, and monitoring matter as much as code.
- **Evidence must beat fluency.** A plausible explanation without a current DCLab citation is not project truth.
- **Automation must be earned.** Start with observe/explain/propose; promote reversible execution only after measured reliability.
- **Negative results are valuable.** Failed candidates, rejected plans, and invalid assumptions must remain discoverable.
- **The notebook is useful but not canonical.** State in RAM and cell order cannot determine production truth.
- **Business value is downstream of scientific honesty.** DCLab may eventually optimize actions and outcomes, but cannot imply causal impact without qualifying evidence.
- **Infrastructure choice should not redefine the product.** AWS/GCP and external providers implement ports; DCLab IDs and lifecycle semantics stay stable.

### 2.3 Disruption thesis

[CONFIRMED] DCLab should not win by adding an AI chat beside a traditional notebook. It should replace the manual coordination burden around the notebook. The user works on an investigation and an ML lifecycle; cells are implementation blocks inside that investigation.

The desired shift is:

```text
Manual today:
question → notebook setup → repeated code/debugging → manual experiment comparison
→ scattered artifacts → hand-built deployment → disconnected monitoring

DCLab:
question + authorized data → reviewable plan → governed execution → evidence comparison
→ approved model package → authorized prediction → monitoring → cited investigation
```

### 2.4 Human expertise and agent autonomy

Humans define or confirm objectives, business constraints, prediction moment, sensitive data purpose, material plan changes, model promotion, external actions, and risk acceptance. Agents may surface ambiguity, draft a plan, invoke read-only tools, propose typed changes, explain failures, and request bounded commands. Deterministic services validate every request. The production MVP includes at most one bounded improvement loop; standing high-impact autonomy is future scope.

### 2.5 Product principles

1. Project before chat, model, notebook, or provider.
2. One authoritative resource for each concern.
3. Shared ML core; Personal and Business are product experiences, not separate engines.
4. Backend and database behavior are functional truth; reference designs control presentation only.
5. Every material output must be attributable, inspectable, and reproducible.
6. Complexity may be summarized but not hidden from authorized technical users.
7. Default to bounded, reversible, and reviewable operations.
8. Do not advertise a capability until its failure, recovery, security, and cost behavior is verified.

### 2.6 Scientific principles

- Lock the prediction moment and holdout before candidate comparison.
- Fit preprocessing only on the appropriate training fold/split.
- Use CV or validation evidence for selection; use the final holdout once for final evaluation.
- Distinguish deterministic facts, cited observations, hypotheses, recommendations, and unknowns.
- Prefer a strong baseline over a large unbounded search.
- Preserve uncertainty, calibration, subgroup behavior, failure, and negative results.
- Treat temporal/group structure and leakage as first-class constraints.
- Never equate correlation, prediction, uplift, and causal impact.
- Never silently use final holdout results to tune candidates.

### 2.7 Engineering principles

- PostgreSQL owns durable product state; immutable object storage owns large bodies.
- Services own transitions; APIs, UI, SDK, CLI, agents, and MCP call the same services.
- Commands are idempotent, version/precondition checked, cancellable, bounded, and auditable.
- Worker/provider loss must not corrupt existing evidence.
- Provider-specific SDKs remain in adapters.
- No arbitrary SQL, path, URL, extension, or secret reaches the bounded analytical scan path.
- New migrations discover the live head and preserve additive compatibility unless a reviewed break is required.
- A current evidence record names the exact source SHA and observed commands.

### 2.8 UX principles

- Calm, premium, technical, and trustworthy—not cyberpunk or visually noisy.
- Clean modern sans typography; monospace only for code, IDs, logs, or technical metadata.
- Generous white space with balanced information density.
- Liquid-glass treatment should clarify layers, not reduce contrast.
- The global product shell uses a left sidebar; local tabs are only for views of one object.
- The notebook/investigation workspace prioritizes code and evidence, with chat and experiment history visible but subordinate.
- Every loading, empty, warning, failure, blocked, cancelled, stale, retrying, and partial-result state is explicit and accessible.
- Capability-disabled UI never substitutes for backend denial.

### 2.9 Non-negotiable values

- Tenant isolation.
- Scientific honesty.
- Reproducibility and provenance.
- Human control over material risk.
- Transparent cost and authority.
- Recoverability and rollback.
- Accessibility.
- No fabricated customers, metrics, certifications, testimonials, or implementation claims.

---

## 3. Problems DCLab Solves

| Problem | Who experiences it | Current workaround | Why it fails | DCLab response | Measurable outcome |
| --- | --- | --- | --- | --- | --- |
| Tool fragmentation | Data scientists, ML engineers, managers | Notebooks + Git + tracker + chat + cloud console + documents | Context and IDs diverge; handoffs become manual | One lifecycle with synchronized views | Fewer handoffs; shorter time to reproduce/explain |
| Slow experiment setup | Data scientists | Copy old notebooks and pipeline scripts | Hidden assumptions and repeated boilerplate | Versioned ProblemSpec, plan, environment, and deterministic builders | Time to first valid baseline |
| Ambiguous target and prediction moment | Data scientists, analysts | Guess from column names or business memory | Wrong labels and future data create misleading results | Explicit target/horizon/entity/time contract and clarification | Fewer invalid runs; reviewable target decision |
| Leakage and split contamination | Data scientists, reviewers | Manual review and ad hoc checks | Leakage often survives until production | Deterministic leakage findings, temporal/group validation, critic review | Leakage detection rate and false-positive review |
| Weak model comparison | ML teams | Compare a few notebook metrics | Metrics, folds, preprocessing, and costs differ | Canonical plan and candidate comparison with locked evidence | Reproducible candidate rankings and constraint satisfaction |
| “More models is better” fallacy | Founders and teams under pressure | Large brute-force search | Wastes cost and increases overfitting risk | Bounded baselines/challengers, diversity and stop rules | Quality per compute/cost, not candidate count |
| Reproducibility failure | ML teams and auditors | Freeze a notebook or copy a folder | Data, environment, order, hidden RAM, and artifacts drift | Immutable data/code/environment/artifact lineage | Reproduction success rate |
| Knowledge loss | Teams and future maintainers | Chat, meetings, ticket comments | Rationale is unstructured and disappears | Immutable ProjectDecisionRecord with citations and supersession | Share of material decisions with evidence |
| Deployment handoff | Data science and engineering | Export pickle, rewrite service manually | Schema/package mismatch and unclear release state | Verified package, feature/environment contract, batch release and rollback | Time and failure rate from selected model to prediction |
| Operational blindness | ML engineers | Cloud logs and separate dashboards | Product state and infrastructure state are not connected | Durable job/event state, OTel, monitoring windows and runbooks | Mean time to detect/understand/recover |
| Uncontrolled AI | Security, platform owners, users | Give assistant broad credentials or avoid AI | Either unsafe or low value | Minimal context, typed tools, reauthorization, budgets, approvals | Unsafe-call rate, policy-block correctness, user trust |
| Notebook state fragility | Data scientists | Keep kernel alive and rerun cells manually | Hidden order/state; refresh or crash loses context | DCLab revisions + bounded Jupyter session/epoch records | Clean whole-notebook replay and recovery success |
| Data integration heterogeneity | ML teams and businesses | Build one-off imports/connectors | Repeated auth, schema, cursor, retry and mapping logic | ConnectorPort, immutable publication, mapping review and drift quarantine | Time to first governed DatasetVersion; sync correctness |
| Unsafe automatic canonicalization | Data/ML teams | LLM or heuristics infer identities/joins | Semantic errors and temporal leakage are hard to detect | Proposed mappings, namespaces, confidence, evidence and human confirmation | Mapping review accuracy and reversibility |
| Business/technical communication gap | Technical users and leaders | Slide decks made after the analysis | Business summary drifts from actual evidence | Technical and audience-safe business views from same resources | Stakeholder comprehension and decision turnaround |
| Cost opacity | Founders, buyers, operators | Provider bills after execution | Costs cannot be attributed or bounded | Reservations, budgets, estimates, usage and reconciliation | Estimate variance, cost per useful result |
| Cross-tenant risk | Every customer | Rely on UI navigation or token claims | IDs and cached state can leak across tenants | Persisted membership resolution and workspace-filtered queries | Zero unauthorized object/data disclosure |
| Causal overclaiming | Business/product teams | Train on historical actions and call correlations uplift | Confounding and reverse causality create wrong decisions | Separate prediction/recommendation/outcome/impact; require qualifying design | Correct evidence-class labels; holdout/experiment adoption |

---

## 4. Target Users and Jobs to Be Done

### 4.1 Primary personas

| Persona | Goals and current workflow | Frustrations | Required capabilities and trust | Adoption motivation | Expected aha moment |
| --- | --- | --- | --- | --- | --- |
| Data scientist | Understand data, form hypotheses, engineer features, train/compare models, explain results | Repeated setup, leakage risk, scattered evidence, fragile notebooks | Transparent plans, editable/reviewable code, valid splits, full metrics, reproducibility | Complete better experiments faster without losing judgment | “I can see what changed, why it helped, and reproduce it without rebuilding the project.” |
| ML engineer | Automate runs, manage environments/artifacts/jobs, package/release, monitor and recover | Handoffs, environment drift, hidden run state, cloud complexity | APIs/SDK/CLI, immutable lineage, idempotent jobs, cost and rollback | Reduce operational work while retaining control | “The same selected result becomes a governed batch release with no custom handoff.” |
| ML researcher | Compare hypotheses and methods, preserve negative results, reproduce experiments | Results become disconnected from method and data versions | Experiment branches, pinned environments, exact evidence and exports | Reliable research memory and comparison | “A failed idea is still a reusable, cited project result.” |
| Data analyst | Investigate data, explain trends, prepare a reliable analytical handoff | ML tooling is too technical; business question gets lost | Guided investigation, bounded queries, clear assumptions and reports | Move from analysis toward trustworthy prediction | “The platform identifies what is answerable and what data is missing.” |

### 4.2 Secondary personas

| Persona | Role in DCLab | Trust/skill expectations | Purchase or adoption motivation |
| --- | --- | --- | --- |
| Technical founder/independent builder | Personal Development user operating end to end | High technical visibility; cost-sensitive | Build and operate an ML product without assembling a large team |
| Head of Data/ML/CTO | Economic buyer and governance owner | Needs auditability, delivery metrics, cost, security and rollback | Improve team throughput and reduce release risk |
| Product/business stakeholder | Consumer of audience-safe evidence | Does not need raw ML internals; must understand limits | See what question was answered and how confidently |
| Workspace owner/admin | Membership, policy, entitlement and project oversight | Needs separation from ML execution roles | Govern a Business team without becoming a platform administrator |
| Platform admin | Operates DCLab across tenants | Full operational evidence with strict audit | Support, incident response, configuration and release management |
| Platform developer | Cross-tenant read-only diagnosis | Must not mutate customer or platform state | Debug/support with complete visibility and no write authority |

### 4.3 Students and casual users

[PROPOSED] Students may use Personal Development later, but they are not the production-MVP design center. If admitted through a free tier, limits, sample data, educational explanations, and cost isolation must not weaken the professional lifecycle contract.

### 4.4 Jobs to be done

The core user hires DCLab to:

1. Convert an ambiguous ML question into a reviewable, answerable ProblemSpec.
2. Determine whether available data is usable before spending on modeling.
3. Build and compare a scientifically valid baseline and challenger set.
4. Investigate failures, weak segments, drift, and tradeoffs with evidence.
5. Preserve exact project history and hand it to another expert without loss.
6. Turn an approved result into a reproducible model package and batch output.
7. Automate the supported path through SDK/CLI without bypassing policy.
8. Give business stakeholders a truthful, audience-safe explanation.

---

## 5. Canonical DCLab User Journey

The table below describes the target product. Status is noted when a stage is planned rather than currently complete.

| Stage | User action | System action | Agent action | Inputs and outputs | Stored artifacts and approvals | Failure/recovery/audit |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Workspace and project | Select Personal or authorized Business workspace; create/open project | Resolve membership/capability; create scoped Project | Explain available scope only | Input: identity/workspace; output: project context | Workspace, Membership, Project; no agent authority | Unauthorized selector denied; active context restorable and audited |
| 2. Problem definition | Describe question, entity, target, horizon, review capacity, constraints | Validate and version ProblemSpec | Ask only material clarifying questions; propose draft | Output: typed objective, task, target, metrics, constraints | ProblemSpec version and decision record; user confirms material semantics | Ambiguity yields `needs_input`, not guessed execution |
| 3. Data authorization | Upload file or choose approved source/version | Quarantine, classify, digest, authorize, publish immutable DatasetVersion | Explain source readiness; no raw-secret access | Input: CSV/Parquet/object/connector; output: DatasetVersion | DataSource, DataAccess, IngestionRun, DatasetAsset/Version, Artifact | Invalid/malicious input quarantined; resumable bounded ingest; full access audit |
| 4. Schema/profile | Review schema, quality and basic distributions | Bounded `DataScanPort` profiling over authorized artifact | Dataset Investigator prioritizes findings and unknowns | Output: schema/profile, missingness, duplicates, imbalance, time/entity candidates | DatasetColumn, DatasetProfile, DataQualityFinding | Resource limits/cancellation; no arbitrary SQL/path/network; deterministic results cited |
| 5. Semantic mapping | Confirm column roles, identifiers, timestamps, units and joins | Version proposed/accepted mapping; retain namespaces and source evidence | Propose uncertain mappings with confidence, never irreversible merge | Output: accepted roles/mappings and unresolved issues | Preparation decisions, future mapping contract, decision records | Rejection or changed source creates new version; no silent remap |
| 6. Leakage/readiness | Review leakage, future information, label availability and split feasibility | Run deterministic leakage/readiness checks | Leakage/Validation Critic adds cited suspicions and plan diff | Output: findings, blocks, mitigation | DataQualityFinding, readiness evidence, ProjectDecisionRecord | Blocking risk prevents build; false-positive review preserved |
| 7. Preparation plan | Approve cleaning, missing-value, type and feature-availability choices | Lock train-only transformations and point-in-time rules | Propose alternatives with impact and uncertainty | Output: PreparationPlan and FeatureSetVersion | DataPreparationDecision, FeatureSet/Version, Feature/Lineage, PreprocessingStep | Upstream change marks downstream stale; historical plan immutable |
| 8. Validation and metrics | Confirm temporal/group/random split, holdout, primary metric and business constraints | Persist one canonical PipelineScientificPlan | Problem/Experiment Planner proposes; critic checks | Output: locked split/validation/metric contract | PipelineScientificPlan; approval for material changes | Holdout inaccessible to tuning; invalid plan blocks execution |
| 9. Experiment plan | Inspect baseline, candidates, budgets, stop conditions | Create versioned plan/command preflight | Experiment Director proposes bounded portfolio | Output: ExperimentPlan and exact command summary | Plan version, budget reservation, optional approval | Changed payload invalidates approval; quota/budget denial explicit |
| 10. Training execution | Start, observe, cancel or retry | Create/replay ExecutionRequest, WorkflowRun, PipelineRun and MlJob; execute deterministic pipeline | Observe/propose repair; cannot edit running locked plan | Outputs: stage events, candidates, fold metrics, artifacts | Run/stage/candidate/fold/evaluation rows, MLflow refs [PROPOSED] | Lease, heartbeat, idempotency, cancellation, child retry, late-result quarantine |
| 11. Evaluation | Compare candidates, thresholds, calibration, errors, slices and cost | Normalize metrics and enforce selection from validation/CV only | Metric Critic explains tradeoffs and weak segments | Output: comparison and constraint status | ModelEvaluation, EvaluationMetric, Visualization, cost/evidence | Failed candidates remain; rejected candidates never get final-holdout result |
| 12. Winner lock/final holdout | Approve or reject selected candidate | Persist selection, final fit, one final holdout evaluation | Audit evidence; no self-promotion | Output: locked winner and final evidence | ModelSelectionDecision, final-holdout evaluation, decision record | Holdout reuse prevented; inconsistency blocks verification |
| 13. Reproduction/package | Request reproduction and package | Verify code, dependencies, environment, data and artifact digests | Artifact/Provenance Auditor reports gaps | Output: reproducible package/report/notebook | CodeSnapshot, RuntimeEnvironment, Artifact, ModelAsset/Version | Unsafe format or missing tracking blocks verified promotion |
| 14. Human review | Accept, reject, supersede or request child experiment | Append immutable decision and resulting links | Summarize facts vs hypotheses and alternatives | Output: reviewed decision | ProjectDecisionRecord with citations | Correction creates superseding record; decision alone never executes |
| 15. Batch release | Approve model/version and scoring input | Create immutable release and queued batch prediction | MLOps auditor preflights and proposes only | Output: prediction artifact and lineage | Model release [PROPOSED], prediction/batch records, manifests | Schema mismatch, cancellation or failure terminalized; previous release unaffected |
| 16. Monitoring | Inspect input/prediction/performance windows | Calculate bounded monitoring evidence and thresholds | Operations/Drift investigator drafts cited proposal | Output: monitoring windows, alerts, diagnosis | MonitoringWindow [PROPOSED], Evidently-derived normalized report | Missing labels produce input/prediction-only evidence; no fake performance claim |
| 17. Retraining/rollback | Approve investigation, child build or rollback | Execute new typed command or restore approved prior release | Recommend alternatives within budget | Output: new branch or rollback result | Command, approval, run/release/decision lineage | No autonomous retraining in MVP; rollback and failure reason auditable |
| 18. Notebook investigation | Open notebook, run bounded cells, save revision | Start isolated Jupyter session/epoch; stream bounded output | Propose revision diff and await approval | Input: authorized artifacts; output: cells, tables, images, errors | Notebook/revision/cell/execution/session/epoch tables [PROPOSED] | Refresh restores saved output; restart loses RAM explicitly; session cleanup enforced |
| 19. Reuse and cross-project learning | Search prior decisions/patterns within permission | Retrieve bounded records and citations | Suggest applicable patterns without claiming transfer validity | Output: cited prior evidence and template proposal | Search index as derivative only; source records remain authoritative | Tenant/purpose filters, deletion propagation, poisoning tests [PROPOSED] |

### 5.1 Supported first golden path

[CONFIRMED] The first sellable/production-MVP path is structured business data to a verified and monitored **batch** model. Churn/retention, lead scoring, demand/purchase prediction, customer value, campaign response, and similar tasks are examples, not hardcoded product domains.

### 5.2 Stages deliberately deferred

- Online/streaming serving as the primary path.
- Automatic production retraining.
- Active-active multi-cloud state.
- Arbitrary connectors or arbitrary SQL.
- Universal canonicalization and automatic identity merging.
- Full causal recommendation/action engine.
- Large foundation-model training.

---

## 6. Core Product Capabilities

### 6.1 Capability map

| Module | Classification | Purpose and user value | Inputs / outputs and key entities | Automation and human control | Dependencies | Current status | Future scope / non-goals |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Identity, tenancy and entitlements | Supporting platform core | Keep Personal and Business use of the same ML core isolated and policy-aware | User, AuthSession, Workspace, Membership, Entitlement, Capability | Database membership is authoritative; admins manage seats/roles | PostgreSQL, BFF/session, authorization service | Implemented substantially; Scope 0 hardening continues | SSO/SCIM/CMK later; UI hiding is never authorization |
| Project and lifecycle | Essential product core | Make one ML project the connected unit of work | Project, ProblemSpec, DatasetVersion, plans, runs, models, releases, decisions | Services derive dependencies/staleness; user reviews versions | Existing lineage plus Scope 1 project projection | Partially implemented; complete projection/decision memory planned | Not a generic graph DB; not LangGraph state |
| Data access and ingestion | Essential product core | Publish governed immutable data versions | DataSource, DataAccess, IngestionRun, DatasetAsset, Dataset, Artifact | Upload/connector jobs classify, quarantine and publish | Object storage, jobs, DataScan | Upload/lineage foundation exists; connector pack planned | No arbitrary source SQL, CDC, reverse ETL in MVP |
| Bounded dataset understanding | Essential product core | Understand large tabular artifacts safely | DatasetColumn/Profile, DataQualityFinding, scan result | Deterministic templates; agent explains/proposes | DataScanPort, DuckDB/Arrow [planned] | Existing pandas-based profiling; bounded port planned | DuckDB never public or durable; Polars deferred |
| Problem and scientific-plan management | Essential product core | Version intent, target, split, metric, constraints and holdout | ProblemSpec, PreparationPlan, PipelineScientificPlan | Agent drafts; deterministic validation and human confirmation | Lifecycle projection and decision records | ProblemSpec/scientific-plan foundations exist | Agent cannot silently alter locked plan |
| Feature intelligence and preprocessing | Essential product core | Reuse and govern feature meaning and transformations | FeatureSet/Version, Feature, lineage, transformation, preprocessing | Deterministic operations; future agent recommendations | Dataset roles, point-in-time contract | Implemented foundation; broader intelligence future | No claim that 100 features is better; no leakage-producing auto joins |
| Experiment and adaptive model builder | Essential product core | Build bounded baseline/challenger portfolios and compare honestly | Workflow/Pipeline/Run, Candidate, Fold, Evaluation, Selection | Code-owned execution; agents may propose bounded changes | Jobs, artifacts, scientific plan | Meaningful implementation exists and is extensively tested | No unbounded AutoML or holdout-driven search |
| Evidence, verification and observability | Essential product core | Reconstruct what happened and why | MlRunEvent, Verification, LlmInvocation, Artifact, Visualization | Append-only runtime events and deterministic verifier precedence | Pipeline instrumentation, OTel later | Implemented meaningful observability foundation | Logs/traces never replace audit/scientific evidence |
| Model registry, package and batch operations | Essential MVP extension | Turn an approved result into governed prediction and rollback | ModelAsset/Version, safe package ref, release, batch run, MonitoringWindow | Exact promotion/release approval; agent audits/proposes | MLflow/Pandera/Evidently/safe format [planned] | ModelAsset/Version implemented; production release path incomplete | Online/streaming serving later |
| Project-aware agent system | Essential MVP extension | Investigate, explain, plan, critique and propose across lifecycle | Agent definitions/versions/sessions/runs/steps/tools/citations/proposals | L0–L2 required, narrow L3, one bounded L4 loop | Scope 0, lifecycle, LLM gateway, ToolRunner | Not implemented in live code; architecture and detailed prompts complete | No LLM authority, hidden nested loops, self-approval or unrestricted tools |
| Durable decision memory | Essential MVP extension | Preserve why choices were accepted, rejected or superseded | ProjectDecisionRecord | User/approved service appends; agent rationale stays labeled | Scope 1 lifecycle/citations | Planned; existing lab/decision records are not the full owner | Not chat history, checkpoint memory or vector-store truth |
| Agentic notebook | Supporting MVP capability | Let users investigate with stateful code while staying linked to project truth | Notebook, revision, cell, environment, session, epoch, execution, output | Human runs; agent proposes diffs; isolated execution | Scope 4, runtime coordinator, Jupyter Server, sandbox | Proposed only; current code exports notebooks but has no interactive runtime | JupyterLab/Hub not required; Enterprise Gateway deferred |
| Developer API, SDK and CLI | Supporting platform capability | Automate the same workflow without private imports or DB access | `/v1` resources/commands, client types, CLI config/results | Client requests same typed commands and approvals | Stable API, service accounts, packaging | HTTP client and internal CLI foundations exist; public parity incomplete | No CLI importing backend internals |
| MCP adapter | Optional but production-MVP workstream | Give external agent clients smaller-or-equal DCLab authority | MCP tools/resources/prompts over public SDK | Read-only default; exact-approved writes; kill switches | Scope 5 API/SDK and Scope 3 commands | Not implemented | Never a second tool authority or internal agent loopback |
| Connector framework | Supporting MVP capability | Reuse one ingestion contract across upload, objects, SQL, CRM, Snowflake | ConnectionDefinition, CredentialRef, SyncPlan/Run, checkpoint, schema snapshot | Scheduler/worker publishes only after validation | Secrets, egress, `dlt`, object storage | Architecture planned; full pack not implemented | Airbyte/Openflow only interoperability, not DCLab control plane |
| Business administration | Business extension | Add team, membership, domains, technical visibility, governance and capabilities | BusinessProfile, WorkspaceDomain, Membership, Capability, Entitlement | Role/capability-aware UI and API | Shared Core resources and authorization | Substantially implemented | Must not duplicate ML records or engine |
| Recommendations, actions and outcomes | Future business core | Connect prediction to candidate actions and measured outcomes | DecisionCase, RecommendationVersion, ActionProposal/Execution, OutcomeObservation, ImpactAssessment | Exact approval and outbox; causal evidence-class restrictions | Core ML, connectors, approvals | Earlier decision ledger/simulation prototypes exist; canonical Scope 8 future | Not required for first ML/DS pilot; no causal overclaim |
| Cloud and compute portability | Supporting production platform | Run same product on AWS or GCP and optional external compute | DeploymentProfile, compute target/profile, grant, quote, attempt, transfer | Placement policy chooses only approved capabilities | Kubernetes/OpenTofu, secrets, identity, storage | Architecture only; local Docker/Compose exists, no production proof | No active-active dual-cloud, automatic fallback or provider-owned truth |

### 6.2 Essential core versus optional layers

**Essential now:** secure tenancy, project/lifecycle, governed data, valid scientific plan, deterministic experiments, evidence, decisions, batch release/monitoring, and primary Data Scientist/ML Engineer workflows.

**MVP workstreams that may be independently disabled while developing:** agents, Deep Investigation, notebook Python, hosted MCP, optional external compute, individual connectors. The production-MVP claim requires the gates explicitly named in Scope 9, but the deterministic golden path must work without any agent runtime.

**Later layers:** online serving, streaming, autonomous retraining, large distributed workloads, proactive business actions, causal impact automation, broad enterprise identity, third-party extension marketplace, and general cross-project learning.

---

## 7. Intelligence and Agent System

### 7.1 Confirmed authority model

[CONFIRMED] DCLab is agent-first but never LLM-authoritative. Every durable agent run selects exactly one loop owner:

- `langgraph` — the pinned raw LangGraph lifecycle-supervisor runtime.
- `deep_investigation` — a separately deployed proposal-only long-horizon investigation worker.
- `openai_agents` — an optional allowlisted whole-run hosted/sandbox adapter after its beta gate.

These runtimes never nest, invoke, checkpoint, or authorize each other. Cross-runtime work is a new DCLab request with independent authorization, budget, citations, state, and terminal outcome.

### 7.2 Specialist topology

| Specialist/capability | Responsibility | Inputs | Typed output | May not do |
| --- | --- | --- | --- | --- |
| Supervisor | Decompose objective, order dependencies, allocate budget, resolve conflicts, synthesize | Objective, policy, lifecycle projection, specialist availability | Task DAG, synthesis, escalation | Override deterministic verifier; widen authority; silently vote through conflict |
| Dataset Investigator/Steward | Inspect source, schema, profile, quality, classification, readiness and drift | Authorized DatasetVersion metadata/profile | Findings, mapping/readiness proposals, questions | Read raw rows outside typed bounds; publish data; merge identities |
| Problem and Experiment Planner | Define objective, target, horizon, constraints, metric, validation, holdout and candidate budget | Problem context, data findings, user constraints | ProblemSpec/ExperimentPlan proposal | Guess material ambiguity; launch run |
| Preparation and Feature Reviewer | Review cleaning, roles, feature availability and transformations | Preparation evidence and feature lineage | Versioned plan diff | Fit on holdout; execute arbitrary code |
| Leakage and Validation Critic | Challenge temporal leakage, contamination, split and metric validity | Scientific plan and findings | Cited risks, blocks and alternatives | Mark a deterministic failure safe without evidence |
| Experiment Director | Track run stages, failures, branches and bounded next attempts | Run state, budget, candidate evidence | Retry/child-experiment proposal | Modify locked running plan; reuse holdout for tuning |
| Candidate and Metric Critic | Compare candidates, calibration, thresholds, stability, cost and segments | Evaluations, metrics, slices, cost | Ranked tradeoffs and constraint status | Promote model or fabricate missing metrics |
| Artifact and Provenance Auditor | Reconcile digests, environment, code, artifacts and reports | Artifact/lineage projection | Gaps, reproduction and repair proposal | Mutate or publish artifacts directly |
| Reporter | Produce technical and audience-safe explanation from same evidence | Cited lifecycle/decision state | Technical report, business summary | Expose restricted raw detail or hidden reasoning |
| MLOps Release Auditor | Preflight package, feature contract, batch release and rollback | Model/package/release evidence | Release/rollback proposal | Deploy, promote or roll back without command/approval |
| Operations/Drift Investigator | Diagnose batch, input/prediction/performance drift, latency and cost | Monitoring windows and run evidence | Cited remediation/retraining/rollback proposal | Claim performance without labels; execute remediation |

### 7.3 Planner, executor and reviewer relationship

The planner creates a typed proposal. The deterministic executor validates it and creates an exact command resource. A reviewer—human or separate bounded critic—assesses evidence. Approval binds the exact resource versions, payload digest, budget, and intended action. Editing the payload invalidates approval. The executor does not accept natural-language authority from the agent.

### 7.4 Context and memory

Agent context is an immutable, bounded `ContextEnvelope` built by DCLab after authorization. It contains resource IDs, versions, digests, safe summaries, data classifications, relevant decisions, policy versions, and citations—not unrestricted database access or a hidden dump of project history.

Project memory is an immutable `ProjectDecisionRecord` with subject, state, actor, facts/hypotheses, evidence, alternatives, rationale, resulting resources, and supersession link. Agent messages and checkpoints may support conversation/resume but are not trusted memory. Vector search, if adopted, is only an index over authoritative records.

### 7.5 Tool use and permission boundaries

Every tool is code-registered and versioned. It has input/output schemas, risk class, required capability, data class, audience, time/output/cost bounds, idempotency behavior, and kill switch. `ToolRunner` reauthorizes each invocation against current membership and resource versions.

The Scope 1 catalog is read-only: identity, project, dataset metadata/profile, lifecycle/build state, completed evidence, artifact metadata, and safe summaries. Scope 3 activates exact typed commands. Agents never receive a database session, object-store credential, provider secret, arbitrary SQL, host filesystem, cloud API, shell, or unbounded HTTP tool.

### 7.6 Human-in-the-loop points

Human confirmation or explicit policy is required for:

- ambiguous target/entity/time/meaning;
- sensitive raw-data access or export;
- material scientific-plan changes;
- significant compute reservation;
- model promotion and production release;
- external writes or business actions;
- deletion, rollback, credential or entitlement changes;
- exception to security/data-residency policy.

### 7.7 Failure handling and observability

Agent runs have durable states, steps, tool calls, events, citations, budget reservations/settlements, model/prompt/tool/data-policy versions, checkpoint references, deadlines, leases, cancellation, and terminal reasons. Provider failure, process loss, invalid structured output, stale resource, revoked permission, budget exhaustion, or unsafe tool request must end safely or resume from the last committed turn without repeating side effects.

Agent quality is evaluated through deterministic fakes, golden tasks, citation validity, hallucination/error taxonomy, security adversarial tests, cost/latency, recovery, specialist ablation, counterfactual replay, and user task completion. A specialist is promoted only when its measured value justifies additional cost and latency.

### 7.8 Learning from previous projects

[PROPOSED] Agents may retrieve patterns, templates, accepted decisions, and aggregate outcomes only within authorized tenant/purpose boundaries. Reuse is a cited suggestion, not an automatic transfer of validity. Customer data and learned project context must not cross tenants. Feedback enters an evaluation dataset or future proposal; it never silently rewrites prompts, policies, or models.

### 7.9 Autonomy levels

| Level | Authority | Product position |
| --- | --- | --- |
| L0 | Observe and explain | Required MVP |
| L1 | Propose and audit | Required MVP |
| L2 | Simulate/preflight and request exact command | Required MVP |
| L3 | Execute an allowlisted reversible command under policy/approval | Narrow MVP subset |
| L4 | Run one bounded experiment-improvement loop | One measured MVP loop |
| L5 | Operate production workflows under standing policy | Long-term, per-capability promotion only |

---

## 8. Scientific and Experimentation Core

### 8.1 Scientific method

1. **Question and hypothesis:** State the business/scientific question, entity, outcome, prediction moment, horizon, constraints, and what evidence would change the decision.
2. **Data fitness:** Verify provenance, access purpose, schema, timestamps, label availability, quality, sampling and representativeness.
3. **Baseline first:** Establish a simple heuristic or model before expanding complexity.
4. **Holdout discipline:** Lock an untouched final test/holdout before candidate selection.
5. **Validation selection:** Use temporal, grouped, stratified, or ordinary split based on how predictions will be used.
6. **Feature availability:** Every feature must be available at prediction time and have lineage.
7. **Bounded candidate portfolio:** Choose a justified mix of baselines, algorithms, feature groups and hyperparameters; stop on budget, instability or insufficient improvement.
8. **Selection:** Rank on validation/CV evidence and declared primary/secondary metrics; include calibration, threshold, cost and subgroup behavior when applicable.
9. **Final evaluation:** Evaluate the locked winner once on final holdout; rejected candidates are not evaluated on it.
10. **Decision and package:** Record acceptance/rejection rationale, limitations, code/environment/artifacts and safe model format.
11. **Operational validation:** Validate feature contract and input/output behavior for batch inference; monitor drift and performance when labels arrive.

### 8.2 Feature engineering

Feature families may include customer/entity, transaction, behavior, temporal, marketing, product, review/text-derived, statistical aggregates, ratios, recency/frequency/value, trends, interactions and domain-specific transformations. With seven feature groups there are mathematically 127 non-empty combinations, but DCLab does not run all combinations by default. Search must be bounded by data size, prediction semantics, leakage risk, cost, diversity, prior evidence, and explicit budget.

Feature lineage records source columns, transformation, version, availability time, parent features, code/environment, and producing run. Feature reuse requires compatibility evidence, not name matching.

### 8.3 Candidate generation and model families

The current deterministic engine supports structured classification/regression foundations and a registry of candidate plans. The target architecture can add tree ensembles, linear/generalized baselines, calibrated classifiers, deep tabular/time-series challengers, and later distributed/GPU methods where evidence justifies them. Model-family count is not a success metric.

### 8.4 Benchmarking and statistical validation

- Compare against simple and existing-process baselines.
- Report central metric plus variability across folds/seeds where applicable.
- Use confidence intervals or uncertainty estimates appropriate to the design.
- Preserve class imbalance, calibration and threshold tradeoffs.
- Evaluate business capacity metrics such as recall at a fixed review budget.
- Track stability across time, segments, folds, and representative datasets.
- Separate retrospective benchmark success from prospective production or causal evidence.

### 8.5 Repeated experiments and negative results

Each attempt creates immutable lineage to parent hypothesis/change digest, data, plan, code, environment and budget. Failed and non-improving runs remain visible with terminal reason. A child experiment never overwrites its parent. Stop conditions include target satisfaction, budget/time/depth, insufficient improvement, instability, scientific risk, cancellation and policy revocation.

### 8.6 Evidence aggregation and promotion

The selected model is an explicit `ModelSelectionDecision`, not an inferred JSON winner. Final holdout evidence is a distinct evaluation scope. Promotion requires complete required telemetry, digest-verified safe package, generated feature-contract validation, acceptable deterministic verification, authorization and approval. Native/permutation importance may be displayed with methodological labels; SHAP is excluded from the production MVP pending a separate measured ADR.

### 8.7 Relationship to the R&D repository

The separate R&D/experiments repository may explore new model families, features, case studies, evaluation methods and scientific hypotheses. It is a supporting research subsystem, not the DCLab product, tenant database, production job system, release authority, or customer UI. Promotion from R&D into DCLab requires:

1. a defined hypothesis and benchmark;
2. reproducible code/data/environment evidence;
3. security/license/privacy review;
4. integration behind the existing candidate/service contract;
5. regression, scientific, cost and operational tests; and
6. a reviewed release decision.

No R&D result becomes a production capability merely because it exists or scores well on one dataset.

### 8.8 Earlier layered Decision Intelligence vision

[CONFIRMED AS FUTURE, NOT MVP] The older vision includes vertical prediction layers, recommendation layers, outcome/reaction prediction, simulation and a final decision engine. It also describes a future “horizontal intelligence” layer that filters or coordinates vertical layers. This remains conceptually valuable but is downstream of the trustworthy Core ML lifecycle and Scope 8. Prediction, recommendation, action, outcome, and causal impact must remain separate domain concepts.

---

## 9. Data and Knowledge Architecture

### 9.1 Canonical lineage

```text
Workspace
  → Project
    → ProblemSpec
      → DataSource
        → DataAccess
          → IngestionRun
            → DatasetAsset
              → DatasetVersion (current physical table: datasets)
                → DatasetColumn / DatasetProfile / DataQualityFinding
                → FeatureSetVersion / Preparation / Validation plan
                  → WorkflowRun
                    → PipelineRun (current physical table: experiments)
                      → Candidates / folds / evaluations / selection
                        → ModelAsset → ModelVersion
                          → BatchPrediction / MonitoringWindow [target extension]
```

### 9.2 Dataset lifecycle

A logical `DatasetAsset` owns immutable physical/versioned `Dataset` rows. Each version carries workspace ownership, content digest, schema/row/column metadata and artifact location. A new body creates a new version; the ORM must not mutate an existing dataset body. Upload compatibility records must link into this lineage rather than create a second ingest engine.

Source authorization, access purpose, classification, retention, deletion and freshness belong to explicit records/policies. Bytes live in private object storage. PostgreSQL stores identity, digests, safe metadata, lineage and state—not large blobs.

### 9.3 Schema, mappings and validation

[CONFLICT RESOLVED] Earlier conversations considered a broad automatic canonical business model. The accepted safe direction is assisted, versioned mapping. DCLab may propose semantic roles, but important identity, join, target, unit, timestamp and metric meanings require evidence and human confirmation. Equal-looking IDs from different systems are namespaced; identity relationships are reversible and confidence/evidence-backed.

Each approved feature/model input contract is DCLab-owned and versioned. Pandera may execute a generated dataframe schema but does not own the public contract.

### 9.4 Lineage types

- **Data lineage:** source/config/access → ingestion → immutable dataset/artifact.
- **Feature lineage:** dataset columns/parent features → transformations → versioned features.
- **Experiment lineage:** workflow invocation → pipeline run → candidates/folds/evaluations → winner.
- **Model lineage:** selected candidate → code/environment/package → model version/release.
- **Artifact lineage:** producer, inputs, digest, storage version, classification and retention.
- **Decision lineage:** evidence and alternatives → accepted/rejected/superseded record → resulting resources.
- **Agent lineage:** runtime/prompt/model/tool/data-policy/budget versions → steps/calls/citations/proposals.

### 9.5 Knowledge and project memory

The project knowledge layer consists of immutable domain records, decisions, findings, reports, citations and versioned summaries. Search may combine structured filters, PostgreSQL full text and—after measured need—pgvector. A graph projection may expose dependencies, but PostgreSQL foreign keys and lifecycle services remain authoritative. Framework memory and embeddings are never the only copy.

### 9.6 Cross-project learning

[PROPOSED] DCLab may learn reusable patterns such as successful validation plans, feature templates, failure signatures and operating defaults. Any cross-project or aggregate learning requires tenant consent, purpose limitation, privacy analysis, deletion propagation, provenance, evaluation and an explicit product policy. Default behavior is project/workspace isolation.

### 9.7 Privacy, retention and deletion

Every data-bearing artifact is classified and governed by workspace policy. Context builders default to metadata-only and fail closed on unknown classification. Deletion must cover database references, object versions, caches, external-provider staging, MLflow artifacts, agent context/checkpoints, search indexes and backups according to policy. Audit/evidence retention may require tombstones or minimal immutable records rather than silently deleting accountability.

### 9.8 Tenant isolation

`Workspace` is the canonical tenant boundary. All tenant-owned resources carry workspace ownership and, for critical edges, composite database constraints. The server resolves active membership from persisted rows. Foreign IDs supplied by callers are selectors only. After selecting an authorized workspace, missing or foreign object IDs return `404` to prevent existence disclosure.

---

## 10. Technical Architecture

### 10.1 Target topology

```text
Browser / SDK / CLI / MCP client
            ↓
Next.js BFF (browser) or versioned FastAPI `/v1`
            ↓
Application services: authorization, lifecycle, commands, approvals,
agent runs, data access, jobs, releases, monitoring
            ↓
PostgreSQL durable state + private immutable object storage
            ↓
Durable worker lanes: deterministic ML/data, agent, investigation,
connector, notebook coordinator, isolated Python, batch inference
            ↓
Private adapters: MLflow, dlt, DuckDB/Arrow, Pandera, Evidently,
OpenTelemetry, AWS/GCP services and approved external compute
```

### 10.2 Major components

| Component | Responsibility and interfaces | Stored state | Trust boundary and failure behavior | Scaling concerns |
| --- | --- | --- | --- | --- |
| Next.js web/BFF | Product UI, HttpOnly session origin, same-origin API proxy, bounded streaming | Browser presentation/cache only | Never authorization authority; API-authored cookie attributes preserved; bodyless responses handled correctly | Horizontal stateless web; streaming connection bounds |
| FastAPI application | `/auth`, `/app`, `/business`, `/admin`, `/development`, future `/v1`; validates and calls services | No private alternate state | Fails closed on auth/config/schema; returns uniform safe errors | Bounded DB pools, request timeouts, pagination |
| Authorization service | Resolve platform/workspace memberships, capabilities and active workspace | Membership/capability rows in PostgreSQL | Rechecked at request/tool execution; UI/JWT role not authority | Cache only with bounded invalidation |
| PostgreSQL | Canonical lifecycle, jobs, approvals, audit, decisions, metadata | Product truth and transactional intent | HA/recovery does not preserve notebook RAM; migrations and integrity gates mandatory | Index/query evidence first; PgBouncer/read replicas/partitioning only when triggered |
| Object storage | Immutable datasets, source, packages, reports, logs, predictions and manifests | Bodies and provider versions | Private; digests/preconditions verified; signed access authorized and short-lived | Streaming, lifecycle/retention, reconciliation, large object cost |
| Existing job system | Durable intents, leases, attempts, heartbeat, retry/cancel/recovery | ExecutionRequest, MlJob and linked run state | Duplicate delivery safe; late/unknown result quarantined | PostgreSQL queue until SLO evidence requires broker |
| Deterministic ML worker | Profiling/preparation/training/evaluation/reporting | Canonical results via services; artifacts via storage | No agent authority; locked plans and evidence | Resource-class workers, CPU/GPU placement later |
| `DataScanPort` [PROPOSED] | Code-owned bounded scan operations | No persistent DuckDB catalog | Fresh hardened DuckDB, authorized path only, Arrow output; network/extensions/arbitrary SQL denied | Memory/thread/input/output/temp/time limits |
| MLflow adapter [PROPOSED] | Detailed run telemetry and model-package metadata | Separate private MLflow DB/artifacts | Missing required tracking blocks verification/promotion, not DCLab state | Reconciliation, lifecycle and separate identity |
| Agent worker [PROPOSED] | Raw LangGraph lifecycle supervisor/specialists | DCLab run/step/events plus private checkpoint refs | One external operation per durable turn; no runtime nesting | Token/tool/cost/time budgets, checkpoint throughput |
| Investigation worker [PROPOSED] | Three Deep Investigation modes | Job-scoped transient state; validated proposal in DCLab | No DB/object/provider credentials or mutation tools | Separate dependency/image and queue lane |
| Notebook coordinator [PROPOSED] | Provision/reconcile/interrupt/destroy private Jupyter sandboxes | RuntimeSession/Epoch/Execution in PostgreSQL | Trusted coordinator; browser never sees Jupyter/Kubernetes token | Admission, idle cleanup, session quotas and provider capacity |
| Jupyter sandbox [PROPOSED] | Stateful Python within one authorized epoch | Ephemeral RAM/scratch; saved outputs through DCLab | Untrusted, no product credentials/default network, strict quotas | One active session/user initially; CPU-first |
| Connector worker [PROPOSED] | Bounded extraction/incremental sync with `dlt` | Opaque engine checkpoint linked to DCLab SyncRun | Read-only MVP; secrets brokered; schema drift quarantined | Rate/backpressure/source quotas and schedules |
| Batch inference/release [PROPOSED] | Load verified package, validate schema, produce predictions | Release/batch/monitoring records and artifacts | Runs outside FastAPI; safe format only; rollback by immutable version | Bounded parallelism; online serving later |
| OpenTelemetry pipeline [PROPOSED] | Export redacted operational signals | External telemetry, not product truth | No raw customer payloads/high-cardinality secrets | Stable semantic conventions and SLOs |
| AWS/GCP infrastructure [PROPOSED] | EKS/GKE, RDS/Cloud SQL, S3/GCS, secrets/KMS, ingress, telemetry | Provider resources and deployment evidence | Provider identifiers remain private adapter/IaC detail | Same signed release, independent readiness and restore proof |
| External compute adapters [PROPOSED] | Place approved work on Runpod/Railway/Lambda GPU/Vast/Nebius | Provider refs/quotes/attempts mapped to DCLab | Provider never owns lifecycle; no automatic fallback | Capacity volatility, transfer, cleanup, spend, capability matrix |

### 10.3 Authentication and authorization

Browser authentication uses an opaque random session token stored only as a SHA-256 hash and issued in an HttpOnly cookie with explicit SameSite/path and deployed-mode Secure. Browser API access goes through `apps/web/app/api/backend/[...path]/route.ts`; browser JavaScript never reads or attaches a long-lived bearer. Non-browser clients use the explicit token flow. Rotation revokes the predecessor; logout and mass revocation are supported. CSRF/CSP/throttling/recovery/kill-switch behavior is part of the verified Scope 0 security package.

### 10.4 Multi-tenancy and product surfaces

- `/admin/*` — DCLab platform administration; admin write, developer read-only.
- `/business/*` — Business organization/technical administration, workspace and capability scoped.
- `/development/*` — full ML engineering experience for Personal or authorized Business workspaces; currently a placeholder route in live code.
- `/app/*` — existing translated client/decision-intelligence experience; do not expose raw ML details by weakening its contract.
- `/lab/*` — existing Labs compatibility experience linked to canonical runs.
- `/v1/*` — stable public product API as it is expanded; current contract is partial.

### 10.5 Repository boundaries

- `apps/api/app/` — API, domain/services, deterministic ML, workers and adapters.
- `apps/web/` — Next.js product/marketing UI and BFF.
- `packages/dclab_client/` — HTTP-only Python client; must not import backend internals.
- `configs/` — versioned task/layer/policy/experiment configuration.
- `contracts/` — generated canonical schema/API/repository facts.
- `docs/verification/` — CURRENT/HISTORICAL evidence ledger.
- `docs/agentic-program/` — target concept, ordered implementation program and detailed prompt packets.
- `data/` and `artifacts/` — samples/ignored runtime outputs under explicit repository rules.
- [PROPOSED] `infra/tofu/`, `infra/kubernetes/` and provider adapter packages when Scope 9 begins.

### 10.6 Local versus cloud execution

Local Docker/Compose and direct development remain supported for development and deterministic tests. Deployed arbitrary/user Python may never fall back to the FastAPI process or an ordinary worker. Production runtime admission stays disabled unless an approved sandbox backend exists. One environment has one platform home, AWS or GCP; external compute is per-job placement, not a second control plane.

### 10.7 Explicit undecided areas

- [UNDECIDED] First pilot CRM: HubSpot or Salesforce.
- [UNDECIDED] First certified external GPU provider; Runpod is a reasonable proposal, not a completed decision.
- [UNDECIDED] Exact production primary cloud and region for the first pilot.
- [UNDECIDED] Whether online inference is required in the first paid pilot.
- [UNDECIDED] Final LLM model/provider portfolio and pricing policy.
- [UNDECIDED] Which advanced model families earn inclusion beyond current tabular foundations.

---

## 11. Canonical Domain Model

The live generated registry currently contains 66 SQLAlchemy tables. The table below lists the important domain owners rather than reproducing every compatibility or join table. Key fields are conceptual summaries; migrations and ORM models remain the exact schema authority.

| Entity | Definition and key fields | Relationships | Lifecycle / ownership / mutability / audit |
| --- | --- | --- | --- |
| `User` | Human identity; email, status, legacy default workspace/role hints | Sessions; workspace/platform memberships | Global identity; mutable status/profile; permissions come from memberships, not token/legacy role alone |
| `AuthSession` | Hashed opaque browser session; token hash, user, selected workspace, idle/absolute expiry, rotation/revocation | User, selected Workspace, predecessor/successor | `active`, `revoked`, `expired`; mutable lifecycle under session service; security audit required |
| `Workspace` | Canonical tenant/resource boundary; kind, name, status, limits | Projects, memberships, entitlements, domains and all tenant resources | `personal` or `business`; no cross-workspace ownership changes; deletion policy controlled |
| `WorkspaceMembership` | User authority in workspace; role/status | User and Workspace | Canonical roles: owner/admin/ml_engineer/viewer; legacy role values readable; membership changes audited |
| `PlatformMembership` | DCLab staff authority | User | `dclab_admin` or read-only `dclab_developer`; separate from customer seats |
| `WorkspaceEntitlement` | Plan/feature/seat/resource allowance | Workspace | Versioned policy/config; does not itself prove authorization for an object |
| `WorkspaceCapability` | Workspace-specific feature exposure | Workspace | Backend-enforced; platform roles may bypass according to policy; changes audited |
| `BusinessProfile` | Business-only legal/industry metadata | One-to-one Business Workspace | Not created for Personal workspace; mutable admin record |
| `BusinessDomain` / `WorkspaceDomain` | Global domain catalog and per-workspace enable/config | Workspace, workflows | Data-driven, not fixed enums or database schemas; configuration versioned/audited |
| `Project` | First-class ML case/investigation | Workspace, ProblemSpecs, datasets/runs/models/notebooks | Tenant-owned; archived rather than repurposed; active project context for lifecycle |
| `ProblemSpec` | Versioned intent: task, target, horizon, entity/time, metrics, constraints | Project; upstream of prediction task/plan | New version for material change; accepted state linked to decision evidence |
| `DataSource` | Logical origin of data | Workspace/Project, DataAccess, IngestionRun | Config/version ownership; no embedded plaintext secret |
| `DataAccess` / `DataAccessEvent` | Authorized purpose/scopes/classification for reading source/artifact | Source, user/service, ingestion or scan | Mutable grant with revocation/expiry; append-only access events |
| `IngestionRun` | One attempt to ingest and publish data | DataSource/Access, Dataset, jobs/events | Queued/running/succeeded/failed/cancelled/quarantined; retry creates attempt/lineage, not overwrite |
| `Artifact` | Metadata for an immutable object body; key/ref, digest, size, media/classification | Dataset, code, model, report, prediction and manifests | Body immutable; access controlled; deletion/retention/reconciliation audited |
| `DatasetAsset` | Logical workspace-owned dataset | Multiple Dataset versions | Mutable metadata; physical versions immutable |
| `Dataset` | Immutable physical dataset version; version, digest, schema/shape/location | Asset, columns/profiles, workflow inputs, pipeline runs | Append-only content; uniqueness by version/digest; workspace constrained |
| `DatasetColumn` / `DatasetProfile` | Versioned schema/statistical description | Dataset | Deterministically derived; regeneration creates/version-links evidence rather than changing source |
| `DataQualityFinding` | Missingness, duplicates, imbalance, leakage candidate or other finding | Dataset/PipelineRun | Typed severity/status/evidence; review outcome preserved |
| `DataPreparationDecision` | Cleaning, imputation, role or exclusion decision | Dataset/PipelineRun | Accepted/rejected/kept choice with rationale and source; immutable evidence |
| `PredictionTask` | Compatibility/engine task representation | Dataset/Experiment/ProblemSpec mapping | Must not replace ProblemSpec; task semantics locked per run |
| `MlWorkflow` | Reusable project/workspace ML workflow definition | Optional WorkspaceDomain, versions and runs | Versioned/configured; domain may be null for Personal Development |
| `WorkflowVersion` | Immutable version of workflow definition | MlWorkflow | Append-only; exact version binds run |
| `WorkflowRun` | One invocation of a workflow | Workflow, ProblemSpec, inputs, PipelineRuns, source upload | Requested/running/succeeded/failed/cancelled; every accepted upload gets distinct run |
| `WorkflowRunInput` | Dataset bound to a run with role such as training/scoring/reference | WorkflowRun and Dataset | Immutable binding after execution lock; cross-workspace DB/service checks |
| `Pipeline` / `PipelineVersion` | Reusable technical pipeline definition and immutable version | Workflow/Project, PipelineRuns | Definition versioned; not a run or candidate |
| `Experiment` (PipelineRun) | One technical pipeline execution | WorkflowRun, Dataset, candidates, stages, scientific plan, selected model | Durable shell persists even on failure; terminal state/reason recorded; existing physical table retained |
| `PipelineScientificPlan` | Locked holdout, validation, metric and scientific contract | PipelineRun | Immutable/lock-protected; material change creates new plan/run branch |
| `PipelineStageRun` | State/timing/result for a real pipeline stage | PipelineRun | Pending/running/succeeded/failed/skipped/cancelled; only emitted when operation exists |
| `FeatureSet` / `FeatureSetVersion` | Logical and immutable versioned feature collection | Dataset/Project/PipelineRun and Features | Versioned; selected version bound to scientific plan/run |
| `Feature` / `FeatureTransformation` / `FeatureLineage` | Feature definition, computation and parents | FeatureSetVersion, dataset columns/features | Append-only lineage; availability and point-in-time semantics auditable |
| `PreprocessingStep` | Train/fold-only preprocessing specification and evidence | PipelineRun/FeatureSet | Order/config/fitted scope recorded; no test/holdout fitting |
| `ExperimentCandidate` | One model/feature/hyperparameter alternative | PipelineRun, hyperparameters, folds, evaluations | Pending/running/succeeded/failed/rejected; payload/evidence immutable after completion |
| `ModelHyperparameter` | Queryable candidate parameter | Candidate | Immutable per candidate |
| `CVFoldRun` | Per-candidate fold execution and metrics | Candidate | Durable fold status/timing/metrics; failed fold preserved |
| `ModelEvaluation` / `EvaluationMetric` | Normalized evaluation and metrics with scope | Candidate/ModelVersion/PipelineRun | CV/validation/final_holdout/monitoring scopes explicit; final holdout only after selection lock |
| `ModelSelectionDecision` | Explicit winner lock based on allowed evidence | PipelineRun and Candidate | Immutable; one canonical lock per run; rationale/citations recorded |
| `ExperimentTestPrediction` | Holdout prediction evidence | Candidate/PipelineRun | Immutable and protected from tuning use |
| `RuntimeEnvironment` | Global reproducibility fingerprint/image/dependencies | CodeSnapshot/ModelVersion/runs | Immutable fingerprint; not a live notebook session |
| `CodeSnapshot` | Immutable source and dependency-lock artifacts for execution | Workspace/PipelineRun/ModelVersion | Digest-protected; immutable |
| `ModelAsset` | Logical managed model | Workspace, Workflow, versions | Mutable metadata/status; no duplicate Personal/Business registry |
| `ModelVersion` | Immutable selected/package version with complete lineage | Asset, Candidate, PipelineRun, WorkflowRun, Dataset, Code/Environment, Artifact | Append-only; at most one per selected pipeline result; promotion/release separate |
| `ExecutionRequest` | Idempotent durable command intent | Workspace/project, jobs and domain resources | Requested/accepted/running/terminal; payload digest and idempotency conflict rules |
| `MlJob` | Queue item/attempt handler with lease and heartbeat | ExecutionRequest and resulting run | Queued/leased/running/succeeded/failed/cancelled; bounded payload; retry ownership explicit |
| `MlRunEvent` | Append-only replayable pipeline event | Workspace, WorkflowRun, PipelineRun, sequence | Immutable, ordered per run; sanitized bounded payload |
| `LlmInvocation` | Generic LLM observability ledger | Workspace/run/purpose/provider/model/prompt/evidence digest | Pending/succeeded/failed/invalid; safe structured summary and usage only; not scientific authority |
| `MlRunVerification` | Specialized deterministic/OpenAI audit result | PipelineRun, optional LlmInvocation | Deterministic verification remains authoritative; immutable result/evidence |
| `LabDecisionRecord` | Existing specialized semantic decision record | Run, optional LlmInvocation | Preserved; not the future general ProjectDecisionRecord owner |
| `Visualization` | Versioned chart/report visualization metadata and artifact | Evaluation/run/model | Must state source evidence and safe rendering contract |
| `Prediction` / `Decision` / `Opportunity` | Existing M1 business scoring/decision-ledger entities | Workspace and model/policy evidence | Existing compatibility/business slice; not generic model release or causal action layer |
| `SimulationRun` | Existing simulation/insight run | Workspace/project where available | Tenant-scoped for customer reads; unowned historical rows denied; not complete causal simulator |
| `ProjectDecisionRecord` [PROPOSED] | General immutable project memory for scientific/product decisions | Project and subject resources/citations/resulting versions | Proposed/accepted/rejected/superseded; acceptance never executes |
| Agent entities [PROPOSED] | Definition/version, session/message, run/step, task/delegation, tool call, event, citation, proposal/review, checkpoint ref | Workspace/project/lifecycle resources | Durable bounded run states; immutable versions; no hidden authority |
| Notebook entities [PROPOSED] | Notebook, revision, cell, environment version, input binding, runtime session/epoch, execution/output | Workspace/project/artifacts and optional agent proposal | Revision append/optimistic concurrency; bounded live session; restart creates new epoch |
| Release/monitoring entities [PROPOSED] | ModelRelease, BatchPredictionRun, MonitoringWindow, Alert/Investigation | ModelVersion, input/output artifacts and decisions | Immutable revisions; requested→running→terminal; rollback selects approved prior version |
| Connector entities [PROPOSED] | Connection/config, credential ref, sync plan/run, checkpoint, schema snapshot, webhook receipt | Workspace/source/ingestion/dataset | Secret values external; cursor advances only after atomic publication |
| Action/outcome entities [PROPOSED] | DecisionCase, RecommendationVersion, ActionProposal/Execution, OutcomeObservation, ImpactAssessment, FeedbackSignal | Project/model/evidence and external provider | Prediction/recommendation/action/outcome/impact states remain distinct; approval/outbox required |

### 11.1 Canonical role model

The target customer roles are `workspace_owner`, `workspace_admin`, `ml_engineer`, and `viewer`. Platform roles are `dclab_admin` and `dclab_developer`. `personal_developer`, `business_admin`, `business_developer`, and `client_user` are compatibility identities translated into capabilities; new work should move toward canonical workspace roles without breaking existing records.

### 11.2 Mutability rule

Material scientific/data/model evidence is immutable. Editable product resources create a new version, branch, or superseding record. Operational state may transition through a service with optimistic/version/fence checks. No caller may directly edit a locked run, selected candidate, final evaluation, artifact digest, published dataset body, or model version.

---

## 12. Product UX and Information Architecture

### 12.1 Global information architecture

```text
Public site
  Home · Platform · Solutions · Industries · Pricing · Resources · Company · Login

Authenticated shell
  Workspace selector / search / account
  ├── Development (Personal and authorized Business ML users)
  │   ├── Projects
  │   ├── Data
  │   ├── Investigations / Agent Studio
  │   ├── Experiments
  │   ├── Models / Releases
  │   ├── Monitoring
  │   └── Notebooks
  ├── Business (Business owner/admin as permitted)
  │   ├── Overview
  │   ├── Members / roles / entitlements
  │   ├── Domains / workflows / runs / models
  │   └── Governance / capabilities / usage
  ├── Existing translated client experience (`/app`)
  │   ├── Dashboard · Insights · Opportunities · Decisions · Upload · Labs
  └── Platform administration (`/admin`)
      ├── Businesses · Labs/Experiments · Registry · Monitoring
      └── Cross-workspace read/write according to platform role
```

[CONFIRMED] The `/development` route exists but is currently a simple shared-core placeholder. The final Development IA is target state, not implemented state.

### 12.2 Canonical project workspace

The project workspace should present three synchronized modes without making them three products:

1. **Investigate / Conversation:** objective, chat, agent progress, proposals, citations, approvals and cancellation.
2. **Workflow / Evidence:** lifecycle graph, pipeline stages, experiments, candidate comparison, decisions, models and monitoring.
3. **Implementation:** notebook/code/config/environment/log/artifact details and developer actions.

Deep links carry the same project/resource IDs. Changing a resource in one view updates the shared server state; local UI state never creates another truth.

### 12.3 Agentic notebook layout decision

The latest visual direction uses:

- A compact liquid-glass left application sidebar for global navigation and files/resources.
- A large, calm central project canvas with the investigation title, metadata and execution pipeline.
- A GitHub-like pipeline/status presentation: the main lifecycle pipeline can be horizontal for the current run; the experiment/history rail is vertical with success, active, pending, failed and cancelled markers.
- Code and plot/evidence beside one another where the screen width permits.
- Research chat beneath or adjacent to the central analysis, with enough separation to keep code/results primary.
- A right-side Experiments/History area with a small number of important details and inspectable run results.
- No generic “Suggested” marketing card occupying critical workspace area; suggestions belong in agent chat/proposals.

[CONFLICT] Earlier images put experiment history in the left sidebar; the later explicit request moves experiment progress to a GitHub-like vertical rail while preserving the global navigation sidebar. Treat the later arrangement as the desired notebook/workspace direction.

### 12.4 Visual system

- Apple-like liquid glass: translucent, higher transparency, layered blur, fine light borders, restrained highlights, readable surfaces.
- More white space and simpler background than the first glass concepts.
- Modern sans typography similar to premium SaaS references; current live UI already uses one sans system and small monospace use.
- Rounded cards/panels with moderate radii, not oversized bubbles.
- Latest requested accent colors: blue `#2596be`, green `#21883e` with lighter green variants, dark/light navy support, and restrained matching yellow for pending/warning.
- [CONFLICT] Live code currently uses primary `#2563eb`, cyan `#38bdf8`, and green `#16a34a`. The exact requested `#2596be`/`#21883e` palette is not yet the live token source. Founder confirmation is needed before changing the global palette because earlier instructions also said not to change unpointed elements.
- Avoid random purple, heavy dark cyberpunk styling, giant gradients, excessive glow, low contrast, and glass on every nested element.

### 12.5 Major screens and desired understanding

| Screen | User should understand | Essential states |
| --- | --- | --- |
| Project home | Current objective, authoritative versions, next decision and overall health | New/empty, stale upstream, blocked, active, completed |
| Data | Source, version, classification, schema, quality, freshness and access purpose | Uploading, quarantined, profiling, needs mapping, ready, drifted, deleted |
| Problem/plan | What is being predicted, when, for whom, metric/constraints and what remains ambiguous | Draft, needs input, proposed, approved, superseded, blocked |
| Pipeline/run | Real current stage, elapsed/cost, events, cancel/retry and failure reason | Queued, running, needs input, cancelling, failed, cancelled, recovered, completed |
| Candidate comparison | Baseline, valid candidates, CV-only selection, uncertainty, threshold/business tradeoffs | Partial candidate failures, insufficient evidence, winner locked |
| Model/version | Complete lineage, package/feature/environment verification, release eligibility | Draft, verified, blocked, approved, released, retired |
| Notebook | Revision, active epoch/session, execution order, outputs and provenance | Starting, connected, idle warning, interrupting, restarted, expired, output truncated |
| Agent Studio | Objective, steps, evidence citations, budget, policy, proposals and human decisions | Thinking is not exposed; show progress, tool/result summaries, blocked, cancelled, retryable |
| Monitoring | Reference/current windows, drift type, labels availability, impact and action options | Healthy, warning, alert, insufficient labels, investigating, rollback proposed |
| Business admin | Members, role/capability/entitlement, projects/runs/models/usage | Read-only role, capability disabled, seat/quota reached |
| Platform admin | Tenant-safe operational status and support evidence | Degraded provider, orphan/reconciliation, kill switch, incident mode |

### 12.6 Accessibility and responsiveness

All controls require semantic labels, keyboard navigation, visible focus, adequate touch targets, contrast, reduced-motion behavior and screen-reader status announcements. Desktop, tablet and ~390px mobile views must avoid horizontal overflow. Mobile sidebar is a dismissible drawer; main technical tables need responsive summaries rather than tiny unreadable columns.

### 12.7 Backend-first UI rule

Before adding a visible page, card, metric, button, dropdown or status, determine its real backend owner, permission, request/response schema, source freshness and failure behavior. If functionality is absent, the UI may present an explicitly labeled concept or disabled state, but must not pretend to execute it or fabricate production data.

---

## 13. Trust, Safety, Security, and Governance

### 13.1 Identity, secrets and access

- Browser sessions are opaque, hashed at rest, HttpOnly, rotated/revocable and protected by CSRF/CSP/throttle/recovery controls.
- API/service tokens are separate from browser sessions and scoped/rotatable/revocable as public automation matures.
- Secrets are references to managed secret stores; they do not enter database JSON, job payloads, source control, logs, images, model context or remote user code.
- Every tenant read/write is workspace filtered and capability checked by backend services.
- Internal platform roles never consume customer seats or live on customer membership rows.

### 13.2 Data privacy

- Default LLM context is metadata-only.
- Raw data use requires classification, purpose, scope, policy and provider allowance.
- Prompt injection content is treated as untrusted data and delimited/labelled.
- Unknown classification or provider retention fails closed.
- No raw rows, secrets, prompts, hidden reasoning, signed URLs or internal-only provider identifiers appear in MCP or audience-safe responses.
- Export and download paths perform the same authorization as normal reads and are independently kill-switchable.

### 13.3 Agent and tool governance

- A model suggestion is never an approval.
- A pending tool call is not authority.
- Tool identity, resource versions, capability, risk tier, budget and payload digest are checked at execution time.
- High-risk/sensitive changes require exact approval and separation of duties where policy requires it.
- Agent feedback does not self-update prompt/model/tool/policy releases.
- No agent runtime can start another agent runtime.

### 13.4 Scientific governance

- Deterministic verification is authoritative over semantic/LLM audits.
- Leakage, split contamination, target ambiguity or missing evidence can block production eligibility.
- Candidate selection and final holdout are separated.
- A model is not production eligible because it has a high single metric.
- Prediction, recommendation, action, outcome and causal impact use separate records and language.
- Causal labels require randomized or otherwise defensible identification design and explicit assumptions.

### 13.5 Destructive actions and recovery

Deletion, revocation, release stop, rollback, mass session termination and external actions have typed commands, preconditions, audit, terminal state and documented compensation/recovery. Ambiguous external delivery reconciles before retry. Destructive account-wide provider cleanup by broad filters is prohibited.

### 13.6 Sandbox boundary

Untrusted/user/agent Python runs only in a verified isolated sandbox with immutable image, unprivileged identity, no product database/secret/metadata access, default-deny egress, bounded inputs/outputs and CPU/memory/disk/process/time limits. If the selected substrate cannot prove the contract, Python remains disabled. A notebook kernel is never a model-serving endpoint.

### 13.7 Audit, explainability and evidence quality

Audit identifies actor, principal type, workspace/project, request/command, policy, approval, versions, timestamps and result without storing secrets or unnecessary raw data. Explanations distinguish observed facts, deterministic findings, cited observations, hypotheses, recommendations and unknowns. Native/permutation importance is labelled with its limits; model explanation is not causal explanation.

### 13.8 Compliance posture

[PROPOSED] DCLab should build evidence for privacy, access, retention/deletion, change control, incident response, backup/restore, vulnerability management, software provenance and audit export before claiming a formal certification. No certification claim is currently evidenced.

---

## 14. Business and Market Context

### 14.1 Market category and positioning

[CONFIRMED] Initial positioning: **AI-native ML development and lifecycle workspace for data scientists and ML engineers**. Internally, “Cursor for ML/DS” communicates the wedge. Externally, a precise promise is stronger: turn a question and governed dataset into a validated, reproducible and operational ML result.

The older “AI Decision Intelligence Company” vision remains the longer-term business layer. It should not obscure the developer-first MVP or imply that DCLab already executes autonomous business actions.

### 14.2 Ideal customer profile

The first paying team should:

- have at least one data scientist and one engineering/technical owner;
- work mainly with structured data and recurring batch predictions;
- already feel pain from disconnected experiments, notebooks and production handoffs;
- need repeatability, evidence or governance;
- possess a real, bounded pilot problem and representative data;
- accept weekly founder-led feedback and measurable before/after comparison.

### 14.3 Competitors and alternatives

| Alternative | Strength | DCLab differentiation target |
| --- | --- | --- |
| Jupyter/Colab/VS Code notebooks | Flexible code and familiar workflow | Canonical lifecycle, decisions, evidence, release and recovery beyond cells |
| Deepnote/Hex/Databricks/SageMaker-style workspaces | Collaboration and platform integration | Project memory, governed agent system and connected ML-specific lifecycle |
| MLflow/W&B experiment tracking | Run telemetry and comparison | Full project/decision/approval/release authority, with tracker behind DCLab |
| AutoML platforms | Candidate search and ranking | Problem framing, scientific controls, evidence, code visibility and operations |
| Coding assistants/agents | Code generation and repository edits | Resource-aware ML investigation and typed scientific/operational commands |
| MLOps platforms | Training/deployment infrastructure | Scientist-centered project journey and durable reasons behind choices |
| Consulting/manual internal workflow | Domain judgment and custom work | Repeatable software, continuous project memory, measurable workflow acceleration |

### 14.4 Business model hypotheses

[PROPOSED] Combine a platform subscription with usage charges:

- Personal/free or low-cost tier to attract technical users within strict resource limits.
- Personal paid tier for private projects, greater compute/storage/agent usage and developer automation.
- Business/team plan for shared workspaces, technical seats, administration, governance, collaboration, monitoring and support.
- Usage for training, notebook sessions, storage, batch prediction, external GPU and LLM calls.
- Enterprise terms later for private deployment, SSO/SCIM, CMK, private networking, regional controls, audit/support commitments and custom integrations.

The current domain model carries a default Business technical-seat concept of five ML engineers, but the product should enforce seats through entitlements rather than hard-coded tenancy logic. Final pricing is not confirmed.

### 14.5 Go-to-market and distribution

[CONFIRMED] The founder's preferred motion is technical-user first:

`Free/accessible developer use → real validated experiment → business-readable brief → internal champion → team adoption → paid business pilot`

The initial sales process is founder-led. Recruit three to five design partners, choose one real use case for each, establish its current baseline process, run the complete DCLab journey, measure time/rework/reproducibility/handoffs/confidence/cost, and ask for continued paid use.

### 14.6 Open source and proprietary boundary

[UNCERTAIN] No final open-source strategy was found. A plausible boundary is open/public clients, contracts, integration SDKs or selected deterministic components while keeping the hosted governance, agent operations, product lifecycle and business controls proprietary; this is a recommendation, not a recovered decision.

### 14.7 Defensibility

[INFERRED] Intended defensibility comes from the connected lifecycle, accumulated decision/evidence history, policy/evaluation layer, synchronized developer/product interfaces and trusted operating workflows—not one model, framework or cloud. Cross-customer data advantage is not assumed and must not violate tenant/privacy boundaries.

### 14.8 Long-term company vision

DCLab can grow from the ML/DS workspace into the intelligence layer where teams connect data, predictions, recommendations, approved actions, outcomes and learning. This broader company vision is conditional on winning the technical user and proving scientific/operational trust first.

---

## 15. Current Product State

### 15.1 Current mechanical baseline

The generated working-tree contract observed during this assembly reports:

- Alembic head `0059_auth_session_constraints`, 59 revisions.
- 66 SQLAlchemy tables.
- 160 OpenAPI paths and 167 operations; 13 `/v1` operations.
- 421 backend Python files, 113 backend test files, 160 frontend TypeScript/TSX files, 3 web E2E files.
- Current Git checkout `f71d42d` on `main`, with pre-existing uncommitted planning/truth/document changes.

The last fully documented exact-SHA baseline gate is `91986b9` with 1,036 backend/SDK tests passed, one intentional live-OpenAI skip, 18/18 Playwright tests and successful same-SHA CI. The current working tree is newer; those counts prove the named SHA, not every uncommitted change.

### 15.2 Capability status table

| Capability | Concept only | Designed | Partial | Implemented | Tested | Production-ready | Evidence and gaps |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | --- |
| Deterministic tabular experiment engine |  |  |  | ✓ | ✓ | No | Candidate/fold/evaluation/selection/report path exists; production batch/release gate incomplete |
| Generic upload and canonical lineage |  |  |  | ✓ | ✓ | No | Upload→ingestion→dataset→workflow/pipeline→model lineage implemented; scale/connectors remain |
| Scientific plan, leakage, holdout and verification |  |  |  | ✓ | ✓ | No | Strong tested foundation; bounded DataScan/ML platform adapters still planned |
| Append-only run/LLM observability |  |  |  | ✓ | ✓ | No | `ml_run_events`, `llm_invocations`, verification and monitor UI exist |
| Platform business/workflow/model/pipeline explorer |  |  |  | ✓ | ✓ | No | `/admin` hierarchy and monitor surfaces present |
| Business-scoped technical administration |  |  |  | ✓ | ✓ | No | Shared contracts, role/capability enforcement and adversarial tests documented |
| Personal/Business shared-core identity |  |  |  | ✓ | ✓ | No | Workspace kind/roles/entitlements and `/development` route foundation; full Development product absent |
| Development ML workspace |  | ✓ | ✓ |  |  | No | `/development` is currently a placeholder; target IA and prompts exist |
| Browser HttpOnly session/BFF |  |  |  | ✓ | ✓ | Near foundation-ready | Current S0-P02 evidence; production deployment still needed |
| Workspace selector and tenant scoping |  |  |  | ✓ | ✓ | Near foundation-ready | Current S0-P03/S0-P04 evidence; remaining Scope 0 plans continue |
| Liquid-glass application shell/sidebar |  |  |  | ✓ | ✓ | No | AppShell/AppSidebar/UI primitives and design tokens exist; final notebook layout/palette not complete |
| Canonical lifecycle projection |  | ✓ | ✓ |  |  | No | Existing rows support it; full typed projection/staleness API/UI is Scope 1.0 |
| ProjectDecisionRecord memory | ✓ | ✓ |  |  |  | No | Existing decision tables are specialized, not canonical project memory |
| Read-only DCLab agent | ✓ | ✓ |  |  |  | No | No Agent classes/routes or LangGraph dependency usage found in live code |
| Multi-agent supervisor/specialists | ✓ | ✓ |  |  |  | No | Detailed Scope 2 design only |
| Deep Investigation worker | ✓ | ✓ |  |  |  | No | Approved workstream, not implemented |
| OpenAI Agents runtime adapter | ✓ | ✓ |  |  |  | No | Optional evaluation design only |
| Agentic notebook/Jupyter runtime | ✓ | ✓ |  |  |  | No | Export/reproduction helpers exist; no interactive Notebook/RuntimeSession models or APIs |
| DataScanPort/DuckDB/Arrow boundary | ✓ | ✓ |  |  |  | No | Approved Scope 0.9 design; current profiling still uses existing path |
| Private MLflow/Pandera/Evidently integration | ✓ | ✓ |  |  |  | No | Approved Scope 3.0 design; not production-integrated |
| Safe model release + batch prediction + monitoring rollback |  | ✓ | ✓ |  |  | No | Model/prediction foundations exist; unified release/monitoring path incomplete |
| Public stable `/v1` API/SDK/CLI |  | ✓ | ✓ |  | ✓ in limited scope | No | 13 `/v1` operations and Python client exist; complete resource/command parity/package gates remain |
| Hosted/local MCP | ✓ | ✓ |  |  |  | No | Scope 6 only |
| Production connector pack | ✓ | ✓ | ✓ |  |  | No | Upload and data-source lineage exist; S3/GCS/SQL/CRM/Snowflake pack not complete |
| AWS production deployment | ✓ | ✓ |  |  |  | No | Canonical design, no protected staging proof |
| GCP production deployment | ✓ | ✓ |  |  |  | No | Canonical design, no protected staging proof |
| Runpod/Railway/Lambda/Vast/Nebius compute | ✓ | ✓ |  |  |  | No | Adapter architecture only; no target readiness may be claimed |
| Recommendations/actions/outcomes/impact |  | ✓ | ✓ |  | ✓ for old limited decision slice | No | Existing M1 decision/simulation is not the full Scope 8 model |

### 15.3 Honest conclusion

DCLab has a serious deterministic ML/database/API/UI foundation and verified Scope 0 work. It is not yet the agentic ML/DS product described in the north star. The immediate task is to complete foundation closure, lifecycle projection/decision memory, the read-only agent, controlled commands and one end-to-end Core ML golden path—not to market planned agents, notebook execution, MCP, connector breadth or production cloud parity as existing capabilities.

---

## 16. Roadmap and Scope Structure

### 16.1 Chronological Scope 0–10 program

| Scope | Objective | Main deliverables | Dependencies / exit meaning | Current position |
| --- | --- | --- | --- | --- |
| 0 — Foundation closure | Make the base current, secure, tenant-safe, bounded and reproducible | Truth package; secure session/BFF; workspace/capability authority; tenant leak closure; data classification/retention; `/v1` foundations; CI/dev parity; ADR/risk baseline; bounded DataScan; cloud-portable immutable storage | Blocks authoritative agents and production data use; every gate must be evidence-backed | Plans 0.1 and 0.2 largely VERIFIED; 0.3/0.4 foundations exist; remaining plans and exact live status must be executed in order |
| 1 — Durable read-only agent | Let an allowlisted user ask cited project/data/run/evidence questions, reload, reconnect and cancel without writes | Lifecycle projection; decision memory; agent contracts/schema; LLM gateway; data policy/context; services; raw LangGraph run; read tools; API/SDK; Agent Studio; eval/gate | Requires Scope 0; no write/export/connector/code tools | Designed, not implemented |
| 2 — Agentic operating system | Add specialist supervision in shadow/critique/proposal modes | Agent tasks/delegation/review; prompt/model/tool/data operations; Dataset/Problem/Feature/Leakage/Experiment/Artifact/Report/Reliability specialists; supervisor UI/evals; Deep Investigation; optional OpenAI adapter | S2-P11F unblocks Scope 3; S2-P12H required for production MVP | Designed, not implemented |
| 3 — Controlled commands and model operations | Activate selected proposals through exact deterministic commands | ML platform reuse; model-build command; cancel/retry/recovery; approvals; agent write tools; bounded iteration; unified UI/SDK/CLI; scientific gate; batch release/monitor/rollback | Requires DataScan and authoritative Scope 2 gate; preserves deterministic correctness | Designed; some existing command/job/model foundations available |
| 4 — Managed agentic notebook | Add versioned managed cells, then isolated stateful Python | Notebook domain/API/jobs/UI; reviewable agent diffs; Jupyter Server sandbox; security/reproduction gate | Managed cells first; Python disabled until isolation passes | Detailed blueprint only |
| 5 — Public API/SDK/CLI | Complete stable automation surface and signed packages | Service identities/tokens; complete `/v1`; sync/async SDK; CLI; stable JSON/exit codes/keychain; release provenance | Reuses Scope 3 commands; no backend imports or plaintext credentials | Partial client/internal CLI only |
| 6 — MCP | Expose a thin SDK adapter for external agents | Architecture/threat model; local read-only stdio; hosted OAuth Streamable HTTP; selected approved writes; conformance/security/load gate | Requires public SDK; write tools require Scope 3 | Not implemented |
| 7 — Data-integration plane | Deliver upload and initial production connector pack | Quarantine/upload; connection/sync/checkpoint/schema/secret model; adapter contract; `dlt` S3/GCS/SQL/CRM/Snowflake pack; scheduler/reconciliation/drift; UI/CLI/agent integration | Immutable publication and independent kill switches; no silent schema drift | Upload foundation exists; pack not implemented |
| 8 — Recommendations/actions/outcomes | Extend trusted prediction into governed business decisions | DecisionCase/RecommendationVersion; action/outbox; one low-risk provider action; outcomes/impact/feedback; surfaces; causal/privacy/recovery gate | Not required for first ML/DS pilot unless selected | Older prototypes only; canonical layer future |
| 9 — Production platform and pilot | Deploy and prove the complete MVP on AWS and GCP contracts | OpenTofu/Kubernetes; managed PostgreSQL/storage/secrets; workload families; OTel/SLO/runbooks; dual-cloud CI/CD; privacy/security/DR; allowlisted pilot; readiness freeze | Named cross-scope gates plus one Data Scientist and one ML Engineer golden path | Architecture designed, no production proof |
| 10 — Measured scale/autonomy | Add enterprise/scale features only on evidence | Capacity baseline; DB/queue evolution; distributed compute; retrieval benchmark; SSO/SCIM/regions/CMK; more providers; online/streaming and higher autonomy | Each capability needs trigger, ADR, cost/security/migration/eval/pilot/owner | Future |

### 16.2 Dependency-based critical path

```text
Current truth/security/tenancy/data bounds (Scope 0)
  → lifecycle projection + durable decision memory (1.0)
  → read-only agent contracts/state/gateway/context/tools (1.1–1.11)
  → specialist proposal operating system and authoritative gate (2.1–2.11)
  → ML platform reuse + one canonical model-build command (3.0–3.1)
  → cancellation/approval/agent tools/bounded iteration (3.2–3.7)
  → verified package + batch prediction + monitoring + rollback (3.8)
  → production pilot proof (9)
```

Parallel but gated workstreams:

- Connector control and initial pack can progress after its Scope 0/identity/secret dependencies.
- Deep Investigation starts after S2-P11F and can run in parallel with Scope 3.
- Notebook domain/managed cells can progress before arbitrary Python; Python requires the sandbox gate.
- Public API/SDK/CLI follows controlled commands; MCP follows the public SDK.
- Cloud infrastructure can be designed early but cannot claim readiness before the product/runtime gates.

### 16.3 Core ML production-MVP release slice

The cross-scope release must prove:

1. secure tenant/data/scan foundation;
2. first-class lifecycle and decision memory;
3. upload plus production connector pack publishing immutable versions;
4. target/problem/constraint contract;
5. deterministic investigation, leakage and validation;
6. reviewable experiment plan and exact model-build command;
7. bounded baseline/candidate portfolio and comparison;
8. read-only/proposal specialists and all three Deep Investigation modes;
9. one bounded autonomous improvement loop;
10. synchronized conversation/workflow/implementation views;
11. SDK/CLI automation, isolated Python and hosted MCP through their gates;
12. verified model registration, batch prediction, monitoring and rollback; and
13. allowlisted real-user pilot.

### 16.4 What must be built first

1. Finish and re-verify all remaining Scope 0 plans on the live head.
2. Implement `ProjectDecisionRecord` and the lifecycle projection before agent chat.
3. Implement the read-only agent end to end with deterministic fake-provider CI.
4. Complete the deterministic Data Scientist golden path without agents.
5. Add one atomic model-build command and approval/cancellation/recovery.
6. Add agents as proposal clients of that command.
7. Complete package/batch/monitor/rollback before expanding business actions.

### 16.5 What must wait

- Full action automation and causal decision claims.
- Online/streaming global serving.
- Enterprise Gateway and remote kernel fleets.
- Broad external compute marketplace claims.
- Distributed GPU training and foundation-model training.
- Automatic cross-cloud failover or active-active state.
- Unrestricted agents, shells, connectors, third-party MCP servers or autonomous retraining.

### 16.6 Completion and supersession

- Scope 0 evidence IDs retain their original meanings; do not reuse them for new work.
- New migrations must discover the live Alembic head rather than assume historical `0054`/`0058` values.
- Older phase documents and Decision.ai plans remain rationale/history; the current Scope 0–10 plan and `DCLAB_CORE_CONCEPT.md` supersede their order and MVP boundary.
- The R&D repo does not define completion of any product scope.

---

## 17. Decisions and Conflicts Ledger

### 17.1 Decision ledger

| ID | Topic | Decision | Status | Rationale | Source/order | Consequences / follow-up |
| --- | --- | --- | --- | --- | --- | --- |
| D-001 | Brand | Company/product name is DCLab | Confirmed | Founder explicitly corrected name | `Remember Disruption Vision`; later repo | Treat Decision.ai as historical/internal layer name unless founder revives it |
| D-002 | Primary customer | Data scientists and ML engineers are the MVP and long-term core users | Confirmed | Technical-user adoption creates business champions | Later concept/development chats; Core Concept | Prioritize Development golden path over broad executive automation |
| D-003 | Product model | One Shared DCLab Core serves Personal Development and Business | Confirmed | Prevents duplicated engines/data/behavior | Shared-Core Architecture | Business adds governance/team features; no `Business → Personal → Core` dependency |
| D-004 | Unit of work | Canonical unit is connected versioned ML lifecycle/project | Confirmed | Chat/cell/provider state is too fragile | Core Concept | Every surface resolves shared resource IDs |
| D-005 | MVP promise | One end-to-end structured-data path to verified monitored batch model | Confirmed | Testable, sellable and bounded | Business proposal/Core Concept | Defer online serving and broad modalities |
| D-006 | Agent authority | Agent-first does not mean LLM-authoritative | Confirmed | Security/scientific correctness require service authority | Agent-first architecture | All tools use DCLab services/policy/approvals |
| D-007 | Runtime owner | Raw LangGraph is authoritative lifecycle supervisor | Confirmed architecture | Durable code-owned topology and checkpoints | Agent-first architecture | Deep Agents/OpenAI adapters must remain separate |
| D-008 | Deep Investigation | Three proposal-only modes in separate worker | Confirmed future workstream | Open-ended synthesis adds value without command authority | Deep Agents architecture | Gate required before production-MVP release |
| D-009 | Project memory | Curated immutable decisions, not chat/checkpoints | Confirmed | Preserve reasons without treating model state as truth | Core Concept | Implement ProjectDecisionRecord in Scope 1.0 |
| D-010 | Notebook position | Notebook is a synchronized secondary view | Confirmed | Kernel order/RAM cannot own production truth | Core Concept/Jupyter blueprint | Saved revisions/artifacts and import commands required |
| D-011 | Notebook runtime | Start with private Jupyter Server + ipykernel per isolated session | Confirmed design | Smallest realistic stateful execution | Jupyter blueprint | Enterprise Gateway deferred |
| D-012 | Platform cloud | Same complete platform deploys independently to either AWS or GCP | Founder-confirmed | Avoid active-active complexity while preserving portability | User choice/AWS-GCP doc | One authoritative DB/object store per environment |
| D-013 | External compute | Runpod, Railway, Lambda GPU, Vast.ai and Nebius are optional adapters | Confirmed direction, unverified | Broader cost/capacity choice without moving control plane | Founder request/external compute doc | Readiness tracked per exact capability; no blanket claim |
| D-014 | Product database | PostgreSQL is canonical product truth | Confirmed | Transactional lifecycle, tenancy and audit owner | Multiple architecture docs | DuckDB/MLflow/Snowflake/provider state remain non-authoritative |
| D-015 | Artifact storage | Large bodies live in private immutable object storage | Confirmed | Scale and reproducibility without database blobs | Domain/cloud docs | Digests/version/preconditions/reconciliation required |
| D-016 | ML platform reuse | Adopt MLflow, Pandera, Evidently, safe formats, OTel behind DCLab ports | Approved roadmap | Reuse mechanics, retain product meaning | ML Platform architecture | W&B/Optuna/OpenLineage/Polars deferred as specified |
| D-017 | Data scan | DuckDB + Arrow behind typed bounded DataScanPort; pandas for modeling | Approved roadmap | Columnar scale with narrow security boundary | ML Platform/Core Concept | No user/agent SQL, persistent catalog, extensions or network |
| D-018 | Connectors | `dlt` behind DCLab ConnectorPort; initial read-only pack | Approved roadmap | Reuse extraction mechanics, not external control plane | Agent-first/Scope 7 | DCLab owns credentials/state/schema/publication |
| D-019 | UI functional truth | Base44/images guide design; DCLab backend/API/DB guide behavior | Confirmed | Prevent fake UI and regression | Redesign conversation | Missing backend capability must be explicit/disabled |
| D-020 | UI style | Clean light liquid glass, modern sans, white space, left shell | Confirmed direction | Premium technical trust and usability | UI conversation/live shell | Exact latest palette still needs token decision |
| D-021 | Scientific search | Bounded evidence-driven candidates, not a fixed 100×100 mandate | Confirmed correction | More candidates can overfit/waste cost | Concept critiques/Core docs | Candidate budget and stop conditions mandatory |
| D-022 | Canonical mapping | Automated system may propose; human confirms material semantics | Confirmed correction | Universal inference is unsafe | Concept critique/connector architecture | Preserve namespaces/evidence/version/reversibility |
| D-023 | Causal claims | Prediction and causal impact remain distinct | Confirmed | Historical actions are confounded | Product critiques/Scope 8 | Qualifying experiment/design required for causal language |
| D-024 | Developer acquisition | Win ML/DS users first, then Business teams | Confirmed founder strategy | Technical champion creates internal demand | DCLab concept chat/business proposal | Free/accessible Personal funnel plus paid Business pilot |
| D-025 | Business actions | Preserve Decision Intelligence as Scope 8, not Core ML blocker | Confirmed | Valuable later, but broadens risk/scope | Scope plan/Core Concept | Existing business slice remains compatible |
| D-026 | Safety limits | Compute/data/runtime are bounded; “unlimited upload” is not literal | Strong architecture conclusion | Production safety/cost require quotas and streaming | Large-upload request vs scope/runtime docs | Remove arbitrary product caps only with scalable bounded processing |

### 17.2 Conflict table

| Conflict | Sources | Resolution / strongest current decision | Remaining action |
| --- | --- | --- | --- |
| `Decision.ai` vs `DCLab` | Older master/business documents vs founder correction/current repo | DCLab is current brand; Decision.ai is historical concept naming | Rename remaining public-facing stale terminology only through reviewed migration |
| Business-first autonomous growth platform vs ML/DS developer-first workspace | Early AI Decision Intelligence chats vs later founder/Core Concept | Developer-first Core ML product is MVP; business decision layer remains future Scope 8 | Keep business compatibility, do not let it block Core ML pilot |
| DCLab Core equals Developer product vs shared core beneath two products | Earlier developer discussion vs later explicit founder correction | Shared Core is product-independent; Personal and Business consume it | Maintain `/development` and `/business` separation over same services |
| Fixed 100 features/models/layers vs bounded search | Founder exploration vs technical critique/accepted docs | Candidate breadth is hypothesis-driven and budgeted; quality is measured | Expose portfolio rationale/cost/stops in UI |
| Universal automatic canonical model vs assisted mappings | Early integration ideas vs explicit critique and revised architecture | Propose and review mappings; no silent identity/join semantics | Define mapping/version/identity graph owner in connector work packet |
| Notebook as product vs lifecycle as product | “Agentic Colab” shorthand vs Core Concept | Notebook is a central experience but secondary synchronized view | UX must make project/evidence authoritative |
| Enterprise Gateway now vs later | User considered EG; runtime blueprint comparison | Jupyter Server first; Enterprise Gateway only after measured remote-kernel need | Record ADR before later adoption |
| One installation choosing AWS/GCP per workspace vs deploy whole platform to either cloud | Earlier idea vs explicit user choice | Whole installation uses one home cloud; optional job placement external | Product UI must not imply active home-cloud switching |
| Experiment history left sidebar vs right vertical rail | Earlier UI image/comments vs later GitHub workflow request | Global nav stays left; experiments/history use vertical details rail in notebook workspace | Validate final responsive layout with prototype |
| Current blue/cyan tokens vs requested `#2596be` and `#21883e` | Existing code/earlier preserve-colors instruction vs later exact colors | Later colors are design intent, but exact global migration not yet confirmed in code | Founder approves token migration scope before implementation |
| Unlimited files/rows/columns vs enforced runtime limits | User request in Generalize task vs security/scale documents | No arbitrary tiny row cap, but ingestion and execution remain bounded, streamed and quota-controlled | Publish plan limits and scalable large-data path |
| Existing legacy roles vs canonical workspace roles | Older access docs/live compatibility vs canonical domain model | Canonical target roles owner/admin/ml_engineer/viewer; legacy values translated | Complete migration without breaking current members |
| Live verification counts/head | Historical docs name 0027/0039/0053/0058 vs generated 0059 | Generated contracts and CURRENT ledger own live facts | Regenerate at exact source SHA before each gate |

---

## 18. Assumptions, Unknowns, and Questions

### 18.1 Product assumptions

- Technical users will adopt a lifecycle workspace if it saves repeated work without hiding code/control.
- Structured-data batch projects provide enough repeated pain and measurable value for the first wedge.
- Business stakeholders will value reports derived directly from project evidence.
- A developer/free tier can produce internal champions without making infrastructure economics unsustainable.

### 18.2 Technical assumptions

- PostgreSQL jobs can meet MVP queue/SLO needs before introducing a broker.
- A bounded DuckDB/Arrow scan layer can provide needed large-artifact analysis while preserving semantics and security.
- Jupyter Server inside per-session isolation is operationally simpler than Enterprise Gateway for the first runtime.
- One portable Kubernetes application contract can achieve adequate AWS/GCP parity.
- Safe model formats cover the initial model families.

### 18.3 Scientific assumptions

- Existing deterministic modeling behavior remains a strong base across target pilot datasets.
- The selected use cases have enough labels, time semantics and representative data for valid evaluation.
- Bounded agent assistance improves task quality/time without overriding deterministic checks.

### 18.4 Business assumptions

- Data/ML leaders will pay for workflow speed, reproducibility, governance and operational handoff.
- Founder-led design partners can supply safe representative data and a named technical user.
- Subscription plus usage can produce acceptable gross margin once real workloads are measured.

### 18.5 Blocking questions ranked

| Priority | Question | Why it blocks | Required answer/evidence |
| --- | --- | --- | --- |
| P0 | Which exact real user/use case is the first production pilot? | Determines data, connectors, metrics, runtime, cloud and release requirements | Named partner/persona, problem, baseline workflow, data and success criteria |
| P0 | What is the live completion state of every Scope 0 plan on current HEAD? | Scope 1 authority depends on foundation closure | Regenerated truth, plan-by-plan evidence and green exact-SHA CI |
| P0 | What data classifications and provider-retention rules apply to LLM contexts? | Read-only agent can leak data without policy | Founder/security-approved data-policy matrix |
| P0 | Which model/package families and batch inference contract are in the first pilot? | Scope 3.8 and sandbox/format decisions depend on this | Model family, feature schema, output and operational SLO |
| P0 | What is the first production home cloud and region? | Needed for live deployment and sandbox validation | AWS or GCP pilot decision, budget/accounts/residency |
| P1 | Which CRM, if any, is needed for the pilot? | Selects Scope 7 adapter | HubSpot/Salesforce/none based on real demand |
| P1 | Is online inference required in the first commercial offer? | Materially changes serving/security/SLO work | Confirm batch-only or specific endpoint requirement |
| P1 | What are Personal free/paid and Business entitlements? | Controls seats, compute, storage, agents and cost | Product/finance policy with trial limits |
| P1 | What exact palette should become canonical? | Latest image request differs from live tokens | Founder-approved token sheet and migration boundary |
| P1 | What is acceptable LLM/provider/model portfolio? | Cost, latency, retention and quality gates | Approved providers/models, region/retention, fallback/kill-switch policy |
| P1 | Which external compute provider is first? | Five-provider implementation at once is too broad | Select one lane/profile with budget and data class |
| P2 | What may be open sourced? | Affects distribution and architecture packaging | Founder/business decision |
| P2 | Are students a supported free persona at launch? | Affects onboarding, support and quotas | Confirm or defer |
| P2 | When should cross-project learning be enabled? | Privacy and evidence risk | Consent/purpose/evaluation design after single-project MVP |

### 18.6 Missing evidence

- Customer interviews and willingness-to-pay.
- Production-sized dataset benchmarks under the proposed DataScan/runtime architecture.
- Live AWS and GCP staging conformance.
- Live agent quality/cost evaluation.
- Sandbox escape/egress/resource evidence.
- Connector provider conformance and schema-drift recovery.
- Model package/batch release/monitoring/rollback drill.
- Real-user task completion for both a data scientist and ML engineer.

---

## 19. DCLab Terminology

| Term | Canonical definition | What it is not | Related concepts | Status |
| --- | --- | --- | --- | --- |
| DCLab | The complete project-centric ML/DS operating environment | Only the R&D repo, a notebook, AutoML or a chat agent | Shared Core, Personal, Business | Confirmed |
| DCLab Core | Shared domain/services for ML lifecycle, data, runs, evidence, models and operations | The Personal Development frontend | Personal/Business | Confirmed |
| Personal Development | Individual-customer product experience over Core | Separate ML engine or data model | `/development`, Personal workspace | Confirmed |
| Business | Team/organization product over Core plus governance/admin | Wrapper around Personal or second registry | Business workspace, entitlements | Confirmed |
| Project | First-class bounded ML investigation/case | Workflow, experiment, notebook or agent run | ProblemSpec, lifecycle | Confirmed |
| ML lifecycle | Versioned path from problem/data to model release/monitoring | LangGraph execution graph | Lifecycle projection | Confirmed |
| ProblemSpec | Versioned intent and prediction contract | Candidate or training config | Target, horizon, constraints | Confirmed |
| Workflow | Reusable objective/configuration | One invocation or pipeline run | WorkflowVersion/Run | Confirmed |
| WorkflowRun | One invocation coordinating inputs and pipelines | Experiment/candidate | PipelineRun | Confirmed |
| PipelineRun | Technical execution; currently `experiments` table | WorkflowRun or candidate | Stages, candidates, evidence | Confirmed |
| Candidate | One model/feature/hyperparameter alternative | Published model version | CV folds/evaluations | Confirmed |
| ModelAsset | Logical managed model | Candidate or release | ModelVersion | Confirmed |
| ModelVersion | Immutable selected/package version with lineage | Mutable “latest model” file | Release, package | Confirmed |
| DatasetAsset / DatasetVersion | Logical dataset and immutable physical version | Raw upload compatibility row | Artifact, source, ingestion | Confirmed |
| Artifact | Immutable object body metadata/digest | General database blob | Object storage | Confirmed |
| DataScanPort | Typed bounded analytical operations over authorized artifacts | SQL console or user DuckDB | DuckDB/Arrow/pandas | Proposed/approved architecture |
| ProjectDecisionRecord | Immutable accepted/rejected/superseded project memory | Chat transcript or agent checkpoint | Citation, resulting resource | Proposed/confirmed design |
| AgentRun | One durable bounded run with exactly one runtime owner | Project lifecycle or hidden framework session | Steps, tools, citations | Proposed |
| ToolRunner | DCLab service that reauthorizes and executes typed tools | Framework-native unrestricted tool dispatch | Capability, approval, audit | Proposed |
| Deep Investigation | Proposal-only long-horizon investigation in three modes | Supervisor or command runner | Deep Agents | Approved future workstream |
| Notebook | Versioned investigation/implementation view | Project truth or deployment | Revision, session, epoch | Proposed |
| RuntimeSession | Bounded authorized live notebook session | RuntimeEnvironment fingerprint | Epoch, sandbox | Proposed |
| RuntimeEnvironment | Immutable execution fingerprint/dependency identity | Live kernel/session | CodeSnapshot | Confirmed existing entity |
| MCP | Thin external adapter over public DCLab SDK/API | Internal agent bus or broader authority | Tools/resources/prompts | Proposed |
| ConnectorPort | DCLab-owned integration contract | Airbyte/dlt control plane | SyncRun, checkpoint | Proposed |
| MLflow | Private detailed experiment/package metadata service | DCLab registry or lifecycle authority | Model package ref | Approved future adapter |
| Decision Intelligence | Later layer connecting prediction, recommendation, action and outcome | Current Core ML MVP identity | Scope 8 | Future/partly historical |
| Horizontal Intelligence | Future cross-layer coordination/filtering concept | Current experiment engine | Vertical prediction layers | Provisional future |
| R&D repository | Separate research/benchmark subsystem | DCLab main product | Promotion gate | Supporting, not inspected here |
| Evidence | Versioned, attributable facts/results used to support a claim | Fluent LLM text | Citation, verification | Confirmed |
| Verified | Passed the named gate for the exact source/version/environment | “Implemented somewhere” | Evidence record | Confirmed |

---

## 20. Implementation Constraints and Invariants

### 20.1 Product and authority

- MUST treat the project/lifecycle as canonical over chat, notebook, framework, tracker and provider state.
- MUST preserve one Shared Core for Personal and Business.
- MUST NOT create Personal-only or Business-only copies of an existing core dataset, workflow, run, experiment, model or evidence owner.
- MUST distinguish prediction, recommendation, action, outcome and causal impact.
- MUST NOT market a designed capability as implemented or production-ready.
- SHOULD optimize the first complete customer journey before adding disconnected features.

### 20.2 Data and storage

- MUST keep PostgreSQL as the product control database and private object storage as the body store.
- MUST version/digest important data, source, code, environment, artifact, model and output.
- MUST NOT place large dataset/model bodies, secrets or unrestricted logs in job JSON or general database fields.
- MUST use workspace ownership and database/service checks on tenant edges.
- MUST NOT accept arbitrary SQL, filesystem paths, URLs, pragmas, extensions or network access through DataScan.
- SHOULD stream and bound large inputs rather than impose arbitrary small row-count limits.

### 20.3 Scientific correctness

- MUST lock validation/holdout/primary metric before candidate comparison.
- MUST fit preprocessing on training/fold data only.
- MUST select using validation/CV evidence and evaluate the final holdout only after winner lock.
- MUST preserve failed/rejected/negative results and their reasons.
- MUST NOT tune using final holdout evidence.
- MUST NOT imply that more models/features guarantee better quality.
- MUST NOT make causal claims without qualifying identification evidence.
- SHOULD expose uncertainty, calibration, thresholds, subgroup behavior, cost and business constraints where relevant.

### 20.4 Agents and LLMs

- MUST keep LLM output untrusted until schema, policy and evidence validation.
- MUST use one runtime owner per AgentRun and MUST NOT nest agent runtimes.
- MUST reauthorize every tool call against current membership and resource versions.
- MUST record prompt/model/tool/data-policy/budget versions and citations.
- MUST NOT give agents database, object-store, cloud, provider-secret, arbitrary shell/HTTP/SQL or approval authority.
- MUST NOT store or reveal hidden chain-of-thought.
- MUST ensure feedback cannot silently self-promote prompts, tools, models or policy.
- SHOULD use deterministic fake-provider CI and bounded synthetic live tests.

### 20.5 Commands, jobs and approvals

- MUST route web, SDK, CLI, agent and MCP changes through the same typed application service.
- MUST support idempotency, payload-digest conflict, preconditions, cancellation, terminalization and recovery.
- MUST bind approval to exact versions, digest, budget and action; any material change invalidates it.
- MUST NOT consume an approval twice or create duplicate work on duplicate delivery.
- MUST preserve prior evidence when retrying/branching/cancelling.

### 20.6 Notebook and compute

- MUST keep notebook source/revisions and saved outputs in DCLab, not Jupyter Contents as a competing authority.
- MUST make restart loss of Python RAM explicit and create a new epoch/sandbox.
- MUST NOT run user/agent Python in FastAPI or an ordinary trusted worker.
- MUST keep arbitrary Python disabled until sandbox escape, egress, resource and cleanup gates pass.
- MUST NOT expose Jupyter/Kubernetes/cloud tokens to the browser.
- SHOULD begin CPU-first with Jupyter Server and defer Enterprise Gateway until measured need.

### 20.7 API, SDK, CLI and MCP

- MUST keep `/v1` contracts versioned, bounded and compatible or explicitly reviewed as breaking.
- MUST NOT allow the SDK/CLI to import backend internals or open the database.
- MUST keep MCP authority smaller than or equal to the same API principal.
- MUST maintain independent read/write MCP kill switches and safe bounded results.
- MUST NOT route internal agents back through hosted MCP.

### 20.8 Cloud and providers

- MUST maintain one platform home per environment: AWS or GCP.
- MUST use short-lived workload identity; MUST NOT deploy static AWS keys or GCP service-account key files.
- MUST keep provider IDs/resources out of public domain identities.
- MUST verify each cloud independently for the same release before advertising it.
- MUST NOT automatically fall back to an external provider or transfer unknown-classification data.
- MUST track external readiness by exact home/provider/backend/region/lane/trust/data/policy/image combination.
- MAY add providers only behind the same placement, transfer, cost, cleanup and recovery contracts.

### 20.9 UX and accessibility

- MUST keep backend/API/database behavior as functional truth and reference designs as visual truth.
- MUST show real loading, empty, error, blocked, cancelled, retry and stale states.
- MUST NOT fabricate backend behavior, customer data, metrics or certifications.
- MUST enforce permissions in backend even when controls are hidden/disabled.
- MUST preserve keyboard navigation, focus, labels, contrast and reduced motion.
- SHOULD use the clean liquid-glass/white-space design direction without sacrificing readability.

### 20.10 Business

- MUST keep the first commercial promise bounded to the verified product journey.
- MUST NOT claim universally better accuracy, guaranteed causal impact or replacement of data scientists.
- SHOULD measure customer baseline process, user return, willingness to pay and actual compute/AI cost before final pricing.
- [PROPOSED] MAY use a developer free tier only with clear entitlement and margin controls.

---

## 21. Context Required by Codex

This section is the compact onboarding packet for a new coding-agent task. It is intentionally shorter than the constitution above but remains subordinate to current code, ADRs, prompt packets and verification evidence.

### 21.1 Objective

Build DCLab into a developer-first, project-centric ML/DS operating environment. The supported customer journey begins with a governed structured dataset and ML question and ends with a reproducible, verified, released and monitored batch model. Agents assist and propose; DCLab services authorize, execute, verify and record.

### 21.2 Product core

- One Shared DCLab Core serves Personal Development and Business.
- Personal Development is the individual ML/DS experience.
- Business uses the same ML resources and adds organization, members, governance, entitlements and administration.
- The lifecycle/project is authoritative; notebook/chat/agent/tracker/provider state is not.
- Existing business/client behavior and deterministic ML evidence must be preserved.

### 21.3 Architecture summary

- FastAPI + PostgreSQL + private object storage own product state and artifacts.
- Next.js uses an HttpOnly-session BFF for browser traffic.
- Workspace membership/capability checks are backend authoritative.
- `Experiment` is the existing physical PipelineRun; do not add a competing run/trainer.
- The deterministic ML engine remains usable with every agent/runtime integration disabled.
- Future agents use raw LangGraph for authoritative supervision; Deep Investigation and OpenAI Agents are separate whole-run adapters.
- Future notebooks use private Jupyter Server/ipykernel in an isolated sandbox; DCLab owns revisions, sessions, epochs, outputs and policy.
- AWS and GCP implement one portable platform contract; external compute is optional placement through reviewed adapters.

### 21.4 Current implementation state

- Current generated head observed: Alembic `0059_auth_session_constraints`; discover again before any migration.
- Strong existing areas: deterministic tabular engine, lineage, scientific plan/evidence, jobs, run events, verification, model records, platform/business explorers, session/BFF, multi-workspace selection, tenant tests and liquid-glass app shell.
- Partial: canonical lifecycle projection, Personal Development product, complete `/v1`, public SDK/CLI, package/batch release/monitoring.
- Not implemented: durable agent domain/runtime/UI, ProjectDecisionRecord, interactive notebook runtime, MCP, production connector pack, MLflow/Pandera/Evidently integration, production AWS/GCP, external compute adapters.
- The last fully documented exact-SHA gate is older than the current working tree. Never reuse its counts as proof for new code.

### 21.5 Repository map

| Path | Responsibility |
| --- | --- |
| `apps/api/app/db/models.py` | Canonical ORM tables; inspect constraints/migrations before changes |
| `apps/api/app/api/` | FastAPI routers/dependencies and audience boundaries |
| `apps/api/app/services/` | Application services and state transitions; reuse before adding owners |
| `apps/api/app/engine/`, `ml/` | Deterministic experiment/modeling engine and facade |
| `apps/api/app/domain/`, `workers/` or handler modules | Typed contracts, job handlers and execution policy |
| `apps/web/app/` | Next.js routes, product/public shells, BFF route |
| `apps/web/app/components/` | Shared UI, explorer, layout, model-build and Labs components |
| `apps/web/lib/` | API client, Zod contracts, hooks and session helpers |
| `packages/dclab_client/` | HTTP-only Python client and contract tests |
| `apps/api/alembic/versions/` | Live migration lineage; never assume a historical head |
| `contracts/` | Generated OpenAPI/table/truth artifacts and manifest |
| `docs/verification/` | Exact-SHA evidence and CURRENT/HISTORICAL ledger |
| `docs/agentic-program/` | Canonical concept, roadmap, architecture and executable prompt packets |

### 21.6 Active scope and priorities

Default to the next incomplete prompt in Scope 0, not the most exciting future feature. At the latest available plan state:

1. Verify live tree and current evidence before editing.
2. Finish Scope 0 in exact order, including bounded DataScan and portable storage.
3. Implement Scope 1.0 lifecycle projection and ProjectDecisionRecord.
4. Build the read-only agent completely before enabling mutation.
5. Preserve the deterministic golden path and existing Business/client surfaces.

If a user gives an explicit later-scope prompt, enforce its declared prerequisites and do not silently stub missing security/authority.

### 21.7 Critical invariants

- Reuse existing owners; no second dataset, experiment, trainer, model registry, job queue, approval service or tenant model.
- All tenant queries/writes are workspace scoped and cross-tenant substitutions are adversarially tested.
- Deterministic scientific evidence overrides LLM suggestions.
- Every material version/change creates immutable or superseding evidence.
- One AgentRun has exactly one loop owner.
- User code never runs in the API process.
- Provider details and secrets never enter public domain state.
- Browser JS never receives the long-lived bearer/session secret.

### 21.8 Known technical debt and risks

- Legacy `users.role`/`workspace_id` compatibility remains while membership becomes authoritative.
- Historical cyclic foreign keys create a known SQLAlchemy ordering warning.
- Some documents are historical and name obsolete heads/counts.
- `/development` is a placeholder, while current `/app` is intentionally translated and cannot simply be opened to raw ML details.
- The complete agent/notebook/connector/cloud schemas are detailed proposals and must be reconciled against live owners before migration.
- Current working tree contains founder-generated uncommitted documents and truth changes; preserve them.

### 21.9 Decisions not to revisit without founder/ADR approval

- DCLab brand and developer-first MVP.
- Shared Core beneath Personal and Business.
- Project/lifecycle as product truth.
- PostgreSQL/object-storage ownership.
- Raw LangGraph as authoritative agent supervisor and no runtime nesting.
- Jupyter Server first; Enterprise Gateway deferred.
- One AWS or GCP platform home per environment.
- Batch-model release first; online serving later unless a pilot explicitly requires it.
- MLflow behind DCLab, no W&B as a second MVP tracker.
- No arbitrary SQL through agents/notebooks/DataScan.
- No SHAP in production MVP.

### 21.10 Questions Codex must ask or surface before assuming

- Which exact plan/prompt ID and prerequisite gate applies?
- Which workspace role/capability/audience owns the behavior?
- Is the resource already represented by an existing table/service?
- Is a migration required, and what is the discovered live head?
- What is the exact API request/response/error/idempotency contract?
- What data classification/provider/retention policy applies?
- What happens on duplicate delivery, cancellation, worker crash, stale version and rollback?
- Which deterministic/scientific rule is authoritative if an LLM disagrees?
- Which feature flag/kill switch and production default protect incomplete capability?
- What evidence is needed before the capability may be called implemented or production-ready?

### 21.11 Definition of done for an implementation prompt

An implementation is done only when:

1. related code and accepted evidence were inspected first;
2. existing correct behavior was reused or repaired, not duplicated;
3. schema, constraints, indexes, ownership and rollback are explicit;
4. service state transitions, idempotency, cancellation, recovery and audit are implemented;
5. API/UI/SDK contracts agree for the supported surface;
6. tenant, role, capability, identifier-substitution and data-policy tests pass;
7. deterministic/scientific correctness and reproducibility tests pass;
8. loading/error/empty/blocked/accessibility states exist in UI when applicable;
9. metrics/logs/events/alerts/runbook/kill switch are included in proportion to risk;
10. migrations pass fresh and upgrade paths;
11. backend, SDK, frontend type/lint/build and relevant browser tests pass;
12. generated truth is refreshed intentionally and idempotently;
13. an exact-SHA evidence record reports observed commands, durations, warnings, limitations and rollback;
14. no claim exceeds the evidence.

---

## 22. Recommended Durable Project Files

This section proposes documentation organization only. It does not introduce new product decisions.

| File | Content boundary | Must link to / must not duplicate |
| --- | --- | --- |
| `DCLAB_MASTER_CONTEXT.md` | The complete recovered product truth, source coverage, decisions/conflicts, current state, constitution and Codex onboarding | Link to canonical implementation and evidence documents; avoid volatile numeric facts except with source/date |
| `PRODUCT_CONSTITUTION.md` | Concise stable product mission, users, principles, non-goals, authority and scientific values | Derived from Sections 1–4 and 20; no roadmap details or current counts |
| `ARCHITECTURE.md` | Current target component topology, trust boundaries, ports, storage/runtime/provider ownership | Link to ADRs and detailed agent/notebook/cloud documents; do not list historical heads as current |
| `DOMAIN_MODEL.md` | Canonical entity definitions, relationships, lifecycle states, ownership, mutability and schema-to-domain terminology | ORM/migrations are exact schema authority; do not create parallel entity names |
| `ROADMAP.md` | Scope 0–10 summary, critical path, release slice, dependencies and current gate status | Detailed prompts remain in `docs/agentic-program/prompts/`; evidence in verification ledger |
| `DECISIONS.md` | Index of product decisions and conflicts with links to ADRs | Material technical decisions get immutable ADRs; do not use chat as sole authority |
| `docs/adr/*.md` | Context, options, decision, consequences, migration/rollback and review trigger for one decision | Never rewrite accepted history silently; supersede with new ADR |
| `AGENTS.md` | Repository-local instructions for coding agents: source precedence, scope selection, invariants, commands, evidence and dirty-tree rules | Keep concise; link to Section 21 and execution standard |
| `docs/R_AND_D_RELATIONSHIP.md` | Clear boundary between main DCLab and separate R&D repo; promotion process, artifact/license/security/evaluation requirements | Must state R&D is not production authority |
| `docs/verification/README.md` | CURRENT/HISTORICAL ledger and exact-SHA evidence index | Generated counts owned only by `contracts/` |
| `contracts/README.md` | Generator ownership, refresh/check commands, breaking-change workflow and manifest semantics | No manual duplicate counts in narrative docs |

### 22.1 Recommended maintenance workflow

1. Founders approve this recovered context and answer P0/P1 questions.
2. Extract stable principles into `PRODUCT_CONSTITUTION.md`.
3. Reconcile `ARCHITECTURE.md` and `DOMAIN_MODEL.md` with current canonical detailed docs; mark historical docs clearly.
4. Make `ROADMAP.md` a concise index over the existing Scope 0–10 master plan, not a replacement.
5. Add a root `AGENTS.md` only after confirming its rules do not conflict with existing automation.
6. Update the conflict/decision ledger whenever a founder decision supersedes an older direction.
7. Keep mechanical facts generated and exact-SHA evidence immutable.

---

## 23. Final Completeness Audit

### 23.1 Subjects covered

- Complete product definition and founder vision.
- Developer-first and Business/shared-core product model.
- User pains, personas and end-to-end lifecycle.
- Deterministic scientific method and layered Decision Intelligence relationship.
- Agent runtimes, specialists, tools, memory, autonomy and evaluation.
- Dataset, lineage, domain, storage and privacy architecture.
- Application, runtime, cloud and external-compute architecture.
- UX/navigation/liquid-glass direction and backend-first rule.
- Security, tenancy, governance, approvals, recovery and audit.
- Business model, ICP, distribution, defensibility and market hypotheses.
- Evidence-based current implementation status.
- Scope 0–10 chronological and dependency roadmaps.
- Decisions, conflicts, unknowns, terminology and invariants.
- Coding-agent onboarding and durable documentation structure.

### 23.2 Weak-evidence subjects

- Exact customer willingness to pay and best initial vertical.
- Final free/paid/Business entitlements and pricing.
- Full content of inaccessible/truncated DCLab chats.
- Production behavior on AWS/GCP and external providers.
- Agent quality/cost and notebook sandbox reliability.
- Cross-project learning and open-source strategy.
- Exact final visual token migration.
- Causal/action/outcome product demand beyond the existing prototypes.

### 23.3 Missing conversations/files

- Full `Automate ML Workflow` conversation.
- Complete `ایده‌های نوآورانه DC Lab` conversation.
- Any DCLab conversations older than the 50-task listing or not accessible through the current app.
- Full saved-memory export.
- Separate R&D repository review.
- Private Sites prototype implementation and user-test evidence.
- Customer discovery, security review and commercial agreements.

### 23.4 Contradictions not fully resolved

- Exact UI palette: live blue/cyan/green tokens versus the latest requested `#2596be` and `#21883e`.
- Product naming cleanup: DCLab is confirmed, but historical `Decision.ai` terminology remains in files and domain text.
- Role-normalization timing: canonical roles are defined, but legacy roles remain operational.
- Literal large/unlimited upload expectation versus published entitlement/runtime limits.
- Whether the first paid pilot needs a CRM connector, online endpoint or external GPU.
- Exact boundary of the free developer acquisition product.

### 23.5 Details likely lost through source summarization

- Some assistant responses in accessible chats were truncated by retrieval limits.
- Image-generation iterations contain visual nuance that text cannot encode exactly.
- Historical implementation turns may contain command-level details not repeated in current docs.
- The R&D experiments, model lists and case-study results are only represented by main-repo docs and filenames here.

### 23.6 Founder confirmations required

1. Approve this document's developer-first product definition and the future position of Decision Intelligence.
2. Name the first design partner/persona and use case.
3. Choose the first production home cloud/region.
4. Confirm batch-only first release or online inference requirement.
5. Select the pilot connector and external compute provider, if any.
6. Approve data/LLM provider retention and classification policy.
7. Confirm Personal free/paid and Business entitlement principles.
8. Approve the exact visual palette/token migration.
9. Decide the open-source boundary.
10. Authorize review of the separate R&D repository if it should contribute to this constitution.

### 23.7 Confidence by major section

| Section | Confidence | Reason |
| --- | ---: | --- |
| 0. Source coverage | 0.95 | Direct inventory of used/failed sources |
| 1. Executive definition | 0.95 | Canonical Core Concept plus newest founder direction |
| 2. Vision/philosophy | 0.92 | Repeated founder statements and accepted architecture |
| 3. Problems | 0.90 | Consistent across product, business and technical sources |
| 4. Users/JTBD | 0.90 | Primary users confirmed; secondary/free personas less certain |
| 5. User journey | 0.90 | Canonical lifecycle plus explicit proposed-stage labels |
| 6. Capabilities | 0.91 | Cross-checked against roadmap and live repository |
| 7. Agents | 0.94 | Detailed canonical design; implementation still absent |
| 8. Scientific core | 0.94 | Strong existing code/docs/evidence |
| 9. Data/knowledge | 0.91 | Strong lineage; future mapping/memory work proposed |
| 10. Technical architecture | 0.93 | Detailed approved architecture with status labels |
| 11. Domain model | 0.91 | Live models/table contract plus future entities labelled |
| 12. UX/IA | 0.85 | Strong direction, but final palette/layout not implemented |
| 13. Trust/security | 0.94 | Scope 0 evidence and detailed future gates |
| 14. Business/market | 0.78 | Product thesis clear; market/pricing not validated |
| 15. Current state | 0.93 | Live inventory and exact-SHA distinction |
| 16. Roadmap | 0.97 | Directly recovered from master Scope 0–10 plan |
| 17. Decisions/conflicts | 0.90 | Strong source comparison; founder must approve remaining conflicts |
| 18. Unknowns | 0.90 | Explicitly marked and prioritized |
| 19. Terminology | 0.93 | Derived from canonical domain/architecture |
| 20. Invariants | 0.94 | Supported by canonical plan/security/scientific sources |
| 21. Codex context | 0.95 | Direct synthesis of current implementation and execution rules |
| 22. Durable files | 0.88 | Documentation organization is proposed, not product truth |

### 23.8 Exact follow-up questions for an authoritative founder-approved version

1. Is the public product name simply **DCLab**, and should all remaining `Decision.ai` product wording be treated as historical?
2. Do you approve this primary promise: “question + governed data to a verified, monitored batch model”?
3. Which real user and dataset will be the first design-partner pilot?
4. Which one or two structured-data use cases should the initial website and onboarding emphasize?
5. Must the first pilot include online inference, or is batch prediction sufficient?
6. Which cloud and region will host the first protected staging/production environment?
7. Which connector—if any—is mandatory for that pilot?
8. Which external compute provider and workload lane should be certified first?
9. Which data classes may be sent to which LLM providers, with what retention settings?
10. What are the initial Personal free, Personal paid and Business entitlements, including seats, storage, compute and agent budgets?
11. Should `#2596be` and `#21883e` replace the current global blue/green tokens, or apply only to the future notebook workspace?
12. Should students be an explicit launch persona or a later community segment?
13. Which parts, if any, should be open source?
14. May the separate R&D repository be inspected and incorporated as a supporting scientific-source appendix?
15. Which founder owns customer discovery/pilots, and which owns product/engineering delivery?

### 23.9 Final assessment

The recovered project truth is coherent enough to guide implementation: DCLab is a developer-first, shared-core ML lifecycle product with a later Business Decision Intelligence layer. The repository already provides a substantial deterministic and multi-tenant foundation. The main gap is not another broad idea; it is disciplined completion of the ordered gates that turn the existing engine into one connected Development journey, then a governed agent, notebook and production release system.

This document becomes authoritative only after founder review of the conflicts and P0/P1 questions. Until then, it is the most complete evidence-labelled master context available from the sources listed in Section 0.
