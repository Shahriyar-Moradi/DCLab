# Scope 6 prompts — MCP server

Use the common preamble. Re-verify the current MCP specification from primary
documentation at implementation time and pin the supported version. MCP is an
SDK adapter, not DCLab's agent, connector, queue, authorization or data layer.

## Plan 6.1 — package architecture and threat model

### S6-P01A — define MCP mapping and release boundary

```text
Create packages/dclab_mcp depending only on the public SDK. Write an ADR mapping
MCP initialize/capabilities/tools/resources/prompts/progress/cancellation/errors
to DCLab API resources, async state and request IDs. Pin protocol and SDK/API
compatibility. Define structured and concise human outputs, result bounds,
pagination/resource links and logs-to-stderr. No handler may import app,
SQLAlchemy, database, engine or worker modules.
```

### S6-P01B — MCP security threat model and policy catalog

```text
Threat-model malicious clients/models, confused deputy, prompt injection,
cross-tenant IDs, OAuth audience confusion, Origin/Host/DNS rebinding, session
fixation, oversized requests/results, notification floods and secret/signed-URL
leakage. Produce a reviewed catalog where each tool/resource/prompt has version,
schema, required scope/capability, risk/read-only annotation, data class, limits,
audit and kill switch. MCP never grants scope or approval.
```

## Plan 6.2 — local read-only stdio MCP

### S6-P02A — implement stdio tools/resources/prompts

```text
Implement stdio with JSON-RPC only on stdin/stdout and logs only on stderr,
using an explicitly selected CLI profile/token provider. Expose bounded
read-only tools for workspaces, projects, datasets/profile, builds/events,
artifact metadata and business summary; resources using authorized dclab://
URIs; and optional user-selected prompts for readiness, failure and completed
build explanation. Use strict input/output schemas, opaque cursors, resource
links and request IDs. Bind no unauthenticated local HTTP listener.
```

### S6-P02B — protocol and data-safety conformance

```text
Test initialize/version negotiation, capability/list/call/read/get, clean stdio
framing, cancellation/progress, structured errors, cursor bounds and SDK
mapping against representative clients. Test malicious/additional fields,
cross-workspace IDs, revoked membership, prompt injection and oversized output.
Prove no raw data, internal-only model detail, prompt/hidden reasoning, storage
key, secret or signed URL is returned. Add golden protocol fixtures without
credentials.
```

## Plan 6.3 — hosted Streamable HTTP and OAuth

### S6-P03A — hosted transport and protected resource

```text
Implement hosted Streamable HTTP behind the API gateway with TLS,
protected-resource and authorization-server metadata, short-lived audience-
bound tokens, protocol/session headers, bounded body/output, session lifetime,
concurrent streams, authorized resumption and rate limits by principal,
workspace, client, tool and IP risk. Validate Origin, Host and DNS-rebinding
conditions; bind local development to loopback. Never accept tokens in URLs.
```

### S6-P03B — OAuth, session and transport attack suite

```text
Test issuer/signature/audience/expiry/scope, token for another resource,
incremental authorization, revoked membership, workspace service-account
escape, allowed/denied/missing Origin policy, Host/DNS abuse, session fixation/
expiry/cross-principal reuse, oversized body/result, concurrency/rate limits,
disconnect/reconnect and resume authorization. Record MCP client and principal
in every audit record. Run current protocol conformance and at least two real
client integrations in staging.
```

## Plan 6.4 — controlled MCP write tools

### S6-P04A — expose selected existing commands

```text
After Scope 3, add narrowly typed tools for create/cancel/retry model build,
start/cancel bounded DCLab agent run and submit an already-required exact
approval under a distinct approval scope. Map directly to SDK methods. Return
accepted resources and status links, never claim completion. Enforce same
current authorization, workspace, data/tool policy, risk, budget, approval,
idempotency and audit as web/API. Do not expose generic action delivery,
connector URL, SQL, code or arbitrary payload tools.
```

### S6-P04B — write retry and approval-substitution proof

```text
Test duplicate MCP calls, disconnect after acceptance, same/different
idempotency digest, stale resource, wrong workspace/scope, lost membership,
expired/changed approval, model attempting self-approval, cancellation and rate
limits. Assert identical API resources/errors to direct SDK use and exactly one
durable effect. Verify independent hosted-MCP and write-tool kill switches.
```

## Plan 6.5 — release gate

### S6-P05A — full conformance and operations

```text
Run protocol, stdio, Streamable HTTP, OAuth, tool/resource/prompt, pagination,
progress/cancel, reconnect and malicious-client suites against the pinned spec
and client matrix. Load test sessions/streams/results and telemetry cardinality.
Add dashboards for auth denial, sessions, tool latency/outcome, schema failure,
rate limit and output policy. Add outage, revoke, disable-tool and incident
runbooks.
```

### S6-P05B — staged read/write rollout

```text
Release local read-only first, hosted read-only second and selected writes last.
For each stage run a separate security review, workspace allowlist, quota,
synthetic E2E, kill-switch and rollback drill. Publish tool/resource schemas,
compatibility and limitations. Keep external action tools disabled until Scope
8 is independently complete.
```
