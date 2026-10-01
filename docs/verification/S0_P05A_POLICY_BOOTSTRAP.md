# S0-P05A — policy schema and fail-closed bootstrap

Status: HISTORICAL — locally verified at the 0060 head; exact-SHA CI and review pending. Baseline: `02d9f04bad25e5f03bda3ae761c9ec0e8cb3e2a4` on `main`, with pre-existing uncommitted Scope 0 work. Plan 0.4 local gate passed; its exact-SHA CI and formal closure remain pending.

## Pre-edit execution card

1. **Goal/cut line.** Add a versioned, tenant-bound dataset default and a deterministic effective-policy resolver over the existing `DatasetColumn` labels. Unknown/null cannot grant LLM use. One migration and one service concern, under the 800-line review budget. Quarantine, retention deletion, LLM calls, UI, and public policy APIs are later prompts.
2. **Source map.** Reuse `apps/api/app/db/models.py` (`Dataset`, immutable; `DatasetColumn`, nullable labels), `app/domain/privacy_audit.py`, `services/dataset_column_service.py`, `apps/api/alembic/versions/0050_privacy_audit.py` as historical context, and `test_privacy_audit.py`. New `0060` follows previous head `0059_auth_session_constraints`. Existing upload/lab callers of `persist_dataset_columns` remain unchanged; no generated API/SDK artifact changes are expected. The worktree is dirty; preserve unrelated changes.
3. **Persistence.** Add append-only `dataset_policy_revisions` keyed by `(dataset_id, revision)`, with direct workspace ID and composite `(workspace_id,dataset_id)` FK; explicit schema version, sensitivity, LLM exposure, retention class, residency class, source/confidence, UTC creation. Extend `dataset_columns` with nullable retention/residency/confidence/schema-version fields and CHECKs. Old null rows stay null and resolve to deny; do not invent historical classification. Expand now; no destructive backfill/contract phase. Previous-head and fresh-head upgrades, metadata parity, and duplicate/cross-workspace checks are required. A downgrade may drop the new annotations/table only after export/backup; safe feature rollback uses deny-only resolver, not schema reversal.
4. **HTTP/client.** N/A — no route, request, response, status, OpenAPI, SDK or CLI change. Existing consumers remain compatible. Any future policy route must perform membership/capability validation before lookup and use 404 for absent/foreign IDs.
5. **Service/state.** `publish_dataset_policy_defaults` performs current workspace-write authorization, validates a full typed policy, and appends the next revision in one transaction; optimistic expected revision prevents lost updates. `resolve_dataset_policy` checks workspace lineage before reading; combines the latest default with column labels, never widening sensitivity/LLM scope, and returns a deny projection if either source is unknown. Dataset scientific rows never mutate. Existing `set_dataset_column_policy` remains a label setter, not an authorization grant.
6. **Jobs/events/effects.** N/A — synchronous PostgreSQL metadata only; no job, external call, object operation, LLM, event or cancellation path. Duplicate publication is controlled by expected revision and the unique key; a transaction failure rolls back the append.
7. **Frontend/audience.** N/A — no UI/BFF route changes. Existing dataset consumers retain their shapes. The resolver returns policy labels only, not raw rows, object keys, prompts or provider bodies.
8. **Tests.** Add PostgreSQL service cases to `apps/api/tests/test_privacy_audit.py` for missing/default/mixed-column policies, invalid values, duplicate revision, absent/foreign dataset and suspended writer. Extend migration catalog coverage via `test_historical_alembic_revisions.py`; run focused tests, migration tests, truth drift, and available static checks. Record observed results below; no same-SHA CI claim from a dirty tree.
9. **Operations/rollback.** No new environment variable or enabled LLM path. Default is deny. Database constraints enforce enum/range and tenant lineage. This slice emits no new metric/audit event because it exposes no customer operation; publication audit and LLM egress controls belong to later prompts. Kill switch is to stop policy publication; unresolved/null policies continue to deny. Operator should inspect policy revision and column labels before any future egress decision. Owner: API/data-policy maintainers.
10. **Completion ledger.** Focused PostgreSQL, migration, full backend/SDK and generated-truth drift checks below pass. Review and exact-SHA CI remain pending. Do not mark Plan 0.5 complete; next dependency is S0-P05B after S0-P05A review and the unresolved Plan 0.4 exact-SHA gate.

## Implemented contract and compatibility

