# Scope 6 execution prompts — Model Context Protocol adapter

Start after Scope 5; write tools also require Scope 3. Apply `README.md` and
`EXECUTION_STANDARD.md`. At implementation time pin one published MCP protocol
revision and SDK version. The current planning reference is the 2026-07-28 MCP
specification; do not silently mix sessionful 2025 behavior with its stateless
core, request-scoped Streamable HTTP, routing headers or authorization model.
Re-check the official [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28)
when Plan 6.1 begins and pin the accepted revision in the repository ADR.

## Scope implementation boundary

Create `packages/dclab_mcp` as a thin adapter over the public Python SDK. It has
no database/API-internal/object-store imports and no authority beyond the caller.
Expose bounded tools/resources/prompts mapped from the approved `/v1` inventory.
Read and write releases and kill switches remain independent. The MCP package
does not import LangGraph, PydanticAI or agent-runtime/checkpointer internals;
MCP requests use `/v1` and cannot create a parallel agent or tool authority.

## Plan 6.1 — package architecture, protocol pin and threat model

**Contract.** The package records exact protocol/SDK revisions, compatibility
matrix and transport differences. MCP names/descriptions are UI metadata, never
authorization. All business behavior remains in `/v1` application services.

### S6-P01A — protocol and SDK decision record

```text
Review the current published MCP specification and supported official SDKs at
implementation time. Write an ADR pinning protocol revision, SDK/package version,
Python/runtime support, stdio and Streamable HTTP bindings, stateless/session
compatibility and deprecation policy. Map required headers/message metadata,
cancellation, progress/tasks/input-required behavior and backward compatibility.
Do not code against “latest” or rely on unpinned examples.
```

### S6-P01B — DCLab resource/tool/prompt mapping

```text
Map approved `/v1`+SDK operations to versioned MCP tools, resources/templates
and prompts. For each record stable name, purpose, input/output schema, required
scope/capability, workspace argument/source, read/write/risk, page/byte/time
bounds, citation/resource URI and safe errors. Exclude raw rows, secrets, signed
URLs, prompt bodies, hidden reasoning and private/admin internals. Approve the
manifest before implementation.
```

### S6-P01C — package skeleton and dependency boundary

```text
Create `packages/dclab_mcp` with isolated metadata/lock, console entry point,
server factory, transport/config modules, generated/declarative catalog and tests.
Depend only on public `dclab_client` plus pinned MCP SDK and narrow utilities.
Add an import-boundary test rejecting `apps.api`, SQLAlchemy/database/storage and
provider SDK imports. Implement initialize/list skeleton only; no DCLab call yet.
```

### S6-P01D — MCP threat model and architecture gate

```text
Threat-model prompt/tool injection, confused deputy, schema/name spoofing, token
passthrough, audience/scope mix-up, DNS rebinding, hostile Origin/Host, stdout
contamination, oversized JSON, resource URI traversal, retry side effects and
output exfiltration. Map controls/tests/kill switches and define trust boundaries
for stdio versus hosted. Close only when package and protocol decisions eliminate
any need for direct DB/internal access.
```

## Plan 6.2 — local read-only stdio server

**Contract.** Stdio reads newline-delimited protocol messages on stdin and writes
only protocol messages to stdout; diagnostics go to stderr. Credentials arrive
through explicit environment/config provider, never protocol arguments/results.

### S6-P02A — stdio transport and lifecycle

```text
Implement pinned-SDK stdio server lifecycle, capability negotiation/version
handling, cancellation and clean EOF/shutdown. Bind locally as a client-launched
subprocess; write no banner/log/progress outside valid MCP frames on stdout.
Configure endpoint/profile/workspace through validated environment/config and
SDK auth provider. Add subprocess framing tests for partial/multiple/malformed/
oversized messages and stderr separation.
```

### S6-P02B — read-only tools

```text
Implement the approved identity/project/dataset/build/evidence/agent/notebook read
tools by calling public SDK methods only. Validate input schema/unknown fields,
explicit workspace/resource IDs and maximum pages/items/bytes/time. Return
structured bounded results and DCLab request/resource IDs; translate SDK errors
to stable protocol errors without provider/internal bodies. Add per-tool tests.
```

### S6-P02C — resources and resource templates

