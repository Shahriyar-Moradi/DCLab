# Jev and NVIDIA NOOA integration architecture

> **PAUSED (2026-10-01).** This document is design reference only. The active plan, order and status live in [`docs/mvp/`](../mvp/AGENTS_NOOA_JEV.md). Do not execute prompts from this program unless a `docs/mvp` prompt cites them.

**Status:** PROPOSED implementation design; not evidence that either integration
exists or is enabled.

This document adds two optional capabilities to the DCLab program:

- TypeSafe AI Jev as a provider behind a DCLab-owned structured-decision port.
- NVIDIA Object-Oriented Agents (NOOA) as a separately selected, whole-run agent
  runtime for bounded proposal work.

They do not replace deterministic ML, the Scope 1 LangGraph supervisor, the
DCLab LLM gateway, ToolRunner, PostgreSQL product state, the notebook runtime or
approval services. Existing evidence IDs keep their meanings.

## 1. Product decision

Use Jev for atomic semantic judgments that have an explicit answer space and a
measured abstention policy. Use NOOA when a bounded objective benefits from a
Python object whose typed methods expose a small set of DCLab capabilities.

The first release is deliberately narrow:

1. Jev evaluates one existing semantic decision in shadow mode using synthetic
   and approved de-identified context.
2. DCLab compares Jev with the deterministic/current provider baseline and
   calibrates purpose-specific thresholds.
3. A NOOA worker runs one explicitly requested read-only investigation and
   returns a typed, cited proposal.
4. After the Scope 4 sandbox and approval gates pass, a NOOA notebook
   collaborator may propose a revision. DCLab applies and executes it only
   through existing notebook services.

Neither a Jev answer nor a NOOA result can grant access, approve a command,
change scientific evidence, publish a model or execute an external action.

## 2. Ownership and call topology

```text
Browser / SDK / agent API
          |
          v
DCLab authorization + capability + data-policy services
          |
          +-----------------------------+
          |                             |
          v                             v
structured-decision service       AgentRun service
          |                             |
          v                             v
Jev adapter in trusted gateway    nooa.run.v1 durable job
          |                             |
          v                             v
TypeSafe API                      isolated worker-nooa
                                        |
                              narrow DCLab API/SDK tools
                                        |
                       proposal/citations/events returned to DCLab

Approved notebook proposal
          |
          v
Notebook review/apply service -> isolated Jupyter runtime
```

Jev is an inference provider, not an agent runtime. It has no tools, product
session, checkpoint or command authority. NOOA is a runtime selected for an
entire `AgentRun`; it is never nested inside LangGraph, Deep Investigation or an
OpenAI Agents run, and none of those runtimes is exposed as a NOOA tool.

## 3. Jev structured-decision contract

Add a provider-neutral `SemanticDecisionPort` with a single bounded operation:

```text
decide(
  purpose,
  release_id,
  state_reference_or_bounded_state,
  questions,
  policy_snapshot,
  deadline,
  idempotency_key,
) -> SemanticDecisionResult
```

The public/domain contract supports three question forms:

| Type | DCLab result | Intended use |
| --- | --- | --- |
| `choice` | selected code, probability map and confidence | route among a fixed, code-owned set |
| `score` | bounded level/score, probability map and confidence | assess one defined dimension |
| `noul` | probability that one proposition is true | flag one explicit yes/no condition |

Question keys, answer options, descriptions, threshold policy and output schema
are immutable releases. Callers cannot supply an arbitrary question for an
automatic path. An exploratory/admin evaluation may accept a bounded draft only
under a separate capability and must never affect product behavior.

### Safe initial uses

- route a request to one registered read-only specialist;
- classify an already-authorized, bounded metadata summary;
- flag a possible semantic leakage concern for deterministic review;
- prioritize review queues without denying access or changing evidence;
- score proposal relevance or completeness as one advisory evaluation signal.

### Prohibited uses

- authentication, authorization, entitlement or tenant selection;
- approval, payment, model release or external side-effect decisions;
- calculation of metrics, schema facts, lineage, budgets or policy compliance;
- automatic data deletion, model promotion or notebook execution;
- replacing leakage/validation tests or deterministic acceptance criteria;
- interpreting confidence as proof of correctness.