The live head is `0060_dataset_policy_bootstrap` (one head). The migration adds
`dataset_policy_revisions`: UUID `id`, required `workspace_id` and `dataset_id`
with a composite tenant FK to immutable `datasets`, positive `revision`, schema
version 1, required sensitivity/LLM exposure/retention/residency/source and
confidence, creator ID and UTC timestamp. `(dataset_id, revision)` is unique;
`(workspace_id, dataset_id, revision DESC)` serves latest-policy reads. Database
CHECKs restrict labels, version and confidence. A PostgreSQL trigger rejects
UPDATE and DELETE. The dataset's scientific/evidence columns do not change.

The column table gains nullable `retention_class`, `residency_class`,
`classification_confidence`, and `policy_schema_version`, each with a bounded
CHECK where applicable. Existing null classification and exposure remain null;
there is no backfill to `public`, `allow`, or a guessed workspace. The resolver
requires current membership, exact dataset workspace, an explicit dataset
revision, all expected column rows, and known per-column labels/source/confidence.
It takes the more sensitive class, intersects LLM exposure (incomparable
restricted modes yield `deny`), and takes the narrower residency. Conflicting
retention classes are unresolved, not guessed. Any unknown, incomplete or
conflicting effective policy returns `deny`. The revision publisher requires
current workspace-write authority and an expected revision; stale updates are
409, foreign/absent datasets 404 after workspace authorization, and missing
workspace authority 403. No HTTP or SDK contract changed.

`unknown` retention/residency are explicit fail-closed labels, not executable
deletion or placement policy. `short`, `standard`, `extended`, `home_cloud_only`
and `home_region_only` are schema vocabulary only until later enforcement.
No preview/download/LLM egress path calls this resolver yet; Plan 0.5's final
gate must establish end-to-end denial before any such feature is enabled.

## Observed local evidence (2026-09-28)

All PostgreSQL checks used a newly initialized disposable PostgreSQL 16
instance at `127.0.0.1:55432`, never a production/shared database. The local
loopback tests required sandbox permission; the initial sandboxed attempt was
blocked by the host, not by an application assertion.

| Check | Observed result |
| --- | --- |
| `alembic heads` after adding 0060 | One head: `0060_dataset_policy_bootstrap` |
| `pytest -q apps/api/tests/test_privacy_audit.py apps/api/tests/test_object_storage_lineage.py apps/api/tests/test_historical_alembic_revisions.py --tb=short` | **26 passed, 7 warnings in 17.13 s**; includes fresh, old and immediate previous-head upgrade, metadata parity, null legacy fields, tenant FK, immutable trigger, mixed classes, revision conflict and suspended membership |
| Session/foundation/truth tests after stale-head repair | **51 passed, 1 warning in 18.09 s** |
| Full PostgreSQL backend + Python SDK: `.venv/bin/pytest -q --tb=short --maxfail=1 apps/api/tests packages/dclab_client/tests` | **1,122 passed, 1 skipped, 22 warnings in 656.32 s** |
| Empty prior-head → 0060 → 0059 downgrade → 0060 reapply/catalog parity | **1 passed**, 4 warnings in 3.91 s; physical downgrade intentionally tested only without customer rows |
| Truth generator/drift and whitespace | Two generations byte-identical; all drift detectors clean; `git diff --check` clean |
| Same-SHA CI | Not available from this uncommitted checkout; no claim |

Warnings are the pre-existing Starlette portal deprecation, SQLAlchemy
dataset/execution-request/experiment/ingestion FK-cycle warning, and scikit-learn
unknown-category warnings. The one skipped test requires opt-in live-provider
credentials. No browser
test is added because this prompt exposes no UI or route. No provider or LLM
credential was used.

The first broad run reached 392 passes and found a test that incorrectly
treated 0059 as the permanent head. The repaired session and foundation tests
now compare against the canonical generated head while still testing the 0059
auth-session constraints. The truth detector was adjusted to distinguish a
dated migration observation in CURRENT evidence from a stale unqualified
current-head claim; synthetic tests cover both. The complete rerun passed.

## Rollout and forward repair

This metadata is additive. Leave all external LLM and new preview/download
paths disabled until their later policy gates. If a policy release is
incorrect, append a stricter revision (`llm_exposure_policy='deny'`) after
current membership verification; do not UPDATE/DELETE an old revision or
rewrite immutable Dataset rows. To revert code safely, stop publishing new
revisions and retain the table/nullable columns. Physical downgrade discards
policy history and therefore requires an audited export/backup and a separate
reviewed decision. No schema downgrade is required for the operational kill
switch. No production deployment was attempted.
