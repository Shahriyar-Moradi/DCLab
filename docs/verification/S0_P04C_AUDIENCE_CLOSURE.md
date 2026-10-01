# S0-P04C raw-event and client-audience closure

**Status:** CURRENT — locally verified; exact-SHA CI pending  
**Baseline SHA:** `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4` (pre-existing dirty worktree preserved)  
**Database head at this gate:** `0059_auth_session_constraints`; no migration added

## Implementation packet and inventory

This is a provider-neutral HTTP read-boundary change. No AWS/GCP resource,
database table, job, event writer, scientific calculation, flag, or user-visible
agent behavior changed. Existing persisted evidence remains immutable. The
server-owned audience projection in `services/audience_projection.py` is used
instead of another storage model or SDK-side authorization matrix.

| Audience/surface | Before | Current projection and authorization |
| --- | --- | --- |
| `/admin/observatory` events, summaries, LLM details | Optional workspace query could become global; stored payload/failure text was returned | Selected workspace is required and query must agree; events/LLM/report fields are sanitized at read time. Platform capability and current workspace membership precede lookup. |
| `/business/observatory` and `/business/.../monitor` | Event route bypassed its filtered helper; monitor lookup preceded capability | `pipeline_monitor` and `raw_pipeline_debug` gate raw-event route before lookup; CV/semantic/audit subsets remain capability-filtered; safe payload is returned even for legacy rows. |
| `/v1/model-builds/.../events`, `/v1/execution-requests/...` | Event payload, request/result dicts and persisted failure summary could carry diagnostics | Current workspace filters the event query; SDK-shaped payloads and execution-request diagnostics receive the same bounded read projection. |
| Legacy `/app/labs/runs` and admin upload detail | Trial failure, stored path, pipeline log and raw LLM output were exposed | Trial failure is generic; admin upload retains safe status/analysis but blanks storage path, omits raw LLM output and sanitizes nested log/evidence. Operator rows remain unchanged. |
| Business/platform explorer and technical explorer | Monitor event payloads, LLM decisions, model failure text and artifact locators could pass through | Business view keeps its capability subsets and adds recursive safe projection. Admin hierarchy/monitor and technical LLM/artifact projections no longer return raw diagnostic bodies or internal locators. Authorized platform membership email remains a typed admin field. |
| Reproducibility metadata and SDK `Artifact` | `object_key`, `artifact_uri`, code entrypoint disclosed internal locators | Existing response shapes remain compatible but `object_key`/entrypoint are blank and `artifact_uri` is null; artifact ID/digest/download remain available. SDK marks `object_key` as a blank compatibility field. |

Projection drops sensitive keys (secrets, paths, object-storage locators,
handler keys, prompts, raw provider/request/response bodies and stack traces),
redacts path/credential/email-like diagnostic strings, and bounds nested
depth, list width and text length. Failed-event `reason` text is generic;
business LLM purpose filtering now occurs in the workspace-scoped query, not
after fetching disallowed rows. Typed safe identifiers, statuses, metrics,
digests and same-workspace counts remain available. The projection never
mutates persisted scientific or operator evidence; protected DB/operator
access retains original diagnostics. An explicitly authorized, short-lived
artifact signed-URL action is a download capability, not metadata; retiring
direct signed URLs in favor of a proxy would be a separate API decision.

## Denial, compatibility and operations contract

Authentication failure is 401. A missing capability is 403 before record
existence lookup; after authorization, a foreign or absent ID is 404. A
workspace query conflicting with the server-validated selection is 404. There
is no fallback to global reads or browser-provided role. The server filters
workspace before event/LLM lookup, and the SDK/browser sees only its response.
No schema migration, backfill or object cleanup is needed. Existing `/v1`
OpenAPI shapes are preserved; legacy locator fields are intentionally blank
and callers should use artifact IDs. No new configuration/feature flag is
introduced. On a projection defect, disable affected read routes or deploy a
fixed projection-enabled build; do not roll back to raw/global readers.
Existing capability-denial metrics and audit paths remain the operational
signal. This is infrastructure-only; it does not alter Data Scientist/ML
Engineer jobs or the deterministic lifecycle.

## Observed verification (2026-09-24 to 2026-09-25)

| Check | Observed result |
| --- | --- |
| PostgreSQL-backed admin/business/client, observability, Labs, reproducibility, technical/platform explorer, `/v1` and SDK suite | 71 passed, 1 existing Starlette deprecation warning, 62.40 s on disposable PostgreSQL 15 at `127.0.0.1:55432` |
| Final purpose-filter and failed-event change: observability, business capability and audience suite | 13 passed, 1 existing Starlette deprecation warning, 22.37 s on a fresh disposable PostgreSQL 15 cluster |
| Focused projection unit snapshots | 3 passed, 1 deselected (DB case), same existing warning |
| `alembic heads` | `0059_auth_session_constraints (head)` |
| `generate_truth_artifacts --verify-idempotent` | Two generations byte-identical |
| `check_truth_drift` | All detectors clean after sequential regeneration |

The new tests inject a deliberately unsafe persisted event and prove the
admin, developer and client responses agree on the safe projection while the
stored row remains unchanged. They also cover LLM provider/prompt snapshots,
SDK artifact metadata, a legacy client failure, capability-before-existence
403 and authorized foreign-resource 404. The first truth-drift run overlapped
the idempotence generator and reported a transient stale artifact; the
subsequent sequential run was clean. No exact-SHA CI result exists for this
dirty checkout. Browser E2E and the full backend suite were not rerun for this
slice. The disposable PostgreSQL server was stopped and its temporary cluster
directory removed after verification. The 13-test final run overlaps the
71-test matrix; these counts are not additive.

The next eligible prompt is **S0-P04D**, subject to review of this audience
contract and exact-SHA CI.