Choice/Score confidence is treated as a statistic derived from the probability
distribution. Noul is a probability of “yes” and has no separate confidence.
Each purpose defines three outcomes in code: `accept_advisory`, `review`, and
`abstain`. The first production release defaults to `review` or `abstain`.
Automatic read-only routing requires an evaluation-backed release threshold and
still passes normal authorization at the selected destination.

## 4. Jev persistence model

The preferred additive model is three tenant-safe tables. Reconcile names with
the implemented Scope 1 registry before migration; reuse an equivalent owner
instead of duplicating it.

### `semantic_decision_releases`

- `id`, `workspace_id`, `purpose`, `version`, `status`;
- `provider_key`, pinned `model_id`, question schema/version and bounded
  question definitions;
- threshold/abstention policy, allowed data classification, maximum state/
  question bytes and maximum question count;
- evaluation-suite/baseline references, canonical digest, creator/reviewer and
  created/activated/retired timestamps.

Constraints and indexes:

- unique `(workspace_id, purpose, version)` and `(workspace_id, digest)`;
- status in `draft`, `shadow`, `active`, `retired`, `disabled`;
- at most one active release per workspace/purpose through a partial unique
  index;
- immutable after activation; correction creates a new version.

### `semantic_decision_invocations`

- `id`, `workspace_id`, optional `project_id`, `agent_run_id`,
  `workflow_run_id` and `experiment_id` where applicable;
- release/provider/model, input/context digest, policy/data/budget snapshot IDs;
- idempotency key hash and canonical request digest;
- state: `reserved`, `dispatching`, `succeeded`, `failed`, `cancelled`,
  `expired`;
- usage, estimated/settled cost, latency, safe error/reason, started/completed
  timestamps and retention deadline.

Constraints and indexes:

- composite workspace foreign keys for every related tenant resource;
- unique `(workspace_id, idempotency_key_hash)` with same-key/different-digest
  conflict behavior;
- indexes for workspace/purpose/time, run lineage, state/age and retention;
- terminal rows are immutable except bounded reconciliation fields.

Raw customer state is not stored in this table. An authorized retained body is
an encrypted artifact with classification, digest and deletion deadline.

### `semantic_decision_answers`

- `workspace_id`, `invocation_id`, `question_key`, `question_type`;
- selected choice or score or Noul probability;
- bounded probability map, nullable confidence, threshold outcome;
- schema/validator verdict, optional reviewer verdict and safe reason code.

Use a unique `(workspace_id, invocation_id, question_key)`, composite invocation
lineage and checks that values are finite and within the declared range. Service
validation enforces exact answer keys, probability keys/sum tolerance and the
question-type-specific nullability rules.

## 5. Jev software and operations

Planned owners:

```text
apps/api/app/domain/semantic_decisions.py
apps/api/app/services/semantic_decision_service.py
apps/api/app/services/semantic_decision_gateway.py
apps/api/app/providers/typesafe_jev.py
apps/api/app/services/semantic_decision_evaluation.py
```

Only the provider adapter imports `typesafe-sdk`. The application resolves the
model and release server-side, reserves budget, applies data policy, bounds and
redacts context, dispatches with deadlines and retries, validates every answer,
settles usage and records the resolved version returned by the provider.

Configuration is typed and fail-closed:

```text
DCLAB_JEV_ENABLED=false
DCLAB_JEV_MODEL=jev-1.13.0
DCLAB_JEV_TIMEOUT_SECONDS=<bounded>
DCLAB_JEV_MAX_STATE_BYTES=<bounded>
DCLAB_JEV_MAX_QUESTIONS=<bounded>
```

The API key is a secret-manager reference available only to the trusted gateway.
It is absent from browsers, agent workers, notebooks, job payloads, database
rows and logs. Sensitive purposes remain disabled until retention, regional,
contractual and deletion requirements are verified for the deployment.

Metrics include invocation/denial/error totals, latency, cost, confidence/
probability buckets, abstention/review rates, baseline disagreement, reviewer
overturn and calibration drift using bounded purpose/model/release labels.
Alerts cover error/rate-limit spikes, breaker-open, budget exhaustion, increased
abstention, calibration regression and unexpected model-version changes.

## 6. NOOA runtime contract

NOOA runs only inside a separately built `worker-nooa` image with a pinned
package/version/digest. The API and `worker-agent` images do not install NOOA.
The first permitted runtime key is `nooa.proposal.v1`.