```text
Define stable non-secret DCLab resource URIs for authorized metadata/evidence and
templates for workspace/project/dataset/run/model/agent/notebook where useful.
Resolve through SDK on each read, re-authorize server-side, bound content/MIME and
include version/digest/citation metadata. Reject traversal, unknown scheme/type,
unbounded lists and changed/deleted resources. No local filesystem URI exposure.
```

### S6-P02D — prompts and untrusted-content boundaries

```text
Expose only versioned client-side convenience prompts that instruct use of the
published read tools and label DCLab/tool/resource content as untrusted. Prompts
contain no server secret, hidden registry body or authority claim and cannot
select arbitrary endpoint/tool. Bound arguments and output. Add injection fixtures
in resource names/errors/results and verify no new tool call bypasses policy.
```

### S6-P02E — stdio conformance and release gate

```text
Run the pinned protocol/SDK conformance suite plus subprocess framing, initialize,
list/change behavior, cancellation, malformed/huge input, two-workspace/scope,
backend outage and stdout secret scan. Verify exact catalog equals the approved
read-only manifest and import boundary is clean. Add safe debug/runbook and package
candidate evidence; do not enable hosted or writes.
```

## Plan 6.3 — hosted Streamable HTTP and OAuth

**Contract.** Implement the pinned revision’s single request-scoped Streamable
HTTP endpoint and stateless core. OAuth tokens are audience-bound to this MCP
resource and validated on every request; they are never passed to another API.

### S6-P03A — Streamable HTTP transport

```text
Implement the pinned revision’s POST/GET behavior, accepted content types,
request-scoped JSON/SSE response, cancellation by response-stream close, protocol
metadata and required mirrored routing headers such as `Mcp-Method`/`Mcp-Name`.
Reject body/header mismatch. Do not add hidden connection session state; explicit
application handles remain tool arguments. Add transport conformance, disconnect,
backpressure and proxy tests.
```

### S6-P03B — protected-resource and authorization metadata

```text
Publish RFC 9728 protected-resource metadata for the canonical MCP resource URI,
associated authorization server(s), minimal scopes and supported bearer methods.
Return standards-compliant `WWW-Authenticate` on 401/insufficient-scope 403.
Support the pinned specification’s authorization-server discovery and client
registration choices. Validate configuration/HTTPS and add metadata/header cache/
negative tests. MCP is a resource server, not a token proxy.
```

### S6-P03C — bearer validation and DCLab identity mapping

```text
Validate signature/issuer/audience/resource/expiry/not-before/scope/token type and
revocation/introspection policy on every request. Map subject/client to current
DCLab principal, membership, selected explicit workspace and capabilities; token
scope is necessary but not sufficient. Return 401 for invalid token and 403 with
complete current-operation scope challenge for insufficient scope. Test mix-up,
wrong audience/resource and revoked membership.
```

### S6-P03D — Origin, Host, routing and network hardening

```text
Require allowlisted Origin when present, validate canonical Host/forwarded headers
only from trusted proxies, enforce HTTPS, body/header/time/concurrency/rate limits
and protect local deployments from DNS rebinding. Bind local-only mode to loopback.
Reject private/unexpected callback/metadata destinations and unsafe CORS. Add
IPv4/IPv6/encoded host/origin/proxy smuggling tests and boot validation.
```

### S6-P03E — stateless scale, progress and recovery

```text
Map long DCLab operations to explicit durable resource handles and polling/task
results allowed by the pinned protocol; do not hold server memory as authority.
Propagate W3C trace context only through approved metadata and validate it. Make
retries/redelivery idempotent and bound SSE/output buffering. Test load-balanced
requests across instances, disconnect/retry, duplicate calls and backend timeout.
```

### S6-P03F — hosted security/conformance gate

```text
Run protocol conformance and multiple compatible MCP clients against staging for
metadata discovery, OAuth/PKCE where applicable, scope challenge/step-up, request
routing, tools/resources, cancellation and stateless instance switching. Execute
Origin/Host/DNS-rebinding/token mix-up/output leakage/rate tests. Add telemetry,
alerts/runbooks and independent hosted-read kill switch before canary.
```

## Plan 6.4 — controlled MCP write tools

**Contract.** Expose selected existing Scope 3 commands without changing their
approval, budget, idempotency, state or audit behavior. MCP never supplies a
generic confirmation that substitutes for exact DCLab approval.

