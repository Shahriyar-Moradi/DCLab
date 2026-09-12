# DCLab core product concept

**Status:** canonical product north star

**Primary users:** data scientists and ML engineers

**Relationship to the implementation roadmap:** this document defines what
DCLab must become and how product decisions are evaluated. The
[`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md) remains the
ordered implementation program. Neither document authorizes bypassing verified
code, tests, security controls or scope gates.

## 1. Product thesis

DCLab is the **project-centric operating environment for the machine-learning
lifecycle**. A useful shorthand is “Cursor for machine learning,” with one
essential distinction: the primary unit of work is not a source file or
notebook cell. It is the connected, versioned ML lifecycle.

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

1. secure foundation and tenant/data controls;
2. first-class immutable ML lifecycle projection;
3. goal, prediction semantics, business constraint and metric contract;
4. dataset investigation and leakage/validation findings;
5. reviewable experiment proposal;
6. canonical approved model-build command;
7. a bounded baseline and candidate portfolio;
8. experiment comparison, explanation, cost and reproducibility;
9. one bounded autonomous improvement loop;
10. durable project decision memory;
11. synchronized conversation/workflow/implementation views;
12. Python SDK and CLI automation for the supported path;
13. immutable model registration and one batch-prediction release path;
14. initial input/prediction/performance-drift monitoring and rollback;
15. an allowlisted production pilot with data scientists and ML engineers.

The initial deployment capability is deliberately narrow: verified model
package plus environment and feature contract, an immutable release, authorized
batch inference, monitoring windows, agent-generated investigation proposals
and rollback. Online REST serving, streaming, scheduled/edge inference,
multi-cloud provisioning and autonomous retraining remain later measured
capabilities.

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

## 8. Conflict-resolution rules

| Potential conflict | Required resolution |
| --- | --- |
| ML lifecycle graph vs LangGraph | The lifecycle graph is authoritative product lineage; LangGraph is private execution routing only. |
| Project memory vs agent checkpoint/message history | `ProjectDecisionRecord` is curated product memory; checkpoints only resume work and messages remain conversation history. |
| Project-centric product vs notebooks | The lifecycle is canonical; notebooks are synchronized investigation and implementation views. |
| Code-hidden UX vs developer control | Complexity may be summarized, but implementation, environment and evidence remain inspectable by authorized users. |
| Multi-agent breadth vs first-user value | Keep the full multi-agent roadmap, but a specialist is promoted only when evaluation proves value; the core release is judged by the end-to-end ML journey. |
| Early SDK/CLI vs later public platform | Ship a bounded internal/allowlisted Core ML client over the same `/v1`; Scope 5 hardens identity, completeness, compatibility and public packaging without creating a second client. |
| Early batch release vs Scope 10 serving | Implement one safe batch path in the Core ML MVP; Scope 10 extends it to online/streaming, advanced monitoring and higher autonomy when evidence triggers it. |
| Core ML focus vs existing business product | Preserve current business behavior and future Scope 8 plans. Business actions/outcomes are not required for the first Core ML MVP gate and cannot block its pilot. |

## 9. Core-product definition of done

The core concept is implemented only when both primary personas can complete the
supported journey with no database intervention and the following evidence is
recorded:

- time to first valid baseline and time to constraint-satisfying result;
- deterministic leakage/validation findings and false-positive review;
- complete lifecycle and decision reconstruction from immutable records;
- exact accepted/rejected changes, costs, metrics, artifacts and reasons;
- reproducible model package and batch predictions from pinned inputs;
- drift detection, investigation proposal and release rollback exercise;
- UI, SDK and CLI identity/state parity;
- tenant, privacy, budget, cancellation, crash-recovery and accessibility gates;
- user evidence from data scientists and ML engineers that the workflow reduces
  manual lifecycle work without hiding control.

Full multi-agent expansion, isolated Python notebooks, hosted MCP, connectors,
the existing business-side roadmap and higher autonomy remain part of the
program. They extend this core; they do not redefine or replace it.