One DCLab `AgentRun` selects one runtime kind/version. The NOOA worker receives
an immutable run manifest containing IDs and digests, a short-lived audience-
bound DCLab token, limits and no provider or product credentials. The worker
constructs one approved agent class release and returns a typed proposal.

Initial restrictions:

- read-only/proposal purposes only;
- no subagents, persistent memory, generic shell, filesystem tool, arbitrary
  HTTP, arbitrary MCP server or package installation;
- no product database, object-store client, cloud credentials or connector
  secret;
- one agent instance per independent run; no shared mutable tool state;
- bounded model calls, iterations, generated-code cells, bytes, CPU/memory,
  processes and wall time;
- cancellation terminates child processes and the worker attempt;
- no hidden reasoning is persisted or returned.

NOOA `PredictStrategy` is preferred for one typed judgment that does not need
tools. `CodeActStrategy` is allowed only for a named purpose whose tool surface
requires iteration and only inside the verified OS isolation profile. Generated
Python is untrusted even when NOOA syntax/import checks accept it.

## 7. DCLab agent classes

Agent classes are immutable, reviewed releases. Public methods are the smallest
useful capability wrappers and return bounded DCLab-owned contracts. A proposed
first class is:

```python
class ExperimentReviewAgent(Agent):
    project: ProjectSummary
    objective: ReviewObjective

    def read_lifecycle(self) -> LifecycleSummary: ...
    def read_dataset_profile(self, dataset_id: UUID) -> DatasetProfileSummary: ...
    def read_experiment_evidence(self, run_id: UUID) -> ExperimentEvidence: ...
    async def request_semantic_review(
        self, request: SemanticDecisionRequest
    ) -> SemanticDecisionResult: ...
    def propose_notebook_revision(
        self, proposal: NotebookRevisionProposal
    ) -> ProposalReceipt: ...

    async def investigate(self, objective: ReviewObjective) -> InvestigationProposal:
        """Return a cited proposal using only the visible DCLab capabilities."""
        ...
```

This is an interface example, not implementation. Tool wrappers call DCLab
HTTP/application boundaries that re-authorize every resource. They do not wrap
database sessions or raw vendor clients. NOOA visibility decorators reduce the
model-visible surface but are not security controls; process isolation and
least-privilege credentials enforce authority.

Jev is exposed to NOOA only as `request_semantic_review` through DCLab. The
agent never receives the TypeSafe client or API key and cannot invent an active
question release. The decision result remains advisory evidence with its model,
release, probability and threshold lineage.

## 8. NOOA persistence model

Reuse Scope 1 `AgentDefinitionVersion`, `AgentRun`, `AgentStep`, `AgentEvent`,
`AgentToolCall`, `Citation`, policy/budget records and `MlJob`. Store agent-class
source/package manifests as immutable artifacts referenced by digest.

If those records cannot represent worker lifecycle without overloading a public
step, add one generic `agent_runtime_attempts` table rather than a NOOA-specific
product model:

- `id`, `workspace_id`, `agent_run_id`, `attempt_number`;
- runtime kind/version, agent-release digest, worker-image digest and strategy;
- objective/context/tool-manifest/policy/budget digests;
- state, lease owner/expiry, heartbeat, cancellation timestamp;
- model-call/iteration/code-cell and usage counters;
- proposal/result artifact digest, safe error/reason and retention deadline;
- created/started/completed timestamps.

Enforce unique `(workspace_id, agent_run_id, attempt_number)`, composite run
lineage, one active attempt per run, monotonic attempt number and indexes for
lease recovery, stuck attempts and retention. This row stores no provider key,
raw context, generated-code transcript, hidden reasoning or mutable product
memory. Normalized bounded events use existing `AgentEvent`; restricted trace
bodies, when explicitly retained, are encrypted artifacts with deletion policy.

## 9. NOOA deployment and model access

Build the same digest-pinned worker for EKS and GKE. A CodeAct-enabled worker
uses the approved isolated runtime class, non-root identity, read-only root,
bounded writable scratch, denied host mounts, workload identity with no cloud
resource grants, and denied-by-default egress.