### S6-P04A — write manifest and scope/risk mapping

```text
Select the smallest write catalog: agent message/run/cancel, model-build create/
cancel/retry, notebook execution and approval review only if policies permit.
For each map MCP schema/name, DCLab command, OAuth scope, capability, risk,
approval requirement, idempotency field, current version/ETag and result handle.
Exclude arbitrary mutation/export/external action. Approve a separately versioned
write manifest disabled by default.
```

### S6-P04B — command adapters and durable results

```text
Implement write tools as thin SDK calls with explicit workspace/resource versions,
Idempotency-Key and typed request. Return durable command/run/approval handle and
current state, never wait indefinitely or invent completion. Translate validation/
conflict/quota/policy/approval errors faithfully. Record MCP request correlation
through DCLab audit. Add same-call replay and changed-argument conflict tests.
```

### S6-P04C — approval and multi-round input behavior

```text
Where the pinned protocol supports input-required/multi-round interaction, use it
only to collect missing user input; it does not approve an action by itself.
Exact DCLab approval remains a server resource binding canonical digest, actor,
expiry and one-time consumption. Echoed request state is integrity-protected and
bounded. Test altered response/state, replay, expiry, different user/workspace and
clients without the feature.
```

### S6-P04D — write authorization, retry and cancellation proof

```text
Re-check OAuth scope, current membership/capability, data/tool policy, budget,
source versions and exact approval at the DCLab command. Test token/membership/
policy revocation between tool list and call, duplicate network retry, disconnect,
ambiguous response and cancellation race. Verify one command/effect/approval
consumption and no MCP-side mutation state.
```

### S6-P04E — write canary gate

```text
Run conformance plus full Scope 3 command/approval tests through multiple MCP
clients and two workspaces. Audit tool catalog/results for secrets/raw internals,
measure rate/concurrency/cost and drill independent write disable while reads
continue. Enable only enumerated low-risk tools for an allowlist; publish exact
scopes, limitations and recovery procedure.
```

## Plan 6.5 — protocol and operational release gate

**Contract.** Release evidence names the pinned MCP revision/SDK/client matrix.
Protocol updates require compatibility review and conformance before activation.

### S6-P05A — full protocol/client compatibility matrix

```text
Run pinned server conformance and representative current clients for stdio and
hosted initialize/version negotiation, catalog/resources/prompts, structured
results, errors, cancellation, progress/tasks/input-required and stateless
behavior. Test documented backward compatibility only; reject unsupported
protocol revisions safely. Store matrix, transcripts stripped of secrets and
package/image digests.
```

### S6-P05B — malicious protocol and output campaign

```text
Fuzz malformed JSON-RPC, IDs, methods/names/header mismatch, duplicate fields,
deep/large payloads, Unicode, resource URIs, injection text, tool results, SSE
framing and error/provider bodies. Attempt token/secret/internal path/storage key/
prompt/hidden reasoning leakage and cross-tenant resource access. Assert bounded
memory/time, stable errors and zero unauthorized output.
```

### S6-P05C — load, rate and failure recovery

```text
Measure concurrent stdio processes and hosted requests/streams, SDK/API pool use,
rate/scope denials, slow/disconnected clients and multi-instance routing. Inject
MCP/API/auth/provider failure and restart; assert no lost authority or duplicate
command. Set capacity/error-budget thresholds from observed staging evidence and
configure backpressure/alerts.
```

### S6-P05D — packaging, observability and runbooks

```text
Build/scan/sign `dclab_mcp` and hosted image through protected CI with SBOM and
provenance. Emit bounded method/name/transport/outcome/latency metrics and trace
links without arguments/results/tokens. Document local setup, OAuth discovery,
client configuration, protocol upgrade, incident, token compromise, drain and
read/write kill switches. Assign owners.
```

### S6-P05E — Scope 6 go/no-go

```text
Run supply-chain, protocol/client, OAuth/transport security, two-workspace,
read/write, rate/load and recovery gates in production-shaped staging. Confirm
the package imports only SDK/public dependencies and authority is never wider
than `/v1`. Publish pinned version matrix and evidence. Roll out reads first;
writes remain independently canaried and immediately disableable.
```
