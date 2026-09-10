# Scope 4 prompts — managed agentic notebook and isolated compute

Use the common preamble. Deliver managed cells first. Python is a separately
gated beta, not an automatic part of notebook creation.

## Plan 4.1 — notebook domain and storage

### S4-P01A — define notebook contracts

```text
Define Notebook, immutable NotebookRevision, stable NotebookCell,
NotebookCellRevision, NotebookExecution/CellExecution, EnvironmentRelease,
InputMount and OutputArtifact contracts. Cover title/status/visibility,
current revision, parent revision, ordered cells, cell type/schema, creator user
or AgentRun, content/source digest, resource references, execution state,
environment/input/output versions, usage and failure. Define optimistic
concurrency for mutable metadata and reviewable revisions for content.
```

### S4-P01B — migrations and integrity tests

```text
Add the notebook schema in small migrations with workspace/project ownership,
composite tenant FKs, immutable revisions/executions, unique version/ordering,
bounded JSON, Artifact references and list/claim indexes. Managed cells may
resolve existing resources without a kernel. Test concurrent revisions,
cross-workspace references, invalid order/type/state, immutable history,
deletion/retention and historical upgrades. Do not add code execution records
or infrastructure secrets to generic cell JSON.
```

## Plan 4.2 — managed cell runtime

### S4-P02A — implement managed cells

```text
Implement a code-owned registry for Markdown, bounded DCLab query, dataset
profile, Visualization, model-build intent/reference, evidence, decision and
agent-objective cells. Each cell has strict versioned input/output schema,
required capability, data class, cost/risk, timeout and renderer. Execute
through typed application services and jobs; never eval user/LLM function names.
Markdown/visualization output is sanitized. Build/effect cells reuse Scope 3
commands and approval. Persist exact revision, resource versions, result links,
digests and usage.
```

### S4-P02B — managed-cell contract suite

```text
For each cell test schema bounds, additional fields, tenant/resource
authorization, stale versions, deterministic re-execution, cancellation/retry,
worker loss, output classification/digest, malicious Markdown/specs and budget.
Test that a notebook cannot smuggle a write through a query cell, invoke an
unknown cell type or bind another workspace's Artifact. Ensure refresh and
export reproduce results from exact inputs without silently using latest state.
```

## Plan 4.3 — APIs, jobs and exports

### S4-P03A — notebook `/v1` and SDK

```text
Add create/list/get/patch notebook; create/get revision; execute/get/cancel/
retry cell; and export endpoints with opaque cursors, ETag/If-Match, standard
errors, idempotency and 202 semantics. Add notebook.render.v1 and
notebook.cell.v1 handlers with deployment allowlists. Extend sync/async SDK
types, iterators/waiters and Artifact links. Enforce current authorization at
execution and download.
```

### S4-P03B — reproducible export and recovery

```text
Export approved revisions as Markdown/HTML/ipynb-compatible/PDF or documented
subset Artifacts with notebook/revision/environment/input/output/provenance
manifest. Omit secrets, signed URLs, hidden prompts/reasoning and forbidden
audience detail. Inject worker loss before/after render/object write/Artifact
commit; reconcile orphan/missing objects by digest. Test cancellation, duplicate
export, corrupt input and restored database/object versions.
```

## Plan 4.4 — agent notebook collaboration

### S4-P04A — reviewable agent diffs

```text
Give notebook agents tools to explain cells, propose add/delete/reorder/replace
operations as a typed diff, bind authorized resource IDs, run allowed managed
cells and explain bounded errors. The proposal identifies base revision and
creates a new immutable revision only after user/policy acceptance; never
silently overwrites current content. Rebase/conflict behavior is explicit. All
generated claims retain citations and agent/task lineage.
```

### S4-P04B — collaboration and injection tests

```text
Test stale-base conflict, concurrent user/agent edit, malicious notebook text,
cross-tenant binding, unauthorized cell type, budget exhaustion, partial diff,
rejected proposal, cancel during execution and refresh. Prove notebook content
cannot change agent policy/tools or execute as instructions without an explicit
typed action. Evaluate useful diff rate, citation correctness and user edit
burden against a no-agent baseline.
```

## Plan 4.5 — notebook UI

### S4-P05A — build the managed notebook workspace

```text
Build notebook list/editor/revision history, managed-cell forms/renderers,
execution status, inputs/outputs/provenance, agent proposals/diffs, conflicts,
cancel/retry and exports. Show exact workspace/project/revision and immutable
published state. Provide loading/empty/stale/denied/failed/cancelled states and
accessible keyboard/focus/status behavior. Hide cell/actions by capability but
still rely on backend enforcement.
```

### S4-P05B — system and accessibility E2E

```text
E2E create notebook, add managed cells, execute profile/chart/evidence, request
and accept/reject agent diff, refresh, handle concurrent conflict, export and
verify Artifact digest/provenance. Test client versus technical audience,
workspace switch, malicious Markdown and denied build cell. Run accessibility
automation and manual critical workflow review.
```

## Plan 4.6 — isolated Python beta

### S4-P06A — design and provision the sandbox plane

```text
Write the code-execution threat model and ADR, then implement an isolated
execution service using short-lived containers or microVMs—not API/ML/agent
workers. Run unprivileged with read-only base, no Docker socket/host mounts/
cloud metadata/secrets/inbound network, outbound denied by default, seccomp or
equivalent, curated immutable signed image/SBOM and CPU/memory/process/file/
disk/output/wall limits. Deliver authorized inputs via a manifest/read-only
mount and collect outputs in isolated scratch. Dynamic pip/install is disabled.
```

### S4-P06B — code-cell contract and controlled publisher

```text
Add versioned Python cell source Artifact/digest, EnvironmentRelease, exact
input digests, resource budgets, execution job/state, bounded stdout/stderr and
exit status. A publisher validates output path traversal/symlink, size, MIME,
digest, schema and malware/content before creating Artifacts. Never inject DB,
cloud, object-store or provider credentials. Implement timeout/cancel/kill/
cleanup and orphan reconciliation. Keep production flag off.
```

## Plan 4.7 — sandbox proof and release

### S4-P07A — escape, egress and exhaustion suite

```text
Against the real sandbox platform attempt host/process/Docker socket/metadata/
private IP/internet/other-execution access; symlink/path/archive escape;
CPU/memory/fork/file/disk/output exhaustion; persistence after termination;
secret leakage through error/artifact; and disallowed package installation.
Also prove allowed input, deterministic environment digest, normal output,
timeout/cancel and cleanup. Any isolation failure blocks Python.
```

### S4-P07B — staged canary and operations

```text
Instrument queue age, startup, resource usage, kills, cleanup failure, egress
denial and suspicious output. Add dashboards, alerts, image-promotion policy,
vulnerability response, stuck-sandbox and kill-switch runbooks. Soak test
resource leakage and cross-execution isolation. Enable only curated cells for
an internal allowlist after security review; preserve managed notebook when
Python is disabled.
```