The worker does not receive an OpenAI/Anthropic/TypeSafe key. It calls a private
DCLab model-inference endpoint using its short-lived run token; that endpoint
resolves the provider/model and applies the Scope 1 gateway, budget, retention
and audit policy. S2-P14A must prove a supported NOOA client adapter can use this
transport without patching private framework internals. Failure of that proof is
a no-go for NOOA rather than permission to distribute provider secrets.

Allowed network edges are limited to the private DCLab inference and tool
facades plus telemetry. Scope 4 notebook execution is a different sandbox and
credential. A NOOA worker may create a proposal for it but may not attach to a
kernel, call Jupyter directly or execute proposed notebook code.

## 10. Notebook collaboration

After S4-P07E and an allowed S2-P14H decision, a `NotebookReviewAgent` may:

1. read an authorized notebook revision and bounded lifecycle/evidence views;
2. return a typed patch against the exact base revision/ETag;
3. attach evidence citations and expected effect;
4. request Jev advisory judgments through an approved release;
5. submit the proposal to the DCLab review queue.

Apply revalidates revision, capability, inputs, data policy, cell schemas,
budgets and required approval. Execution creates an ordinary notebook execution
intent/job and runs through the existing sandbox. A stale, rejected or uncertain
proposal is never executed automatically.

## 11. Testing and promotion

Jev promotion requires a versioned corpus with purpose-specific accuracy,
calibration, abstention, subgroup/language behavior, latency and cost. Compare
against deterministic logic and the existing structured LLM path. Hard-fail on
tenant/data-policy leakage, unknown answer keys, NaN/out-of-range probability,
unrecorded model change, automatic write or threshold bypass.

NOOA promotion requires deterministic fake-model transcripts plus controlled
provider tests. Exercise forged tools, prompt injection, private-name access,
arbitrary imports/HTTP/filesystem attempts, resource exhaustion, cancellation,
worker death, stale/replayed jobs, cross-tenant IDs and malformed proposals.
Hard-fail on runtime nesting, secret access, direct database/object storage,
uncited claims accepted as facts, automatic command execution or sandbox escape.

Each integration records one release decision:

- `SHADOW_ALLOWED` for named purposes and data classes;
- `DISABLED_PENDING_EVIDENCE`;
- `REJECTED`.

Jev and NOOA have separate feature flags and global kill switches. Removing
either dependency/image must leave deterministic ML and the authoritative
LangGraph path healthy. Activation is never required merely because the plan
was implemented.

## 12. Ordered roadmap placement

- **Plan 1.12:** Jev provider-neutral structured-decision foundation and shadow
  release. It may begin after Plans 1.4–1.5 and finishes after Plan 1.11
  evaluation owners exist. It does not block Scope 2.
- **Plan 2.14:** isolated NOOA proposal runtime. It begins after S2-P11F and
  requires an S1-P12F Jev decision only when a NOOA purpose uses Jev. It does
  not block Scope 3 or the core production-MVP path.
- **Plan 4.8:** NOOA notebook collaborator. It requires `SHADOW_ALLOWED` or
  stronger S2-P14H evidence plus S4-P07E. If NOOA is disabled or rejected, this
  plan is recorded not applicable and Scope 4 remains releasable.

## 13. Official references and version rule

Reviewed on 2026-09-23:

- [TypeSafe Jev quick start](https://docs.typesafe.ai/introduction/quickstart)
  documents the `/v1/systemone` endpoint, `typesafe-sdk`, Choice/Score/Noul,
  the movable `jev-latest` alias and a response resolved to `jev-1.13.0`.
- [NVIDIA NOOA repository](https://github.com/NVIDIA-NeMo/labs-OO-Agents) and
  its [framework tour](https://github.com/NVIDIA-NeMo/labs-OO-Agents/blob/main/docs/tour.md)
  document Python object/method capabilities, typed results, Predict versus
  CodeAct and the requirement to run CodeAct in an OS-level sandbox.
- [NOOA repository agent guidance](https://github.com/NVIDIA-NeMo/labs-OO-Agents/blob/main/AGENTS.md)
  documents visible-by-default names, explicit hiding and the live-object Python
  REPL behavior. Visibility is usability, not a DCLab security boundary.

These projects are evolving. Implementation prompts must re-read official
documentation, select an exact package/image/model/API version, record a
dependency and license review and rerun compatibility/security evidence. Never
ship `jev-latest`, an unpinned NOOA commit or documentation examples as a
production control without that gate.
